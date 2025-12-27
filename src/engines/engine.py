"""
Patient generation engines.

The engines orchestrate the generation of complete synthetic patients
by coordinating the various generators (persona, timeline, encounters, etc.).
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from src.models import (
    Allergy,
    AllergyCategory,
    AllergyReaction,
    AllergySeverity,
    CodeableConcept,
    ComplexityTier,
    Condition,
    ConditionStatus,
    Demographics,
    DevelopmentalMilestone,
    DevelopmentalScreen,
    Encounter,
    EncounterType,
    GenerationSeed,
    GrowthMeasurement,
    Immunization,
    Medication,
    MedicationStatus,
    MessageCategory,
    MessageMedium,
    MessageStatus,
    Patient,
    PatientMessage,
    Provider,
    Location,
    Sex,
    SocialHistory,
)
from src.llm import get_client, LLMClient
from src.engines.messiness import MessinessInjector


class LifeArc(BaseModel):
    """High-level life trajectory for a patient."""
    health_trajectory: str  # "healthy", "single_chronic", "multiple_chronic", "complex"
    major_conditions: list[str]
    condition_onset_ages: dict[str, int]  # condition name -> age in months
    hospitalizations: list[dict[str, Any]]
    surgeries: list[dict[str, Any]]
    key_events: list[dict[str, Any]]


class EncounterStub(BaseModel):
    """A placeholder for an encounter to be fully generated."""
    date: date
    type: EncounterType
    reason: str
    conditions_to_address: list[str]
    is_new_condition_diagnosis: bool = False
    new_condition: str | None = None


class BaseEngine(ABC):
    """Abstract base class for patient generation engines."""
    
    def __init__(
        self,
        llm_client: LLMClient | None = None,
        knowledge_dir: Path | None = None,
    ):
        # LLM client is optional - rule-based generation works without it
        try:
            self.llm = llm_client or get_client()
        except (ValueError, Exception):
            self.llm = None
        self.knowledge_dir = knowledge_dir or Path(__file__).parent.parent.parent / "knowledge"
        
    @abstractmethod
    def generate(self, seed: GenerationSeed) -> Patient:
        """Generate a complete patient record."""
        pass
    
    @abstractmethod
    def generate_life_arc(self, demographics: Demographics, seed: GenerationSeed) -> LifeArc:
        """Generate the high-level life trajectory."""
        pass
    
    @abstractmethod
    def generate_encounter_timeline(
        self,
        demographics: Demographics,
        life_arc: LifeArc,
        seed: GenerationSeed,
    ) -> list[EncounterStub]:
        """Generate the timeline of encounters."""
        pass


class PedsEngine(BaseEngine):
    """
    Pediatric patient generation engine.

    Handles patients from birth through age 21.
    """

    # Class-level cache for conditions data
    _conditions_cache: dict | None = None

    @classmethod
    def _load_conditions(cls, knowledge_dir: Path) -> dict:
        """Load conditions from YAML file, with caching."""
        if cls._conditions_cache is not None:
            return cls._conditions_cache

        conditions_path = knowledge_dir / "conditions" / "conditions.yaml"
        if conditions_path.exists():
            with open(conditions_path, 'r') as f:
                cls._conditions_cache = yaml.safe_load(f)
        else:
            # Fallback to empty dict if file doesn't exist
            cls._conditions_cache = {}

        return cls._conditions_cache

    def __init__(self, *args, use_llm: bool = True, messiness_level: int = 0, **kwargs):
        super().__init__(*args, **kwargs)
        # Load conditions database
        self._conditions = self._load_conditions(self.knowledge_dir)

        # Build derived data structures for efficient lookups
        self._build_condition_lookups()

        # LLM is enabled if requested AND client is available
        self.use_llm = use_llm and self.llm is not None

        # Messiness level for realistic chart artifacts (0=pristine, 5=hostile)
        self.messiness_level = messiness_level
        self.messiness = MessinessInjector(level=messiness_level)

    def _build_condition_lookups(self):
        """Build lookup tables from conditions database."""
        # Chronic conditions with their minimum ages
        self._chronic_conditions = {}
        self._acute_conditions = {}
        self._comorbidity_map = {}
        self._seasonal_weights = self._conditions.get('_seasonal_weights', {})

        for key, data in self._conditions.items():
            if key.startswith('_'):  # Skip metadata keys like _seasonal_weights
                continue
            if not isinstance(data, dict):
                continue

            category = data.get('category', 'acute')
            display_name = data.get('display_name', key.replace('_', ' ').title())
            age_ranges = data.get('age_ranges', {})
            min_months = age_ranges.get('min_months', 0)

            if category == 'chronic':
                self._chronic_conditions[display_name] = {
                    'key': key,
                    'min_months': min_months,
                    'typical_diagnosis_months': age_ranges.get('typical_diagnosis_months', min_months),
                    'comorbidities': data.get('comorbidities', {}),
                }
                # Build comorbidity map
                comorbidities = data.get('comorbidities', {})
                if comorbidities:
                    strong = comorbidities.get('strong', [])
                    moderate = comorbidities.get('moderate', [])
                    comorb_list = []
                    for c in strong:
                        # Convert key to display name
                        c_display = c.replace('_', ' ').title()
                        comorb_list.append((c_display, 0.35))
                    for c in moderate:
                        c_display = c.replace('_', ' ').title()
                        comorb_list.append((c_display, 0.20))
                    if comorb_list:
                        self._comorbidity_map[display_name] = comorb_list
            else:
                self._acute_conditions[display_name] = {
                    'key': key,
                    'min_months': min_months,
                    'seasonality': data.get('seasonality', {}),
                }

        # Build display name to key mapping for reverse lookups
        self._display_to_key = {}
        for key, data in self._conditions.items():
            if key.startswith('_') or not isinstance(data, dict):
                continue
            display_name = data.get('display_name', key.replace('_', ' ').title())
            self._display_to_key[display_name.lower()] = key

    def _get_condition_key(self, reason: str) -> str | None:
        """Map a display name or reason text to a condition key."""
        # Try exact match first (case-insensitive)
        key = self._display_to_key.get(reason.lower())
        if key:
            return key

        # Try partial matching for conditions mentioned in reason
        reason_lower = reason.lower()
        for display_name, key in self._display_to_key.items():
            if display_name in reason_lower:
                return key

        return None

    def _parse_description(self, seed: GenerationSeed) -> GenerationSeed:
        """
        Parse natural language description to extract patient parameters.

        Uses LLM to interpret descriptions like:
        - "healthy 6 month old girl"
        - "3 year old boy with asthma and eczema"
        - "teenager with anxiety and depression"
        """
        import json
        from pydantic import BaseModel

        class ParsedDescription(BaseModel):
            age_months: int | None = None
            sex: str | None = None
            conditions: list[str] = []
            complexity_tier: str | None = None

        prompt = f"""Parse this patient description and extract structured data.

Description: "{seed.description}"

Extract:
- age_months: Patient age in months (e.g., "6 month old" = 6, "2 year old" = 24, "teenager" = 168)
- sex: "male" or "female" (from "boy/girl", "male/female", "son/daughter", etc.)
- conditions: List of medical conditions mentioned (e.g., ["asthma", "eczema"])
- complexity_tier: "tier-0" for healthy, "tier-1" for single chronic, "tier-2" for multiple chronic

If something isn't specified, leave it as null.

