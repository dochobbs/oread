"""
Auth middleware for FastAPI.

Provides dependency injection for authenticated routes.
"""

import logging
from typing import Optional
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from src import runtime_config
from src.db.client import get_client, get_user_client, is_configured
from src.db.repositories import UserRepository

logger = logging.getLogger("oread.auth")

# HTTP Bearer scheme for Authorization header
security = HTTPBearer(auto_error=False)


@dataclass
class AuthenticatedUser:
  """Represents an authenticated user from JWT."""
  id: str
  email: str
  role: str = "learner"
  learner_level: Optional[str] = None
  token: Optional[str] = None

  def db(self):
    """Supabase client whose queries run as this user (RLS applies)."""
    if not self.token:
      raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return get_user_client(self.token)


# The single user of a standalone (no-auth, loopback-only) server.
LOCAL_USER = AuthenticatedUser(id="local", email="local@localhost", role="admin")


def decode_token(token: str) -> dict:
  """
  Verify a Supabase JWT by asking Supabase (auth.get_user), which checks
  signature and expiry server-side. Does not attach a session to the client.
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

  except HTTPException:
    raise
  except Exception:
    logger.info("Token validation failed", exc_info=True)
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Invalid or expired token"
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

  token = credentials.credentials
  token_data = decode_token(token)

  # Get user profile from database, querying as this user
  user_repo = UserRepository(client=get_user_client(token))
  user_profile = user_repo.get_by_id(token_data["sub"])

  if user_profile:
    return AuthenticatedUser(
      id=token_data["sub"],
      email=token_data["email"],
      role=user_profile.get("role", "learner"),
      learner_level=user_profile.get("learner_level"),
      token=token,
    )
  else:
    # User exists in auth but not in profiles table
    # Auto-create the profile using admin client to bypass RLS
    try:
      admin_repo = UserRepository(use_admin=True)
      admin_repo.create(
        user_id=token_data["sub"],
        email=token_data["email"],
      )
    except Exception:
      logger.warning("Profile auto-create failed for %s", token_data["sub"], exc_info=True)

    return AuthenticatedUser(
      id=token_data["sub"],
      email=token_data["email"],
      token=token,
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
    token = credentials.credentials
    token_data = decode_token(token)

    user_repo = UserRepository(client=get_user_client(token))
    user_profile = user_repo.get_by_id(token_data["sub"])

    if user_profile:
      return AuthenticatedUser(
        id=token_data["sub"],
        email=token_data["email"],
        role=user_profile.get("role", "learner"),
        learner_level=user_profile.get("learner_level"),
        token=token,
      )
    else:
      return AuthenticatedUser(
        id=token_data["sub"],
        email=token_data["email"],
        token=token,
      )

  except HTTPException:
    return None
  except Exception:
    return None


async def require_user(
  credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> AuthenticatedUser:
  """
  Mode-aware auth for patient routes.

  Standalone: returns LOCAL_USER (the server is loopback-only).
  Platform: requires a valid bearer token, same as get_current_user.
  """
  if not runtime_config.current().is_platform:
    return LOCAL_USER
  return await get_current_user(credentials)


async def require_platform_user(
  credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> AuthenticatedUser:
  """
  Auth for features that need Supabase (accounts, panels).

  Standalone: 501, since there is no database to back them.
  """
  if not runtime_config.current().is_platform:
    raise HTTPException(
      status_code=status.HTTP_501_NOT_IMPLEMENTED,
      detail="Requires platform mode (Supabase). This Oread runs standalone.",
    )
  return await get_current_user(credentials)


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
