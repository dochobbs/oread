"""
Pediatric growth references using the LMS method.

Reference by age band (CDC recommendation since 2010: WHO growth standards
for children under 2 years, CDC 2000 growth charts from 2 years):

- 0 to < 24 months: WHO Child Growth Standards (2006) weight-for-age,
  length-for-age and head circumference-for-age. LMS tables as published by
  CDC: https://www.cdc.gov/growthcharts/who-data-files.htm
  (files from https://ftp.cdc.gov/pub/Health_Statistics/NCHS/growthcharts/,
  retrieved 2026-10-27).
- 24 to 240 months: CDC 2000 growth charts weight-for-age (wtage.csv) and
  stature-for-age (statage.csv). https://www.cdc.gov/growthcharts/
  (data from https://www.cdc.gov/growthcharts/data/zscore/, retrieved
  2026-10-27).
- Head circumference 24 to 36 months: CDC 2000 infant head circumference-
  for-age (hcageinf.csv); WHO HC tables used here stop at 24 months.
- BMI-for-age 24 to 240 months: CDC extended BMI-for-age charts (2022),
  bmi-age-2022.csv, https://www.cdc.gov/growthcharts/extended-bmi-data-files.htm
  (retrieved 2026-10-27). At or below the 95th percentile the LMS parameters
  apply. Above the 95th percentile (Wei R et al., Ann Hum Biol
  2020;47(6):514-521):
      percentile = 90 + 10 * Phi((BMI - P95) / sigma)
      BMI = P95 + sigma * Phi^-1((percentile - 90) / 10)
      z = Phi^-1(percentile / 100)

The CSVs in knowledge/growth/data/ are the unmodified source files.

The LMS method:
    Z = ((value/M)^L - 1) / (L * S)   when L != 0
    Z = ln(value/M) / S               when L == 0
    Percentile = Phi(Z)

Ages are fractional months. Tables are interpolated linearly between rows.
"""

from __future__ import annotations

import csv
import math
import random as _random
from bisect import bisect_left
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from scipy import stats

DATA_DIR = Path(__file__).parent / "data"

# Average days per month, used to convert age in days to fractional months
# (WHO Anthro convention: 365.25 / 12).
DAYS_PER_MONTH = 30.4375

# CDC recommends WHO standards below 24 months, CDC 2000 charts from 24 months.
WHO_MAX_AGE_MONTHS = 24.0

Sex = Literal["male", "female"]
LmsRow = tuple[float, float, float, float]  # (age_months, L, M, S)


def age_in_months(age_days: int | float) -> float:
    """Fractional age in months from age in days."""
    return age_days / DAYS_PER_MONTH


def bmi_chart_age(age_months: float) -> float | None:
    """Age to read the BMI-for-age chart at, or None if too young for BMI.

    The 2-year visit lands a few days either side of 24.0 fractional months
    (730 days is 23.98), and BMI is first plotted there, so a visit within a
    week before 24 months reads the chart at 24.
    """
    if age_months < 24 - 7 / DAYS_PER_MONTH:
        return None
    return max(age_months, 24.0)


@lru_cache(maxsize=None)
def _load_who(measure: str, sex: Sex) -> tuple[LmsRow, ...]:
    who_sex = "Boys" if sex == "male" else "Girls"
    path = DATA_DIR / f"WHO-{who_sex}-{measure}-for-age-Percentiles.csv"
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [(float(r["Month"]), float(r["L"]), float(r["M"]), float(r["S"]))
                for r in csv.DictReader(f)]
    return tuple(sorted(rows))


@lru_cache(maxsize=None)
def _load_cdc(filename: str, sex: Sex) -> tuple[tuple[float, ...], ...]:
    """CDC LMS file rows for one sex: (agemos, L, M, S[, sigma, P95])."""
    code = "1" if sex == "male" else "2"
    with open(DATA_DIR / filename, encoding="utf-8-sig", newline="") as f:
        out = []
        for r in csv.DictReader(f):
            r = {k.strip().lower(): v for k, v in r.items()}
            if r["sex"].strip() != code:
                continue
            row = [float(r["agemos"]), float(r["l"]), float(r["m"]), float(r["s"])]
            if "sigma" in r:
                row += [float(r["sigma"]), float(r["p95"])]
            out.append(tuple(row))
    return tuple(sorted(out))


