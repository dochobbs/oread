"""
Unit tests for the due-dose rules in src/engines/immunizations.py.

Each case is a hand-built history checked against the AAP 2026 schedule
(Table 1 routine ages, Table 2 catch-up minimum intervals, Notes).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

from dateutil.relativedelta import relativedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engines import immunizations as imz

DOB = date(2024, 1, 15)


def at(months: int = 0, days: int = 0) -> date:
  return DOB + relativedelta(months=months) + timedelta(days=days)


def due(visit: date, history: dict[str, list[date]], dob: date = DOB) -> dict[str, int]:
  return dict(imz.due_series_doses(dob, visit, history))


def routine_infant_history(through_months: int) -> dict[str, list[date]]:
  """A fully on-time history: birth HepB, then 2/4/6 mo doses as of a visit age."""
  h: dict[str, list[date]] = {"HepB": [DOB, at(1)]}
  for m in (2, 4, 6):
    if m <= through_months:
      for a in ("DTaP", "Hib", "PCV", "IPV", "RV"):
        h.setdefault(a, []).append(at(m))
  if through_months >= 6:
    h["HepB"].append(at(6))
  return h


def test_six_month_visit_at_5_7_months_gets_dose_3s():
  visit = at(5, 21)  # about 5.7 months
  d = due(visit, routine_infant_history(4))
  assert d == {"HepB": 3, "DTaP": 3, "Hib": 3, "PCV": 3, "IPV": 3, "RV": 3}


def test_two_month_visit():
  d = due(at(2), {"HepB": [DOB, at(1)]})
  assert d == {"DTaP": 1, "Hib": 1, "PCV": 1, "IPV": 1, "RV": 1}


def test_twelve_month_visit_and_mmr_minimum_age():
  h = routine_infant_history(6)
  # 3 days before the 1st birthday: inside the 4-day grace period
  d = due(at(12, -3), h)
  assert d == {"Hib": 4, "PCV": 4, "MMR": 1, "VAR": 1, "HepA": 1}
  # 6 days early: live vaccines and HepA not yet valid; Hib/PCV boosters also
  # need age >= 12 months
  d = due(at(12, -6), h)
  assert "MMR" not in d and "VAR" not in d and "HepA" not in d
  assert "Hib" not in d and "PCV" not in d


def test_rotavirus_not_started_after_14_weeks_6_days():
  h = {"HepB": [DOB, at(1)]}
  assert due(DOB + timedelta(weeks=14, days=6), h).get("RV") == 1
  assert "RV" not in due(DOB + timedelta(weeks=15), h)


def test_rotavirus_no_dose_after_8_months():
  h = {"RV": [at(2), at(4)]}
  assert due(at(7), h).get("RV") == 3
  assert "RV" not in due(at(8, 1), h)


def test_never_repeats_a_dose_number():
  h = routine_infant_history(6)
  d = due(at(7), h)
  for antigen, n in d.items():
    assert n == len(h.get(antigen, [])) + 1


def test_dtap4_needs_6_months_after_dose_3():
  h = routine_infant_history(6)
  h["DTaP"][2] = at(10)  # late dose 3
  assert "DTaP" not in due(at(15), h)
  assert due(at(16), h).get("DTaP") == 4


def test_catch_up_unvaccinated_4_year_old():
  dob = date(2021, 3, 1)
  visit = dob + relativedelta(years=4)
  d = dict(imz.due_series_doses(dob, visit, {}))
  # AAP 2026: no RV after 8 months; Hib not needed >= 5 y but 15-59 mo
  # unvaccinated get 1 dose; healthy PCV 2-4 y incomplete get 1 dose.
  assert d == {"HepB": 1, "DTaP": 1, "Hib": 1, "PCV": 1, "IPV": 1,
               "MMR": 1, "VAR": 1, "HepA": 1}

  # One dose of each given today; 4 weeks later the next doses.
  h = {k: [visit] for k in d}
  d2 = dict(imz.due_series_doses(dob, visit + timedelta(weeks=4), h))
  # Hib and PCV complete after a single dose at this age.
  assert "Hib" not in d2 and "PCV" not in d2
  assert d2.get("DTaP") == 2 and d2.get("IPV") == 2 and d2.get("HepB") == 2
  assert d2.get("MMR") == 2  # catch-up MMR: 2 doses >= 4 weeks apart
  assert "VAR" not in d2     # varicella < 13 y: 3 months
  assert "HepA" not in d2    # HepA: 6 months


def test_hib_first_dose_12_to_14_months_two_dose_series():
  h = {"Hib": [at(13)]}
  assert "Hib" not in due(at(13) + timedelta(weeks=7), h)
  assert due(at(13) + timedelta(weeks=8), h).get("Hib") == 2
  h["Hib"].append(at(15))
  assert "Hib" not in due(at(18), h)


def test_ipv_dose_4_not_needed_when_dose_3_at_4_years():
  dob = date(2018, 1, 1)
  h = {"IPV": [dob + relativedelta(months=40), dob + relativedelta(months=41),
               dob + relativedelta(months=48)]}
  assert "IPV" not in dict(imz.due_series_doses(dob, dob + relativedelta(years=5), h))


def test_dtap_5_not_needed_when_dose_4_at_4_years():
  dob = date(2018, 1, 1)
  h = {"DTaP": [dob + relativedelta(months=m) for m in (2, 4, 6, 48)]}
  assert "DTaP" not in dict(imz.due_series_doses(dob, dob + relativedelta(years=5), h))


def test_dtap_series_stops_at_7_years_and_tdap_catches_up():
  dob = date(2015, 1, 1)
  h = {"DTaP": [dob + relativedelta(months=m) for m in (2, 4, 6)]}
  d = dict(imz.due_series_doses(dob, dob + relativedelta(years=8), h))
  assert "DTaP" not in d
  assert d.get("Tdap") == 1


def test_adolescent_doses_at_11():
  dob = date(2014, 6, 1)
  full = {"DTaP": [dob + relativedelta(months=m) for m in (2, 4, 6, 15, 48)],
          "HPV": [dob + relativedelta(years=9), dob + relativedelta(years=10)]}
  d = dict(imz.due_series_doses(dob, dob + relativedelta(years=11), full))
  assert d.get("Tdap") == 1 and d.get("MenACWY") == 1 and "HPV" not in d


def test_hpv_two_vs_three_dose_series():
  dob = date(2008, 1, 1)
  start_12 = {"HPV": [dob + relativedelta(years=12)]}
  assert dict(imz.due_series_doses(dob, dob + relativedelta(years=12, months=6), start_12))["HPV"] == 2
  start_15 = {"HPV": [dob + relativedelta(years=15)]}
  assert dict(imz.due_series_doses(dob, dob + relativedelta(years=15, months=1), start_15))["HPV"] == 2
  two = {"HPV": [dob + relativedelta(years=15), dob + relativedelta(years=15, months=1)]}
  assert dict(imz.due_series_doses(dob, dob + relativedelta(years=15, months=6), two))["HPV"] == 3


def test_menacwy_first_dose_at_16_needs_no_booster():
  dob = date(2008, 1, 1)
  h = {"MenACWY": [dob + relativedelta(years=16)]}
  assert "MenACWY" not in dict(imz.due_series_doses(dob, dob + relativedelta(years=17), h))


def test_flu_two_doses_first_season_then_one():
  dob = date(2024, 3, 1)
  v1 = date(2024, 10, 1)
  assert imz.flu_dose_due(dob, v1, []) == 1
  assert imz.flu_doses_needed(dob, 2024, v1, []) == 2
  assert imz.flu_dose_due(dob, v1 + timedelta(weeks=3), [v1]) is None
  assert imz.flu_dose_due(dob, v1 + timedelta(weeks=4), [v1]) == 2
  prior = [v1, v1 + timedelta(weeks=4)]
  assert imz.flu_doses_needed(dob, 2025, date(2025, 10, 1), prior) == 1
  assert imz.flu_dose_due(dob, date(2025, 10, 1), prior) == 1


def test_flu_not_before_6_months_or_in_july():
  dob = date(2024, 6, 1)
  assert imz.flu_dose_due(dob, date(2024, 11, 1), []) is None  # 5 months old
  assert imz.flu_dose_due(dob, date(2025, 7, 15), []) is None   # July: no vaccine


def test_rsv_antibody_first_season_only():
  dob = date(2025, 5, 1)
  assert not imz.rsv_mab_due(dob, date(2025, 9, 1), [])          # before season
  assert imz.rsv_mab_due(dob, date(2025, 11, 1), [])             # entering season, 6 mo
  assert not imz.rsv_mab_due(dob, date(2026, 1, 15), [])         # 8.5 months
  assert not imz.rsv_mab_due(dob, date(2025, 11, 1), [], maternal_rsv_vaccine=True)


def test_covid_6_to_23_months():
  dob = date(2025, 1, 1)
  assert imz.covid_dose_due(dob, date(2025, 6, 1), []) is None   # 5 months
  assert imz.covid_dose_due(dob, date(2025, 7, 1), []) == 1
  assert imz.covid_dose_due(dob, date(2025, 7, 20), [date(2025, 7, 1)]) is None
  assert imz.covid_dose_due(dob, date(2025, 7, 29), [date(2025, 7, 1)]) == 2
  assert imz.covid_dose_due(dob, date(2027, 1, 2), []) is None   # 24 months


def test_engine_seed_messiness_overrides_engine_level():
  from src.engines import PedsEngine
  from src.models import GenerationSeed
  engine = PedsEngine(use_llm=False, messiness_level=0)
  assert engine._hesitancy_messiness(GenerationSeed(messiness_level=4)) == 4
  assert engine._hesitancy_messiness(GenerationSeed()) == 0
  engine5 = PedsEngine(use_llm=False, messiness_level=5)
  assert engine5._hesitancy_messiness(GenerationSeed()) == 5
  assert engine5._hesitancy_messiness(GenerationSeed(messiness_level=0)) == 0
