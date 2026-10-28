"""
PatientContext exporter for Oread.

Converts Oread's nested Patient model to the PatientContext format shared
across the MedEd platform (Metis `shared/models/context.schema.json`,
generated into `src/models/_generated/context.py`).

Two shapes:

- Shared (``echo_compat=False``): validates against the generated
  `PatientContext`. Codes are CodeableConcept objects.
- Echo-compatible (``echo_compat=True``, the default, served by
  `GET /api/patients/{id}/context`): identical to the shared shape except
  that `problem_list[].code` and `medication_list[].code` are flattened to
  the bare code string (system in `code_system`). Echo's own
  `PatientContext` model types `code` as `str`, so sending the
  CodeableConcept object there fails validation. Flip the default once Echo
  accepts the shared shape.

Both shapes also carry the legacy convenience fields Echo reads
(`is_active`, `dose`, allergy `reaction`/`severity`); the generated
models ignore them.
"""

from __future__ import annotations

from typing import Any

from src.models import Patient
from src.models._generated import PatientContext as SharedPatientContext


def _codeable(concept: Any) -> dict[str, str] | None:
  if concept is None:
    return None
  return {
    "system": concept.system,
    "code": concept.code,
    "display": concept.display,
  }


def _flatten_code(entry: dict[str, Any]) -> None:
  """Echo-compat: replace a CodeableConcept `code` with its code string."""
  concept = entry.get("code")
  if isinstance(concept, dict):
    entry["code"] = concept.get("code")
    entry["code_system"] = concept.get("system")


def _condition_entry(c: Any) -> dict[str, Any]:
  return {
    "id": c.id,
    "code": _codeable(c.code),
    "display_name": c.display_name,
    "category": c.category,
    "clinical_status": c.clinical_status.value,
    "verification_status": c.verification_status.value,
    "severity": c.severity.value if c.severity else None,
    "onset_date": c.onset_date.isoformat(),
    "onset_age_description": c.onset_age_description,
    "abatement_date": c.abatement_date.isoformat() if c.abatement_date else None,
    "recorded_date": c.recorded_date.isoformat() if c.recorded_date else None,
    "notes": c.notes,
    "diagnosed_by": c.diagnosed_by,
    "caused_by": c.caused_by,
    "complications": list(c.complications),
    # Legacy field read by Echo's tutor.
    "is_active": c.is_active,
  }


def _medication_entry(m: Any) -> dict[str, Any]:
  return {
    "id": m.id,
    "code": _codeable(m.code),
    "display_name": m.display_name,
    "status": m.status.value,
    "dose_quantity": m.dose_quantity,
    "dose_unit": m.dose_unit,
    "frequency": m.frequency,
    "route": m.route,
    "instructions": m.instructions,
    "prn": m.prn,
    "prn_reason": m.prn_reason,
    "start_date": m.start_date.isoformat(),
    "end_date": m.end_date.isoformat() if m.end_date else None,
    "prescribed_at_encounter": m.prescribed_at_encounter,
    "indication": m.indication,
    "indication_condition_id": m.indication_condition_id,
    "prescriber": m.prescriber,
    "pharmacy": m.pharmacy,
    "refills_remaining": m.refills_remaining,
    "dose_changes": [
      {
        "date": dc.date.isoformat(),
        "previous_dose": dc.previous_dose,
        "new_dose": dc.new_dose,
      }
      for dc in m.dose_changes
    ],
    "discontinuation_reason": m.discontinuation_reason,
    # Legacy fields read by Echo.
    "dose": f"{m.dose_quantity} {m.dose_unit}",
    "is_active": True,
  }


def _allergy_entry(a: Any) -> dict[str, Any]:
  reactions = [
    {
      "manifestation": r.manifestation,
      "severity": r.severity.value if r.severity else None,
    }
    for r in a.reactions
  ]
  return {
    "id": a.id,
    "code": _codeable(a.code),
    "display_name": a.display_name,
    "category": a.category.value,
    "type": a.type.value,
    "criticality": a.criticality,
    "reactions": reactions,
    "onset_date": a.onset_date.isoformat() if a.onset_date else None,
    "recorded_date": a.recorded_date.isoformat() if a.recorded_date else None,
    "clinical_status": a.clinical_status,
    "verification_status": a.verification_status,
    "notes": a.notes,
    # Legacy fields read by Echo.
    "reaction": reactions[0]["manifestation"] if reactions else None,
    "severity": a.criticality,
  }


def _encounter_entry(e: Any) -> dict[str, Any]:
  return {
    "id": e.id,
    # Shared schema types this as a date; Echo coerces either form.
    "date": e.date.date().isoformat(),
    "type": e.type.value,
    "chief_complaint": e.chief_complaint or "",
    "diagnoses": [a.diagnosis for a in e.assessment if a.diagnosis],
    "provider": e.provider.name if e.provider else None,
  }


def _family_history(patient: Patient) -> str | None:
  items = []
  for fh in patient.family_history:
    if fh.condition:
      items.append(f"{fh.relationship}: {fh.condition}")
  return "; ".join(items) if items else None


def _social_history(patient: Patient) -> str | None:
  sh = patient.social_history
  if sh is None:
    return None
  parts = [sh.living_situation]
  if sh.grade_level:
    parts.append(sh.grade_level)
  if sh.occupation:
    parts.append(f"occupation: {sh.occupation}")
  if sh.safety_concerns:
    parts.append(f"safety: {sh.safety_concerns}")
  text = "; ".join(p for p in parts if p)
  return text or None


def patient_to_context(patient: Patient, echo_compat: bool = True) -> dict[str, Any]:
  """Convert an Oread Patient to the platform PatientContext format.

  Args:
    patient: Oread Patient model instance.
    echo_compat: When True (default, used by the `/context` endpoint),
      flatten condition and medication `code` to a string for Echo. When
      False, return the shared-schema shape, validated against the
      generated `PatientContext` model.

  Returns:
    JSON-ready dict.

  Raises:
    pydantic.ValidationError: (echo_compat=False) the output does not match
      the shared schema.
  """
  demo = patient.demographics

  problem_list = [_condition_entry(c) for c in patient.problem_list]
  # Only active medications, as before (Echo lists them as current meds).
  medication_list = [
    _medication_entry(m)
    for m in patient.medication_list
    if m.status.value == "active"
  ]
  allergy_list = [_allergy_entry(a) for a in patient.allergy_list]

  sorted_encounters = sorted(patient.encounters, key=lambda e: e.date, reverse=True)
  recent_encounters = [_encounter_entry(e) for e in sorted_encounters[:5]]

  context: dict[str, Any] = {
    "patient_id": patient.id,
    "source": "oread",
    "name": demo.full_name,
    "date_of_birth": demo.date_of_birth.isoformat(),
    "age_years": demo.age_years,
    "age_months": demo.age_months,
    "sex": demo.sex_at_birth.value,
    "problem_list": problem_list,
    "medication_list": medication_list,
    "allergy_list": allergy_list,
    "recent_encounters": recent_encounters,
    "family_history": _family_history(patient),
    "social_history": _social_history(patient),
  }

  if echo_compat:
    for entry in problem_list + medication_list:
      _flatten_code(entry)
    return context

  # The shared schema's sex enum has no "intersex"; "other" is closest.
  if context["sex"] == "intersex":
    context["sex"] = "other"

  # Validate against the generated shared model; keep the legacy extras.
  SharedPatientContext.model_validate(context)
  return context