Examples:
- "healthy 6 month old girl" -> {{"age_months": 6, "sex": "female", "conditions": [], "complexity_tier": "tier-0"}}
- "3 year old boy with asthma" -> {{"age_months": 36, "sex": "male", "conditions": ["asthma"], "complexity_tier": "tier-1"}}
- "infant with ear infection" -> {{"age_months": 6, "sex": null, "conditions": ["acute otitis media"], "complexity_tier": "tier-0"}}
- "teenager with anxiety and ADHD" -> {{"age_months": 168, "sex": null, "conditions": ["anxiety", "adhd"], "complexity_tier": "tier-2"}}"""

        system = "You are a medical data parser. Extract structured patient information from natural language. Return valid JSON only."

        try:
            result = self.llm.generate_structured(prompt, ParsedDescription, system=system, temperature=0.1)

            # Update seed with parsed values (only if not already set)
            updates = {}
            if result.age_months is not None and seed.age is None and seed.age_months is None:
                updates["age_months"] = result.age_months
            if result.sex and seed.sex is None:
                updates["sex"] = Sex(result.sex)
            if result.conditions and seed.conditions is None:
                updates["conditions"] = result.conditions
            if result.complexity_tier and seed.complexity_tier is None:
                updates["complexity_tier"] = ComplexityTier(result.complexity_tier)

            if updates:
                # Create new seed with updates
                seed_dict = seed.model_dump()
                seed_dict.update(updates)
                return GenerationSeed(**seed_dict)

        except Exception:
            # On any error, return original seed unchanged
            pass

        return seed

    def _apply_vitals_impact(
        self,
        vitals_dict: dict,
        condition_key: str | None,
        encounter_type: EncounterType
    ) -> dict:
        """Apply illness-aware modifications to vitals based on condition."""
        if not condition_key or encounter_type not in (
            EncounterType.ACUTE_ILLNESS,
            EncounterType.URGENT_CARE,
            EncounterType.ED_VISIT
        ):
            return vitals_dict

        condition_data = self._conditions.get(condition_key, {})
        vitals_impact = condition_data.get('vitals_impact', {})

        if not vitals_impact:
            return vitals_dict

        # Apply temperature range if specified
        temp_range = vitals_impact.get('temp_f')
        if temp_range and len(temp_range) == 2:
            vitals_dict['temperature_f'] = random.uniform(temp_range[0], temp_range[1])

        # Apply heart rate multiplier
        hr_mult = vitals_impact.get('hr_multiplier', 1.0)
        if 'heart_rate' in vitals_dict:
            vitals_dict['heart_rate'] = int(vitals_dict['heart_rate'] * hr_mult)

        # Apply respiratory rate multiplier
        rr_mult = vitals_impact.get('rr_multiplier', 1.0)
        if 'respiratory_rate' in vitals_dict:
            vitals_dict['respiratory_rate'] = int(vitals_dict['respiratory_rate'] * rr_mult)

        # Apply SpO2 minimum
        spo2_min = vitals_impact.get('spo2_min')
        if spo2_min is not None:
            # Generate SpO2 between condition-specific minimum and 100
            vitals_dict['o2_sat'] = random.randint(spo2_min, 100)

        return vitals_dict

    def _select_symptoms(self, condition_key: str, age_months: int) -> list[str]:
        """Probabilistically select symptoms from condition definition."""
        condition_data = self._conditions.get(condition_key, {})
        presentation = condition_data.get('presentation', {})
        symptoms_def = presentation.get('symptoms', [])

        if not symptoms_def:
            return []

        selected = []
        for s in symptoms_def:
            # Handle old format (simple strings)
            if isinstance(s, str):
                selected.append(s)
                continue

            # Handle new format (dicts with probability)
            name = s.get('name', '')
            probability = s.get('probability', 1.0)
            description = s.get('description', name)
            age_min = s.get('age_min')
            age_max = s.get('age_max')

            # Check age constraints
            if age_min is not None and age_months < age_min:
                continue
            if age_max is not None and age_months > age_max:
                continue

            # Probabilistic selection
            if random.random() < probability:
                selected.append(description if description else name)

        return selected

    def _generate_condition_physical_exam(
        self,
        condition_key: str | None,
        encounter_type: EncounterType
    ) -> dict[str, str]:
        """Generate condition-specific physical exam findings."""
        from src.models import PhysicalExam

        # Default findings by system
        findings = {
            'general': "Alert, in no acute distress",
            'heent': "Normocephalic, atraumatic",
            'cardiovascular': "Regular rate and rhythm",
            'respiratory': "Clear to auscultation bilaterally",
        }

        if not condition_key or encounter_type not in (
            EncounterType.ACUTE_ILLNESS,
            EncounterType.URGENT_CARE,
            EncounterType.ED_VISIT
        ):
            return findings

        condition_data = self._conditions.get(condition_key, {})
        presentation = condition_data.get('presentation', {})
        exam_findings = presentation.get('physical_exam', [])

        if not exam_findings:
            return findings

        # Apply probabilistic exam findings
        system_findings = {}
        for f in exam_findings:
            if not isinstance(f, dict):
                continue

            system = f.get('system', '').lower()
            finding_text = f.get('finding', '')
            probability = f.get('probability', 1.0)

            if not system or not finding_text:
                continue

            # Probabilistic selection
            if random.random() < probability:
                if system not in system_findings:
                    system_findings[system] = []
                system_findings[system].append(finding_text)

        # Merge condition findings with defaults
        for system, exam_texts in system_findings.items():
            if exam_texts:
                findings[system] = ". ".join(exam_texts)

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
            EncounterType.ED_VISIT
        ):
            return []

        condition_data = self._conditions.get(condition_key, {})
        diagnostics = condition_data.get('diagnostics', {})
        labs_def = diagnostics.get('labs', [])

        if not labs_def:
            return []

        results = []
        for lab in labs_def:
            if not isinstance(lab, dict):
                continue

            name = lab.get('name', '')
            loinc = lab.get('loinc', '')
            result_positive = lab.get('result_positive', 'Positive')
            result_negative = lab.get('result_negative', 'Negative')
            prob_positive = lab.get('probability_positive', 0.5)

            if not name or not loinc:
                continue

            # Probabilistically determine if result is positive
            is_positive = random.random() < prob_positive

            results.append(LabResult(
                code=CodeableConcept(
                    system="http://loinc.org",
                    code=loinc,
                    display=name
                ),
                display_name=name,
                value=result_positive if is_positive else result_negative,
                interpretation=Interpretation.POSITIVE if is_positive else Interpretation.NEGATIVE,
                resulted_date=datetime.combine(encounter_date, datetime.min.time()),
            ))

        return results

    # Well-child visit schedule (age in months)
    WELL_CHILD_SCHEDULE = [
        0,  # Newborn
        1,  # 1 month
        2,  # 2 months
        4,  # 4 months
        6,  # 6 months
        9,  # 9 months
        12, # 12 months
        15, # 15 months
        18, # 18 months
        24, # 2 years
        30, # 2.5 years
        36, # 3 years
        48, # 4 years
        60, # 5 years
        72, # 6 years
        84, # 7 years
        96, # 8 years
        108, # 9 years
        120, # 10 years
        132, # 11 years
        144, # 12 years
        156, # 13 years
        168, # 14 years
        180, # 15 years
        192, # 16 years
        204, # 17 years
        216, # 18 years
        228, # 19 years
        240, # 20 years
        252, # 21 years
    ]
    
    # Acute illness frequency by age (per year)
    ACUTE_ILLNESS_FREQUENCY = {
        (0, 12): 6,    # 0-1 year: ~6 acute visits/year
        (12, 36): 4,   # 1-3 years: ~4 acute visits/year
        (36, 72): 3,   # 3-6 years: ~3 acute visits/year
        (72, 144): 2,  # 6-12 years: ~2 acute visits/year
        (144, 264): 1, # 12-22 years: ~1 acute visit/year
    }

    # Evidence-based pediatric life event rates (annual probability)
    # Sources: CDC, PMC studies on pediatric injuries/surgery
    LIFE_EVENT_DEFINITIONS = {
        "fracture": {
            "annual_rate": 0.02,  # 2% per year (12-30/1000)
            "encounter_type": EncounterType.ED_VISIT,
            "sex_modifier": {"male": 1.4, "female": 0.7},  # Boys 2x more likely
            "age_modifier": {  # Peak at 10-14 years
                (0, 24): 0.3, (24, 72): 0.7, (72, 120): 1.0,
                (120, 168): 1.5, (168, 264): 1.0
            },
            "variants": [
                {"name": "Distal radius fracture", "weight": 0.30, "icd10": "S52.501A"},
                {"name": "Clavicle fracture", "weight": 0.15, "icd10": "S42.001A"},
                {"name": "Distal humerus fracture", "weight": 0.12, "icd10": "S42.401A"},
                {"name": "Tibial fracture", "weight": 0.10, "icd10": "S82.201A"},
                {"name": "Finger fracture", "weight": 0.15, "icd10": "S62.600A"},
                {"name": "Toe fracture", "weight": 0.08, "icd10": "S92.501A"},
                {"name": "Ankle fracture", "weight": 0.10, "icd10": "S82.891A"},
            ],
        },
        "laceration": {
            "annual_rate": 0.025,  # 2.5% per year
            "encounter_type": EncounterType.URGENT_CARE,
            "sex_modifier": {"male": 1.3, "female": 0.8},
            "age_modifier": {
                (0, 24): 0.5, (24, 72): 1.2, (72, 144): 1.0, (144, 264): 0.8
            },
            "variants": [
                {"name": "Forehead laceration", "weight": 0.25, "icd10": "S01.81XA"},
                {"name": "Chin laceration", "weight": 0.20, "icd10": "S01.81XA"},
                {"name": "Finger laceration", "weight": 0.20, "icd10": "S61.219A"},
                {"name": "Knee laceration", "weight": 0.15, "icd10": "S81.01XA"},
                {"name": "Scalp laceration", "weight": 0.10, "icd10": "S01.01XA"},
                {"name": "Lip laceration", "weight": 0.10, "icd10": "S01.511A"},
            ],
        },
        "concussion": {
            "annual_rate": 0.01,  # 1% per year
            "encounter_type": EncounterType.ED_VISIT,
            "sex_modifier": {"male": 1.2, "female": 0.9},
            "age_modifier": {  # Higher in sports ages
                (0, 60): 0.4, (60, 120): 0.8, (120, 168): 1.5, (168, 264): 1.3
            },
            "variants": [
                {"name": "Concussion without loss of consciousness", "weight": 0.70, "icd10": "S06.0X0A"},
                {"name": "Concussion with brief loss of consciousness", "weight": 0.30, "icd10": "S06.0X1A"},
            ],
        },
        "accidental_poisoning": {
            "annual_rate": 0.007,  # 0.7% for young children
            "encounter_type": EncounterType.ED_VISIT,
            "age_modifier": {  # Mostly toddlers (1-4 years)
                (0, 12): 0.5, (12, 48): 2.5, (48, 72): 0.5, (72, 264): 0.1
            },
            "variants": [
                {"name": "Accidental ingestion of medication", "weight": 0.50, "icd10": "T50.901A"},
                {"name": "Accidental ingestion of household product", "weight": 0.30, "icd10": "T65.91XA"},
                {"name": "Accidental ingestion of plant material", "weight": 0.10, "icd10": "T62.2X1A"},
                {"name": "Accidental ingestion of cosmetic", "weight": 0.10, "icd10": "T49.8X1A"},
            ],
        },
        "minor_burn": {
            "annual_rate": 0.005,  # 0.5% per year
            "encounter_type": EncounterType.URGENT_CARE,
            "age_modifier": {  # Higher in toddlers
                (0, 12): 0.5, (12, 48): 2.0, (48, 120): 0.8, (120, 264): 0.5
            },
            "variants": [
                {"name": "Scald burn to hand", "weight": 0.35, "icd10": "T23.001A"},
                {"name": "Contact burn to hand", "weight": 0.25, "icd10": "T23.001A"},
                {"name": "Scald burn to arm", "weight": 0.20, "icd10": "T22.001A"},
                {"name": "Sunburn", "weight": 0.20, "icd10": "L55.9"},
            ],
        },
        "foreign_body": {
            "annual_rate": 0.008,  # 0.8% per year
            "encounter_type": EncounterType.URGENT_CARE,
            "age_modifier": {  # Toddlers and young children
                (0, 12): 0.3, (12, 60): 2.0, (60, 120): 0.8, (120, 264): 0.3
            },
            "variants": [
                {"name": "Foreign body in ear", "weight": 0.35, "icd10": "T16.9XXA"},
                {"name": "Foreign body in nose", "weight": 0.35, "icd10": "T17.1XXA"},
                {"name": "Foreign body in eye", "weight": 0.20, "icd10": "T15.90XA"},
                {"name": "Splinter", "weight": 0.10, "icd10": "W45.8XXA"},
            ],
        },
        "medication_allergy_discovery": {
            "annual_rate": 0.015,  # 1.5% per year discover new allergy
            "encounter_type": None,  # Creates allergy, not encounter
            "variants": [
                {"name": "Amoxicillin", "weight": 0.40, "rxnorm": "723"},
                {"name": "Penicillin", "weight": 0.25, "rxnorm": "7984"},
                {"name": "Sulfonamide", "weight": 0.15, "rxnorm": "10831"},
                {"name": "Ibuprofen", "weight": 0.10, "rxnorm": "5640"},
                {"name": "Cephalosporin", "weight": 0.10, "rxnorm": "2176"},
            ],
        },
        "tympanostomy_tubes": {
            "annual_rate": 0.015,  # 1.5% for ages 1-5
            "encounter_type": EncounterType.PROCEDURE,
            "age_modifier": {  # Only ages 1-5
                (0, 12): 0.2, (12, 60): 3.0, (60, 264): 0.0
            },
            "is_surgery": True,
            "icd10": "Z96.20",
            "cpt": "69436",
        },
        "appendectomy": {
            "annual_rate": 0.001,  # 0.1% per year
            "encounter_type": EncounterType.HOSPITAL_ADMISSION,
            "age_modifier": {  # Peak 10-18 years
                (0, 60): 0.3, (60, 120): 0.8, (120, 216): 2.0, (216, 264): 1.0
            },
            "is_surgery": True,
            "icd10": "K35.80",
            "cpt": "44970",
        },
        "anaphylaxis": {
            "annual_rate": 0.001,  # 0.1% per year (rare but important)
            "encounter_type": EncounterType.ED_VISIT,
            "variants": [
                {"name": "Anaphylaxis due to food", "weight": 0.60, "icd10": "T78.00XA"},
                {"name": "Anaphylaxis due to insect sting", "weight": 0.25, "icd10": "T63.441A"},
                {"name": "Anaphylaxis due to medication", "weight": 0.15, "icd10": "T88.6XXA"},
            ],
        },
    }

    def generate(self, seed: GenerationSeed) -> Patient:
        """Generate a complete pediatric patient."""
        # Parse natural language description if provided
        if seed.description and self.use_llm:
            seed = self._parse_description(seed)

        # Set random seed for reproducibility
        if seed.random_seed is not None:
            random.seed(seed.random_seed)

        # Step 1: Generate persona (demographics, family, social)
        demographics = self._generate_demographics(seed)
        social_history = self._generate_social_history(demographics, seed)
        
        # Step 2: Generate life arc
        life_arc = self.generate_life_arc(demographics, seed)
        
        # Step 3: Generate encounter timeline
        encounter_stubs = self.generate_encounter_timeline(demographics, life_arc, seed)
        
        # Step 4: Generate growth trajectory
        from knowledge.growth.cdc_2000 import GrowthTrajectory
        sex = "male" if demographics.sex_at_birth == Sex.MALE else "female"
        
        # Determine starting percentiles (can be influenced by conditions)
        weight_pct = random.gauss(50, 20)
        height_pct = random.gauss(50, 20)
        weight_pct = max(5, min(95, weight_pct))
        height_pct = max(5, min(95, height_pct))
        
        growth = GrowthTrajectory(
            sex=sex,
            weight_percentile=weight_pct,
            height_percentile=height_pct,
            hc_percentile=random.gauss(50, 15),
        )
        
        # Step 5: Generate each encounter
        encounters = []
        growth_data = []
        immunizations = []
        
        # Create a default provider and location
        provider = Provider(
            name=self._generate_provider_name(),
            credentials="MD",
            specialty="Pediatrics",
        )
        location = Location(
            name="Main Street Pediatrics",
            type="Outpatient clinic",
        )
        
        total_encounters = len(encounter_stubs)
        for idx, stub in enumerate(encounter_stubs):
            # Calculate age at encounter
            days_old = (stub.date - demographics.date_of_birth).days
            months_old = days_old // 30

            # Determine timeline position for messiness injection
            # Early = first 20% of encounters, Middle = 20-70%, Recent = last 30%
            position_ratio = idx / max(total_encounters - 1, 1)
            if position_ratio <= 0.2:
                timeline_position = "early"
            elif position_ratio <= 0.7:
                timeline_position = "middle"
            else:
                timeline_position = "recent"

            # Generate growth measurement if well-child or appropriate
            if stub.type in (EncounterType.WELL_CHILD, EncounterType.NEWBORN):
                weight, height, hc, bmi = growth.generate_measurement(months_old)
                growth_data.append(GrowthMeasurement(
                    date=stub.date,
                    age_in_days=days_old,
                    weight_kg=weight,
                    height_cm=height,
                    head_circumference_cm=hc,
                    bmi=bmi,
                ))

            # Generate the full encounter
            encounter = self._generate_encounter(
                stub=stub,
                demographics=demographics,
                age_months=months_old,
                growth_data=growth_data,
                life_arc=life_arc,
                provider=provider,
                location=location,
                seed=seed,
                encounter_index=idx,
                timeline_position=timeline_position,
            )
            encounters.append(encounter)
            
            # Collect immunizations
            immunizations.extend(encounter.immunizations_given)

        # Generate all LLM content in parallel if enabled
        if self.use_llm and seed.include_narrative_notes:
            self._generate_narratives_parallel(encounters, demographics, life_arc)

        # Determine complexity tier
        if len(life_arc.major_conditions) == 0:
            tier = ComplexityTier.TIER_0
        elif len(life_arc.major_conditions) == 1:
            tier = ComplexityTier.TIER_1
        elif len(life_arc.major_conditions) <= 3:
            tier = ComplexityTier.TIER_2
        else:
            tier = ComplexityTier.TIER_3
        
        # Create Condition objects from life_arc
        problem_list = []
        for cond_name in life_arc.major_conditions:
            onset_months = life_arc.condition_onset_ages.get(cond_name, 24)
            onset_date = demographics.date_of_birth + timedelta(days=onset_months * 30)
            
            # Map condition names to ICD-10 codes (case-insensitive lookup)
            condition_codes = {
                "asthma": ("J45.20", "Mild intermittent asthma, uncomplicated"),
                "adhd": ("F90.2", "Attention-deficit hyperactivity disorder, combined type"),
                "eczema": ("L30.9", "Dermatitis, unspecified"),
                "allergic rhinitis": ("J30.9", "Allergic rhinitis, unspecified"),
                "anxiety": ("F41.1", "Generalized anxiety disorder"),
                "food allergy": ("T78.1", "Other adverse food reactions, not elsewhere classified"),
                "obesity": ("E66.9", "Obesity, unspecified"),
                "constipation": ("K59.00", "Constipation, unspecified"),
                "recurrent otitis media": ("H66.90", "Otitis media, unspecified"),
                "depression": ("F32.9", "Major depressive disorder, single episode, unspecified"),
                "autism": ("F84.0", "Autistic disorder"),
                "type 1 diabetes": ("E10.9", "Type 1 diabetes mellitus without complications"),
                "type 2 diabetes": ("E11.9", "Type 2 diabetes mellitus without complications"),
                "seizure disorder": ("G40.909", "Epilepsy, unspecified, not intractable"),
                "epilepsy": ("G40.909", "Epilepsy, unspecified, not intractable"),
                "cerebral palsy": ("G80.9", "Cerebral palsy, unspecified"),
            }
            
            # Case-insensitive lookup
            lookup_key = cond_name.lower().strip()
            code, display = condition_codes.get(lookup_key, ("R69", cond_name))
            
            # Normalize display name
            display_name = cond_name.title() if code != "R69" else cond_name
            
            condition = Condition(
                display_name=display_name,
                code=CodeableConcept(
                    system="http://hl7.org/fhir/sid/icd-10-cm",
                    code=code,
                    display=display,
                ),
                clinical_status=ConditionStatus.ACTIVE,
                onset_date=onset_date,
            )
            problem_list.append(condition)
        
        # Generate message frequency (some patients message a lot, some rarely)
        # Higher complexity patients tend to message more
        base_frequency = random.random()
        complexity_boost = len(life_arc.major_conditions) * 0.1
        message_frequency = min(1.0, base_frequency + complexity_boost)

        # Generate patient messages based on encounters, medications, and frequency
        patient_messages = self._generate_patient_messages(
            demographics=demographics,
            encounters=encounters,
            conditions=problem_list,
            message_frequency=message_frequency,
            provider=provider,
        )

        # Extract resolved conditions and past medications from acute illness encounters
        resolved_conditions, past_medications = self._extract_resolved_history(encounters)
        problem_list.extend(resolved_conditions)

        # Create Allergy objects from discovered medication allergies (life events)
        allergy_list = []
        discovered_allergies = getattr(self, '_discovered_allergies', [])
        for allergy_info in discovered_allergies:
            allergy = Allergy(
                display_name=f"{allergy_info['substance']} allergy",
                category=AllergyCategory.MEDICATION,
                criticality=random.choice(["low", "high"]),
                reactions=[
                    AllergyReaction(
                        manifestation=random.choice([
                            "Rash", "Hives", "Itching", "Swelling",
                            "Nausea", "Stomach upset"
                        ]),
                        severity=random.choice([AllergySeverity.MILD, AllergySeverity.MODERATE]),
                    )
                ],
                onset_date=allergy_info["discovery_date"],
                code=CodeableConcept(
                    system="http://www.nlm.nih.gov/research/umls/rxnorm",
                    code=allergy_info.get("rxnorm", ""),
                    display=allergy_info["substance"],
                ) if allergy_info.get("rxnorm") else None,
            )
            allergy_list.append(allergy)

        # Build the patient
        patient = Patient(
            demographics=demographics,
            social_history=social_history,
            problem_list=problem_list,
            medication_list=past_medications,
            allergy_list=allergy_list,
            encounters=encounters,
            growth_data=growth_data,
            immunization_record=immunizations,
            complexity_tier=tier,
            generation_seed=seed.model_dump(),
            patient_messages=patient_messages,
            message_frequency=message_frequency,
        )

        return patient
    
    def _generate_demographics(self, seed: GenerationSeed) -> Demographics:
        """Generate patient demographics."""
        # Age
        if seed.age is not None:
            age_months = seed.age * 12
        elif seed.age_months is not None:
            age_months = seed.age_months
        else:
            # Random age 0-21 years
            age_months = random.randint(0, 252)
        
        # Sex
        sex = seed.sex or random.choice([Sex.MALE, Sex.FEMALE])
        
        # Calculate DOB
        today = date.today()
        dob = today - timedelta(days=age_months * 30)
        
        # Generate name based on sex
        first_name = self._generate_first_name(sex)
        last_name = self._generate_last_name()
        
        # Address
        from src.models import Address, Contact
        address = Address(
            line1=f"{random.randint(100, 9999)} {random.choice(['Oak', 'Maple', 'Cedar', 'Pine', 'Main', 'First', 'Park'])} {random.choice(['Street', 'Avenue', 'Lane', 'Drive', 'Court'])}",
            city=random.choice(["Springfield", "Riverside", "Lakewood", "Fairview", "Madison"]),
            state=seed.state or random.choice(["MN", "WI", "CA", "TX", "NY", "FL", "IL"]),
            postal_code=f"{random.randint(10000, 99999)}",
        )
        
        # Emergency contact (parent) - ensure realistic age gap
        # Parent should be at least 18 + child's age, typically 22-38 years older
        child_age_years = age_months // 12
        parent_age_gap = random.randint(22, 38)
        parent_age = child_age_years + parent_age_gap

        parent_sex = random.choice([Sex.MALE, Sex.FEMALE])
        parent_first = self._generate_first_name(parent_sex)
        emergency_contact = Contact(
            name=f"{parent_first} {last_name}",
            relationship="Mother" if parent_sex == Sex.FEMALE else "Father",
            phone=self._generate_phone(),
        )
        # Store parent age for social history generation
        self._last_parent_age = parent_age

        # Guardian for minors
        guardian = Contact(
            name=emergency_contact.name,
            relationship=emergency_contact.relationship,
            phone=emergency_contact.phone,
        ) if age_months < 216 else None
        
        return Demographics(
            given_names=[first_name],
            family_name=last_name,
            date_of_birth=dob,
            sex_at_birth=sex,
            race=seed.race or [random.choice(["White", "Black or African American", "Asian", "Two or more races"])],
            ethnicity=seed.ethnicity or random.choice(["Not Hispanic or Latino", "Hispanic or Latino"]),
            preferred_language="English",
            address=address,
            phone=self._generate_phone(),
            emergency_contact=emergency_contact,
            legal_guardian=guardian,
        )
    
    def _generate_social_history(self, demographics: Demographics, seed: GenerationSeed) -> SocialHistory:
        """Generate social history appropriate to age."""
        from src.models import HouseholdMember, SubstanceUse
        
        age_years = demographics.age_years
        
        # Household
        household = []
        if age_years < 18:
            # Add parents - use realistic parent age based on child's age
            parent_age = getattr(self, '_last_parent_age', age_years + random.randint(22, 38))
            household.append(HouseholdMember(
                name=demographics.legal_guardian.name if demographics.legal_guardian else "Parent",
                relationship="Parent",
                age=parent_age,
            ))
            if random.random() > 0.3:  # 70% two-parent households
                # Second parent within a few years of first parent
                second_parent_age = parent_age + random.randint(-5, 5)
                household.append(HouseholdMember(
                    name="Parent",
                    relationship="Parent",
                    age=max(age_years + 18, second_parent_age),  # Ensure at least 18 years older than child
                ))
            # Maybe siblings
            if random.random() > 0.4:
                household.append(HouseholdMember(
                    name="Sibling",
                    relationship="Sibling",
                    age=random.randint(1, 18),
                ))
        
        # School/childcare
        school_name = None
        grade_level = None
        if 3 <= age_years < 5:
            school_name = "Little Stars Preschool"
            grade_level = "Preschool"
        elif 5 <= age_years < 18:
            school_name = f"{demographics.address.city} {random.choice(['Elementary', 'Middle', 'High'])} School"
            grade_level = self._age_to_grade(age_years)
        
        return SocialHistory(
            living_situation=f"Lives with {'parents' if age_years < 18 else 'independently'}",
            household_members=household,
            school_name=school_name,
            grade_level=grade_level,
            school_performance="Good" if random.random() > 0.3 else random.choice(["Excellent", "Average", "Struggling"]),
            tobacco=SubstanceUse(substance="tobacco", status="never") if age_years >= 11 else None,
            food_security="secure",
            housing_stability="stable",
            transportation_access="adequate",
            firearms_in_home=random.random() > 0.7,
        )
    
    def generate_life_arc(self, demographics: Demographics, seed: GenerationSeed) -> LifeArc:
        """Generate the high-level life trajectory."""
        # Determine complexity
        if seed.complexity_tier:
            tier = seed.complexity_tier
        elif seed.conditions:
            tier = ComplexityTier.TIER_1 if len(seed.conditions) == 1 else ComplexityTier.TIER_2
        else:
            # Random weighted distribution
            tier = random.choices(
                [ComplexityTier.TIER_0, ComplexityTier.TIER_1, ComplexityTier.TIER_2, ComplexityTier.TIER_3],
                weights=[60, 25, 12, 3],
            )[0]
        
        # Determine conditions based on tier
        conditions = []
        onset_ages = {}
        
        if seed.conditions:
            conditions = list(seed.conditions)
            # Apply co-morbidity logic to user-specified conditions
            conditions = self._apply_comorbidity_logic(conditions)
            # Generate plausible onset ages
            for cond in conditions:
                max_onset = min(demographics.age_months, 120)
                min_onset = min(6, max_onset)  # Don't use 6 if child is younger
                onset_ages[cond] = random.randint(min_onset, max(min_onset, max_onset))
        elif tier != ComplexityTier.TIER_0:
            # Use conditions loaded from YAML database
            age_months = demographics.age_months

            # Filter to age-appropriate chronic conditions
            condition_pool = [
                name for name, data in self._chronic_conditions.items()
                if data['min_months'] <= age_months
            ]

            # If too young for most conditions, use infant-appropriate ones
            if len(condition_pool) < 3:
                infant_conditions = ["Eczema", "GERD", "Food Allergy"]
                condition_pool = [
                    c for c in infant_conditions
                    if c in self._chronic_conditions and
                    self._chronic_conditions[c]['min_months'] <= age_months
                ]

            # If still no valid conditions for this age, return healthy
            if not condition_pool:
                return LifeArc(
                    health_trajectory="healthy",
                    major_conditions=[],
                    condition_onset_ages={},
                    hospitalizations=[],
                )

            num_conditions = {
                ComplexityTier.TIER_1: 1,
                ComplexityTier.TIER_2: random.randint(2, 3),
                ComplexityTier.TIER_3: random.randint(3, 5),
            }.get(tier, 0)

            conditions = random.sample(condition_pool, min(num_conditions, len(condition_pool)))

            # Apply co-morbidity logic - conditions tend to cluster
            conditions = self._apply_comorbidity_logic(conditions)

            for cond in conditions:
                # Use condition-specific minimum onset age from YAML
                cond_data = self._chronic_conditions.get(cond, {})
                cond_min_age = cond_data.get('min_months', 6)
                max_onset = min(demographics.age_months, 120)
                min_onset = min(cond_min_age, max_onset)
                onset_ages[cond] = random.randint(min_onset, max(min_onset, max_onset))
        
        trajectory = "healthy" if not conditions else "single_chronic" if len(conditions) == 1 else "multiple_chronic"
        
        return LifeArc(
            health_trajectory=trajectory,
            major_conditions=conditions,
            condition_onset_ages=onset_ages,
            hospitalizations=[],
            surgeries=[],
            key_events=[],
        )
    
    def generate_encounter_timeline(
        self,
        demographics: Demographics,
        life_arc: LifeArc,
        seed: GenerationSeed,
    ) -> list[EncounterStub]:
        """Generate the timeline of encounter stubs."""
        stubs = []
        dob = demographics.date_of_birth
        current_age_months = demographics.age_months
        today = date.today()
        
        # Well-child visits
        for visit_age in self.WELL_CHILD_SCHEDULE:
            if visit_age > current_age_months:
                break
            
            visit_date = dob + timedelta(days=visit_age * 30)
            if visit_date > today:
                continue
                
            # Add some realistic variation to dates
            visit_date += timedelta(days=random.randint(-7, 14))
            
            stub = EncounterStub(
                date=visit_date,
                type=EncounterType.NEWBORN if visit_age == 0 else EncounterType.WELL_CHILD,
                reason=f"Well-child visit - {self._age_to_description(visit_age)}",
                conditions_to_address=[],
            )
            stubs.append(stub)
        
        # Acute illness visits
        # Minimum age for routine acute illness visits (newborns handled differently)
        MIN_ACUTE_ILLNESS_AGE = 2  # months

        for (min_age, max_age), frequency in self.ACUTE_ILLNESS_FREQUENCY.items():
            if current_age_months < min_age:
                continue

            # How many months of this age range has the patient lived?
            # Don't start acute illness visits before MIN_ACUTE_ILLNESS_AGE
            start_month = max(MIN_ACUTE_ILLNESS_AGE, min_age)
            end_month = min(current_age_months, max_age)
            months_in_range = end_month - start_month

            if months_in_range <= 0:
                continue

            # Expected number of acute visits in this range
            expected_visits = (months_in_range / 12) * frequency
            actual_visits = int(expected_visits + random.random())  # Randomize

            for _ in range(actual_visits):
                # Random date in this age range
                visit_age_months = random.randint(start_month, end_month)
                visit_date = dob + timedelta(days=visit_age_months * 30 + random.randint(0, 29))

                if visit_date > today:
                    continue

                # Select illness based on season AND age for realism
                illness = self._get_seasonal_illness(visit_date, visit_age_months)
                
                stub = EncounterStub(
                    date=visit_date,
                    type=EncounterType.ACUTE_ILLNESS,
                    reason=illness,
                    conditions_to_address=[],
                )
                stubs.append(stub)
        
        # Condition-related visits
        for condition, onset_age in life_arc.condition_onset_ages.items():
            if onset_age > current_age_months:
                continue
            
            # Initial diagnosis visit
            diagnosis_date = dob + timedelta(days=onset_age * 30)
            stubs.append(EncounterStub(
                date=diagnosis_date,
                type=EncounterType.CHRONIC_FOLLOWUP,
                reason=f"Evaluation for {condition.lower()} symptoms",
                conditions_to_address=[condition],
                is_new_condition_diagnosis=True,
                new_condition=condition,
            ))
            
            # Follow-up visits (every 3-6 months)
            follow_up_age = onset_age + random.randint(2, 4)
            while follow_up_age < current_age_months:
                fu_date = dob + timedelta(days=follow_up_age * 30)
                if fu_date <= today:
                    stubs.append(EncounterStub(
                        date=fu_date,
                        type=EncounterType.CHRONIC_FOLLOWUP,
                        reason=f"{condition} follow-up",
                        conditions_to_address=[condition],
                    ))
                follow_up_age += random.randint(3, 6)
        
        # Generate random life events (injuries, surgeries, etc.)
        life_event_stubs, discovered_allergies = self._generate_life_events(
            demographics=demographics,
            current_age_months=current_age_months,
        )
        stubs.extend(life_event_stubs)

        # Sort by date
        stubs.sort(key=lambda s: s.date)

        # Limit to requested count if specified
        if seed.encounter_count:
            stubs = stubs[:seed.encounter_count]

        # Store discovered allergies for later use in patient generation
        self._discovered_allergies = discovered_allergies

        return stubs

    def _generate_life_events(
        self,
        demographics: Demographics,
        current_age_months: int,
    ) -> tuple[list[EncounterStub], list[dict]]:
        """
        Generate random life events based on evidence-based pediatric rates.

        Returns tuple of (encounter_stubs, discovered_allergies).
        """
        stubs = []
        discovered_allergies = []
        dob = demographics.date_of_birth
        today = date.today()
        sex = "male" if demographics.sex_at_birth == Sex.MALE else "female"

        # Track what events have occurred to avoid duplicates
        had_tympanostomy = False
        had_appendectomy = False

        # For each year of life, roll dice for each event type
        for age_year in range(current_age_months // 12 + 1):
            age_months_start = age_year * 12
            age_months_end = min((age_year + 1) * 12, current_age_months)

            for event_type, event_def in self.LIFE_EVENT_DEFINITIONS.items():
                # Skip one-time surgeries if already had
                if event_type == "tympanostomy_tubes" and had_tympanostomy:
                    continue
                if event_type == "appendectomy" and had_appendectomy:
                    continue

                # Calculate adjusted probability
                base_rate = event_def["annual_rate"]

                # Apply sex modifier if exists
                sex_mod = event_def.get("sex_modifier", {}).get(sex, 1.0)

                # Apply age modifier if exists
                age_mod = 1.0
                age_modifiers = event_def.get("age_modifier", {})
                for (min_m, max_m), modifier in age_modifiers.items():
                    if min_m <= age_months_start < max_m:
                        age_mod = modifier
                        break

                adjusted_rate = base_rate * sex_mod * age_mod

                # Roll the dice
                if random.random() < adjusted_rate:
                    # Event occurred!
                    event_age_months = random.randint(age_months_start, age_months_end)
                    event_date = dob + timedelta(days=event_age_months * 30 + random.randint(0, 29))

                    if event_date > today:
                        continue

                    # Select variant if applicable
                    variants = event_def.get("variants", [])
                    if variants:
                        weights = [v["weight"] for v in variants]
                        variant = random.choices(variants, weights=weights)[0]
                        event_name = variant["name"]
                        icd10_code = variant.get("icd10", "")
                    else:
                        event_name = event_type.replace("_", " ").title()
                        icd10_code = event_def.get("icd10", "")

                    # Handle different event types
                    encounter_type = event_def.get("encounter_type")

                    if event_type == "medication_allergy_discovery":
                        # Create an allergy record instead of encounter
                        discovered_allergies.append({
                            "substance": event_name,
                            "rxnorm": variant.get("rxnorm", ""),
                            "discovery_date": event_date,
                        })
                    elif encounter_type:
                        # Create encounter stub
                        stub = EncounterStub(
                            date=event_date,
                            type=encounter_type,
                            reason=event_name,
                            conditions_to_address=[],
                        )
                        stubs.append(stub)

                        # Mark one-time events
                        if event_type == "tympanostomy_tubes":
                            had_tympanostomy = True
                        elif event_type == "appendectomy":
                            had_appendectomy = True

        return stubs, discovered_allergies

    def _generate_encounter(
        self,
        stub: EncounterStub,
        demographics: Demographics,
        age_months: int,
        growth_data: list[GrowthMeasurement],
        life_arc: LifeArc,
        provider: Provider,
        location: Location,
        seed: GenerationSeed,
        encounter_index: int = 0,
        timeline_position: str = "middle",
    ) -> Encounter:
        """Generate a full encounter from a stub."""
        from src.models import (
            VitalSigns, PhysicalExam, Assessment, PlanItem,
            GrowthPercentiles, ReviewOfSystems
        )
        from knowledge.growth.cdc_2000 import (
            generate_normal_vitals, calculate_weight_percentile,
            calculate_height_percentile, calculate_hc_percentile,
            calculate_bmi_percentile
        )
        
        sex = "male" if demographics.sex_at_birth == Sex.MALE else "female"
        
        # Get latest growth data
        latest_growth = growth_data[-1] if growth_data else None

        # Generate vitals (illness-aware for acute encounters)
        vitals_dict = generate_normal_vitals(age_months)

        # Apply illness-specific vital sign modifications
        condition_key = self._get_condition_key(stub.reason)
        vitals_dict = self._apply_vitals_impact(vitals_dict, condition_key, stub.type)

        vitals = VitalSigns(
            date=datetime.combine(stub.date, datetime.min.time()),
            temperature_f=vitals_dict.get("temperature_f", 98.6),
            heart_rate=int(vitals_dict.get("heart_rate", 80)),
            respiratory_rate=int(vitals_dict.get("respiratory_rate", 16)),
            blood_pressure_systolic=int(vitals_dict.get("systolic_bp", 110)) if age_months >= 36 else None,
            blood_pressure_diastolic=int(vitals_dict.get("diastolic_bp", 70)) if age_months >= 36 else None,
            oxygen_saturation=vitals_dict.get("o2_sat", 99),
            weight_kg=latest_growth.weight_kg if latest_growth else None,
            height_cm=latest_growth.height_cm if latest_growth else None,
            head_circumference_cm=latest_growth.head_circumference_cm if latest_growth else None,
        )
        
        # Calculate percentiles
        growth_percentiles = None
        if latest_growth and latest_growth.weight_kg:
            try:
                weight_result = calculate_weight_percentile(latest_growth.weight_kg, age_months, sex)
                height_result = calculate_height_percentile(latest_growth.height_cm, age_months, sex) if latest_growth.height_cm else None
                hc_result = calculate_hc_percentile(latest_growth.head_circumference_cm, age_months, sex) if latest_growth.head_circumference_cm and age_months <= 36 else None
                bmi_result = calculate_bmi_percentile(latest_growth.bmi, age_months, sex) if latest_growth.bmi and age_months >= 24 else None
                
                growth_percentiles = GrowthPercentiles(
                    weight_percentile=weight_result.percentile,
                    height_percentile=height_result.percentile if height_result else None,
                    hc_percentile=hc_result.percentile if hc_result else None,
                    bmi_percentile=bmi_result.percentile if bmi_result else None,
                )
            except Exception:
                pass
        
        # Generate physical exam based on encounter type
        if stub.type in (EncounterType.WELL_CHILD, EncounterType.NEWBORN):
            physical_exam = PhysicalExam(
                general="Well-appearing, well-nourished, in no acute distress",
                heent="Normocephalic, atraumatic. Pupils equal, round, reactive. TMs clear bilaterally. Oropharynx clear.",
                neck="Supple, no lymphadenopathy",
                cardiovascular="Regular rate and rhythm, no murmur",
                respiratory="Clear to auscultation bilaterally, no wheezes, rales, or rhonchi",
                abdomen="Soft, non-tender, non-distended, no hepatosplenomegaly",
                musculoskeletal="Normal tone and strength, moves all extremities well",
                skin="Warm, dry, no rashes",
                neurological="Alert, appropriate for age, normal tone",
            )
        else:
            # Generate condition-specific physical exam for acute visits
            exam_findings = self._generate_condition_physical_exam(condition_key, stub.type)
            physical_exam = PhysicalExam(
                general=exam_findings.get('general', "Alert, in no acute distress"),
                heent=exam_findings.get('heent', "Normocephalic, atraumatic"),
                neck=exam_findings.get('neck'),
                cardiovascular=exam_findings.get('cardiovascular', "Regular rate and rhythm"),
                respiratory=exam_findings.get('respiratory', "Clear to auscultation bilaterally"),
                abdomen=exam_findings.get('abdomen'),
                skin=exam_findings.get('skin'),
                neurological=exam_findings.get('neurological'),
            )
        
        # Generate assessment
        assessment = []
        if stub.type == EncounterType.WELL_CHILD:
            assessment.append(Assessment(
                diagnosis=f"Well-child examination - {self._age_to_description(age_months)}",
                is_primary=True,
            ))
        elif stub.type == EncounterType.NEWBORN:
            assessment.append(Assessment(
                diagnosis="Healthy newborn",
                is_primary=True,
            ))
        else:
            assessment.append(Assessment(
                diagnosis=stub.reason,
                is_primary=True,
            ))
        
        # Add chronic conditions to assessment if being addressed
        for condition in stub.conditions_to_address:
            if condition in life_arc.major_conditions:
                status = "newly diagnosed" if stub.is_new_condition_diagnosis else "stable"
                assessment.append(Assessment(
                    diagnosis=f"{condition}, {status}",
                    is_primary=False,
                ))
        
        # Generate plan based on encounter type
        plan = []
        illness_prescriptions = []  # Prescriptions generated from treatment plans
        if stub.type in (EncounterType.WELL_CHILD, EncounterType.NEWBORN):
            plan.append(PlanItem(
                category="education",
                description="Anticipatory guidance provided",
                details=self._generate_anticipatory_guidance(age_months),
            ))
            plan.append(PlanItem(
                category="follow-up",
                description="Return for next well-child visit",
            ))
        elif stub.type == EncounterType.ACUTE_ILLNESS:
            # Generate appropriate plan for acute illnesses (with weight-based prescriptions)
            weight_kg = latest_growth.weight_kg if latest_growth else None
            illness_plans, illness_prescriptions = self._generate_acute_illness_plan(
                reason=stub.reason,
                weight_kg=weight_kg,
                age_months=age_months,
                encounter_date=stub.date
            )
            plan.extend(illness_plans)
        elif stub.type == EncounterType.CHRONIC_FOLLOWUP:
            # Generate plan for chronic condition management
            for condition in stub.conditions_to_address:
                condition_plans = self._generate_chronic_condition_plan(condition, stub.is_new_condition_diagnosis)
                plan.extend(condition_plans)
            if not plan:
                plan.append(PlanItem(
                    category="follow-up",
                    description="Continue current management",
                ))
                plan.append(PlanItem(
                    category="follow-up",
                    description="Return in 3-6 months or sooner if symptoms worsen",
                ))
        
        # Generate immunizations for well visits
        immunizations = []
        if stub.type in (EncounterType.WELL_CHILD, EncounterType.NEWBORN):
            immunizations = self._generate_immunizations(age_months, stub.date)

        # Generate developmental screening for well-child visits
        developmental_screen = None
        if stub.type in (EncounterType.WELL_CHILD, EncounterType.NEWBORN):
            developmental_screen = self._generate_developmental_screen(age_months, stub.date)

        # Generate lab results for acute illness encounters
        lab_results = self._generate_labs(condition_key, stub.date, stub.type)

        # Apply timeline-aware messiness errors
        vitals_contradiction_text = ""
        threading_content = None
        if self.messiness_level > 0:
            # Get timeline-appropriate errors (for future structured error injection)
            # Currently used for tracking; structured injection coming in future update
            _ = self.messiness.get_errors_for_timeline_position(
                timeline_position, age_months
            )

            # Apply vitals contradictions (level 3+) - only if BP is available
            if vitals.blood_pressure_systolic is not None:
                vitals_dict_copy = {
                    "temperature_f": vitals.temperature_f,
                    "blood_pressure_systolic": vitals.blood_pressure_systolic,
                }
                _, vitals_contradiction_text = self.messiness.inject_vitals_contradiction(vitals_dict_copy)

            # For level 5, get threading error content for this visit
            if self.messiness_level >= 5:
                threading_content = self.messiness.get_threading_stage_content(encounter_index)

        # Build the encounter
        encounter = Encounter(
            date=datetime.combine(stub.date, datetime.min.time().replace(hour=random.randint(8, 16))),
            type=stub.type,
            chief_complaint=stub.reason,
            provider=provider,
            location=location,
            vital_signs=vitals,
            physical_exam=physical_exam,
            assessment=assessment,
            plan=plan,
            growth_percentiles=growth_percentiles,
            immunizations_given=immunizations,
            lab_results=lab_results,
            prescriptions=illness_prescriptions,
            anticipatory_guidance=self._generate_anticipatory_guidance_list(age_months) if stub.type in (EncounterType.WELL_CHILD, EncounterType.NEWBORN) else [],
            developmental_screen=developmental_screen,
        )
        
        # Note: Narrative generation is now done in parallel after all encounters are created
        # See _generate_narratives_parallel() called from generate()

        return encounter
    
    def _generate_immunizations(self, age_months: int, visit_date: date) -> list[Immunization]:
        """Generate age-appropriate immunizations."""
        from src.models import CodeableConcept
        
        immunizations = []
        
        # Simplified immunization logic based on AAP schedule
        vaccines_by_age = {
            0: [("HepB", "08", 1)],
            2: [("DTaP", "20", 1), ("Hib", "17", 1), ("PCV", "152", 1), ("IPV", "10", 1), ("RV", "122", 1)],
            4: [("DTaP", "20", 2), ("Hib", "17", 2), ("PCV", "152", 2), ("IPV", "10", 2), ("RV", "122", 2)],
            6: [("DTaP", "20", 3), ("HepB", "08", 3), ("PCV", "152", 3), ("RV", "122", 3)],
            12: [("MMR", "03", 1), ("VAR", "21", 1), ("HepA", "83", 1), ("PCV", "152", 4), ("Hib", "17", 4)],
            15: [("DTaP", "20", 4)],
            18: [("HepA", "83", 2)],
            48: [("DTaP", "20", 5), ("IPV", "10", 4), ("MMR", "03", 2), ("VAR", "21", 2)],
            132: [("Tdap", "115", 1), ("HPV", "165", 1), ("MenACWY", "147", 1)],
            144: [("HPV", "165", 2)],
            192: [("MenACWY", "147", 2)],
        }
        
        # Find the closest age match
        closest_age = min(vaccines_by_age.keys(), key=lambda x: abs(x - age_months))
        if abs(closest_age - age_months) <= 2:  # Within 2 months
            for vaccine_name, cvx, dose_num in vaccines_by_age.get(closest_age, []):
                immunizations.append(Immunization(
                    vaccine_code=CodeableConcept(
                        system="http://hl7.org/fhir/sid/cvx",
                        code=cvx,
                        display=vaccine_name,
                    ),
                    display_name=vaccine_name,
                    date=visit_date,
                    dose_number=dose_num,
                ))
        
        return immunizations

    def _generate_developmental_screen(self, age_months: int, visit_date: date) -> DevelopmentalScreen | None:
        """Generate age-appropriate developmental screening for well-child visits."""
        # AAP recommends developmental screening at 9, 18, and 30 months
        # Autism screening (M-CHAT-R) at 18 and 24 months
        # General surveillance at all well-child visits

        # Determine appropriate screening tool based on age
        if age_months < 4:
            # Newborn/early infant - basic developmental surveillance
            tool = "Developmental Surveillance"
            domains = ["reflexes", "tone", "alertness"]
        elif age_months <= 12:
            tool = "ASQ-3" if age_months in [9] else "Developmental Surveillance"
            domains = ["gross-motor", "fine-motor", "communication", "problem-solving", "personal-social"]
        elif age_months <= 24:
            if age_months in [18, 24]:
                tool = random.choice(["ASQ-3", "M-CHAT-R/F"])
            else:
                tool = "Developmental Surveillance"
            domains = ["gross-motor", "fine-motor", "language", "social-emotional", "cognitive"]
        elif age_months <= 36:
            tool = "ASQ-3" if age_months == 30 else "Developmental Surveillance"
            domains = ["gross-motor", "fine-motor", "language", "social-emotional", "cognitive"]
        elif age_months <= 60:
            tool = "PEDS" if random.random() < 0.3 else "Developmental Surveillance"
            domains = ["motor", "language", "social", "self-help", "academic-readiness"]
        else:
            # School-age - less frequent formal screening
            if random.random() < 0.2:
                tool = "Developmental Surveillance"
                domains = ["academic", "social", "behavioral"]
            else:
                return None  # Not every school-age visit needs formal documentation

        # Generate result - most children develop normally
        result_weights = {
            "normal": 0.90,
            "at-risk": 0.07,
            "delayed": 0.02,
            "not-completed": 0.01,
        }
        result = random.choices(
            list(result_weights.keys()),
            weights=list(result_weights.values())
        )[0]

        # Generate concerns if at-risk or delayed
        concerns = []
        if result in ["at-risk", "delayed"]:
            possible_concerns = {
                "gross-motor": ["not walking independently", "delayed crawling", "poor balance"],
                "fine-motor": ["difficulty with pincer grasp", "not stacking blocks", "poor hand-eye coordination"],
                "language": ["limited vocabulary", "no two-word phrases", "not following commands"],
                "social-emotional": ["limited eye contact", "not responding to name", "difficulty with transitions"],
                "cognitive": ["not imitating actions", "limited problem-solving", "short attention span"],
                "communication": ["limited babbling", "not pointing", "no words"],
            }
            for domain in domains:
                if domain in possible_concerns and random.random() < 0.3:
                    concerns.append(random.choice(possible_concerns[domain]))

        # Generate notes
        notes = None
        if result == "normal":
            notes = random.choice([
                "Development appropriate for age",
                "Meeting all milestones",
                "No developmental concerns at this time",
                "Age-appropriate development noted",
            ])
        elif concerns:
            notes = f"Discussed concerns with family. Will monitor at next visit."

        return DevelopmentalScreen(
            tool=tool,
            date=visit_date,
            result=result,
            domains_assessed=domains,
            concerns=concerns,
            notes=notes,
        )

    def _generate_narratives_parallel(
        self,
        encounters: list[Encounter],
        demographics: Demographics,
        life_arc: LifeArc | None = None,
        max_workers: int = 8
    ) -> None:
        """
        Generate all LLM content for encounters in parallel.

        Generates:
        - Narrative notes for all encounters
        - Family narratives (HPI) for acute illness visits
        - Assessment reasoning for acute illness visits
        - Anticipatory guidance for well-child visits

        Uses ThreadPoolExecutor to make concurrent LLM calls, dramatically
        reducing generation time from O(n * latency) to O(n/workers * latency).
        """
        if not self.use_llm or not encounters:
            return

        # Get conditions list for anticipatory guidance
        conditions = life_arc.major_conditions if life_arc else []

        def generate_all_for_encounter(enc: Encounter, enc_idx: int) -> dict:
            """Generate all LLM content for a single encounter."""
            days_old = (enc.date.date() - demographics.date_of_birth).days
            age_months = days_old // 30

            result = {"id": enc.id}

            # Generate narrative note
            try:
                note = self._generate_llm_narrative(enc, demographics, age_months)
            except Exception:
                note = self._generate_templated_note(enc, demographics, age_months)

            # Apply chart messiness if configured
            if self.messiness_level > 0:
                context = {
                    "sex": demographics.sex_at_birth.value,
                    "age_months": age_months,
                }
                note = self.messiness.inject_text(note, context)
                note = self.messiness.add_redundant_text(note)
                note = self.messiness.inject_incomplete_sentence(note)

                # Level 4+: potentially add wrong-sex exam finding
                wrong_finding = self.messiness.get_wrong_sex_finding(demographics.sex_at_birth.value)
                if wrong_finding:
                    note = note.rstrip() + f"\n\n{wrong_finding}"

                # Level 5: inject threading error content
                if self.messiness_level >= 5:
                    threading_content = self.messiness.get_threading_stage_content(enc_idx)
                    if threading_content:
                        # Inject threading error into the note
                        note = note.rstrip() + f"\n\n{threading_content}"

            result["narrative"] = note

            # Generate family narrative (HPI) for acute illness visits
            if enc.type == EncounterType.ACUTE_ILLNESS:
                try:
                    result["hpi"] = self._generate_llm_family_narrative(enc, demographics, age_months)
                except Exception:
                    result["hpi"] = None

                # Generate assessment reasoning
                try:
                    result["reasoning"] = self._generate_llm_assessment_reasoning(enc, demographics, age_months)
                except Exception:
                    result["reasoning"] = None

            # Generate anticipatory guidance for well-child visits
            if enc.type in (EncounterType.WELL_CHILD, EncounterType.NEWBORN):
                try:
                    result["guidance"] = self._generate_llm_anticipatory_guidance(
                        age_months, demographics, conditions
                    )
                except Exception:
                    result["guidance"] = None

            return result

        # Run LLM calls in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(generate_all_for_encounter, enc, idx): enc
                for idx, enc in enumerate(encounters)
            }

            for future in as_completed(futures):
                result = future.result()
                enc_id = result["id"]

                # Find encounter and apply generated content
                for enc in encounters:
                    if enc.id == enc_id:
                        # Always set narrative note
                        enc.narrative_note = result.get("narrative")

                        # Set HPI if generated
                        if "hpi" in result and result["hpi"]:
                            enc.hpi = result["hpi"]

                        # Set assessment reasoning if generated
                        if "reasoning" in result and result["reasoning"]:
                            for assessment in enc.assessment:
                                if assessment.is_primary:
                                    assessment.clinical_notes = result["reasoning"]
                                    break

                        # Set anticipatory guidance if generated
                        if "guidance" in result and result["guidance"]:
                            enc.anticipatory_guidance = result["guidance"]

                        break

    def _generate_llm_narrative(self, encounter: Encounter, demographics: Demographics, age_months: int) -> str:
        """Generate a natural clinical narrative using Claude."""
        age_str = self._age_to_description(age_months)

        # Build structured context for the LLM
        vitals_str = ""
        if encounter.vital_signs:
            vs = encounter.vital_signs
            parts = []
            if vs.temperature_f:
                parts.append(f"Temp {vs.temperature_f}F")
            if vs.heart_rate:
                parts.append(f"HR {vs.heart_rate}")
            if vs.respiratory_rate:
                parts.append(f"RR {vs.respiratory_rate}")
            if vs.oxygen_saturation:
                parts.append(f"SpO2 {vs.oxygen_saturation}%")
            if vs.weight_kg:
                parts.append(f"Wt {vs.weight_kg}kg")
            vitals_str = ", ".join(parts)

        exam_str = ""
        if encounter.physical_exam:
            pe = encounter.physical_exam
            exam_parts = []
            if pe.general:
                exam_parts.append(f"General: {pe.general}")
            if pe.heent:
                exam_parts.append(f"HEENT: {pe.heent}")
            if pe.cardiovascular:
                exam_parts.append(f"CV: {pe.cardiovascular}")
            if pe.respiratory:
                exam_parts.append(f"Resp: {pe.respiratory}")
            if pe.abdomen:
                exam_parts.append(f"Abd: {pe.abdomen}")
            if pe.skin:
                exam_parts.append(f"Skin: {pe.skin}")
            exam_str = "; ".join(exam_parts)

        assessment_str = ", ".join([a.diagnosis for a in encounter.assessment]) if encounter.assessment else ""
        plan_str = "; ".join([p.description for p in encounter.plan]) if encounter.plan else ""

        prompt = f"""Write a concise pediatric clinical note in natural medical prose. Professional but warm tone.
