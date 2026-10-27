"""
Adult medication table and selection.

Turns condition med strings ("lisinopril 10mg", "sacubitril/valsartan
49/51mg") into Medication records with a verified RxNorm code, the labeled
route and frequency, and a class-level duplicate-therapy guard. The table
lives in adult_medications.yaml; see its header for sources.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

from src.models import CodeableConcept, Medication, MedicationStatus, Sex

RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
_TABLE_PATH = Path(__file__).parent / "adult_medications.yaml"
_TABLE: dict | None = None

# "10mg", "49/51mg", "250/50", "5mg/kg", "0.005%", "10units", "1000IU", "2.4g".
_DOSE_RE = re.compile(
    r"^(?P<qty>\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)*)\s*(?P<unit>[A-Za-z%][A-Za-z%/]*)?$"
)
_UNIT_NORMALIZE = {"ml": "mL", "meq": "mEq", "iu": "IU", "units": "units", "unit": "units"}
_MASS_UNITS = {"mg", "mcg", "g"}


def med_table() -> dict:
    """Load (once) and return the adult medication table."""
    global _TABLE
    if _TABLE is None:
        with open(_TABLE_PATH) as f:
            _TABLE = yaml.safe_load(f) or {}
    return _TABLE


def parse_dose(strength: str) -> tuple[str, str | None]:
    """Split a strength token into (quantity, unit).

    Combination strengths keep their slash: "49/51mg" -> ("49/51", "mg"),
    "250/50" -> ("250/50", None).

    Raises:
        ValueError: if the token is not a recognizable strength.
    """
    m = _DOSE_RE.match(strength.strip().replace(" ", ""))
    if not m:
        raise ValueError(f"unparseable strength {strength!r}")
    unit = m.group("unit")
    if unit:
        unit = _UNIT_NORMALIZE.get(unit.lower(), unit)
    return m.group("qty"), unit


def split_med_string(med_str: str) -> tuple[str, str]:
    """'budesonide/formoterol 160/4.5' -> ('budesonide/formoterol', '160/4.5')."""
    name, rest = [], []
    toks = med_str.split()
    for i, tok in enumerate(toks):
        if re.match(r"^\d", tok):
            rest = toks[i:]
            break
        name.append(tok)
    return " ".join(name), " ".join(rest)


@dataclass
class MedSpec:
    """A resolved med string: table entry merged with its overrides."""
    key: str
    strength: str
    dose_quantity: str
    dose_unit: str
    entry: dict = field(repr=False)

    @property
    def classes(self) -> set[str]:
        return set(self.entry.get("classes", []))

    @property
    def mg(self) -> float | None:
        if self.dose_unit != "mg" or "/" in self.dose_quantity:
            return None
        return float(self.dose_quantity)


def resolve(med_str: str, condition: str | None = None) -> MedSpec | None:
    """Resolve a condition med string, or None if the drug has no table entry."""
    key, strength = split_med_string(med_str)
    base = med_table().get(key)
    if base is None:
        return None
    entry = {k: v for k, v in base.items() if k not in ("by_strength", "by_condition")}
    entry.update((base.get("by_strength") or {}).get(strength.replace(" ", ""), {}))
    if condition:
        entry.update((base.get("by_condition") or {}).get(condition, {}))
    if "dose" in entry:
        qty, unit = entry["dose"]
    elif strength:
        qty, unit = parse_dose(strength)
        unit = unit or entry.get("unit")
        if unit is None:
            raise ValueError(f"{med_str!r}: strength has no unit and table gives none")
    else:
        raise ValueError(f"{med_str!r}: no strength in string and no table dose")
    return MedSpec(key=key, strength=strength, dose_quantity=str(qty), dose_unit=unit, entry=entry)


def unresolved(med_strings: list[str]) -> list[str]:
    """Med strings with no verified table entry (they are never prescribed)."""
    return [m for m in med_strings if resolve(m) is None]


def _allowed(spec: MedSpec, condition: str, sex: Sex) -> bool:
    e = spec.entry
    if sex == Sex.MALE and condition in (e.get("female_only_for") or []):
        return False
    cap = e.get("female_max_mg")
    if sex == Sex.FEMALE and cap is not None and spec.mg is not None and spec.mg > cap:
        return False
    return True


@dataclass
class TherapyState:
    """Drugs and classes already on the list, for duplicate-therapy checks."""
    keys: set[str] = field(default_factory=set)
    rxnorm: set[str] = field(default_factory=set)
    classes: set[str] = field(default_factory=set)

    def conflicts(self, spec: MedSpec) -> bool:
        return (spec.key in self.keys or spec.entry["rxnorm"] in self.rxnorm
                or bool(spec.classes & self.classes))

    def add(self, spec: MedSpec) -> None:
        self.keys.add(spec.key)
        self.rxnorm.add(spec.entry["rxnorm"])
        self.classes |= spec.classes


def select_for_condition(
    condition: str,
    med_strings: list[str],
    state: TherapyState,
    sex: Sex,
    rng: random.Random,
    first_line: list[str] | None = None,
) -> list[MedSpec]:
    """Pick 1 (70%) or 2 non-overlapping drugs for a condition.

    A drug is skipped if it shares an ingredient or therapeutic class with
    anything already chosen (no statin + statin, no SSRI + SNRI, no
    ACE inhibitor + ARB). Methotrexate brings folic acid with it. When the
    condition lists ``first_line`` drug names, the first pick comes from
    them (e.g. a statin before ezetimibe).
    """
    by_key: dict[str, list[MedSpec]] = {}
    for s in med_strings:
        spec = resolve(s, condition)
        if spec is None or not _allowed(spec, condition, sex):
            continue
        by_key.setdefault(spec.key, []).append(spec)
    keys = list(by_key)
    rng.shuffle(keys)
    if first_line:
        preferred = set(first_line)
        keys.sort(key=lambda k: 0 if k in preferred else 1)
    want = 1 if rng.random() < 0.7 else 2
    chosen: list[MedSpec] = []
    for key in keys:
        if len(chosen) >= want:
            break
        spec = rng.choice(by_key[key])
        if state.conflicts(spec):
            continue
        state.add(spec)
        chosen.append(spec)
        pair = spec.entry.get("pair_with")
        if pair:
            extra = resolve(pair, condition)
            if extra is not None and not state.conflicts(extra):
                state.add(extra)
                chosen.append(extra)
    return chosen


def to_medication(
    spec: MedSpec,
    condition_display: str,
    start: date,
    today: date,
    weight_kg: float | None = None,
) -> Medication:
    """Build the Medication record for a resolved spec."""
    e = spec.entry
    display = e["display"]
    show_dose = bool(spec.strength) or spec.dose_unit in _MASS_UNITS
    display_name = f"{display} {spec.dose_quantity} {spec.dose_unit}" if show_dose else display
    instructions = e.get("instructions")
    if spec.dose_unit == "mg/kg" and weight_kg:
        total = float(spec.dose_quantity) * weight_kg
        # Infusions are rounded to the nearest 100 mg vial size.
        rounded = int(round(total / 100.0) * 100) or int(total)
        note = f"Dose {spec.dose_quantity} mg/kg = about {rounded} mg at {weight_kg:.0f} kg."
        instructions = f"{instructions} {note}" if instructions else note
    start = min(start, today)
    end = None
    status = MedicationStatus.ACTIVE
    course = e.get("course_days")
    if course:
        end = start + timedelta(days=int(course))
        if end <= today:
            status = MedicationStatus.COMPLETED
    return Medication(
        code=CodeableConcept(system=RXNORM, code=str(e["rxnorm"]), display=e["rxnorm_name"]),
        display_name=display_name,
        status=status,
        dose_quantity=spec.dose_quantity,
        dose_unit=spec.dose_unit,
        frequency=e["frequency"],
        route=e["route"],
        instructions=instructions,
        prn=bool(e.get("prn", False)),
        start_date=start,
        end_date=end,
        indication=condition_display,
    )