def _interpolate(rows: tuple[tuple[float, ...], ...], age_months: float) -> tuple[float, ...]:
    """Linear interpolation of every column after age; clamps to table range."""
    ages = [r[0] for r in rows]
    if age_months <= ages[0]:
        return rows[0][1:]
    if age_months >= ages[-1]:
        return rows[-1][1:]
    i = bisect_left(ages, age_months)
    if ages[i] == age_months:
        return rows[i][1:]
    lo, hi = rows[i - 1], rows[i]
    t = (age_months - lo[0]) / (hi[0] - lo[0])
    return tuple(a + t * (b - a) for a, b in zip(lo[1:], hi[1:]))


def _lms(measure: str, age_months: float, sex: Sex) -> tuple[float, float, float]:
    """LMS for a measure at a fractional age, choosing the reference by age."""
    if measure == "weight":
        rows = _load_who("Weight", sex) if age_months < WHO_MAX_AGE_MONTHS \
            else _load_cdc("wtage.csv", sex)
    elif measure == "height":
        rows = _load_who("Length", sex) if age_months < WHO_MAX_AGE_MONTHS \
            else _load_cdc("statage.csv", sex)
    elif measure == "hc":
        rows = _load_who("Head-Circumference", sex) if age_months < WHO_MAX_AGE_MONTHS \
            else _load_cdc("hcageinf.csv", sex)
    elif measure == "bmi":
        rows = _load_cdc("bmi-age-2022.csv", sex)
    else:
        raise ValueError(f"unknown measure {measure!r}")
    L, M, S = _interpolate(rows, age_months)[:3]
    return L, M, S


def _bmi_extended(age_months: float, sex: Sex) -> tuple[float, float, float, float, float]:
    """(L, M, S, sigma, P95) from the CDC 2022 extended BMI table."""
    return _interpolate(_load_cdc("bmi-age-2022.csv", sex), age_months)  # type: ignore[return-value]


@dataclass
class GrowthResult:
    """Result of a growth calculation."""
    value: float
    percentile: float
    z_score: float
    interpretation: str


def _z_score_from_lms(value: float, L: float, M: float, S: float) -> float:
    """
    Calculate Z-score from value and LMS parameters.
    """
    if abs(L) < 1e-10:  # L ≈ 0
        return math.log(value / M) / S
    else:
        return (math.pow(value / M, L) - 1) / (L * S)


def _value_from_lms_z(z: float, L: float, M: float, S: float) -> float:
    """
    Calculate value from Z-score and LMS parameters.
    """
    if abs(L) < 1e-10:  # L ≈ 0
        return M * math.exp(z * S)
    else:
        return M * math.pow(1 + L * S * z, 1 / L)


def _percentile_from_z(z: float) -> float:
    """Convert Z-score to percentile using normal CDF."""
    return stats.norm.cdf(z) * 100


def _z_from_percentile(percentile: float) -> float:
    """Convert percentile to Z-score using inverse normal CDF."""
    return stats.norm.ppf(percentile / 100)


def _interpret_percentile(percentile: float, measure: str) -> str:
    """Interpret a growth percentile."""
    if percentile < 3:
        return f"Very low {measure} (<3rd percentile)"
    elif percentile < 10:
        return f"Low {measure} (3rd-10th percentile)"
    elif percentile < 25:
        return f"Low-normal {measure} (10th-25th percentile)"
    elif percentile <= 75:
        return f"Normal {measure} (25th-75th percentile)"
    elif percentile <= 90:
        return f"High-normal {measure} (75th-90th percentile)"
    elif percentile <= 97:
        return f"High {measure} (90th-97th percentile)"
    else:
        return f"Very high {measure} (>97th percentile)"


