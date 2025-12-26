"""
Repository classes for database operations.

Each repository handles CRUD operations for a specific table,
providing a clean interface for the rest of the application.
"""

from datetime import datetime, timedelta
from typing import Optional, Any
from uuid import UUID

from src.db.client import get_client, get_admin_client, SupabaseClient


class BaseRepository:
  """Base class for all repositories."""

  table_name: str = ""

  def __init__(self, client: Optional[SupabaseClient] = None, use_admin: bool = False):
    """
    Initialize repository with optional client.

    Args:
      client: Supabase client to use. If None, gets default client.
      use_admin: If True and no client provided, use admin client.
    """
    if client:
      self._client = client
    elif use_admin:
      self._client = get_admin_client()
    else:
      self._client = get_client()

  @property
  def table(self):
    """Get the table reference."""
    return self._client.table(self.table_name)

  def _to_dict(self, obj: Any) -> dict:
    """Convert object to dict for storage."""
    if hasattr(obj, "model_dump"):
      return obj.model_dump(mode="json", exclude_none=True)
    elif hasattr(obj, "dict"):
      return obj.dict(exclude_none=True)
    elif isinstance(obj, dict):
      return obj
    else:
      raise ValueError(f"Cannot convert {type(obj)} to dict")


class UserRepository(BaseRepository):
  """Repository for user operations."""

  table_name = "users"

  def get_by_id(self, user_id: str | UUID) -> Optional[dict]:
    """Get user by ID."""
    response = self.table.select("*").eq("id", str(user_id)).single().execute()
    return response.data if response.data else None

  def get_by_email(self, email: str) -> Optional[dict]:
    """Get user by email."""
    response = self.table.select("*").eq("email", email).single().execute()
    return response.data if response.data else None

  def create(self, user_id: str | UUID, email: str, **kwargs) -> dict:
    """Create a new user profile."""
    data = {
      "id": str(user_id),
      "email": email,
      **kwargs
    }
    response = self.table.insert(data).execute()
    return response.data[0] if response.data else None

  def update(self, user_id: str | UUID, **kwargs) -> dict:
    """Update user profile."""
    response = self.table.update(kwargs).eq("id", str(user_id)).execute()
    return response.data[0] if response.data else None

  def set_learner_level(self, user_id: str | UUID, level: str) -> dict:
    """Set user's learner level."""
    return self.update(user_id, learner_level=level)


class PanelRepository(BaseRepository):
  """Repository for patient panel operations."""

  table_name = "panels"

  def get_by_id(self, panel_id: str | UUID) -> Optional[dict]:
    """Get panel by ID."""
    response = self.table.select("*").eq("id", str(panel_id)).single().execute()
    return response.data if response.data else None

  def get_by_owner(self, owner_id: str | UUID) -> list[dict]:
    """Get all panels for a user."""
    response = self.table.select("*").eq("owner_id", str(owner_id)).order("created_at", desc=True).execute()
    return response.data or []

  def create(self, owner_id: str | UUID, name: str, config: dict = None) -> dict:
    """Create a new panel."""
    data = {
      "owner_id": str(owner_id),
      "name": name,
      "config": config or {},
    }
    response = self.table.insert(data).execute()
    return response.data[0] if response.data else None

  def update(self, panel_id: str | UUID, **kwargs) -> dict:
    """Update panel."""
    response = self.table.update(kwargs).eq("id", str(panel_id)).execute()
    return response.data[0] if response.data else None

  def delete(self, panel_id: str | UUID) -> bool:
    """Delete panel and all associated patients."""
    response = self.table.delete().eq("id", str(panel_id)).execute()
    return len(response.data) > 0 if response.data else False


class PatientRepository(BaseRepository):
  """Repository for synthetic patient operations."""

  table_name = "patients"

  def get_by_id(self, patient_id: str | UUID) -> Optional[dict]:
    """Get patient by ID."""
    response = self.table.select("*").eq("id", str(patient_id)).single().execute()
    return response.data if response.data else None

  def get_by_panel(self, panel_id: str | UUID) -> list[dict]:
    """Get all patients in a panel."""
    response = self.table.select("*").eq("panel_id", str(panel_id)).order("created_at").execute()
    return response.data or []

  def create(self, panel_id: str | UUID, demographics: dict, full_record: dict, **kwargs) -> dict:
    """Create a new patient."""
    data = {
      "panel_id": str(panel_id),
      "demographics": demographics,
      "full_record": full_record,
      **kwargs
    }
    response = self.table.insert(data).execute()
    return response.data[0] if response.data else None

  def create_many(self, patients: list[dict]) -> list[dict]:
    """Bulk create patients."""
    response = self.table.insert(patients).execute()
    return response.data or []

  def update(self, patient_id: str | UUID, **kwargs) -> dict:
    """Update patient record."""
    response = self.table.update(kwargs).eq("id", str(patient_id)).execute()
    return response.data[0] if response.data else None

  def delete(self, patient_id: str | UUID) -> bool:
    """Delete a patient."""
    response = self.table.delete().eq("id", str(patient_id)).execute()
    return len(response.data) > 0 if response.data else False


