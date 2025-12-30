"""
Patient reconciliation.

Ensures bidirectional consistency between narrative notes and structured data.
When discrepancies are found, either the structured data is augmented or
the narrative is flagged for regeneration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import (
  Discrepancy,
  DiscrepancyType,
  NarrativeClaim,
  ClaimType,
  ReconciliationResult,
)
from .claim_extractor import NarrativeClaimExtractor

if TYPE_CHECKING:
  from src.models import Patient, Encounter


class PatientReconciler:
  """
  Reconciles narrative notes with structured patient data.

  Process:
  1. Extract claims from each encounter's narrative
  2. Compare claims to structured data
  3. Identify discrepancies
  4. Determine if structured data should be augmented or narrative regenerated

  Usage:
    reconciler = PatientReconciler(llm_client)
    result = reconciler.reconcile(patient)

    if result.narratives_to_regenerate:
      # Handle narratives that need regeneration
      pass
  """

  # Keywords that indicate chemotherapy
  CHEMO_KEYWORDS = [
    "chemotherapy", "chemo protocol", "maintenance therapy",
    "oncology protocol", "cancer treatment", "leukemia treatment",
  ]

  # Medication keywords that indicate actual chemotherapy agents
  CHEMO_MED_KEYWORDS = [
    "mercaptopurine", "6-mp", "methotrexate", "vincristine",
    "dexamethasone", "prednisone", "asparaginase", "daunorubicin",
    "cytarabine", "cyclophosphamide",
  ]

  def __init__(self, llm_client: Any):
    self.llm = llm_client
    self.claim_extractor = NarrativeClaimExtractor(llm_client)

  def reconcile(self, patient: Patient) -> ReconciliationResult:
    """
    Reconcile all narrative notes with structured data.

    Args:
      patient: The Patient object to reconcile

    Returns:
      ReconciliationResult with discrepancies and action items
    """
    all_discrepancies: list[Discrepancy] = []

    for encounter in patient.encounters:
      if not encounter.narrative_note:
        continue

      # Extract claims from narrative
      claims = self.claim_extractor.extract(encounter.narrative_note)

      # Get structured facts for comparison
      structured = self._get_structured_facts(encounter, patient)

      # Find discrepancies
      discrepancies = self._find_discrepancies(
        encounter_id=encounter.id,
        claims=claims,
        structured=structured,
      )

      all_discrepancies.extend(discrepancies)

    # Determine which narratives need regeneration
    narratives_to_regen = list(set(
      d.encounter_id for d in all_discrepancies
      if d.requires_narrative_regeneration
    ))

    return ReconciliationResult(
      discrepancies=all_discrepancies,
      narratives_to_regenerate=narratives_to_regen,
    )

  def _get_structured_facts(
    self,
    encounter: Encounter,
    patient: Patient
  ) -> dict:
    """Extract structured facts from encounter and patient for comparison."""
    return {
      # Encounter-specific data
      "encounter_prescriptions": [
        p.display_name.lower() for p in (encounter.prescriptions or [])
      ],
      "encounter_labs": [
        lab.display_name.lower() for lab in (encounter.lab_results or [])
      ],
      "encounter_diagnoses": [
        a.diagnosis.lower() for a in (encounter.assessment or [])
      ],

      # Patient-level data
      "active_medications": [
        m.display_name.lower() for m in patient.medication_list
        if m.status.value == "active"
      ],
      "all_medications": [
        m.display_name.lower() for m in patient.medication_list
      ],
      "active_conditions": [
        c.display_name.lower() for c in patient.problem_list
        if c.clinical_status.value == "active"
      ],
      "all_conditions": [
        c.display_name.lower() for c in patient.problem_list
      ],
    }

  def _find_discrepancies(
    self,
    encounter_id: str,
    claims: list[NarrativeClaim],
    structured: dict,
  ) -> list[Discrepancy]:
    """Compare claims to structured data and identify discrepancies."""
    discrepancies = []

    for claim in claims:
      if claim.claim_type == ClaimType.TREATMENT_STATUS:
        disc = self._check_treatment_status_claim(encounter_id, claim, structured)
        if disc:
          discrepancies.append(disc)

      elif claim.claim_type == ClaimType.MEDICATION:
        disc = self._check_medication_claim(encounter_id, claim, structured)
        if disc:
          discrepancies.append(disc)

      elif claim.claim_type == ClaimType.CONDITION:
        disc = self._check_condition_claim(encounter_id, claim, structured)
        if disc:
          discrepancies.append(disc)

    return discrepancies

  def _check_treatment_status_claim(
    self,
    encounter_id: str,
    claim: NarrativeClaim,
    structured: dict,
  ) -> Discrepancy | None:
    """Check claims about treatment status (e.g., 'on chemotherapy')."""
    claim_lower = claim.claim_text.lower()

    # Check for chemotherapy mentions
    mentions_chemo = any(kw in claim_lower for kw in self.CHEMO_KEYWORDS)

    if mentions_chemo:
      # Verify patient has oncology condition
      has_onc_condition = any(
        any(kw in cond for kw in ["leukemia", "lymphoma", "cancer", "tumor", "malignancy"])
        for cond in structured["active_conditions"]
      )

      # Verify patient has chemo medications
      all_meds = structured["active_medications"] + structured["all_medications"]
      has_chemo_meds = any(
        any(kw in med for kw in self.CHEMO_MED_KEYWORDS)
        for med in all_meds
      )

      if has_onc_condition and not has_chemo_meds:
        return Discrepancy(
          encounter_id=encounter_id,
          claim=claim,
          discrepancy_type=DiscrepancyType.TREATMENT_STATUS_MISMATCH,
          can_add_to_structured=True,
          requires_narrative_regeneration=False,
          suggested_fix="Add chemotherapy medications to patient medication list",
        )

      if not has_onc_condition:
        return Discrepancy(
          encounter_id=encounter_id,
          claim=claim,
          discrepancy_type=DiscrepancyType.TREATMENT_STATUS_MISMATCH,
          can_add_to_structured=False,
          requires_narrative_regeneration=True,
          suggested_fix="Narrative mentions chemotherapy but patient has no oncology diagnosis",
        )

    return None

  def _check_medication_claim(
    self,
    encounter_id: str,
    claim: NarrativeClaim,
    structured: dict,
  ) -> Discrepancy | None:
    """Check claims about specific medications."""
    # Extract medication name from claim if possible
    med_name = claim.structured_value.get("medication_name", "").lower()

    if not med_name:
      return None

    # Check if medication exists in structured data
    all_meds = (
      structured["encounter_prescriptions"] +
      structured["active_medications"] +
      structured["all_medications"]
    )

    med_found = any(med_name in med for med in all_meds)

    if not med_found:
      return Discrepancy(
        encounter_id=encounter_id,
        claim=claim,
        discrepancy_type=DiscrepancyType.MEDICATION_NOT_IN_STRUCTURED,
        can_add_to_structured=True,
        requires_narrative_regeneration=False,
        suggested_fix=f"Add '{med_name}' to medication list or remove from narrative",
      )

    return None

  def _check_condition_claim(
    self,
    encounter_id: str,
    claim: NarrativeClaim,
    structured: dict,
  ) -> Discrepancy | None:
    """Check claims about conditions/diagnoses."""
    condition_name = claim.structured_value.get("condition_name", "").lower()

    if not condition_name:
      return None

    # Check if condition exists
    all_conditions = structured["active_conditions"] + structured["all_conditions"]
    condition_found = any(condition_name in cond for cond in all_conditions)

    if not condition_found:
      return Discrepancy(
        encounter_id=encounter_id,
        claim=claim,
        discrepancy_type=DiscrepancyType.CONDITION_NOT_IN_STRUCTURED,
        can_add_to_structured=False,
        requires_narrative_regeneration=True,
        suggested_fix=f"Narrative mentions '{condition_name}' not in problem list",
      )

    return None
