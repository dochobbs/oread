"""
Shared medication-sig parsing for the exporters.

The generator stores frequency as free text ("divided BID", "Q4-6H PRN",
"at bedtime") and dose as a string ("600", "1-2", "per package"). C-CDA and
FHIR need structured timing and a numeric dose, and every format must say the
same thing. This module turns the model fields into one structured reading
that the C-CDA, FHIR and Markdown exporters all use.

Conventions (match the engine):
- "divided BID/TID/QID": dose_quantity is the TOTAL DAILY dose, split into
  that many administrations (engine computes mg/kg/day * weight).
- Otherwise dose_quantity is the amount per administration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Times per day for the Latin abbreviations.
_PER_DAY_ABBR = {"qd": 1, "bid": 2, "tid": 3, "qid": 4}
_ABBR_WORDS = {1: "once daily", 2: "twice daily", 3: "three times daily",
               4: "four times daily"}

# Unit text -> UCUM. None means "count of the product" (unitless in C-CDA).
# Source: UCUM (unitsofmeasure.org); C-CDA 2.1 Medication Activity doseQuantity
# guidance (unitless when the product itself is the unit, e.g. tablets, puffs).
_UCUM_UNITS = {
    "mg": "mg", "g": "g", "mcg": "ug", "ug": "ug", "µg": "ug", "ml": "mL",
    "l": "L", "unit": "[iU]", "units": "[iU]", "iu": "[iU]", "meq": "meq",
    "mg/kg": "mg/kg", "%": "%",
}
_COUNT_UNITS = {"puff", "puffs", "tab", "tabs", "tablet", "tablets", "cap", "caps",
                "capsule", "capsules", "drop", "drops", "spray", "sprays",
                "patch", "patches", "suppository", "inhalation", "inhalations",
                "application", "applications", "dose", "doses"}

_NUM = r"\d+(?:\.\d+)?"


@dataclass
class Timing:
  """Structured reading of a frequency string."""
  text: str                       # source text
  per_day: float | None = None    # administrations per day, when fixed
  period: float | None = None     # interval between doses
  period_max: float | None = None  # upper bound for "q4-6h"
  period_unit: str | None = None  # UCUM-ish: h, d, wk, mo
  event: str | None = None        # HL7 TimingEvent (HS = bedtime)
  prn: bool = False
  divided: bool = False           # dose_quantity is a total daily dose
  once: bool = False              # single administration
  ambiguous: bool = False         # e.g. "BID-QID": no single per-dose amount
  institution_specified: bool = False  # "BID" = clinic's times, not strict q12h

  @property
  def has_fixed_period(self) -> bool:
    return self.period is not None and not self.prn and not self.once

  def phrase(self) -> str:
    """Plain-English frequency, without the PRN clause."""
    if self.once:
      return "once (single dose)"
    if self.event == "HS":
      return "at bedtime"
    if self.period is not None:
      unit = {"h": "hour", "d": "day", "wk": "week", "mo": "month"}[self.period_unit]
      if self.period_max is not None:
        return f"every {self.period:g}-{self.period_max:g} {unit}s"
      if self.period == 1:
        return {"h": "every hour", "d": "once daily", "wk": "once weekly",
                "mo": "once monthly"}[self.period_unit]
      if self.period_unit == "h" and 24 % self.period == 0 and 24 / self.period in _ABBR_WORDS:
        return _ABBR_WORDS[int(24 / self.period)]
      return f"every {self.period:g} {unit}s"
    return self.text


def parse_frequency(freq: str | None, prn: bool = False) -> Timing:
  """Parse a free-text frequency.

  Args:
    freq: Frequency text, any case ("BID", "q6-8h prn", "at bedtime").
    prn: The medication's own PRN flag; OR-ed with "PRN" in the text.

  Returns:
    A Timing. Unrecognized text yields a Timing with no period.
  """
  text = (freq or "").strip()
  f = text.lower()
  t = Timing(text=text)
  t.prn = bool(prn) or bool(re.search(r"\bprn\b|as needed", f))
  t.divided = "divided" in f
  t.once = bool(re.search(r"\bx\s*1\b|single dose|once only|one time|\bstat\b", f))

  m = re.search(r"\b(bid|tid|qid)\s*-\s*(bid|tid|qid)\b", f)
  if m:
    lo, hi = _PER_DAY_ABBR[m.group(1)], _PER_DAY_ABBR[m.group(2)]
    t.period, t.period_max, t.period_unit = 24 / hi, 24 / lo, "h"
    t.ambiguous = True
    return t

  m = re.search(rf"\bq\s*({_NUM})\s*(?:-|to)\s*({_NUM})\s*h(?:ours?|rs?)?\b", f) \
      or re.search(rf"every\s+({_NUM})\s*(?:-|to)\s*({_NUM})\s*hours?", f)
  if m:
    t.period, t.period_max, t.period_unit = float(m.group(1)), float(m.group(2)), "h"
    return t
  m = re.search(rf"\bq\s*({_NUM})\s*h(?:ours?|rs?)?\b", f) \
      or re.search(rf"every\s+({_NUM})\s*hours?", f)
  if m:
    t.period, t.period_unit = float(m.group(1)), "h"
    t.per_day = 24 / t.period
    return t
  m = re.search(rf"\bq\s*({_NUM})\s*(?:wk|weeks?)\b", f) or re.search(rf"every\s+({_NUM})\s*weeks?", f)
  if m:
    t.period, t.period_unit = float(m.group(1)), "wk"
    return t
  if re.search(r"\bweekly\b|once a week|\bq\s*week\b|every week", f):
    t.period, t.period_unit = 1, "wk"
    return t
  if re.search(r"\bmonthly\b|every month|once a month", f):
    t.period, t.period_unit = 1, "mo"
    return t
  if re.search(r"\bqod\b|every other day", f):
    t.period, t.period_unit = 2, "d"
    return t

  for pat, n in ((r"\bqid\b|four times", 4), (r"\btid\b|three times", 3),
                 (r"\bbid\b|twice", 2)):
    if re.search(pat, f):
      t.per_day, t.period, t.period_unit = n, 24 / n, "h"
      t.institution_specified = True
      return t
  if re.search(r"\bqhs\b|at bedtime|\bbedtime\b|\bhs\b|nightly", f):
    t.per_day, t.event = 1, "HS"
    return t
  if re.search(r"\bdaily\b|\bqd\b|once a day|every day|every morning|\bqam\b|in am\b", f):
    t.per_day, t.period, t.period_unit = 1, 24, "h"
    t.institution_specified = True
    return t
  return t


def parse_dose(quantity: str | None) -> tuple[float, float | None] | None:
  """Numeric dose: (value, None) or (low, high) for a range like "1-2"."""
  q = (quantity or "").strip()
  m = re.fullmatch(rf"({_NUM})\s*(?:-|to)\s*({_NUM})", q)
  if m:
    return float(m.group(1)), float(m.group(2))
  m = re.fullmatch(_NUM, q)
  if m:
    return float(q), None
  return None


def ucum_unit(unit: str | None) -> tuple[bool, str | None]:
  """Map a dose unit to UCUM.

  Returns:
    (known, ucum). known=False means the unit can't be coded, so callers
    should not emit a structured dose. ucum=None with known=True means a
    countable product (tablets, puffs): emit the value without a unit.
  """
  u = (unit or "").strip().lower()
  if not u:
    return True, None
  if u in _UCUM_UNITS:
    return True, _UCUM_UNITS[u]
  if u in _COUNT_UNITS:
    return True, None
  return False, None


_ROUTE_PHRASES = {"oral": "by mouth", "ophthalmic": "in the affected eye(s)",
                  "otic": "in the affected ear(s)", "topical": "topically",
                  "inhaled": "by inhalation", "inhalation": "by inhalation",
                  "subcutaneous": "subcutaneously", "intramuscular": "intramuscularly",
                  "intravenous": "intravenously", "nasal": "in each nostril",
                  "rectal": "rectally"}


def _fmt(n: float) -> str:
  return f"{n:.2f}".rstrip("0").rstrip(".")


@dataclass
class Sig:
  """A medication's dosing, read once for every exporter."""
  timing: Timing
  dose: tuple[float, float | None] | None   # per administration
  daily_dose: float | None                   # for divided doses
  unit_known: bool
  ucum: str | None
  text: str                                  # full human-readable sig