def _result(measure: str, label: str, value: float, age_months: float, sex: Sex) -> GrowthResult:
    L, M, S = _lms(measure, age_months, sex)
    z = _z_score_from_lms(value, L, M, S)
    percentile = _percentile_from_z(z)
    return GrowthResult(
        value=value,
        percentile=round(float(percentile), 1),
        z_score=round(float(z), 2),
        interpretation=_interpret_percentile(percentile, label),
    )


def calculate_weight_percentile(
    weight_kg: float,
    age_months: float,
    sex: Sex,
) -> GrowthResult:
    """
    Calculate weight-for-age percentile (WHO < 24 months, CDC 2000 after).

    Args:
        weight_kg: Weight in kilograms
        age_months: Age in fractional months (0-240)
        sex: "male" or "female"

    Returns:
        GrowthResult with percentile, z-score, and interpretation
    """
    return _result("weight", "weight", weight_kg, age_months, sex)


def calculate_height_percentile(
    height_cm: float,
    age_months: float,
    sex: Sex,
) -> GrowthResult:
    """
    Calculate length-for-age (WHO < 24 months) or stature-for-age (CDC 2000)
    percentile.

    Args:
        height_cm: Height/length in centimeters
        age_months: Age in fractional months (0-240)
        sex: "male" or "female"

    Returns:
        GrowthResult with percentile, z-score, and interpretation
    """
    return _result("height", "height", height_cm, age_months, sex)


def calculate_hc_percentile(
    hc_cm: float,
    age_months: float,
    sex: Sex,
) -> GrowthResult:
    """
    Calculate head circumference percentile (0-36 months only).

    Args:
        hc_cm: Head circumference in centimeters
        age_months: Age in fractional months (0-36)
        sex: "male" or "female"

    Returns:
        GrowthResult with percentile, z-score, and interpretation
    """
    if age_months > 36:
        raise ValueError("Head circumference charts only available for ages 0-36 months")
    return _result("hc", "head circumference", hc_cm, age_months, sex)


def calculate_bmi_percentile(
    bmi: float,
    age_months: float,
    sex: Sex,
) -> GrowthResult:
    """
    Calculate BMI-for-age percentile (24+ months), CDC 2022 extended charts.

    Above the 95th percentile, uses the extended method (see module
    docstring), so severe obesity gets a finite, meaningful percentile.

    Args:
        bmi: Body mass index
        age_months: Age in fractional months (24-240)
        sex: "male" or "female"

    Returns:
        GrowthResult with percentile, z-score, and interpretation
    """
    if age_months < 24:
        raise ValueError("BMI-for-age charts only available for ages 24+ months")

    L, M, S, sigma, p95 = _bmi_extended(age_months, sex)
    if bmi <= p95:
        z = _z_score_from_lms(bmi, L, M, S)
        percentile = _percentile_from_z(z)
    else:
        percentile = 90 + 10 * stats.norm.cdf((bmi - p95) / sigma)
        z = _z_from_percentile(percentile)

    # CDC weight categories; severe obesity = BMI >= 120% of the 95th
    # percentile (CDC extended BMI charts / AAP 2023 obesity CPG, class 2+).
    if percentile < 5:
        interpretation = "Underweight (<5th percentile)"
    elif percentile < 85:
        interpretation = "Healthy weight (5th-85th percentile)"
    elif percentile < 95:
        interpretation = "Overweight (85th-95th percentile)"
    elif bmi >= 1.2 * p95:
        interpretation = "Severe obesity (≥120% of 95th percentile)"
    else:
        interpretation = "Obese (≥95th percentile)"

    return GrowthResult(
        value=bmi,
        percentile=round(float(percentile), 1),
        z_score=round(float(z), 2),
        interpretation=interpretation,
    )


def calculate_bmi(weight_kg: float, height_cm: float) -> float:
    """Calculate BMI from weight and height."""
    height_m = height_cm / 100
    return round(weight_kg / (height_m * height_m), 1)


