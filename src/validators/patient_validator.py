"""
Post-generation validation for synthetic patients.

Catches clinical implausibilities, coding errors, and internal inconsistencies
that would make a patient record unusable for training or testing.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from .models import ValidationIssue, ValidationResult, ValidationSeverity, ValidationType

if TYPE_CHECKING:
  from src.models import Patient, Encounter, Condition, GrowthMeasurement


class PatientValidator:
  """
  Validates synthetic patient records for clinical plausibility.

  Validation categories:
  1. Temporal consistency (all events after DOB)
  2. Code quality (no garbage ICD-10 codes)
  3. Condition-medication consistency
  4. Chronic condition monitoring requirements
  5. Growth trajectory plausibility
  6. Immunization series integrity
  """

  # ICD-10 codes that should never be primary diagnoses
  GARBAGE_CODES = {
    "R69",    # Illness, unspecified
    "R99",    # Ill-defined and unknown cause of mortality
    "Z00.00", # Encounter for general adult medical examination
  }

  # Conditions that require medications when active
  # Maps condition keywords to medication keywords that should be present
  CONDITIONS_REQUIRING_MEDS = {
    "leukemia": ["mercaptopurine", "methotrexate", "vincristine", "chemotherapy", "6-mp"],
    "lymphoma": ["chemotherapy", "rituximab", "prednisone"],
    "type 1 diabetes": ["insulin"],
    "type 2 diabetes": ["metformin", "insulin", "glipizide", "jardiance"],
    "hypothyroidism": ["levothyroxine", "synthroid"],
    "hyperthyroidism": ["methimazole", "ptu"],
    "seizure": ["levetiracetam", "keppra", "valproic", "lamotrigine", "phenobarbital"],
    "epilepsy": ["levetiracetam", "keppra", "valproic", "lamotrigine", "phenobarbital"],
    "asthma": ["albuterol", "fluticasone", "budesonide", "montelukast"],
    "adhd": ["methylphenidate", "adderall", "vyvanse", "strattera", "guanfacine"],
  }

  # Conditions that require labs at chronic follow-up
  CONDITIONS_REQUIRING_LABS = {
    "leukemia": ["cbc", "complete blood count", "metabolic panel", "cmp"],
    "lymphoma": ["cbc", "complete blood count", "metabolic panel"],
    "type 1 diabetes": ["hemoglobin a1c", "hba1c", "glucose"],
    "type 2 diabetes": ["hemoglobin a1c", "hba1c", "glucose", "lipid"],
    "hypothyroidism": ["tsh", "thyroid"],
    "chronic kidney disease": ["creatinine", "bun", "gfr", "cmp"],
  }

  def validate(self, patient: Patient) -> ValidationResult:
    """
    Run all validation checks on a patient.

    Returns ValidationResult with all issues found.
    """
    issues: list[ValidationIssue] = []

    # Run all validators
    issues.extend(self._validate_temporal_consistency(patient))
    issues.extend(self._validate_icd10_codes(patient))
    issues.extend(self._validate_condition_medications(patient))
    issues.extend(self._validate_chronic_condition_labs(patient))
    issues.extend(self._validate_growth_trajectory(patient))
    issues.extend(self._validate_immunization_series(patient))
    issues.extend(self._validate_encounter_internal_consistency(patient))

    # Determine overall validity (no critical or error issues)
    has_blocking_issues = any(
      i.severity in (ValidationSeverity.CRITICAL, ValidationSeverity.ERROR)
      for i in issues
    )

    return ValidationResult(
      valid=not has_blocking_issues,
      issues=issues,
    )

  def _validate_temporal_consistency(self, patient: Patient) -> list[ValidationIssue]:
    """Ensure all events occur after date of birth."""
    issues = []
    dob = patient.demographics.date_of_birth

    # Check encounters
    for i, enc in enumerate(patient.encounters):
      enc_date = enc.date.date() if hasattr(enc.date, 'date') else enc.date
      if enc_date < dob:
        issues.append(ValidationIssue(
          type=ValidationType.TEMPORAL,
          severity=ValidationSeverity.CRITICAL,
          message=f"Encounter dated {enc_date} is before DOB {dob}",
          path=f"encounters[{i}].date",
          suggested_fix=f"Set encounter date to {dob + timedelta(days=3)} or later",
          auto_fixable=True,
        ))

    # Check conditions
    for i, cond in enumerate(patient.problem_list):
      if cond.onset_date and cond.onset_date < dob:
        issues.append(ValidationIssue(
          type=ValidationType.TEMPORAL,
          severity=ValidationSeverity.CRITICAL,
          message=f"Condition '{cond.display_name}' onset {cond.onset_date} before DOB {dob}",
          path=f"problem_list[{i}].onset_date",
          auto_fixable=True,
        ))

    # Check immunizations
    for i, imm in enumerate(patient.immunization_record):
      if imm.date and imm.date < dob:
        issues.append(ValidationIssue(
          type=ValidationType.TEMPORAL,
          severity=ValidationSeverity.CRITICAL,
          message=f"Immunization '{imm.display_name}' dated {imm.date} before DOB {dob}",
          path=f"immunization_record[{i}].date",
          auto_fixable=True,
        ))

    return issues

  def _validate_icd10_codes(self, patient: Patient) -> list[ValidationIssue]:
    """Check for garbage/placeholder ICD-10 codes."""
    issues = []

    for i, cond in enumerate(patient.problem_list):
      code = cond.code.code if cond.code else None
      if code in self.GARBAGE_CODES:
        issues.append(ValidationIssue(
          type=ValidationType.CODING,
          severity=ValidationSeverity.ERROR,
          message=f"Invalid ICD-10 code '{code}' for '{cond.display_name}'",
          path=f"problem_list[{i}].code.code",
          suggested_fix=f"Look up correct ICD-10 for '{cond.display_name}'",
          auto_fixable=True,
        ))

    return issues

  def _validate_condition_medications(self, patient: Patient) -> list[ValidationIssue]:
    """Check that conditions requiring medications have them."""
    issues = []

    # Get active condition names (lowercase for matching)
    active_conditions = [
      c.display_name.lower()
      for c in patient.problem_list
      if c.clinical_status.value == "active"
    ]

    # Get active medication names (lowercase)
    active_meds = [
      m.display_name.lower()
      for m in patient.medication_list
      if m.status.value == "active"
    ]

    for condition_keyword, required_med_keywords in self.CONDITIONS_REQUIRING_MEDS.items():
      # Check if patient has this condition
      has_condition = any(condition_keyword in c for c in active_conditions)

      if has_condition:
        # Check if they have any of the required medications
        has_required_med = any(
          any(med_kw in med for med_kw in required_med_keywords)
          for med in active_meds
        )

        if not has_required_med:
          issues.append(ValidationIssue(
            type=ValidationType.MEDICATION,
            severity=ValidationSeverity.ERROR,
            message=f"Active '{condition_keyword}' condition has no expected medications",
            path="medication_list",
            suggested_fix=f"Add appropriate medications for {condition_keyword}",
            auto_fixable=True,
          ))

    return issues

  def _validate_chronic_condition_labs(self, patient: Patient) -> list[ValidationIssue]:
    """Check that chronic conditions have monitoring labs at follow-up visits."""
    issues = []

    # Get active chronic condition keywords
    active_conditions = [
      c.display_name.lower()
      for c in patient.problem_list
      if c.clinical_status.value == "active"
    ]

    # Find chronic follow-up encounters
    chronic_followups = [
      enc for enc in patient.encounters
      if enc.type.value == "chronic-followup"
    ]

    for condition_keyword, required_lab_keywords in self.CONDITIONS_REQUIRING_LABS.items():
      has_condition = any(condition_keyword in c for c in active_conditions)

      if has_condition and chronic_followups:
        # Check if ANY follow-up has labs
        has_any_labs = False
        for enc in chronic_followups:
          if enc.lab_results:
            lab_names = [lab.display_name.lower() for lab in enc.lab_results]
            if any(
              any(kw in lab for kw in required_lab_keywords)
              for lab in lab_names
            ):
              has_any_labs = True
              break

        if not has_any_labs:
          issues.append(ValidationIssue(
            type=ValidationType.LAB,
            severity=ValidationSeverity.ERROR,
            message=f"'{condition_keyword}' has {len(chronic_followups)} follow-up(s) but no monitoring labs",
            path="encounters[].lab_results",
            suggested_fix=f"Add {required_lab_keywords[0]} labs to chronic follow-up encounters",
            auto_fixable=True,
          ))

    return issues

  def _validate_growth_trajectory(self, patient: Patient) -> list[ValidationIssue]:
    """Check for implausible growth patterns."""
    issues = []

    if len(patient.growth_data) < 2:
      return issues

    # Sort by date
    sorted_growth = sorted(patient.growth_data, key=lambda g: g.date)

    for i in range(1, len(sorted_growth)):
      prev = sorted_growth[i - 1]
      curr = sorted_growth[i]

      days_diff = (curr.date - prev.date).days
      if days_diff <= 0:
        continue

      # Check for identical weights over >14 days (implausible)
      if prev.weight_kg and curr.weight_kg:
        if abs(curr.weight_kg - prev.weight_kg) < 0.01 and days_diff > 14:
          issues.append(ValidationIssue(
            type=ValidationType.GROWTH,
            severity=ValidationSeverity.WARNING,
            message=f"Identical weight ({curr.weight_kg}kg) across {days_diff} days",
            path=f"growth_data[{i}]",
            suggested_fix="Regenerate growth measurement with appropriate trajectory",
          ))

      # Check for weight loss in infants (usually pathological)
      if prev.weight_kg and curr.weight_kg:
        age_months = (curr.date - patient.demographics.date_of_birth).days // 30
        weight_change = curr.weight_kg - prev.weight_kg

        if age_months < 12 and weight_change < -0.2 and days_diff > 7:
          issues.append(ValidationIssue(
            type=ValidationType.GROWTH,
            severity=ValidationSeverity.WARNING,
            message=f"Infant weight loss ({weight_change:.2f}kg) over {days_diff} days",
            path=f"growth_data[{i}]",
            suggested_fix="Verify this is intentional (e.g., FTT scenario)",
          ))

    return issues

  def _validate_immunization_series(self, patient: Patient) -> list[ValidationIssue]:
    """Check for gaps in immunization series."""
    issues = []

    # Group immunizations by vaccine
    by_vaccine: dict[str, list] = {}
    for imm in patient.immunization_record:
      name = imm.display_name
      if name not in by_vaccine:
        by_vaccine[name] = []
      by_vaccine[name].append(imm)

    # Expected doses for common series
    expected_doses = {
      "DTaP": 5,
      "Hib": 4,
      "PCV": 4,
      "IPV": 4,
      "HepB": 3,
      "MMR": 2,
      "VAR": 2,
      "HepA": 2,
      "RV": 3,
    }

    for vaccine, expected in expected_doses.items():
      if vaccine in by_vaccine:
        doses = by_vaccine[vaccine]
        dose_numbers = sorted([d.dose_number for d in doses if d.dose_number])

        # Check for gaps (e.g., [1, 2, 4, 5] missing 3)
        if dose_numbers:
          for i in range(1, max(dose_numbers)):
            if i not in dose_numbers:
              issues.append(ValidationIssue(
                type=ValidationType.IMMUNIZATION,
                severity=ValidationSeverity.WARNING,
                message=f"{vaccine} series has dose #{i} missing (has doses {dose_numbers})",
                path="immunization_record",
                suggested_fix=f"Add {vaccine} dose #{i} or remove later doses",
              ))

    return issues

  def _validate_encounter_internal_consistency(self, patient: Patient) -> list[ValidationIssue]:
    """Check internal consistency within encounters."""
    issues = []

    for i, enc in enumerate(patient.encounters):
      # Check for narrative mentioning things not in structured data
      if enc.narrative_note:
        narrative_lower = enc.narrative_note.lower()

        # Check for chemotherapy mention without chemo meds
        chemo_keywords = ["chemotherapy", "chemo protocol", "maintenance therapy", "6-mp", "methotrexate"]
        mentions_chemo = any(kw in narrative_lower for kw in chemo_keywords)

        if mentions_chemo:
          # Check if prescriptions or patient meds include chemo
          rx_names = [p.display_name.lower() for p in (enc.prescriptions or [])]
          all_meds = [m.display_name.lower() for m in patient.medication_list]

          has_chemo_med = any(
            any(kw in med for kw in ["mercaptopurine", "methotrexate", "vincristine", "6-mp"])
            for med in (rx_names + all_meds)
          )

          if not has_chemo_med:
            issues.append(ValidationIssue(
              type=ValidationType.NARRATIVE,
              severity=ValidationSeverity.ERROR,
              message="Encounter narrative mentions chemotherapy but no chemo meds in record",
              path=f"encounters[{i}].narrative_note",
              suggested_fix="Add chemotherapy medications or regenerate narrative",
              auto_fixable=True,
            ))

    return issues