Do not use bullet points or numbered lists in the body. Write flowing sentences.
Include only sections with actual content.

Patient: {age_str} {demographics.sex_at_birth.value}
Visit Type: {encounter.type.value.replace('-', ' ').title()}
Chief Complaint: {encounter.chief_complaint}
Vitals: {vitals_str}
Physical Exam: {exam_str}
Assessment: {assessment_str}
Plan: {plan_str}

Write the clinical note (start with HPI, end with signature):"""

        system = """You are a pediatric physician writing clinical documentation.
Write clear, professional SOAP-style notes. Use standard medical abbreviations where appropriate.
Keep it concise but complete. Sign as the provider."""

        note = self.llm.generate(prompt, system=system, max_tokens=600, temperature=0.7)

        # Ensure proper signature
        if encounter.provider and encounter.provider.name not in note:
            note += f"\n\nSigned: {encounter.provider.name}, {encounter.provider.credentials}"

        return note

    def _generate_llm_family_narrative(
        self,
        encounter: Encounter,
        demographics: Demographics,
        age_months: int
    ) -> str:
        """
        Generate a family narrative (HPI) using Claude.

        Phase 5: Creates a realistic narrative of how symptoms developed
        from the parent's perspective - when they started, what they observed,
        what they tried, and their concerns.
        """
        age_str = self._age_to_description(age_months)
        pronoun = "he" if demographics.sex_at_birth == Sex.MALE else "she"
        possessive = "his" if demographics.sex_at_birth == Sex.MALE else "her"

        # Build context about the encounter
        vitals_context = ""
        if encounter.vital_signs:
            vs = encounter.vital_signs
            if vs.temperature_f and vs.temperature_f > 100.4:
                vitals_context += f"Child has fever of {vs.temperature_f}°F. "
            if vs.oxygen_saturation and vs.oxygen_saturation < 95:
                vitals_context += f"Oxygen saturation is {vs.oxygen_saturation}%. "

        # Get symptoms from physical exam
        symptoms_context = ""
        if encounter.physical_exam:
            pe = encounter.physical_exam
            abnormal_findings = []
            if pe.heent and "normal" not in pe.heent.lower():
                abnormal_findings.append(pe.heent)
            if pe.respiratory and "clear" not in pe.respiratory.lower():
                abnormal_findings.append(pe.respiratory)
            if pe.skin and "normal" not in pe.skin.lower():
                abnormal_findings.append(pe.skin)
            if abnormal_findings:
                symptoms_context = "Clinical findings suggest: " + "; ".join(abnormal_findings)

        prompt = f"""Write a realistic parent narrative (HPI) for a pediatric visit.

