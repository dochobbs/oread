"""
Growth reference tests: WHO < 24 months, CDC 2000 from 24 months, CDC 2022
extended BMI above the 95th percentile, fractional ages, no 3-97 clamp.

Expected values are read from the source CSVs in knowledge/growth/data/.
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

import pytest
from scipy import stats

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from knowledge.growth import cdc_2000 as g

DATA = ROOT / "knowledge" / "growth" / "data"


def _cdc_row(filename: str, sex: str, agemos: float) -> dict:
  with open(DATA / filename, encoding="utf-8-sig", newline="") as f:
    for r in csv.DictReader(f):
      r = {k.strip().lower(): v for k, v in r.items()}
      if r["sex"] == sex and float(r["agemos"]) == agemos:
        return r
  raise KeyError(agemos)


def test_birth_weight_uses_who_not_cdc():
  """Birth uses WHO. (The old CDC table's male L of -0.3053 at birth was wrong:
  CDC wtageinf.csv, agemos 0, has L = 1.815151075.)"""
  # WHO boys weight-for-age, month 0: L 0.3487, M 3.3464, S 0.14602
  assert g.generate_weight_at_percentile(50, 0, "male") == pytest.approx(3.35, abs=0.01)
  assert g._lms("weight", 0, "male") == pytest.approx((0.3487, 3.3464, 0.14602))


def test_cdc_from_24_months():
  row = _cdc_row("wtage.csv", "1", 120.5)
  assert g._lms("weight", 120.5, "male") == pytest.approx(
    (float(row["l"]), float(row["m"]), float(row["s"])))
  row = _cdc_row("statage.csv", "2", 24.0)
  assert g.generate_height_at_percentile(50, 24, "female") == pytest.approx(float(row["m"]), abs=0.05)


def test_fractional_age_interpolates():
  lo = g._lms("weight", 6, "female")[1]
  hi = g._lms("weight", 7, "female")[1]
  mid = g._lms("weight", 6.5, "female")[1]
  assert lo < mid < hi
  assert mid == pytest.approx((lo + hi) / 2)
  assert g.age_in_months(365.25) == pytest.approx(12.0)


def test_extended_bmi_above_95th():
  row = _cdc_row("bmi-age-2022.csv", "1", 120.5)
  p95, sigma = float(row["p95"]), float(row["sigma"])
  bmi = p95 + sigma  # one sigma above P95
  r = g.calculate_bmi_percentile(bmi, 120.5, "male")
  expected = 90 + 10 * stats.norm.cdf(1.0)
  assert r.percentile == pytest.approx(expected, abs=0.05)
  assert r.z_score == pytest.approx(stats.norm.ppf(expected / 100), abs=0.01)
  severe = g.calculate_bmi_percentile(1.3 * p95, 120.5, "male")
  assert severe.interpretation.startswith("Severe obesity")
  # Continuity at the 95th percentile
  at95 = g.calculate_bmi_percentile(p95, 120.5, "male")
  assert at95.percentile == pytest.approx(95.0, abs=0.1)


def test_no_3_to_97_clamp():
  # Obesity is defined on BMI-for-age, which the obesity pattern drives; with
  # no 3-97 clamp, BMI percentiles above 97 must be reachable, and once the
  # 6-month ramp is done the child stays >= 95th.
  bmi_pcts = []
  for seed in range(1, 6):
    traj = g.GrowthTrajectory("male", weight_percentile=96, pattern="obesity",
                              pattern_onset_age=24, rng=random.Random(seed))
    for age in range(30, 200, 12):
      w, h, _, bmi = traj.generate_measurement(age)
      pct = g.calculate_bmi_percentile(bmi, age, "male").percentile
      bmi_pcts.append(pct)
      assert pct >= 95, (seed, age, pct)
      assert g.PERCENTILE_FLOOR <= traj.weight_percentile <= g.PERCENTILE_CEILING
  assert max(bmi_pcts) > 97


def test_trajectory_reproducible_with_rng():
  a = g.GrowthTrajectory("female", rng=random.Random(7))
  b = g.GrowthTrajectory("female", rng=random.Random(7))
  assert [a.generate_measurement(m) for m in (1, 2.5, 6.2, 30)] == \
         [b.generate_measurement(m) for m in (1, 2.5, 6.2, 30)]
