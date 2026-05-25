"""Validation result models."""

from enum import Enum
from pydantic import BaseModel, Field


class ValidationSeverity(str, Enum):
  """Severity level of a validation issue."""
  CRITICAL = "critical"  # Breaks clinical plausibility
  ERROR = "error"        # Incorrect but recoverable
  WARNING = "warning"    # Suspicious but possibly valid


class ValidationType(str, Enum):
  """Category of validation issue."""
  TEMPORAL = "temporal"
  CODING = "coding"
  MEDICATION = "medication"
  LAB = "lab"
  GROWTH = "growth"
  IMMUNIZATION = "immunization"
  NARRATIVE = "narrative"
  CONSISTENCY = "consistency"
  AGE_GATE = "age_gate"


class ValidationIssue(BaseModel):
  """A single validation issue found in a patient record."""
  type: ValidationType = Field(description="Category of the validation issue")
  severity: ValidationSeverity = Field(description="How serious the issue is")
  message: str = Field(description="Human-readable description of the issue")
  path: str | None = Field(default=None, description="JSONPath to the problematic field")
  suggested_fix: str | None = Field(default=None, description="How to resolve the issue")
  auto_fixable: bool = Field(default=False, description="Whether this can be fixed automatically")


class ValidationResult(BaseModel):
  """Complete validation result for a patient."""
  valid: bool = Field(description="True if no critical/error issues")
  issues: list[ValidationIssue] = Field(default_factory=list)

  @property
  def critical_issues(self) -> list[ValidationIssue]:
    """Get all critical severity issues."""
    return [i for i in self.issues if i.severity == ValidationSeverity.CRITICAL]

  @property
  def errors(self) -> list[ValidationIssue]:
    """Get all error severity issues."""
    return [i for i in self.issues if i.severity == ValidationSeverity.ERROR]

  @property
  def warnings(self) -> list[ValidationIssue]:
    """Get all warning severity issues."""
    return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]
