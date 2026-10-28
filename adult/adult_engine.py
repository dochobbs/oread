"""
Adult Patient Generation Engine.

Generates synthetic adult patient records (18+ years).
Key differences from pediatric engine:
- Life stages: young adult → adult → middle age → older adult
- Preventive care: USPSTF screenings instead of well-child visits
- No growth percentiles; weight trends instead
- Chronic disease accumulation (HTN, DM2, HLD)
- Employment/insurance/social complexity
- Polypharmacy common in older adults

Messiness Levels (matches PedsEngine):
  0 - Pristine: Teaching ideal
  1 - Real World: Minor inconsistencies
  2 - Busy Clinic: Copy-forward artifacts
  3 - Needs Reconciliation: Conflicts requiring judgment
  4 - Safety Landmines: Hidden dangers
  5 - Chart From Hell: Threading errors, near-misses
"""

from __future__ import annotations

import random
import re
import yaml
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from dateutil.relativedelta import relativedelta
from pydantic import BaseModel

from adult import adult_labs, adult_meds
from adult.adult_labs import LabContext, LabValues
from src.models import (
    ArcStage,
    Assessment,
    CodeableConcept,
    ComplexityTier,
    Condition,
    ConditionStatus,
    Demographics,
    DiseaseArc,
    Encounter,
    EncounterType,
    GenerationSeed,
    Immunization,
    Medication,
    Order,
    OrderStatus,
    Patient,
    PatientTimeline,
    PhysicalExam,
    PlanItem,
    Procedure,
    Provider,
    Location,
    Sex,
    SocialHistory,
    SubstanceUse,
    HouseholdMember,
    Address,
    Contact,
    TimeSnapshot,
    VitalSigns,
)
from src.engines.messiness import MessinessInjector
from src.llm import LLMClient


class UnknownConditionError(ValueError):
    """A requested condition key is not in the adult registry."""


class ConditionGateError(ValueError):
    """A requested condition is impossible for the patient's sex or age."""


# Common names and peds-engine keys -> adult registry keys. Keys are
# normalized (lowercase, spaces and hyphens to underscores).
CONDITION_ALIASES: dict[str, str] = {
    "ckd": "ckd_stage3", "chronic_kidney_disease": "ckd_stage3", "ckd_3": "ckd_stage3",
    "ckd_stage_3": "ckd_stage3",
    "type_2_diabetes": "type2_diabetes", "t2dm": "type2_diabetes", "t2d": "type2_diabetes",
    "dm2": "type2_diabetes", "diabetes": "type2_diabetes", "diabetes_mellitus": "type2_diabetes",
    "type_2_diabetes_mellitus": "type2_diabetes", "diabetes_type_2": "type2_diabetes",
    "htn": "hypertension", "high_blood_pressure": "hypertension",
    "hld": "hyperlipidemia", "high_cholesterol": "hyperlipidemia", "dyslipidemia": "hyperlipidemia",
    "cad": "coronary_artery_disease", "ischemic_heart_disease": "coronary_artery_disease",
    "afib": "atrial_fibrillation", "a_fib": "atrial_fibrillation",
    "chf": "heart_failure", "congestive_heart_failure": "heart_failure",
    "pad": "peripheral_artery_disease",
    "osa": "sleep_apnea", "obstructive_sleep_apnea": "sleep_apnea",
    "anxiety_disorder": "anxiety", "gad": "anxiety", "generalized_anxiety_disorder": "anxiety",
    "major_depressive_disorder": "depression", "mdd": "depression",
    "ra": "rheumatoid_arthritis", "oa": "osteoarthritis",
    "uc": "ulcerative_colitis", "crohns": "crohns_disease", "crohn_disease": "crohns_disease",
    "nafld": "fatty_liver", "masld": "fatty_liver", "fatty_liver_disease": "fatty_liver",
    "hcv": "hepatitis_c", "hbv": "hepatitis_b",
    "bipolar": "bipolar_disorder", "oud": "opioid_use_disorder", "aud": "alcohol_use_disorder",
    "ms": "multiple_sclerosis", "sle": "lupus",
    "ed": "erectile_dysfunction", "polycystic_ovary_syndrome": "pcos",
    "hypothyroid": "hypothyroidism", "hyperthyroid": "hyperthyroidism",
    "atopic_dermatitis": "eczema", "migraines": "migraine", "seizure_disorder": "epilepsy",
    "parkinson": "parkinsons", "parkinson_disease": "parkinsons", "parkinsons_disease": "parkinsons",
    "chronic_obstructive_pulmonary_disease": "copd", "benign_prostatic_hyperplasia": "bph",
    "allergic_rhinitis_seasonal": "allergic_rhinitis",
}


def normalize_condition_name(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().lower().replace("'", ""))


# Age phrases in free-text descriptions. Numeric forms win over word cues.
_AGE_PATTERNS = [
    re.compile(r"\b(\d{1,3})\s*[- ]?\s*(?:years?|yrs?)\s*[- ]?\s*old\b", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:y/o|yo|y\.o\.|yr old)\b", re.I),
    re.compile(r"\b(?:age|aged)\s*(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{2,3})\s*(?:m|f|male|female)\b(?!\w)", re.I),
]
_MONTH_PATTERN = re.compile(r"\b(\d{1,3})\s*[- ]?\s*months?\s*[- ]?\s*old\b", re.I)
# Word cue -> (min, max) age in years.
_ADULT_AGE_WORDS: list[tuple[str, tuple[int, int] | None]] = [
    (r"nonagenarian", (90, 99)), (r"octogenarian", (80, 89)), (r"septuagenarian", (70, 79)),
    (r"sexagenarian", (60, 69)), (r"elderly|geriatric|frail older", (70, 90)),
    (r"older adult|senior citizen|older (?:man|woman)", (65, 85)), (r"retired|retiree", (62, 85)),
    (r"middle[- ]aged", (45, 64)), (r"young adult", (22, 35)), (r"\badult\b", None),
]
_PEDS_WORDS = re.compile(
    r"\b(?:infant|newborn|neonate|baby|toddler|child|kid|preschool|school[- ]age|adolescent|"
    r"teen|teenager|pediatric|paediatric)\b", re.I)


def infer_age_from_description(description: str | None,
                               rng: random.Random | None = None) -> tuple[bool, int | None]:
    """Read an age from free text.

    Returns:
        (is_adult, age_years). ``is_adult`` is True when the text names an
        age of 22+ (the peds engine covers 0-21) or uses an adult-only cue
        ("elderly", "middle-aged", "adult"). ``age_years`` is the stated age,
        a sampled age for a word cue (when ``rng`` is given), or None.
    """
    if not description:
        return False, None
    text = description.lower()
    m = _MONTH_PATTERN.search(text)
    if m:
        return False, int(m.group(1)) // 12
    for pat in _AGE_PATTERNS:
        m = pat.search(text)
        if m:
            age = int(m.group(1))
            if 0 <= age <= 120:
                return age >= 22, age
    if _PEDS_WORDS.search(text):
        return False, None
    for words, band in _ADULT_AGE_WORDS:
        if re.search(words, text):
            if band is None or rng is None:
                return True, None
            return True, rng.randint(*band)
    return False, None


def infer_conditions_from_description(description: str) -> list[str]:
    """Registry keys for chronic conditions named in free text."""
    registry = ConditionRegistry.get()
    text = " " + re.sub(r"[\-_/]", " ", description.lower()) + " "
    found: list[str] = []

    def hit(phrase: str) -> bool:
        phrase = phrase.replace("_", " ").strip()
        return bool(phrase) and re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", text) is not None

    type1 = re.search(r"type\s*(?:1|i)\s*diabetes", text) is not None
    for key in registry.all_chronic_conditions():
        cond = registry.get_condition(key) or {}
        phrases = [key, cond.get("display", "")]
        # Two-letter aliases (ed, ms, ra, uc) collide with ordinary words.
        phrases += [a for a, k in CONDITION_ALIASES.items() if k == key and len(a) > 2]
        for p in phrases:
            if p == "diabetes" and type1:
                continue
            if hit(p.lower()):
                found.append(key)
                break
    return found


# =============================================================================
# Condition Registry
# =============================================================================

def sex_weight(cond: dict, sex: Sex) -> float:
    """Selection weight for a sex-predominant condition (never zero)."""
    pred = cond.get("sex_predominance")
    if not pred:
        return 1.0
    return 1.0 if pred == sex.value else 0.5


class ConditionRegistry:
    """Loads and provides access to condition definitions."""

    _instance = None
    _data = None
    
    # Categories that contain chronic conditions
    CHRONIC_CATEGORIES = [
        "cardiovascular", "metabolic", "respiratory", "mental_health",
        "musculoskeletal", "gastrointestinal", "renal", "neurologic",
        "genitourinary", "dermatologic", "infectious", "other_chronic",
        "chronic_additional"
    ]
    
    # Categories that contain acute conditions
    ACUTE_CATEGORIES = [
        "acute_respiratory", "acute_gi", "acute_gu", "acute_skin",
        "acute_msk", "acute_neuro", "acute_eye_ear", "acute_other",
        "acute_dental", "acute_sti", "acute_pain_syndromes",
        "acute_skin_additional", "acute_gi_additional", "acute_gu_additional",
        "acute_neuro_additional", "acute_eye_ear_additional", "acute_misc"
    ]
    
    @classmethod
    def get(cls) -> "ConditionRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reload(cls):
        """Force reload of condition data."""
        cls._data = None
        cls._instance = None
    
    def __init__(self):
        if ConditionRegistry._data is None:
            self._load()
    
    def _load(self):
        """Load conditions from YAML."""
        yaml_path = Path(__file__).parent / "adult_conditions.yaml"
        if yaml_path.exists():
            with open(yaml_path) as f:
                ConditionRegistry._data = yaml.safe_load(f)
        else:
            ConditionRegistry._data = {}
    
    def get_condition(self, name: str) -> dict | None:
        """Get condition by name, searching all categories."""
        name_lower = name.lower().replace(" ", "_").replace("-", "_")
        
        for category, conditions in ConditionRegistry._data.items():
            if isinstance(conditions, dict):
                if name_lower in conditions:
                    return conditions[name_lower]
                # Also check display names
                for cond_id, cond_data in conditions.items():
                    if isinstance(cond_data, dict) and cond_data.get("display", "").lower() == name_lower:
                        return cond_data
        return None
    
    def resolve_key(self, name: str) -> str:
        """Map a requested condition name to its chronic registry key.

        Accepts registry keys, aliases (``ckd`` -> ``ckd_stage3``) and display
        names. Never falls back to a placeholder code.

        Raises:
            UnknownConditionError: if the name matches nothing.
        """
        norm = normalize_condition_name(name)
        norm = CONDITION_ALIASES.get(norm, norm)
        chronic = self.all_chronic_conditions()
        if norm in chronic:
            return norm
        for key in chronic:
            cond = self.get_condition(key) or {}
            if normalize_condition_name(cond.get("display", "")) == norm:
                return key
        close = [k for k in chronic if norm[:4] and norm[:4] in k][:5]
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise UnknownConditionError(f"Unknown adult condition '{name}'.{hint}")

    def get_icd10(self, name: str) -> tuple[str, str]:
        """Get ICD-10 code and display for a condition.

        Raises:
            UnknownConditionError: if the condition (or its code) is missing.
        """
        key = self.resolve_key(name)
        cond = self.get_condition(key) or {}
        if not cond.get("icd10"):
            raise UnknownConditionError(f"Adult condition '{key}' has no ICD-10 code")
        return str(cond["icd10"]), cond.get("display", key.replace("_", " ").title())

    def get_min_age(self, name: str) -> int:
        """Hard minimum age in years for the diagnosis (default 18)."""
        cond = self.get_condition(name) or {}
        return int(cond.get("min_age", 18))

    def get_sex_predominance(self, name: str) -> str | None:
        cond = self.get_condition(name) or {}
        return cond.get("sex_predominance")

    def sex_allowed(self, name: str, sex: Sex) -> bool:
        """False only when the condition is anatomically impossible for sex."""
        restriction = self.get_sex_specific(name)
        if restriction == "male":
            return sex == Sex.MALE
        if restriction == "female":
            return sex == Sex.FEMALE
        return True
    
    def get_meds(self, name: str) -> list[str]:
        """Get typical medications for a condition."""
        cond = self.get_condition(name)
        if cond:
            return cond.get("meds", [])
        return []
    
    def get_onset_range(self, name: str) -> tuple[int, int]:
        """Get typical onset age range."""
        cond = self.get_condition(name)
        if cond and "onset_age" in cond:
            return tuple(cond["onset_age"])
        return (30, 70)
    
    def get_prevalence(self, name: str) -> float:
        """Get prevalence weight for a condition."""
        cond = self.get_condition(name)
        if cond:
            return cond.get("prevalence", 0.05)
        return 0.05
    
    def get_risk_factors(self, name: str) -> list[str]:
        """Get conditions that increase risk of this one."""
        cond = self.get_condition(name)
        if cond:
            return cond.get("risk_factors", [])
        return []
    
    def get_labs(self, name: str) -> list[str]:
        """Get labs typically ordered for this condition."""
        cond = self.get_condition(name)
        if cond:
            return cond.get("labs", [])
        return []
    
    def get_visit_pattern(self, name: str) -> str:
        """Get visit frequency pattern."""
        cond = self.get_condition(name)
        if cond:
            return cond.get("visits", "annual")
        return "annual"
    
    def get_sex_specific(self, name: str) -> str | None:
        """Get the anatomic sex restriction, if any (male/female)."""
        cond = self.get_condition(name)
        if cond:
            return cond.get("sex")
        return None
    
    def all_chronic_conditions(self) -> list[str]:
        """Get all chronic condition names."""
        conditions = []
        for category in self.CHRONIC_CATEGORIES:
            conds = ConditionRegistry._data.get(category, {})
            if isinstance(conds, dict):
                conditions.extend(conds.keys())
        return conditions
    
    def all_acute_conditions(self) -> list[str]:
        """Get all acute condition names."""
        conditions = []
        for category in self.ACUTE_CATEGORIES:
            conds = ConditionRegistry._data.get(category, {})
            if isinstance(conds, dict):
                conditions.extend(conds.keys())
        return conditions
    
    def conditions_by_category(self, category: str) -> list[str]:
        """Get conditions in a category."""
        return list(ConditionRegistry._data.get(category, {}).keys())
    
    def get_acute_condition(self, name: str) -> dict | None:
        """Get acute condition data."""
        name_lower = name.lower().replace(" ", "_").replace("-", "_")
        for category in self.ACUTE_CATEGORIES:
            conds = ConditionRegistry._data.get(category, {})
            if isinstance(conds, dict) and name_lower in conds:
                return conds[name_lower]
        return None
    
    def get_weighted_chronic_conditions(self, age: int, sex: Sex) -> list[tuple[str, float]]:
        """Get chronic conditions with prevalence-based weights adjusted for age/sex."""
        weighted = []
        for cond_name in self.all_chronic_conditions():
            cond = self.get_condition(cond_name)
            if not cond:
                continue
            
            # Skip sex-specific conditions that don't apply
            sex_restriction = cond.get("sex")
            if sex_restriction:
                if sex_restriction == "male" and sex == Sex.FEMALE:
                    continue
                if sex_restriction == "female" and sex == Sex.MALE:
                    continue
            
            # Get base prevalence
            prevalence = cond.get("prevalence", 0.05)
            
            # Adjust by age (conditions more common near their typical onset)
            onset_min, onset_max = self.get_onset_range(cond_name)
            if age < onset_min:
                age_factor = 0.1  # Very unlikely before typical onset
            elif age > onset_max + 10:
                age_factor = 1.0  # At or past typical onset
            else:
                # Peak probability in the middle of onset range
                mid = (onset_min + onset_max) / 2
                range_width = max(1, onset_max - onset_min)
                age_factor = 0.3 + 0.7 * (1 - abs(age - mid) / range_width)
                age_factor = max(0.1, min(1.2, age_factor))
            
            weighted.append((cond_name, prevalence * age_factor * sex_weight(cond, sex)))
        
        return weighted
    
    def get_weighted_acute_conditions(self, age: int, season: str = None) -> list[tuple[str, float]]:
        """Get acute conditions with frequency-based weights adjusted for age/season."""
        weighted = []
        for cond_name in self.all_acute_conditions():
            cond = self.get_acute_condition(cond_name)
            if not cond:
                continue
            
            frequency = cond.get("frequency", 1.0)
            
            # Adjust by season
            seasonality = cond.get("seasonality")
            if seasonality and season:
                if season in seasonality:
                    frequency *= 2.0
                else:
                    frequency *= 0.3
            
            # Adjust by age bias
            age_bias = cond.get("age_bias")
            if age_bias == "young" and age > 40:
                frequency *= 0.5
            elif age_bias == "older" and age < 50:
                frequency *= 0.5
            elif age_bias == "middle" and (age < 30 or age > 70):
                frequency *= 0.6
            
            weighted.append((cond_name, frequency))
        
        return weighted


