"""
Supabase client wrapper for Oread.

Provides singleton access to Supabase client with proper configuration.
"""

import os
from functools import lru_cache
from typing import Optional

from supabase import create_client, Client


class SupabaseConfig:
  """Configuration for Supabase connection."""

  def __init__(self):
    self.url = os.environ.get("SUPABASE_URL")
    self.anon_key = os.environ.get("SUPABASE_ANON_KEY")
    self.service_key = os.environ.get("SUPABASE_SERVICE_KEY")

  @property
  def is_configured(self) -> bool:
    """Check if Supabase is properly configured."""
    return bool(self.url and self.anon_key)

  def validate(self) -> None:
    """Raise error if not properly configured."""
    if not self.url:
      raise ValueError("SUPABASE_URL environment variable not set")
    if not self.anon_key:
      raise ValueError("SUPABASE_ANON_KEY environment variable not set")


class SupabaseClient:
  """
  Wrapper around Supabase client with convenience methods.

  Provides both authenticated (user context) and admin (service role) access.
  """

  def __init__(self, client: Client):
    self._client = client

  @property
  def client(self) -> Client:
    """Get the underlying Supabase client."""
    return self._client

  @property
  def auth(self):
    """Get the auth module."""
    return self._client.auth

  def table(self, name: str):
    """Get a table reference for queries."""
    return self._client.table(name)

  def rpc(self, fn_name: str, params: dict = None):
    """Call a database function."""
    return self._client.rpc(fn_name, params or {})

  # -------------------------------------------------------------------------
  # Auth convenience methods
  # -------------------------------------------------------------------------

  def sign_up(self, email: str, password: str) -> dict:
    """Sign up a new user."""
    response = self._client.auth.sign_up({
      "email": email,
      "password": password,
    })
    return response

  def sign_in(self, email: str, password: str) -> dict:
    """Sign in an existing user."""
    response = self._client.auth.sign_in_with_password({
      "email": email,
      "password": password,
    })
    return response

  def sign_out(self) -> None:
    """Sign out the current user."""
    self._client.auth.sign_out()

  def get_user(self):
    """Get the current authenticated user."""
    return self._client.auth.get_user()

  def get_session(self):
    """Get the current session."""
    return self._client.auth.get_session()

  def set_session(self, access_token: str, refresh_token: str):
    """Set the session from tokens."""
    return self._client.auth.set_session(access_token, refresh_token)


# -----------------------------------------------------------------------------
# Singleton instances
# -----------------------------------------------------------------------------

_client: Optional[SupabaseClient] = None
_admin_client: Optional[SupabaseClient] = None
_config: Optional[SupabaseConfig] = None


def get_config() -> SupabaseConfig:
  """Get the Supabase configuration (singleton)."""
  global _config
  if _config is None:
    _config = SupabaseConfig()
  return _config


def get_client() -> SupabaseClient:
  """
  Get the Supabase client (singleton).

  Uses the anon key, which respects Row Level Security.
  Use this for user-facing operations.
  """
  global _client
  if _client is None:
    config = get_config()
    config.validate()
    raw_client = create_client(config.url, config.anon_key)
    _client = SupabaseClient(raw_client)
  return _client


def get_admin_client() -> SupabaseClient:
  """
  Get the admin Supabase client (singleton).

  Uses the service_role key, which bypasses Row Level Security.
  Use this for admin operations and background jobs.
  """
  global _admin_client
  if _admin_client is None:
    config = get_config()
    config.validate()
    if not config.service_key:
      raise ValueError("SUPABASE_SERVICE_KEY environment variable not set")
    raw_client = create_client(config.url, config.service_key)
    _admin_client = SupabaseClient(raw_client)
  return _admin_client


def is_configured() -> bool:
  """Check if Supabase is configured without raising errors."""
  return get_config().is_configured


def reset_clients() -> None:
  """Reset client singletons (useful for testing)."""
  global _client, _admin_client, _config
  _client = None
  _admin_client = None
  _config = None
