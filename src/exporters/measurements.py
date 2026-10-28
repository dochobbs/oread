"""
Vital-sign sessions shared by the C-CDA and FHIR exporters.

The engine records each weigh-in twice: as the encounter's VitalSigns and as a
GrowthMeasurement on the same date (with percentiles). Exporting both gives
two weight observations for one measurement. This module merges them into one
session per measurement so every format emits each value once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from src.models import Patient


def as_date(value: date | datetime) -> date:
  return value.date() if isinstance(value, datetime) else value


@dataclass
class VitalSession:
  """One measurement session: encounter vitals and/or same-day growth data."""
  when: date | datetime
  encounter_id: str | None
  vitals: object | None
  growth: object | None

  def _pick(self, vs_attr: str, gm_attr: str | None = None):
    v = getattr(self.vitals, vs_attr, None) if self.vitals is not None else None
    if v is None and gm_attr and self.growth is not None:
      v = getattr(self.growth, gm_attr, None)
    return v

  def values(self) -> dict[str, float | None]:
    gm = self.growth
    return {
      "height": self._pick("height_cm", "height_cm"),
      "weight": self._pick("weight_kg", "weight_kg"),
      "bmi": self._pick("bmi", "bmi"),
      "bmi_pct": gm.bmi_percentile if gm is not None else None,
      "hc": self._pick("head_circumference_cm", "head_circumference_cm"),
      "hc_pct": gm.hc_percentile if gm is not None else None,
      "temp": self._pick("temperature_f"),
      "hr": self._pick("heart_rate"),
      "rr": self._pick("respiratory_rate"),
      "sbp": self._pick("blood_pressure_systolic"),
      "dbp": self._pick("blood_pressure_diastolic"),
      "spo2": self._pick("oxygen_saturation"),
    }


def vital_sessions(patient: Patient) -> list[VitalSession]:
  """Encounter vitals merged with same-day growth data, plus growth-only days.

  Newest first. A growth measurement attaches to at most one encounter.
  """
  growth_by_date = {}
  for gm in patient.growth_data:
    growth_by_date.setdefault(gm.date, gm)
  used: set[date] = set()
  sessions = []
  for enc in sorted(patient.encounters, key=lambda e: e.date, reverse=True):
    vs = enc.vital_signs
    if not vs:
      continue
    when = vs.date or enc.date
    day = as_date(when)
    gm = growth_by_date.get(day) if day not in used else None
    if gm is not None:
      used.add(day)
    sessions.append(VitalSession(when=when, encounter_id=enc.id, vitals=vs, growth=gm))
  for day, gm in growth_by_date.items():
    if day not in used:
      sessions.append(VitalSession(when=gm.date, encounter_id=gm.encounter_id,
                                   vitals=None, growth=gm))
  sessions.sort(key=lambda s: as_date(s.when), reverse=True)
  return sessions