# =============================================================================
# Life Stage Definitions
# =============================================================================

@dataclass
class LifeStage:
    """Adult life stage with typical characteristics."""
    name: str
    age_range: tuple[int, int | None]  # (min_age, max_age) - None means no upper bound
    visit_frequency_months: int  # Typical months between wellness visits
    screening_focus: list[str]
    typical_conditions: list[str]
    social_context: dict[str, Any]


# =============================================================================
# Adult Vaccine Schedule (ACIP Recommendations)
# =============================================================================

ADULT_VACCINES = {
    "influenza": {
        "cvx": "141",
        "display": "Influenza, seasonal, injectable",
        "schedule": "annual",
        "min_age": 18,
        "series_doses": 1,
    },
    "tdap": {
        "cvx": "115",
        "display": "Tdap (Tetanus, Diphtheria, Pertussis)",
        "schedule": "every_10_years",
        "min_age": 18,
        "series_doses": 1,
    },
    "td": {
        "cvx": "09",
        "display": "Td (Tetanus, Diphtheria)",
        "schedule": "every_10_years",
        "min_age": 18,
        "series_doses": 1,
        "notes": "After initial Tdap, Td boosters every 10 years",
    },
    "shingrix": {
        "cvx": "187",
        "display": "Shingrix (Zoster Recombinant)",
        "schedule": "2_dose_series",
        "min_age": 50,
        "series_doses": 2,
        "dose_interval_days": 60,  # 2-6 months apart
    },
    "pcv20": {
        "cvx": "216",
        "display": "Prevnar 20 (Pneumococcal conjugate)",
        "schedule": "once",
        "min_age": 65,
        "series_doses": 1,
    },
    "ppsv23": {
        "cvx": "33",
        "display": "Pneumovax (Pneumococcal polysaccharide)",
        "schedule": "once",
        "min_age": 65,
        "series_doses": 1,
        "notes": "Given 1 year after PCV20 or for high-risk adults",
    },
    "covid19": {
        "cvx": "213",
        "display": "COVID-19 mRNA vaccine",
        "schedule": "annual",
        "min_age": 18,
        "series_doses": 1,
        "start_year": 2020,
    },
    "hepb": {
        "cvx": "43",
        "display": "Hepatitis B",
        "schedule": "3_dose_series",
        "min_age": 18,
        "max_age": 59,
        "series_doses": 3,
        "notes": "Recommended for unvaccinated adults 19-59",
    },
}

# High-risk conditions that may require additional vaccines
# Registry keys for ACIP risk conditions (ACIP adult schedule 2025:
# pneumococcal for adults 19-49 with chronic heart, lung, liver or kidney
# disease, diabetes, alcoholism or immunocompromise).
HIGH_RISK_CONDITIONS_FOR_VACCINES = {
    "pneumococcal": ["type2_diabetes", "heart_failure", "coronary_artery_disease", "ckd_stage3",
                     "copd", "asthma", "cirrhosis", "hepatitis_b", "hepatitis_c",
                     "alcohol_use_disorder", "hiv"],
    "hepb": ["type2_diabetes", "ckd_stage3", "hiv", "cirrhosis", "hepatitis_c", "fatty_liver"],
}


ADULT_LIFE_STAGES = [
    LifeStage(
        name="young_adult",
        age_range=(18, 39),
        visit_frequency_months=24,  # Every 1-3 years
        screening_focus=[
            "blood_pressure",
            "depression",
            "anxiety",
            "sti_screening",
            "cervical_cancer",  # women 21+
            "immunization_catchup",
        ],
        typical_conditions=[
            "anxiety", "depression", "adhd", 
            "obesity", "substance_use",
        ],
        social_context={
            "employment": ["student", "entry_level", "unemployed", "military"],
            "insurance": ["parent_plan", "employer", "marketplace", "uninsured", "medicaid"],
            "living": ["with_parents", "roommates", "alone", "with_partner"],
        }
    ),
    LifeStage(
        name="adult",
        age_range=(40, 64),
        visit_frequency_months=12,  # Annual
        screening_focus=[
            "blood_pressure",
            "lipids",
            "diabetes",
            "colorectal_cancer",
            "breast_cancer",  # women 40+
            "cervical_cancer",  # women to 65
            "lung_cancer",  # if smoking history
            "depression",
            "alcohol_misuse",
        ],
        typical_conditions=[
            "hypertension", "hyperlipidemia", "type2_diabetes",
            "obesity", "prediabetes", "depression", "anxiety",
            "gerd", "osteoarthritis", "low_back_pain",
        ],
        social_context={
            "employment": ["employed_fulltime", "employed_parttime", "self_employed", "unemployed", "disabled"],
            "insurance": ["employer", "marketplace", "medicaid", "uninsured"],
            "living": ["with_spouse", "alone", "with_children", "with_aging_parents"],
            "caregiving": ["children", "aging_parents", "both", "none"],
        }
    ),
    LifeStage(
        name="older_adult",
        age_range=(65, None),
        visit_frequency_months=12,  # Annual Wellness Visit
        screening_focus=[
            "blood_pressure",
            "diabetes",
            "colorectal_cancer",  # to 75
            "breast_cancer",  # to 74
            "lung_cancer",  # if eligible
            "osteoporosis",  # women 65+
            "falls_prevention",
            "cognitive_screening",
            "depression",
            "advance_care_planning",
        ],
        typical_conditions=[
            "hypertension", "hyperlipidemia", "type2_diabetes",
            "coronary_artery_disease", "heart_failure", "atrial_fibrillation",
            "ckd_stage3", "copd", "osteoarthritis", "osteoporosis",
            "cognitive_impairment", "depression", "falls",
        ],
        social_context={
            "employment": ["retired", "part_time", "working"],
            "insurance": ["medicare", "medicare_advantage", "medicaid_dual"],
            "living": ["with_spouse", "alone", "assisted_living", "with_family", "nursing_home"],
            "caregiving_needs": ["independent", "needs_some_help", "dependent"],
            "social_support": ["strong", "moderate", "isolated"],
        }
    ),
]


def get_life_stage(age: int) -> LifeStage:
    """Get the appropriate life stage for an age."""
    for stage in ADULT_LIFE_STAGES:
        min_age, max_age = stage.age_range
        if max_age is None:
            if age >= min_age:
                return stage
        elif min_age <= age <= max_age:
            return stage
    return ADULT_LIFE_STAGES[0]  # Default to young adult


# =============================================================================
# Adult-Specific Models
# =============================================================================

class AdultLifeArc(BaseModel):
    """High-level life trajectory for an adult patient."""
    
    # Health trajectory
    health_trajectory: str  # "healthy", "single_chronic", "multiple_chronic", "complex"
    
    # Chronic conditions with onset ages
    chronic_conditions: list[str] = []
    condition_onset_ages: dict[str, int] = {}  # condition -> age at onset
    condition_onset_dates: dict[str, date] = {}  # condition -> onset date (<= today)

    # Major health events
    hospitalizations: list[dict[str, Any]] = []
    surgeries: list[dict[str, Any]] = []
    
    # Cardiovascular risk
    ascvd_risk_category: str = "low"  # "low", "borderline", "intermediate", "high", "very_high"
    
    # Social trajectory
    employment_history: list[dict[str, Any]] = []
    insurance_gaps: list[tuple[int, int]] = []  # (start_age, end_age) of uninsured periods
    
    # Family formation
    marital_status: str = "single"
    children: int = 0
    
    # End of life (for older adults)
    advance_directive: bool = False
    goals_of_care: str | None = None


class WeightTrajectory(BaseModel):
    """Models weight changes over adult life (no growth charts)."""
    
    baseline_bmi: float  # BMI at age 18
    current_bmi: float
    trajectory: str  # "stable", "gradual_gain", "significant_gain", "weight_loss", "yo_yo"
    
    # Key inflection points
    inflection_points: list[dict[str, Any]] = []  # {"age": 35, "bmi": 28, "reason": "sedentary_job"}
    
    def get_bmi_at_age(self, age: int) -> float:
        """Interpolate BMI at a given age."""
        # Simple linear interpolation for now
        # Could be made more sophisticated with inflection points
        if age <= 18:
            return self.baseline_bmi
        
        # Find surrounding inflection points
        for i, point in enumerate(self.inflection_points):
            if point["age"] > age:
                if i == 0:
                    prev_age, prev_bmi = 18, self.baseline_bmi
                else:
                    prev_age = self.inflection_points[i-1]["age"]
                    prev_bmi = self.inflection_points[i-1]["bmi"]
                next_age, next_bmi = point["age"], point["bmi"]
                
                # Linear interpolation
                ratio = (age - prev_age) / (next_age - prev_age)
                return prev_bmi + ratio * (next_bmi - prev_bmi)
        
        # After last inflection point
        if self.inflection_points:
            return self.inflection_points[-1]["bmi"]
        return self.current_bmi
    
    def get_weight_at_age(self, age: int, height_m: float) -> float:
        """Get weight in kg at a given age."""
        bmi = self.get_bmi_at_age(age)
        return bmi * (height_m ** 2)


# =============================================================================
# Condition Onset Probability by Age
# =============================================================================

# Base probability weights by category (higher = more common)
CATEGORY_WEIGHTS = {
    "cardiovascular": 25,
    "metabolic": 20,
    "mental_health": 15,
    "musculoskeletal": 15,
    "gastrointestinal": 10,
    "respiratory": 8,
    "neurologic": 5,
    "dermatologic": 5,
    "genitourinary": 5,
    "renal": 3,
    "infectious": 2,
    "other": 5,
}

# Age multipliers (probability increases with age for most conditions)
def get_age_multiplier(age: int, condition: str) -> float:
    """Get probability multiplier based on age."""
    registry = ConditionRegistry.get()
    onset_min, onset_max = registry.get_onset_range(condition)
    
    if age < onset_min:
        return 0.1  # Very unlikely before typical onset
    elif age > onset_max:
        return 1.2  # Slightly higher after typical range
    else:
        # Peak probability in the middle of onset range
        mid = (onset_min + onset_max) / 2
        return 0.5 + 0.5 * (1 - abs(age - mid) / (onset_max - onset_min))


# =============================================================================
# Adult Engine Implementation
# =============================================================================