class EncounterRepository(BaseRepository):
  """Repository for encounter operations."""

  table_name = "encounters"

  def get_by_id(self, encounter_id: str | UUID) -> Optional[dict]:
    """Get encounter by ID."""
    response = self.table.select("*").eq("id", str(encounter_id)).single().execute()
    return response.data if response.data else None

  def get_by_patient(self, patient_id: str | UUID) -> list[dict]:
    """Get all encounters for a patient."""
    response = self.table.select("*").eq("patient_id", str(patient_id)).order("generated_at", desc=True).execute()
    return response.data or []

  def create(self, patient_id: str | UUID, encounter_type: str, encounter_json: dict, **kwargs) -> dict:
    """Create a new encounter."""
    data = {
      "patient_id": str(patient_id),
      "encounter_type": encounter_type,
      "encounter_json": encounter_json,
      **kwargs
    }
    response = self.table.insert(data).execute()
    return response.data[0] if response.data else None

  def create_many(self, encounters: list[dict]) -> list[dict]:
    """Bulk create encounters."""
    response = self.table.insert(encounters).execute()
    return response.data or []


class SessionRepository(BaseRepository):
  """Repository for learning session operations."""

  table_name = "sessions"

  def get_by_id(self, session_id: str | UUID) -> Optional[dict]:
    """Get session by ID."""
    response = self.table.select("*").eq("id", str(session_id)).single().execute()
    return response.data if response.data else None

  def get_by_user(self, user_id: str | UUID, limit: int = 50) -> list[dict]:
    """Get recent sessions for a user."""
    response = (
      self.table.select("*, encounters(*)")
      .eq("user_id", str(user_id))
      .order("started_at", desc=True)
      .limit(limit)
      .execute()
    )
    return response.data or []

  def get_active(self, user_id: str | UUID) -> list[dict]:
    """Get incomplete sessions for a user."""
    response = (
      self.table.select("*")
      .eq("user_id", str(user_id))
      .is_("completed_at", "null")
      .order("started_at", desc=True)
      .execute()
    )
    return response.data or []

  def create(self, user_id: str | UUID, encounter_id: str | UUID) -> dict:
    """Start a new learning session."""
    data = {
      "user_id": str(user_id),
      "encounter_id": str(encounter_id),
    }
    response = self.table.insert(data).execute()
    return response.data[0] if response.data else None

  def update(self, session_id: str | UUID, **kwargs) -> dict:
    """Update session with learner work or scores."""
    response = self.table.update(kwargs).eq("id", str(session_id)).execute()
    return response.data[0] if response.data else None

  def complete(self, session_id: str | UUID, score: dict = None) -> dict:
    """Mark session as completed."""
    data = {"completed_at": datetime.utcnow().isoformat()}
    if score:
      data["score"] = score
    return self.update(session_id, **data)


class ReviewRepository(BaseRepository):
  """Repository for spaced repetition review operations."""

  table_name = "reviews"

  def get_due(self, user_id: str | UUID, limit: int = 10) -> list[dict]:
    """Get reviews that are due for a user."""
    response = (
      self.table.select("*, encounters(*)")
      .eq("user_id", str(user_id))
      .lte("next_review", datetime.utcnow().isoformat())
      .order("next_review")
      .limit(limit)
      .execute()
    )
    return response.data or []

  def get_due_count(self, user_id: str | UUID) -> int:
    """Get count of reviews due for a user."""
    response = (
      self.table.select("id", count="exact")
      .eq("user_id", str(user_id))
      .lte("next_review", datetime.utcnow().isoformat())
      .execute()
    )
    return response.count or 0

  def get_or_create(self, user_id: str | UUID, encounter_id: str | UUID) -> dict:
    """Get existing review or create new one."""
    response = (
      self.table.select("*")
      .eq("user_id", str(user_id))
      .eq("encounter_id", str(encounter_id))
      .single()
      .execute()
    )
    if response.data:
      return response.data

    # Create new review
    data = {
      "user_id": str(user_id),
      "encounter_id": str(encounter_id),
    }
    response = self.table.insert(data).execute()
    return response.data[0] if response.data else None

  def update_after_review(
    self,
    review_id: str | UUID,
    quality: int,
    ease_factor: float,
    interval_days: int,
    repetitions: int,
    correct: bool
  ) -> dict:
    """Update review after a review session (SM-2 algorithm)."""
    next_review = datetime.utcnow() + timedelta(days=interval_days)
    data = {
      "ease_factor": ease_factor,
      "interval_days": interval_days,
      "repetitions": repetitions,
      "next_review": next_review.isoformat(),
      "last_reviewed_at": datetime.utcnow().isoformat(),
    }
    if correct:
      data["correct_count"] = self._client.client.rpc(
        "increment", {"row_id": str(review_id), "column_name": "correct_count"}
      )
    else:
      data["incorrect_count"] = self._client.client.rpc(
        "increment", {"row_id": str(review_id), "column_name": "incorrect_count"}
      )

    # Simplified: just update the fields directly
    update_data = {
      "ease_factor": ease_factor,
      "interval_days": interval_days,
      "repetitions": repetitions,
      "next_review": next_review.isoformat(),
      "last_reviewed_at": datetime.utcnow().isoformat(),
    }
    response = self.table.update(update_data).eq("id", str(review_id)).execute()
    return response.data[0] if response.data else None