Patient: {age_str} {demographics.sex_at_birth.value} named {demographics.given_names[0]}
Chief Complaint: {encounter.chief_complaint}
Visit Type: {encounter.type.value.replace('-', ' ').title()}
{vitals_context}
{symptoms_context}

Write 2-4 sentences from the parent's perspective describing:
- When symptoms started (hours/days ago)
- What they noticed and how it progressed
- What home remedies they tried (if applicable)
- Their main concern

Use natural parent language like "started yesterday", "won't eat", "seems fussy".
Use {pronoun}/{possessive} pronouns. Be concise but realistic."""

        system = """You are helping document a pediatric visit. Write natural, realistic
parent descriptions of childhood illness. Keep it brief and clinical-appropriate."""

        try:
            result = self.llm.generate(prompt, system=system, max_tokens=250, temperature=0.7)
            # Clean up markdown formatting
            lines = result.split('\n')
            cleaned = []
            for line in lines:
                line = line.strip()
                # Skip markdown headers
                if line.startswith('#'):
                    continue
                # Remove bold markers
                line = line.replace('**', '')
                if line:
                    cleaned.append(line)
            return ' '.join(cleaned)
        except Exception:
            # Fallback to simple template
            return f"{age_str} {demographics.sex_at_birth.value} presenting with {encounter.chief_complaint.lower()}."

    def _generate_llm_assessment_reasoning(
        self,
        encounter: Encounter,
        demographics: Demographics,
        age_months: int
    ) -> str:
        """
        Generate clinical reasoning for the assessment using Claude.

        Phase 3: Creates the "why" behind the diagnosis - explains how
        the clinical findings support the assessment.
        """
        age_str = self._age_to_description(age_months)

        # Get the primary diagnosis
        primary_dx = None
        for a in encounter.assessment:
            if a.is_primary:
                primary_dx = a.diagnosis
                break

        if not primary_dx:
            return ""

        # Skip reasoning for well-child visits
        if "well-child" in primary_dx.lower() or "healthy" in primary_dx.lower():
            return ""

        # Build clinical evidence
        evidence = []

        if encounter.vital_signs:
            vs = encounter.vital_signs
            if vs.temperature_f and vs.temperature_f > 100.4:
                evidence.append(f"fever {vs.temperature_f}°F")
            if vs.heart_rate:
                evidence.append(f"HR {vs.heart_rate}")
            if vs.respiratory_rate and vs.respiratory_rate > 24:
                evidence.append(f"tachypnea RR {vs.respiratory_rate}")
            if vs.oxygen_saturation and vs.oxygen_saturation < 95:
                evidence.append(f"hypoxia SpO2 {vs.oxygen_saturation}%")

        if encounter.physical_exam:
            pe = encounter.physical_exam
            if pe.heent:
                evidence.append(f"HEENT: {pe.heent}")
            if pe.respiratory:
                evidence.append(f"Lungs: {pe.respiratory}")
            if pe.skin:
                evidence.append(f"Skin: {pe.skin}")

        if encounter.lab_results:
            for lab in encounter.lab_results:
                if hasattr(lab, 'display_name') and hasattr(lab, 'interpretation'):
                    if lab.interpretation:
                        evidence.append(f"{lab.display_name}: {lab.interpretation.value}")

        evidence_str = "; ".join(evidence) if evidence else "clinical presentation"

        prompt = f"""Write brief clinical reasoning for this pediatric assessment.

