"""
CDC 2000 Growth Chart calculations using the LMS method.

Reference: https://www.cdc.gov/growthcharts/

The LMS method expresses growth as:
- L (lambda): Box-Cox power transformation
- M (mu): Median
- S (sigma): Coefficient of variation

Z-score = ((value/M)^L - 1) / (L * S)  when L ≠ 0
Z-score = ln(value/M) / S              when L = 0

Percentile = Φ(Z-score) where Φ is the standard normal CDF
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from scipy import stats

# LMS parameters for CDC 2000 growth charts
# Format: age_months -> (L, M, S)
# These are sampled key points; in production you'd interpolate

# Weight-for-age (kg), Males, 0-240 months
WEIGHT_FOR_AGE_MALE: dict[int, tuple[float, float, float]] = {
    0: (-0.3053, 3.530, 0.1514),
    1: (0.0977, 4.470, 0.1359),
    2: (0.1890, 5.380, 0.1296),
    3: (0.1346, 6.123, 0.1256),
    6: (-0.0171, 7.934, 0.1215),
    9: (-0.1667, 9.180, 0.1182),
    12: (-0.2714, 10.15, 0.1149),
    18: (-0.3823, 11.47, 0.1127),
    24: (-0.4242, 12.59, 0.1139),
    36: (-0.4669, 14.34, 0.1198),
    48: (-0.5614, 16.33, 0.1307),
    60: (-0.7159, 18.62, 0.1441),
    72: (-0.8876, 20.93, 0.1555),
    84: (-1.0100, 23.39, 0.1644),
    96: (-1.0682, 25.94, 0.1722),
    108: (-1.0708, 28.58, 0.1803),
    120: (-1.0240, 31.44, 0.1893),
    132: (-0.9476, 34.77, 0.1979),
    144: (-0.8693, 38.91, 0.2044),
    156: (-0.8237, 43.87, 0.2082),
    168: (-0.8247, 49.49, 0.2091),
    180: (-0.8659, 55.38, 0.2070),
    192: (-0.9402, 60.98, 0.2016),
    204: (-1.0346, 65.89, 0.1934),
    216: (-1.1413, 70.11, 0.1837),
    228: (-1.2545, 73.71, 0.1737),
    240: (-1.3686, 76.78, 0.1642),
}

# Weight-for-age (kg), Females, 0-240 months
WEIGHT_FOR_AGE_FEMALE: dict[int, tuple[float, float, float]] = {
    0: (-0.3821, 3.399, 0.1433),
    1: (0.1744, 4.187, 0.1319),
    2: (0.3421, 5.030, 0.1253),
    3: (0.3181, 5.720, 0.1216),
    6: (0.0813, 7.351, 0.1192),
    9: (-0.0810, 8.475, 0.1175),
    12: (-0.1887, 9.363, 0.1162),
    18: (-0.3076, 10.67, 0.1165),
    24: (-0.3523, 11.91, 0.1202),
    36: (-0.3964, 13.86, 0.1294),
    48: (-0.4995, 16.06, 0.1411),
    60: (-0.6602, 18.48, 0.1522),
    72: (-0.8193, 20.93, 0.1612),
    84: (-0.9386, 23.53, 0.1691),
    96: (-0.9953, 26.31, 0.1774),
    108: (-0.9883, 29.34, 0.1868),
    120: (-0.9237, 32.78, 0.1970),
    132: (-0.8150, 36.90, 0.2068),
    144: (-0.6885, 41.74, 0.2141),
    156: (-0.5772, 47.00, 0.2173),
    168: (-0.5079, 52.11, 0.2163),
    180: (-0.4868, 56.56, 0.2116),
    192: (-0.5076, 60.08, 0.2042),
    204: (-0.5573, 62.68, 0.1954),
    216: (-0.6252, 64.52, 0.1865),
    228: (-0.7040, 65.81, 0.1784),
    240: (-0.7893, 66.75, 0.1714),
}

# Height/Length-for-age (cm), Males, 0-240 months
HEIGHT_FOR_AGE_MALE: dict[int, tuple[float, float, float]] = {
    0: (0.3487, 49.99, 0.0379),
    1: (0.1550, 54.72, 0.0370),
    2: (0.0093, 58.42, 0.0365),
    3: (-0.0928, 61.43, 0.0363),
    6: (-0.2623, 67.62, 0.0358),
    9: (-0.3040, 72.03, 0.0356),
    12: (-0.2847, 75.75, 0.0356),
    18: (-0.1884, 82.39, 0.0357),
    24: (-0.0554, 87.78, 0.0363),
    36: (0.1957, 96.10, 0.0393),
    48: (0.2708, 102.9, 0.0417),
    60: (0.2204, 109.2, 0.0432),
    72: (0.1080, 115.1, 0.0445),
    84: (-0.0168, 120.8, 0.0457),
    96: (-0.1368, 126.2, 0.0468),
    108: (-0.2427, 131.5, 0.0479),
    120: (-0.3254, 136.8, 0.0490),
    132: (-0.3816, 142.4, 0.0500),
    144: (-0.4097, 148.7, 0.0505),
    156: (-0.4134, 155.5, 0.0502),
    168: (-0.3994, 162.2, 0.0489),
    180: (-0.3757, 168.1, 0.0465),
    192: (-0.3502, 172.7, 0.0437),
    204: (-0.3295, 175.8, 0.0412),
    216: (-0.3173, 177.6, 0.0396),
    228: (-0.3134, 178.6, 0.0386),
    240: (-0.3155, 179.1, 0.0382),
}

# Height/Length-for-age (cm), Females, 0-240 months
HEIGHT_FOR_AGE_FEMALE: dict[int, tuple[float, float, float]] = {
    0: (0.3809, 49.29, 0.0379),
    1: (0.1700, 53.69, 0.0369),
    2: (0.0178, 57.07, 0.0365),
    3: (-0.0858, 59.80, 0.0361),
    6: (-0.2777, 65.73, 0.0353),
    9: (-0.3379, 70.11, 0.0350),
    12: (-0.3433, 73.96, 0.0349),
    18: (-0.2962, 80.80, 0.0352),
    24: (-0.2046, 86.40, 0.0362),
    36: (0.0047, 94.86, 0.0399),
    48: (0.0884, 101.8, 0.0428),
    60: (0.0696, 108.4, 0.0449),
    72: (-0.0049, 114.6, 0.0467),
    84: (-0.0919, 120.6, 0.0484),
    96: (-0.1759, 126.4, 0.0502),
    108: (-0.2483, 132.0, 0.0519),
    120: (-0.3033, 137.5, 0.0537),
    132: (-0.3380, 143.3, 0.0553),
    144: (-0.3547, 149.4, 0.0560),
    156: (-0.3600, 155.0, 0.0556),
    168: (-0.3607, 159.5, 0.0540),
    180: (-0.3608, 162.5, 0.0518),
    192: (-0.3616, 164.2, 0.0498),
    204: (-0.3632, 165.0, 0.0484),
    216: (-0.3655, 165.4, 0.0477),
    228: (-0.3684, 165.6, 0.0474),
    240: (-0.3718, 165.7, 0.0473),
}

# Head circumference (cm), Males, 0-36 months
HC_FOR_AGE_MALE: dict[int, tuple[float, float, float]] = {
    0: (1.8758, 34.71, 0.0369),
    1: (1.3893, 37.31, 0.0349),
    2: (1.0199, 39.21, 0.0338),
    3: (0.7459, 40.56, 0.0331),
    6: (0.2426, 43.34, 0.0318),
    9: (-0.0100, 45.19, 0.0311),
    12: (-0.1532, 46.55, 0.0308),
    18: (-0.2902, 48.15, 0.0304),
    24: (-0.3510, 49.27, 0.0303),
    36: (-0.3934, 50.65, 0.0305),
}

# Head circumference (cm), Females, 0-36 months
HC_FOR_AGE_FEMALE: dict[int, tuple[float, float, float]] = {
    0: (2.1539, 33.88, 0.0359),
    1: (1.5817, 36.42, 0.0341),
    2: (1.1416, 38.22, 0.0331),
    3: (0.8108, 39.53, 0.0324),
    6: (0.2618, 42.17, 0.0312),
    9: (-0.0362, 43.93, 0.0306),
    12: (-0.2078, 45.23, 0.0304),
    18: (-0.3685, 46.76, 0.0303),
    24: (-0.4463, 47.84, 0.0304),
    36: (-0.5101, 49.13, 0.0308),
}

# BMI-for-age, Males, 24-240 months (BMI only meaningful after 2 years)
BMI_FOR_AGE_MALE: dict[int, tuple[float, float, float]] = {
    24: (-0.7766, 16.42, 0.0861),
    36: (-1.2236, 15.79, 0.0823),
    48: (-1.4997, 15.48, 0.0839),
    60: (-1.6315, 15.34, 0.0885),
    72: (-1.6623, 15.32, 0.0950),
    84: (-1.6293, 15.44, 0.1024),
    96: (-1.5635, 15.72, 0.1102),
    108: (-1.4867, 16.15, 0.1178),
    120: (-1.4143, 16.72, 0.1250),
    132: (-1.3563, 17.44, 0.1311),
    144: (-1.3159, 18.30, 0.1360),
    156: (-1.2932, 19.27, 0.1394),
    168: (-1.2865, 20.29, 0.1413),
    180: (-1.2926, 21.29, 0.1417),
    192: (-1.3074, 22.21, 0.1407),
    204: (-1.3268, 23.02, 0.1388),
    216: (-1.3467, 23.69, 0.1364),
    228: (-1.3651, 24.22, 0.1339),
    240: (-1.3815, 24.63, 0.1317),
}

# BMI-for-age, Females, 24-240 months
BMI_FOR_AGE_FEMALE: dict[int, tuple[float, float, float]] = {
    24: (-0.6075, 16.13, 0.0917),
    36: (-0.9803, 15.58, 0.0890),
    48: (-1.1963, 15.29, 0.0903),
    60: (-1.2959, 15.17, 0.0942),
    72: (-1.3224, 15.17, 0.0997),
    84: (-1.3064, 15.32, 0.1063),
    96: (-1.2716, 15.59, 0.1132),
    108: (-1.2353, 16.00, 0.1200),
    120: (-1.2062, 16.53, 0.1264),
    132: (-1.1882, 17.20, 0.1319),
    144: (-1.1814, 18.00, 0.1361),
    156: (-1.1839, 18.88, 0.1389),
    168: (-1.1929, 19.79, 0.1401),
    180: (-1.2053, 20.66, 0.1399),
    192: (-1.2183, 21.43, 0.1388),
    204: (-1.2301, 22.07, 0.1373),
    216: (-1.2399, 22.56, 0.1358),
    228: (-1.2475, 22.93, 0.1346),
    240: (-1.2531, 23.20, 0.1338),
}


@dataclass
class GrowthResult:
    """Result of a growth calculation."""
    value: float
    percentile: float
    z_score: float
    interpretation: str


def _interpolate_lms(
    age_months: int,
    lms_table: dict[int, tuple[float, float, float]],
) -> tuple[float, float, float]:
    """
    Interpolate LMS values for a given age.
    Uses linear interpolation between known points.
    """
    ages = sorted(lms_table.keys())
    
    # Exact match
    if age_months in lms_table:
        return lms_table[age_months]
    
    # Clamp to range
    if age_months < ages[0]:
        return lms_table[ages[0]]
    if age_months > ages[-1]:
        return lms_table[ages[-1]]
    
    # Find bracketing ages
    lower_age = max(a for a in ages if a < age_months)
    upper_age = min(a for a in ages if a > age_months)
    
    # Linear interpolation factor
    t = (age_months - lower_age) / (upper_age - lower_age)
    
    L1, M1, S1 = lms_table[lower_age]
    L2, M2, S2 = lms_table[upper_age]
    
    L = L1 + t * (L2 - L1)
    M = M1 + t * (M2 - M1)
    S = S1 + t * (S2 - S1)
    
    return L, M, S


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


def calculate_weight_percentile(
    weight_kg: float,
    age_months: int,
    sex: Literal["male", "female"],
) -> GrowthResult:
    """
    Calculate weight-for-age percentile.
    
    Args:
        weight_kg: Weight in kilograms
        age_months: Age in months (0-240)
        sex: "male" or "female"
    
    Returns:
        GrowthResult with percentile, z-score, and interpretation
    """
    table = WEIGHT_FOR_AGE_MALE if sex == "male" else WEIGHT_FOR_AGE_FEMALE
    L, M, S = _interpolate_lms(age_months, table)
    z = _z_score_from_lms(weight_kg, L, M, S)
    percentile = _percentile_from_z(z)
    
    return GrowthResult(
        value=weight_kg,
        percentile=round(percentile, 1),
        z_score=round(z, 2),
        interpretation=_interpret_percentile(percentile, "weight"),
    )


def calculate_height_percentile(
    height_cm: float,
    age_months: int,
    sex: Literal["male", "female"],
) -> GrowthResult:
    """
    Calculate height/length-for-age percentile.
    
    Args:
        height_cm: Height/length in centimeters
        age_months: Age in months (0-240)
        sex: "male" or "female"
    
    Returns:
        GrowthResult with percentile, z-score, and interpretation
    """
    table = HEIGHT_FOR_AGE_MALE if sex == "male" else HEIGHT_FOR_AGE_FEMALE
    L, M, S = _interpolate_lms(age_months, table)
    z = _z_score_from_lms(height_cm, L, M, S)
    percentile = _percentile_from_z(z)
    
    return GrowthResult(
        value=height_cm,
        percentile=round(percentile, 1),
        z_score=round(z, 2),
        interpretation=_interpret_percentile(percentile, "height"),
    )


def calculate_hc_percentile(
    hc_cm: float,
    age_months: int,
    sex: Literal["male", "female"],
) -> GrowthResult:
    """
    Calculate head circumference percentile (0-36 months only).
    
    Args:
        hc_cm: Head circumference in centimeters
        age_months: Age in months (0-36)
        sex: "male" or "female"
    
    Returns:
        GrowthResult with percentile, z-score, and interpretation
    """
    if age_months > 36:
        raise ValueError("Head circumference charts only available for ages 0-36 months")
    
    table = HC_FOR_AGE_MALE if sex == "male" else HC_FOR_AGE_FEMALE
    L, M, S = _interpolate_lms(age_months, table)
    z = _z_score_from_lms(hc_cm, L, M, S)
    percentile = _percentile_from_z(z)
    
    return GrowthResult(
        value=hc_cm,
        percentile=round(percentile, 1),
        z_score=round(z, 2),
        interpretation=_interpret_percentile(percentile, "head circumference"),
    )


def calculate_bmi_percentile(
    bmi: float,
    age_months: int,
    sex: Literal["male", "female"],
) -> GrowthResult:
    """
    Calculate BMI-for-age percentile (24+ months only).
    
    Args:
        bmi: Body mass index
        age_months: Age in months (24-240)
        sex: "male" or "female"
    
    Returns:
        GrowthResult with percentile, z-score, and interpretation
    """
    if age_months < 24:
        raise ValueError("BMI-for-age charts only available for ages 24+ months")
    
    table = BMI_FOR_AGE_MALE if sex == "male" else BMI_FOR_AGE_FEMALE
    L, M, S = _interpolate_lms(age_months, table)
    z = _z_score_from_lms(bmi, L, M, S)
    percentile = _percentile_from_z(z)
    
    # BMI interpretation is slightly different
    if percentile < 5:
        interpretation = "Underweight (<5th percentile)"
    elif percentile < 85:
        interpretation = "Healthy weight (5th-85th percentile)"
    elif percentile < 95:
        interpretation = "Overweight (85th-95th percentile)"
    else:
        interpretation = "Obese (≥95th percentile)"
    
    return GrowthResult(
        value=bmi,
        percentile=round(percentile, 1),
        z_score=round(z, 2),
        interpretation=interpretation,
    )


def calculate_bmi(weight_kg: float, height_cm: float) -> float:
    """Calculate BMI from weight and height."""
    height_m = height_cm / 100
    return round(weight_kg / (height_m * height_m), 1)


def generate_weight_at_percentile(
    percentile: float,
    age_months: int,
    sex: Literal["male", "female"],
) -> float:
    """
    Generate a weight value at a given percentile.
    
    Args:
        percentile: Target percentile (0-100)
        age_months: Age in months
        sex: "male" or "female"
    
    Returns:
        Weight in kg at that percentile
    """
    table = WEIGHT_FOR_AGE_MALE if sex == "male" else WEIGHT_FOR_AGE_FEMALE
    L, M, S = _interpolate_lms(age_months, table)
    z = _z_from_percentile(percentile)
    return round(_value_from_lms_z(z, L, M, S), 2)


def generate_height_at_percentile(
    percentile: float,
    age_months: int,
    sex: Literal["male", "female"],
) -> float:
    """
    Generate a height value at a given percentile.
    
    Args:
        percentile: Target percentile (0-100)
        age_months: Age in months
        sex: "male" or "female"
    
    Returns:
        Height in cm at that percentile
    """
    table = HEIGHT_FOR_AGE_MALE if sex == "male" else HEIGHT_FOR_AGE_FEMALE
    L, M, S = _interpolate_lms(age_months, table)
    z = _z_from_percentile(percentile)
    return round(_value_from_lms_z(z, L, M, S), 1)


def generate_hc_at_percentile(
    percentile: float,
    age_months: int,
    sex: Literal["male", "female"],
) -> float:
    """
    Generate a head circumference at a given percentile.
    
    Args:
        percentile: Target percentile (0-100)
        age_months: Age in months (0-36)
        sex: "male" or "female"
    
    Returns:
        Head circumference in cm at that percentile
    """
    if age_months > 36:
        raise ValueError("Head circumference charts only available for ages 0-36 months")
    
    table = HC_FOR_AGE_MALE if sex == "male" else HC_FOR_AGE_FEMALE
    L, M, S = _interpolate_lms(age_months, table)
    z = _z_from_percentile(percentile)
    return round(_value_from_lms_z(z, L, M, S), 1)


class GrowthTrajectory:
    """
    Generates a coherent growth trajectory for a patient.
    
    Uses a "percentile channel" approach where a patient generally
    tracks along the same percentile lines, with natural variation.
    """
    
    def __init__(
        self,
        sex: Literal["male", "female"],
        weight_percentile: float = 50,
        height_percentile: float = 50,
        hc_percentile: float = 50,
        variance: float = 0.3,  # Bug 7 fix: increased from 0.1 for more realistic variation
    ):
        """
        Initialize a growth trajectory.

        Args:
            sex: "male" or "female"
            weight_percentile: Starting weight percentile (0-100)
            height_percentile: Starting height percentile (0-100)
            hc_percentile: Starting HC percentile (0-100)
            variance: How much percentiles can drift between measurements (0-1)
        """
        self.sex = sex
        self.weight_percentile = weight_percentile
        self.height_percentile = height_percentile
        self.hc_percentile = hc_percentile
        self.variance = variance
        
        # Track history
        self._measurements: list[tuple[int, float, float, float | None]] = []
    
    def _drift_percentile(self, current: float, variance: float) -> float:
        """Apply random walk to a percentile (Bug 7 fix: increased drift)."""
        import random
        # Scale variance to percentile points - increased multiplier for more variation
        drift = random.gauss(0, variance * 15)
        new = current + drift
        # Keep within bounds, blend new (85%) with current (15%) for channel tracking
        return max(3, min(97, new * 0.85 + current * 0.15))
    
    def generate_measurement(
        self,
        age_months: int,
        include_hc: bool | None = None,
    ) -> tuple[float, float, float | None, float | None]:
        """
        Generate a measurement at a given age.
        
        Args:
            age_months: Age in months
            include_hc: Include head circumference (auto-determined if None)
        
        Returns:
            Tuple of (weight_kg, height_cm, hc_cm or None, bmi or None)
        """
        # Apply drift to percentiles
        self.weight_percentile = self._drift_percentile(
            self.weight_percentile, self.variance
        )
        self.height_percentile = self._drift_percentile(
            self.height_percentile, self.variance
        )
        
        # Generate measurements
        weight = generate_weight_at_percentile(
            self.weight_percentile, age_months, self.sex
        )
        height = generate_height_at_percentile(
            self.height_percentile, age_months, self.sex
        )
        
        # Head circumference (only for 0-36 months)
        hc = None
        if include_hc is None:
            include_hc = age_months <= 36
        if include_hc and age_months <= 36:
            self.hc_percentile = self._drift_percentile(
                self.hc_percentile, self.variance
            )
            hc = generate_hc_at_percentile(
                self.hc_percentile, age_months, self.sex
            )
        
        # BMI (only for 24+ months)
        bmi = None
        if age_months >= 24:
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


def generate_normal_vitals(age_months: int) -> dict[str, float]:
    """Generate normal vital signs for an age."""
    import random
    
    ranges = get_vital_ranges(age_months)
    vitals = {}
    
    for name, (low, high) in ranges.items():
        # Generate value in middle 80% of range
        margin = (high - low) * 0.1
        value = random.uniform(low + margin, high - margin)
        
        # Round appropriately
        if name in ("temperature_f", "o2_sat"):
            vitals[name] = round(value, 1)
        else:
            vitals[name] = round(value)
    
    return vitals
