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
    CodeableConcept,
    ComplexityTier,
    Condition,
    ConditionStatus,
    Demographics,
    Encounter,
    EncounterType,
    GenerationSeed,
    GrowthMeasurement,
    Immunization,
    Medication,
    MedicationStatus,
    Patient,
    Provider,
    Location,
    Sex,
    SocialHistory,
    HouseholdMember,
    Address,
    Contact,
)


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
        yaml_path = Path(__file__).parent.parent.parent / "knowledge" / "conditions" / "adult_conditions.yaml"
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
    """
    
    def __init__(self, knowledge_dir: Path | None = None):
        self.knowledge_dir = knowledge_dir or Path(__file__).parent.parent.parent / "knowledge"
        self._load_knowledge()
    
    def _load_knowledge(self):
        """Load knowledge bases for adult care."""
        # TODO: Load USPSTF schedule, condition definitions, medication formulary
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
        
        # Determine complexity tier
        tier = self._determine_complexity(life_arc)
        
        # Build patient
        patient = Patient(
            demographics=demographics,
            social_history=social_history,
            problem_list=problem_list,
            medication_list=medications,
            encounters=encounters,
            complexity_tier=tier,
            generation_seed=seed.model_dump(),
        )
        
        return patient
    
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
        """Generate an adult wellness/preventive visit."""
        
        life_stage = get_life_stage(visit_age)
        
        # Chief complaint varies by age
        if visit_age >= 65:
            chief_complaint = "Medicare Annual Wellness Visit"
        else:
            chief_complaint = "Annual physical examination"
        
        # Generate vitals
        from src.models import VitalSigns
        
        height_m = random.uniform(1.55, 1.90) if demographics.sex_at_birth == Sex.MALE else random.uniform(1.50, 1.75)
        weight_kg = weight_trajectory.get_weight_at_age(visit_age, height_m)
        
        # BP - higher if hypertensive
        if "hypertension" in life_arc.chronic_conditions:
            systolic = random.randint(125, 145)
            diastolic = random.randint(78, 92)
        else:
            systolic = random.randint(110, 128)
            diastolic = random.randint(65, 82)
        
        vitals = VitalSigns(
            date=visit_date,
            blood_pressure_systolic=systolic,
            blood_pressure_diastolic=diastolic,
            heart_rate=random.randint(60, 85),
            respiratory_rate=random.randint(12, 18),
            temperature_celsius=round(random.uniform(36.4, 37.2), 1),
            oxygen_saturation=random.randint(95, 100),
            height_cm=round(height_m * 100, 1),
            weight_kg=round(weight_kg, 1),
        )
        
        return Encounter(
            date=visit_date,
            type=EncounterType.ANNUAL_PHYSICAL,
            chief_complaint=chief_complaint,
            vitals=vitals,
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
        """Generate encounters related to a chronic condition."""
        
        encounters = []
        years_with_condition = current_age - onset_age
        
        # Initial diagnosis encounter
        diagnosis_date = date.today() - timedelta(days=years_with_condition * 365 + random.randint(-30, 30))
        
        diagnosis_encounter = Encounter(
            date=diagnosis_date,
            type=EncounterType.ACUTE_ILLNESS,
            chief_complaint=f"New diagnosis: {condition.replace('_', ' ').title()}",
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
        for year in range(years_with_condition):
            # More visits in first year, then 2-4 per year
            visits_this_year = 4 if year == 0 else random.randint(2, 4)
            
            for visit in range(visits_this_year):
                visit_date = date.today() - timedelta(
                    days=(years_with_condition - year) * 365 - visit * 90 + random.randint(-15, 15)
                )
                
                if visit_date > date.today():
                    continue
                
                followup = Encounter(
                    date=visit_date,
                    type=EncounterType.CHRONIC_FOLLOWUP,
                    chief_complaint=f"{condition.replace('_', ' ').title()} follow-up",
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
        """Generate acute illness encounters."""
        
        encounters = []
        age = demographics.age_years
        
        # Acute visit frequency: 0.5-2 per year depending on health
        visits_per_year = 0.5 if life_arc.health_trajectory == "healthy" else 1.5
        
        acute_reasons = [
            "Upper respiratory infection",
            "Back pain",
            "Urinary symptoms",
            "Skin rash",
            "Headache",
            "Joint pain",
            "GI symptoms",
            "Fatigue",
            "Chest pain evaluation",
            "Shortness of breath",
        ]
        
        for year in range(years_history):
            if random.random() < visits_per_year:
                visit_date = date.today() - timedelta(days=year * 365 + random.randint(0, 364))
                
                encounter = Encounter(
                    date=visit_date,
                    type=EncounterType.ACUTE_ILLNESS,
                    chief_complaint=random.choice(acute_reasons),
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
