"""
Code-system and value-set tables shared by the C-CDA and FHIR exporters.

Every table cites its source. Keep the C-CDA and FHIR exporters reading from
here so the two formats code the same fact the same way.
"""

from __future__ import annotations

import re
import uuid

# --------------------------------------------------------------------------- #
# Identifiers                                                                 #
# --------------------------------------------------------------------------- #

# Stable identifier namespaces. A 2.25 OID is the ISO/ITU-T joint arc for
# UUID-derived OIDs (ITU-T X.667), so no OID registration is needed.
_OREAD_NS = uuid.UUID("6f8e3c1a-2b4d-5e6f-8a9b-0c1d2e3f4a5b")


def oread_oid(kind: str) -> str:
  """Deterministic 2.25 OID for an Oread identifier namespace (patient, encounter...)."""
  return f"2.25.{uuid.uuid5(_OREAD_NS, kind).int}"


# NPI OID. Source: HL7 OID registry (National Provider Identifier).
NPI_OID = "2.16.840.1.113883.4.6"


# --------------------------------------------------------------------------- #
# Code systems                                                                #
# --------------------------------------------------------------------------- #

# FHIR canonical URI -> (OID, name). Source: HL7 terminology.hl7.org
# NamingSystem entries for each system.
CODE_SYSTEMS: dict[str, tuple[str, str]] = {
  "http://hl7.org/fhir/sid/icd-10-cm": ("2.16.840.1.113883.6.90", "ICD-10-CM"),
  "http://hl7.org/fhir/sid/icd-10": ("2.16.840.1.113883.6.3", "ICD-10"),
  "http://snomed.info/sct": ("2.16.840.1.113883.6.96", "SNOMED CT"),
  "http://www.nlm.nih.gov/research/umls/rxnorm": ("2.16.840.1.113883.6.88", "RxNorm"),
  "http://loinc.org": ("2.16.840.1.113883.6.1", "LOINC"),
  "http://hl7.org/fhir/sid/cvx": ("2.16.840.1.113883.12.292", "CVX"),
  "http://www.ama-assn.org/go/cpt": ("2.16.840.1.113883.6.12", "CPT"),
  "http://fdasis.nlm.nih.gov": ("2.16.840.1.113883.4.9", "UNII"),
  "http://hl7.org/fhir/sid/ndc": ("2.16.840.1.113883.6.69", "NDC"),
  "http://unitsofmeasure.org": ("2.16.840.1.113883.6.8", "UCUM"),
}
_OID_RE = re.compile(r"^[0-2](\.(0|[1-9]\d*))+$")
_SYSTEM_HINTS = (("snomed", "http://snomed.info/sct"), ("rxnorm", "http://www.nlm.nih.gov/research/umls/rxnorm"),
                 ("loinc", "http://loinc.org"), ("icd-10-cm", "http://hl7.org/fhir/sid/icd-10-cm"),
                 ("icd10", "http://hl7.org/fhir/sid/icd-10-cm"), ("cvx", "http://hl7.org/fhir/sid/cvx"),
                 ("unii", "http://fdasis.nlm.nih.gov"), ("cpt", "http://www.ama-assn.org/go/cpt"))


def is_placeholder_code(code: str | None) -> bool:
  """Empty or all-zero codes are generator placeholders, not real codes."""
  c = (code or "").strip()
  return not c or set(c) <= {"0", "-"}


def system_uri(system: str | None) -> str | None:
  """Normalize a system (URI, OID or name) to a FHIR canonical URI."""
  s = (system or "").strip()
  if not s:
    return None
  if s in CODE_SYSTEMS:
    return s
  for uri, (oid, _) in CODE_SYSTEMS.items():
    if s == oid or s == f"urn:oid:{oid}":
      return uri
  low = s.lower()
  for hint, uri in _SYSTEM_HINTS:
    if hint in low:
      return uri
  if _OID_RE.match(s):
    return f"urn:oid:{s}"
  return s if re.match(r"^[a-z][a-z0-9+.-]*:", low) else None


def system_oid(system: str | None) -> tuple[str, str | None] | None:
  """Map a system (URI, OID or name) to (OID, display name), or None if unknown."""
  uri = system_uri(system)
  if uri in CODE_SYSTEMS:
    return CODE_SYSTEMS[uri]
  s = (system or "").strip()
  if s.startswith("urn:oid:"):
    s = s[8:]
  if _OID_RE.match(s):
    return s, None
  return None


# --------------------------------------------------------------------------- #
# Demographics                                                                #
# --------------------------------------------------------------------------- #