def bmi_at_percentile(percentile: float, age_months: float, sex: Sex) -> float:
    """BMI value at a BMI-for-age percentile (24+ months).

    Inverse of calculate_bmi_percentile: LMS up to the 95th, CDC 2022 extended
    method above it (percentile = 90 + 10 * Phi((BMI - P95) / sigma)).
    """
    L, M, S, sigma, p95 = _bmi_extended(age_months, sex)
    if percentile <= 95:
        return _value_from_lms_z(_z_from_percentile(percentile), L, M, S)
    q = min((percentile - 90) / 10, 0.9999)
    return p95 + sigma * float(stats.norm.ppf(q))


def _value_at_percentile(measure: str, percentile: float, age_months: float, sex: Sex) -> float:
    L, M, S = _lms(measure, age_months, sex)
    return _value_from_lms_z(_z_from_percentile(percentile), L, M, S)


def generate_weight_at_percentile(
    percentile: float,
    age_months: float,
    sex: Sex,
) -> float:
    """
    Generate a weight value at a given percentile.

    Args:
        percentile: Target percentile (0-100, exclusive)
        age_months: Age in fractional months
        sex: "male" or "female"

    Returns:
        Weight in kg at that percentile
    """
    return round(_value_at_percentile("weight", percentile, age_months, sex), 2)


def generate_height_at_percentile(
    percentile: float,
    age_months: float,
    sex: Sex,
) -> float:
    """
    Generate a height value at a given percentile.

    Args:
        percentile: Target percentile (0-100, exclusive)
        age_months: Age in fractional months
        sex: "male" or "female"

    Returns:
        Height in cm at that percentile
    """
    return round(_value_at_percentile("height", percentile, age_months, sex), 1)


def generate_hc_at_percentile(
    percentile: float,
    age_months: float,
    sex: Sex,
) -> float:
    """
    Generate a head circumference at a given percentile.

    Args:
        percentile: Target percentile (0-100, exclusive)
        age_months: Age in fractional months (0-36)
        sex: "male" or "female"

    Returns:
        Head circumference in cm at that percentile
    """
    if age_months > 36:
        raise ValueError("Head circumference charts only available for ages 0-36 months")
    return round(_value_at_percentile("hc", percentile, age_months, sex), 1)


# Numerical bounds for the percentile random walk (z = +/-3.09). Not a
# clinical clamp: it keeps the LMS inversion finite while allowing values far
# beyond the 3rd/97th percentiles, including severe obesity.
PERCENTILE_FLOOR = 0.1
PERCENTILE_CEILING = 99.9