Patient: {age_str} {demographics.sex_at_birth.value}
Chief Complaint: {encounter.chief_complaint}
Diagnosis: {primary_dx}
Clinical Evidence: {evidence_str}

Write 1-2 sentences explaining how the findings support the diagnosis.
Use medical shorthand and be concise. Start with "Clinical presentation..." or "History and exam..."."""

        system = """You are a pediatrician documenting clinical reasoning.
Be concise and use standard medical abbreviations."""

        try:
            return self.llm.generate(prompt, system=system, max_tokens=150, temperature=0.5)
        except Exception:
            return ""

    def _generate_llm_anticipatory_guidance(
        self,
        age_months: int,
        demographics: Demographics,
        conditions: list[str] | None = None
    ) -> list[str]:
        """
        Generate personalized anticipatory guidance using Claude.

        Phase 6: Creates age-appropriate guidance that is more specific
        and personalized than template-based lists.
        """
        age_str = self._age_to_description(age_months)

        # Determine developmental stage
        if age_months < 2:
            stage = "newborn"
        elif age_months < 6:
            stage = "young infant"
        elif age_months < 12:
            stage = "older infant"
        elif age_months < 24:
            stage = "toddler"
        elif age_months < 48:
            stage = "preschooler"
        elif age_months < 72:
            stage = "school-age child"
        elif age_months < 132:
            stage = "pre-teen"
        else:
            stage = "adolescent"

        conditions_str = ""
        if conditions:
            conditions_str = f"\nChronic conditions: {', '.join(conditions)}"

        prompt = f"""Generate 4-5 anticipatory guidance points for a well-child visit.

Patient: {age_str} {demographics.sex_at_birth.value}
Developmental stage: {stage}{conditions_str}

Provide specific, actionable guidance covering:
- Safety appropriate for this age
- Nutrition and feeding
- Sleep recommendations
- Developmental activities
- Screen time/social considerations

Be specific (e.g., "introduce finger foods" not "nutrition guidance").
Format as a simple list, one topic per line. No bullets or numbers."""

        system = """You are a pediatrician giving anticipatory guidance.
Be specific and practical. Reference AAP recommendations where relevant."""

        try:
            response = self.llm.generate(prompt, system=system, max_tokens=300, temperature=0.6)
            # Parse into list
            lines = [line.strip() for line in response.split('\n') if line.strip()]
            # Remove markdown formatting and bullet points
            cleaned = []
            for line in lines:
                # Skip markdown headers and empty formatting
                if line.startswith('#') or line.startswith('**') and line.endswith('**'):
                    continue
                # Remove bullet points, numbers, and markdown bold markers
                line = line.lstrip('•-*0123456789. ')
                line = line.replace('**', '')
                if line and len(line) > 10:  # Skip very short lines
                    cleaned.append(line)
            return cleaned[:5]  # Cap at 5 items
        except Exception:
            # Fallback to template
            return self._generate_anticipatory_guidance_list(age_months)

    def _generate_templated_note(self, encounter: Encounter, demographics: Demographics, age_months: int) -> str:
        """Generate a templated clinical note (fallback when LLM unavailable)."""
        age_str = self._age_to_description(age_months)

        note = f"""PATIENT: {demographics.full_name}
DATE: {encounter.date.strftime('%Y-%m-%d')}
VISIT TYPE: {encounter.type.value.replace('-', ' ').title()}

CHIEF COMPLAINT: {encounter.chief_complaint}

HISTORY OF PRESENT ILLNESS:
{age_str} {demographics.sex_at_birth.value} presenting for {encounter.chief_complaint.lower()}.

