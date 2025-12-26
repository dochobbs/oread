"""
Database module for Oread Learning Platform.

Provides Supabase client and repository classes for data access.
"""

from src.db.client import get_client, get_admin_client, SupabaseClient
from src.db.repositories import (
  UserRepository,
  PanelRepository,
  PatientRepository,
  EncounterRepository,
  SessionRepository,
  ReviewRepository,
)

__all__ = [
  "get_client",
  "get_admin_client",
  "SupabaseClient",
  "UserRepository",
  "PanelRepository",
  "PatientRepository",
  "EncounterRepository",
  "SessionRepository",
  "ReviewRepository",
]