# OMB race/ethnicity categories. Source: CDC Race & Ethnicity Code Set v1.0
# (OID 2.16.840.1.113883.6.238), as required by US Core / C-CDA US Realm.
CDC_RACE_ETH_OID = "2.16.840.1.113883.6.238"
CDC_RACE_ETH_URI = "urn:oid:2.16.840.1.113883.6.238"
OMB_RACE = {
  "american indian or alaska native": ("1002-5", "American Indian or Alaska Native"),
  "asian": ("2028-9", "Asian"),
  "black or african american": ("2054-5", "Black or African American"),
  "black": ("2054-5", "Black or African American"),
  "native hawaiian or other pacific islander": ("2076-8", "Native Hawaiian or Other Pacific Islander"),
  "white": ("2106-3", "White"),
}
OMB_ETHNICITY = {
  "hispanic or latino": ("2135-2", "Hispanic or Latino"),
  "not hispanic or latino": ("2186-5", "Not Hispanic or Latino"),
}

# Source: IETF BCP 47 / ISO 639-1.
LANGUAGES = {"english": "en", "spanish": "es", "french": "fr", "vietnamese": "vi",
             "chinese": "zh", "mandarin": "zh", "cantonese": "zh", "arabic": "ar",
             "somali": "so", "hmong": "hmn", "russian": "ru", "korean": "ko",
             "tagalog": "tl", "portuguese": "pt", "german": "de", "haitian creole": "ht",
             "amharic": "am", "oromo": "om", "karen": "kar", "nepali": "ne", "hindi": "hi",
             "urdu": "ur", "japanese": "ja", "polish": "pl", "italian": "it"}


def language_code(language: str | None) -> str:
  lang = (language or "English").strip()
  if re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*", lang):
    return lang
  return LANGUAGES.get(lang.lower(), "und")


# --------------------------------------------------------------------------- #
# Routes                                                                      #
# --------------------------------------------------------------------------- #

# route text -> (NCI Thesaurus code, NCI name, SNOMED code, SNOMED display,
# HL7 v3 RouteOfAdministration code).
# Sources: FDA SPL route of administration (NCI Thesaurus, C-CDA Medication
# Route value set 2.16.840.1.113883.3.88.12.3221.8.7); SNOMED CT route
# hierarchy (FHIR SNOMEDCTRouteCodes); HL7 v3 RouteOfAdministration.
_ROUTES = {
  "oral": ("C38288", "ORAL", "26643006", "Oral route", "PO"),
  "topical": ("C38304", "TOPICAL", "6064005", "Topical route", "TOPICAL"),
  "inhaled": ("C38216", "RESPIRATORY (INHALATION)", "447694001", "Respiratory tract route", "IPINHL"),
  "intramuscular": ("C28161", "INTRAMUSCULAR", "78421000", "Intramuscular route", "IM"),
  "intravenous": ("C38276", "INTRAVENOUS", "47625008", "Intravenous route", "IVINJ"),
  "subcutaneous": ("C38299", "SUBCUTANEOUS", "34206005", "Subcutaneous route", "SQ"),
  "rectal": ("C38295", "RECTAL", "37161004", "Rectal route", "RECTAL"),
  "ophthalmic": ("C38287", "OPHTHALMIC", "54485002", "Ophthalmic route", "OPTHALTA"),
  "otic": ("C38192", "AURICULAR (OTIC)", "10547007", "Otic route", "OTIC"),
  "nasal": ("C38284", "NASAL", "46713006", "Nasal route", "NASINSTIL"),
  "transdermal": ("C38305", "TRANSDERMAL", "45890007", "Transdermal route", "TRNSDERM"),
  "sublingual": ("C38300", "SUBLINGUAL", "37839007", "Sublingual route", "SL"),
  "intranasal": ("C38284", "NASAL", "46713006", "Nasal route", "NASINSTIL"),
}
_ROUTE_ALIASES = {
  "po": "oral", "by mouth": "oral", "inhalation": "inhaled", "inhaled": "inhaled",
  "nebulized": "inhaled", "respiratory": "inhaled", "im": "intramuscular",
  "injection": "intramuscular", "iv": "intravenous", "sc": "subcutaneous",
  "sq": "subcutaneous", "subq": "subcutaneous", "pr": "rectal", "eye": "ophthalmic",
  "ear": "otic", "skin": "topical", "td": "transdermal", "sl": "sublingual",
}
NCI_OID = "2.16.840.1.113883.3.26.1.1"
V3_ROUTE_URI = "http://terminology.hl7.org/CodeSystem/v3-RouteOfAdministration"


def route_info(route: str | None) -> tuple[str, str, str, str, str] | None:
  r = (route or "").strip().lower()
  if not r:
    return None
  r = _ROUTE_ALIASES.get(r, r)
  if r in _ROUTES:
    return _ROUTES[r]
  for key in _ROUTES:
    if key in r:
      return _ROUTES[key]
  return None


# --------------------------------------------------------------------------- #
# Immunization not-done reasons                                               #
# --------------------------------------------------------------------------- #

