"""Models for condition knowledge."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LabDefinition(BaseModel):
  """Definition of a lab test."""
  name: str = Field(description="Lab test name")
  loinc: str | None = Field(default=None, description="LOINC code")
  value_type: str = Field(default="binary", description="'binary' or 'numeric'")
  unit: str | None = Field(default=None, description="Unit for numeric values")
  normal_range_low: float | None = Field(default=None, description="Lower bound of normal")
  normal_range_high: float | None = Field(default=None, description="Upper bound of normal")
  probability_abnormal: float = Field(default=0.3, description="Probability of abnormal result")
  required_at_followup: bool = Field(default=False, description="Required at chronic follow-up")
  monitoring_frequency: str | None = Field(default=None, description="How often to check")


class MedicationDefinition(BaseModel):
  """Definition of a medication for a condition."""
  agent: str = Field(description="Medication name")
  rxnorm: str | None = Field(default=None, description="RxNorm code")
  dose_mg_kg: float | None = Field(default=None, description="Weight-based dose")
  max_dose_mg: float | None = Field(default=None, description="Maximum dose cap")
  frequency: str | None = Field(default=None, description="Dosing frequency")
  route: str = Field(default="oral", description="Route of administration")
  indication: str | None = Field(default=None, description="Why prescribed")
  line: str = Field(default="first", description="'first', 'second', or 'alternative'")
  contraindicated_by: list[str] = Field(default_factory=list, description="Allergens that contraindicate")
  escalation_from: list[str] = Field(default_factory=list, description="Meds this escalates from")


class ExamFinding(BaseModel):
  """Physical exam finding."""
  system: str = Field(description="Body system (heent, respiratory, etc.)")
  finding: str = Field(description="The finding text")
  probability: float = Field(default=0.8, description="Probability of finding present")


class ConditionDefinition(BaseModel):
  """
  Complete condition definition from YAML or dynamic retrieval.

  This is the unified interface - both curated YAML conditions and
  dynamically-retrieved conditions produce this structure.
  """
  condition_key: str = Field(description="Snake_case key for the condition")
  display_name: str = Field(description="Human-readable condition name")
  aliases: list[str] = Field(default_factory=list, description="Alternative names")

  # Coding
  icd10_codes: list[str] = Field(default_factory=list, description="All applicable ICD-10 codes")
  icd10_primary: str | None = Field(default=None, description="Preferred ICD-10 code")
  snomed_code: str | None = Field(default=None, description="SNOMED CT code")

  # Classification
  category: str = Field(default="acute", description="'acute' or 'chronic'")
  body_system: str | None = Field(default=None, description="Primary body system")

  # Clinical presentation
  typical_symptoms: list[str] = Field(default_factory=list, description="Expected symptoms")
  physical_exam_findings: list[ExamFinding] = Field(default_factory=list, description="PE findings")

  # Diagnostics
  labs: list[LabDefinition] = Field(default_factory=list, description="Lab tests")
  imaging: list[dict] = Field(default_factory=list, description="Imaging studies")

  # Treatment
  medications: list[MedicationDefinition] = Field(default_factory=list, description="Medications")
  treatment_approach: str | None = Field(default=None, description="General approach")
  managed_by_specialty: bool = Field(default=False, description="Requires specialist")
  specialty: str | None = Field(default=None, description="Which specialty")

  # Monitoring (for chronic conditions)
  requires_monitoring_labs: bool = Field(default=False, description="Needs follow-up labs")
  monitoring_lab_types: list[str] = Field(default_factory=list, description="What labs to monitor")
  followup_frequency: str | None = Field(default=None, description="How often to follow up")

  # Metadata
  source: str = Field(default="yaml", description="'yaml', 'cache', 'web_search', 'llm'")
  needs_verification: bool = Field(default=False, description="Codes need human review")
  confidence: float = Field(default=1.0, description="Confidence in accuracy")


class ConditionLookupResult(BaseModel):
  """Result of a condition lookup."""
  found: bool = Field(description="Whether condition was found")
  definition: ConditionDefinition | None = Field(default=None, description="The definition if found")
  source: str | None = Field(default=None, description="Where it came from")
  cached: bool = Field(default=False, description="Whether from cache")