VITAL SIGNS:
"""
        if encounter.vital_signs:
            vs = encounter.vital_signs
            if vs.temperature_f:
                note += f"Temperature: {vs.temperature_f}°F\n"
            if vs.heart_rate:
                note += f"Heart Rate: {vs.heart_rate} bpm\n"
            if vs.respiratory_rate:
                note += f"Respiratory Rate: {vs.respiratory_rate}\n"
            if vs.blood_pressure_systolic:
                note += f"Blood Pressure: {vs.blood_pressure_systolic}/{vs.blood_pressure_diastolic} mmHg\n"
            if vs.oxygen_saturation:
                note += f"O2 Saturation: {vs.oxygen_saturation}%\n"
            if vs.weight_kg:
                note += f"Weight: {vs.weight_kg} kg\n"
            if vs.height_cm:
                note += f"Height: {vs.height_cm} cm\n"

        if encounter.growth_percentiles:
            gp = encounter.growth_percentiles
            note += "\nGROWTH PERCENTILES:\n"
            if gp.weight_percentile:
                note += f"Weight: {gp.weight_percentile}th percentile\n"
            if gp.height_percentile:
                note += f"Height: {gp.height_percentile}th percentile\n"
            if gp.hc_percentile:
                note += f"Head Circumference: {gp.hc_percentile}th percentile\n"
            if gp.bmi_percentile:
                note += f"BMI: {gp.bmi_percentile}th percentile\n"

        if encounter.physical_exam:
            note += "\nPHYSICAL EXAMINATION:\n"
            pe = encounter.physical_exam
            if pe.general:
                note += f"General: {pe.general}\n"
            if pe.heent:
                note += f"HEENT: {pe.heent}\n"
            if pe.cardiovascular:
                note += f"Cardiovascular: {pe.cardiovascular}\n"
            if pe.respiratory:
                note += f"Respiratory: {pe.respiratory}\n"
            if pe.abdomen:
                note += f"Abdomen: {pe.abdomen}\n"
            if pe.skin:
                note += f"Skin: {pe.skin}\n"
            if pe.neurological:
                note += f"Neurological: {pe.neurological}\n"

        note += "\nASSESSMENT:\n"
        for i, a in enumerate(encounter.assessment, 1):
            note += f"{i}. {a.diagnosis}\n"

        note += "\nPLAN:\n"
        for p in encounter.plan:
            note += f"- {p.description}"
            if p.details:
                note += f": {p.details}"
            note += "\n"

        if encounter.immunizations_given:
            note += "\nIMMUNIZATIONS ADMINISTERED:\n"
            for imm in encounter.immunizations_given:
                note += f"- {imm.display_name}\n"

        if encounter.anticipatory_guidance:
            note += "\nANTICIPATORY GUIDANCE:\n"
            for ag in encounter.anticipatory_guidance:
                note += f"- {ag}\n"

        note += f"\nSigned: {encounter.provider.name}, {encounter.provider.credentials}\n"

        return note
    
    # Helper methods
    def _generate_first_name(self, sex: Sex) -> str:
        male_names = ["James", "William", "Oliver", "Benjamin", "Elijah", "Lucas", "Mason", "Ethan", "Alexander", "Henry", "Sebastian", "Jack", "Aiden", "Owen", "Samuel", "Ryan", "Nathan", "Caleb", "Dylan", "Luke"]
        female_names = ["Olivia", "Emma", "Charlotte", "Amelia", "Sophia", "Isabella", "Mia", "Evelyn", "Harper", "Luna", "Camila", "Sofia", "Scarlett", "Elizabeth", "Eleanor", "Emily", "Chloe", "Mila", "Violet", "Penelope"]
        return random.choice(male_names if sex == Sex.MALE else female_names)
    
    def _generate_last_name(self) -> str:
        names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez", "Anderson", "Taylor", "Thomas", "Moore", "Jackson", "Martin", "Lee", "Thompson", "White", "Harris"]
        return random.choice(names)
    
    def _generate_provider_name(self) -> str:
        first = random.choice(["Sarah", "Michael", "Jennifer", "David", "Emily", "Robert", "Jessica", "William", "Amanda", "James"])
        last = random.choice(["Chen", "Patel", "Kim", "Singh", "Johnson", "Williams", "Brown", "Garcia", "Miller", "Davis"])
        return f"Dr. {first} {last}"
    
    def _generate_phone(self) -> str:
        return f"({random.randint(200, 999)}) {random.randint(200, 999)}-{random.randint(1000, 9999)}"
    
    def _age_to_description(self, age_months: int) -> str:
        if age_months == 0:
            return "newborn"
        elif age_months < 12:
            return f"{age_months} month old"
        elif age_months < 24:
            years = age_months // 12
            months = age_months % 12
            if months == 0:
                return f"{years} year old"
            return f"{years} year {months} month old"
        else:
            return f"{age_months // 12} year old"
    
    def _age_to_grade(self, age_years: int) -> str:
        grade_map = {
            5: "Kindergarten", 6: "1st grade", 7: "2nd grade", 8: "3rd grade",
            9: "4th grade", 10: "5th grade", 11: "6th grade", 12: "7th grade",
            13: "8th grade", 14: "9th grade", 15: "10th grade", 16: "11th grade",
            17: "12th grade"
        }
        return grade_map.get(age_years, "N/A")
    
    def _generate_anticipatory_guidance(self, age_months: int) -> str:
        guidance = {
            0: "Safe sleep practices, feeding support, newborn care",
            2: "Tummy time, reading to baby, car seat safety",
            4: "Starting solids at 6 months, developmental milestones",
            6: "Choking hazards, baby-proofing home, dental care",
            12: "Toddler safety, language development, limit screen time",
            24: "Toilet training readiness, discipline strategies, outdoor play",
            48: "School readiness, healthy eating habits, physical activity",
            72: "Homework routines, peer relationships, internet safety",
            132: "Puberty education, mental health awareness, substance avoidance",
        }
        closest = min(guidance.keys(), key=lambda x: abs(x - age_months))
        return guidance[closest]
    
    def _generate_anticipatory_guidance_list(self, age_months: int) -> list[str]:
        guidance = self._generate_anticipatory_guidance(age_months)
        return [g.strip() for g in guidance.split(",")]

    def _apply_comorbidity_logic(self, conditions: list[str]) -> list[str]:
        """
        Apply realistic co-morbidity patterns to condition lists.

        Uses comorbidity associations loaded from conditions.yaml.
        Research shows certain conditions cluster together:
        - Obesity: associated with asthma, ADHD, anxiety, sleep apnea, GERD
        - Asthma: associated with eczema, allergic rhinitis (atopic triad)
        - ADHD: associated with anxiety, learning disorders, sleep issues
        - Eczema: associated with asthma, food allergy (allergic march)
        """
        result = list(conditions)

        # Use comorbidity map loaded from YAML (built in _build_condition_lookups)
        comorbidity_map = self._comorbidity_map

        # Check each existing condition for potential co-morbidities
        for condition in list(result):  # Iterate over copy to allow modification
            if condition in comorbidity_map:
                for comorbid, probability in comorbidity_map[condition]:
                    if comorbid not in result and random.random() < probability:
                        result.append(comorbid)

        return result

    def _get_seasonal_illness(self, visit_date: date, age_months: int = 12) -> str:
        """Select an illness based on the season and patient age.

        Uses seasonal weights from conditions.yaml.
        Age-appropriate illness selection:
        - 2-6 months: Mainly bronchiolitis, viral syndrome, fever (no ear infections)
        - 6-12 months: Can add ear infections, croup
        - 12+ months: Full range of pediatric illnesses
        """
        month = visit_date.month

        # Get seasonal weights from YAML (loaded in _build_condition_lookups)
        seasonal_weights = self._seasonal_weights

        # Select season based on month
        if month in [12, 1, 2]:  # Winter
            season_key = 'winter'
        elif month in [3, 4, 5]:  # Spring
            season_key = 'spring'
        elif month in [6, 7, 8]:  # Summer
            season_key = 'summer'
        else:  # Fall (9, 10, 11)
            season_key = 'fall'

        # Get illness weights for this season
        season_data = seasonal_weights.get(season_key, {})

        # Convert YAML keys to display names and build illness pool
        illness_pool = []
        for condition_key, weight in season_data.items():
            # Look up display name from conditions
            if condition_key in self._conditions:
                display_name = self._conditions[condition_key].get('display_name', condition_key.replace('_', ' ').title())
            else:
                display_name = condition_key.replace('_', ' ').title()
            illness_pool.append((display_name, weight))

        # Fallback if no seasonal data
        if not illness_pool:
            illness_pool = [
                ("Upper Respiratory Infection", 25),
                ("Viral Syndrome", 20),
                ("Fever", 15),
                ("Viral Gastroenteritis", 10),
            ]

        # Age-appropriate filtering using acute conditions from YAML
        filtered_pool = []
        for illness, weight in illness_pool:
            # Check minimum age from acute conditions
            if illness in self._acute_conditions:
                min_age = self._acute_conditions[illness].get('min_months', 0)
            else:
                # Fallback min ages for common conditions
                fallback_min_ages = {
                    "Acute Otitis Media": 6,
                    "Swimmer's Ear": 12,
                    "Croup": 6,
                    "Allergic Rhinitis": 24,
                    "Influenza": 6,
                    "Insect Bite Reaction": 6,
                }
                min_age = fallback_min_ages.get(illness, 0)

            if min_age <= age_months:
                filtered_pool.append((illness, weight))

        # If all filtered out (very young infant), use infant fallback from YAML
        if not filtered_pool:
            infant_fallback = seasonal_weights.get('infant_fallback', {})
            if infant_fallback:
                for condition_key, weight in infant_fallback.items():
                    if condition_key in self._conditions:
                        display_name = self._conditions[condition_key].get('display_name', condition_key.replace('_', ' ').title())
                    else:
                        display_name = condition_key.replace('_', ' ').title()
                    filtered_pool.append((display_name, weight))
            else:
                # Hardcoded fallback if YAML doesn't have infant_fallback
                filtered_pool = [
                    ("Viral Syndrome", 30),
                    ("Upper Respiratory Infection", 25),
                    ("Bronchiolitis", 25),
                    ("Fever", 15),
                    ("Viral Gastroenteritis", 5),
                ]

        # Weighted random selection
        illnesses, weights = zip(*filtered_pool)
        return random.choices(illnesses, weights=weights, k=1)[0]

    def _generate_acute_illness_plan(
        self,
        reason: str,
        weight_kg: float | None = None,
        age_months: int | None = None,
        encounter_date: date | None = None
    ) -> tuple[list, list]:
        """
        Generate plan items and prescriptions for acute illness visits.

        Returns:
            Tuple of (plan_items, prescriptions)
        """
        from src.models import PlanItem, Medication, CodeableConcept, MedicationStatus

        plan_items = []
        prescriptions = []

        # Try to get condition-specific treatment from YAML
        condition_key = self._get_condition_key(reason)

        if condition_key:
            condition_data = self._conditions.get(condition_key, {})
            treatment = condition_data.get('treatment', {})
            medications = treatment.get('medications', [])
            instructions = treatment.get('patient_instructions', [])

            if medications or instructions:
                # Generate medication plan items and prescriptions
                for med in medications:
                    if not isinstance(med, dict):
                        continue

                    agent = med.get('agent', '')
                    rxnorm = med.get('rxnorm', '')
                    dose_mg_kg = med.get('dose_mg_kg')
                    max_dose_mg = med.get('max_dose_mg')
                    fixed_dose = med.get('fixed_dose_mg')
                    frequency = med.get('frequency', '')
                    duration = med.get('duration_days')
                    route = med.get('route', 'oral')
                    indication = med.get('indication')
                    is_prn = med.get('prn', False)
                    age_min = med.get('age_min_months')

                    if not agent:
                        continue

                    # Check age minimum
                    if age_min and age_months and age_months < age_min:
                        continue

                    # Calculate dose
                    if dose_mg_kg and weight_kg:
                        dose = dose_mg_kg * weight_kg
                        if max_dose_mg:
                            dose = min(dose, max_dose_mg)
                        dose_str = f"{dose:.0f}mg"
                        dose_unit = "mg"
                    elif fixed_dose:
                        dose_str = f"{fixed_dose}mg"
                        dose_unit = "mg"
                    else:
                        dose_str = "per package"
                        dose_unit = ""

                    # Duration string
                    duration_str = f" x {duration} days" if duration else ""
                    prn_str = " PRN" if is_prn else ""

                    # Create plan item
                    plan_items.append(PlanItem(
                        category="medication",
                        description=f"{agent} {dose_str} {frequency}{duration_str}{prn_str}",
                        details=indication if indication else None,
                    ))

                    # Create prescription (non-PRN meds only for now)
                    if rxnorm and encounter_date:
                        prescriptions.append(Medication(
                            code=CodeableConcept(
                                system="http://www.nlm.nih.gov/research/umls/rxnorm",
                                code=rxnorm,
                                display=agent
                            ),
                            display_name=agent,
                            dose_quantity=dose_str,
                            dose_unit=dose_unit,
                            frequency=frequency,
                            route=route,
                            status=MedicationStatus.ACTIVE,
                            start_date=encounter_date,
                            prn=is_prn,
                            indication=indication,
                        ))

                # Add patient instructions
                for instruction in instructions:
                    plan_items.append(PlanItem(
                        category="education",
                        description=instruction,
                    ))

                return plan_items, prescriptions

        # Fallback to legacy hardcoded plans if no YAML data
        reason_lower = reason.lower()

        if "respiratory" in reason_lower or "uri" in reason_lower or "cold" in reason_lower:
            plan_items.append(PlanItem(
                category="other",
                description="Supportive care with rest and hydration",
            ))
            plan_items.append(PlanItem(
                category="medication",
                description="Acetaminophen or ibuprofen as needed for fever/discomfort",
            ))
            plan_items.append(PlanItem(
                category="education",
                description="Return precautions reviewed: difficulty breathing, high fever >72hrs, worsening symptoms",
            ))
        elif "otitis" in reason_lower or "ear" in reason_lower:
            plan_items.append(PlanItem(
                category="medication",
                description="Amoxicillin 90mg/kg/day divided BID x 10 days",
            ))
            plan_items.append(PlanItem(
                category="medication",
                description="Ibuprofen for pain management",
            ))
            plan_items.append(PlanItem(
                category="follow-up",
                description="Return if no improvement in 48-72 hours",
            ))
        elif "gastroenteritis" in reason_lower or "vomiting" in reason_lower or "diarrhea" in reason_lower:
            plan_items.append(PlanItem(
                category="education",
                description="Oral rehydration with small frequent amounts of fluids",
            ))
            plan_items.append(PlanItem(
                category="education",
                description="BRAT diet as tolerated, advance as symptoms improve",
            ))
            plan_items.append(PlanItem(
                category="education",
                description="Return if unable to keep fluids down, bloody stool, or signs of dehydration",
            ))
        elif "fever" in reason_lower:
            plan_items.append(PlanItem(
                category="medication",
                description="Acetaminophen or ibuprofen for temperature control",
            ))
            plan_items.append(PlanItem(
                category="education",
                description="Monitor for source of infection, return if fever persists >3 days",
            ))
            plan_items.append(PlanItem(
                category="follow-up",
                description="Follow up as needed if symptoms worsen",
            ))
        elif "rash" in reason_lower:
            plan_items.append(PlanItem(
                category="other",
                description="Topical care as appropriate for rash type",
            ))
            plan_items.append(PlanItem(
                category="education",
                description="Return if rash spreads, becomes painful, or child develops fever",
            ))
        elif "conjunctivitis" in reason_lower or "pink eye" in reason_lower:
            plan_items.append(PlanItem(
                category="medication",
                description="Antibiotic eye drops if bacterial; supportive care if viral",
            ))
            plan_items.append(PlanItem(
                category="education",
                description="Good hand hygiene to prevent spread",
            ))
        else:
            plan_items.append(PlanItem(
                category="other",
                description="Supportive care with rest and hydration",
            ))
            plan_items.append(PlanItem(
                category="follow-up",
                description="Return if symptoms worsen or do not improve in 3-5 days",
            ))

        return plan_items, prescriptions

    def _generate_chronic_condition_plan(self, condition: str, is_new_diagnosis: bool) -> list[PlanItem]:
        """Generate plan items for chronic condition management."""
        from src.models import PlanItem

        plans = []
        condition_lower = condition.lower()

        if "asthma" in condition_lower:
            if is_new_diagnosis:
                plans.append(PlanItem(
                    category="medication",
                    description="Start albuterol inhaler PRN for rescue",
                ))
                plans.append(PlanItem(
                    category="education",
                    description="Asthma education provided: triggers, inhaler technique, action plan",
                ))
                plans.append(PlanItem(
                    category="referral",
                    description="Consider pulmonology referral if poorly controlled",
                ))
            else:
                plans.append(PlanItem(
                    category="medication",
                    description="Continue current asthma regimen",
                ))
                plans.append(PlanItem(
                    category="other",
                    description="Asthma well controlled, continue current management",
                ))
            plans.append(PlanItem(
                category="follow-up",
                description="Return in 3 months for asthma review",
            ))
        elif "adhd" in condition_lower:
            if is_new_diagnosis:
                plans.append(PlanItem(
                    category="education",
                    description="ADHD education provided; discussed behavioral strategies",
                ))
                plans.append(PlanItem(
                    category="medication",
                    description="Consider starting methylphenidate after discussion with family",
                ))
            else:
                plans.append(PlanItem(
                    category="other",
                    description="Review medication efficacy and side effects",
                ))
                plans.append(PlanItem(
                    category="medication",
                    description="Continue current ADHD medication regimen",
                ))
            plans.append(PlanItem(
                category="follow-up",
                description="Return in 1-3 months for medication review",
            ))
        elif "eczema" in condition_lower or "atopic dermatitis" in condition_lower:
            if is_new_diagnosis:
                plans.append(PlanItem(
                    category="medication",
                    description="Start topical corticosteroid for flares",
                ))
                plans.append(PlanItem(
                    category="education",
                    description="Skin care education: moisturize frequently, avoid triggers",
                ))
            else:
                plans.append(PlanItem(
                    category="medication",
                    description="Continue emollient therapy and topical steroids as needed",
                ))
            plans.append(PlanItem(
                category="follow-up",
                description="Return if flares not controlled or signs of infection",
            ))
        elif "allergy" in condition_lower or "allergic" in condition_lower:
            plans.append(PlanItem(
                category="medication",
                description="Antihistamine as needed for symptoms",
            ))
            plans.append(PlanItem(
                category="education",
                description="Allergen avoidance strategies discussed",
            ))
            if "food" in condition_lower:
                plans.append(PlanItem(
                    category="medication",
                    description="Epinephrine auto-injector prescribed; training provided",
                ))
        elif "anxiety" in condition_lower:
            plans.append(PlanItem(
                category="referral",
                description="Counseling/therapy referral for CBT",
            ))
            plans.append(PlanItem(
                category="education",
                description="Discussed coping strategies and relaxation techniques",
            ))
            plans.append(PlanItem(
                category="follow-up",
                description="Follow up in 4-6 weeks to assess progress",
            ))
        elif "obesity" in condition_lower:
            plans.append(PlanItem(
                category="education",
                description="Nutrition counseling; goal of modest lifestyle changes",
            ))
            plans.append(PlanItem(
                category="referral",
                description="Refer to dietitian for comprehensive nutrition plan",
            ))
            plans.append(PlanItem(
                category="follow-up",
                description="Return in 3 months for weight check",
            ))
        elif "constipation" in condition_lower:
            plans.append(PlanItem(
                category="education",
                description="Increase fiber and fluid intake",
            ))
            plans.append(PlanItem(
                category="medication",
                description="MiraLAX or similar osmotic laxative as needed",
            ))
        else:
            # Generic chronic condition plan
            plans.append(PlanItem(
                category="medication",
                description="Continue current medication regimen",
            ))
            plans.append(PlanItem(
                category="follow-up",
                description="Return in 3-6 months for condition review",
            ))

        return plans

    def _generate_patient_messages(
        self,
        demographics: Demographics,
        encounters: list[Encounter],
        conditions: list[Condition],
        message_frequency: float,
        provider: Provider,
    ) -> list[PatientMessage]:
        """
        Generate patient messages (portal messages, phone messages) with replies.

        Args:
            demographics: Patient demographics
            encounters: List of patient encounters
            conditions: List of patient conditions
            message_frequency: 0.0 (never messages) to 1.0 (frequent messager)
            provider: Primary provider for replies

        Returns:
            List of PatientMessage objects
        """
        messages = []

        # Determine base number of messages based on frequency and encounter count
        # A patient with 10 encounters and 0.5 frequency might have ~2-3 messages
        base_count = int(len(encounters) * message_frequency * 0.3)

        # Add some randomness - some patients message more than expected
        if random.random() < 0.2:  # 20% chance of being more prolific
            base_count = int(base_count * 1.5) + 1

        # Ensure at least 0, cap at reasonable maximum
        num_messages = max(0, min(base_count, 8))

        if num_messages == 0:
            return messages

        # Get parent/guardian name as sender
        if demographics.legal_guardian:
            sender_name = demographics.legal_guardian.name
        elif demographics.emergency_contact:
            sender_name = demographics.emergency_contact.name
        else:
            sender_name = f"Parent of {demographics.first_name}"

        # Get provider info for replies
        provider_name = provider.name if provider else "Office Staff"
        provider_role = "RN" if random.random() < 0.6 else "MD"  # Most replies from nurses

        # Message templates by category
        message_templates = {
            MessageCategory.REFILL_REQUEST: [
                {
                    "subject": "Medication refill needed",
                    "body": "Hi, we need a refill on {medication}. The pharmacy is {pharmacy}. Thank you.",
                    "reply": "Hi {parent_name}, the refill for {medication} has been sent to your pharmacy. Please allow 24-48 hours for processing."
                },
                {
                    "subject": "Prescription refill request",
                    "body": "Hello, {child_name} is running low on {medication}. Can you please send a refill to the pharmacy? Thanks!",
                    "reply": "Refill has been submitted. If you don't receive it within 2 business days, please call the pharmacy directly."
                },
            ],
            MessageCategory.CLINICAL_QUESTION: [
                {
                    "subject": "Question about symptoms",
                    "body": "Hi, {child_name} has been having {symptom} for the past few days. Should we come in or is this something we can manage at home?",
                    "reply": "Thank you for reaching out. Based on what you've described, here are some recommendations: {advice}. If symptoms worsen or you have concerns, please call the office or go to urgent care."
                },
                {
                    "subject": "Side effects question",
                    "body": "We started {medication} last week and noticed {side_effect}. Is this normal? Should we continue?",
                    "reply": "The symptoms you're describing can be a common side effect. Continue the medication unless symptoms worsen significantly. If you notice {warning_sign}, please call us right away."
                },
            ],
            MessageCategory.FOLLOW_UP: [
                {
                    "subject": "Follow up on visit",
                    "body": "Hi, we saw you last week for {reason}. {child_name} is doing better but I wanted to check if we should schedule a follow-up appointment?",
                    "reply": "Glad to hear {child_name} is improving! Since symptoms are resolving, no follow-up is needed unless symptoms return. If you have any concerns, don't hesitate to reach out."
                },
                {
                    "subject": "Update after appointment",
                    "body": "Just wanted to let you know that {child_name} is feeling much better after starting the treatment. Thank you!",
                    "reply": "Thank you for the update! We're happy to hear {child_name} is doing well. Take care!"
                },
            ],
            MessageCategory.APPOINTMENT_REQUEST: [
                {
                    "subject": "Need to schedule appointment",
                    "body": "Hello, we need to schedule an appointment for {child_name}. {reason}. What times do you have available this week?",
                    "reply": "We have availability on {day} at {time}. Please call the office at {phone} to confirm, or reply to this message if that time works."
                },
            ],
            MessageCategory.LAB_RESULT_QUESTION: [
                {
                    "subject": "Question about lab results",
                    "body": "Hi, I saw that {child_name}'s lab results are in the portal. Can someone explain what they mean? I'm not sure if they're normal.",
                    "reply": "I've reviewed the results and everything looks normal/within expected ranges. {explanation} Let us know if you have any other questions."
                },
            ],
            MessageCategory.SCHOOL_FORM: [
                {
                    "subject": "School form request",
                    "body": "Hi, {child_name} needs a {form_type} filled out for school. Can you please complete it? The deadline is {deadline}.",
                    "reply": "The form has been completed and is ready for pickup at the front desk. You can also request a digital copy be sent to the school directly."
                },
            ],
            MessageCategory.AVOID_VISIT: [
                {
                    "subject": "Quick question - hoping to avoid visit",
                    "body": "Hi, {child_name} has {symptom}. I'm trying to figure out if we need to come in or if there's something we can try at home first?",
                    "reply": "Based on what you've described, you can try {home_treatment} for the next 24-48 hours. If {warning_signs}, please come in for an evaluation."
                },
            ],
        }

        # Symptoms and related data for templates
        common_symptoms = ["a cough", "a runny nose", "a fever", "stomach aches", "a rash", "ear pain", "a headache", "trouble sleeping"]
        side_effects = ["some stomach upset", "drowsiness", "decreased appetite", "mild headaches", "some irritability"]
        warning_signs = ["high fever over 104F", "difficulty breathing", "severe pain", "refusing to eat or drink", "extreme lethargy"]
        home_treatments = ["rest and fluids", "cool mist humidifier", "saline drops and suction", "age-appropriate pain reliever", "honey for cough (if over 1 year)"]
        pharmacies = ["CVS", "Walgreens", "Walmart Pharmacy", "Target Pharmacy", "local pharmacy"]
        form_types = ["sports physical form", "immunization record", "medical clearance form", "allergy action plan", "asthma action plan"]
        advice_options = ["ensure adequate hydration", "use a cool mist humidifier", "monitor temperature", "keep them comfortable with rest"]

        # Get medications from conditions for refill requests
        medication_names = ["the regular medication"]
        for condition in conditions:
            cond_lower = condition.display_name.lower()
            if "asthma" in cond_lower:
                medication_names.extend(["albuterol inhaler", "Flovent", "Qvar"])
            elif "adhd" in cond_lower:
                medication_names.extend(["methylphenidate", "Adderall", "Concerta"])
            elif "eczema" in cond_lower:
                medication_names.extend(["hydrocortisone cream", "Eucrisa", "triamcinolone"])
            elif "allergy" in cond_lower or "allergic" in cond_lower:
                medication_names.extend(["Zyrtec", "Flonase", "Claritin"])
            elif "anxiety" in cond_lower:
                medication_names.extend(["medication", "fluoxetine"])
            elif "constipation" in cond_lower:
                medication_names.extend(["MiraLAX", "polyethylene glycol"])

        # Select categories weighted by realism
        category_weights = {
            MessageCategory.CLINICAL_QUESTION: 30,
            MessageCategory.REFILL_REQUEST: 25 if len(conditions) > 0 else 5,
            MessageCategory.FOLLOW_UP: 15,
            MessageCategory.AVOID_VISIT: 15,
            MessageCategory.APPOINTMENT_REQUEST: 10,
            MessageCategory.SCHOOL_FORM: 8 if demographics.age_years >= 5 else 0,
            MessageCategory.LAB_RESULT_QUESTION: 5,
        }

        # Filter out zero-weight categories
        available_categories = [(cat, weight) for cat, weight in category_weights.items() if weight > 0]
        categories, weights = zip(*available_categories)

        # Generate messages
        for _ in range(num_messages):
            # Select category
            category = random.choices(categories, weights=weights, k=1)[0]

            # Select template
            templates = message_templates.get(category, message_templates[MessageCategory.CLINICAL_QUESTION])
            template = random.choice(templates)

            # Select medium (mostly portal, some phone)
            medium = MessageMedium.PORTAL if random.random() < 0.75 else MessageMedium.PHONE

            # Generate timestamp relative to encounters
            if encounters:
                # Pick a random encounter as reference point
                ref_encounter = random.choice(encounters)
                # Message can be 1-14 days after encounter
                days_after = random.randint(1, 14)
                sent_dt = ref_encounter.date + timedelta(days=days_after, hours=random.randint(7, 21))
                related_encounter_id = ref_encounter.id if random.random() < 0.5 else None
            else:
                # No encounters, generate within last year
                days_ago = random.randint(1, 365)
                sent_dt = datetime.now() - timedelta(days=days_ago)
                related_encounter_id = None

            # Reply typically within 4-48 hours (business hours more common)
            reply_hours = random.choices([4, 8, 24, 48], weights=[15, 40, 35, 10], k=1)[0]
            reply_dt = sent_dt + timedelta(hours=reply_hours)

            # Fill in template variables
            child_name = demographics.given_names[0] if demographics.given_names else "Child"
            symptom = random.choice(common_symptoms)
            medication = random.choice(medication_names)
            pharmacy = random.choice(pharmacies)
            side_effect = random.choice(side_effects)
            warning_sign = random.choice(warning_signs)
            home_treatment = random.choice(home_treatments)
            form_type = random.choice(form_types)
            advice = random.choice(advice_options)
            reason = random.choice(["a checkup", "follow-up on previous issue", "ongoing symptoms"])

            # Format message body
            body = template["body"].format(
                child_name=child_name,
                medication=medication,
                pharmacy=pharmacy,
                symptom=symptom,
                side_effect=side_effect,
                reason=reason,
                form_type=form_type,
                deadline="next week",
            )

            # Format reply
            reply = template["reply"].format(
                parent_name=sender_name.split()[0],
                child_name=child_name,
                medication=medication,
                advice=advice,
                warning_sign=warning_sign,
                warning_signs=warning_sign,
                home_treatment=home_treatment,
                explanation="No action needed at this time.",
                day="Thursday",
                time="2:30 PM",
                phone="(555) 123-4567",
            )

            # Determine related medication/condition
            related_medication_id = None
            related_condition = None
            if category == MessageCategory.REFILL_REQUEST and medication != "the regular medication":
                related_condition = medication

            # Create message
            message = PatientMessage(
                sent_datetime=sent_dt,
                reply_datetime=reply_dt,
                sender_name=sender_name,
                sender_is_patient=True,
                recipient_name=provider_name,
                replier_name=provider_name if medium == MessageMedium.PORTAL else f"Office - {provider_role}",
                replier_role=provider_role,
                category=category,
                medium=medium,
                subject=template["subject"].format(
                    child_name=child_name,
                    medication=medication,
                ),
                message_body=body,
                reply_body=reply,
                status=MessageStatus.COMPLETED,
                related_encounter_id=related_encounter_id,
                related_medication_id=related_medication_id,
                related_condition=related_condition,
            )

            messages.append(message)

        # Sort by sent datetime
        messages.sort(key=lambda m: m.sent_datetime)

        return messages

    def _extract_resolved_history(
        self,
        encounters: list[Encounter],
    ) -> tuple[list[Condition], list[Medication]]:
        """
        Extract resolved conditions and past medications from acute illness encounters.

        This creates the "resolved problems" and "past medications" sections by looking
        at what was diagnosed and prescribed in past acute illness visits.

        Returns:
            Tuple of (resolved_conditions, past_medications)
        """
        resolved_conditions = []
        past_medications = []
        seen_diagnoses = set()  # Avoid duplicates
        seen_medications = set()  # Avoid duplicates

        # ICD-10 codes for common acute conditions
        acute_icd_codes = {
            "acute otitis media": ("H66.90", "Otitis media, unspecified"),
            "otitis media": ("H66.90", "Otitis media, unspecified"),
            "upper respiratory infection": ("J06.9", "Acute upper respiratory infection, unspecified"),
            "uri": ("J06.9", "Acute upper respiratory infection, unspecified"),
            "fever": ("R50.9", "Fever, unspecified"),
            "viral syndrome": ("B34.9", "Viral infection, unspecified"),
            "gastroenteritis": ("A09", "Infectious gastroenteritis and colitis, unspecified"),
            "vomiting": ("R11.10", "Vomiting, unspecified"),
            "diarrhea": ("R19.7", "Diarrhea, unspecified"),
            "pharyngitis": ("J02.9", "Acute pharyngitis, unspecified"),
            "strep pharyngitis": ("J02.0", "Streptococcal pharyngitis"),
            "bronchiolitis": ("J21.9", "Acute bronchiolitis, unspecified"),
            "croup": ("J05.0", "Acute obstructive laryngitis [croup]"),
            "conjunctivitis": ("H10.9", "Unspecified conjunctivitis"),
            "pink eye": ("H10.9", "Unspecified conjunctivitis"),
            "influenza": ("J11.1", "Influenza due to unidentified influenza virus with other respiratory manifestations"),
            "hand foot mouth disease": ("B08.4", "Enteroviral vesicular stomatitis with exanthem"),
            "hfmd": ("B08.4", "Enteroviral vesicular stomatitis with exanthem"),
            "rash": ("R21", "Rash and other nonspecific skin eruption"),
            "insect bite": ("T14.0", "Superficial injury of unspecified body region"),
            "insect bite reaction": ("T14.0", "Superficial injury of unspecified body region"),
            "cellulitis": ("L03.90", "Cellulitis, unspecified"),
            "impetigo": ("L01.00", "Impetigo, unspecified"),
            "sinusitis": ("J01.90", "Acute sinusitis, unspecified"),
            "pneumonia": ("J18.9", "Pneumonia, unspecified organism"),
            "bronchitis": ("J20.9", "Acute bronchitis, unspecified"),
            "urinary tract infection": ("N39.0", "Urinary tract infection, site not specified"),
            "uti": ("N39.0", "Urinary tract infection, site not specified"),
        }

        # Process acute illness encounters
        for encounter in encounters:
            if encounter.type.value != "acute-illness":
                continue

            # Extract diagnoses as resolved conditions
            if encounter.assessment:
                for assessment in encounter.assessment:
                    if not assessment.diagnosis:
                        continue

                    diagnosis_lower = assessment.diagnosis.lower().strip()
                    if diagnosis_lower in seen_diagnoses:
                        continue
                    seen_diagnoses.add(diagnosis_lower)

                    # Get ICD-10 code
                    code_info = acute_icd_codes.get(diagnosis_lower, ("R69", assessment.diagnosis))

                    # Calculate resolution date (typically 7-14 days after encounter for acute)
                    resolution_days = random.randint(7, 14)
                    resolution_date = encounter.date.date() + timedelta(days=resolution_days)

                    resolved_condition = Condition(
                        display_name=assessment.diagnosis,
                        code=CodeableConcept(
                            system="http://hl7.org/fhir/sid/icd-10-cm",
                            code=code_info[0],
                            display=code_info[1],
                        ),
                        clinical_status=ConditionStatus.RESOLVED,
                        onset_date=encounter.date.date() if isinstance(encounter.date, datetime) else encounter.date,
                        abatement_date=resolution_date,
                    )
                    resolved_conditions.append(resolved_condition)

            # Extract prescriptions as past medications
            if encounter.prescriptions:
                for rx in encounter.prescriptions:
                    # Skip if we've seen this medication
                    med_key = f"{rx.display_name}_{rx.start_date}"
                    if med_key in seen_medications:
                        continue
                    seen_medications.add(med_key)

                    # Create a copy with completed/stopped status
                    # PRN meds get "stopped", scheduled meds get "completed"
                    new_status = MedicationStatus.STOPPED if rx.prn else MedicationStatus.COMPLETED

                    # Calculate end date based on typical course
                    if rx.end_date:
                        end_date = rx.end_date
                    else:
                        # Typical antibiotic course is 7-10 days
                        course_days = 10 if "amox" in rx.display_name.lower() else 7
                        end_date = rx.start_date + timedelta(days=course_days)

                    past_med = Medication(
                        code=rx.code,
                        display_name=rx.display_name,
                        status=new_status,
                        dose_quantity=rx.dose_quantity,
                        dose_unit=rx.dose_unit,
                        frequency=rx.frequency,
                        route=rx.route,
                        prn=rx.prn,
                        start_date=rx.start_date,
                        end_date=end_date,
                        indication=rx.indication,
                        discontinuation_reason="Course completed" if new_status == MedicationStatus.COMPLETED else "No longer needed",
                    )
                    past_medications.append(past_med)

        return resolved_conditions, past_medications

    # =========================================================================
    # SINGLE CASE GENERATION (for learning platform)
    # =========================================================================

    # Difficulty level configurations
    DIFFICULTY_CONFIGS = {
        1: {  # Routine
            "name": "Routine",
            "description": "Well-child or straightforward visit",
            "visit_types": [EncounterType.WELL_CHILD],
            "condition_pool": [],  # No acute illness
            "atypical_probability": 0.0,
            "complicating_factors": [],
        },
        2: {  # Standard
            "name": "Standard",
            "description": "Common illness, classic presentation",
            "visit_types": [EncounterType.ACUTE_ILLNESS],
            "condition_pool": [
                "otitis_media", "pharyngitis", "viral_gastroenteritis",
                "bronchiolitis", "conjunctivitis", "viral_uri"
            ],
            "atypical_probability": 0.0,
            "complicating_factors": [],
        },
        3: {  # Complex
            "name": "Complex",
            "description": "Multiple factors, management decisions needed",
            "visit_types": [EncounterType.ACUTE_ILLNESS, EncounterType.CHRONIC_FOLLOWUP],
            "condition_pool": [
                "asthma", "pneumonia", "urinary_tract_infection",
                "croup", "eczema", "allergic_rhinitis"
            ],
            "atypical_probability": 0.1,
            "complicating_factors": ["concurrent_chronic", "medication_failure"],
        },
        4: {  # Challenging
            "name": "Challenging",
            "description": "Atypical presentation, competing diagnoses",
            "visit_types": [EncounterType.ACUTE_ILLNESS, EncounterType.URGENT_CARE],
            "condition_pool": [
                "pneumonia", "urinary_tract_infection", "appendicitis",
                "meningitis", "sepsis", "intussusception"
            ],
            "atypical_probability": 0.5,
            "complicating_factors": [
                "atypical_vitals", "conflicting_history", "red_herrings"
            ],
        },
        5: {  # Zebra
            "name": "Zebra",
            "description": "Rare or unexpected diagnosis",
            "visit_types": [EncounterType.ACUTE_ILLNESS, EncounterType.ED_VISIT],
            "condition_pool": [
                "kawasaki_disease", "henoch_schonlein_purpura",
                "juvenile_idiopathic_arthritis", "type_1_diabetes",
                "leukemia", "child_abuse"
            ],
            "atypical_probability": 0.8,
            "complicating_factors": [
                "atypical_vitals", "conflicting_history", "red_herrings",
                "social_complexity", "communication_barrier"
            ],
        },
    }

    def generate_encounter_for_patient(
        self,
        patient: Patient,
        difficulty_level: int = 2,
        visit_type: EncounterType | None = None,
        condition: str | None = None,
    ) -> Encounter:
        """
        Generate a new encounter for an existing patient.

        This is the core method for the learning platform's "Single Case"
        feature, creating realistic follow-up visits with configurable
        difficulty.

        Args:
            patient: Existing patient to generate encounter for
            difficulty_level: 1-5 (Routine, Standard, Complex, Challenging, Zebra)
            visit_type: Optional override for encounter type
            condition: Optional specific condition to use

        Returns:
            A new Encounter object
        """
        from src.models import GrowthMeasurement
        from knowledge.growth.cdc_2000 import GrowthTrajectory

        # Validate difficulty level
        difficulty_level = max(1, min(5, difficulty_level))
        config = self.DIFFICULTY_CONFIGS[difficulty_level]

        # Get patient demographics
        demographics = patient.demographics
        age_months = demographics.age_months
        sex = "male" if demographics.sex_at_birth == Sex.MALE else "female"

        # Determine visit type
        if visit_type is None:
            visit_type = random.choice(config["visit_types"])

        # Determine condition for acute/urgent visits
        encounter_condition = None
        if visit_type in (EncounterType.ACUTE_ILLNESS, EncounterType.URGENT_CARE, EncounterType.ED_VISIT):
            if condition:
                encounter_condition = condition
            elif config["condition_pool"]:
                # Filter conditions by age appropriateness
                valid_conditions = []
                for cond_key in config["condition_pool"]:
                    cond_data = self._conditions.get(cond_key, {})
                    demo = cond_data.get("demographics", {})
                    age_range = demo.get("age_months", {})
                    min_age = age_range.get("min", 0)
                    max_age = age_range.get("max", 264)
                    if min_age <= age_months <= max_age:
                        valid_conditions.append(cond_key)

                if valid_conditions:
                    encounter_condition = random.choice(valid_conditions)
                else:
                    # Fall back to common conditions
                    encounter_condition = random.choice(["otitis_media", "viral_uri", "pharyngitis"])

        # Build the encounter stub
        today = date.today()
        encounter_reason = self._get_encounter_reason(visit_type, encounter_condition, age_months)

        stub = EncounterStub(
            date=today,
            type=visit_type,
            reason=encounter_reason,
            conditions_to_address=[c.display_name for c in patient.active_conditions] if visit_type == EncounterType.CHRONIC_FOLLOWUP else [],
            is_new_condition_diagnosis=(encounter_condition is not None),
            new_condition=encounter_condition,
        )

        # Get or generate growth data
        latest_growth = patient.growth_data[-1] if patient.growth_data else None
        if not latest_growth:
            # Generate plausible growth measurement
            growth_trajectory = GrowthTrajectory(
                sex=sex,
                weight_percentile=50,
                height_percentile=50,
                hc_percentile=50,
            )
            weight, height, hc, bmi = growth_trajectory.generate_measurement(age_months)
            latest_growth = GrowthMeasurement(
                date=today,
                age_in_days=(today - demographics.date_of_birth).days,
                weight_kg=weight,
                height_cm=height,
                head_circumference_cm=hc if age_months <= 36 else None,
                bmi=bmi,
            )

        # Create life arc from existing conditions
        life_arc = LifeArc(
            health_trajectory="complex" if len(patient.active_conditions) > 2 else "healthy",
            major_conditions=[c.display_name for c in patient.active_conditions],
            condition_onset_ages={},
            hospitalizations=[],
            surgeries=[],
            key_events=[],
        )

        # Get or create provider/location
        provider = patient.care_team[0] if patient.care_team else Provider(
            name=self._generate_provider_name(),
            credentials="MD",
            specialty="Pediatrics",
        )
        if isinstance(provider, dict):
            provider = Provider(**provider)
        elif hasattr(provider, 'name'):
            provider = Provider(
                name=provider.name,
                credentials=getattr(provider, 'credentials', 'MD'),
                specialty=getattr(provider, 'specialty', 'Pediatrics'),
            )

        location = Location(
            name="Main Street Pediatrics",
            type="Outpatient clinic",
        )

        # Apply difficulty modifiers
        encounter = self._generate_encounter(
            stub=stub,
            demographics=demographics,
            age_months=age_months,
            growth_data=[latest_growth] if latest_growth else [],
            life_arc=life_arc,
            provider=provider,
            location=location,
            seed=GenerationSeed(),
        )

        # Apply difficulty-specific modifications
        encounter = self._apply_difficulty_modifiers(encounter, difficulty_level, config)

        # Generate LLM content if enabled
        if self.use_llm:
            self._generate_narratives_parallel([encounter], demographics, life_arc)

        return encounter

    def _get_encounter_reason(
        self,
        visit_type: EncounterType,
        condition_key: str | None,
        age_months: int
    ) -> str:
        """Generate appropriate reason for visit based on type and condition."""
        if visit_type == EncounterType.WELL_CHILD:
            return f"Well-child examination - {self._age_to_description(age_months)}"
        elif visit_type == EncounterType.NEWBORN:
            return "Healthy newborn visit"
        elif visit_type == EncounterType.CHRONIC_FOLLOWUP:
            return "Chronic condition follow-up"
        elif condition_key:
            # Get display name from conditions database
            cond_data = self._conditions.get(condition_key, {})
            return cond_data.get("display_name", condition_key.replace("_", " ").title())
        else:
            return "Acute illness"

    def _apply_difficulty_modifiers(
        self,
        encounter: Encounter,
        difficulty_level: int,
        config: dict
    ) -> Encounter:
        """Apply difficulty-specific modifications to the encounter."""
        if difficulty_level <= 2:
            # Levels 1-2: No modifications, classic presentation
            return encounter

        # Levels 3-5: Apply complicating factors
        complicating_factors = config.get("complicating_factors", [])
        atypical_prob = config.get("atypical_probability", 0.0)

        # Atypical vitals
        if "atypical_vitals" in complicating_factors and random.random() < atypical_prob:
            if encounter.vital_signs:
                # Make vitals less obviously abnormal
                # e.g., afebrile UTI, hypothermic sepsis
                if encounter.vital_signs.temperature_f and encounter.vital_signs.temperature_f > 100.4:
                    # 30% chance of being afebrile despite infection
                    if random.random() < 0.3:
                        encounter.vital_signs.temperature_f = random.uniform(98.0, 99.5)

        # Red herrings in physical exam
        if "red_herrings" in complicating_factors and random.random() < atypical_prob:
            if encounter.physical_exam:
                # Add an incidental finding
                red_herrings = [
                    ("skin", "Small bruise noted on anterior shin"),
                    ("heent", "Mild nasal congestion"),
                    ("respiratory", "Occasional cough during exam"),
                ]
                system, finding = random.choice(red_herrings)
                current = getattr(encounter.physical_exam, system, None)
                if current:
                    setattr(encounter.physical_exam, system, f"{current}. {finding}")
                else:
                    setattr(encounter.physical_exam, system, finding)

        # Social complexity
        if "social_complexity" in complicating_factors and random.random() < 0.3:
            # Add a note about social factors
            if encounter.assessment and encounter.assessment[0].clinical_notes:
                encounter.assessment[0].clinical_notes += " Consider social factors in disposition."

        return encounter

    # =========================================================================
    # TIME TRAVEL: Timeline Generation Methods
    # =========================================================================

    _disease_arcs_cache: dict | None = None

    @classmethod
    def _load_disease_arcs(cls, knowledge_dir: Path) -> dict:
        """Load disease arcs from YAML file, with caching."""
        if cls._disease_arcs_cache is not None:
            return cls._disease_arcs_cache

        arcs_path = knowledge_dir / "conditions" / "disease_arcs.yaml"
        if arcs_path.exists():
            with open(arcs_path, 'r') as f:
                cls._disease_arcs_cache = yaml.safe_load(f)
        else:
            cls._disease_arcs_cache = {}

        return cls._disease_arcs_cache

    def generate_timeline(
        self,
        patient: Patient,
        arc_names: list[str] | None = None,
        snapshot_interval_months: int = 6,
    ) -> tuple[list["TimeSnapshot"], list["DiseaseArc"]]:
        """
        Generate timeline snapshots and disease arcs for a patient.

        This is the core Time Travel feature - it creates a series of
        snapshots showing the patient's clinical state at different ages,
        tracking disease progression over time.

        Args:
            patient: The patient to generate timeline for
            arc_names: Optional list of disease arcs to include (e.g., ['atopic_march'])
            snapshot_interval_months: Months between regular snapshots (default: 6)

        Returns:
            Tuple of (snapshots, disease_arcs)
        """
        from src.models.patient import (
            TimeSnapshot, DiseaseArc, ArcStage, ArcStageStatus,
            DecisionPoint, MedicationChange, MedicationChangeType
        )

        # Load disease arc definitions
        arc_defs = self._load_disease_arcs(self.knowledge_dir)

        demographics = patient.demographics
        current_age_months = demographics.age_months
        dob = demographics.date_of_birth

        # Determine which arcs to simulate
        if arc_names:
            active_arc_keys = [k for k in arc_names if k in arc_defs]
        else:
            # Infer arcs from patient's conditions
            active_arc_keys = self._infer_disease_arcs(patient, arc_defs)

        # Build DiseaseArc objects from definitions
        disease_arcs = []
        for arc_key in active_arc_keys:
            arc_def = arc_defs.get(arc_key, {})
            if not arc_def:
                continue

            stages = []
            for stage_def in arc_def.get("stages", []):
                age_range = stage_def.get("typical_age_range", [0, 264])
                stage = ArcStage(
                    condition_key=stage_def.get("condition_key", ""),
                    display_name=stage_def.get("display_name", ""),
                    typical_age_range=(age_range[0], age_range[1]),
                    symptoms=stage_def.get("symptoms", []),
                    treatments=stage_def.get("treatments", []),
                    transition_triggers=stage_def.get("transition_triggers", []),
                )
                stages.append(stage)

            arc = DiseaseArc(
                name=arc_def.get("name", arc_key),
                description=arc_def.get("description", ""),
                stages=stages,
                clinical_pearls=arc_def.get("clinical_pearls", []),
                references=arc_def.get("references", []),
            )
            disease_arcs.append(arc)

        # Generate snapshots at regular intervals
        snapshots = []
        prev_conditions = set()
        prev_medications = set()
        decision_points = []

        # Calculate snapshot ages
        snapshot_ages = list(range(0, current_age_months + 1, snapshot_interval_months))
        if current_age_months not in snapshot_ages:
            snapshot_ages.append(current_age_months)

        # Track which arc stages are active at each age
        arc_stage_activations = self._simulate_arc_progressions(
            disease_arcs, current_age_months, patient
        )

        for age_months in snapshot_ages:
            snapshot_date = dob + timedelta(days=age_months * 30)

            # Determine active conditions at this age
            active_conditions = self._get_conditions_at_age(
                patient, age_months, arc_stage_activations
            )
            active_meds = self._get_medications_at_age(
                patient, age_months, active_conditions, arc_stage_activations
            )

            # Calculate what changed
            current_condition_names = {c.display_name for c in active_conditions}
            current_med_names = {m.display_name for m in active_meds}

            new_conditions = list(current_condition_names - prev_conditions)
            resolved_conditions = list(prev_conditions - current_condition_names)

            medication_changes = []
            for med in current_med_names - prev_medications:
                medication_changes.append(MedicationChange(
                    type=MedicationChangeType.STARTED,
                    medication=med,
                ))
            for med in prev_medications - current_med_names:
                medication_changes.append(MedicationChange(
                    type=MedicationChangeType.STOPPED,
                    medication=med,
                ))

            # Get decision points for this age
            age_decisions = [
                dp for dp in decision_points
                if age_months - 3 <= dp.age_months <= age_months
            ]

            # Determine if this is a key moment
            is_key = bool(new_conditions or resolved_conditions or age_decisions)

            # Generate event description
            event_desc = None
            if new_conditions:
                event_desc = f"New: {', '.join(new_conditions)}"
            elif resolved_conditions:
                event_desc = f"Resolved: {', '.join(resolved_conditions)}"

            # Get growth at this age (interpolate if needed)
            growth = self._interpolate_growth(patient, age_months)

            snapshot = TimeSnapshot(
                age_months=age_months,
                date=snapshot_date,
                active_conditions=active_conditions,
                medications=active_meds,
                growth=growth,
                new_conditions=new_conditions,
                resolved_conditions=resolved_conditions,
                medication_changes=medication_changes,
                decision_points=age_decisions,
                is_key_moment=is_key,
                event_description=event_desc,
            )
            snapshots.append(snapshot)

            prev_conditions = current_condition_names
            prev_medications = current_med_names

        return snapshots, disease_arcs

    def _infer_disease_arcs(
        self,
        patient: Patient,
        arc_defs: dict
    ) -> list[str]:
        """Infer which disease arcs apply to this patient based on their conditions."""
        matching_arcs = []
        patient_conditions = {c.display_name.lower() for c in patient.problem_list}

        for arc_key, arc_def in arc_defs.items():
            stages = arc_def.get("stages", [])
            stage_conditions = {
                s.get("display_name", "").lower()
                for s in stages
            }
            # If patient has any of the arc's conditions, include it
            if patient_conditions & stage_conditions:
                matching_arcs.append(arc_key)

        return matching_arcs

    def _simulate_arc_progressions(
        self,
        disease_arcs: list["DiseaseArc"],
        current_age_months: int,
        patient: Patient
    ) -> dict[str, list[tuple[int, int, str]]]:
        """
        Simulate when each arc stage becomes active.

        Returns dict mapping arc name to list of (start_age, end_age, stage_name)
        """
        from src.models.patient import ArcStageStatus

        activations = {}

        for arc in disease_arcs:
            arc_activations = []
            cumulative_age = 0

            for i, stage in enumerate(arc.stages):
                # Determine when this stage activates
                min_age, max_age = stage.typical_age_range

                # Use middle of range as activation age
                if cumulative_age < min_age:
                    start_age = random.randint(min_age, min(max_age, current_age_months))
                else:
                    start_age = cumulative_age + random.randint(6, 18)

                if start_age > current_age_months:
                    # Haven't reached this stage yet
                    break

                # Determine end age (when next stage starts or current)
                if i + 1 < len(arc.stages):
                    next_min, _ = arc.stages[i + 1].typical_age_range
                    end_age = min(current_age_months, next_min + random.randint(0, 12))
                else:
                    end_age = current_age_months

                arc_activations.append((start_age, end_age, stage.display_name))
                stage.actual_onset_age = start_age
                stage.status = ArcStageStatus.ACTIVE if end_age >= current_age_months else ArcStageStatus.RESOLVED

                cumulative_age = start_age

            activations[arc.name] = arc_activations
            arc.current_stage_index = len(arc_activations) - 1 if arc_activations else 0

        return activations

    def _get_conditions_at_age(
        self,
        patient: Patient,
        age_months: int,
        arc_activations: dict
    ) -> list["Condition"]:
        """Get the active conditions at a specific age."""
        from src.models.patient import Condition, CodeableConcept, ConditionStatus

        active_conditions = []

        # Check arc-based conditions
        for arc_name, activations in arc_activations.items():
            for start_age, end_age, stage_name in activations:
                if start_age <= age_months <= end_age:
                    # This stage is active at this age
                    # Look up condition details from database
                    cond_key = stage_name.lower().replace(" ", "_")
                    cond_data = self._conditions.get(cond_key, {})

                    billing = cond_data.get("billing_codes", {})
                    icd10 = billing.get("icd10", ["R69"])[0] if isinstance(billing.get("icd10"), list) else billing.get("icd10", "R69")
                    snomed = billing.get("snomed", "")

                    condition = Condition(
                        display_name=stage_name,
                        code=CodeableConcept(
                            system="http://hl7.org/fhir/sid/icd-10-cm",
                            code=icd10,
                            display=stage_name,
                        ),
                        snomed_code=snomed,
                        clinical_status=ConditionStatus.ACTIVE,
                        onset_date=patient.demographics.date_of_birth + timedelta(days=start_age * 30),
                    )
                    active_conditions.append(condition)

        # Also include any patient conditions that are from earlier
        for cond in patient.problem_list:
            if cond.onset_date:
                onset_age = (cond.onset_date - patient.demographics.date_of_birth).days // 30
                if onset_age <= age_months:
                    # Check if it's not already in active_conditions
                    if not any(c.display_name == cond.display_name for c in active_conditions):
                        if cond.clinical_status == ConditionStatus.ACTIVE:
                            active_conditions.append(cond)

        return active_conditions

    def _get_medications_at_age(
        self,
        patient: Patient,
        age_months: int,
        active_conditions: list["Condition"],
        arc_activations: dict
    ) -> list["Medication"]:
        """Get medications that would be active at a specific age."""
        from src.models.patient import Medication, MedicationStatus, CodeableConcept

        active_meds = []
        condition_names = {c.display_name.lower() for c in active_conditions}
        dob = patient.demographics.date_of_birth
        med_start_date = dob + timedelta(days=age_months * 30)

        # Map conditions to typical medications with full details
        # (name, rxnorm, dose_qty, dose_unit, frequency, route)
        condition_meds = {
            "eczema": [
                ("Hydrocortisone 1% Cream", "310466", "Apply thin layer", "application", "BID", "topical")
            ],
            "asthma": [
                ("Albuterol HFA Inhaler", "351137", "1-2", "puffs", "Q4-6H PRN", "inhaled"),
                ("Fluticasone Propionate HFA", "315926", "1-2", "puffs", "BID", "inhaled"),
            ],
            "reactive airway disease": [
                ("Albuterol HFA Inhaler", "351137", "1-2", "puffs", "Q4-6H PRN", "inhaled")
            ],
            "allergic rhinitis": [
                ("Cetirizine", "203152", "5-10", "mg", "once daily", "oral"),
                ("Fluticasone Nasal Spray", "1013996", "1-2", "sprays", "daily", "nasal"),
            ],
            "food allergy": [
                ("Epinephrine Auto-Injector", "731370", "0.3", "mg", "PRN", "intramuscular")
            ],
            "adhd": [
                ("Methylphenidate", "303947", "10-20", "mg", "once daily", "oral")
            ],
            "anxiety disorder": [
                ("Sertraline", "311989", "25-50", "mg", "once daily", "oral")
            ],
            "obesity": [],  # Lifestyle modification, no meds typically
            "prediabetes": [],
            "type 2 diabetes": [
                ("Metformin", "6809", "500", "mg", "BID", "oral")
            ],
        }

        for cond_name in condition_names:
            meds = condition_meds.get(cond_name.lower(), [])
            for med_tuple in meds:
                med_name, rxnorm, dose_qty, dose_unit, frequency, route = med_tuple
                if not any(m.display_name == med_name for m in active_meds):
                    med = Medication(
                        display_name=med_name,
                        code=CodeableConcept(
                            system="http://www.nlm.nih.gov/research/umls/rxnorm",
                            code=rxnorm,
                            display=med_name,
                        ),
                        status=MedicationStatus.ACTIVE,
                        dose_quantity=dose_qty,
                        dose_unit=dose_unit,
                        frequency=frequency,
                        route=route,
                        start_date=med_start_date,
                    )
                    active_meds.append(med)

        return active_meds

    def _interpolate_growth(
        self,
        patient: Patient,
        age_months: int
    ) -> "GrowthMeasurement | None":
        """Get or interpolate growth measurement at a specific age."""
        from knowledge.growth.cdc_2000 import GrowthTrajectory
        from src.models.patient import GrowthMeasurement

        if not patient.growth_data:
            return None

        # Find closest measurement
        closest = min(
            patient.growth_data,
            key=lambda g: abs((g.date - patient.demographics.date_of_birth).days // 30 - age_months)
        )

        # If close enough, use it
        closest_age = (closest.date - patient.demographics.date_of_birth).days // 30
        if abs(closest_age - age_months) <= 3:
            return closest

        # Otherwise, interpolate using growth trajectory
        sex = "male" if patient.demographics.sex_at_birth == Sex.MALE else "female"
        growth = GrowthTrajectory(
            sex=sex,
            weight_percentile=50,  # Could be improved by inferring from existing data
            height_percentile=50,
            hc_percentile=50,
        )
        weight, height, hc, bmi = growth.generate_measurement(age_months)

        return GrowthMeasurement(
            date=patient.demographics.date_of_birth + timedelta(days=age_months * 30),
            age_in_days=age_months * 30,
            weight_kg=weight,
            height_cm=height,
            head_circumference_cm=hc if age_months <= 36 else None,
            bmi=bmi,
        )

    def get_snapshot_at_age(
        self,
        patient: Patient,
        age_months: int,
        arc_names: list[str] | None = None,
    ) -> tuple["TimeSnapshot", "TimeSnapshot | None"]:
        """
        Get the patient snapshot at a specific age.

        Returns tuple of (snapshot_at_age, previous_snapshot)
        """
        snapshots, _ = self.generate_timeline(
            patient,
            arc_names=arc_names,
            snapshot_interval_months=3,  # Higher resolution for specific lookup
        )

        # Find the closest snapshot
        closest = min(snapshots, key=lambda s: abs(s.age_months - age_months))
        closest_idx = snapshots.index(closest)

        prev = snapshots[closest_idx - 1] if closest_idx > 0 else None
        return closest, prev


class AdultEngine(BaseEngine):
    """
    Adult patient generation engine.
    
    Handles patients 18 and older.
    """
    
    def generate(self, seed: GenerationSeed) -> Patient:
        """Generate a complete adult patient."""
        # Placeholder - to be implemented
        raise NotImplementedError("Adult engine not yet implemented")
    
    def generate_life_arc(self, demographics: Demographics, seed: GenerationSeed) -> LifeArc:
        raise NotImplementedError("Adult engine not yet implemented")
    
    def generate_encounter_timeline(
        self,
        demographics: Demographics,
        life_arc: LifeArc,
        seed: GenerationSeed,
    ) -> list[EncounterStub]:
        raise NotImplementedError("Adult engine not yet implemented")


class EngineOrchestrator:
    """
    Routes patient generation to the appropriate engine based on age.
    """
    
    def __init__(self, llm_client: LLMClient | None = None):
        self.peds_engine = PedsEngine(llm_client=llm_client)
        self.adult_engine = AdultEngine(llm_client=llm_client)
    
    def generate(self, seed: GenerationSeed) -> Patient:
        """Generate a patient, routing to the appropriate engine."""
        # Determine target age
        if seed.age is not None:
            age_years = seed.age
        elif seed.age_months is not None:
            age_years = seed.age_months // 12
        else:
            # Default to random
            age_years = random.randint(0, 80)
        
        # Route to appropriate engine (peds cutoff at 22)
        if age_years < 22:
            return self.peds_engine.generate(seed)
        else:
            return self.adult_engine.generate(seed)
