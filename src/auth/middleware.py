"""
Auth middleware for FastAPI.

Provides dependency injection for authenticated routes.
"""

import os
from typing import Optional
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

from src.db.client import get_client, is_configured
from src.db.repositories import UserRepository


# Supabase JWT settings
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET")
ALGORITHM = "HS256"

# HTTP Bearer scheme for Authorization header
security = HTTPBearer(auto_error=False)


@dataclass
class AuthenticatedUser:
  """Represents an authenticated user from JWT."""
  id: str
  email: str
  role: str = "learner"
  learner_level: Optional[str] = None


def decode_token(token: str) -> dict:
  """
  Decode and verify a Supabase JWT token.

  Note: Supabase tokens are signed with the JWT secret from project settings.
  For simplicity, we verify the token by calling Supabase's auth.getUser().
  """
  if not is_configured():
    raise HTTPException(
      status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
      detail="Database not configured"
    )

  try:
    client = get_client()
    # Set the session with the token to verify it
    # This validates the token with Supabase
    user_response = client.auth.get_user(token)

    if not user_response or not user_response.user:
      raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token"
      )

    return {
      "sub": user_response.user.id,
      "email": user_response.user.email,
    }

  except Exception as e:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail=f"Token validation failed: {str(e)}"
    )


async def get_current_user(
  credentials: HTTPAuthorizationCredentials = Depends(security)
) -> AuthenticatedUser:
  """
  Dependency to get the current authenticated user.

  Use this for routes that REQUIRE authentication.
  Raises 401 if not authenticated.
  """
  if not credentials:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Authentication required",
      headers={"WWW-Authenticate": "Bearer"},
    )

  token_data = decode_token(credentials.credentials)

  # Get user profile from database
  user_repo = UserRepository()
  user_profile = user_repo.get_by_id(token_data["sub"])

  if user_profile:
    return AuthenticatedUser(
      id=token_data["sub"],
      email=token_data["email"],
      role=user_profile.get("role", "learner"),
      learner_level=user_profile.get("learner_level"),
    )
  else:
    # User exists in auth but not in profiles table
    # This can happen if profile creation failed
    return AuthenticatedUser(
      id=token_data["sub"],
      email=token_data["email"],
    )


async def get_current_user_optional(
  credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[AuthenticatedUser]:
  """
  Dependency to get the current user if authenticated.

  Use this for routes that work with OR without authentication.
  Returns None if not authenticated.
  """
  if not credentials:
    return None

  try:
    token_data = decode_token(credentials.credentials)

    user_repo = UserRepository()
    user_profile = user_repo.get_by_id(token_data["sub"])

    if user_profile:
      return AuthenticatedUser(
        id=token_data["sub"],
        email=token_data["email"],
        role=user_profile.get("role", "learner"),
        learner_level=user_profile.get("learner_level"),
      )
    else:
      return AuthenticatedUser(
        id=token_data["sub"],
        email=token_data["email"],
      )

  except HTTPException:
    return None
  except Exception:
    return None


async def get_admin_user(
  user: AuthenticatedUser = Depends(get_current_user)
) -> AuthenticatedUser:
  """
  Dependency to require admin role.

  Raises 403 if user is not an admin.
  """
  if user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Admin access required"
    )
  return user