def build_sig(med) -> Sig:
  """Read a Medication into a Sig with structured and free-text forms."""
  timing = parse_frequency(med.frequency, med.prn)
  raw = parse_dose(med.dose_quantity)
  unit_known, ucum = ucum_unit(med.dose_unit)
  unit = (med.dose_unit or "").strip()
  unit_sp = f" {unit}" if unit else ""

  dose = raw
  daily = None
  dose_text = f"{med.dose_quantity}{unit_sp}".strip()
  if timing.divided and raw and raw[1] is None:
    daily = raw[0]
    if timing.per_day and not timing.ambiguous:
      per = raw[0] / timing.per_day
      dose = (round(per, 2), None)
      dose_text = (f"{_fmt(per)}{unit_sp} per dose "
                   f"({_fmt(daily)}{unit_sp}/day divided {_ABBR_WORDS[int(timing.per_day)]})")
    else:
      dose = None  # per-dose amount depends on how many doses: sig carries it
      dose_text = f"{_fmt(daily)}{unit_sp}/day divided {timing.text.lower().replace('divided', '').strip()}"
  elif raw is None:
    dose = None

  parts = [dose_text]
  route = (med.route or "").strip()
  if route:
    parts.append(_ROUTE_PHRASES.get(route.lower(), route.lower()))
  freq_phrase = timing.phrase()
  if timing.divided and timing.ambiguous:
    freq_phrase = ""
  if freq_phrase and freq_phrase.lower() not in dose_text.lower():
    parts.append(freq_phrase)
  if timing.prn:
    reason = getattr(med, "prn_reason", None) or getattr(med, "indication", None)
    parts.append(f"as needed for {reason}" if reason else "as needed")
  text = " ".join(p for p in parts if p)
  if med.instructions:
    text += f". {med.instructions}"
  return Sig(timing=timing, dose=dose, daily_dose=daily, unit_known=unit_known,
             ucum=ucum, text=text)
