"""
User and panel models for Oread Learning Platform.

These models represent authenticated users and their patient panels.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, Any
from uuid import UUID

from pydantic import BaseModel, Field, EmailStr


class LearnerLevel(str, Enum):
  """Learner experience levels, from student to attending."""

  NP_STUDENT = "np_student"
  MS3 = "ms3"              # 3rd year medical student
  MS4 = "ms4"              # 4th year medical student
  INTERN = "intern"        # PGY-1
  PGY2 = "pgy2"            # PGY-2
  PGY3 = "pgy3"            # PGY-3 (chief resident)
  FELLOW = "fellow"        # Fellowship
  ATTENDING = "attending"  # Board certified


class UserRole(str, Enum):
  """User roles for access control."""

  LEARNER = "learner"
  INSTRUCTOR = "instructor"
  ADMIN = "admin"


class User(BaseModel):
  """
  User profile for authenticated users.

  Links to Supabase auth.users and stores additional profile data.
  """

  id: UUID
  email: EmailStr
  display_name: Optional[str] = None
  role: UserRole = UserRole.LEARNER
  learner_level: Optional[LearnerLevel] = None
  institution: Optional[str] = None
  created_at: datetime = Field(default_factory=datetime.utcnow)
  updated_at: datetime = Field(default_factory=datetime.utcnow)

  class Config:
    from_attributes = True

  @property
  def is_admin(self) -> bool:
    """Check if user is an admin."""
    return self.role == UserRole.ADMIN

  @property
  def is_instructor(self) -> bool:
    """Check if user is an instructor or admin."""
    return self.role in (UserRole.INSTRUCTOR, UserRole.ADMIN)

  @property
  def difficulty_range(self) -> tuple[int, int]:
    """
    Get appropriate difficulty range based on learner level.

    Returns (min_difficulty, max_difficulty) where difficulty is 1-5.
    """
    level_ranges = {
      LearnerLevel.NP_STUDENT: (1, 2),
      LearnerLevel.MS3: (1, 2),
      LearnerLevel.MS4: (1, 3),
      LearnerLevel.INTERN: (2, 3),
      LearnerLevel.PGY2: (2, 4),
      LearnerLevel.PGY3: (3, 5),
      LearnerLevel.FELLOW: (3, 5),
      LearnerLevel.ATTENDING: (1, 5),  # Full range for self-study
    }
    return level_ranges.get(self.learner_level, (1, 3))


class PanelConfig(BaseModel):
  """Configuration for patient panel generation."""

  # Age distribution
  min_age_months: int = 0
  max_age_months: int = 216  # 18 years

  # Complexity distribution (should sum to 1.0)
  healthy_weight: float = 0.6       # 60% healthy
  single_chronic_weight: float = 0.25  # 25% single chronic condition
  complex_weight: float = 0.15      # 15% complex/multi-condition

  # Panel size
  target_size: int = 20

  # Conditions to include/exclude
  include_conditions: list[str] = Field(default_factory=list)
  exclude_conditions: list[str] = Field(default_factory=list)

  class Config:
    from_attributes = True


class Panel(BaseModel):
  """
  A collection of synthetic patients for a learner.

  Panels provide continuity - the same patients return for follow-up visits.
  """

  id: UUID
  name: str
  description: Optional[str] = None
  owner_id: UUID
  config: PanelConfig = Field(default_factory=PanelConfig)
  patient_count: int = 0
  created_at: datetime = Field(default_factory=datetime.utcnow)
  updated_at: datetime = Field(default_factory=datetime.utcnow)

  class Config:
    from_attributes = True

  @classmethod
  def from_db(cls, data: dict) -> "Panel":
    """Create Panel from database row."""
    # Parse config from JSONB
    config_data = data.get("config", {})
    if isinstance(config_data, dict):
      config = PanelConfig(**config_data)
    else:
      config = PanelConfig()

    return cls(
      id=UUID(data["id"]) if isinstance(data["id"], str) else data["id"],
      name=data["name"],
      description=data.get("description"),
      owner_id=UUID(data["owner_id"]) if isinstance(data["owner_id"], str) else data["owner_id"],
      config=config,
      patient_count=data.get("patient_count", 0),
      created_at=data.get("created_at", datetime.utcnow()),
      updated_at=data.get("updated_at", datetime.utcnow()),
    )


class Session(BaseModel):
  """
  A learning session where a user interacts with an encounter.

  Tracks the learner's work and feedback received.
  """

  id: UUID
  user_id: UUID
  encounter_id: UUID
  started_at: datetime = Field(default_factory=datetime.utcnow)
  completed_at: Optional[datetime] = None

  # Learner's work
  learner_notes: Optional[str] = None
  learner_hpi: Optional[str] = None
  learner_assessment: Optional[str] = None
  learner_plan: Optional[str] = None
  learner_billing: Optional[dict] = None

  # AI feedback
  echo_transcript: Optional[list[dict]] = None
  documentation_score: Optional[dict] = None
  billing_score: Optional[dict] = None

  # Overall score
  score: Optional[dict] = None

  class Config:
    from_attributes = True

  @property
  def is_complete(self) -> bool:
    """Check if session is completed."""
    return self.completed_at is not None

  @property
  def duration_minutes(self) -> Optional[float]:
    """Get session duration in minutes."""
    if self.completed_at and self.started_at:
      delta = self.completed_at - self.started_at
      return delta.total_seconds() / 60
    return None


class Review(BaseModel):
  """
  Spaced repetition review tracking (SM-2 algorithm).

  Determines when a case should be reviewed again.
  """

  id: UUID
  user_id: UUID
  encounter_id: UUID

  # SM-2 fields
  next_review: datetime
  ease_factor: float = 2.5
  interval_days: int = 1
  repetitions: int = 0

  # Stats
  correct_count: int = 0
  incorrect_count: int = 0
  last_reviewed_at: Optional[datetime] = None
  created_at: datetime = Field(default_factory=datetime.utcnow)

  class Config:
    from_attributes = True

  @property
  def is_due(self) -> bool:
    """Check if review is due."""
    return datetime.utcnow() >= self.next_review

  @property
  def accuracy(self) -> float:
    """Get accuracy percentage."""
    total = self.correct_count + self.incorrect_count
    if total == 0:
      return 0.0
    return (self.correct_count / total) * 100


class CompetencyProgress(BaseModel):
  """Track learner progress on ACGME/AAP competencies."""

  user_id: UUID
  competency_code: str
  cases_seen: int = 0
  total_score: float = 0
  avg_score: float = 0
  last_seen_at: Optional[datetime] = None

  class Config:
    from_attributes = True

  @property
  def needs_attention(self) -> bool:
    """Check if this competency needs more practice."""
    return self.cases_seen < 3 or self.avg_score < 70


class Feedback(BaseModel):
  """User feedback on generated content."""

  id: UUID
  user_id: UUID
  encounter_id: Optional[UUID] = None
  patient_id: Optional[UUID] = None
  feedback_type: str
  content: str
  status: str = "new"
  admin_notes: Optional[str] = None
  created_at: datetime = Field(default_factory=datetime.utcnow)
  resolved_at: Optional[datetime] = None

  class Config:
    from_attributes = True