class CompetencyRepository(BaseRepository):
  """Repository for competency progress tracking."""

  table_name = "competency_progress"

  def get_by_user(self, user_id: str | UUID) -> list[dict]:
    """Get all competency progress for a user."""
    response = (
      self.table.select("*")
      .eq("user_id", str(user_id))
      .order("competency_code")
      .execute()
    )
    return response.data or []

  def get_gaps(self, user_id: str | UUID, min_cases: int = 3, min_score: float = 70) -> list[dict]:
    """Get competencies that need attention."""
    response = (
      self.table.select("*")
      .eq("user_id", str(user_id))
      .or_(f"cases_seen.lt.{min_cases},avg_score.lt.{min_score}")
      .order("cases_seen")
      .execute()
    )
    return response.data or []

  def upsert(self, user_id: str | UUID, competency_code: str, score: float) -> dict:
    """Update or create competency progress."""
    # Try to get existing
    response = (
      self.table.select("*")
      .eq("user_id", str(user_id))
      .eq("competency_code", competency_code)
      .single()
      .execute()
    )

    if response.data:
      # Update existing
      new_cases = response.data["cases_seen"] + 1
      new_total = response.data["total_score"] + score
      update_response = (
        self.table.update({
          "cases_seen": new_cases,
          "total_score": new_total,
          "last_seen_at": datetime.utcnow().isoformat(),
        })
        .eq("user_id", str(user_id))
        .eq("competency_code", competency_code)
        .execute()
      )
      return update_response.data[0] if update_response.data else None
    else:
      # Create new
      data = {
        "user_id": str(user_id),
        "competency_code": competency_code,
        "cases_seen": 1,
        "total_score": score,
        "last_seen_at": datetime.utcnow().isoformat(),
      }
      insert_response = self.table.insert(data).execute()
      return insert_response.data[0] if insert_response.data else None


class FeedbackRepository(BaseRepository):
  """Repository for user feedback operations."""

  table_name = "feedback"

  def create(
    self,
    user_id: str | UUID,
    feedback_type: str,
    content: str,
    encounter_id: str | UUID = None,
    patient_id: str | UUID = None
  ) -> dict:
    """Create new feedback."""
    data = {
      "user_id": str(user_id),
      "feedback_type": feedback_type,
      "content": content,
    }
    if encounter_id:
      data["encounter_id"] = str(encounter_id)
    if patient_id:
      data["patient_id"] = str(patient_id)

    response = self.table.insert(data).execute()
    return response.data[0] if response.data else None

  def get_by_user(self, user_id: str | UUID) -> list[dict]:
    """Get all feedback from a user."""
    response = (
      self.table.select("*")
      .eq("user_id", str(user_id))
      .order("created_at", desc=True)
      .execute()
    )
    return response.data or []

  def get_pending(self, limit: int = 50) -> list[dict]:
    """Get pending feedback (admin only)."""
    response = (
      self.table.select("*, users(email, display_name)")
      .eq("status", "new")
      .order("created_at")
      .limit(limit)
      .execute()
    )
    return response.data or []

  def update_status(self, feedback_id: str | UUID, status: str, admin_notes: str = None) -> dict:
    """Update feedback status (admin only)."""
    data = {"status": status}
    if admin_notes:
      data["admin_notes"] = admin_notes
    if status == "resolved":
      data["resolved_at"] = datetime.utcnow().isoformat()

    response = self.table.update(data).eq("id", str(feedback_id)).execute()
    return response.data[0] if response.data else None