# HL7 v3 ActReason, No Immunization Reason value set
# (2.16.840.1.113883.1.11.19717; FHIR immunization-status-reason uses the
# same codes). Order matters: first matching keyword wins.
ACT_REASON_OID = "2.16.840.1.113883.5.8"
ACT_REASON_URI = "http://terminology.hl7.org/CodeSystem/v3-ActReason"
_NOT_DONE_REASONS = [
  (("religio",), "RELIG", "religious objection"),
  (("philosoph",), "PHILISOP", "philosophical objection"),
  (("safety", "side effect", "autism"), "VACSAF", "vaccine safety concerns"),
  (("efficacy", "doesn't work"), "VACEFF", "vaccine efficacy concerns"),
  (("immune", "titer", "had the disease", "immunity"), "IMMUNE", "immunity"),
  (("stock", "supply", "unavailable"), "OSTOCK", "product out of stock"),
  (("contraindicat", "precaution", "illness", "immunocompromis", "allerg",
    "fever", "medical"), "MEDPREC", "medical precaution"),
  (("refus", "declin", "hesitan", "delay", "request", "objection", "exempt",
    "parent"), "PATOBJ", "patient objection"),
]


def not_done_reason(text: str | None) -> tuple[str, str] | None:
  """(ActReason code, display) for a free-text not-done reason, or None if unmappable."""
  t = (text or "").lower()
  if not t:
    return None
  if "immunocompromis" in t or "contraindicat" in t:
    return "MEDPREC", "medical precaution"
  for needles, code, display in _NOT_DONE_REASONS:
    if any(n in t for n in needles):
      return code, display
  return None


# --------------------------------------------------------------------------- #
# Encounters                                                                  #
# --------------------------------------------------------------------------- #

# Preventive medicine CPT by age (established patient 99391-99397; new-patient
# newborn visit 99381). Source: AMA CPT 2026, Preventive Medicine Services.
_PREVENTIVE_EST = [(1, "99391"), (5, "99392"), (12, "99393"), (18, "99394"),
                   (40, "99395"), (65, "99396"), (200, "99397")]
_PREVENTIVE_DISPLAY = {
  "99381": "Initial preventive medicine evaluation, infant (age younger than 1 year)",
  "99391": "Periodic preventive medicine reevaluation, infant (age younger than 1 year)",
  "99392": "Periodic preventive medicine reevaluation, early childhood (age 1 through 4 years)",
  "99393": "Periodic preventive medicine reevaluation, late childhood (age 5 through 11 years)",
  "99394": "Periodic preventive medicine reevaluation, adolescent (age 12 through 17 years)",
  "99395": "Periodic preventive medicine reevaluation, age 18-39 years",
  "99396": "Periodic preventive medicine reevaluation, age 40-64 years",
  "99397": "Periodic preventive medicine reevaluation, age 65 years and older",
}


def encounter_cpt(enc_type: str, age_years: int) -> tuple[str, str] | None:
  """CPT for an encounter when the type determines it, else None.

  Problem-oriented visits (acute, follow-up) depend on an E/M level that the
  generator doesn't model, so they get None (nullFlavor) rather than a guess.
  """
  if enc_type == "newborn":
    return "99381", _PREVENTIVE_DISPLAY["99381"]
  if enc_type in ("well-child", "annual-physical"):
    for upper, code in _PREVENTIVE_EST:
      if age_years < upper:
        return code, _PREVENTIVE_DISPLAY[code]
  return None


# --------------------------------------------------------------------------- #
# Lab units                                                                   #
# --------------------------------------------------------------------------- #

# Conventional lab unit text -> UCUM. Source: UCUM; LOINC example UCUM units.
LAB_UCUM = {
  "mg/dl": "mg/dL", "g/dl": "g/dL", "k/ul": "10*3/uL", "m/ul": "10*6/uL",
  "x10^3/ul": "10*3/uL", "x10^9/l": "10*9/L", "%": "%", "miu/l": "m[IU]/L",
  "uiu/ml": "u[IU]/mL", "miu/ml": "m[IU]/mL", "pg/ml": "pg/mL", "ng/ml": "ng/mL",
  "ng/dl": "ng/dL", "mmol/l": "mmol/L", "meq/l": "meq/L", "u/l": "U/L", "iu/l": "[IU]/L",
  "fl": "fL", "pg": "pg", "mm/hr": "mm/h", "mm/h": "mm/h", "mg/l": "mg/L",
  "ug/dl": "ug/dL", "mcg/dl": "ug/dL", "ml/min/1.73m2": "mL/min/{1.73_m2}",
  "mg/g": "mg/g", "ratio": "{ratio}", "sec": "s", "s": "s", "cells/ul": "/uL",
  "/ul": "/uL", "/hpf": "/[HPF]", "kg/m2": "kg/m2", "mmhg": "mm[Hg]",
}


def lab_ucum(unit: str | None) -> str | None:
  u = (unit or "").strip()
  if not u:
    return None
  return LAB_UCUM.get(u.lower())


def parse_number(value) -> float | None:
  """Numeric lab value from a str/int/float, or None if not a plain number."""
  if isinstance(value, bool):
    return None
  if isinstance(value, (int, float)):
    return float(value)
  s = str(value or "").strip()
  return float(s) if re.fullmatch(r"-?\d+(?:\.\d+)?", s) else None