class GrowthTrajectory:
    """
    Generates a coherent growth trajectory for a patient.

    Uses a "percentile channel" approach where a patient generally
    tracks along the same percentile lines, with natural variation.

    Supports growth patterns:
      - normal: Standard percentile channel tracking
      - ftt: Failure to thrive (weight falls, height preserved)
      - obesity: Weight percentile >> height percentile
      - preterm_catchup: Accelerated catch-up growth in first 24 months
      - growth_delay: Plateau during specific age range
    """

    def __init__(
        self,
        sex: Literal["male", "female"],
        weight_percentile: float = 50,
        height_percentile: float = 50,
        hc_percentile: float = 50,
        variance: float = 0.3,  # Bug 7 fix: increased from 0.1 for more realistic variation
        # Growth pattern support
        pattern: str = "normal",  # normal, ftt, obesity, preterm_catchup, growth_delay
        pattern_onset_age: int | None = None,  # Age in months when pattern starts
        gestational_age_weeks: int | None = None,  # For preterm catch-up
        rng: _random.Random | None = None,
    ):
        """
        Initialize a growth trajectory.

        Args:
            sex: "male" or "female"
            weight_percentile: Starting weight percentile (0-100)
            height_percentile: Starting height percentile (0-100)
            hc_percentile: Starting HC percentile (0-100)
            variance: How much percentiles can drift between measurements (0-1)
            pattern: Growth pattern type (normal, ftt, obesity, preterm_catchup, growth_delay)
            pattern_onset_age: Age in months when non-normal pattern starts
            gestational_age_weeks: For preterm infants (corrected age calculation)
            rng: Random source for percentile drift (defaults to the random
                module); pass a seeded random.Random for reproducibility.
        """
        self._rng = rng or _random
        self.sex = sex
        self.weight_percentile = weight_percentile
        self.height_percentile = height_percentile
        self.hc_percentile = hc_percentile
        self.variance = variance

        # Growth pattern settings
        self.pattern = pattern
        self.pattern_onset_age = pattern_onset_age or 0
        self.gestational_age_weeks = gestational_age_weeks

        # For preterm, calculate corrected age offset
        if pattern == "preterm_catchup" and gestational_age_weeks:
            self._corrected_age_offset = (40 - gestational_age_weeks) / 4.3  # months
            # Preterm infants typically start lower
            self.weight_percentile = min(weight_percentile, 35)
            self.height_percentile = min(height_percentile, 40)
        else:
            self._corrected_age_offset = 0

        # Each child's channel: the percentile it tracks. Random noise reverts
        # toward it; a growth pattern's bias moves the channel itself, so
        # obesity / FTT still cross percentiles while normal kids track.
        self._channel = {
            "weight": self.weight_percentile,
            "height": self.height_percentile,
            "hc": self.hc_percentile,
        }
        self._last_height: float | None = None
        self._last_hc: float | None = None
        # Obesity pattern: the BMI percentile this child settles at (chosen on
        # first use, so it comes from the seeded rng)
        self._obesity_bmi_target: float | None = None

        # Track history
        self._measurements: list[tuple[float, float, float, float | None]] = []

    def _pattern_drift(self, age_months: int) -> tuple[float, float]:
        """
        Calculate pattern-specific drift bias for weight and height.

        Returns:
            Tuple of (weight_drift_bias, height_drift_bias)
        """
        if self.pattern == "ftt":
            # Failure to thrive: weight drifts down, height relatively stable
            # More severe for younger children
            if age_months < 24:
                return (-1.5, 0.0)  # Aggressive downward weight drift
            else:
                return (-0.8, 0.0)  # Slower decline for older children

        elif self.pattern == "obesity":
            if age_months >= self.pattern_onset_age:
                # Steer the weight channel toward the obese range: CDC defines
                # obesity as BMI >= 95th percentile for age and sex. Converges
                # over a few visits instead of a fixed nudge that could stall
                # below the 95th.
                time_factor = min(1.0, (age_months - self.pattern_onset_age) / 24)
                gap = max(0.0, self.OBESITY_TARGET_PERCENTILE - self._channel["weight"])
                return (gap * (0.35 + 0.65 * time_factor), 0.0)
            return (0.0, 0.0)

        elif self.pattern == "preterm_catchup":
            # Accelerated catch-up growth in first 24 months (corrected age)
            corrected_age = age_months - self._corrected_age_offset
            if corrected_age < 24:
                # Catch-up: both weight and height drift upward
                catch_up_factor = max(0.3, 1.0 - (corrected_age / 24))
                return (1.5 * catch_up_factor, 1.2 * catch_up_factor)
            return (0.0, 0.0)

        elif self.pattern == "growth_delay":
            # Plateau during specific period
            if self.pattern_onset_age <= age_months < self.pattern_onset_age + 12:
                # Minimal drift during plateau - almost no growth
                return (0.0, -0.5)  # Slight height lag
            elif age_months >= self.pattern_onset_age + 12:
                # Recovery phase - accelerated growth
                return (0.5, 0.8)
            return (0.0, 0.0)

        return (0.0, 0.0)  # Normal pattern

    # Pull toward the channel per measurement, and noise SD (percentile points
    # per unit variance). Infants cross percentiles more than older children,
    # who track their channel (Bright Futures; Mei et al., Pediatrics 2004).
    # Above the 95th cut-off with room for visit-to-visit noise
    OBESITY_TARGET_PERCENTILE = 98.5
    _REVERSION = 0.4
    _NOISE_UNDER_2Y = 15.0
    _NOISE_OVER_2Y = {"weight": 8.0, "height": 4.0, "hc": 4.0}

    def _drift_percentile(
        self,
        current: float,
        variance: float,
        bias: float = 0.0,
        metric: str = "weight",
        age_months: float = 0.0,
    ) -> float:
        """Mean-reverting walk around the child's channel; bias moves the channel.

        Only numerical bounds apply (no 3rd-97th clamp), so severe obesity and
        true failure to thrive can still be generated.
        """
        channel = self._channel.get(metric, current) + bias
        channel = max(PERCENTILE_FLOOR, min(PERCENTILE_CEILING, channel))
        self._channel[metric] = channel
        noise = self._NOISE_UNDER_2Y if age_months < 24 else self._NOISE_OVER_2Y.get(metric, 8.0)
        new = current + self._REVERSION * (channel - current) + self._rng.gauss(0, variance * noise)
        return max(PERCENTILE_FLOOR, min(PERCENTILE_CEILING, new))
    
    def generate_measurement(
        self,
        age_months: float,
        include_hc: bool | None = None,
    ) -> tuple[float, float, float | None, float | None]:
        """
        Generate a measurement at a given age.

        Args:
            age_months: Age in fractional months (see age_in_months)
            include_hc: Include head circumference (auto-determined if None)

        Returns:
            Tuple of (weight_kg, height_cm, hc_cm or None, bmi or None)
        """
        # Get pattern-specific drift biases
        weight_bias, height_bias = self._pattern_drift(age_months)

        # Apply drift to percentiles with pattern-aware bias
        self.weight_percentile = self._drift_percentile(
            self.weight_percentile, self.variance, bias=weight_bias,
            metric="weight", age_months=age_months,
        )
        self.height_percentile = self._drift_percentile(
            self.height_percentile, self.variance, bias=height_bias,
            metric="height", age_months=age_months,
        )

        # Generate measurements
        weight = generate_weight_at_percentile(
            self.weight_percentile, age_months, self.sex
        )
        height = generate_height_at_percentile(
            self.height_percentile, age_months, self.sex
        )
        # Children don't shrink: never report less than the last height
        if self._last_height is not None and height < self._last_height:
            height = self._last_height
        self._last_height = height

        # Head circumference (only for 0-36 months)
        hc = None
        if include_hc is None:
            include_hc = age_months <= 36
        if include_hc and age_months <= 36:
            self.hc_percentile = self._drift_percentile(
                self.hc_percentile, self.variance, metric="hc", age_months=age_months,
            )
            hc = generate_hc_at_percentile(
                self.hc_percentile, age_months, self.sex
            )
            if self._last_hc is not None and hc < self._last_hc:
                hc = self._last_hc
            self._last_hc = hc

        # Obesity is defined by BMI-for-age >= 95th (CDC), so drive BMI rather
        # than weight: ramp to this child's target over the 6 months after the
        # pattern starts (which the engine sets 6 months before diagnosis),
        # then derive weight from BMI and height. A pattern that starts before
        # 2 y (no BMI chart yet) is already part-way up the ramp at 24 months.
        onset = float(self.pattern_onset_age)
        chart_age = bmi_chart_age(age_months)
        if self.pattern == "obesity" and chart_age is not None and age_months >= onset:
            if self._obesity_bmi_target is None:
                self._obesity_bmi_target = self._rng.uniform(96.5, 99.5)
            natural = calculate_bmi_percentile(calculate_bmi(weight, height), chart_age, self.sex).percentile
            frac = min(1.0, (age_months - onset) / 6.0)
            # Pull toward the target from either side: left above it, the
            # drifting weight channel lands BMI in the far tail, where a
            # fraction of a percentile is a 15-20% swing in weight.
            pct = natural + frac * (self._obesity_bmi_target - natural)
            weight = round(bmi_at_percentile(pct, chart_age, self.sex) * (height / 100) ** 2, 2)
            if age_months <= 240:
                self.weight_percentile = max(PERCENTILE_FLOOR, min(
                    PERCENTILE_CEILING,
                    calculate_weight_percentile(weight, age_months, self.sex).percentile))
            self._channel["weight"] = self.weight_percentile

        # BMI (only for 24+ months)
        bmi = None
        if bmi_chart_age(age_months) is not None:
            bmi = calculate_bmi(weight, height)
        
        self._measurements.append((age_months, weight, height, hc))
        return weight, height, hc, bmi


