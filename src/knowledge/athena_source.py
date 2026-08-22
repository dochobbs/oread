"""
Athena knowledge source for Oread.

Fetches curated condition definitions from the Athena knowledge service
(port 9105) as a middle layer in ConditionKnowledgeService: after local
YAML and disk cache, before ungrounded web-search/LLM retrieval.

Design mirrors echo/src/frameworks/loader.py: direct sync httpx call,
short timeout, graceful None on any failure. Athena is an enrichment
layer — generation must work identically when it is unreachable.
"""

from __future__ import annotations

import os

import httpx

from .models import ConditionDefinition, ExamFinding, LabDefinition, MedicationDefinition

ATHENA_URL = os.environ.get("ATHENA_URL", "http://localhost:9105")
ATHENA_TIMEOUT_SECONDS = 3.0


def athena_enabled() -> bool:
  """Check whether Athena lookups are enabled (OREAD_DISABLE_ATHENA opts out)."""
  return os.environ.get("OREAD_DISABLE_ATHENA", "").strip().lower() not in {
    "1", "true", "yes", "on",
  }


def fetch_condition_from_athena(
  name: str,
  specialty: str = "pediatrics",
  base_url: str | None = None,
) -> dict | None:
  """Fetch one condition definition from Athena by normalized name.

  Args:
    name: Normalized snake_case condition key (e.g. 'otitis_media').
    specialty: Specialty pool to confirm membership ('pediatrics',
      'internal_medicine', 'family_practice').
    base_url: Override the Athena base URL (for tests).

  Returns:
    Raw Athena condition dict, or None when disabled/unreachable/unknown.
  """
  if not athena_enabled():
    return None
  try:
    response = httpx.get(
      f"{base_url or ATHENA_URL}/api/conditions/{name}",
      params={"specialty": specialty},
      timeout=ATHENA_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
      return None
    data = response.json()
    return data if isinstance(data, dict) and data.get("id") else None
  except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
    return None


def athena_condition_to_definition(data: dict) -> ConditionDefinition:
  """Map an Athena condition payload onto Oread's ConditionDefinition.

  Athena's condition schema is Oread-compatible by design ("Clinical data
  (Oread-compatible)" section in athena/src/models.py); this adapter makes
  the mapping explicit so schema drift fails loudly here instead of
  silently degrading generation.
  """
  presentation = data.get("presentation") or {}
  diagnostics = data.get("diagnostics") or {}
  treatment = data.get("treatment") or {}

  symptoms = [
    s.get("name", "")
    for s in presentation.get("symptoms", [])
    if isinstance(s, dict) and s.get("name")
  ]

  exam_findings = [
    ExamFinding(
      system=f.get("system", ""),
      finding=f.get("finding", ""),
      probability=f.get("probability", 0.8),
    )
    for f in presentation.get("physical_exam", [])
    if isinstance(f, dict) and f.get("finding")
  ]

  labs = [
    LabDefinition(
      name=l.get("name", ""),
      loinc=l.get("loinc"),
      value_type=l.get("value_type", "binary"),
      unit=l.get("unit"),
      normal_range_low=l.get("normal_range_low"),
      normal_range_high=l.get("normal_range_high"),
      probability_abnormal=l.get("probability_abnormal", 0.3),
      required_at_followup=l.get("required_at_followup", False),
    )
    for l in diagnostics.get("labs", [])
    if isinstance(l, dict) and l.get("name")
  ]

  medications = [
    MedicationDefinition(
      agent=m.get("agent", ""),
      rxnorm=m.get("rxnorm"),
      dose_mg_kg=m.get("dose_mg_kg"),
      max_dose_mg=m.get("max_dose_mg"),
      frequency=m.get("frequency"),
      route=m.get("route", "oral"),
      indication=m.get("indication"),
    )
    for m in treatment.get("medications", [])
    if isinstance(m, dict) and m.get("agent")
  ]

  icd10_raw = data.get("icd10") or []
  icd10_codes = icd10_raw if isinstance(icd10_raw, list) else [icd10_raw]

  # Athena allows 'newborn' as a category; Oread's engine only branches
  # on acute vs chronic, so normalize.
  category = data.get("category", "acute")
  if category not in ("acute", "chronic"):
    category = "acute"

  return ConditionDefinition(
    condition_key=data.get("id", ""),
    display_name=data.get("display_name", ""),
    aliases=data.get("aliases", []),
    icd10_codes=icd10_codes,
    icd10_primary=icd10_codes[0] if icd10_codes else None,
    snomed_code=data.get("snomed") or None,
    category=category,
    body_system=data.get("system"),
    typical_symptoms=symptoms,
    physical_exam_findings=exam_findings,
    labs=labs,
    imaging=diagnostics.get("imaging", []) or [],
    medications=medications,
    treatment_approach=treatment.get("approach"),
    managed_by_specialty=treatment.get("managed_by_specialty", False),
    specialty=treatment.get("specialty"),
    requires_monitoring_labs=False,
    monitoring_lab_types=[],
    followup_frequency=None,
    source="athena",
    confidence=0.95,
  )
