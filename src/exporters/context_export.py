"""
PatientContext exporter for Oread.

Converts Oread's nested Patient model to the flat PatientContext format
used by Echo, Metis, and other MedEd platform services.
"""

from __future__ import annotations
from typing import Any

from src.models import Patient


def patient_to_context(patient: Patient) -> dict[str, Any]:
  """Convert an Oread Patient to Echo's PatientContext format.

  Args:
    patient: Oread Patient model instance.

  Returns:
    Dictionary matching Echo's PatientContext schema with flat fields.
  """
  demo = patient.demographics

  # Build problem list
  problem_list = []
  for c in patient.problem_list:
    entry: dict[str, Any] = {
      "display_name": c.display_name,
      "is_active": c.is_active,
    }
    if c.code:
      entry["code"] = c.code.code
      entry["code_system"] = c.code.system
    problem_list.append(entry)

  # Build medication list
  medication_list = []
  for m in patient.medication_list:
    if m.status.value != "active":
      continue
    entry = {
      "display_name": m.display_name,
      "dose": f"{m.dose_quantity} {m.dose_unit}",
      "frequency": m.frequency,
      "is_active": True,
    }
    if m.code:
      entry["code"] = m.code.code
      entry["code_system"] = m.code.system
    medication_list.append(entry)

  # Build allergy list
  allergy_list = []
  for a in patient.allergy_list:
    reactions = a.reactions
    reaction_str = reactions[0].manifestation if reactions else None
    allergy_list.append({
      "display_name": a.display_name,
      "reaction": reaction_str,
      "severity": a.criticality,
    })

  # Build recent encounters (last 5)
  sorted_encounters = sorted(
    patient.encounters,
    key=lambda e: e.date,
    reverse=True,
  )
  recent_encounters = []
  for e in sorted_encounters[:5]:
    recent_encounters.append({
      "date": e.date.isoformat(),
      "type": e.type.value,
      "chief_complaint": e.chief_complaint,
    })

  # Build family history summary
  family_history = None
  if patient.family_history:
    items = []
    for fh in patient.family_history:
      conditions = ", ".join(fh.conditions) if fh.conditions else ""
      if conditions:
        items.append(f"{fh.relationship}: {conditions}")
    if items:
      family_history = "; ".join(items)

  return {
    "patient_id": patient.id,
    "source": "oread",
    "name": demo.full_name,
    "age_years": demo.age_years,
    "age_months": demo.age_months,
    "sex": demo.sex_at_birth.value,
    "problem_list": problem_list,
    "medication_list": medication_list,
    "allergy_list": allergy_list,
    "recent_encounters": recent_encounters,
    "family_history": family_history,
  }
