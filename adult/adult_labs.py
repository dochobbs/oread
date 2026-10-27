"""
Adult lab, imaging and screening-result builders.

Panels (CMP, BMP, lipid, CBC, UA) are returned as a LabPanel whose results
are the individual components, each with its own LOINC code, unit and
reference range. Single analytes are a LabResult. Imaging reads are an
ImagingResult, never a numeric lab.

Every LOINC code below was confirmed against the NLM Clinical Tables LOINC
service (https://clinicaltables.nlm.nih.gov/api/loinc_items/v3/search) on
2026-10-27. Lab names with no verified code (spirometry, sleep study, RF,
anti-CCP ...) are not generated rather than given an invented code.

Reference intervals are typical adult intervals from Mayo Clinic
Laboratories' test catalog unless another source is cited on the line.
Real labs differ slightly; these are teaching values.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime

from src.models import (
    CodeableConcept,
    ImagingResult,
    Interpretation,
    LabPanel,
    LabResult,
    ReferenceRange,
    Sex,
)

LOINC = "http://loinc.org"


def _code(loinc: str, display: str) -> CodeableConcept:
    return CodeableConcept(system=LOINC, code=loinc, display=display)


@dataclass(frozen=True)
class Analyte:
    """One numeric lab component."""
    key: str
    loinc: str
    display: str
    unit: str
    low: float | None
    high: float | None
    decimals: int = 0
    # Sex-specific interval (low, high) overrides.
    female: tuple[float | None, float | None] | None = None


A = Analyte
ANALYTES: dict[str, Analyte] = {a.key: a for a in [
    A("glucose", "2345-7", "Glucose", "mg/dL", 70, 99),
    A("bun", "3094-0", "Urea nitrogen (BUN)", "mg/dL", 7, 20),
    A("creatinine", "2160-0", "Creatinine", "mg/dL", 0.74, 1.35, 2, female=(0.59, 1.04)),
    # KDIGO 2024: eGFR >= 60 is G1-G2; < 60 for > 3 months defines CKD.
    A("egfr", "98979-8", "eGFR (CKD-EPI 2021)", "mL/min/1.73m2", 60, None),
    A("sodium", "2951-2", "Sodium", "mmol/L", 135, 145),
    A("potassium", "2823-3", "Potassium", "mmol/L", 3.6, 5.2, 1),
    A("chloride", "2075-0", "Chloride", "mmol/L", 98, 107),
    A("co2", "2028-9", "Carbon dioxide, total", "mmol/L", 22, 29),
    A("calcium", "17861-6", "Calcium", "mg/dL", 8.6, 10.3, 1),
    A("protein", "2885-2", "Protein, total", "g/dL", 6.3, 7.9, 1),
    A("albumin", "1751-7", "Albumin", "g/dL", 3.5, 5.0, 1),
    A("bilirubin", "1975-2", "Bilirubin, total", "mg/dL", 0.0, 1.2, 1),
    A("alk_phos", "6768-6", "Alkaline phosphatase", "U/L", 40, 129, female=(35, 104)),
    A("ast", "1920-8", "AST", "U/L", 8, 48),
    A("alt", "1742-6", "ALT", "U/L", 7, 55),
    A("total_chol", "2093-3", "Cholesterol, total", "mg/dL", None, 199),
    A("triglycerides", "2571-8", "Triglycerides", "mg/dL", None, 149),
    # NCEP ATP III: low HDL < 40 mg/dL (men), < 50 mg/dL (women).
    A("hdl", "2085-9", "HDL cholesterol", "mg/dL", 40, None, female=(50, None)),
    # NCEP ATP III: LDL < 100 mg/dL optimal; calculated by Friedewald.
    A("ldl", "13457-7", "LDL cholesterol (calculated)", "mg/dL", None, 99),
    A("wbc", "6690-2", "WBC", "10*3/uL", 3.4, 9.6, 1),
    A("rbc", "789-8", "RBC", "10*6/uL", 4.35, 5.65, 2, female=(3.92, 5.13)),
    # WHO anemia definition: Hgb < 13 g/dL (men), < 12 g/dL (non-pregnant women).
    A("hgb", "718-7", "Hemoglobin", "g/dL", 13.2, 16.6, 1, female=(11.6, 15.0)),
    A("hct", "4544-3", "Hematocrit", "%", 38.3, 48.6, 1, female=(35.5, 44.9)),
    A("mcv", "787-2", "MCV", "fL", 78.2, 97.9, 1),
    A("plt", "777-3", "Platelets", "10*3/uL", 135, 317, female=(157, 371)),
    # ADA Standards of Care in Diabetes 2026, section 2: < 5.7% normal,
    # 5.7-6.4% prediabetes, >= 6.5% diabetes.
    A("hba1c", "4548-4", "Hemoglobin A1c", "%", None, 5.6, 1),
    A("tsh", "3016-3", "TSH", "mIU/L", 0.3, 4.2, 2),
    A("free_t4", "3024-7", "Free T4", "ng/dL", 0.9, 1.7, 2),
    # ACC/AHA/HFSA 2022 HF guideline: BNP >= 35 pg/mL (ambulatory) supports HF;
    # most labs flag > 100 pg/mL.
    A("bnp", "30934-4", "BNP", "pg/mL", None, 100),
    A("troponin", "10839-9", "Troponin I, cardiac", "ng/mL", None, 0.04, 2),
    A("inr", "6301-6", "INR", "", 0.9, 1.1, 1),
    # KDIGO 2024: urine ACR < 30 mg/g is A1 (normal to mildly increased).
    A("uacr", "14959-1", "Microalbumin/creatinine ratio, urine", "mg/g", None, 29),
    # Endocrine Society 2011 vitamin D guideline: < 20 ng/mL deficient.
    A("vitamin_d", "62292-8", "25-Hydroxyvitamin D (D2+D3)", "ng/mL", 20, 50),
    A("b12", "2132-9", "Vitamin B12", "pg/mL", 180, 914),
    A("psa", "2857-1", "PSA", "ng/mL", None, 4.0, 1),
    A("uric_acid", "3084-1", "Uric acid", "mg/dL", 3.7, 8.0, 1, female=(2.7, 6.1)),
    # Therapeutic maintenance range 0.6-1.2 mmol/L (CANMAT/ISBD 2018).
    A("lithium", "14334-7", "Lithium", "mmol/L", 0.6, 1.2, 2),
    A("ferritin", "2276-4", "Ferritin", "ng/mL", 24, 336, female=(11, 307)),
    # ADA 2026 section 2: fasting glucose 100-125 mg/dL is prediabetes.
    A("fasting_glucose", "1558-6", "Glucose, fasting", "mg/dL", 70, 99),
    A("crp", "1988-5", "C-reactive protein", "mg/L", None, 8.0, 1),
    A("esr", "30341-2", "Sedimentation rate", "mm/h", 0, 22, female=(0, 29)),
    A("phq9", "44261-6", "PHQ-9 total score", "{score}", None, 4),
]}
del A

PANELS: dict[str, tuple[str, str, list[str]]] = {
    "cmp": ("24323-8", "Comprehensive metabolic panel",
            ["glucose", "bun", "creatinine", "egfr", "sodium", "potassium", "chloride", "co2",
             "calcium", "protein", "albumin", "bilirubin", "alk_phos", "ast", "alt"]),
    "bmp": ("24321-2", "Basic metabolic panel",
            ["glucose", "bun", "creatinine", "egfr", "sodium", "potassium", "chloride", "co2",
             "calcium"]),
    "lipid_panel": ("24331-1", "Lipid panel", ["total_chol", "triglycerides", "hdl", "ldl"]),
    "cbc": ("58410-2", "CBC panel", ["wbc", "rbc", "hgb", "hct", "mcv", "plt"]),
}

# Condition-lab names in adult_conditions.yaml that map to one analyte.
SINGLES: dict[str, str] = {
    "hba1c": "hba1c", "tsh": "tsh", "free_t4": "free_t4", "bnp": "bnp", "troponin": "troponin",
    "inr": "inr", "urine_microalbumin": "uacr", "vitamin_d": "vitamin_d", "b12": "b12",
    "psa": "psa", "uric_acid": "uric_acid", "lithium_level": "lithium", "ferritin": "ferritin",
    "fasting_glucose": "fasting_glucose", "crp": "crp", "esr": "esr", "ast": "ast",
    "alt": "alt", "gfr": "egfr", "calcium": "calcium", "phq9": "phq9",
}

# Qualitative tests: key -> (loinc, display, positive text, negative text).
QUALITATIVE: dict[str, tuple[str, str, str, str]] = {
    "rapid_strep": ("78012-2", "Strep A antigen, rapid", "Positive", "Negative"),
    "rapid_flu": ("80382-5", "Influenza A antigen, rapid", "Positive", "Negative"),
    "rapid_covid": ("95209-3", "SARS-CoV-2 antigen, rapid", "Positive", "Negative"),
    "hcv_ab": ("13955-0", "Hepatitis C virus antibody", "Reactive", "Nonreactive"),
    "hiv_screen": ("56888-1", "HIV-1/2 Ag/Ab screen", "Reactive", "Nonreactive"),
    "pap": ("10524-7", "Cervical cytology",
            "Low-grade squamous intraepithelial lesion (LSIL)",
            "Negative for intraepithelial lesion or malignancy (NILM)"),
    "hpv": ("30167-1", "High-risk HPV DNA, cervix", "Detected", "Not detected"),
}

# Urinalysis dipstick components: key -> (loinc, display).
UA_COMPONENTS = [
    ("5811-5", "Specific gravity, urine"), ("5803-2", "pH, urine"),
    ("5792-7", "Glucose, urine"), ("20454-5", "Protein, urine"),
    ("5794-3", "Blood, urine"), ("5799-2", "Leukocyte esterase, urine"),
    ("5802-4", "Nitrite, urine"),
]

# Imaging studies: key -> (loinc, display, modality).
IMAGING: dict[str, tuple[str, str, str]] = {
    "chest_xray": ("36643-5", "XR Chest 2 views", "XR"),
    "dexa_scan": ("38269-7", "DXA bone density", "DXA"),
    "mammogram": ("24606-6", "MG Breast screening", "MG"),
    "ldct": ("79086-5", "CT Chest for lung cancer screening WO contrast", "CT"),
}

# Minimum months between repeat monitoring draws of the same test.
# Sources: ADA Standards of Care 2026 sec. 6 (A1c twice yearly at goal,
# quarterly if not); ATA 2014 hypothyroidism guideline (TSH annually once
# stable); ACC/AHA 2018 cholesterol guideline (lipids 3-12 months after
# therapy changes, then periodically); KDIGO 2024 (eGFR/ACR at least
# yearly, more often in G3); CANMAT/ISBD 2018 (lithium every 3-6 months).
LAB_MIN_INTERVAL_MONTHS: dict[str, int] = {
    "hba1c": 3, "cmp": 6, "bmp": 6, "lipid_panel": 12, "cbc": 12, "tsh": 12, "free_t4": 12,
    "urine_microalbumin": 12, "inr": 1, "vitamin_d": 12, "b12": 12, "psa": 12, "uric_acid": 6,
    "lithium_level": 6, "ferritin": 12, "crp": 3, "esr": 3, "bnp": 12, "ast": 6, "alt": 6,
    "fasting_glucose": 12, "gfr": 6, "urinalysis": 12, "calcium": 12,
}


# A draw of any panel on the right also reports the test on the left.
COVERED_BY: dict[str, list[str]] = {
    "bmp": ["cmp"], "gfr": ["cmp", "bmp"], "calcium": ["cmp", "bmp"],
    "ast": ["cmp"], "alt": ["cmp"],
}


def is_known_lab(name: str) -> bool:
    n = name.lower()
    return n in PANELS or n in SINGLES or n in QUALITATIVE or n == "urinalysis"


def is_imaging(name: str) -> bool:
    return name.lower() in IMAGING


# --------------------------------------------------------------------------- #
# Patient context and value generation                                        #
# --------------------------------------------------------------------------- #

@dataclass
class LabContext:
    """What the value generator needs to know about the patient on that day."""
    sex: Sex
    age: int
    conditions: set[str]
    drug_classes: set[str]
    drugs: set[str] = frozenset()
    weight_kg: float | None = None


def ckd_epi_2021(scr: float, age: int, female: bool) -> float:
    """eGFR by the race-free CKD-EPI 2021 creatinine equation.

    Source: Inker LA et al. N Engl J Med 2021;385:1737-1749.
    """
    kappa = 0.7 if female else 0.9
    alpha = -0.241 if female else -0.302
    egfr = (142 * min(scr / kappa, 1) ** alpha * max(scr / kappa, 1) ** -1.200
            * 0.9938 ** age * (1.012 if female else 1.0))
    return egfr


def _creatinine_for_egfr(target: float, age: int, female: bool) -> float:
    """Invert CKD-EPI 2021 by bisection (eGFR falls monotonically with Scr)."""
    lo, hi = 0.2, 15.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if ckd_epi_2021(mid, age, female) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


class LabValues:
    """Generates one internally consistent set of values for a lab draw."""

    def __init__(self, ctx: LabContext, rng: random.Random):
        self.ctx = ctx
        self.rng = rng
        self._cache: dict[str, float] = {}

    def _u(self, lo: float, hi: float) -> float:
        return self.rng.uniform(lo, hi)

    def get(self, key: str) -> float:
        if key not in self._cache:
            self._cache[key] = self._make(key)
        return self._cache[key]

    def _make(self, key: str) -> float:  # noqa: C901 - one branch per analyte
        c, d = self.ctx.conditions, self.ctx.drug_classes
        female = self.ctx.sex == Sex.FEMALE
        u = self._u
        if key == "egfr":
            return ckd_epi_2021(self.get("creatinine"), self.ctx.age, female)
        if key == "creatinine":
            if "ckd_stage3" in c:
                # KDIGO G3a 45-59, G3b 30-44 mL/min/1.73m2.
                target = u(30, 59)
            else:
                # Normal kidney function for age; eGFR declines ~1/yr after 40.
                target = u(max(62, 115 - 0.8 * max(0, self.ctx.age - 30)),
                           max(75, 125 - 0.8 * max(0, self.ctx.age - 30)))
            return _creatinine_for_egfr(target, self.ctx.age, female)
        if key in ("glucose", "fasting_glucose"):
            if "type2_diabetes" in c:
                return u(115, 185)
            if "prediabetes" in c:
                return u(100, 125)
            return u(72, 98)
        if key == "hba1c":
            if "type2_diabetes" in c:
                treated = bool(d & {"biguanide", "incretin", "sglt2_inhibitor", "basal_insulin",
                                    "sulfonylurea", "tzd"})
                return u(6.3, 8.6) if treated else u(6.6, 9.8)
            if "prediabetes" in c:
                return u(5.7, 6.4)
            return u(4.8, 5.6)
        if key == "bun":
            return u(22, 40) if "ckd_stage3" in c else u(9, 20)
        if key == "potassium":
            hi = 5.4 if ("ckd_stage3" in c or "mra" in d or "raas_inhibitor" in d) else 4.9
            return u(3.7, hi)
        if key == "sodium":
            return u(130, 138) if ("cirrhosis" in c or "heart_failure" in c) else u(137, 143)
        if key == "chloride":
            return u(99, 106)
        if key == "co2":
            return u(19, 24) if "ckd_stage3" in c else u(23, 28)
        if key == "calcium":
            return u(8.8, 10.1)
        if key == "protein":
            return u(6.4, 7.8)
        if key == "albumin":
            return u(2.7, 3.4) if "cirrhosis" in c else u(3.8, 4.7)
        if key == "bilirubin":
            return u(1.4, 2.8) if "cirrhosis" in c else u(0.3, 1.0)
        if key == "alk_phos":
            return u(45, 110)
        if key == "ast":
            if "cirrhosis" in c or "alcohol_use_disorder" in c:
                return u(45, 110)  # AST:ALT > 2 typical of alcohol-associated disease
            if "fatty_liver" in c or "hepatitis_c" in c:
                return u(35, 75)
            return u(14, 35)
        if key == "alt":
            if "cirrhosis" in c or "alcohol_use_disorder" in c:
                return u(25, 50)
            if "fatty_liver" in c or "hepatitis_c" in c:
                return u(50, 110)
            return u(10, 40)
        if key == "hdl":
            metabolic = bool(c & {"type2_diabetes", "obesity", "prediabetes"})
            if female:
                return u(40, 60) if metabolic else u(50, 80)
            return u(32, 45) if metabolic else u(40, 65)
        if key == "triglycerides":
            return u(150, 320) if c & {"type2_diabetes", "obesity", "fatty_liver"} else u(60, 145)
        if key == "ldl":
            on_lipid_tx = bool(d & {"statin", "ezetimibe"})
            hld = bool(c & {"hyperlipidemia", "coronary_artery_disease",
                            "peripheral_artery_disease"})
            if on_lipid_tx:
                return u(55, 110)
            return u(130, 195) if hld else u(70, 129)
        if key == "total_chol":
            # Friedewald: LDL = TC - HDL - TG/5, so TC is derived for consistency.
            return self.get("ldl") + self.get("hdl") + self.get("triglycerides") / 5
        if key == "wbc":
            return u(4.2, 9.5)
        if key in ("hgb", "hct", "rbc", "mcv"):
            anemic = bool(c & {"anemia", "ckd_stage3"})
            hgb = self._cache.get("hgb")
            if hgb is None:
                if "anemia" in c:
                    hgb = u(9.4, 11.4) if female else u(10.2, 12.4)
                elif anemic:
                    hgb = u(10.8, 12.4) if female else u(11.6, 13.0)
                else:
                    hgb = u(12.3, 14.8) if female else u(13.6, 16.4)
                self._cache["hgb"] = hgb
            if key == "hgb":
                return hgb
            if key == "hct":
                return hgb * 3.0
            if key == "mcv":
                return u(72, 79) if "anemia" in c else u(83, 96)
            return hgb * 3.0 / self.get("mcv") * 10  # RBC (10*6/uL) = Hct(%) / MCV(fL) x 10
        if key == "plt":
            return u(70, 130) if "cirrhosis" in c else u(165, 340)
        if key == "tsh":
            if "hypothyroidism" in c:
                return u(4.5, 9.0) if self.rng.random() < 0.15 else u(0.6, 4.0)
            if "hyperthyroidism" in c:
                return u(0.01, 0.25) if "antithyroid" not in d else u(0.1, 2.5)
            return u(0.6, 3.8)
        if key == "free_t4":
            return u(1.9, 3.2) if ("hyperthyroidism" in c and "antithyroid" not in d) else u(0.9, 1.6)
        if key == "bnp":
            return u(150, 900) if "heart_failure" in c else u(10, 80)
        if key == "troponin":
            return u(0.0, 0.03)
        if key == "inr":
            # Warfarin target INR 2.0-3.0 (CHEST 2012 / ACC/AHA AF guideline).
            return u(1.8, 3.2) if "warfarin" in self.ctx.drugs else (
                u(1.3, 1.8) if "cirrhosis" in c else u(0.9, 1.1))
        if key == "uacr":
            return u(15, 250) if c & {"type2_diabetes", "ckd_stage3"} else u(2, 25)
        if key == "vitamin_d":
            return u(9, 19) if "vitamin_d_deficiency" in c else u(22, 48)
        if key == "b12":
            return u(150, 230) if "biguanide" in d and self.rng.random() < 0.2 else u(280, 850)
        if key == "psa":
            return u(1.5, 6.0) if "bph" in c else u(0.3, 2.5)
        if key == "uric_acid":
            return u(5.0, 9.5) if "gout" in c else (u(3.0, 5.8) if female else u(4.0, 7.2))
        if key == "lithium":
            return u(0.6, 1.1)
        if key == "ferritin":
            return u(5, 30) if c & {"anemia", "restless_leg_syndrome"} else u(40, 250)
        if key == "crp":
            return u(6, 35) if c & {"rheumatoid_arthritis", "crohns_disease", "ulcerative_colitis",
                                    "lupus"} else u(0.3, 5)
        if key == "esr":
            return u(20, 55) if c & {"rheumatoid_arthritis", "lupus", "crohns_disease"} else u(2, 18)
        if key == "phq9":
            return float(self.rng.randint(5, 14) if c & {"depression", "anxiety", "bipolar_disorder",
                                                          "ptsd"} else self.rng.randint(0, 4))
        raise KeyError(key)


def _interpret(value: float, low: float | None, high: float | None) -> Interpretation:
    if low is not None and value < low:
        return Interpretation.LOW
    if high is not None and value > high:
        return Interpretation.HIGH
    return Interpretation.NORMAL


def _analyte_result(key: str, vals: LabValues, when: datetime) -> LabResult:
    a = ANALYTES[key]
    low, high = (a.female if (a.female and vals.ctx.sex == Sex.FEMALE) else (a.low, a.high))
    value = round(vals.get(key), a.decimals)
    if a.decimals == 0:
        value = int(value)
    return LabResult(
        code=_code(a.loinc, a.display),
        display_name=a.display,
        value=value,
        unit=a.unit,
        reference_range=ReferenceRange(low=low, high=high, unit=a.unit,
                                       sex=vals.ctx.sex if a.female else None),
        interpretation=_interpret(value, low, high),
        collected_date=when,
        resulted_date=when,
    )


def _qualitative(key: str, positive: bool, when: datetime) -> LabResult:
    loinc, display, pos, neg = QUALITATIVE[key]
    return LabResult(
        code=_code(loinc, display),
        display_name=display,
        value=pos if positive else neg,
        reference_range=ReferenceRange(text=neg),
        interpretation=Interpretation.POSITIVE if positive else Interpretation.NEGATIVE,
        collected_date=when,
        resulted_date=when,
    )


def _urinalysis(abnormal: bool, ctx: LabContext, rng: random.Random, when: datetime) -> LabPanel:
    glycosuria = "type2_diabetes" in ctx.conditions and "sglt2_inhibitor" in ctx.drug_classes
    texts = {
        "5811-5": f"{rng.choice([1.010, 1.015, 1.020, 1.025]):.3f}",
        "5803-2": f"{rng.choice([5.5, 6.0, 6.5, 7.0])}",
        "5792-7": "3+" if glycosuria else "Negative",
        "20454-5": "1+" if ("ckd_stage3" in ctx.conditions and rng.random() < 0.5) else "Negative",
        "5794-3": "Trace" if abnormal else "Negative",
        "5799-2": "Moderate" if abnormal else "Negative",
        "5802-4": "Positive" if abnormal else "Negative",
    }
    results = []
    for loinc, display in UA_COMPONENTS:
        v = texts[loinc]
        normal_ref = {"5811-5": "1.005-1.030", "5803-2": "5.0-8.0"}.get(loinc, "Negative")
        if loinc in ("5811-5", "5803-2"):
            interp = Interpretation.NORMAL
        else:
            interp = Interpretation.NORMAL if v == "Negative" else Interpretation.ABNORMAL
        results.append(LabResult(
            code=_code(loinc, display), display_name=display, value=v,
            reference_range=ReferenceRange(text=normal_ref), interpretation=interp,
            collected_date=when, resulted_date=when,
        ))
    return LabPanel(code=_code("24356-8", "Urinalysis"), display_name="Urinalysis",
                    results=results, collected_date=when, resulted_date=when)


def build_lab(name: str, ctx: LabContext, on: date, rng: random.Random,
              values: LabValues | None = None, acute_condition: str | None = None
              ) -> LabPanel | LabResult | None:
    """Build one lab order's results, or None if the lab has no verified code.

    Pass the same ``values`` for every lab drawn at one visit so shared
    analytes (glucose in a CMP and a fasting glucose, creatinine and eGFR)
    agree with each other.
    """
    n = name.lower()
    when = datetime.combine(on, datetime.min.time()).replace(hour=9)
    vals = values or LabValues(ctx, rng)
    if n in PANELS:
        loinc, display, comps = PANELS[n]
        return LabPanel(
            code=_code(loinc, display), display_name=display,
            results=[_analyte_result(k, vals, when) for k in comps],
            collected_date=when, resulted_date=when,
        )
    if n in SINGLES:
        return _analyte_result(SINGLES[n], vals, when)
    if n == "urinalysis":
        return _urinalysis(acute_condition == "urinary_tract_infection", ctx, rng, when)
    if n in QUALITATIVE:
        positive = {
            "rapid_flu": acute_condition == "influenza",
            "rapid_covid": acute_condition == "covid19",
            # Centor-positive adults with pharyngitis: ~10-20% GAS (IDSA 2012).
            "rapid_strep": acute_condition in ("acute_pharyngitis", "strep_pharyngitis")
            and rng.random() < 0.2,
            "hcv_ab": "hepatitis_c" in ctx.conditions,
            "hiv_screen": "hiv" in ctx.conditions,
        }.get(n, False)
        return _qualitative(n, positive, when)
    return None


def build_imaging(name: str, ctx: LabContext, on: date, rng: random.Random,
                  acute_condition: str | None = None) -> ImagingResult | None:
    """Build an imaging report. Normal reads are reported as normal findings."""
    n = name.lower()
    if n not in IMAGING:
        return None
    loinc, display, modality = IMAGING[n]
    when = datetime.combine(on, datetime.min.time()).replace(hour=10)
    if n == "chest_xray":
        if acute_condition == "pneumonia":
            findings = "Patchy airspace opacity in the right lower lobe. No effusion."
            impression = "Right lower lobe pneumonia."
        elif "heart_failure" in ctx.conditions and rng.random() < 0.3:
            findings = "Mild cardiomegaly. No focal consolidation or effusion."
            impression = "Cardiomegaly without acute cardiopulmonary disease."
        else:
            findings = "Lungs clear. Normal cardiomediastinal silhouette. No effusion."
            impression = "No acute cardiopulmonary disease."
    elif n == "dexa_scan":
        # WHO T-score categories: >= -1.0 normal, -1.0 to -2.5 osteopenia,
        # <= -2.5 osteoporosis.
        if "osteoporosis" in ctx.conditions:
            t = round(rng.uniform(-3.4, -2.5), 1)
            impression = "Osteoporosis (WHO criteria)."
        elif rng.random() < 0.4:
            t = round(rng.uniform(-2.4, -1.1), 1)
            impression = "Low bone mass (osteopenia)."
        else:
            t = round(rng.uniform(-0.9, 0.8), 1)
            impression = "Normal bone density."
        findings = f"Lowest T-score {t} (femoral neck)."
    elif n == "mammogram":
        if rng.random() < 0.85:
            findings = "Scattered fibroglandular densities. No suspicious mass or calcification."
            impression = "BI-RADS 1: Negative. Routine screening recommended."
        else:
            findings = "Stable benign-appearing calcifications."
            impression = "BI-RADS 2: Benign. Routine screening recommended."
    else:  # ldct
        if rng.random() < 0.85:
            findings = "No pulmonary nodules. Mild centrilobular emphysema."
            impression = "Lung-RADS 1: Negative. Continue annual screening."
        else:
            findings = "Solid 4 mm nodule, right upper lobe."
            impression = "Lung-RADS 2: Benign appearance. Continue annual screening."
    return ImagingResult(
        code=_code(loinc, display), display_name=display, modality=modality,
        findings=findings, impression=impression, performed_date=when, reported_date=when,
    )
