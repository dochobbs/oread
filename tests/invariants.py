"""
Clinical invariants for generated patients.

Each check takes a Patient (and sometimes its exports) and returns a list of
human-readable violation strings. An empty list means the patient passes.
Checks are pure so they can be reused outside pytest (e.g. a CLI audit).

Clinical thresholds cite their source next to the constant. Keep it that way:
no uncited dose or age numbers.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from xml.etree import ElementTree as ET

from dateutil.relativedelta import relativedelta

from src.models import ImmunizationStatus, Patient


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _d(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _age_days(patient: Patient, on: date | datetime) -> int:
    return (_d(on) - patient.demographics.date_of_birth).days


def _label(patient: Patient) -> str:
    dem = patient.demographics
    return f"{patient.id} ({dem.age_years}y{dem.age_months % 12}m {dem.sex_at_birth.value})"


def _all_meds(patient: Patient):
    """Active-list meds plus every encounter prescription."""
    yield from patient.medication_list
    for enc in patient.encounters:
        yield from enc.prescriptions


# CVX code -> antigen group. Combination vaccines count toward each component.
# Source: CDC CVX table, https://www2a.cdc.gov/vaccines/iis/iisstandards/vaccines.asp
CVX_GROUPS: dict[str, set[str]] = {
    "08": {"HepB"}, "43": {"HepB"}, "44": {"HepB"}, "45": {"HepB"},
    "116": {"RV"}, "119": {"RV"}, "122": {"RV"},
    "20": {"DTaP"}, "106": {"DTaP"}, "107": {"DTaP"},
    "17": {"Hib"}, "46": {"Hib"}, "47": {"Hib"}, "48": {"Hib"}, "49": {"Hib"},
    "133": {"PCV"}, "152": {"PCV"}, "215": {"PCV"}, "216": {"PCV"},
    "10": {"IPV"}, "89": {"IPV"},
    "03": {"MMR"}, "94": {"MMR", "VAR"},
    "21": {"VAR"},
    "83": {"HepA"}, "85": {"HepA"},
    "62": {"HPV"}, "165": {"HPV"},
    "114": {"MenACWY"}, "136": {"MenACWY"}, "147": {"MenACWY"}, "203": {"MenACWY"},
    "115": {"Tdap"},
    "88": {"Flu"}, "140": {"Flu"}, "141": {"Flu"}, "150": {"Flu"}, "158": {"Flu"},
    "161": {"Flu"}, "171": {"Flu"}, "186": {"Flu"}, "320": {"Flu"},
    # Combinations
    "110": {"DTaP", "HepB", "IPV"},       # Pediarix
    "120": {"DTaP", "Hib", "IPV"},        # Pentacel
    "130": {"DTaP", "IPV"},               # Kinrix / Quadracel
    "146": {"DTaP", "IPV", "Hib", "HepB"},  # Vaxelis
}


def _doses(patient: Patient, group: str, completed_only: bool = True):
    out = []
    for imm in patient.immunization_record:
        if completed_only and imm.status != ImmunizationStatus.COMPLETED:
            continue
        if group in CVX_GROUPS.get(imm.vaccine_code.code, set()):
            out.append(imm)
    return sorted(out, key=lambda i: i.date)


def _has_refusal(patient: Patient) -> bool:
    return any(i.status != ImmunizationStatus.COMPLETED for i in patient.immunization_record)


# --------------------------------------------------------------------------- #
# Demographics / dates                                                        #
# --------------------------------------------------------------------------- #

def check_dates(patient: Patient, today: date | None = None) -> list[str]:
    """No clinical date before DOB or after today."""
    today = today or date.today()
    dob = patient.demographics.date_of_birth
    out = []

    def chk(kind: str, value):
        if value is None:
            return
        v = _d(value)
        if v < dob:
            out.append(f"{kind} {v} before DOB {dob}")
        if v > today:
            out.append(f"{kind} {v} after today {today}")

    for c in patient.problem_list:
        chk(f"condition '{c.display_name}' onset", c.onset_date)
        chk(f"condition '{c.display_name}' abatement", c.abatement_date)
    for e in patient.encounters:
        chk(f"encounter {e.type.value}", e.date)
    for i in patient.immunization_record:
        chk(f"immunization '{i.display_name}'", i.date)
    for m in _all_meds(patient):
        chk(f"med '{m.display_name}' start", m.start_date)
    return out


def check_requested_age(patient: Patient, *, age_months: int | None = None,
                        age_years: int | None = None, today: date | None = None) -> list[str]:
    """Generated DOB yields exactly the requested age."""
    today = today or date.today()
    delta = relativedelta(today, patient.demographics.date_of_birth)
    if age_years is not None and delta.years != age_years:
        return [f"requested {age_years}y, got {delta.years}y{delta.months}m"]
    if age_months is not None:
        got = delta.years * 12 + delta.months
        if got != age_months:
            return [f"requested {age_months}mo, got {got}mo"]
    return []


# --------------------------------------------------------------------------- #
# Immunizations                                                               #
# --------------------------------------------------------------------------- #

# Minimum completed doses by age for a child with no refusals.
# Source: AAP 2026 recommended schedule (routine series; counts are the floor
# a fully-vaccinated child must have reached by that age, with grace for the
# upper end of each dose window).
SERIES_FLOOR_BY_AGE_MONTHS: list[tuple[int, dict[str, int]]] = [
    (3, {"HepB": 2, "DTaP": 1, "IPV": 1, "Hib": 1, "PCV": 1}),
    (7, {"HepB": 2, "DTaP": 3, "IPV": 2, "Hib": 2, "PCV": 3}),
    (19, {"HepB": 3, "DTaP": 3, "IPV": 3, "Hib": 3, "PCV": 4, "MMR": 1, "VAR": 1, "HepA": 1}),
    (24, {"HepB": 3, "DTaP": 4, "IPV": 3, "Hib": 3, "PCV": 4, "MMR": 1, "VAR": 1, "HepA": 2}),
    (84, {"HepB": 3, "DTaP": 5, "IPV": 4, "MMR": 2, "VAR": 2, "HepA": 2}),
    (156, {"HepB": 3, "DTaP": 5, "IPV": 4, "MMR": 2, "VAR": 2, "HepA": 2, "Tdap": 1,
           "MenACWY": 1, "HPV": 1}),
]


def check_vaccine_series(patient: Patient) -> list[str]:
    """A child with no refusals has the routine series complete for age."""
    if _has_refusal(patient):
        return []
    months = patient.demographics.age_months
    floor: dict[str, int] = {}
    for min_age, req in SERIES_FLOOR_BY_AGE_MONTHS:
        if months >= min_age:
            floor = req
    out = []
    for group, need in floor.items():
        have = len(_doses(patient, group))
        if have < need:
            out.append(f"{group}: {have} doses at {months}mo, expected >= {need}")
    return out


# Minimum and maximum age (days) per antigen dose. Uses the 4-day grace period.
# Source: CDC/ACIP General Best Practice Guidelines, Table 3-1 (min ages);
# rotavirus max ages from AAP Red Book (dose 1 by 14w6d, last dose by 8m0d).
_GRACE = 4
MIN_AGE_DAYS: dict[str, int] = {
    "DTaP": 42, "IPV": 42, "Hib": 42, "PCV": 42, "RV": 42,
    "MMR": 365, "VAR": 365, "HepA": 365, "Flu": 182, "HPV": 9 * 365,
}
RV_FIRST_DOSE_MAX_DAYS = 14 * 7 + 6
RV_LAST_DOSE_MAX_DAYS = 8 * 30 + 1  # 8 months 0 days


def check_vaccine_ages(patient: Patient) -> list[str]:
    """No dose given before its minimum age or (rotavirus) after its maximum."""
    out = []
    for group, min_days in MIN_AGE_DAYS.items():
        for imm in _doses(patient, group):
            age = _age_days(patient, imm.date)
            if age < min_days - _GRACE:
                out.append(f"{group} given at {age}d, minimum {min_days}d")
    rv = _doses(patient, "RV")
    if rv:
        first = _age_days(patient, rv[0].date)
        if first > RV_FIRST_DOSE_MAX_DAYS:
            out.append(f"RV dose 1 at {first}d, max {RV_FIRST_DOSE_MAX_DAYS}d")
        for imm in rv:
            age = _age_days(patient, imm.date)
            if age > RV_LAST_DOSE_MAX_DAYS:
                out.append(f"RV given at {age}d, max {RV_LAST_DOSE_MAX_DAYS}d")
    return out


def check_vaccine_dose_numbers(patient: Patient) -> list[str]:
    """Dose numbers within an antigen are strictly increasing over time."""
    out = []
    for group in ("HepB", "DTaP", "IPV", "Hib", "PCV", "RV", "MMR", "VAR", "HepA"):
        nums = [i.dose_number for i in _doses(patient, group) if i.dose_number]
        if nums != sorted(nums) or len(nums) != len(set(nums)):
            out.append(f"{group} dose numbers out of order: {nums}")
    return out


def check_annual_flu(patient: Patient) -> list[str]:
    """Children >= 6 months get flu vaccine each season (AAP: annual from 6mo)."""
    if _has_refusal(patient):
        return []
    dob = patient.demographics.date_of_birth
    six_mo = dob + relativedelta(months=6)
    seasons_eligible = set()
    for enc in patient.encounters:
        d = _d(enc.date)
        if d >= six_mo and enc.type.value in ("well-child", "newborn"):
            seasons_eligible.add(d.year if d.month >= 8 else d.year - 1)
    flu_seasons = {(_d(i.date).year if _d(i.date).month >= 8 else _d(i.date).year - 1)
                   for i in _doses(patient, "Flu")}
    missing = len(seasons_eligible - flu_seasons)
    # Allow one miss: a well visit in Aug–Sep before stock arrives is realistic.
    if missing > 1:
        return [f"flu missing in {missing} of {len(seasons_eligible)} seasons with well visits"]
    return []


# --------------------------------------------------------------------------- #
# Medications                                                                 #
# --------------------------------------------------------------------------- #

# Drug classes where two concurrent agents is duplicate therapy.
DRUG_CLASSES: dict[str, tuple[str, ...]] = {
    "SSRI": ("sertraline", "fluoxetine", "escitalopram", "citalopram", "fluvoxamine",
             "paroxetine"),
    "statin": ("atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin",
               "pitavastatin"),
    "aminopenicillin": ("amoxicillin",),  # includes amoxicillin-clavulanate
    "stimulant": ("methylphenidate", "amphetamine", "lisdexamfetamine",
                  "dexmethylphenidate"),
}


def _drug_class(name: str) -> str | None:
    n = name.lower()
    for cls, members in DRUG_CLASSES.items():
        if any(m in n for m in members):
            return cls
    return None


def check_duplicate_therapy(patient: Patient) -> list[str]:
    """No encounter prescribes, and no active list holds, two drugs of one class."""
    out = []
    for enc in patient.encounters:
        seen: dict[str, str] = {}
        for m in enc.prescriptions:
            cls = _drug_class(m.display_name)
            if cls and cls in seen and seen[cls] != m.display_name:
                out.append(f"{enc.date:%Y-%m-%d}: {seen[cls]} + {m.display_name} ({cls})")
            elif cls:
                seen[cls] = m.display_name
    active: dict[str, str] = {}
    for m in patient.medication_list:
        if m.status.value != "active":
            continue
        cls = _drug_class(m.display_name)
        if cls and cls in active and active[cls] != m.display_name:
            out.append(f"active list: {active[cls]} + {m.display_name} ({cls})")
        elif cls:
            active[cls] = m.display_name
    return out


def _weight_on(patient: Patient, on: date) -> float | None:
    best = None
    for g in patient.growth_data:
        if g.weight_kg and g.date <= on and (best is None or g.date > best.date):
            best = g
    return best.weight_kg if best else None


_FREQ_PER_DAY = [
    (r"\bqid\b|four times", 4), (r"\btid\b|three times", 3), (r"\bbid\b|twice", 2),
    (r"q12h", 2), (r"q8h", 3), (r"q6h", 4),
    (r"daily|once|qd|at bedtime|qhs|in am", 1),
]


def _per_day(freq: str) -> int | None:
    f = freq.lower()
    for pat, n in _FREQ_PER_DAY:
        if re.search(pat, f):
            return n
    return None


def _mg(m) -> float | None:
    if (m.dose_unit or "").lower() != "mg":
        return None
    try:
        return float(m.dose_quantity)
    except (TypeError, ValueError):
        return None


# Chronic peds dose ceilings. (mg/kg/day max, absolute mg/day max).
# Sources: Lexicomp Pediatric & Neonatal Dosage Handbook; AAP ADHD CPG 2019;
# FDA labels (levetiracetam, cetirizine, sertraline, fluoxetine).
DOSE_CEILINGS: dict[str, tuple[float | None, float]] = {
    "levetiracetam": (60.0, 3000.0),
    "methylphenidate": (2.0, 72.0),
    "sertraline": (None, 200.0),
    "fluoxetine": (None, 60.0),
}
# Cetirizine by age (FDA label): 6-23mo 2.5 mg/day, 2-5y 5 mg/day, >=6y 10 mg/day.
CETIRIZINE_MAX_BY_AGE_MONTHS = [(6, 2.5), (24, 5.0), (72, 10.0)]
# Methylphenidate ER starting dose ceiling for age 4-5 (AAP ADHD CPG 2019:
# preschoolers start low, max ~22.5 mg/day IR equivalent before titration).
PRESCHOOL_STIMULANT_START_MAX_MG = 10.0


def check_chronic_doses(patient: Patient) -> list[str]:
    """Chronic peds meds are within weight/age-appropriate daily limits."""
    out = []
    dob = patient.demographics.date_of_birth
    for m in _all_meds(patient):
        name = m.display_name.lower()
        mg = _mg(m)
        freq = (m.frequency or "").lower()
        # "divided BID" means dose_quantity is the daily total split in two.
        n = 1 if freq.startswith("divided") else _per_day(freq)
        if mg is None or n is None:
            continue
        daily = mg * n
        start = _d(m.start_date)
        age_mo = (start.year - dob.year) * 12 + start.month - dob.month
        wt = _weight_on(patient, start)
        for drug, (per_kg, abs_max) in DOSE_CEILINGS.items():
            if drug not in name:
                continue
            if daily > abs_max:
                out.append(f"{m.display_name} {daily:g} mg/day > {abs_max:g} max")
            if per_kg and wt and daily / wt > per_kg:
                out.append(f"{m.display_name} {daily / wt:.0f} mg/kg/day at {wt:.1f} kg "
                           f"> {per_kg:g} (age {age_mo}mo)")
            if drug == "methylphenidate" and age_mo < 72 and daily > PRESCHOOL_STIMULANT_START_MAX_MG:
                out.append(f"{m.display_name} {daily:g} mg/day start at {age_mo}mo "
                           f"(preschool start <= {PRESCHOOL_STIMULANT_START_MAX_MG:g})")
        if "cetirizine" in name:
            cap = None
            for min_age, mx in CETIRIZINE_MAX_BY_AGE_MONTHS:
                if age_mo >= min_age:
                    cap = mx
            if cap is None:
                out.append(f"cetirizine at {age_mo}mo (not labeled < 6mo)")
            elif daily > cap:
                out.append(f"cetirizine {daily:g} mg/day at {age_mo}mo > {cap:g}")
    return out


# Medications with a minimum age (months).
# Source: ondansetron — AAP/IDSA AGE guidance, not recommended < 6 months.
MED_MIN_AGE_MONTHS = {"ondansetron": 6}


def check_med_min_age(patient: Patient) -> list[str]:
    out = []
    dob = patient.demographics.date_of_birth
    for m in _all_meds(patient):
        for drug, min_mo in MED_MIN_AGE_MONTHS.items():
            if drug in m.display_name.lower():
                s = _d(m.start_date)
                age_mo = (s.year - dob.year) * 12 + s.month - dob.month
                if age_mo < min_mo:
                    out.append(f"{m.display_name} at {age_mo}mo (min {min_mo}mo)")
    return out


def _has_condition(patient: Patient, *needles: str) -> bool:
    for c in patient.problem_list:
        if c.clinical_status.value != "active":
            continue
        name = c.display_name.lower()
        if any(n in name for n in needles):
            return True
    return False


def _has_med(patient: Patient, *needles: str) -> bool:
    return any(any(n in m.display_name.lower() for n in needles) for m in _all_meds(patient))


def check_required_meds(patient: Patient) -> list[str]:
    """Chronic conditions carry their standard-of-care medications."""
    out = []
    if _has_condition(patient, "type 1 diabetes"):
        # ADA Standards of Care in Diabetes (2026), Section 14 (children)
        if not _has_med(patient, "glargine", "detemir", "degludec"):
            out.append("T1D without basal insulin")
        if not _has_med(patient, "lispro", "aspart", "glulisine", "regular insulin"):
            out.append("T1D without rapid-acting insulin")
        if not _has_med(patient, "glucagon"):
            out.append("T1D without glucagon")
    if _has_condition(patient, "type 2 diabetes"):
        # ADA Standards of Care (2026) / AAP T2D CPG 2013: metformin first-line
        if not _has_med(patient, "metformin"):
            out.append("T2D without metformin")
    if _has_condition(patient, "food allergy"):
        # AAP Clinical Report: Epinephrine for First-Aid Management of Anaphylaxis (2017)
        if not _has_med(patient, "epinephrine"):
            out.append("food allergy without epinephrine auto-injector")
    return out


def check_sliding_scale_basal(patient: Patient) -> list[str]:
    """Basal insulin is never dosed by sliding scale."""
    return [f"{m.display_name}: '{m.dose_quantity} {m.dose_unit} {m.frequency}'"
            for m in _all_meds(patient)
            if "glargine" in m.display_name.lower()
            and "sliding" in f"{m.dose_quantity} {m.dose_unit} {m.frequency} {m.instructions}".lower()]


# --------------------------------------------------------------------------- #
# Conditions / coding / labs                                                  #
# --------------------------------------------------------------------------- #

# Self-limited conditions that must resolve (have an abatement date) once the
# episode is over. Matched by display-name substring.
SELF_LIMITED = ("bronchiolitis", "otitis media", "upper respiratory", "croup",
                "gastroenteritis", "conjunctivitis", "kawasaki", "pharyngitis",
                "hand, foot", "roseola", "fifth disease", "influenza", "pneumonia",
                "urinary tract infection", "impetigo", "swimmer", "viral syndrome",
                "acute cough", "fever")
SELF_LIMITED_EXCLUDE = ("recurrent",)


def check_self_limited_resolve(patient: Patient, today: date | None = None) -> list[str]:
    """Self-limited problems older than 90 days are resolved with an abatement date."""
    today = today or date.today()
    out = []
    for c in patient.problem_list:
        name = c.display_name.lower()
        if not any(s in name for s in SELF_LIMITED) or any(x in name for x in SELF_LIMITED_EXCLUDE):
            continue
        if (today - c.onset_date).days > 90 and (c.clinical_status.value == "active"
                                                 or c.abatement_date is None):
            out.append(f"'{c.display_name}' onset {c.onset_date} still "
                       f"{c.clinical_status.value}, abatement={c.abatement_date}")
    return out


def check_no_placeholder_codes(patient: Patient) -> list[str]:
    """No RxNorm 000000 and no ICD-10 R69 'illness, unspecified'."""
    out = []
    for m in _all_meds(patient):
        if m.code.code in ("000000", "", None):
            out.append(f"med '{m.display_name}' has placeholder RxNorm '{m.code.code}'")
    for c in patient.problem_list:
        if c.code.code == "R69":
            out.append(f"condition '{c.display_name}' coded R69")
    return out


def check_diabetes_a1c(patient: Patient) -> list[str]:
    """Diabetes patients with follow-up have at least one HbA1c (LOINC 4548-4)."""
    if not _has_condition(patient, "diabetes"):
        return []
    for enc in patient.encounters:
        for lab in enc.lab_results:
            results = getattr(lab, "results", None) or [lab]
            for r in results:
                code = getattr(getattr(r, "code", None), "code", None)
                name = (getattr(r, "display_name", "") or "").lower()
                if code == "4548-4" or "a1c" in name:
                    return []
    return ["diabetes with no HbA1c result"]


# --------------------------------------------------------------------------- #
# Encounters                                                                  #
# --------------------------------------------------------------------------- #

IN_PERSON_VISITS = {"newborn", "well-child", "annual-physical", "acute-illness", "acute-injury",
                    "chronic-followup", "medication-check", "mental-health", "urgent-care",
                    "emergency", "specialist-consult"}
PROBLEM_VISITS = IN_PERSON_VISITS - {"newborn", "well-child", "annual-physical"}


def check_encounter_vitals(patient: Patient) -> list[str]:
    """Every in-person office encounter has vital signs."""
    visits = [e for e in patient.encounters if e.type.value in IN_PERSON_VISITS]
    missing = [e for e in visits if e.vital_signs is None]
    if missing:
        return [f"{len(missing)}/{len(visits)} in-person encounters have no vital_signs"]
    return []


def check_encounter_hpi(patient: Patient) -> list[str]:
    """Problem-focused encounters have an HPI."""
    acute = [e for e in patient.encounters if e.type.value in PROBLEM_VISITS]
    missing = [e for e in acute if not e.hpi]
    if missing:
        return [f"{len(missing)}/{len(acute)} problem visits have no HPI"]
    return []


def check_exam_age_appropriate(patient: Patient) -> list[str]:
    """No fontanelle findings after 24 months."""
    out = []
    for e in patient.encounters:
        if _age_days(patient, e.date) < 730:
            continue
        text = " ".join(filter(None, [
            e.physical_exam.model_dump_json(exclude_none=True) if e.physical_exam else "",
            e.narrative_note or "",
        ])).lower()
        if "fontanel" in text:
            out.append(f"{e.date:%Y-%m-%d}: fontanelle mentioned at "
                       f"{_age_days(patient, e.date) // 30}mo")
    return out


# --------------------------------------------------------------------------- #
# Exports                                                                     #
# --------------------------------------------------------------------------- #

CCDA_NS = {"c": "urn:hl7-org:v3"}
VITALS_SECTION = "2.16.840.1.113883.10.20.22.2.4.1"
IMM_SECTION = "2.16.840.1.113883.10.20.22.2.2.1"
RESULTS_SECTION = "2.16.840.1.113883.10.20.22.2.3.1"
PROBLEM_SECTION = "2.16.840.1.113883.10.20.22.2.5.1"
RESULT_ORGANIZER = "2.16.840.1.113883.10.20.22.4.1"
VALID_LENGTH_UCUM = {"cm", "m", "[in_i]", "mm"}


def _section(root: ET.Element, template: str) -> list[ET.Element]:
    return [s for s in root.iter(f"{{{CCDA_NS['c']}}}section")
            if any(t.get("root") == template
                   for t in s.findall("c:templateId", CCDA_NS))]


def check_fhir_valid(bundle: dict) -> list[str]:
    """Bundle validates against FHIR R4 (fhir.resources.R4B)."""
    from fhir.resources.R4B.bundle import Bundle
    from pydantic import ValidationError
    try:
        Bundle.model_validate(bundle)
    except ValidationError as exc:
        errs = exc.errors()
        first = "; ".join(f"{'.'.join(map(str, e['loc'][-3:]))}: {e['msg']}" for e in errs[:3])
        return [f"{len(errs)} FHIR R4 validation errors, e.g. {first}"]
    return []


def check_ccda_structure(xml: str, patient: Patient) -> list[str]:
    """C-CDA structural rules that an importing EHR relies on."""
    out = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return [f"C-CDA not well-formed: {exc}"]
    ns = CCDA_NS

    if len(_section(root, VITALS_SECTION)) > 1:
        out.append(f"{len(_section(root, VITALS_SECTION))} Vital Signs sections (max 1)")

    # Height units are valid UCUM
    for obs in root.iter(f"{{{ns['c']}}}observation"):
        code = obs.find("c:code", ns)
        val = obs.find("c:value", ns)
        if code is not None and code.get("code") == "8302-2" and val is not None:
            if val.get("unit") not in VALID_LENGTH_UCUM:
                out.append(f"height unit '{val.get('unit')}' not valid UCUM")
                break

    # Results: every result observation sits inside a Result Organizer
    for sec in _section(root, RESULTS_SECTION):
        for entry in sec.findall("c:entry", ns):
            if entry.find("c:organizer", ns) is None and entry.find("c:observation", ns) is not None:
                out.append("result observation not wrapped in a Result Organizer")
                break

    # Immunizations: refused/not-done doses carry negationInd="true"
    not_done = sum(1 for i in patient.immunization_record
                   if i.status != ImmunizationStatus.COMPLETED)
    negated = 0
    for sec in _section(root, IMM_SECTION):
        for sa in sec.iter(f"{{{ns['c']}}}substanceAdministration"):
            if sa.get("negationInd") == "true":
                negated += 1
    if negated < not_done:
        out.append(f"{not_done} not-done immunizations, {negated} negated in C-CDA")

    # Problems: resolved problems carry effectiveTime/high
    resolved = sum(1 for c in patient.problem_list if c.abatement_date is not None)
    with_high = 0
    for sec in _section(root, PROBLEM_SECTION):
        for act in sec.iter(f"{{{ns['c']}}}act"):
            eff = act.find("c:effectiveTime", ns)
            if eff is not None and eff.find("c:high", ns) is not None \
                    and eff.find("c:high", ns).get("value"):
                with_high += 1
    if with_high < resolved:
        out.append(f"{resolved} resolved problems, {with_high} with effectiveTime/high")

    # Medication frequency: a BID med is not encoded as a 24h period
    bid_meds = [m for m in _all_meds(patient) if _per_day(m.frequency or "") == 2]
    if bid_meds:
        periods = [p.get("value") + (p.get("unit") or "")
                   for p in root.iter(f"{{{ns['c']}}}period")]
        if periods and "12h" not in periods:
            out.append(f"{len(bid_meds)} BID meds but no 12h PIVL period in C-CDA "
                       f"(periods seen: {sorted(set(periods))})")
    return out


def check_format_parity(patient: Patient, bundle: dict, xml: str, markdown: str) -> list[str]:
    """Problem/med/immunization counts and refusal status agree across formats."""
    out = []
    by_type: dict[str, int] = {}
    for e in bundle.get("entry", []):
        rt = e["resource"]["resourceType"]
        by_type[rt] = by_type.get(rt, 0) + 1
    if by_type.get("Condition", 0) != len(patient.problem_list):
        out.append(f"FHIR Conditions {by_type.get('Condition', 0)} != {len(patient.problem_list)}")
    if by_type.get("Immunization", 0) != len(patient.immunization_record):
        out.append(f"FHIR Immunizations {by_type.get('Immunization', 0)} != "
                   f"{len(patient.immunization_record)}")
    fhir_not_done = sum(1 for e in bundle.get("entry", [])
                        if e["resource"]["resourceType"] == "Immunization"
                        and e["resource"].get("status") == "not-done")
    model_not_done = sum(1 for i in patient.immunization_record
                         if i.status != ImmunizationStatus.COMPLETED)
    if fhir_not_done != model_not_done:
        out.append(f"FHIR not-done {fhir_not_done} != model {model_not_done}")
    if model_not_done and not re.search(r"not given|refused|declined|not done", markdown, re.I):
        out.append(f"{model_not_done} refused vaccines but Markdown never says so")
    return out