# Vital sign normal ranges by age
VITAL_SIGNS_BY_AGE: dict[str, dict[str, tuple[float, float]]] = {
    "0-1mo": {
        "heart_rate": (100, 160),
        "respiratory_rate": (30, 60),
        "systolic_bp": (60, 90),
        "diastolic_bp": (30, 60),
        "temperature_f": (97.5, 99.5),
        "o2_sat": (95, 100),
    },
    "1-12mo": {
        "heart_rate": (100, 150),
        "respiratory_rate": (25, 40),
        "systolic_bp": (80, 100),
        "diastolic_bp": (50, 70),
        "temperature_f": (97.5, 99.5),
        "o2_sat": (95, 100),
    },
    "1-3yr": {
        "heart_rate": (90, 130),
        "respiratory_rate": (20, 30),
        "systolic_bp": (90, 105),
        "diastolic_bp": (55, 70),
        "temperature_f": (97.5, 99.5),
        "o2_sat": (95, 100),
    },
    "3-6yr": {
        "heart_rate": (80, 120),
        "respiratory_rate": (18, 25),
        "systolic_bp": (95, 110),
        "diastolic_bp": (60, 75),
        "temperature_f": (97.5, 99.5),
        "o2_sat": (95, 100),
    },
    "6-12yr": {
        "heart_rate": (70, 110),
        "respiratory_rate": (16, 22),
        "systolic_bp": (100, 120),
        "diastolic_bp": (60, 80),
        "temperature_f": (97.5, 99.5),
        "o2_sat": (95, 100),
    },
    "12-18yr": {
        "heart_rate": (60, 100),
        "respiratory_rate": (12, 20),
        "systolic_bp": (110, 130),
        "diastolic_bp": (65, 85),
        "temperature_f": (97.5, 99.5),
        "o2_sat": (95, 100),
    },
    "adult": {
        "heart_rate": (60, 100),
        "respiratory_rate": (12, 20),
        "systolic_bp": (110, 140),
        "diastolic_bp": (70, 90),
        "temperature_f": (97.5, 99.5),
        "o2_sat": (95, 100),
    },
}


