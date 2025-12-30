"""Models for narrative-structured reconciliation."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


class ClaimType(str, Enum):
  """Type of factual claim extracted from narrative."""
  MEDICATION = "medication"
  CONDITION = "condition"
  PROCEDURE = "procedure"
  LAB = "lab"
  VITAL = "vital"
  TREATMENT_STATUS = "treatment_status"
  SOCIAL = "social"


class NarrativeClaim(BaseModel):
  """A factual claim extracted from a narrative note."""
  claim_type: ClaimType = Field(description="Category of the claim")
  claim_text: str = Field(description="The exact text making the claim")
  structured_value: dict = Field(default_factory=dict, description="Parsed interpretation")
  confidence: float = Field(default=0.8, description="Confidence in extraction")


class DiscrepancyType(str, Enum):
  """Type of discrepancy between narrative and structured data."""
  MEDICATION_NOT_IN_STRUCTURED = "medication_not_in_structured"
  CONDITION_NOT_IN_STRUCTURED = "condition_not_in_structured"
  LAB_NOT_IN_STRUCTURED = "lab_not_in_structured"
  TREATMENT_STATUS_MISMATCH = "treatment_status_mismatch"
  VITAL_MISMATCH = "vital_mismatch"


class Discrepancy(BaseModel):
  """A discrepancy between narrative and structured data."""
  encounter_id: str = Field(description="ID of the encounter with the discrepancy")
  claim: NarrativeClaim = Field(description="The claim that caused the discrepancy")
  discrepancy_type: DiscrepancyType = Field(description="Type of discrepancy")
  can_add_to_structured: bool = Field(default=False, description="Can fix by adding to structured data")
  requires_narrative_regeneration: bool = Field(default=False, description="Needs narrative rewrite")
  suggested_fix: str | None = Field(default=None, description="How to resolve")


class ReconciliationResult(BaseModel):
  """Result of reconciling a patient's narratives with structured data."""
  discrepancies: list[Discrepancy] = Field(default_factory=list)
  narratives_to_regenerate: list[str] = Field(default_factory=list, description="Encounter IDs to regenerate")

  @property
  def has_issues(self) -> bool:
    """Check if any discrepancies were found."""
    return len(self.discrepancies) > 0

  def get_discrepancies_for_encounter(self, encounter_id: str) -> list[Discrepancy]:
    """Get all discrepancies for a specific encounter."""
    return [d for d in self.discrepancies if d.encounter_id == encounter_id]
