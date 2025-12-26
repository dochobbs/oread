"""
Authentication module for Oread Learning Platform.

Provides JWT verification and user context for FastAPI.
"""

from src.auth.middleware import (
  get_current_user,
  get_current_user_optional,
  get_admin_user,
  AuthenticatedUser,
)

__all__ = [
  "get_current_user",
  "get_current_user_optional",
  "get_admin_user",
  "AuthenticatedUser",
]
