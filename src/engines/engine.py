"""
Patient generation engines.

The engines orchestrate the generation of complete synthetic patients
by coordinating the various generators (persona, timeline, encounters, etc.).
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
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
    Patient,
    Provider,
    Location,
    Sex,
    SocialHistory,
)
from src.llm import get_client, LLMClient


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
    
    def generate(self, seed: GenerationSeed) -> Patient:
        """Generate a complete pediatric patient."""
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
        
        for stub in encounter_stubs:
            # Calculate age at encounter
            days_old = (stub.date - demographics.date_of_birth).days
            months_old = days_old // 30
            
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
            )
            encounters.append(encounter)
            
            # Collect immunizations
            immunizations.extend(encounter.immunizations_given)
        
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
        
        # Build the patient
        patient = Patient(
            demographics=demographics,
            social_history=social_history,
            problem_list=problem_list,
            encounters=encounters,
            growth_data=growth_data,
            immunization_record=immunizations,
            complexity_tier=tier,
            generation_seed=seed.model_dump(),
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
                onset_ages[cond] = random.randint(6, min(demographics.age_months, 120))
        elif tier != ComplexityTier.TIER_0:
            # Select conditions based on tier
            condition_pool = [
                "Asthma", "ADHD", "Eczema", "Allergic rhinitis", "Anxiety",
                "Food allergy", "Obesity", "Constipation", "Recurrent otitis media"
            ]

            num_conditions = {
                ComplexityTier.TIER_1: 1,
                ComplexityTier.TIER_2: random.randint(2, 3),
                ComplexityTier.TIER_3: random.randint(3, 5),
            }.get(tier, 0)

            conditions = random.sample(condition_pool, min(num_conditions, len(condition_pool)))

            # Apply co-morbidity logic - conditions tend to cluster
            conditions = self._apply_comorbidity_logic(conditions)

            for cond in conditions:
                onset_ages[cond] = random.randint(6, min(demographics.age_months, 120))
        
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
        for (min_age, max_age), frequency in self.ACUTE_ILLNESS_FREQUENCY.items():
            if current_age_months < min_age:
                continue
            
            # How many months of this age range has the patient lived?
            start_month = max(0, min_age)
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

                # Select illness based on season for realism
                illness = self._get_seasonal_illness(visit_date)
                
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
        
        # Sort by date
        stubs.sort(key=lambda s: s.date)
        
        # Limit to requested count if specified
        if seed.encounter_count:
            stubs = stubs[:seed.encounter_count]
        
        return stubs
    
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
        
        # Generate vitals
        vitals_dict = generate_normal_vitals(age_months)
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
            # Simplified exam for acute visits
            physical_exam = PhysicalExam(
                general="Alert, active, in no acute distress",
                heent="Normocephalic. TMs clear. Oropharynx mildly erythematous." if "URI" in stub.reason or "respiratory" in stub.reason.lower() else "Unremarkable",
                cardiovascular="Regular rate and rhythm",
                respiratory="Clear to auscultation bilaterally",
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
            # Generate appropriate plan for acute illnesses
            illness_plans = self._generate_acute_illness_plan(stub.reason)
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
            anticipatory_guidance=self._generate_anticipatory_guidance_list(age_months) if stub.type in (EncounterType.WELL_CHILD, EncounterType.NEWBORN) else [],
        )
        
        # Generate narrative note if requested
        if seed.include_narrative_notes:
            encounter.narrative_note = self._generate_narrative_note(encounter, demographics, age_months)
        
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
    
    def _generate_narrative_note(self, encounter: Encounter, demographics: Demographics, age_months: int) -> str:
        """Generate a narrative clinical note."""
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

        Research shows certain conditions cluster together:
        - Obesity: associated with asthma, ADHD, anxiety, sleep apnea, GERD
        - Asthma: associated with eczema, allergic rhinitis (atopic triad)
        - ADHD: associated with anxiety, learning disorders, sleep issues
        - Eczema: associated with asthma, food allergy (allergic march)
        """
        result = list(conditions)

        # Co-morbidity associations with probabilities
        comorbidity_map = {
            "Obesity": [
                ("Asthma", 0.35),  # 35% chance to add if obesity present
                ("ADHD", 0.25),
                ("Anxiety", 0.30),
                ("Obstructive sleep apnea", 0.20),
                ("GERD", 0.15),
                ("Prediabetes", 0.15),
            ],
            "Asthma": [
                ("Eczema", 0.40),  # Atopic triad
                ("Allergic rhinitis", 0.50),
                ("Food allergy", 0.25),
            ],
            "Eczema": [
                ("Asthma", 0.35),  # Allergic march
                ("Food allergy", 0.30),
                ("Allergic rhinitis", 0.35),
            ],
            "ADHD": [
                ("Anxiety", 0.35),
                ("Learning disorder", 0.25),
                ("Sleep disorder", 0.20),
            ],
            "Anxiety": [
                ("ADHD", 0.20),
                ("Depression", 0.25),
            ],
            "Food allergy": [
                ("Eczema", 0.40),
                ("Asthma", 0.30),
            ],
        }

        # Check each existing condition for potential co-morbidities
        for condition in list(result):  # Iterate over copy to allow modification
            if condition in comorbidity_map:
                for comorbid, probability in comorbidity_map[condition]:
                    if comorbid not in result and random.random() < probability:
                        result.append(comorbid)

        return result

    def _get_seasonal_illness(self, visit_date: date) -> str:
        """Select an illness based on the season of the visit date."""
        month = visit_date.month

        # Define seasonal illness distributions
        # Winter (Dec-Feb): More respiratory illness, flu
        # Spring (Mar-May): Allergies, some viral
        # Summer (Jun-Aug): Skin issues, GI, injuries
        # Fall (Sep-Nov): Back-to-school illnesses, respiratory

        winter_illnesses = [
            ("Upper respiratory infection", 25),
            ("Influenza-like illness", 15),
            ("Viral syndrome", 15),
            ("Otitis media", 15),
            ("Croup", 10),
            ("Bronchiolitis", 10),
            ("Fever", 5),
            ("Gastroenteritis", 5),
        ]

        spring_illnesses = [
            ("Allergic rhinitis flare", 20),
            ("Upper respiratory infection", 15),
            ("Viral syndrome", 15),
            ("Otitis media", 15),
            ("Conjunctivitis", 10),
            ("Rash", 10),
            ("Fever", 10),
            ("Gastroenteritis", 5),
        ]

        summer_illnesses = [
            ("Gastroenteritis", 20),
            ("Skin infection/rash", 15),
            ("Swimmer's ear", 10),
            ("Viral syndrome", 15),
            ("Insect bite reaction", 10),
            ("Conjunctivitis", 10),
            ("Fever", 10),
            ("Upper respiratory infection", 10),
        ]

        fall_illnesses = [
            ("Upper respiratory infection", 25),
            ("Viral syndrome", 15),
            ("Otitis media", 15),
            ("Fever", 10),
            ("Cough", 10),
            ("Gastroenteritis", 10),
            ("Conjunctivitis", 10),
            ("Rash", 5),
        ]

        # Select illness pool based on month
        if month in [12, 1, 2]:  # Winter
            illness_pool = winter_illnesses
        elif month in [3, 4, 5]:  # Spring
            illness_pool = spring_illnesses
        elif month in [6, 7, 8]:  # Summer
            illness_pool = summer_illnesses
        else:  # Fall (9, 10, 11)
            illness_pool = fall_illnesses

        # Weighted random selection
        illnesses, weights = zip(*illness_pool)
        return random.choices(illnesses, weights=weights, k=1)[0]

    def _generate_acute_illness_plan(self, reason: str) -> list[PlanItem]:
        """Generate plan items for acute illness visits."""
        from src.models import PlanItem

        plans = []
        reason_lower = reason.lower()

        # Common acute illness plans
        if "respiratory" in reason_lower or "uri" in reason_lower or "cold" in reason_lower:
            plans.append(PlanItem(
                category="treatment",
                description="Supportive care with rest and hydration",
            ))
            plans.append(PlanItem(
                category="medication",
                description="Acetaminophen or ibuprofen as needed for fever/discomfort",
            ))
            plans.append(PlanItem(
                category="education",
                description="Return precautions reviewed: difficulty breathing, high fever >72hrs, worsening symptoms",
            ))
        elif "otitis" in reason_lower or "ear" in reason_lower:
            plans.append(PlanItem(
                category="medication",
                description="Amoxicillin 90mg/kg/day divided BID x 10 days",
            ))
            plans.append(PlanItem(
                category="treatment",
                description="Ibuprofen for pain management",
            ))
            plans.append(PlanItem(
                category="follow-up",
                description="Return if no improvement in 48-72 hours",
            ))
        elif "gastroenteritis" in reason_lower or "vomiting" in reason_lower or "diarrhea" in reason_lower:
            plans.append(PlanItem(
                category="treatment",
                description="Oral rehydration with small frequent amounts of fluids",
            ))
            plans.append(PlanItem(
                category="diet",
                description="BRAT diet as tolerated, advance as symptoms improve",
            ))
            plans.append(PlanItem(
                category="education",
                description="Return if unable to keep fluids down, bloody stool, or signs of dehydration",
            ))
        elif "fever" in reason_lower:
            plans.append(PlanItem(
                category="treatment",
                description="Acetaminophen or ibuprofen for temperature control",
            ))
            plans.append(PlanItem(
                category="education",
                description="Monitor for source of infection, return if fever persists >3 days",
            ))
            plans.append(PlanItem(
                category="follow-up",
                description="Follow up as needed if symptoms worsen",
            ))
        elif "rash" in reason_lower:
            plans.append(PlanItem(
                category="treatment",
                description="Topical care as appropriate for rash type",
            ))
            plans.append(PlanItem(
                category="education",
                description="Return if rash spreads, becomes painful, or child develops fever",
            ))
        elif "conjunctivitis" in reason_lower or "pink eye" in reason_lower:
            plans.append(PlanItem(
                category="medication",
                description="Antibiotic eye drops if bacterial; supportive care if viral",
            ))
            plans.append(PlanItem(
                category="education",
                description="Good hand hygiene to prevent spread",
            ))
        else:
            # Generic acute illness plan
            plans.append(PlanItem(
                category="treatment",
                description="Supportive care with rest and hydration",
            ))
            plans.append(PlanItem(
                category="follow-up",
                description="Return if symptoms worsen or do not improve in 3-5 days",
            ))

        return plans

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
                    category="assessment",
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
                    category="assessment",
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
                category="treatment",
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
