"""
Authentication module for Oread Learning Platform.

Provides JWT verification and user context for FastAPI.
"""

from src.auth.middleware import (
  LOCAL_USER,
  get_current_user,
  get_current_user_optional,
  get_admin_user,
  require_platform_user,
  require_user,
  AuthenticatedUser,
)

__all__ = [
  "LOCAL_USER",
  "get_current_user",
  "get_current_user_optional",
  "get_admin_user",
  "require_platform_user",
  "require_user",
  "AuthenticatedUser",
]
