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
import yaml
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

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
    GrowthMeasurement,
    Immunization,
    Interpretation,
    LabResult,
    Medication,
    MedicationStatus,
    Patient,
    PatientTimeline,
    PhysicalExam,
    PlanItem,
    Provider,
    Location,
    ReferenceRange,
    Sex,
    SocialHistory,
    HouseholdMember,
    Address,
    Contact,
    TimeSnapshot,
    VitalSigns,
)
from src.engines.messiness import MessinessInjector
from src.llm import get_client, LLMClient


# =============================================================================
# Condition Registry
# =============================================================================

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
    
    def get_icd10(self, name: str) -> tuple[str, str]:
        """Get ICD-10 code and display for a condition."""
        cond = self.get_condition(name)
        if cond:
            return cond.get("icd10", "R69"), cond.get("display", name)
        return "R69", name.replace("_", " ").title()
    
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
        """Get sex restriction if any (male/female)."""
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
            
            weighted.append((cond_name, prevalence * age_factor))
        
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
HIGH_RISK_CONDITIONS_FOR_VACCINES = {
    "pneumococcal": ["type2_diabetes", "heart_failure", "ckd", "copd", "asthma", "cirrhosis", "hiv"],
    "hepb": ["type2_diabetes", "ckd", "hiv", "chronic_liver_disease"],
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
            "ckd", "copd", "osteoarthritis", "osteoporosis",
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
        use_llm: Enable LLM-powered narrative generation (default True)
        messiness_level: Chart quality level 0-5 (default 0 = pristine)
        llm_client: Optional pre-configured LLM client
    """

    def __init__(
        self,
        use_llm: bool = True,
        messiness_level: int = 0,
        llm_client: LLMClient | None = None,
        knowledge_dir: Path | None = None,
    ):
        self.knowledge_dir = knowledge_dir or Path(__file__).parent.parent / "knowledge"

        # LLM integration
        self.llm: LLMClient | None = None
        if use_llm:
            try:
                self.llm = llm_client or get_client()
            except (ValueError, Exception):
                self.llm = None
        self.use_llm = use_llm and self.llm is not None

        # Messiness injection (0-5)
        self.messiness_level = max(0, min(5, messiness_level))
        self.messiness = MessinessInjector(level=self.messiness_level)

        self._load_knowledge()

    def _load_knowledge(self):
        """Load knowledge bases for adult care."""
        # Condition registry is loaded on-demand via ConditionRegistry singleton
        pass
    
    def generate(self, seed: GenerationSeed) -> Patient:
        """Generate a complete adult patient record."""
        
        # Set random seed if provided
        if seed.random_seed:
            random.seed(seed.random_seed)
        
        # Step 1: Generate demographics
        demographics = self._generate_demographics(seed)
        
        # Step 2: Generate life arc (conditions, events, social trajectory)
        life_arc = self._generate_life_arc(demographics, seed)
        
        # Step 3: Generate weight trajectory
        weight_trajectory = self._generate_weight_trajectory(demographics, life_arc)
        
        # Step 4: Generate social history
        social_history = self._generate_social_history(demographics, life_arc, seed)
        
        # Step 5: Generate encounter timeline
        encounters = self._generate_encounter_timeline(demographics, life_arc, weight_trajectory, seed)
        
        # Step 6: Create problem list from life arc
        problem_list = self._create_problem_list(demographics, life_arc)
        
        # Step 7: Generate medications based on conditions
        medications = self._generate_medications(life_arc, demographics)

        # Step 8: Generate adult immunization record
        immunizations = self._generate_immunizations(demographics, life_arc, seed)

        # Determine complexity tier
        tier = self._determine_complexity(life_arc)

        # Build patient
        patient = Patient(
            demographics=demographics,
            social_history=social_history,
            problem_list=problem_list,
            medication_list=medications,
            immunization_record=immunizations,
            encounters=encounters,
            complexity_tier=tier,
            generation_seed=seed.model_dump(),
        )

        return patient

    def _generate_immunizations(
        self,
        demographics: Demographics,
        life_arc: AdultLifeArc,
        seed: GenerationSeed
    ) -> list[Immunization]:
        """Generate adult immunization record based on age, conditions, and ACIP guidelines."""

        immunizations = []
        age = demographics.age_years
        dob = demographics.date_of_birth
        today = date.today()
        current_year = today.year

        # Track which years we've given certain vaccines
        flu_years = []
        covid_years = []

        # Calculate years of history to generate
        years_history = seed.years_of_history or min(age - 18, 15)

        # High-risk check for early pneumococcal
        high_risk = any(
            cond in life_arc.chronic_conditions
            for cond in HIGH_RISK_CONDITIONS_FOR_VACCINES.get("pneumococcal", [])
        )

        # 1. Annual Influenza vaccines
        for year_offset in range(years_history):
            year = current_year - year_offset
            if random.random() < 0.65:  # ~65% uptake rate
                flu_date = date(year, random.randint(9, 11), random.randint(1, 28))
                if flu_date <= today:
                    flu_years.append(year)
                    immunizations.append(self._create_immunization(
                        "influenza", flu_date, 1, 1
                    ))

        # 2. Tdap/Td vaccines (every 10 years)
        # Initial Tdap at age 18 or earlier in history
        tdap_age = 18 if age >= 18 else age
        tdap_date = dob + timedelta(days=tdap_age * 365 + random.randint(0, 180))
        if tdap_date <= today:
            immunizations.append(self._create_immunization("tdap", tdap_date, 1, 1))

        # Td boosters every 10 years after
        for boost_age in range(tdap_age + 10, age, 10):
            td_date = dob + timedelta(days=boost_age * 365 + random.randint(0, 180))
            if td_date <= today:
                immunizations.append(self._create_immunization("td", td_date, 1, 1))

        # 3. Shingrix (age 50+, 2-dose series)
        if age >= 50:
            shingrix_age = max(50, age - random.randint(0, 5))
            dose1_date = dob + timedelta(days=shingrix_age * 365 + random.randint(0, 180))
            if dose1_date <= today and random.random() < 0.40:  # ~40% uptake
                immunizations.append(self._create_immunization("shingrix", dose1_date, 1, 2))
                dose2_date = dose1_date + timedelta(days=random.randint(60, 180))
                if dose2_date <= today:
                    immunizations.append(self._create_immunization("shingrix", dose2_date, 2, 2))

        # 4. Pneumococcal vaccines (age 65+ or high-risk)
        pneumo_eligible = age >= 65 or (age >= 19 and high_risk)
        if pneumo_eligible:
            pneumo_age = 65 if age >= 65 else max(19, age - random.randint(0, 5))
            pcv_date = dob + timedelta(days=pneumo_age * 365 + random.randint(0, 180))
            if pcv_date <= today and random.random() < 0.70:
                immunizations.append(self._create_immunization("pcv20", pcv_date, 1, 1))

        # 5. COVID-19 vaccines (2020+)
        for year_offset in range(min(years_history, current_year - 2020)):
            year = current_year - year_offset
            if year >= 2020 and random.random() < 0.55:  # ~55% uptake
                covid_date = date(year, random.randint(1, 12), random.randint(1, 28))
                if covid_date <= today:
                    covid_years.append(year)
                    immunizations.append(self._create_immunization("covid19", covid_date, 1, 1))

        # 6. Hepatitis B (adults 19-59, 3-dose series)
        if age <= 59 and random.random() < 0.30:
            hepb_age = random.randint(max(18, age - 10), min(59, age))
            dose1_date = dob + timedelta(days=hepb_age * 365)
            if dose1_date <= today:
                immunizations.append(self._create_immunization("hepb", dose1_date, 1, 3))
                dose2_date = dose1_date + timedelta(days=30)
                if dose2_date <= today:
                    immunizations.append(self._create_immunization("hepb", dose2_date, 2, 3))
                dose3_date = dose1_date + timedelta(days=180)
                if dose3_date <= today:
                    immunizations.append(self._create_immunization("hepb", dose3_date, 3, 3))

        # Sort by date
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
            site="Left deltoid" if random.random() < 0.6 else "Right deltoid",
            route="Intramuscular",
            manufacturer=random.choice(["Pfizer", "Moderna", "GSK", "Merck", "Sanofi"]),
            performer="RN",
            location="Primary Care Associates",
        )
    
    def _generate_demographics(self, seed: GenerationSeed) -> Demographics:
        """Generate adult demographics."""
        
        # Age (must be 18+)
        if seed.age is not None:
            age_years = max(18, seed.age)
        elif seed.age_months is not None:
            age_years = max(18, seed.age_months // 12)
        else:
            # Random age distribution weighted toward working age
            age_years = random.choices(
                [random.randint(18, 39), random.randint(40, 64), random.randint(65, 90)],
                weights=[35, 40, 25]
            )[0]
        
        # Sex
        sex = seed.sex or random.choice([Sex.MALE, Sex.FEMALE])
        
        # Calculate DOB
        today = date.today()
        dob = today - timedelta(days=age_years * 365 + random.randint(0, 364))
        
        # Generate names
        first_name = self._generate_first_name(sex, age_years)
        last_name = self._generate_last_name()
        
        # Address
        address = Address(
            line1=f"{random.randint(100, 9999)} {random.choice(['Oak', 'Maple', 'Cedar', 'Pine', 'Main', 'First', 'Park', 'Lake', 'Forest', 'River'])} {random.choice(['Street', 'Avenue', 'Lane', 'Drive', 'Court', 'Boulevard', 'Way'])}",
            city=random.choice(["Springfield", "Riverside", "Lakewood", "Fairview", "Madison", "Franklin", "Clinton", "Georgetown"]),
            state=seed.state or random.choice(["MN", "WI", "CA", "TX", "NY", "FL", "IL", "OH", "PA", "MI"]),
            postal_code=f"{random.randint(10000, 99999)}",
        )
        
        # Emergency contact (spouse, parent, or adult child depending on age)
        ec_relationship = self._get_emergency_contact_relationship(age_years)
        ec_sex = random.choice([Sex.MALE, Sex.FEMALE])
        emergency_contact = Contact(
            name=f"{self._generate_first_name(ec_sex, age_years)} {last_name if ec_relationship == 'Spouse' else self._generate_last_name()}",
            relationship=ec_relationship,
            phone=f"({random.randint(200, 999)}) {random.randint(200, 999)}-{random.randint(1000, 9999)}",
        )
        
        # Race and ethnicity
        race = seed.race or random.choices(
            [["White"], ["Black or African American"], ["Asian"], ["Two or more races"], ["American Indian or Alaska Native"]],
            weights=[60, 13, 6, 10, 1]
        )[0]
        
        ethnicity = seed.ethnicity or random.choices(
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
            phone=f"({random.randint(200, 999)}) {random.randint(200, 999)}-{random.randint(1000, 9999)}",
            emergency_contact=emergency_contact,
        )
    
    def _generate_life_arc(self, demographics: Demographics, seed: GenerationSeed) -> AdultLifeArc:
        """Generate the adult life trajectory."""
        
        age = demographics.age_years
        sex = demographics.sex_at_birth
        
        # Determine complexity tier
        if seed.complexity_tier:
            tier = seed.complexity_tier
        elif seed.conditions:
            tier = ComplexityTier.TIER_1 if len(seed.conditions) == 1 else ComplexityTier.TIER_2
        else:
            tier = random.choices(
                [ComplexityTier.TIER_0, ComplexityTier.TIER_1, ComplexityTier.TIER_2, ComplexityTier.TIER_3],
                weights=[30, 35, 25, 10]
            )[0]
        
        # Generate conditions based on tier and age
        conditions = []
        onset_ages = {}
        
        if seed.conditions:
            conditions = list(seed.conditions)
            for cond in conditions:
                # Generate plausible onset age
                onset_ages[cond] = random.randint(max(18, age - 20), age - 1) if age > 20 else 18
        else:
            # Stochastic condition generation based on age and tier
            conditions, onset_ages = self._generate_conditions_by_age_and_tier(age, tier, sex)
        
        # Determine health trajectory
        if not conditions:
            trajectory = "healthy"
        elif len(conditions) == 1:
            trajectory = "single_chronic"
        elif len(conditions) <= 3:
            trajectory = "multiple_chronic"
        else:
            trajectory = "complex"
        
        # Generate ASCVD risk category based on conditions and age
        ascvd_risk = self._calculate_ascvd_risk_category(conditions, age, sex)
        
        # Social trajectory
        life_stage = get_life_stage(age)
        marital_status = self._generate_marital_status(age)
        children = self._generate_number_of_children(age, marital_status)
        
        return AdultLifeArc(
            health_trajectory=trajectory,
            chronic_conditions=conditions,
            condition_onset_ages=onset_ages,
            ascvd_risk_category=ascvd_risk,
            marital_status=marital_status,
            children=children,
        )
    
    def _generate_conditions_by_age_and_tier(
        self, age: int, tier: ComplexityTier, sex: Sex
    ) -> tuple[list[str], dict[str, int]]:
        """Generate conditions probabilistically based on age and complexity tier."""
        
        conditions = []
        onset_ages = {}
        registry = ConditionRegistry.get()
        
        # Target number of conditions by tier
        target_conditions = {
            ComplexityTier.TIER_0: 0,
            ComplexityTier.TIER_1: 1,
            ComplexityTier.TIER_2: random.randint(2, 4),
            ComplexityTier.TIER_3: random.randint(4, 7),
        }.get(tier, 0)
        
        if target_conditions == 0:
            return [], {}
        
        # Get all chronic conditions from registry
        all_conditions = registry.all_chronic_conditions()
        
        # Calculate probability-weighted candidates
        candidates = []
        for condition in all_conditions:
            # Skip sex-specific conditions that don't apply
            sex_restriction = registry.get_sex_specific(condition)
            if sex_restriction:
                if sex_restriction == "male" and sex == Sex.FEMALE:
                    continue
                if sex_restriction == "female" and sex == Sex.MALE:
                    continue
            
            # Get age-based probability
            prob = get_age_multiplier(age, condition) * random.uniform(0.3, 1.0)
            candidates.append((condition, prob))
        
        # Sort by probability (higher = more likely to select)
        random.shuffle(candidates)  # Add randomness
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        # Select conditions
        for condition, prob in candidates:
            if len(conditions) >= target_conditions:
                break
            
            # Higher probability = more likely to be selected
            if random.random() < prob:
                conditions.append(condition)
                
                # Generate onset age based on typical onset range
                onset_min, onset_max = registry.get_onset_range(condition)
                onset = random.randint(
                    max(18, onset_min),
                    min(age - 1, onset_max) if age > onset_min else age
                )
                onset_ages[condition] = max(18, onset)
        
        return conditions, onset_ages
    
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
        high_risk_conditions = {"coronary_artery_disease", "heart_failure", "atrial_fibrillation"}
        cv_risk_factors = {"hypertension", "hyperlipidemia", "type2_diabetes", "obesity", "ckd"}
        
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
            baseline_bmi = random.uniform(24, 28)  # Often overweight by 18
            trajectory_type = "gradual_gain"
        else:
            baseline_bmi = random.uniform(19, 24)
            trajectory_type = random.choice(["stable", "gradual_gain", "stable"])
        
        # Current BMI
        if has_obesity:
            current_bmi = random.uniform(30, 42)
        elif has_diabetes:
            current_bmi = random.uniform(26, 34)  # Often overweight
        else:
            current_bmi = random.uniform(19, 28)
        
        # Generate inflection points
        inflection_points = []
        
        if trajectory_type == "gradual_gain" and age > 30:
            # Typical middle-age weight gain
            inflection_points.append({
                "age": 30,
                "bmi": baseline_bmi + random.uniform(1, 3),
                "reason": "lifestyle_changes"
            })
            if age > 45:
                inflection_points.append({
                    "age": 45,
                    "bmi": baseline_bmi + random.uniform(3, 6),
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
        life_stage = get_life_stage(age)
        
        # Employment
        if age < 65:
            employment_status = random.choices(
                ["Employed full-time", "Employed part-time", "Self-employed", "Unemployed", "Disabled", "Student"],
                weights=[60, 10, 10, 8, 7, 5]
            )[0]
        else:
            employment_status = random.choices(
                ["Retired", "Employed part-time", "Employed full-time"],
                weights=[70, 20, 10]
            )[0]
        
        # Occupation (if employed)
        occupation = None
        if "Employed" in employment_status or "Self-employed" in employment_status:
            occupation = random.choice([
                "Office worker", "Healthcare worker", "Retail", "Construction",
                "Teacher", "Engineer", "Manager", "Service industry",
                "Skilled trades", "Professional services"
            ])
        
        # Living situation based on marital status
        if life_arc.marital_status in ["Married", "Domestic partnership"]:
            living_situation = "Lives with spouse/partner"
        elif age >= 75:
            living_situation = random.choices(
                ["Lives alone", "Lives with family", "Assisted living", "Nursing home"],
                weights=[40, 35, 15, 10]
            )[0]
        else:
            living_situation = random.choice(["Lives alone", "Lives with family", "Lives with roommates"])
        
        # Household members
        household = []
        if "spouse" in living_situation.lower() or "partner" in living_situation.lower():
            spouse_sex = Sex.FEMALE if demographics.sex_at_birth == Sex.MALE else Sex.MALE
            household.append(HouseholdMember(
                name="Spouse",
                relationship="Spouse",
                age=demographics.age_years + random.randint(-5, 5),
            ))
        
        if life_arc.children > 0 and "alone" not in living_situation.lower():
            # Add some children if they'd still be at home
            for i in range(min(life_arc.children, 3)):
                child_age = random.randint(0, min(age - 20, 25))
                if child_age < 22:  # Still might be at home
                    household.append(HouseholdMember(
                        name=f"Child {i+1}",
                        relationship="Child",
                        age=child_age,
                    ))
        
        # Substance use
        tobacco_use = random.choices(
            ["Never", "Former", "Current"],
            weights=[55, 25, 20]
        )[0]
        
        alcohol_use = random.choices(
            ["None", "Social/occasional", "Moderate", "Heavy"],
            weights=[25, 45, 25, 5]
        )[0]
        
        return SocialHistory(
            living_situation=living_situation,
            household_members=household,
            employment_status=employment_status,
            occupation=occupation,
            tobacco_use=tobacco_use,
            alcohol_use=alcohol_use,
            food_security="Secure",
            housing_stability="Stable",
        )
    
    def _generate_encounter_timeline(
        self,
        demographics: Demographics,
        life_arc: AdultLifeArc,
        weight_trajectory: WeightTrajectory,
        seed: GenerationSeed,
    ) -> list[Encounter]:
        """Generate encounter history for an adult patient."""
        
        encounters = []
        age = demographics.age_years
        
        # Determine years of history to generate
        years_history = seed.years_of_history or min(age - 18, 15)
        
        # Generate annual wellness visits
        for year_offset in range(years_history, 0, -1):
            visit_age = age - year_offset
            if visit_age < 18:
                continue
            
            life_stage = get_life_stage(visit_age)
            
            # Annual wellness visit (more frequent in older adults)
            if year_offset % max(1, life_stage.visit_frequency_months // 12) == 0:
                visit_date = date.today() - timedelta(days=year_offset * 365 + random.randint(-30, 30))
                
                encounter = self._generate_wellness_encounter(
                    demographics, life_arc, weight_trajectory, visit_date, visit_age
                )
                encounters.append(encounter)
        
        # Generate condition-related encounters
        for condition in life_arc.chronic_conditions:
            onset_age = life_arc.condition_onset_ages.get(condition, age - 5)
            condition_encounters = self._generate_condition_encounters(
                demographics, condition, onset_age, age
            )
            encounters.extend(condition_encounters)
        
        # Generate acute illness encounters
        acute_encounters = self._generate_acute_encounters(demographics, life_arc, years_history)
        encounters.extend(acute_encounters)
        
        # Sort by date
        encounters.sort(key=lambda e: e.date)
        
        return encounters
    
    def _generate_wellness_encounter(
        self,
        demographics: Demographics,
        life_arc: AdultLifeArc,
        weight_trajectory: WeightTrajectory,
        visit_date: date,
        visit_age: int,
    ) -> Encounter:
        """Generate an adult wellness/preventive visit with condition-aware content."""

        life_stage = get_life_stage(visit_age)

        # Chief complaint varies by age
        if visit_age >= 65:
            chief_complaint = "Medicare Annual Wellness Visit"
        else:
            chief_complaint = "Annual physical examination"

        # Generate vitals
        height_m = random.uniform(1.55, 1.90) if demographics.sex_at_birth == Sex.MALE else random.uniform(1.50, 1.75)
        weight_kg = weight_trajectory.get_weight_at_age(visit_age, height_m)

        # BP - higher if hypertensive
        if "hypertension" in life_arc.chronic_conditions:
            systolic = random.randint(125, 145)
            diastolic = random.randint(78, 92)
        else:
            systolic = random.randint(110, 128)
            diastolic = random.randint(65, 82)

        # Heart rate - higher with certain conditions
        base_hr = random.randint(60, 85)
        if "atrial_fibrillation" in life_arc.chronic_conditions:
            base_hr = random.randint(70, 110)  # Can be irregular/elevated
        if "anxiety" in life_arc.chronic_conditions:
            base_hr = int(base_hr * 1.05)

        # O2 sat - lower with respiratory conditions
        o2_sat = random.randint(96, 100)
        if "copd" in life_arc.chronic_conditions:
            o2_sat = random.randint(90, 96)
        elif "heart_failure" in life_arc.chronic_conditions:
            o2_sat = random.randint(92, 97)

        vitals = VitalSigns(
            date=visit_date,
            blood_pressure_systolic=systolic,
            blood_pressure_diastolic=diastolic,
            heart_rate=base_hr,
            respiratory_rate=random.randint(12, 18),
            temperature_f=round(random.uniform(97.5, 98.9), 1),
            oxygen_saturation=o2_sat,
            height_cm=round(height_m * 100, 1),
            weight_kg=round(weight_kg, 1),
        )

        # Generate condition-aware physical exam
        exam_findings = {
            'general': "Well-appearing, in no acute distress",
            'heent': "Normocephalic, atraumatic, oropharynx clear",
            'cardiovascular': "Regular rate and rhythm, no murmurs",
            'respiratory': "Clear to auscultation bilaterally",
            'abdomen': "Soft, non-tender, non-distended",
            'extremities': "No edema, pulses 2+ bilaterally",
            'skin': "Warm, dry, no lesions",
        }

        # Modify exam based on chronic conditions
        if "heart_failure" in life_arc.chronic_conditions:
            exam_findings['cardiovascular'] = "S3 gallop present, regular rhythm"
            exam_findings['extremities'] = "1+ pedal edema bilaterally"
        if "atrial_fibrillation" in life_arc.chronic_conditions:
            exam_findings['cardiovascular'] = "Irregularly irregular rhythm, no murmurs"
        if "copd" in life_arc.chronic_conditions:
            exam_findings['respiratory'] = "Diminished breath sounds, prolonged expiratory phase"
        if "obesity" in life_arc.chronic_conditions:
            exam_findings['general'] = "Obese, well-appearing, in no acute distress"

        physical_exam = PhysicalExam(
            general=exam_findings['general'],
            heent=exam_findings['heent'],
            cardiovascular=exam_findings['cardiovascular'],
            respiratory=exam_findings['respiratory'],
            abdomen=exam_findings['abdomen'],
            extremities=exam_findings['extremities'],
            skin=exam_findings['skin'],
        )

        # Generate labs for chronic conditions being monitored
        lab_results = []
        registry = ConditionRegistry.get()
        for condition in life_arc.chronic_conditions:
            labs_list = registry.get_labs(condition)
            for lab_name in labs_list[:2]:  # Max 2 labs per condition
                lab_result = self._create_lab_result(lab_name, visit_date, condition)
                if lab_result and lab_result not in lab_results:
                    lab_results.append(lab_result)

        # Generate assessment and plan
        conditions_list = [c.replace("_", " ").title() for c in life_arc.chronic_conditions]
        assessment_text = f"Annual wellness visit. Active conditions: {', '.join(conditions_list) if conditions_list else 'None'}."

        screening_items = life_stage.screening_focus[:3]
        plan_text = f"Continue current medications. Screenings addressed: {', '.join(screening_items)}."

        return Encounter(
            date=visit_date,
            type=EncounterType.ANNUAL_PHYSICAL,
            chief_complaint=chief_complaint,
            vitals=vitals,
            physical_exam=physical_exam,
            lab_results=lab_results if lab_results else [],
            assessment=[Assessment(diagnosis=assessment_text, is_primary=True)],
            plan=[
                PlanItem(category="medication", description="Continue current medications"),
                PlanItem(category="follow-up", description=f"Follow up in {life_stage.visit_frequency_months} months")
            ],
            provider=Provider(
                name=self._generate_provider_name(),
                credentials="MD",
                specialty="Internal Medicine" if visit_age >= 18 else "Family Medicine",
            ),
            location=Location(
                name="Primary Care Associates",
                type="Outpatient clinic",
            ),
        )
    
    def _generate_condition_encounters(
        self,
        demographics: Demographics,
        condition: str,
        onset_age: int,
        current_age: int,
    ) -> list[Encounter]:
        """Generate encounters related to a chronic condition with condition-aware content."""

        encounters = []
        years_with_condition = current_age - onset_age
        registry = ConditionRegistry.get()
        cond_data = registry.get_condition(condition)

        # Get display name
        display_name = cond_data.get('display', condition.replace('_', ' ').title()) if cond_data else condition.replace('_', ' ').title()

        # Initial diagnosis encounter
        diagnosis_date = date.today() - timedelta(days=years_with_condition * 365 + random.randint(-30, 30))

        # Generate vitals for diagnosis
        height_m = random.uniform(1.55, 1.90) if demographics.sex_at_birth == Sex.MALE else random.uniform(1.50, 1.75)
        weight_kg = random.uniform(60, 100)

        vitals = VitalSigns(
            date=diagnosis_date,
            blood_pressure_systolic=random.randint(120, 140) if condition == "hypertension" else random.randint(110, 125),
            blood_pressure_diastolic=random.randint(80, 95) if condition == "hypertension" else random.randint(65, 82),
            heart_rate=random.randint(65, 90),
            respiratory_rate=random.randint(14, 18),
            temperature_f=round(random.uniform(97.6, 98.9), 1),
            oxygen_saturation=random.randint(95, 100),
            height_cm=round(height_m * 100, 1),
            weight_kg=round(weight_kg, 1),
        )

        # Initial workup labs
        labs_list = registry.get_labs(condition) if cond_data else []
        lab_results = []
        for lab_name in labs_list[:3]:
            lab_result = self._create_lab_result(lab_name, diagnosis_date, condition)
            if lab_result:
                lab_results.append(lab_result)

        diagnosis_encounter = Encounter(
            date=diagnosis_date,
            type=EncounterType.ACUTE_ILLNESS,
            chief_complaint=f"New diagnosis: {display_name}",
            history_of_present_illness=f"Patient presents with symptoms concerning for {display_name}. Initial workup performed.",
            vitals=vitals,
            lab_results=lab_results if lab_results else [],
            assessment=[Assessment(diagnosis=f"New diagnosis of {display_name}", is_primary=True)],
            plan=[
                PlanItem(category="medication", description="Start treatment per protocol"),
                PlanItem(category="education", description="Patient education provided"),
                PlanItem(category="follow-up", description="Follow up in 4-6 weeks to assess response")
            ],
            provider=Provider(
                name=self._generate_provider_name(),
                credentials="MD",
                specialty="Internal Medicine",
            ),
            location=Location(
                name="Primary Care Associates",
                type="Outpatient clinic",
            ),
        )
        encounters.append(diagnosis_encounter)

        # Follow-up encounters (quarterly initially, then less frequent)
        visit_pattern = registry.get_visit_pattern(condition) if cond_data else "quarterly_then_annual"
        for year in range(years_with_condition):
            # More visits in first year based on visit pattern
            if visit_pattern == "monthly":
                visits_this_year = 12 if year == 0 else 6
            elif visit_pattern == "quarterly":
                visits_this_year = 4
            elif visit_pattern == "biannual":
                visits_this_year = 2
            elif visit_pattern == "quarterly_then_annual":
                visits_this_year = 4 if year == 0 else random.randint(1, 2)
            else:  # annual
                visits_this_year = 1

            for visit in range(visits_this_year):
                visit_date = date.today() - timedelta(
                    days=(years_with_condition - year) * 365 - visit * (365 // visits_this_year) + random.randint(-15, 15)
                )

                if visit_date > date.today():
                    continue

                # Generate follow-up vitals
                followup_vitals = VitalSigns(
                    date=visit_date,
                    blood_pressure_systolic=random.randint(120, 138) if condition == "hypertension" else random.randint(110, 125),
                    blood_pressure_diastolic=random.randint(75, 88) if condition == "hypertension" else random.randint(65, 82),
                    heart_rate=random.randint(65, 85),
                    respiratory_rate=random.randint(14, 18),
                    temperature_f=round(random.uniform(97.6, 98.9), 1),
                    oxygen_saturation=random.randint(95, 100),
                    height_cm=round(height_m * 100, 1),
                    weight_kg=round(weight_kg + random.uniform(-2, 2), 1),
                )

                # Monitoring labs for follow-up
                followup_labs = []
                for lab_name in labs_list[:2]:
                    lab_result = self._create_lab_result(lab_name, visit_date, condition)
                    if lab_result:
                        followup_labs.append(lab_result)

                # Get medications for this condition
                meds = registry.get_meds(condition) if cond_data else []
                med_list = ", ".join(meds[:2]) if meds else "current regimen"

                is_stable = random.random() > 0.2
                assessment_text = f"{display_name} - stable on current therapy" if is_stable else f"{display_name} - suboptimal control, adjustment needed"

                followup = Encounter(
                    date=visit_date,
                    type=EncounterType.CHRONIC_FOLLOWUP,
                    chief_complaint=f"{display_name} follow-up",
                    vitals=followup_vitals,
                    lab_results=followup_labs if followup_labs else [],
                    assessment=[Assessment(diagnosis=assessment_text, is_primary=True)],
                    plan=[
                        PlanItem(category="medication", description=f"Continue {med_list}"),
                        PlanItem(category="education", description="Lifestyle modifications reinforced"),
                        PlanItem(category="follow-up", description="Follow up per protocol")
                    ],
                    provider=Provider(
                        name=self._generate_provider_name(),
                        credentials="MD",
                        specialty="Internal Medicine",
                    ),
                    location=Location(
                        name="Primary Care Associates",
                        type="Outpatient clinic",
                    ),
                )
                encounters.append(followup)

        return encounters
    
    def _generate_acute_encounters(
        self,
        demographics: Demographics,
        life_arc: AdultLifeArc,
        years_history: int,
    ) -> list[Encounter]:
        """Generate acute illness encounters with condition-aware content."""

        encounters = []
        age = demographics.age_years

        # Acute visit frequency: 0.5-2 per year depending on health
        visits_per_year = 0.5 if life_arc.health_trajectory == "healthy" else 1.5

        # Weighted acute reasons based on condition registry
        registry = ConditionRegistry.get()
        season_map = {1: "winter", 2: "winter", 3: "spring", 4: "spring",
                      5: "spring", 6: "summer", 7: "summer", 8: "summer",
                      9: "fall", 10: "fall", 11: "fall", 12: "winter"}

        for year in range(years_history):
            if random.random() < visits_per_year:
                visit_date = date.today() - timedelta(days=year * 365 + random.randint(0, 364))
                season = season_map.get(visit_date.month, "winter")

                # Select condition based on weighted probabilities
                weighted_conditions = registry.get_weighted_acute_conditions(age, season)
                if weighted_conditions:
                    conditions, weights = zip(*weighted_conditions)
                    condition_key = random.choices(conditions, weights=weights)[0]
                else:
                    condition_key = "upper_respiratory_infection"

                # Get condition info
                cond = registry.get_acute_condition(condition_key)
                chief_complaint = cond.get("display", condition_key.replace("_", " ").title()) if cond else condition_key.replace("_", " ").title()

                # Generate symptoms for HPI
                symptoms = self._select_symptoms(condition_key)
                hpi = f"Patient presents with {', '.join(symptoms[:3]) if symptoms else 'presenting complaints'} for the past {random.randint(1, 5)} days."

                # Generate base vitals
                height_m = random.uniform(1.55, 1.90) if demographics.sex_at_birth == Sex.MALE else random.uniform(1.50, 1.75)
                weight_kg = random.uniform(60, 100)

                vitals = VitalSigns(
                    date=visit_date,
                    blood_pressure_systolic=random.randint(110, 130),
                    blood_pressure_diastolic=random.randint(65, 85),
                    heart_rate=random.randint(65, 90),
                    respiratory_rate=random.randint(14, 18),
                    temperature_f=round(random.uniform(97.6, 98.9), 1),
                    oxygen_saturation=random.randint(96, 100),
                    height_cm=round(height_m * 100, 1),
                    weight_kg=round(weight_kg, 1),
                )

                # Apply condition-specific vitals modifications
                vitals = self._apply_vitals_impact(vitals, condition_key, EncounterType.ACUTE_ILLNESS)

                # Generate physical exam findings
                exam_findings = self._generate_condition_physical_exam(condition_key, EncounterType.ACUTE_ILLNESS)
                physical_exam = PhysicalExam(
                    general=exam_findings.get('general', "Alert and oriented, appears comfortable"),
                    heent=exam_findings.get('heent', "Normocephalic, atraumatic"),
                    cardiovascular=exam_findings.get('cardiovascular', "Regular rate and rhythm"),
                    respiratory=exam_findings.get('respiratory', "Clear to auscultation bilaterally"),
                    abdomen=exam_findings.get('abdomen', "Soft, non-tender"),
                    extremities=exam_findings.get('extremities', "No edema"),
                    skin=exam_findings.get('skin', "Warm, dry"),
                )

                # Generate lab results
                lab_results = self._generate_labs(condition_key, visit_date, EncounterType.ACUTE_ILLNESS)

                # Generate treatment plan based on condition
                treatment = cond.get('treatment', {}) if cond else {}
                meds = []
                for key in ['symptomatic', 'if_bacterial', 'antibiotics', 'antiviral', 'medications']:
                    meds.extend(treatment.get(key, []))
                treatment_desc = f"Treatment: {', '.join(meds[:3])}" if meds else "Supportive care"
                followup_days = random.randint(3, 7)

                assessment_text = f"{chief_complaint}"
                if symptoms:
                    assessment_text += f". {', '.join(symptoms[:2])}"

                encounter = Encounter(
                    date=visit_date,
                    type=EncounterType.ACUTE_ILLNESS,
                    chief_complaint=chief_complaint,
                    history_of_present_illness=hpi,
                    vitals=vitals,
                    physical_exam=physical_exam,
                    lab_results=lab_results if lab_results else [],
                    assessment=[Assessment(diagnosis=assessment_text, is_primary=True)],
                    plan=[
                        PlanItem(category="medication", description=treatment_desc),
                        PlanItem(category="follow-up", description=f"Follow up if symptoms worsen or no improvement in {followup_days} days")
                    ],
                    provider=Provider(
                        name=self._generate_provider_name(),
                        credentials="MD",
                        specialty="Family Medicine",
                    ),
                    location=Location(
                        name="Primary Care Associates",
                        type="Outpatient clinic",
                    ),
                )
                encounters.append(encounter)

        return encounters
    
    def _create_problem_list(
        self, demographics: Demographics, life_arc: AdultLifeArc
    ) -> list[Condition]:
        """Create problem list from life arc conditions using registry."""
        
        problems = []
        registry = ConditionRegistry.get()
        
        for cond_name in life_arc.chronic_conditions:
            onset_age = life_arc.condition_onset_ages.get(cond_name, demographics.age_years - 5)
            onset_date = demographics.date_of_birth + timedelta(days=onset_age * 365)
            
            code, display = registry.get_icd10(cond_name)
            
            condition = Condition(
                display_name=display,
                code=CodeableConcept(
                    system="http://hl7.org/fhir/sid/icd-10-cm",
                    code=code,
                    display=display,
                ),
                clinical_status=ConditionStatus.ACTIVE,
                onset_date=onset_date,
            )
            problems.append(condition)
        
        return problems
    
    def _generate_medications(
        self, life_arc: AdultLifeArc, demographics: Demographics
    ) -> list[Medication]:
        """Generate medications based on conditions from registry."""
        
        medications = []
        registry = ConditionRegistry.get()
        
        for condition in life_arc.chronic_conditions:
            meds = registry.get_meds(condition)
            if not meds:
                continue
            
            # Pick 1-2 meds for this condition
            num_meds = 1 if random.random() < 0.7 else 2
            selected_meds = random.sample(meds, min(num_meds, len(meds)))
            
            for med_str in selected_meds:
                # Parse "lisinopril 10mg" -> name="lisinopril", dose="10", unit="mg"
                parts = med_str.split()
                if len(parts) >= 2:
                    name = parts[0]
                    dose_str = parts[1]
                    # Extract number and unit from "10mg" or "10 mg"
                    import re
                    match = re.match(r'(\d+\.?\d*)\s*(\w+)', dose_str)
                    if match:
                        dose_quantity = match.group(1)
                        dose_unit = match.group(2)
                    else:
                        dose_quantity = dose_str
                        dose_unit = "unit"
                else:
                    name = med_str
                    dose_quantity = "1"
                    dose_unit = "unit"
                
                # Calculate start date based on condition onset
                onset_age = life_arc.condition_onset_ages.get(condition, demographics.age_years - 5)
                start_date = demographics.date_of_birth + timedelta(days=onset_age * 365 + random.randint(0, 180))
                
                medications.append(Medication(
                    display_name=med_str,
                    code=CodeableConcept(
                        system="http://www.nlm.nih.gov/research/umls/rxnorm",
                        code="000000",  # Placeholder
                        display=name,
                    ),
                    status=MedicationStatus.ACTIVE,
                    dose_quantity=dose_quantity,
                    dose_unit=dose_unit,
                    frequency="daily",
                    route="oral",
                    start_date=start_date,
                    indication=condition.replace("_", " ").title(),
                ))
        
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
        return random.choice(names)
    
    def _generate_last_name(self) -> str:
        """Generate a last name."""
        last_names = [
            "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
            "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
            "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
            "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
            "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
        ]
        return random.choice(last_names)
    
    def _generate_provider_name(self) -> str:
        """Generate a provider name."""
        first_names = ["Sarah", "Michael", "Jennifer", "David", "Emily", "Robert", "Lisa", "James"]
        last_names = ["Chen", "Patel", "Williams", "Johnson", "Kim", "Anderson", "Martinez", "Thompson"]
        return f"Dr. {random.choice(first_names)} {random.choice(last_names)}"
    
    def _get_emergency_contact_relationship(self, age: int) -> str:
        """Get appropriate emergency contact relationship based on age."""
        if age < 30:
            return random.choice(["Parent", "Sibling", "Spouse"])
        elif age < 65:
            return random.choices(["Spouse", "Sibling", "Adult child", "Parent"], weights=[60, 15, 15, 10])[0]
        else:
            return random.choices(["Spouse", "Adult child", "Sibling"], weights=[40, 45, 15])[0]
    
    def _generate_marital_status(self, age: int) -> str:
        """Generate marital status based on age."""
        if age < 25:
            return random.choices(
                ["Single", "Married", "Domestic partnership"],
                weights=[80, 15, 5]
            )[0]
        elif age < 40:
            return random.choices(
                ["Single", "Married", "Divorced", "Domestic partnership"],
                weights=[35, 50, 10, 5]
            )[0]
        elif age < 65:
            return random.choices(
                ["Single", "Married", "Divorced", "Widowed"],
                weights=[15, 55, 20, 10]
            )[0]
        else:
            return random.choices(
                ["Married", "Widowed", "Divorced", "Single"],
                weights=[40, 35, 15, 10]
            )[0]
    
    def _generate_number_of_children(self, age: int, marital_status: str) -> int:
        """Generate number of children based on age and marital status."""
        if age < 25:
            return random.choices([0, 1], weights=[85, 15])[0]
        elif age < 35:
            return random.choices([0, 1, 2], weights=[40, 35, 25])[0]
        elif age < 50:
            return random.choices([0, 1, 2, 3, 4], weights=[20, 25, 35, 15, 5])[0]
        else:
            return random.choices([0, 1, 2, 3, 4], weights=[15, 20, 40, 20, 5])[0]

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
            vitals.temperature_f = round(random.uniform(temp_range[0], temp_range[1]), 1)

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
            vitals.oxygen_saturation = float(random.randint(spo2_min, max_spo2))

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
                    if random.random() < probability:
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
                    if system and finding and random.random() < probability:
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
        encounter_type: EncounterType
    ) -> list:
        """Generate condition-specific lab results."""
        from src.models import LabResult, CodeableConcept, Interpretation

        if not condition_key or encounter_type not in (
            EncounterType.ACUTE_ILLNESS,
            EncounterType.URGENT_CARE,
            EncounterType.ED_VISIT,
            EncounterType.CHRONIC_FOLLOWUP
        ):
            return []

        # Get workup from condition
        registry = ConditionRegistry.get()
        cond = registry.get_acute_condition(condition_key)

        workup = []
        if cond:
            workup_def = cond.get('workup', 'clinical')
            if isinstance(workup_def, list):
                workup = workup_def
            elif workup_def and workup_def != 'clinical' and workup_def != 'none':
                workup = [workup_def]

        if not workup:
            return []

        results = []
        for lab_name in workup:
            lab_result = self._create_lab_result(lab_name, encounter_date, condition_key)
            if lab_result:
                results.append(lab_result)

        return results

    def _create_lab_result(self, lab_name: str, encounter_date: date, condition_key: str | None):
        """Create a lab result based on name and condition."""
        from src.models import LabResult, CodeableConcept, Interpretation, ReferenceRange

        # Lab definitions with LOINC codes
        lab_definitions = {
            "cbc": {
                "loinc": "58410-2",
                "display": "Complete Blood Count",
                "panels": [
                    {"name": "WBC", "loinc": "6690-2", "unit": "K/uL", "range": [4.5, 11.0]},
                    {"name": "RBC", "loinc": "789-8", "unit": "M/uL", "range": [4.2, 5.9]},
                    {"name": "Hemoglobin", "loinc": "718-7", "unit": "g/dL", "range": [12.0, 17.5]},
                    {"name": "Hematocrit", "loinc": "4544-3", "unit": "%", "range": [36.0, 50.0]},
                    {"name": "Platelets", "loinc": "777-3", "unit": "K/uL", "range": [150, 400]},
                ]
            },
            "cmp": {
                "loinc": "24323-8",
                "display": "Comprehensive Metabolic Panel",
                "panels": [
                    {"name": "Glucose", "loinc": "2345-7", "unit": "mg/dL", "range": [70, 100]},
                    {"name": "BUN", "loinc": "3094-0", "unit": "mg/dL", "range": [7, 20]},
                    {"name": "Creatinine", "loinc": "2160-0", "unit": "mg/dL", "range": [0.7, 1.3]},
                    {"name": "Sodium", "loinc": "2951-2", "unit": "mEq/L", "range": [136, 145]},
                    {"name": "Potassium", "loinc": "2823-3", "unit": "mEq/L", "range": [3.5, 5.0]},
                    {"name": "Chloride", "loinc": "2075-0", "unit": "mEq/L", "range": [98, 106]},
                    {"name": "CO2", "loinc": "2028-9", "unit": "mEq/L", "range": [23, 29]},
                ]
            },
            "bmp": {
                "loinc": "24320-4",
                "display": "Basic Metabolic Panel",
                "panels": [
                    {"name": "Glucose", "loinc": "2345-7", "unit": "mg/dL", "range": [70, 100]},
                    {"name": "BUN", "loinc": "3094-0", "unit": "mg/dL", "range": [7, 20]},
                    {"name": "Creatinine", "loinc": "2160-0", "unit": "mg/dL", "range": [0.7, 1.3]},
                    {"name": "Sodium", "loinc": "2951-2", "unit": "mEq/L", "range": [136, 145]},
                    {"name": "Potassium", "loinc": "2823-3", "unit": "mEq/L", "range": [3.5, 5.0]},
                ]
            },
            "lipid_panel": {
                "loinc": "24331-1",
                "display": "Lipid Panel",
                "panels": [
                    {"name": "Total Cholesterol", "loinc": "2093-3", "unit": "mg/dL", "range": [0, 200]},
                    {"name": "Triglycerides", "loinc": "2571-8", "unit": "mg/dL", "range": [0, 150]},
                    {"name": "HDL", "loinc": "2085-9", "unit": "mg/dL", "range": [40, 100]},
                    {"name": "LDL", "loinc": "13457-7", "unit": "mg/dL", "range": [0, 100]},
                ]
            },
            "urinalysis": {
                "loinc": "24356-8",
                "display": "Urinalysis",
                "binary": True,
                "result": "normal" if condition_key != "urinary_tract_infection" else "abnormal"
            },
            "rapid_strep": {
                "loinc": "78012-2",
                "display": "Rapid Strep Test",
                "binary": True,
                "result": "positive" if random.random() < 0.3 else "negative"
            },
            "rapid_flu": {
                "loinc": "80382-5",
                "display": "Influenza A+B Rapid Test",
                "binary": True,
                "result": "positive" if condition_key == "influenza" else "negative"
            },
            "rapid_covid": {
                "loinc": "95209-3",
                "display": "SARS-CoV-2 Rapid Antigen",
                "binary": True,
                "result": "positive" if condition_key == "covid19" else "negative"
            },
            "chest_xray": {
                "loinc": "36643-5",
                "display": "Chest X-ray PA and Lateral",
                "imaging": True,
                "result": "Infiltrate in right lower lobe" if condition_key == "pneumonia" else "No acute cardiopulmonary disease"
            },
            "hba1c": {
                "loinc": "4548-4",
                "display": "Hemoglobin A1c",
                "unit": "%",
                "range": [4.0, 5.6],
                "value": round(random.uniform(5.5, 9.5), 1) if condition_key in ["type2_diabetes", "prediabetes"] else round(random.uniform(4.5, 5.6), 1)
            },
            "tsh": {
                "loinc": "3016-3",
                "display": "TSH",
                "unit": "mIU/L",
                "range": [0.4, 4.0]
            },
            "troponin": {
                "loinc": "49563-0",
                "display": "Troponin I",
                "unit": "ng/mL",
                "range": [0, 0.04],
                "value": 0.02 if condition_key != "chest_pain" or random.random() > 0.1 else round(random.uniform(0.1, 2.0), 2)
            },
            "bnp": {
                "loinc": "30934-4",
                "display": "BNP",
                "unit": "pg/mL",
                "range": [0, 100]
            },
        }

        lab_def = lab_definitions.get(lab_name.lower())
        if not lab_def:
            return None

        # Handle binary/imaging results
        if lab_def.get('binary') or lab_def.get('imaging'):
            result_text = lab_def.get('result', 'normal')
            interp = Interpretation.NORMAL if result_text in ['normal', 'negative'] else Interpretation.ABNORMAL

            return LabResult(
                display_name=lab_def['display'],
                code=CodeableConcept(
                    system="http://loinc.org",
                    code=lab_def['loinc'],
                    display=lab_def['display']
                ),
                value=result_text,
                unit="",
                interpretation=interp,
                resulted_date=datetime.combine(encounter_date, datetime.min.time())
            )

        # Handle panel results (return first result as representative)
        if 'panels' in lab_def:
            panel = lab_def['panels'][0]
            low, high = panel['range']
            value = round(random.uniform(low, high), 1)

            return LabResult(
                display_name=lab_def['display'],
                code=CodeableConcept(
                    system="http://loinc.org",
                    code=lab_def['loinc'],
                    display=lab_def['display']
                ),
                value=str(value),
                unit=panel['unit'],
                interpretation=Interpretation.NORMAL,
                resulted_date=datetime.combine(encounter_date, datetime.min.time()),
                reference_range=ReferenceRange(low=low, high=high)
            )

        # Handle single value results
        if 'value' in lab_def:
            value = lab_def['value']
        elif 'range' in lab_def:
            low, high = lab_def['range']
            value = round(random.uniform(low, high * 1.1), 2)  # Allow slight elevation
        else:
            return None

        low, high = lab_def.get('range', [0, 100])
        interp = Interpretation.NORMAL if low <= value <= high else Interpretation.ABNORMAL

        return LabResult(
            display_name=lab_def['display'],
            code=CodeableConcept(
                system="http://loinc.org",
                code=lab_def['loinc'],
                display=lab_def['display']
            ),
            value=str(value),
            unit=lab_def.get('unit', ''),
            interpretation=interp,
            resulted_date=datetime.combine(encounter_date, datetime.min.time()),
            reference_range=ReferenceRange(low=low, high=high) if 'range' in lab_def else None
        )

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

        # Snapshot at adult start (age 18/216 months)
        adult_start = 216
        if current_age_months >= adult_start:
            snapshots.append(TimeSnapshot(
                age_months=adult_start,
                date=dob + timedelta(days=adult_start * 30),
                conditions=[c for c in patient.problem_list
                           if c.onset_date and (c.onset_date - dob).days <= adult_start * 30],
                medications=[],
                immunizations=[],
                key_events=["Entered adult care"],
                narrative="Transitioned to adult primary care.",
            ))

        # Add snapshots at condition onsets
        for condition in patient.problem_list:
            if condition.onset_date:
                age_at_onset = (condition.onset_date - dob).days // 30
                if adult_start < age_at_onset <= current_age_months:
                    snapshots.append(TimeSnapshot(
                        age_months=age_at_onset,
                        date=condition.onset_date,
                        conditions=[c for c in patient.problem_list
                                   if c.onset_date and c.onset_date <= condition.onset_date],
                        medications=[m for m in patient.medication_list
                                    if m.start_date and m.start_date <= condition.onset_date],
                        immunizations=[],
                        key_events=[f"Diagnosed with {condition.display_name}"],
                        narrative=f"New diagnosis of {condition.display_name}. Treatment initiated.",
                    ))

        # Add disease arc stage transitions
        for arc in disease_arcs:
            for i, stage in enumerate(arc.stages):
                if i <= arc.current_stage_index:
                    min_age, max_age = stage.typical_age_range
                    stage_age = random.randint(min_age, min(max_age, current_age_months))
                    stage_date = dob + timedelta(days=stage_age * 30)

                    # Don't duplicate if we already have a snapshot at this time
                    if not any(abs(s.age_months - stage_age) < 12 for s in snapshots):
                        snapshots.append(TimeSnapshot(
                            age_months=stage_age,
                            date=stage_date,
                            conditions=[],  # Will be filled in below
                            medications=[],
                            immunizations=[],
                            key_events=[f"{arc.name}: Progressed to {stage.display_name}"],
                            narrative=f"Disease arc '{arc.name}' progressed to stage: {stage.display_name}. "
                                     f"Symptoms: {', '.join(stage.symptoms[:3])}.",
                        ))

        # Current snapshot
        snapshots.append(TimeSnapshot(
            age_months=current_age_months,
            date=date.today(),
            conditions=patient.problem_list,
            medications=patient.medication_list,
            immunizations=patient.immunization_record,
            key_events=["Current state"],
            narrative="Current clinical status.",
        ))

        # Sort by age
        snapshots.sort(key=lambda s: s.age_months)

        # Deduplicate and limit
        seen_ages = set()
        unique_snapshots = []
        for s in snapshots:
            age_bucket = s.age_months // 12  # Group by year
            if age_bucket not in seen_ages:
                seen_ages.add(age_bucket)
                unique_snapshots.append(s)

        return unique_snapshots[:20]  # Limit to 20 snapshots