class AdultEngine:
    """
    Generates synthetic adult patient records.

    Key differences from PedsEngine:
    - Uses USPSTF screening schedule instead of well-child visits
    - Generates chronic disease accumulation patterns
    - Models weight trends instead of growth percentiles
    - Includes employment, insurance, social complexity
    - Handles polypharmacy and drug interactions

    Args:
        use_llm: Accepted for API symmetry with PedsEngine but NOT supported:
            the adult engine is fully template-driven and never calls an LLM,
            so output is identical with use_llm True or False and no API key
            is needed. ``self.use_llm`` is always False.
        messiness_level: Chart quality level 0-5 (default 0 = pristine).
            Stored for symmetry; the adult engine does not inject messiness.
        llm_client: Ignored (see use_llm).

    Raises (from ``generate``):
        UnknownConditionError: a requested condition is not in the registry
            and has no alias.
        ConditionGateError: a requested condition is impossible for the
            requested sex or age (e.g. PCOS in a male, BPH at 25).
    """

    def __init__(
        self,
        use_llm: bool = True,
        messiness_level: int = 0,
        llm_client: LLMClient | None = None,
        knowledge_dir: Path | None = None,
    ):
        self.knowledge_dir = knowledge_dir or Path(__file__).parent.parent / "knowledge"

        # LLM narratives are not implemented for adults (see class docstring).
        self.llm: LLMClient | None = None
        self.use_llm = False

        # Messiness injection (0-5)
        self.messiness_level = max(0, min(5, messiness_level))
        self.messiness = MessinessInjector(level=self.messiness_level)

        self._rng = random.Random()
        self._today = date.today()
        self._height_m = 1.70

    def generate(self, seed: GenerationSeed) -> Patient:
        """Generate a complete adult patient record."""

        # Per-call RNG: reproducible for a seed and safe under concurrent
        # requests (never reseeds the global random module).
        self._rng = random.Random(seed.random_seed) if seed.random_seed is not None else random.Random()
        self._today = date.today()

        requested = self.resolve_conditions(seed.conditions) if seed.conditions else None
        if requested is None and seed.description:
            requested = infer_conditions_from_description(seed.description) or None

        demographics = self._generate_demographics(seed, requested)
        if requested:
            self.validate_conditions(requested, demographics.sex_at_birth, demographics.age_years)

        life_arc = self._generate_life_arc(demographics, seed, requested)
        weight_trajectory = self._generate_weight_trajectory(demographics, life_arc)
        if demographics.sex_at_birth == Sex.MALE:
            self._height_m = self._rng.uniform(1.65, 1.88)
        else:
            self._height_m = self._rng.uniform(1.52, 1.75)
        social_history = self._generate_social_history(demographics, life_arc, seed)

        problem_list = self._create_problem_list(demographics, life_arc)
        medications = self._generate_medications(life_arc, demographics, weight_trajectory)
        encounters, procedures = self._generate_encounter_timeline(
            demographics, life_arc, weight_trajectory, seed, social_history, medications
        )
        immunizations = self._generate_immunizations(demographics, life_arc, seed)
        tier = self._determine_complexity(life_arc)

        patient = Patient(
            demographics=demographics,
            social_history=social_history,
            problem_list=problem_list,
            medication_list=medications,
            immunization_record=immunizations,
            procedure_history=procedures,
            encounters=encounters,
            complexity_tier=tier,
            generation_seed=seed.model_dump(),
        )

        # Age-gate enforcement (W1.1 fix) - clamp any condition whose
        # onset_date violates its hardcoded age gate. Even for adults, the
        # min-age gates (e.g. ADHD 48mo, asthma 12mo) protect against
        # downstream code paths that mis-set onset_date.
        patient = self._validate_and_fix_age_gates(patient)

        return patient

    # ------------------------------------------------------------------ #
    # Requested-condition validation                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def resolve_conditions(names: list[str]) -> list[str]:
        """Map requested names to registry keys (aliases applied, deduped).

        Raises:
            UnknownConditionError: for any name the registry cannot resolve.
        """
        registry = ConditionRegistry.get()
        out: list[str] = []
        for name in names:
            key = registry.resolve_key(name)
            if key not in out:
                out.append(key)
        return out

    @staticmethod
    def validate_conditions(keys: list[str], sex: Sex | None, age: int | None) -> None:
        """Reject conditions that are impossible for this sex or age.

        Raises:
            ConditionGateError: naming every violation.
        """
        registry = ConditionRegistry.get()
        problems = []
        for key in keys:
            if sex is not None and not registry.sex_allowed(key, sex):
                problems.append(f"{key} requires sex {registry.get_sex_specific(key)}, "
                                f"patient is {sex.value}")
            min_age = registry.get_min_age(key)
            if age is not None and age < min_age:
                problems.append(f"{key} requires age >= {min_age}, patient is {age}")
        if problems:
            raise ConditionGateError("; ".join(problems))

    # ------------------------------------------------------------------ #
    # Age-gate enforcement (W1.1)                                        #
    # ------------------------------------------------------------------ #

    # Mirror of PedsEngine._hardcoded_age_gates / PatientValidator.AGE_GATES
    _ADULT_AGE_GATES: dict[str, tuple[int, int | None]] = {
        "adhd": (48, None),
        "anxiety": (36, None),
        "depression": (72, None),
        "asthma": (12, None),
        "bronchiolitis": (0, 24),
        "prediabetes": (120, None),
        "type_2_diabetes": (120, None),
        "learning_disorder": (60, None),
        "enuresis": (60, None),
        "obesity": (24, None),
        "obstructive_sleep_apnea": (24, None),
    }

    @classmethod
    def _age_gate_key_for(cls, display_name: str) -> str | None:
        """Map a condition display_name to its age-gate key (if any)."""
        if not display_name:
            return None
        name = display_name.lower().strip()
        candidates = [name, name.replace(" ", "_"), name.replace("-", "_")]
        alias_map = {
            "attention deficit hyperactivity disorder": "adhd",
            "attention-deficit hyperactivity disorder": "adhd",
            "type 2 diabetes mellitus": "type_2_diabetes",
            "type 1 diabetes mellitus": "type_1_diabetes",
            "obstructive sleep apnea": "obstructive_sleep_apnea",
            "osa": "obstructive_sleep_apnea",
            "major depressive disorder": "depression",
            "generalized anxiety disorder": "anxiety",
        }
        if name in alias_map:
            candidates.append(alias_map[name])
        for c in candidates:
            if c in cls._ADULT_AGE_GATES:
                return c
        return None

    def _validate_and_fix_age_gates(self, patient: Patient) -> Patient:
        """
        Walk patient.problem_list; clamp any condition whose onset_date
        violates its age gate using age AT ONSET (not current patient age).
        """
        from dateutil.relativedelta import relativedelta

        dob = patient.demographics.date_of_birth
        today = date.today()

        for cond in patient.problem_list:
            if cond.onset_date is None:
                continue
            gate_key = self._age_gate_key_for(cond.display_name)
            if gate_key is None:
                continue
            min_months, max_months = self._ADULT_AGE_GATES[gate_key]
            age_at_onset_months = (cond.onset_date - dob).days / 30.44

            if age_at_onset_months < min_months:
                new_onset = dob + relativedelta(months=min_months) + timedelta(days=1)
                if new_onset > today:
                    new_onset = today
                cond.onset_date = new_onset
            elif max_months is not None and age_at_onset_months > max_months:
                new_onset = dob + relativedelta(months=max_months) - timedelta(days=1)
                if new_onset < dob:
                    new_onset = dob
                cond.onset_date = new_onset

        return patient

    def _generate_immunizations(
        self,
        demographics: Demographics,
        life_arc: AdultLifeArc,
        seed: GenerationSeed
    ) -> list[Immunization]:
        """Generate adult immunization record based on age, conditions, and ACIP guidelines.

        Every date is between the 18th birthday and today.
        """

        immunizations = []
        age = demographics.age_years
        dob = demographics.date_of_birth
        today = self._today
        adult_start = dob + relativedelta(years=18)
        current_year = today.year

        def ok(d: date) -> bool:
            return adult_start <= d <= today

        def at_age(years: int, spread_days: int = 180) -> date:
            return dob + relativedelta(years=years) + timedelta(days=self._rng.randint(0, spread_days))

        years_history = seed.years_of_history if seed.years_of_history is not None else min(age - 18, 15)
        years_history = max(0, years_history)

        high_risk = any(
            cond in life_arc.chronic_conditions
            for cond in HIGH_RISK_CONDITIONS_FOR_VACCINES.get("pneumococcal", [])
        )

        # 1. Annual influenza, given September-November (~50% adult uptake, CDC FluVaxView).
        for year_offset in range(years_history + 1):
            year = current_year - year_offset
            if self._rng.random() < 0.55:
                flu_date = date(year, self._rng.randint(9, 11), self._rng.randint(1, 28))
                if ok(flu_date):
                    immunizations.append(self._create_immunization("influenza", flu_date, 1, 1))

        # 2. Tdap once as an adult, Td/Tdap every 10 years.
        tdap_date = at_age(18)
        if ok(tdap_date):
            immunizations.append(self._create_immunization("tdap", tdap_date, 1, 1))
        for boost_age in range(28, age + 1, 10):
            td_date = at_age(boost_age)
            if ok(td_date):
                immunizations.append(self._create_immunization("td", td_date, 1, 1))

        # 3. Shingrix (age 50+, 2 doses 2-6 months apart; ACIP 2018).
        if age >= 50 and self._rng.random() < 0.40:  # ~40% uptake
            dose1_date = at_age(max(50, age - self._rng.randint(0, 5)))
            if ok(dose1_date):
                immunizations.append(self._create_immunization("shingrix", dose1_date, 1, 2))
                dose2_date = dose1_date + timedelta(days=self._rng.randint(60, 180))
                if ok(dose2_date):
                    immunizations.append(self._create_immunization("shingrix", dose2_date, 2, 2))

        # 4. Pneumococcal conjugate: age 65+ historically; ACIP lowered the
        # universal age to 50 on 2024-10-23. Adults 19-49 with risk conditions.
        if high_risk:
            eligible = dob + relativedelta(years=19)
            onsets = [life_arc.condition_onset_dates[c] for c in life_arc.chronic_conditions
                      if c in HIGH_RISK_CONDITIONS_FOR_VACCINES["pneumococcal"]
                      and c in life_arc.condition_onset_dates]
            if onsets:
                eligible = max(eligible, min(onsets))
        else:
            eligible = min(dob + relativedelta(years=65),
                           max(dob + relativedelta(years=50), date(2024, 10, 23)))
        if age >= 19 and self._rng.random() < 0.70:
            pcv_date = eligible + timedelta(days=self._rng.randint(0, 365))
            if ok(pcv_date):
                immunizations.append(self._create_immunization("pcv20", pcv_date, 1, 1))

        # 5. COVID-19: none before the 2020-12-11 EUA; 2021 primary series in
        # spring, updated vaccines each fall from 2022.
        for year in range(max(2021, current_year - years_history), current_year + 1):
            if self._rng.random() < 0.45:
                month = self._rng.randint(3, 6) if year == 2021 else self._rng.randint(9, 11)
                covid_date = date(year, month, self._rng.randint(1, 28))
                if ok(covid_date):
                    immunizations.append(self._create_immunization("covid19", covid_date, 1, 1))

        # 6. Hepatitis B (universal for adults 19-59, ACIP 2022; 0, 1, 6 months).
        if age >= 19 and self._rng.random() < 0.30:
            hepb_age = self._rng.randint(max(19, age - 10), min(59, age)) if age <= 69 else None
            if hepb_age is not None and hepb_age <= age:
                dose1_date = at_age(hepb_age)
                for n, offset in ((1, 0), (2, 30), (3, 180)):
                    d = dose1_date + timedelta(days=offset)
                    if ok(d):
                        immunizations.append(self._create_immunization("hepb", d, n, 3))

        immunizations.sort(key=lambda x: x.date)
        return immunizations

    def _create_immunization(
        self,
        vaccine_key: str,
        admin_date: date,
        dose_number: int,
        series_doses: int
    ) -> Immunization:
        """Create an immunization record."""
        vaccine = ADULT_VACCINES.get(vaccine_key, {})

        return Immunization(
            vaccine_code=CodeableConcept(
                system="http://hl7.org/fhir/sid/cvx",
                code=vaccine.get("cvx", "999"),
                display=vaccine.get("display", vaccine_key),
            ),
            display_name=vaccine.get("display", vaccine_key),
            date=admin_date,
            dose_number=dose_number,
            series_doses=series_doses,
            site="Left deltoid" if self._rng.random() < 0.6 else "Right deltoid",
            route="Intramuscular",
            manufacturer=self._rng.choice(["Pfizer", "Moderna", "GSK", "Merck", "Sanofi"]),
            performer="RN",
            location="Primary Care Associates",
        )
    
    def _generate_demographics(self, seed: GenerationSeed,
                               requested: list[str] | None = None) -> Demographics:
        """Generate adult demographics.

        Age comes from the seed, else from the description, else is sampled
        (never below the minimum age of a requested condition). When sex is
        unspecified and a requested condition is sex-specific, the compatible
        sex is chosen.
        """
        registry = ConditionRegistry.get()
        requested = requested or []
        today = self._today
        min_req_age = max([18] + [registry.get_min_age(c) for c in requested])

        age_months_exact: int | None = None
        if seed.age is not None:
            age_years = max(18, seed.age)
        elif seed.age_months is not None:
            age_months_exact = max(18 * 12, seed.age_months)
            age_years = age_months_exact // 12
        else:
            _, described_age = infer_age_from_description(seed.description, self._rng)
            if described_age is not None and described_age >= 18:
                age_years = described_age
            else:
                # Random age distribution weighted toward working age
                age_years = self._rng.choices(
                    [self._rng.randint(18, 39), self._rng.randint(40, 64),
                     self._rng.randint(65, 90)],
                    weights=[35, 40, 25]
                )[0]
            if age_years < min_req_age:
                age_years = self._rng.randint(min_req_age, max(min_req_age, 75))

        # Sex: honor the seed; otherwise pick one compatible with requested
        # sex-specific conditions.
        if seed.sex is not None:
            sex = seed.sex
        else:
            needed = {registry.get_sex_specific(c) for c in requested} - {None}
            if len(needed) > 1:
                raise ConditionGateError(
                    f"requested conditions need both sexes: {', '.join(requested)}")
            sex = Sex(needed.pop()) if needed else self._rng.choice([Sex.MALE, Sex.FEMALE])

        # DOB: uniformly within the requested age, using calendar arithmetic
        # (relativedelta) so leap days never shift the age by one.
        if age_months_exact is not None:
            latest = today - relativedelta(months=age_months_exact)
            earliest = today - relativedelta(months=age_months_exact + 1) + timedelta(days=1)
        else:
            latest = today - relativedelta(years=age_years)
            earliest = today - relativedelta(years=age_years + 1) + timedelta(days=1)
        dob = earliest + timedelta(days=self._rng.randint(0, (latest - earliest).days))

        # Generate names
        first_name = self._generate_first_name(sex, age_years)
        last_name = self._generate_last_name()
        
        # Address
        address = Address(
            line1=f"{self._rng.randint(100, 9999)} {self._rng.choice(['Oak', 'Maple', 'Cedar', 'Pine', 'Main', 'First', 'Park', 'Lake', 'Forest', 'River'])} {self._rng.choice(['Street', 'Avenue', 'Lane', 'Drive', 'Court', 'Boulevard', 'Way'])}",
            city=self._rng.choice(["Springfield", "Riverside", "Lakewood", "Fairview", "Madison", "Franklin", "Clinton", "Georgetown"]),
            state=seed.state or self._rng.choice(["MN", "WI", "CA", "TX", "NY", "FL", "IL", "OH", "PA", "MI"]),
            postal_code=f"{self._rng.randint(10000, 99999)}",
        )
        
        # Emergency contact (spouse, parent, or adult child depending on age)
        ec_relationship = self._get_emergency_contact_relationship(age_years)
        ec_sex = self._rng.choice([Sex.MALE, Sex.FEMALE])
        emergency_contact = Contact(
            name=f"{self._generate_first_name(ec_sex, age_years)} {last_name if ec_relationship == 'Spouse' else self._generate_last_name()}",
            relationship=ec_relationship,
            phone=f"({self._rng.randint(200, 999)}) {self._rng.randint(200, 999)}-{self._rng.randint(1000, 9999)}",
        )
        
        # Race and ethnicity
        race = seed.race or self._rng.choices(
            [["White"], ["Black or African American"], ["Asian"], ["Two or more races"], ["American Indian or Alaska Native"]],
            weights=[60, 13, 6, 10, 1]
        )[0]
        
        ethnicity = seed.ethnicity or self._rng.choices(
            ["Not Hispanic or Latino", "Hispanic or Latino"],
            weights=[82, 18]
        )[0]
        
        return Demographics(
            given_names=[first_name],
            family_name=last_name,
            date_of_birth=dob,
            sex_at_birth=sex,
            race=race,
            ethnicity=ethnicity,
            preferred_language="English",
            address=address,
            phone=f"({self._rng.randint(200, 999)}) {self._rng.randint(200, 999)}-{self._rng.randint(1000, 9999)}",
            emergency_contact=emergency_contact,
        )
    
    def _generate_life_arc(self, demographics: Demographics, seed: GenerationSeed,
                           requested: list[str] | None = None) -> AdultLifeArc:
        """Generate the adult life trajectory."""

        age = demographics.age_years
        sex = demographics.sex_at_birth

        # Determine complexity tier
        if seed.complexity_tier:
            tier = seed.complexity_tier
        elif requested:
            tier = ComplexityTier.TIER_1 if len(requested) == 1 else ComplexityTier.TIER_2
        else:
            tier = self._rng.choices(
                [ComplexityTier.TIER_0, ComplexityTier.TIER_1, ComplexityTier.TIER_2, ComplexityTier.TIER_3],
                weights=[30, 35, 25, 10]
            )[0]

        if requested:
            conditions = list(requested)
        else:
            excluded = set()
            for name in seed.exclude_conditions or []:
                try:
                    excluded.add(ConditionRegistry.get().resolve_key(name))
                except UnknownConditionError:
                    pass
            conditions = self._generate_conditions_by_age_and_tier(age, tier, sex, excluded)

        onset_ages: dict[str, int] = {}
        onset_dates: dict[str, date] = {}
        for cond in conditions:
            onset_ages[cond] = self._sample_onset_age(cond, age)
            onset_dates[cond] = self._onset_date(demographics.date_of_birth, onset_ages[cond])

        # Determine health trajectory
        if not conditions:
            trajectory = "healthy"
        elif len(conditions) == 1:
            trajectory = "single_chronic"
        elif len(conditions) <= 3:
            trajectory = "multiple_chronic"
        else:
            trajectory = "complex"

        ascvd_risk = self._calculate_ascvd_risk_category(conditions, age, sex)
        marital_status = self._generate_marital_status(age)
        children = self._generate_number_of_children(age, marital_status)

        return AdultLifeArc(
            health_trajectory=trajectory,
            chronic_conditions=conditions,
            condition_onset_ages=onset_ages,
            condition_onset_dates=onset_dates,
            ascvd_risk_category=ascvd_risk,
            marital_status=marital_status,
            children=children,
        )

    def _sample_onset_age(self, condition: str, age: int) -> int:
        """Onset age spread across the condition's typical onset window.

        The window is the YAML ``onset_age`` range, clipped to the patient's
        life (never above current age, never below the condition's minimum
        age). A patient younger than the typical window gets a recent onset.
        Triangular sampling peaks mid-window rather than piling up in the
        years just before the visit.
        """
        registry = ConditionRegistry.get()
        lo_typ, hi_typ = registry.get_onset_range(condition)
        floor = max(registry.get_min_age(condition) if registry.get_condition(condition) else 18,
                    min(lo_typ, 18))
        lo, hi = max(floor, lo_typ), min(age, hi_typ)
        if lo > hi:
            lo, hi = max(floor, age - 3), age
        if lo >= hi:
            return hi
        return int(round(self._rng.triangular(lo, hi, (lo + hi) / 2)))

    def _onset_date(self, dob: date, onset_age: int) -> date:
        """A date during the given year of life, never after today."""
        start = dob + relativedelta(years=onset_age)
        d = start + timedelta(days=self._rng.randint(0, 364))
        return min(d, self._today - timedelta(days=self._rng.randint(1, 30)))

    def _generate_conditions_by_age_and_tier(
        self, age: int, tier: ComplexityTier, sex: Sex, excluded: set[str] | None = None
    ) -> list[str]:
        """Generate conditions probabilistically based on age and complexity tier."""

        conditions: list[str] = []
        registry = ConditionRegistry.get()
        excluded = excluded or set()

        # Target number of conditions by tier
        target_conditions = {
            ComplexityTier.TIER_0: 0,
            ComplexityTier.TIER_1: 1,
            ComplexityTier.TIER_2: self._rng.randint(2, 4),
            ComplexityTier.TIER_3: self._rng.randint(4, 7),
        }.get(tier, 0)

        if target_conditions == 0:
            return []

        candidates = []
        for condition in registry.all_chronic_conditions():
            if condition in excluded:
                continue
            if not registry.sex_allowed(condition, sex) or age < registry.get_min_age(condition):
                continue
            cond = registry.get_condition(condition) or {}
            prob = (get_age_multiplier(age, condition) * sex_weight(cond, sex)
                    * self._rng.uniform(0.3, 1.0))
            candidates.append((condition, prob))

        # Sort by probability (higher = more likely to select)
        self._rng.shuffle(candidates)  # Add randomness
        candidates.sort(key=lambda x: x[1], reverse=True)

        for condition, prob in candidates:
            if len(conditions) >= target_conditions:
                break
            if self._rng.random() < prob:
                conditions.append(condition)

        return conditions

    def _get_typical_onset_age(self, condition: str) -> int:
        """Get typical age of onset for a condition."""
        registry = ConditionRegistry.get()
        onset_min, onset_max = registry.get_onset_range(condition)
        return (onset_min + onset_max) // 2
    
    def _calculate_ascvd_risk_category(
        self, conditions: list[str], age: int, sex: Sex
    ) -> str:
        """Calculate ASCVD risk category."""
        
        # Simplified risk stratification
        # Clinical ASCVD (CAD, PAD) is high risk outright (ACC/AHA 2018
        # cholesterol guideline); CKD is a risk enhancer, keyed ckd_stage3.
        high_risk_conditions = {"coronary_artery_disease", "peripheral_artery_disease",
                                "heart_failure", "atrial_fibrillation"}
        cv_risk_factors = {"hypertension", "hyperlipidemia", "type2_diabetes", "obesity",
                           "ckd_stage3"}
        
        if any(c in conditions for c in high_risk_conditions):
            return "very_high" if len(set(conditions) & cv_risk_factors) >= 2 else "high"
        
        risk_factor_count = len(set(conditions) & cv_risk_factors)
        
        if risk_factor_count >= 3:
            return "high"
        elif risk_factor_count >= 2:
            return "intermediate"
        elif risk_factor_count == 1 or age >= 65:
            return "borderline"
        else:
            return "low"
    
    def _generate_weight_trajectory(
        self, demographics: Demographics, life_arc: AdultLifeArc
    ) -> WeightTrajectory:
        """Generate weight trajectory over adult life."""
        
        age = demographics.age_years
        has_obesity = "obesity" in life_arc.chronic_conditions
        has_diabetes = "type2_diabetes" in life_arc.chronic_conditions
        
        # Baseline BMI at 18
        if has_obesity:
            baseline_bmi = self._rng.uniform(24, 28)  # Often overweight by 18
            trajectory_type = "gradual_gain"
        else:
            baseline_bmi = self._rng.uniform(19, 24)
            trajectory_type = self._rng.choice(["stable", "gradual_gain", "stable"])
        
        # Current BMI
        if has_obesity:
            current_bmi = self._rng.uniform(30, 42)
        elif has_diabetes:
            current_bmi = self._rng.uniform(26, 34)  # Often overweight
        else:
            current_bmi = self._rng.uniform(19, 28)
        
        # Generate inflection points
        inflection_points = []
        
        if trajectory_type == "gradual_gain" and age > 30:
            # Typical middle-age weight gain
            inflection_points.append({
                "age": 30,
                "bmi": baseline_bmi + self._rng.uniform(1, 3),
                "reason": "lifestyle_changes"
            })
            if age > 45:
                inflection_points.append({
                    "age": 45,
                    "bmi": baseline_bmi + self._rng.uniform(3, 6),
                    "reason": "metabolism_slowing"
                })
        
        return WeightTrajectory(
            baseline_bmi=baseline_bmi,
            current_bmi=current_bmi,
            trajectory=trajectory_type,
            inflection_points=inflection_points,
        )
    
    def _generate_social_history(
        self, demographics: Demographics, life_arc: AdultLifeArc, seed: GenerationSeed
    ) -> SocialHistory:
        """Generate comprehensive adult social history."""
        
        age = demographics.age_years
        
        # Employment
        if age < 65:
            employment_status = self._rng.choices(
                ["Employed full-time", "Employed part-time", "Self-employed", "Unemployed", "Disabled", "Student"],
                weights=[60, 10, 10, 8, 7, 5]
            )[0]
        else:
            employment_status = self._rng.choices(
                ["Retired", "Employed part-time", "Employed full-time"],
                weights=[70, 20, 10]
            )[0]
        
        # Occupation (if employed)
        occupation = None
        if "Employed" in employment_status or "Self-employed" in employment_status:
            occupation = self._rng.choice([
                "Office worker", "Healthcare worker", "Retail", "Construction",
                "Teacher", "Engineer", "Manager", "Service industry",
                "Skilled trades", "Professional services"
            ])
        
        # Living situation based on marital status
        if life_arc.marital_status in ["Married", "Domestic partnership"]:
            living_situation = "Lives with spouse/partner"
        elif age >= 75:
            living_situation = self._rng.choices(
                ["Lives alone", "Lives with family", "Assisted living", "Nursing home"],
                weights=[40, 35, 15, 10]
            )[0]
        else:
            living_situation = self._rng.choice(["Lives alone", "Lives with family", "Lives with roommates"])
        
        # Household members
        household = []
        if "spouse" in living_situation.lower() or "partner" in living_situation.lower():
            household.append(HouseholdMember(
                name="Spouse",
                relationship="Spouse",
                age=demographics.age_years + self._rng.randint(-5, 5),
            ))
        
        if life_arc.children > 0 and "alone" not in living_situation.lower():
            # Add some children if they'd still be at home
            for i in range(min(life_arc.children, 3)):
                child_age = self._rng.randint(0, max(0, min(age - 20, 25)))
                if child_age < 22:  # Still might be at home
                    household.append(HouseholdMember(
                        name=f"Child {i+1}",
                        relationship="Child",
                        age=child_age,
                    ))
        
        # Substance use. Smoking status weights approximate US adults
        # (CDC NHIS 2022: ~12% current, ~22% former cigarette smokers).
        today = self._today
        dob = demographics.date_of_birth
        tobacco_status = self._rng.choices(["never", "former", "current"], weights=[62, 24, 14])[0]
        if "copd" in life_arc.chronic_conditions and tobacco_status == "never":
            tobacco_status = self._rng.choice(["former", "current"])  # COPD is mostly smoking-related
        tobacco = SubstanceUse(substance="Tobacco (cigarettes)", status=tobacco_status)
        if tobacco_status != "never":
            start_age = self._rng.randint(15, 22)
            start = dob + relativedelta(years=start_age)
            if tobacco_status == "former":
                quit_age = self._rng.randint(min(start_age + 2, age), age)
                quit_date = min(dob + relativedelta(years=quit_age, days=self._rng.randint(0, 300)),
                                today - timedelta(days=30))
                years_smoked = max(1, quit_age - start_age)
                tobacco.quit_date = quit_date
            else:
                years_smoked = max(1, age - start_age)
            ppd = self._rng.choice([0.25, 0.5, 0.5, 1.0, 1.0, 1.5])
            pack_years = round(ppd * years_smoked, 1)
            tobacco.start_date = start
            tobacco.frequency = f"{ppd:g} pack(s) per day"
            tobacco.quantity = f"{pack_years:g} pack-years"

        alcohol_status, alcohol_freq = self._rng.choices(
            [("never", None), ("current", "Social, 1-2 drinks per week"),
             ("current", "3-7 drinks per week"), ("current", "15+ drinks per week")],
            weights=[25, 45, 25, 5]
        )[0]
        if "alcohol_use_disorder" in life_arc.chronic_conditions:
            alcohol_status, alcohol_freq = "current", "15+ drinks per week"
        alcohol = SubstanceUse(substance="Alcohol", status=alcohol_status, frequency=alcohol_freq)

        return SocialHistory(
            living_situation=living_situation,
            household_members=household,
            employment_status=employment_status,
            occupation=occupation,
            tobacco=tobacco,
            alcohol=alcohol,
            food_security="Secure",
            housing_stability="Stable",
        )
    
    def _followup_interval_months(self, condition: str, months_since_onset: int) -> int:
        """Months until the next chronic-care visit for one condition.

        YAML visit patterns, read as "frequent until controlled, then
        maintenance". Sources: ACC/AHA 2017 hypertension guideline (monthly
        until at goal, then every 3-6 months); ADA Standards of Care 2026
        sec. 6 (every 3 months until at goal, then every 6). Stable disease
        does not stay on a quarterly schedule forever.
        """
        pattern = ConditionRegistry.get().get_visit_pattern(condition)
        m = months_since_onset
        if pattern == "monthly":
            return 1 if m < 6 else 3
        if pattern == "monthly_then_quarterly":
            return 1 if m < 3 else 3
        if pattern == "quarterly":
            return 3 if m < 24 else 6
        if pattern == "quarterly_then_annual":
            return 3 if m < 12 else 12
        if pattern == "biannual":
            return 6
        return 12

    def _jitter(self, d: date, days: int) -> date:
        return d + timedelta(days=self._rng.randint(-days, days))

    def _generate_encounter_timeline(
        self,
        demographics: Demographics,
        life_arc: AdultLifeArc,
        weight_trajectory: WeightTrajectory,
        seed: GenerationSeed,
        social_history: SocialHistory,
        medications: list[Medication],
    ) -> tuple[list[Encounter], list[Procedure]]:
        """Generate encounter history for an adult patient.

        All visits fall inside one history window (the last
        ``years_of_history`` years, default up to 15, never before age 18)
        and never after today. Chronic follow-up is one consolidated visit
        stream that addresses every active condition at the cadence of the
        most demanding one, instead of a separate visit stream per
        condition.
        """
        age = demographics.age_years
        dob = demographics.date_of_birth
        today = self._today
        years_history = seed.years_of_history if seed.years_of_history is not None else min(age - 18, 15)
        years_history = max(0, min(years_history, age - 18))
        window_start = max(dob + relativedelta(years=18), today - relativedelta(years=years_history))

        onsets = life_arc.condition_onset_dates
        self._lab_last: dict[str, date] = {}
        self._screen_last: dict[str, date] = {}
        self._screen_next: dict[str, date] = {}

        schedule: list[tuple[date, str, Any]] = []

        # Annual wellness / preventive visits.
        for year_offset in range(years_history, -1, -1):
            visit_date = self._jitter(today - relativedelta(years=year_offset), 30)
            if not (window_start <= visit_date <= today):
                continue
            visit_age = relativedelta(visit_date, dob).years
            stage = get_life_stage(visit_age)
            every = max(1, stage.visit_frequency_months // 12)
            if year_offset % every == 0:
                schedule.append((visit_date, "wellness", None))

        # New-diagnosis visits for conditions diagnosed inside the window.
        for cond, onset in onsets.items():
            if window_start <= onset <= today:
                schedule.append((onset, "diagnosis", cond))

        # Consolidated chronic follow-up.
        if onsets:
            t = max(window_start, min(onsets.values()) + relativedelta(months=1))
            while t <= today:
                active = [c for c, d in onsets.items() if d < t]
                if not active:
                    later = [d for d in onsets.values() if d >= t]
                    if not later:
                        break
                    t = min(later) + relativedelta(months=1)
                    continue
                interval = min(
                    self._followup_interval_months(c, relativedelta(t, onsets[c]).years * 12
                                                   + relativedelta(t, onsets[c]).months)
                    for c in active
                )
                schedule.append((t, "followup", active))
                t = self._jitter(t + relativedelta(months=interval), 10)

        encounters: list[Encounter] = []
        procedures: list[Procedure] = []
        for when, kind, payload in sorted(schedule, key=lambda x: x[0]):
            visit_age = relativedelta(when, dob).years
            if kind == "wellness":
                enc, procs = self._generate_wellness_encounter(
                    demographics, life_arc, weight_trajectory, when, visit_age,
                    social_history, medications,
                )
                procedures.extend(procs)
            elif kind == "diagnosis":
                enc = self._generate_diagnosis_encounter(
                    demographics, life_arc, weight_trajectory, payload, when, visit_age, medications)
            else:
                enc = self._generate_followup_encounter(
                    demographics, life_arc, weight_trajectory, payload, when, visit_age, medications)
            encounters.append(enc)

        encounters.extend(self._generate_acute_encounters(
            demographics, life_arc, weight_trajectory, window_start))
        encounters.sort(key=lambda e: e.date)
        return encounters, procedures

    # -- shared visit pieces ------------------------------------------------

    def _active_meds(self, medications: list[Medication], on: date) -> list[Medication]:
        return [m for m in medications
                if m.start_date <= on and (m.end_date is None or m.end_date >= on)]

    def _lab_context(self, demographics: Demographics, conditions: list[str],
                     meds: list[Medication], visit_age: int) -> LabContext:
        table = adult_meds.med_table()
        classes: set[str] = set()
        drugs: set[str] = set()
        for m in meds:
            for key, entry in table.items():
                if str(entry.get("rxnorm")) == m.code.code:
                    classes |= set(entry.get("classes", []))
                    drugs.add(key.split()[0])
        return LabContext(sex=demographics.sex_at_birth, age=visit_age,
                          conditions=set(conditions), drug_classes=classes, drugs=drugs)

    def _vitals(self, demographics: Demographics, life_arc: AdultLifeArc,
                weight_trajectory: WeightTrajectory, when: date, visit_age: int,
                conditions: list[str]) -> VitalSigns:
        """Office vitals. Height is fixed for the patient; weight follows the
        BMI trajectory, so serial weights are consistent."""
        height_m = self._height_m
        weight = weight_trajectory.get_weight_at_age(visit_age, height_m) + self._rng.uniform(-1.5, 1.5)
        if "hypertension" in conditions:
            # Treated hypertension: mostly near goal (<130/80, ACC/AHA 2017), sometimes above.
            systolic, diastolic = self._rng.randint(122, 146), self._rng.randint(74, 92)
        else:
            systolic, diastolic = self._rng.randint(106, 128), self._rng.randint(64, 80)
        hr = self._rng.randint(60, 86)
        if "atrial_fibrillation" in conditions:
            hr = self._rng.randint(64, 104)
        spo2 = self._rng.randint(96, 100)
        if "copd" in conditions:
            spo2 = self._rng.randint(90, 96)
        elif "heart_failure" in conditions:
            spo2 = self._rng.randint(93, 98)
        return VitalSigns(
            date=datetime.combine(when, datetime.min.time()).replace(hour=9),
            blood_pressure_systolic=systolic,
            blood_pressure_diastolic=diastolic,
            heart_rate=hr,
            respiratory_rate=self._rng.randint(12, 18),
            temperature_f=round(self._rng.uniform(97.5, 98.9), 1),
            oxygen_saturation=float(spo2),
            height_cm=round(height_m * 100, 1),
            weight_kg=round(weight, 1),
            bmi=round(weight / height_m ** 2, 1),
        )

    @staticmethod
    def _exam(findings: dict[str, str]) -> PhysicalExam:
        """Build a PhysicalExam from a findings dict.

        The templates use an 'extremities' key, which PhysicalExam does not
        have. Edema and pulse findings belong with the cardiovascular exam;
        other extremity findings (strength, tenderness) with musculoskeletal.
        """
        f = dict(findings)
        ext = f.pop("extremities", None)
        if ext:
            if re.search(r"edema|pulse", ext, re.I):
                cv = f.get("cardiovascular")
                f["cardiovascular"] = f"{cv}. Extremities: {ext}" if cv else f"Extremities: {ext}"
            else:
                f["musculoskeletal"] = ext
        allowed = set(PhysicalExam.model_fields)
        return PhysicalExam(**{k: v for k, v in f.items() if k in allowed})

    def _provider(self, specialty: str = "Internal Medicine") -> Provider:
        return Provider(name=self._generate_provider_name(), credentials="MD", specialty=specialty)

    @staticmethod
    def _clinic() -> Location:
        return Location(name="Primary Care Associates", type="Outpatient clinic")

    def _condition_code(self, condition: str) -> tuple[str, CodeableConcept]:
        code, display = ConditionRegistry.get().get_icd10(condition)
        return display, CodeableConcept(system="http://hl7.org/fhir/sid/icd-10-cm", code=code,
                                        display=display)

    def _monitoring_labs(self, conditions: list[str], meds: list[Medication]) -> list[str]:
        """Lab names relevant to the active conditions and drugs."""
        registry = ConditionRegistry.get()
        names: list[str] = []
        for c in conditions:
            names += registry.get_labs(c)
        med_names = " ".join(m.display_name.lower() for m in meds)
        # Drug monitoring: warfarin -> INR (CHEST 2012); lithium -> level, TSH,
        # renal function (CANMAT/ISBD 2018); methotrexate -> CBC, liver and
        # renal function (ACR 2008/2021).
        if "warfarin" in med_names:
            names.append("inr")
        if "lithium" in med_names:
            names += ["lithium_level", "tsh", "bmp"]
        if "methotrexate" in med_names:
            names += ["cbc", "cmp"]
        # Drug levels only for patients on the drug. INR stays for liver
        # disease (synthetic function) but is not drawn for an AF patient on
        # a DOAC.
        if "lithium" not in med_names:
            names = [n for n in names if n != "lithium_level"]
        if "warfarin" not in med_names and not ({"cirrhosis", "hepatitis_c"} & set(conditions)):
            names = [n for n in names if n != "inr"]
        seen: list[str] = []
        for n in names:
            if n not in seen:
                seen.append(n)
        return seen

    def _draw_labs(self, names: list[str], when: date, ctx: LabContext,
                   force: bool = False, interval_override: dict[str, int] | None = None):
        """Results for every due lab, sharing one set of values per draw.

        A lab is due when it has never been drawn or its minimum repeat
        interval has passed (adult_labs.LAB_MIN_INTERVAL_MONTHS). Imaging
        names return ImagingResults; names without a verified code are
        skipped.
        """
        values = LabValues(ctx, self._rng)
        labs, imaging = [], []
        # Bigger panels first so a CMP can stand in for a BMP or an eGFR.
        order = {"cmp": 0, "bmp": 1}
        for name in sorted(names, key=lambda x: order.get(x.lower(), 2)):
            n = name.lower()
            if not (adult_labs.is_known_lab(n) or adult_labs.is_imaging(n)):
                continue
            interval = (interval_override or {}).get(n, adult_labs.LAB_MIN_INTERVAL_MONTHS.get(n, 12))
            draws = [self._lab_last.get(x) for x in [n, *adult_labs.COVERED_BY.get(n, [])]]
            last = max((d for d in draws if d is not None), default=None)
            if not force and last is not None and when < last + relativedelta(months=interval):
                continue
            if force and any(self._lab_last.get(x) == when for x in adult_labs.COVERED_BY.get(n, [])):
                continue
            if adult_labs.is_imaging(n):
                img = adult_labs.build_imaging(n, ctx, when, self._rng)
                if img:
                    imaging.append(img)
            else:
                lab = adult_labs.build_lab(n, ctx, when, self._rng, values=values)
                if lab:
                    labs.append(lab)
            self._lab_last[n] = when
        return labs, imaging

    # -- wellness ---------------------------------------------------------

    def _generate_wellness_encounter(
        self,
        demographics: Demographics,
        life_arc: AdultLifeArc,
        weight_trajectory: WeightTrajectory,
        visit_date: date,
        visit_age: int,
        social_history: SocialHistory,
        medications: list[Medication],
    ) -> tuple[Encounter, list[Procedure]]:
        """Adult preventive visit with condition monitoring and USPSTF screening."""
        conditions = [c for c, d in life_arc.condition_onset_dates.items() if d <= visit_date]
        meds = self._active_meds(medications, visit_date)
        vitals = self._vitals(demographics, life_arc, weight_trajectory, visit_date, visit_age, conditions)

        exam_findings = {
            "general": "Well-appearing, in no acute distress",
            "heent": "Normocephalic, atraumatic, oropharynx clear",
            "cardiovascular": "Regular rate and rhythm, no murmurs",
            "respiratory": "Clear to auscultation bilaterally",
            "abdomen": "Soft, non-tender, non-distended",
            "extremities": "No edema, pulses 2+ bilaterally",
            "skin": "Warm, dry, no lesions",
        }
        if "heart_failure" in conditions:
            exam_findings["cardiovascular"] = "S3 gallop present, regular rhythm"
            exam_findings["extremities"] = "1+ pedal edema bilaterally"
        if "atrial_fibrillation" in conditions:
            exam_findings["cardiovascular"] = "Irregularly irregular rhythm, no murmurs"
        if "copd" in conditions:
            exam_findings["respiratory"] = "Diminished breath sounds, prolonged expiratory phase"
        if "obesity" in conditions:
            exam_findings["general"] = "Obese, well-appearing, in no acute distress"

        ctx = self._lab_context(demographics, conditions, meds, visit_age)
        labs, imaging = self._draw_labs(self._monitoring_labs(conditions, meds), visit_date, ctx)
        s_labs, s_imaging, procedures, orders, s_plan = self._uspstf_screenings(
            demographics, visit_date, visit_age, social_history, conditions, vitals, ctx)
        labs += s_labs
        imaging += s_imaging

        displays = [ConditionRegistry.get().get_icd10(c)[1] for c in conditions]
        assessment = [Assessment(
            diagnosis="Encounter for general adult medical examination", is_primary=True,
            code=CodeableConcept(system="http://hl7.org/fhir/sid/icd-10-cm", code="Z00.00",
                                 display="Encounter for general adult medical examination "
                                         "without abnormal findings"),
        )]
        for c in conditions:
            display, code = self._condition_code(c)
            assessment.append(Assessment(diagnosis=display, code=code))

        life_stage = get_life_stage(visit_age)
        plan = [PlanItem(category="medication", description="Continue current medications")
                if meds else PlanItem(category="other", description="No chronic medications")]
        plan += s_plan
        plan.append(PlanItem(category="follow-up",
                             description=f"Preventive visit in {life_stage.visit_frequency_months} months"))

        return Encounter(
            date=datetime.combine(visit_date, datetime.min.time()).replace(hour=9),
            type=EncounterType.ANNUAL_PHYSICAL,
            chief_complaint="Medicare Annual Wellness Visit" if visit_age >= 65 else "Annual physical examination",
            hpi=(f"{visit_age}-year-old presenting for a preventive visit. "
                 + (f"Chronic conditions: {', '.join(displays)}. " if displays else "No chronic conditions. ")
                 + "No new concerns today."),
            vital_signs=vitals,
            physical_exam=self._exam(exam_findings),
            lab_results=labs,
            imaging_results=imaging,
            procedures=procedures,
            orders=orders,
            assessment=assessment,
            plan=plan,
            provider=self._provider(),
            location=self._clinic(),
        ), procedures

    def _uspstf_screenings(self, demographics, visit_date, visit_age, social, conditions,
                           vitals, ctx):
        """Age- and risk-appropriate USPSTF screening with actual results.

        Sources (USPSTF recommendation statements):
          colorectal cancer 2021: ages 45-75 (colonoscopy every 10 years);
            surveillance intervals after polypectomy per USMSTF 2020
          breast cancer 2024: biennial mammography, women 40-74
          cervical cancer 2018: cytology every 3 y at 21-29; hrHPV every
            5 y at 30-65
          lung cancer 2021: annual LDCT at 50-80 with >= 20 pack-years,
            current smoker or quit within 15 years
          osteoporosis 2025: women 65+ (DXA)
          prediabetes/T2D 2021: ages 35-70 with overweight/obesity
            (every 3 years)
          hepatitis C 2020: once, ages 18-79; HIV 2019: once, ages 15-65
          depression 2023: all adults (PHQ-9)
        Lipid screening every 5 years from 20 follows ACC/AHA 2018.
        Uptake is ~70-80% per test (CDC NHIS); the rest are documented as
        declined.
        """
        today = self._today
        female = demographics.sex_at_birth == Sex.FEMALE
        labs, imaging, procedures, orders, plan = [], [], [], [], []

        def due(test: str, years: float | None) -> bool:
            nxt = self._screen_next.get(test)
            if nxt is not None:
                return visit_date >= nxt
            last = self._screen_last.get(test)
            if last is None:
                return True
            return years is not None and visit_date >= last + relativedelta(months=int(years * 12))

        def mark(test: str, when: date, next_years: float | None = None) -> None:
            self._screen_last[test] = when
            if next_years is not None:
                self._screen_next[test] = when + relativedelta(months=int(next_years * 12))
            else:
                self._screen_next.pop(test, None)

        def accepts() -> bool:
            return self._rng.random() < 0.8

        def later(lo: int, hi: int) -> date | None:
            d = visit_date + timedelta(days=self._rng.randint(lo, hi))
            return d if d <= today else None

        def order(name: str, loinc: str, kind: str) -> Order:
            return Order(type=kind, display_name=name,
                         code=CodeableConcept(system=adult_labs.LOINC, code=loinc, display=name),
                         status=OrderStatus.PENDING,
                         ordered_date=datetime.combine(visit_date, datetime.min.time()))

        # Depression (PHQ-9) every preventive visit.
        phq, _ = self._draw_labs(["phq9"], visit_date, ctx, force=True)
        labs += phq

        # Colorectal cancer
        if 45 <= visit_age <= 75 and due("colonoscopy", 10):
            if accepts():
                when = later(14, 60)
                if when:
                    r = self._rng.random()
                    if r < 0.75:
                        outcome, nxt = "No polyps. Repeat colonoscopy in 10 years.", 10
                    elif r < 0.95:
                        outcome, nxt = ("1-2 tubular adenomas < 10 mm removed. "
                                        "Repeat colonoscopy in 7-10 years."), 7
                    else:
                        outcome, nxt = ("3-4 tubular adenomas < 10 mm removed. "
                                        "Repeat colonoscopy in 3-5 years."), 3
                    proc = Procedure(
                        code=CodeableConcept(system=adult_labs.LOINC, code="18746-8",
                                             display="Colonoscopy study"),
                        display_name="Screening colonoscopy", performed_date=when,
                        reason="Colorectal cancer screening (USPSTF 2021)",
                        performer="Gastroenterology", outcome=outcome,
                    )
                    procedures.append(proc)
                    mark("colonoscopy", when, nxt)
                    plan.append(PlanItem(category="procedure",
                                         description=f"Screening colonoscopy {when:%Y-%m-%d}: {outcome}"))
                else:
                    orders.append(order("Screening colonoscopy", "18746-8", "procedure"))
                    plan.append(PlanItem(category="referral",
                                         description="Referred for screening colonoscopy"))
            else:
                plan.append(PlanItem(category="education", description=(
                    "Colorectal cancer screening offered (colonoscopy or annual FIT); declined")))
                mark("colonoscopy", visit_date, 1)

        # Breast cancer
        if female and 40 <= visit_age <= 74 and due("mammogram", 2):
            if accepts():
                when = later(3, 30)
                if when:
                    img = adult_labs.build_imaging("mammogram", ctx, when, self._rng)
                    imaging.append(img)
                    mark("mammogram", when, 2)
                    plan.append(PlanItem(category="order",
                                         description=f"Screening mammogram: {img.impression}"))
                else:
                    orders.append(order("Screening mammogram", "24606-6", "imaging"))
                    plan.append(PlanItem(category="order", description="Screening mammogram ordered"))
            else:
                plan.append(PlanItem(category="education",
                                     description="Biennial screening mammography offered; declined"))
                mark("mammogram", visit_date, 1)

        # Cervical cancer
        if female and 21 <= visit_age <= 65:
            if visit_age < 30 and due("pap", 3):
                labs += [adult_labs.build_lab("pap", ctx, visit_date, self._rng)]
                mark("pap", visit_date, 3)
                plan.append(PlanItem(category="procedure", description="Cervical cytology collected"))
            elif visit_age >= 30 and due("hpv", 5):
                hpv_pos = self._rng.random() < 0.08
                hpv = adult_labs.build_lab("hpv", ctx, visit_date, self._rng)
                if hpv_pos:
                    hpv = adult_labs._qualitative("hpv", True, hpv.resulted_date)
                labs.append(hpv)
                if hpv_pos:
                    # ASCCP 2019: reflex cytology on a positive primary HPV test.
                    labs.append(adult_labs._qualitative("pap", self._rng.random() < 0.3,
                                                        hpv.resulted_date))
                    mark("hpv", visit_date, 1)
                else:
                    mark("hpv", visit_date, 5)
                plan.append(PlanItem(category="procedure",
                                     description="Primary HPV test collected for cervical cancer screening"))

        # Lung cancer
        tob = social.tobacco
        if tob and tob.status in ("current", "former") and tob.quantity and 50 <= visit_age <= 80:
            pack_years = float(tob.quantity.split()[0])
            quit_ok = tob.status == "current" or (
                tob.quit_date is not None and (visit_date - tob.quit_date).days <= 15 * 365)
            if pack_years >= 20 and quit_ok and due("ldct", 1):
                when = later(5, 30)
                if when and accepts():
                    img = adult_labs.build_imaging("ldct", ctx, when, self._rng)
                    imaging.append(img)
                    mark("ldct", when, 1)
                    plan.append(PlanItem(category="order",
                                         description=f"Low-dose CT lung cancer screening: {img.impression}"))
                elif when is None:
                    orders.append(order("Low-dose CT chest (lung cancer screening)", "79086-5", "imaging"))
                    plan.append(PlanItem(category="order", description="Low-dose CT for lung cancer screening ordered"))
                else:
                    plan.append(PlanItem(category="education",
                                         description="Lung cancer screening (LDCT) offered; declined"))
                    mark("ldct", visit_date, 1)

        # Osteoporosis
        if (female and visit_age >= 65) or "osteoporosis" in conditions:
            if due("dexa", 2 if "osteoporosis" in conditions else None):
                when = later(7, 45)
                if when:
                    img = adult_labs.build_imaging("dexa_scan", ctx, when, self._rng)
                    imaging.append(img)
                    # Repeat in 2 years if low bone mass; USPSTF gives no
                    # interval after a normal DXA.
                    normal = img.impression.startswith("Normal")
                    mark("dexa", when, None if normal else 2)
                    plan.append(PlanItem(category="order", description=f"DXA: {img.impression}"))
                else:
                    orders.append(order("DXA bone density", "38269-7", "imaging"))
                    plan.append(PlanItem(category="order", description="DXA bone density ordered"))

        # Prediabetes / type 2 diabetes
        diabetic = bool({"type2_diabetes", "prediabetes"} & set(conditions))
        if (not diabetic and 35 <= visit_age <= 70 and (vitals.bmi or 0) >= 25
                and due("diabetes_screen", 3)):
            if self._lab_last.get("hba1c") is None or visit_date >= self._lab_last["hba1c"] + relativedelta(months=12):
                got, _ = self._draw_labs(["hba1c"], visit_date, ctx, force=True)
                labs += got
            mark("diabetes_screen", visit_date, 3)
            plan.append(PlanItem(category="order", description="Hemoglobin A1c for diabetes screening"))

        # Lipids
        lipid_monitored = bool({"hyperlipidemia", "coronary_artery_disease", "type2_diabetes",
                                "peripheral_artery_disease"} & set(conditions))
        if not lipid_monitored and visit_age >= 20 and due("lipids", 5):
            last = self._lab_last.get("lipid_panel")
            if last is None or visit_date >= last + relativedelta(years=5):
                got, _ = self._draw_labs(["lipid_panel"], visit_date, ctx, force=True)
                labs += got
            mark("lipids", visit_date, 5)
            plan.append(PlanItem(category="order", description="Lipid panel for cardiovascular risk assessment"))

        # One-time infectious screening
        if 18 <= visit_age <= 79 and "hcv" not in self._screen_last and "hepatitis_c" not in conditions:
            labs.append(adult_labs.build_lab("hcv_ab", ctx, visit_date, self._rng))
            mark("hcv", visit_date)
            plan.append(PlanItem(category="order", description="One-time hepatitis C antibody screening"))
        if 18 <= visit_age <= 65 and "hiv" not in self._screen_last and "hiv" not in conditions:
            labs.append(adult_labs.build_lab("hiv_screen", ctx, visit_date, self._rng))
            mark("hiv", visit_date)
            plan.append(PlanItem(category="order", description="One-time HIV screening"))

        return labs, imaging, procedures, orders, plan

    # -- problem visits ---------------------------------------------------

    def _generate_diagnosis_encounter(self, demographics, life_arc, weight_trajectory,
                                      condition, when, visit_age, medications) -> Encounter:
        """The visit at which a chronic condition is first diagnosed."""
        conditions = [c for c, d in life_arc.condition_onset_dates.items() if d <= when]
        meds_started = [m for m in medications
                        if m.indication == self._condition_code(condition)[0]
                        and abs((m.start_date - when).days) <= 60]
        display, code = self._condition_code(condition)
        ctx = self._lab_context(demographics, conditions, self._active_meds(medications, when), visit_age)
        labs, imaging = self._draw_labs(ConditionRegistry.get().get_labs(condition), when, ctx, force=True)
        sex = "man" if demographics.sex_at_birth == Sex.MALE else "woman"
        start_text = (f"Started {', '.join(m.display_name for m in meds_started)}."
                      if meds_started else "Lifestyle measures first; no medication started.")
        return Encounter(
            date=datetime.combine(when, datetime.min.time()).replace(hour=10),
            type=EncounterType.CHRONIC_FOLLOWUP,
            chief_complaint=f"Evaluation: {display}",
            hpi=(f"{visit_age}-year-old {sex} seen for evaluation. History, examination and "
                 f"initial workup support a new diagnosis of {display.lower()}."),
            vital_signs=self._vitals(demographics, life_arc, weight_trajectory, when, visit_age, conditions),
            physical_exam=self._exam(self._generate_condition_physical_exam(None, EncounterType.CHRONIC_FOLLOWUP)),
            lab_results=labs,
            imaging_results=imaging,
            assessment=[Assessment(diagnosis=f"{display}, new diagnosis", code=code, is_primary=True)],
            plan=[
                PlanItem(category="medication", description=start_text),
                PlanItem(category="education", description=f"Counseled on {display.lower()}"),
                PlanItem(category="follow-up", description="Follow up in 4-6 weeks to assess response"),
            ],
            provider=self._provider(),
            location=self._clinic(),
        )

    def _generate_followup_encounter(self, demographics, life_arc, weight_trajectory,
                                     conditions, when, visit_age, medications) -> Encounter:
        """One chronic-care visit addressing every active condition."""
        meds = self._active_meds(medications, when)
        ctx = self._lab_context(demographics, conditions, meds, visit_age)
        override = {"cbc": 3, "cmp": 3} if any("methotrexate" in m.display_name.lower() for m in meds) else None
        labs, imaging = self._draw_labs(self._monitoring_labs(conditions, meds), when, ctx,
                                        interval_override=override)
        assessments, statuses = [], []
        for i, c in enumerate(conditions):
            display, code = self._condition_code(c)
            stable = self._rng.random() > 0.2
            statuses.append(f"{display.lower()} {'stable' if stable else 'not at goal'}")
            assessments.append(Assessment(
                diagnosis=f"{display} - {'stable on current therapy' if stable else 'suboptimal control, adjustment needed'}",
                code=code, is_primary=(i == 0)))
        med_text = ", ".join(m.display_name for m in meds) if meds else "no chronic medications"
        adherence = self._rng.choice(["Reports taking medications as prescribed",
                                      "Reports occasional missed doses",
                                      "Reports good adherence and no side effects"])
        return Encounter(
            date=datetime.combine(when, datetime.min.time()).replace(hour=14),
            type=EncounterType.CHRONIC_FOLLOWUP,
            chief_complaint="Chronic disease follow-up",
            hpi=(f"Returns for follow-up of {'; '.join(statuses)}. Current medications: {med_text}. "
                 f"{adherence}."),
            vital_signs=self._vitals(demographics, life_arc, weight_trajectory, when, visit_age, conditions),
            physical_exam=self._exam(self._generate_condition_physical_exam(None, EncounterType.CHRONIC_FOLLOWUP)),
            lab_results=labs,
            imaging_results=imaging,
            assessment=assessments,
            plan=[
                PlanItem(category="medication", description=f"Continue {med_text}" if meds else "No changes"),
                PlanItem(category="education", description="Lifestyle modifications reinforced"),
                PlanItem(category="follow-up", description="Follow up per chronic care schedule"),
            ],
            provider=self._provider(),
            location=self._clinic(),
        )

    def _generate_acute_encounters(
        self,
        demographics: Demographics,
        life_arc: AdultLifeArc,
        weight_trajectory: WeightTrajectory,
        window_start: date,
    ) -> list[Encounter]:
        """Acute illness visits inside the history window, never after today."""
        encounters = []
        dob = demographics.date_of_birth
        today = self._today
        # Acute visit frequency: 0.5-1.5 per year depending on health
        visits_per_year = 0.5 if life_arc.health_trajectory == "healthy" else 1.5
        registry = ConditionRegistry.get()
        season_map = {1: "winter", 2: "winter", 3: "spring", 4: "spring",
                      5: "spring", 6: "summer", 7: "summer", 8: "summer",
                      9: "fall", 10: "fall", 11: "fall", 12: "winter"}

        years = max(0, relativedelta(today, window_start).years)
        for year in range(years):
            if self._rng.random() >= visits_per_year:
                continue
            visit_date = today - timedelta(days=year * 365 + self._rng.randint(1, 364))
            if visit_date < window_start:
                continue
            visit_age = relativedelta(visit_date, dob).years
            season = season_map.get(visit_date.month, "winter")

            weighted_conditions = registry.get_weighted_acute_conditions(visit_age, season)
            if weighted_conditions:
                keys, weights = zip(*weighted_conditions)
                condition_key = self._rng.choices(keys, weights=weights)[0]
            else:
                condition_key = "upper_respiratory_infection"

            cond = registry.get_acute_condition(condition_key)
            chief_complaint = cond.get("display", condition_key.replace("_", " ").title()) if cond else condition_key.replace("_", " ").title()
            symptoms = self._select_symptoms(condition_key)
            hpi = (f"Presents with {', '.join(symptoms[:3]) if symptoms else 'acute symptoms'} "
                   f"for the past {self._rng.randint(1, 5)} days.")

            conditions = [c for c, d in life_arc.condition_onset_dates.items() if d <= visit_date]
            vitals = self._vitals(demographics, life_arc, weight_trajectory, visit_date, visit_age, conditions)
            vitals = self._apply_vitals_impact(vitals, condition_key, EncounterType.ACUTE_ILLNESS)
            physical_exam = self._exam(
                self._generate_condition_physical_exam(condition_key, EncounterType.ACUTE_ILLNESS))
            ctx = LabContext(sex=demographics.sex_at_birth, age=visit_age,
                             conditions=set(conditions), drug_classes=set())
            lab_results, imaging = self._generate_labs(condition_key, visit_date,
                                                       EncounterType.ACUTE_ILLNESS, ctx)

            treatment = cond.get('treatment', {}) if cond else {}
            meds = []
            for key in ['symptomatic', 'if_bacterial', 'antibiotics', 'antiviral', 'medications']:
                meds.extend(treatment.get(key, []))
            treatment_desc = f"Treatment: {', '.join(meds[:3])}" if meds else "Supportive care"
            followup_days = self._rng.randint(3, 7)
            assessment_text = chief_complaint
            if symptoms:
                assessment_text += f". {', '.join(symptoms[:2])}"
            acute_code = None
            if cond and cond.get("icd10"):
                acute_code = CodeableConcept(system="http://hl7.org/fhir/sid/icd-10-cm",
                                             code=str(cond["icd10"]), display=chief_complaint)

            encounters.append(Encounter(
                date=datetime.combine(visit_date, datetime.min.time()).replace(hour=11),
                type=EncounterType.ACUTE_ILLNESS,
                chief_complaint=chief_complaint,
                hpi=hpi,
                vital_signs=vitals,
                physical_exam=physical_exam,
                lab_results=lab_results,
                imaging_results=imaging,
                assessment=[Assessment(diagnosis=assessment_text, code=acute_code, is_primary=True)],
                plan=[
                    PlanItem(category="medication", description=treatment_desc),
                    PlanItem(category="follow-up", description=f"Follow up if symptoms worsen or no improvement in {followup_days} days")
                ],
                provider=self._provider("Family Medicine"),
                location=self._clinic(),
            ))
        return encounters

    def _create_problem_list(
        self, demographics: Demographics, life_arc: AdultLifeArc
    ) -> list[Condition]:
        """Problem list from the life arc. Every condition has a real ICD-10 code."""
        problems = []
        for cond_name in life_arc.chronic_conditions:
            display, code = self._condition_code(cond_name)
            problems.append(Condition(
                display_name=display,
                code=code,
                clinical_status=ConditionStatus.ACTIVE,
                onset_date=life_arc.condition_onset_dates[cond_name],
                recorded_date=life_arc.condition_onset_dates[cond_name],
            ))
        return problems

    def _generate_medications(
        self, life_arc: AdultLifeArc, demographics: Demographics,
        weight_trajectory: WeightTrajectory | None = None,
    ) -> list[Medication]:
        """Medications for each chronic condition from the verified table.

        No two active drugs share an ingredient or therapeutic class
        (adult_meds.TherapyState), so a statin is never doubled and
        methotrexate appears once, with folic acid.
        """
        registry = ConditionRegistry.get()
        state = adult_meds.TherapyState()
        medications = []
        for condition in life_arc.chronic_conditions:
            specs = adult_meds.select_for_condition(
                condition, registry.get_meds(condition), state, demographics.sex_at_birth, self._rng,
                first_line=(registry.get_condition(condition) or {}).get("first_line"))
            if not specs:
                continue
            display = self._condition_code(condition)[0]
            onset = life_arc.condition_onset_dates[condition]
            weight = None
            if weight_trajectory is not None:
                weight = weight_trajectory.get_weight_at_age(demographics.age_years, self._height_m)
            for spec in specs:
                start = onset + timedelta(days=self._rng.randint(0, 45))
                medications.append(adult_meds.to_medication(spec, display, start, self._today, weight))
        return medications

    def _determine_complexity(self, life_arc: AdultLifeArc) -> ComplexityTier:
        """Determine complexity tier from life arc."""
        
        num_conditions = len(life_arc.chronic_conditions)
        
        if num_conditions == 0:
            return ComplexityTier.TIER_0
        elif num_conditions == 1:
            return ComplexityTier.TIER_1
        elif num_conditions <= 3:
            return ComplexityTier.TIER_2
        else:
            return ComplexityTier.TIER_3
    
    # ==========================================================================
    # Helper Methods
    # ==========================================================================
    
    def _generate_first_name(self, sex: Sex, age: int) -> str:
        """Generate age-appropriate first name."""
        
        # Names popular in different decades
        if age >= 70:
            male_names = ["Robert", "William", "James", "Richard", "Donald", "Thomas", "Charles", "John"]
            female_names = ["Mary", "Patricia", "Barbara", "Linda", "Elizabeth", "Dorothy", "Margaret", "Susan"]
        elif age >= 50:
            male_names = ["Michael", "David", "John", "James", "Robert", "Mark", "Steven", "Paul"]
            female_names = ["Jennifer", "Lisa", "Michelle", "Susan", "Karen", "Nancy", "Donna", "Laura"]
        elif age >= 35:
            male_names = ["Michael", "Christopher", "Matthew", "Joshua", "David", "Daniel", "Andrew", "Justin"]
            female_names = ["Jessica", "Ashley", "Amanda", "Sarah", "Stephanie", "Jennifer", "Nicole", "Melissa"]
        else:
            male_names = ["Liam", "Noah", "Oliver", "Elijah", "James", "William", "Benjamin", "Lucas"]
            female_names = ["Olivia", "Emma", "Ava", "Sophia", "Isabella", "Mia", "Charlotte", "Amelia"]
        
        names = male_names if sex == Sex.MALE else female_names
        return self._rng.choice(names)
    
    def _generate_last_name(self) -> str:
        """Generate a last name."""
        last_names = [
            "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
            "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
            "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
            "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
            "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
        ]
        return self._rng.choice(last_names)
    
    def _generate_provider_name(self) -> str:
        """Generate a provider name."""
        first_names = ["Sarah", "Michael", "Jennifer", "David", "Emily", "Robert", "Lisa", "James"]
        last_names = ["Chen", "Patel", "Williams", "Johnson", "Kim", "Anderson", "Martinez", "Thompson"]
        return f"Dr. {self._rng.choice(first_names)} {self._rng.choice(last_names)}"
    
    def _get_emergency_contact_relationship(self, age: int) -> str:
        """Get appropriate emergency contact relationship based on age."""
        if age < 30:
            return self._rng.choice(["Parent", "Sibling", "Spouse"])
        elif age < 65:
            return self._rng.choices(["Spouse", "Sibling", "Adult child", "Parent"], weights=[60, 15, 15, 10])[0]
        else:
            return self._rng.choices(["Spouse", "Adult child", "Sibling"], weights=[40, 45, 15])[0]
    
    def _generate_marital_status(self, age: int) -> str:
        """Generate marital status based on age."""
        if age < 25:
            return self._rng.choices(
                ["Single", "Married", "Domestic partnership"],
                weights=[80, 15, 5]
            )[0]
        elif age < 40:
            return self._rng.choices(
                ["Single", "Married", "Divorced", "Domestic partnership"],
                weights=[35, 50, 10, 5]
            )[0]
        elif age < 65:
            return self._rng.choices(
                ["Single", "Married", "Divorced", "Widowed"],
                weights=[15, 55, 20, 10]
            )[0]
        else:
            return self._rng.choices(
                ["Married", "Widowed", "Divorced", "Single"],
                weights=[40, 35, 15, 10]
            )[0]
    
    def _generate_number_of_children(self, age: int, marital_status: str) -> int:
        """Generate number of children based on age and marital status."""
        if age < 25:
            return self._rng.choices([0, 1], weights=[85, 15])[0]
        elif age < 35:
            return self._rng.choices([0, 1, 2], weights=[40, 35, 25])[0]
        elif age < 50:
            return self._rng.choices([0, 1, 2, 3, 4], weights=[20, 25, 35, 15, 5])[0]
        else:
            return self._rng.choices([0, 1, 2, 3, 4], weights=[15, 20, 40, 20, 5])[0]

    # ==========================================================================
    # Condition-Aware Generation Methods
    # ==========================================================================

    def _get_condition_key(self, reason: str | None) -> str | None:
        """Map encounter reason to condition YAML key."""
        if not reason:
            return None

        # Normalize the reason text
        normalized = reason.lower().replace(" ", "_").replace("-", "_")

        # Direct mappings for common acute conditions
        mappings = {
            "upper_respiratory_infection": "upper_respiratory_infection",
            "uri": "upper_respiratory_infection",
            "common_cold": "upper_respiratory_infection",
            "sinusitis": "acute_sinusitis",
            "acute_sinusitis": "acute_sinusitis",
            "pharyngitis": "acute_pharyngitis",
            "sore_throat": "acute_pharyngitis",
            "bronchitis": "acute_bronchitis",
            "acute_bronchitis": "acute_bronchitis",
            "influenza": "influenza",
            "flu": "influenza",
            "covid": "covid19",
            "covid_19": "covid19",
            "pneumonia": "pneumonia",
            "gastroenteritis": "gastroenteritis",
            "gi_symptoms": "gastroenteritis",
            "food_poisoning": "food_poisoning",
            "urinary_symptoms": "urinary_tract_infection",
            "uti": "urinary_tract_infection",
            "urinary_tract_infection": "urinary_tract_infection",
            "back_pain": "low_back_pain",
            "low_back_pain": "low_back_pain",
            "headache": "headache",
            "migraine": "migraine",
            "chest_pain": "chest_pain",
            "chest_pain_evaluation": "chest_pain",
            "skin_rash": "dermatitis",
            "rash": "dermatitis",
            "joint_pain": "arthralgia",
            "shortness_of_breath": "dyspnea",
            "dyspnea": "dyspnea",
        }

        # Check direct mapping
        for pattern, key in mappings.items():
            if pattern in normalized:
                return key

        return None

    def _apply_vitals_impact(
        self,
        vitals: VitalSigns,
        condition_key: str | None,
        encounter_type: EncounterType
    ) -> VitalSigns:
        """Apply condition-specific modifications to vitals."""
        if not condition_key or encounter_type not in (
            EncounterType.ACUTE_ILLNESS,
            EncounterType.URGENT_CARE,
            EncounterType.ED_VISIT
        ):
            return vitals

        # Get vitals impact from condition registry
        registry = ConditionRegistry.get()
        cond = registry.get_acute_condition(condition_key)
        if not cond:
            return vitals

        vitals_impact = cond.get('vitals_impact', {})
        if not vitals_impact:
            # Apply sensible defaults based on condition type
            vitals_impact = self._get_default_vitals_impact(condition_key)

        # Apply temperature range
        temp_range = vitals_impact.get('temp_f')
        if temp_range and len(temp_range) == 2:
            vitals.temperature_f = round(self._rng.uniform(temp_range[0], temp_range[1]), 1)

        # Apply heart rate multiplier
        hr_mult = vitals_impact.get('hr_multiplier', 1.0)
        if vitals.heart_rate:
            vitals.heart_rate = int(vitals.heart_rate * hr_mult)

        # Apply respiratory rate multiplier
        rr_mult = vitals_impact.get('rr_multiplier', 1.0)
        if vitals.respiratory_rate:
            vitals.respiratory_rate = int(vitals.respiratory_rate * rr_mult)

        # Apply SpO2 impact
        spo2_min = vitals_impact.get('spo2_min')
        if spo2_min is not None and vitals.oxygen_saturation:
            max_spo2 = min(100, int(vitals.oxygen_saturation) + 2)
            vitals.oxygen_saturation = float(self._rng.randint(spo2_min, max_spo2))

        # Apply BP impact for certain conditions
        bp_impact = vitals_impact.get('bp_impact')
        if bp_impact and vitals.blood_pressure_systolic:
            vitals.blood_pressure_systolic += bp_impact.get('systolic_delta', 0)
            vitals.blood_pressure_diastolic += bp_impact.get('diastolic_delta', 0)

        return vitals

    def _get_default_vitals_impact(self, condition_key: str) -> dict:
        """Get sensible default vitals impact for common conditions."""
        defaults = {
            # Respiratory infections - fever, elevated HR/RR
            "upper_respiratory_infection": {"temp_f": [99.0, 101.0], "hr_multiplier": 1.05, "rr_multiplier": 1.0},
            "acute_sinusitis": {"temp_f": [99.0, 101.5], "hr_multiplier": 1.05, "rr_multiplier": 1.0},
            "acute_pharyngitis": {"temp_f": [100.0, 102.0], "hr_multiplier": 1.08, "rr_multiplier": 1.0},
            "acute_bronchitis": {"temp_f": [99.0, 101.0], "hr_multiplier": 1.05, "rr_multiplier": 1.1},
            "influenza": {"temp_f": [101.0, 103.5], "hr_multiplier": 1.15, "rr_multiplier": 1.05},
            "covid19": {"temp_f": [99.5, 102.5], "hr_multiplier": 1.10, "rr_multiplier": 1.1, "spo2_min": 92},
            "pneumonia": {"temp_f": [101.0, 103.5], "hr_multiplier": 1.15, "rr_multiplier": 1.25, "spo2_min": 90},

            # GI conditions
            "gastroenteritis": {"temp_f": [99.0, 101.0], "hr_multiplier": 1.10, "rr_multiplier": 1.0},
            "food_poisoning": {"temp_f": [99.0, 100.5], "hr_multiplier": 1.08, "rr_multiplier": 1.0},

            # UTI
            "urinary_tract_infection": {"temp_f": [99.5, 102.0], "hr_multiplier": 1.08, "rr_multiplier": 1.0},

            # Pain conditions - elevated HR due to pain
            "low_back_pain": {"hr_multiplier": 1.05},
            "headache": {"hr_multiplier": 1.0},
            "migraine": {"hr_multiplier": 1.05},
            "chest_pain": {"hr_multiplier": 1.15, "bp_impact": {"systolic_delta": 10, "diastolic_delta": 5}},

            # Respiratory distress
            "dyspnea": {"hr_multiplier": 1.15, "rr_multiplier": 1.3, "spo2_min": 91},
            "asthma_exacerbation": {"hr_multiplier": 1.15, "rr_multiplier": 1.3, "spo2_min": 90},
            "copd_exacerbation": {"hr_multiplier": 1.10, "rr_multiplier": 1.25, "spo2_min": 88},
        }
        return defaults.get(condition_key, {})

    def _select_symptoms(self, condition_key: str) -> list[str]:
        """Select symptoms based on condition."""
        registry = ConditionRegistry.get()
        cond = registry.get_acute_condition(condition_key)

        if cond and 'presentation' in cond:
            symptoms_def = cond['presentation'].get('symptoms', [])
            selected = []
            for s in symptoms_def:
                if isinstance(s, str):
                    selected.append(s)
                elif isinstance(s, dict):
                    name = s.get('name', '')
                    probability = s.get('probability', 1.0)
                    description = s.get('description', name)
                    if self._rng.random() < probability:
                        selected.append(description if description else name)
            return selected

        # Use defaults if no presentation data
        return self._get_default_symptoms(condition_key)

    def _get_default_symptoms(self, condition_key: str) -> list[str]:
        """Get default symptoms for common conditions."""
        defaults = {
            "upper_respiratory_infection": ["nasal congestion", "rhinorrhea", "sore throat", "mild cough"],
            "acute_sinusitis": ["facial pain/pressure", "nasal congestion", "purulent nasal discharge", "headache"],
            "acute_pharyngitis": ["sore throat", "odynophagia", "fever", "cervical lymphadenopathy"],
            "acute_bronchitis": ["productive cough", "chest discomfort", "low-grade fever", "fatigue"],
            "influenza": ["high fever", "myalgia", "headache", "dry cough", "fatigue", "chills"],
            "covid19": ["fever", "cough", "fatigue", "myalgia", "loss of taste/smell"],
            "pneumonia": ["fever", "productive cough", "dyspnea", "pleuritic chest pain"],
            "gastroenteritis": ["nausea", "vomiting", "diarrhea", "abdominal cramps"],
            "food_poisoning": ["acute nausea", "vomiting", "diarrhea", "abdominal pain"],
            "urinary_tract_infection": ["dysuria", "urinary frequency", "urgency", "suprapubic pain"],
            "low_back_pain": ["lumbar pain", "muscle stiffness", "limited range of motion"],
            "headache": ["bilateral head pain", "pressure sensation"],
            "migraine": ["unilateral throbbing headache", "photophobia", "nausea"],
            "chest_pain": ["substernal chest discomfort", "dyspnea on exertion"],
            "dyspnea": ["shortness of breath", "difficulty breathing", "air hunger"],
        }
        return defaults.get(condition_key, ["presenting symptoms as reported"])

    def _generate_condition_physical_exam(
        self,
        condition_key: str | None,
        encounter_type: EncounterType
    ) -> dict[str, str]:
        """Generate condition-specific physical exam findings."""
        # Default normal findings
        findings = {
            'general': "Alert and oriented, appears comfortable",
            'heent': "Normocephalic, atraumatic, PERRLA, EOMI",
            'cardiovascular': "Regular rate and rhythm, no murmurs, rubs, or gallops",
            'respiratory': "Clear to auscultation bilaterally, no wheezes or crackles",
            'abdomen': "Soft, non-tender, non-distended, normoactive bowel sounds",
            'extremities': "No edema, normal strength and sensation",
            'skin': "Warm, dry, no rashes",
        }

        if not condition_key or encounter_type not in (
            EncounterType.ACUTE_ILLNESS,
            EncounterType.URGENT_CARE,
            EncounterType.ED_VISIT
        ):
            return findings

        # Get condition-specific exam findings
        registry = ConditionRegistry.get()
        cond = registry.get_acute_condition(condition_key)

        if cond and 'presentation' in cond:
            exam_def = cond['presentation'].get('physical_exam', [])
            for f in exam_def:
                if isinstance(f, dict):
                    system = f.get('system', '').lower()
                    finding = f.get('finding', '')
                    probability = f.get('probability', 1.0)
                    if system and finding and self._rng.random() < probability:
                        findings[system] = finding
            return findings

        # Use defaults
        return self._get_default_physical_exam(condition_key, findings)

    def _get_default_physical_exam(self, condition_key: str, base_findings: dict) -> dict:
        """Get default physical exam findings for common conditions."""
        findings = base_findings.copy()

        condition_exams = {
            "upper_respiratory_infection": {
                "heent": "Nasal mucosa erythematous with clear discharge, oropharynx mildly erythematous",
                "respiratory": "Clear to auscultation bilaterally"
            },
            "acute_sinusitis": {
                "heent": "Tenderness over maxillary/frontal sinuses, purulent nasal discharge, posterior pharyngeal drainage",
            },
            "acute_pharyngitis": {
                "heent": "Oropharynx erythematous with tonsillar enlargement, anterior cervical lymphadenopathy",
            },
            "acute_bronchitis": {
                "respiratory": "Scattered rhonchi bilaterally, no wheezes",
                "general": "Mild respiratory distress"
            },
            "influenza": {
                "general": "Ill-appearing, febrile",
                "heent": "Conjunctival injection, erythematous oropharynx",
                "respiratory": "Clear to auscultation"
            },
            "covid19": {
                "general": "Mildly ill-appearing",
                "respiratory": "Clear to auscultation or scattered crackles",
            },
            "pneumonia": {
                "general": "Ill-appearing, using accessory muscles",
                "respiratory": "Decreased breath sounds with crackles in affected lobe, egophony present",
            },
            "gastroenteritis": {
                "abdomen": "Mild diffuse tenderness, hyperactive bowel sounds, no rebound or guarding",
            },
            "urinary_tract_infection": {
                "abdomen": "Suprapubic tenderness, no CVA tenderness",
            },
            "low_back_pain": {
                "extremities": "Paravertebral muscle tenderness, limited lumbar flexion, negative straight leg raise, normal strength and reflexes",
            },
            "chest_pain": {
                "cardiovascular": "Regular rate and rhythm, no murmurs, chest wall non-tender",
                "respiratory": "Clear to auscultation bilaterally",
            },
            "dyspnea": {
                "general": "Appears dyspneic, speaking in short sentences",
                "respiratory": "Tachypneic, using accessory muscles, diminished breath sounds or wheezes",
            },
        }

        if condition_key in condition_exams:
            for system, finding in condition_exams[condition_key].items():
                findings[system] = finding

        return findings

    def _generate_labs(
        self,
        condition_key: str | None,
        encounter_date: date,
        encounter_type: EncounterType,
        ctx: LabContext,
    ) -> tuple[list, list]:
        """Acute workup from the condition's ``workup`` list.

        Returns (lab results, imaging results). Labs without a verified LOINC
        code are skipped; imaging is reported as an ImagingResult.
        """
        if not condition_key or encounter_type not in (
            EncounterType.ACUTE_ILLNESS,
            EncounterType.URGENT_CARE,
            EncounterType.ED_VISIT,
        ):
            return [], []
        cond = ConditionRegistry.get().get_acute_condition(condition_key)
        workup: list[str] = []
        if cond:
            workup_def = cond.get('workup', 'clinical')
            if isinstance(workup_def, list):
                workup = workup_def
            elif workup_def and workup_def not in ('clinical', 'none'):
                workup = [workup_def]
        labs, imaging = [], []
        values = LabValues(ctx, self._rng)
        for name in workup:
            if adult_labs.is_imaging(name):
                img = adult_labs.build_imaging(name, ctx, encounter_date, self._rng, condition_key)
                if img:
                    imaging.append(img)
            else:
                lab = adult_labs.build_lab(name, ctx, encounter_date, self._rng, values=values,
                                           acute_condition=condition_key)
                if lab:
                    labs.append(lab)
        return labs, imaging

    # ==========================================================================
    # Timeline and Disease Arc Generation
    # ==========================================================================

    _disease_arcs_cache: dict | None = None

    @classmethod
    def _load_disease_arcs(cls) -> dict:
        """Load adult disease arc definitions from YAML."""
        if cls._disease_arcs_cache is not None:
            return cls._disease_arcs_cache

        arcs_path = Path(__file__).parent / "adult_disease_arcs.yaml"
        if arcs_path.exists():
            with open(arcs_path) as f:
                cls._disease_arcs_cache = yaml.safe_load(f)
        else:
            cls._disease_arcs_cache = {}

        return cls._disease_arcs_cache

    def generate_timeline(
        self,
        patient: Patient,
        arc_keys: list[str] | None = None,
    ) -> PatientTimeline:
        """Generate timeline with snapshots and disease arcs for an adult patient.

        Args:
            patient: The patient to generate timeline for
            arc_keys: Specific arc keys to include, or None to auto-detect

        Returns:
            PatientTimeline with snapshots and disease arcs
        """
        arc_defs = self._load_disease_arcs()
        current_age_months = patient.demographics.age_months

        # Determine applicable arcs
        if arc_keys:
            active_arc_keys = [k for k in arc_keys if k in arc_defs]
        else:
            active_arc_keys = self._infer_disease_arcs(patient, arc_defs)

        # Build DiseaseArc objects
        disease_arcs = []
        for arc_key in active_arc_keys:
            arc_def = arc_defs.get(arc_key, {})
            if not arc_def:
                continue

            stages = []
            current_stage = 0
            for i, stage_def in enumerate(arc_def.get("stages", [])):
                # Determine if patient has progressed to this stage
                condition_key = stage_def.get("condition_key", "")
                has_condition = any(
                    c.display_name.lower().replace(" ", "_") == condition_key
                    or condition_key in c.display_name.lower()
                    for c in patient.problem_list
                )

                stage = ArcStage(
                    condition_key=condition_key,
                    display_name=stage_def.get("display_name", condition_key),
                    typical_age_range=tuple(stage_def.get("typical_age_range", [0, 1200])),
                    symptoms=stage_def.get("symptoms", []),
                    treatments=stage_def.get("treatments", []),
                    transition_triggers=stage_def.get("transition_triggers", []),
                )
                stages.append(stage)

                if has_condition:
                    current_stage = i

            arc = DiseaseArc(
                name=arc_def.get("name", arc_key),
                description=arc_def.get("description", ""),
                stages=stages,
                current_stage_index=current_stage,
                clinical_pearls=arc_def.get("clinical_pearls", []),
                references=arc_def.get("references", []),
            )
            disease_arcs.append(arc)

        # Generate timeline snapshots
        snapshots = self._generate_timeline_snapshots(patient, disease_arcs, current_age_months)

        return PatientTimeline(
            patient_id=patient.id,
            current_age_months=current_age_months,
            snapshots=snapshots,
            disease_arcs=disease_arcs,
        )

    def _infer_disease_arcs(self, patient: Patient, arc_defs: dict) -> list[str]:
        """Infer which disease arcs apply to this patient based on conditions."""
        applicable_arcs = []
        condition_names = set()

        for c in patient.problem_list:
            name = c.display_name.lower().replace(" ", "_")
            condition_names.add(name)
            # Also check common variations
            condition_names.add(name.replace("_", ""))

        for arc_key, arc_def in arc_defs.items():
            stages = arc_def.get("stages", [])
            for stage in stages:
                cond_key = stage.get("condition_key", "").lower()
                # Check if patient has any condition in this arc
                if cond_key in condition_names or any(cond_key in cn for cn in condition_names):
                    applicable_arcs.append(arc_key)
                    break

        return applicable_arcs

    def _generate_timeline_snapshots(
        self,
        patient: Patient,
        disease_arcs: list[DiseaseArc],
        current_age_months: int
    ) -> list[TimeSnapshot]:
        """Generate timeline snapshots at key points in the patient's history."""
        snapshots = []
        dob = patient.demographics.date_of_birth
        today = date.today()

        def months_at(d: date) -> int:
            r = relativedelta(d, dob)
            return r.years * 12 + r.months

        def conditions_on(d: date) -> list[Condition]:
            return [c for c in patient.problem_list if c.onset_date and c.onset_date <= d]

        def meds_on(d: date) -> list[Medication]:
            return [m for m in patient.medication_list
                    if m.start_date <= d and (m.end_date is None or m.end_date >= d)]

        # Snapshot at adult start (age 18)
        adult_start = 216
        if current_age_months >= adult_start:
            d = dob + relativedelta(years=18)
            snapshots.append(TimeSnapshot(
                age_months=adult_start,
                date=d,
                active_conditions=conditions_on(d),
                is_key_moment=True,
                event_description="Entered adult primary care.",
            ))

        # Snapshots at condition onsets
        for condition in patient.problem_list:
            if condition.onset_date:
                age_at_onset = months_at(condition.onset_date)
                if adult_start < age_at_onset <= current_age_months:
                    snapshots.append(TimeSnapshot(
                        age_months=age_at_onset,
                        date=condition.onset_date,
                        active_conditions=conditions_on(condition.onset_date),
                        medications=meds_on(condition.onset_date),
                        new_conditions=[condition.display_name],
                        is_key_moment=True,
                        event_description=f"Diagnosed with {condition.display_name}. Treatment initiated.",
                    ))

        # Disease arc stage transitions
        for arc in disease_arcs:
            for i, stage in enumerate(arc.stages):
                if i <= arc.current_stage_index:
                    min_age, max_age = stage.typical_age_range
                    hi = min(max_age, current_age_months)
                    if min_age > hi:
                        continue
                    stage_age = self._rng.randint(min_age, hi)
                    stage_date = min(dob + relativedelta(months=stage_age), today)

                    if not any(abs(s.age_months - stage_age) < 12 for s in snapshots):
                        snapshots.append(TimeSnapshot(
                            age_months=stage_age,
                            date=stage_date,
                            active_conditions=conditions_on(stage_date),
                            medications=meds_on(stage_date),
                            is_key_moment=True,
                            event_description=(
                                f"{arc.name}: progressed to {stage.display_name}. "
                                f"Symptoms: {', '.join(stage.symptoms[:3])}."),
                        ))

        # Current snapshot
        snapshots.append(TimeSnapshot(
            age_months=current_age_months,
            date=today,
            active_conditions=patient.active_conditions,
            medications=patient.active_medications,
            event_description="Current clinical status.",
        ))

        snapshots.sort(key=lambda s: s.age_months)

        # Deduplicate by year and limit
        seen_ages = set()
        unique_snapshots = []
        for s in snapshots:
            age_bucket = s.age_months // 12
            if age_bucket not in seen_ages:
                seen_ages.add(age_bucket)
                unique_snapshots.append(s)

        return unique_snapshots[:20]