def get_vital_ranges(age_months: int) -> dict[str, tuple[float, float]]:
    """Get normal vital sign ranges for an age."""
    if age_months < 1:
        return VITAL_SIGNS_BY_AGE["0-1mo"]
    elif age_months < 12:
        return VITAL_SIGNS_BY_AGE["1-12mo"]
    elif age_months < 36:
        return VITAL_SIGNS_BY_AGE["1-3yr"]
    elif age_months < 72:
        return VITAL_SIGNS_BY_AGE["3-6yr"]
    elif age_months < 144:
        return VITAL_SIGNS_BY_AGE["6-12yr"]
    elif age_months < 216:
        return VITAL_SIGNS_BY_AGE["12-18yr"]
    else:
        return VITAL_SIGNS_BY_AGE["adult"]


def generate_normal_vitals(age_months: int, rng: _random.Random | None = None) -> dict[str, float]:
    """Generate normal vital signs for an age. Pass rng for reproducible output."""
    rng = rng or _random
    
    ranges = get_vital_ranges(age_months)
    vitals = {}
    
    for name, (low, high) in ranges.items():
        # Generate value in middle 80% of range
        margin = (high - low) * 0.1
        value = rng.uniform(low + margin, high - margin)
        
        # Round appropriately
        if name in ("temperature_f", "o2_sat"):
            vitals[name] = round(value, 1)
        else:
            vitals[name] = round(value)
    
    return vitals
