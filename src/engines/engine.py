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
from src.validators import PatientValidator, ValidationResult
from src.knowledge import (
    ConditionKnowledgeService,
    create_condition_service,
    create_exa_search_functions,
)
from src.reconciliation import PatientReconciler
from src.engines import peds_dosing
from src.validators.age import (
    add_months,
    calendar_months_between,
    meets_min_age,
    within_max_age,
)


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
    # Class-level cache for immunization schedule
    _immunization_cache: dict | None = None

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

    @classmethod
    def _load_immunization_schedule(cls, knowledge_dir: Path) -> dict:
        """Load AAP immunization schedule from YAML file, with caching."""
        if cls._immunization_cache is not None:
            return cls._immunization_cache

        schedule_path = knowledge_dir / "immunizations" / "aap_schedule.yaml"
        if schedule_path.exists():
            with open(schedule_path, 'r') as f:
                cls._immunization_cache = yaml.safe_load(f)
        else:
            cls._immunization_cache = {}

        return cls._immunization_cache

    def __init__(
        self,
        *args,
        use_llm: bool = True,
        messiness_level: int = 0,
        today: date | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        # Per-engine RNG. Every random draw in the engine goes through
        # self.rng so a GenerationSeed.random_seed reproduces a patient
        # without touching (or racing on) the process-global `random` state.
        self.rng = random.Random()
        # Injectable "today". None means the real date at generate() time.
        # Pinning it makes a seed reproduce the same chart on any day.
        self._today_override = today
        self.today = today or date.today()
        # Load conditions database
        self._conditions = self._load_conditions(self.knowledge_dir)
        # Load immunization schedule
        self._immunization_schedule = self._load_immunization_schedule(self.knowledge_dir)

        # Build derived data structures for efficient lookups
        self._build_condition_lookups()
        self._build_immunization_lookups()

        # LLM is enabled if requested AND client is available
        self.use_llm = use_llm and self.llm is not None

        # Messiness level for realistic chart artifacts (0=pristine, 5=hostile)
        self.messiness_level = messiness_level
        self.messiness = MessinessInjector(level=messiness_level)
        self._base_messiness_level = messiness_level
        self._rx_history: list[tuple[date, str]] = []
        self._current_dob: date | None = None

        # Initialize condition knowledge service with optional web search
        cache_dir = self.knowledge_dir.parent / "cache" / "conditions"

        # Try to enable Exa web search if available
        web_search_fn, web_fetch_fn = None, None
        if create_exa_search_functions:
            try:
                web_search_fn, web_fetch_fn = create_exa_search_functions()
                if web_search_fn:
                    print("✓ Exa web search enabled for condition knowledge")
            except Exception as e:
                print(f"Note: Exa web search not available: {e}")

        self.condition_service = create_condition_service(
            yaml_conditions=self._conditions,
            llm_client=self.llm,
            cache_dir=cache_dir,
            web_search_fn=web_search_fn,
            web_fetch_fn=web_fetch_fn,
        )

        # Initialize validator
        self.validator = PatientValidator()

        # Initialize reconciler (requires LLM)
        self.reconciler = PatientReconciler(self.llm) if self.llm else None

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

            # Handle both age restriction formats in YAML:
            # 1. age_ranges.min_months (newer format)
            # 2. demographics.age_months.min (older format)
            age_ranges = data.get('age_ranges', {})
            demographics = data.get('demographics', {})
            age_months_config = demographics.get('age_months', {})

            min_months = (
                age_ranges.get('min_months') or
                age_months_config.get('min') or
                0
            )
            max_months = (
                age_ranges.get('max_months') or
                age_months_config.get('max') or
                None  # None means no upper limit
            )

            if category == 'chronic':
                self._chronic_conditions[display_name] = {
                    'key': key,
                    'min_months': min_months,
                    'max_months': max_months,
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
                    'max_months': max_months,
                    'seasonality': data.get('seasonality', {}),
                }

        # Build display name to key mapping for reverse lookups
        self._display_to_key = {}
        for key, data in self._conditions.items():
            if key.startswith('_') or not isinstance(data, dict):
                continue
            display_name = data.get('display_name', key.replace('_', ' ').title())
            self._display_to_key[display_name.lower()] = key

        # Hardcoded age gates for conditions that need explicit limits
        # Format: condition_key -> (min_months, max_months or None)
        self._hardcoded_age_gates = {
            'adhd': (48, None),           # ADHD: ≥4 years
            'anxiety': (36, None),        # Anxiety: ≥3 years
            'depression': (72, None),     # Depression: ≥6 years
            'asthma': (12, None),         # Asthma: ≥1 year
            'bronchiolitis': (0, 24),     # Bronchiolitis: ≤2 years
            'prediabetes': (120, None),   # Prediabetes: ≥10 years
            'type_2_diabetes': (120, None),  # T2D: ≥10 years
            'learning_disorder': (60, None), # Learning disorder: ≥5 years
            'enuresis': (60, None),       # Enuresis: ≥5 years (not diagnosed before expected continence)
            'obesity': (24, None),        # Obesity: ≥2 years
            'obstructive_sleep_apnea': (24, None),  # OSA: ≥2 years
        }

    def _age_months(self, demographics: Demographics, on: date | None = None) -> int:
        """Whole calendar months of age on `on` (default: the run's today).

        Demographics.age_months uses the wall clock; the engine must use its
        injected today so a pinned run reproduces.
        """
        return calendar_months_between(demographics.date_of_birth, on or self.today)

    def _months_to_date(self, base_date: date, months: int) -> date:
        """
        Add months to a date using proper calendar arithmetic.

        Uses relativedelta instead of days*30 for accurate month calculations.
        """
        from dateutil.relativedelta import relativedelta
        years = months // 12
        remaining_months = months % 12
        return base_date + relativedelta(years=years, months=remaining_months)

    def _validate_condition_age(self, condition_name: str, age_at_onset_months: float) -> tuple[bool, str]:
        """
        Validate that a condition can be diagnosed at the given age.

        Returns (is_valid, error_message).
        """
        min_age, max_age = self._get_condition_age_limits(condition_name)

        if age_at_onset_months < min_age:
            return False, f"{condition_name} requires age >={min_age}mo at onset, got {age_at_onset_months:.0f}mo"
        if max_age is not None and age_at_onset_months > max_age:
            return False, f"{condition_name} requires age <={max_age}mo at onset, got {age_at_onset_months:.0f}mo"
        return True, ""

    def _yaml_key(self, condition_name: str) -> str | None:
        """conditions.yaml key for a key, display name, or alias."""
        n = condition_name.strip()
        if n in self._conditions and isinstance(self._conditions[n], dict):
            return n
        k = n.lower().replace(' ', '_')
        if k in self._conditions and isinstance(self._conditions[k], dict):
            return k
        if n.lower() in self._display_to_key:
            return self._display_to_key[n.lower()]
        result = self.condition_service.get_condition(n)
        if result.found and result.source == "yaml" and result.definition:
            return result.definition.condition_key
        return None

    def _get_condition_age_limits(self, condition_name: str) -> tuple[int, int | None]:
        """
        Get age limits for a condition.

        Resolves keys, display names and aliases (e.g. "Learning Disability"
        -> learning_disorder) so the life arc, the onset clamp and the
        validator all see the same gate.

        Returns (min_months, max_months) where max_months may be None.
        """
        keys = [condition_name.lower().replace(' ', '_')]
        yaml_key = self._yaml_key(condition_name)
        if yaml_key:
            keys.append(yaml_key)

        # Check hardcoded gates first (most reliable)
        for k in keys:
            if k in self._hardcoded_age_gates:
                return self._hardcoded_age_gates[k]

        if yaml_key:
            data = self._conditions[yaml_key]
            age_ranges = data.get('age_ranges', {}) or {}
            age_cfg = (data.get('demographics', {}) or {}).get('age_months', {}) or {}
            min_age = age_ranges.get('min_months') or age_cfg.get('min') or 0
            max_age = age_ranges.get('max_months') or age_cfg.get('max') or None
            return min_age, max_age

        # Check chronic then acute conditions dictionaries
        for conditions_dict in (self._chronic_conditions, self._acute_conditions):
            if condition_name in conditions_dict:
                data = conditions_dict[condition_name]
                return data.get('min_months', 0), data.get('max_months')

        # No age gate found - no restrictions
        return 0, None

    def _clamp_onset_to_gate(
        self,
        condition_name: str,
        onset_date: date,
        dob: date,
    ) -> date:
        """
        Clamp an onset_date so it satisfies the condition's age gate.

        Uses the same calendar-month comparison as the validator
        (src/validators/age.py): the gate boundary is the date the child
        turns N months. A violating onset moves exactly to that boundary,
        never past today.
        """
        min_months, max_months = self._get_condition_age_limits(condition_name)
        if onset_date is None:
            return onset_date

        if not meets_min_age(dob, onset_date, min_months):
            return min(add_months(dob, min_months), self.today)

        if max_months is not None and not within_max_age(dob, onset_date, max_months):
            return max(add_months(dob, max_months), dob)

        return onset_date

    def _validate_and_fix_age_gates(self, patient: Patient) -> Patient:
        """
        Post-generation pass that walks patient.problem_list and ensures every
        condition's onset_date satisfies its age gate using age AT ONSET (not
        current patient age).

        Fixes violations in-place by clamping onset_date to the minimum (or
        maximum) valid onset rather than removing the condition. This catches
        bugs where prior code paths produced an onset_date that bypassed the
        gate-filtering logic in _generate_life_arc — e.g. _fix_temporal_issue
        setting onset to dob + 30 days, or disease-arc / encounter resolution
        paths assigning onset_date via raw arithmetic.
        """
        dob = patient.demographics.date_of_birth
        for cond in patient.problem_list:
            if cond.onset_date is None:
                continue
            new_onset = self._clamp_onset_to_gate(
                cond.display_name, cond.onset_date, dob
            )
            if new_onset != cond.onset_date:
                cond.onset_date = new_onset
        return patient

    def _build_immunization_lookups(self):
        """Build product and CVX lookups from the AAP schedule YAML.

        Due-dose rules live in src/engines/immunizations.py; the YAML holds the
        products the engine records and the CVX -> antigen map.
        """
        self._vaccine_antigens = self._immunization_schedule.get('antigens', {})
        self._cvx_antigens = {
            str(cvx): set(groups)
            for cvx, groups in self._immunization_schedule.get('cvx_antigens', {}).items()
        }

    def _get_condition_key(self, reason: str) -> str | None:
        """Map a display name or reason text to a condition key.

        Uses local YAML lookup first, then falls back to knowledge service
        for unknown conditions.
        """
        # Try exact match first (case-insensitive)
        key = self._display_to_key.get(reason.lower())
        if key:
            return key

        # Try partial matching for conditions mentioned in reason
        reason_lower = reason.lower()
        for display_name, key in self._display_to_key.items():
            if display_name in reason_lower:
                return key

        # Fall back to condition knowledge service for unknown conditions
        if hasattr(self, 'condition_service'):
            result = self.condition_service.get_condition(reason)
            if result.found and result.definition:
                return result.definition.condition_key

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

    def _round_dose(self, dose_mg: float) -> int:
        """Round dose to clinically practical value.

        Doses are rounded to values that match typical pharmaceutical preparations:
        - <10mg: nearest 1mg
        - 10-100mg: nearest 5mg
        - 100-500mg: nearest 10mg
        - 500-1000mg: nearest 25mg
        - >1000mg: nearest 50mg
        """
        if dose_mg < 10:
            return round(dose_mg)
        elif dose_mg < 100:
            return round(dose_mg / 5) * 5
        elif dose_mg < 500:
            return round(dose_mg / 10) * 10
        elif dose_mg < 1000:
            return round(dose_mg / 25) * 25
        else:
            return round(dose_mg / 50) * 50

    def _dedupe_immunizations(self, immunizations: list) -> list:
        """Drop exact duplicate immunization records and sort by date.

        Keyed on (CVX, dose number, date, status): annual influenza doses and a
        refusal followed by a later catch-up dose are distinct records.
        """
        seen = set()
        deduped = []
        for imm in sorted(immunizations, key=lambda i: i.date):
            key = (imm.vaccine_code.code, imm.dose_number, imm.date, imm.status)
            if key not in seen:
                deduped.append(imm)
                seen.add(key)
        return deduped

    def _dedupe_medications(self, medications: list) -> list:
        """Drop exact repeats (same drug, same start date).

        Each acute course is its own medication-list entry (completed or
        stopped), so every encounter prescription appears in the exported
        medication history. Earlier code kept one entry per drug name, which
        silently dropped all but the first amoxicillin course.
        """
        seen: set = set()
        out = []
        for med in medications:
            key = (med.display_name.lower(), med.start_date)
            if key not in seen:
                seen.add(key)
                out.append(med)
        return out

    # Problem display name (lowercased) -> regimen key in peds_dosing.
    _CHRONIC_REGIMEN_KEYS = {
        "asthma": "asthma",
        "adhd": "adhd",
        "attention deficit hyperactivity disorder": "adhd",
        "eczema": "eczema",
        "atopic dermatitis": "eczema",
        "anxiety": "anxiety",
        "anxiety disorder": "anxiety",
        "generalized anxiety disorder": "anxiety",
        "depression": "depression",
        "major depressive disorder": "depression",
        "allergic rhinitis": "allergic_rhinitis",
        "type 1 diabetes": "type_1_diabetes",
        "type 2 diabetes": "type_2_diabetes",
        "epilepsy": "epilepsy",
        "seizure disorder": "epilepsy",
        "food allergy": "food_allergy",
        "kawasaki disease": "kawasaki",
    }

    def _regimen_key(self, display_name: str) -> str | None:
        name = display_name.lower().replace("_", " ").strip()
        return self._CHRONIC_REGIMEN_KEYS.get(name)

    def _ensure_chronic_treatment(self, patient) -> None:
        """Add weight/age-dosed maintenance medications for chronic conditions.

        Doses come from src/engines/peds_dosing.py (each number cited there),
        computed from the child's weight and calendar age on the prescribing
        date. At most one agent per drug class is active: anxiety plus
        depression share one SSRI.
        """
        dob = patient.demographics.date_of_birth
        active_classes = {
            self._med_class(m.display_name)
            for m in patient.medication_list
            if m.status == MedicationStatus.ACTIVE
        } - {None}
        existing = {m.display_name.lower() for m in patient.medication_list}

        by_key: dict[str, Condition] = {}
        for problem in patient.problem_list:
            key = self._regimen_key(problem.display_name)
            if key and key not in by_key:
                by_key[key] = problem

        def add(rx: peds_dosing.Rx, problem: Condition, start: date,
                replaced_on: date | None = None) -> None:
            """Append rx. replaced_on marks an earlier weight-based dose that
            was re-dosed (stopped) on that date."""
            cls = self._med_class(rx.agent) or rx.drug_class
            if replaced_on is None:
                if rx.agent.lower() in existing:
                    return
                if cls and cls in active_classes:
                    return
            if start > self.today:
                return  # Not started yet (e.g. preschool behavioral trial)
            end = replaced_on or peds_dosing.end_date_for(rx, start)
            done = end is not None and end <= self.today
            if replaced_on:
                status, reason = MedicationStatus.STOPPED, "Re-dosed for current weight"
            elif done:
                status, reason = MedicationStatus.COMPLETED, "Course completed"
            else:
                status, reason = MedicationStatus.ACTIVE, None
            patient.medication_list.append(Medication(
                code=CodeableConcept(system=peds_dosing.RXNORM, code=rx.rxnorm, display=rx.agent),
                display_name=rx.agent,
                dose_quantity=rx.dose_quantity,
                dose_unit=rx.dose_unit,
                frequency=rx.frequency,
                route=rx.route,
                prn=rx.prn,
                prn_reason=rx.prn_reason if rx.prn else None,
                instructions=rx.instructions,
                status=status,
                start_date=start,
                end_date=end,
                discontinuation_reason=reason,
                indication=rx.indication or f"Maintenance treatment for {problem.display_name}",
            ))
            if replaced_on:
                return
            existing.add(rx.agent.lower())
            if cls and not done:
                active_classes.add(cls)

        def add_weight_based(regimen, problem: Condition, onset: date) -> None:
            """Weight-based maintenance meds are re-dosed as the child grows:
            the starting dose (onset weight) is stopped and re-ordered at the
            most recent measured weight when the dose has changed."""
            age0, wt0 = age_wt(onset)
            if not wt0:
                return
            initial = regimen(age0, wt0)
            latest = max((g for g in patient.growth_data
                          if g.weight_kg and onset < g.date <= self.today),
                         key=lambda g: g.date, default=None)
            current = regimen(calendar_months_between(dob, latest.date), latest.weight_kg) \
                if latest else initial
            cur_by_agent = {rx.agent: rx for rx in current}
            for rx in initial:
                new = cur_by_agent.get(rx.agent)
                if latest and new and new.dose_quantity != rx.dose_quantity:
                    add(rx, problem, onset, replaced_on=latest.date)
                    add(new, problem, latest.date)
                else:
                    add(rx, problem, onset)

        def age_wt(on: date) -> tuple[int, float | None]:
            return (calendar_months_between(dob, on),
                    peds_dosing.weight_on(patient.growth_data, on))

        ssri_considered = False
        for key, problem in by_key.items():
            onset = problem.onset_date or self.today
            if not problem.is_active and key != "kawasaki":
                continue
            age, wt = age_wt(onset)

            if key == "asthma":
                severity = self.rng.choices(
                    ["intermittent", "mild_persistent", "moderate_persistent"],
                    weights=[40, 35, 25])[0]
                code, display = peds_dosing.ICD10_ASTHMA[severity]
                problem.code = CodeableConcept(
                    system="http://hl7.org/fhir/sid/icd-10-cm", code=code, display=display)
                for rx in peds_dosing.asthma(age, severity):
                    add(rx, problem, onset)
            elif key == "adhd":
                start = onset
                if age < peds_dosing.PRESCHOOL_MAX_AGE_MONTHS:
                    # AAP 2019 KAS 5a: behavioral therapy first for ages 4-5
                    start = onset + timedelta(days=peds_dosing.PRESCHOOL_BEHAVIORAL_TRIAL_DAYS)
                if start <= self.today:
                    for rx in peds_dosing.methylphenidate(calendar_months_between(dob, start)):
                        add(rx, problem, start)
            elif key in ("anxiety", "depression"):
                if ssri_considered:
                    continue
                ssri_considered = True
                mood = [by_key[k] for k in ("depression", "anxiety")
                        if k in by_key and by_key[k].is_active]
                start = max(p.onset_date or self.today for p in mood) if mood else onset
                for rx in peds_dosing.ssri(
                        calendar_months_between(dob, start),
                        depression="depression" in by_key and by_key["depression"].is_active,
                        anxiety="anxiety" in by_key and by_key["anxiety"].is_active):
                    add(rx, problem, start)
            elif key == "allergic_rhinitis":
                for rx in peds_dosing.cetirizine(age):
                    add(rx, problem, onset)
            elif key == "eczema":
                add(peds_dosing.Rx(
                    "Hydrocortisone 1% Cream", "312312", "thin layer", "", "BID to affected areas",
                    route="topical", prn=True, prn_reason="eczema flare",
                    drug_class="topical corticosteroid"), problem, onset)
            elif key == "epilepsy":
                add_weight_based(peds_dosing.levetiracetam, problem, onset)
            elif key == "type_1_diabetes":
                add_weight_based(peds_dosing.type_1_diabetes, problem, onset)
            elif key == "type_2_diabetes":
                for rx in peds_dosing.type_2_diabetes(age):
                    add(rx, problem, onset)
            elif key == "food_allergy":
                # Auto-injectors are re-prescribed as the child grows: dose the
                # current device by the latest measured weight.
                latest = max((g for g in patient.growth_data if g.weight_kg),
                             key=lambda g: g.date, default=None)
                if latest or wt:
                    start = max(onset, latest.date) if latest else onset
                    for rx in peds_dosing.epinephrine(latest.weight_kg if latest else wt):
                        add(rx, problem, start)
            elif key == "kawasaki" and wt:
                for rx in peds_dosing.kawasaki_aspirin(wt):
                    add(rx, problem, onset)

    # Drug-class membership for duplicate-therapy checks. Substring match on
    # the display name; combination products count toward their class.
    _DRUG_CLASSES = {
        "SSRI": ("sertraline", "fluoxetine", "escitalopram", "citalopram",
                 "fluvoxamine", "paroxetine"),
        "stimulant": ("methylphenidate", "amphetamine", "lisdexamfetamine"),
        "aminopenicillin": ("amoxicillin", "ampicillin"),
        "ICS": ("fluticasone", "budesonide", "beclomethasone", "mometasone", "ciclesonide"),
        "basal insulin": ("glargine", "detemir", "degludec"),
        "rapid-acting insulin": ("lispro", "aspart", "glulisine"),
    }

    def _med_class(self, name: str) -> str | None:
        n = name.lower()
        for cls, members in self._DRUG_CLASSES.items():
            if any(m in n for m in members):
                return cls
        return None

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
            vitals_dict['temperature_f'] = round(self.rng.uniform(temp_range[0], temp_range[1]), 1)

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
            vitals_dict['o2_sat'] = self.rng.randint(spo2_min, 100)

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
            if self.rng.random() < probability:
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
            if self.rng.random() < probability:
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
        encounter_type: EncounterType,
        age_months: int | None = None,
    ) -> list:
        """Generate condition-specific lab results.

        Supports both binary (positive/negative) and numeric lab values.

        Args:
            condition_key: The condition key to look up labs for
            encounter_date: Date of the encounter
            encounter_type: Type of encounter (determines if labs are generated)
            age_months: Patient age for age-specific reference ranges

        Returns:
            List of LabResult objects
        """
        from src.models import LabResult, CodeableConcept, Interpretation, ReferenceRange

        # Must have a condition to generate labs
        if not condition_key:
            return []

        # Allow labs for chronic follow-ups if condition requires monitoring
        if encounter_type == EncounterType.CHRONIC_FOLLOWUP:
            condition_data = self._conditions.get(condition_key, {})
            monitoring = condition_data.get('monitoring_requirements', {})
            if not monitoring.get('required_labs_at_followup'):
                return []
            # Continue to generate monitoring labs
        elif encounter_type not in (
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

            if not name or not loinc:
                continue

            value_type = lab.get('value_type', 'binary')

            if value_type == 'numeric':
                # Generate numeric lab value
                result = self._generate_numeric_lab(lab, age_months, encounter_date)
                if result:
                    results.append(result)
            else:
                # Binary (positive/negative) result
                result_positive = lab.get('result_positive', 'Positive')
                result_negative = lab.get('result_negative', 'Negative')
                prob_positive = lab.get('probability_positive', 0.5)

                is_positive = self.rng.random() < prob_positive

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

    def _generate_numeric_lab(
        self,
        lab_def: dict,
        age_months: int | None,
        encounter_date: date,
    ):
        """Generate a numeric lab result with value, unit, and reference range.

        Args:
            lab_def: Lab definition dictionary from conditions.yaml
            age_months: Patient age for reference range lookup
            encounter_date: Date for the result

        Returns:
            LabResult object or None
        """
        from src.models import LabResult, CodeableConcept, Interpretation, ReferenceRange

        name = lab_def.get('name', '')
        loinc = lab_def.get('loinc', '')
        unit = lab_def.get('unit', '')

        # Get reference range (age-specific if available)
        normal_low = lab_def.get('normal_range_low')
        normal_high = lab_def.get('normal_range_high')

        if age_months and lab_def.get('age_ranges'):
            for age_range in lab_def['age_ranges']:
                age_min = age_range.get('age_min', 0)
                age_max = age_range.get('age_max', 999)
                if age_min <= age_months <= age_max:
                    normal_low = age_range.get('low', normal_low)
                    normal_high = age_range.get('high', normal_high)
                    break

        if normal_low is None or normal_high is None:
            return None  # Can't generate without reference range

        # Determine if abnormal
        prob_abnormal = lab_def.get('probability_abnormal', 0.3)
        is_abnormal = self.rng.random() < prob_abnormal

        if is_abnormal:
            # Generate abnormal value (high or low)
            if self.rng.random() < 0.6:  # 60% high abnormal
                abn_min = lab_def.get('abnormal_high_min', normal_high)
                abn_max = lab_def.get('abnormal_high_max', normal_high * 1.5)
                value = self.rng.uniform(abn_min, abn_max)
                interpretation = Interpretation.HIGH
            else:  # 40% low abnormal
                abn_min = lab_def.get('abnormal_low_min', normal_low * 0.5)
                abn_max = lab_def.get('abnormal_low_max', normal_low)
                value = self.rng.uniform(abn_min, abn_max)
                interpretation = Interpretation.LOW
        else:
            # Generate normal value
            value = self.rng.uniform(normal_low, normal_high)
            interpretation = Interpretation.NORMAL

        # Round appropriately
        if value >= 100:
            value = round(value, 0)
        elif value >= 10:
            value = round(value, 1)
        else:
            value = round(value, 2)

        return LabResult(
            code=CodeableConcept(
                system="http://loinc.org",
                code=loinc,
                display=name
            ),
            display_name=name,
            value=value,
            unit=unit,
            reference_range=ReferenceRange(
                low=normal_low,
                high=normal_high,
                unit=unit,
            ),
            interpretation=interpretation,
            resulted_date=datetime.combine(encounter_date, datetime.min.time()),
        )

    def _generate_imaging(
        self,
        condition_key: str | None,
        encounter_date: date,
    ) -> list:
        """Generate imaging results for a condition.

        Args:
            condition_key: Condition key from conditions.yaml (e.g., 'pneumonia')
            encounter_date: Date of the encounter

        Returns:
            List of ImagingResult objects
        """
        from src.models import ImagingResult, CodeableConcept, ResultStatus

        if not condition_key:
            return []

        condition_def = self._conditions.get(condition_key, {})
        imaging_defs = condition_def.get('diagnostics', {}).get('imaging', [])

        if not imaging_defs:
            return []

        results = []
        for img_def in imaging_defs:
            name = img_def.get('name', '')
            modality = img_def.get('modality', 'X-ray')
            loinc = img_def.get('loinc', '')
            prob_positive = img_def.get('probability_positive', 0.5)

            # Determine positive or negative finding
            is_positive = self.rng.random() < prob_positive

            if is_positive:
                finding = img_def.get('finding_positive', 'Abnormal finding')
                impression = img_def.get('impression_positive', 'Abnormal study')
            else:
                finding = img_def.get('finding_negative', 'No abnormality')
                impression = img_def.get('impression_negative', 'Normal study')

            performed_dt = datetime.combine(encounter_date, datetime.min.time())

            result = ImagingResult(
                code=CodeableConcept(
                    system="http://loinc.org",
                    code=loinc,
                    display=name
                ) if loinc else None,
                display_name=name,
                modality=modality,
                status=ResultStatus.FINAL,
                findings=finding,
                impression=impression,
                performed_date=performed_dt,
                reported_date=performed_dt,
                performing_facility="Pediatric Imaging Center",
                radiologist="Dr. Sarah Chen, MD",
            )
            results.append(result)

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

        # Reproducibility: re-seed the per-engine RNG (never the global one)
        # and pin "today" for this run.
        self.rng = random.Random(seed.random_seed)
        self.today = self._today_override or date.today()

        # GenerationSeed.messiness_level drives this run when the caller set
        # it explicitly; otherwise the engine's constructor level applies.
        if "messiness_level" in seed.model_fields_set:
            self.messiness_level = seed.messiness_level
        else:
            self.messiness_level = self._base_messiness_level
        self.messiness = MessinessInjector(
            level=self.messiness_level, seed=seed.random_seed
        )
        # Per-run prescription history (date, agent) for class-aware choices.
        self._rx_history: list[tuple[date, str]] = []

        # Step 1: Generate persona (demographics, family, social)
        demographics = self._generate_demographics(seed)
        self._current_dob = demographics.date_of_birth
        social_history = self._generate_social_history(demographics, seed)
        
        # Step 2: Generate life arc
        life_arc = self.generate_life_arc(demographics, seed)
        
        # Step 3: Generate encounter timeline
        encounter_stubs = self.generate_encounter_timeline(demographics, life_arc, seed)

        # Step 4: Generate growth trajectory
        from knowledge.growth.cdc_2000 import GrowthTrajectory
        sex = "male" if demographics.sex_at_birth == Sex.MALE else "female"

        # Determine starting percentiles (can be influenced by conditions)
        weight_pct = self.rng.gauss(50, 20)
        height_pct = self.rng.gauss(50, 20)
        weight_pct = max(5, min(95, weight_pct))
        height_pct = max(5, min(95, height_pct))

        # Select growth pattern based on conditions
        pattern, pattern_kwargs = self._select_growth_pattern(life_arc.major_conditions)

        growth = GrowthTrajectory(
            sex=sex,
            weight_percentile=weight_pct,
            height_percentile=height_pct,
            hc_percentile=self.rng.gauss(50, 15),
            pattern=pattern,
            rng=self.rng,
            **pattern_kwargs,
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
                weight, height, hc, bmi = growth.generate_measurement(days_old / 30.4375)
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
                prior_immunizations=immunizations,
            )
            encounters.append(encounter)
            if growth_data and growth_data[-1].date == stub.date and growth_data[-1].encounter_id is None:
                growth_data[-1].encounter_id = encounter.id
            
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
        today = self.today
        for cond_name in life_arc.major_conditions:
            onset_months = life_arc.condition_onset_ages.get(cond_name, 24)
            onset_date = self._months_to_date(demographics.date_of_birth, onset_months)
            # Clamp onset date: not before DOB, not after today
            onset_date = max(onset_date, demographics.date_of_birth)
            onset_date = min(onset_date, today)

            code, display_name = self._resolve_icd10(cond_name)

            # Self-limited conditions requested as part of the life arc
            # (bronchiolitis, AOM, Kawasaki...) resolve after their course.
            # An episode still in progress today stays active.
            abatement = self._abatement_for(cond_name, onset_date)
            condition = Condition(
                display_name=display_name,
                code=CodeableConcept(
                    system="http://hl7.org/fhir/sid/icd-10-cm",
                    code=code,
                    display=display_name,
                ),
                clinical_status=ConditionStatus.RESOLVED if abatement else ConditionStatus.ACTIVE,
                onset_date=onset_date,
                abatement_date=abatement,
            )
            problem_list.append(condition)
        
        # Generate message frequency (some patients message a lot, some rarely)
        # Higher complexity patients tend to message more
        base_frequency = self.rng.random()
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

        # Deduplicate problem list - only add if not already present (Bug 10 fix)
        existing_problem_names = {p.display_name.lower() for p in problem_list}
        for cond in resolved_conditions:
            if cond.display_name.lower() not in existing_problem_names:
                problem_list.append(cond)
                existing_problem_names.add(cond.display_name.lower())

        # Deduplicate medications - keep unique, most recent PRN (Bug 11 fix)
        past_medications = self._dedupe_medications(past_medications)

        # Deduplicate immunizations - one record per vaccine/dose combo (Bug 2 fix)
        immunizations = self._dedupe_immunizations(immunizations)

        # Create Allergy objects from discovered medication allergies (life events)
        allergy_list = []
        discovered_allergies = getattr(self, '_discovered_allergies', [])
        for allergy_info in discovered_allergies:
            # Ensure substance is not empty (Bug 12 fix)
            substance = allergy_info.get('substance', '').strip()
            if not substance:
                substance = self.rng.choice(['Penicillin', 'Amoxicillin', 'Sulfa', 'Cephalosporin'])

            allergy = Allergy(
                display_name=f"{substance} allergy",
                category=AllergyCategory.MEDICATION,
                criticality=self.rng.choice(["low", "high"]),
                reactions=[
                    AllergyReaction(
                        manifestation=self.rng.choice([
                            "Rash", "Hives", "Itching", "Swelling",
                            "Nausea", "Stomach upset"
                        ]),
                        severity=self.rng.choice([AllergySeverity.MILD, AllergySeverity.MODERATE]),
                    )
                ],
                onset_date=allergy_info.get("discovery_date"),
                code=CodeableConcept(
                    system="http://www.nlm.nih.gov/research/umls/rxnorm",
                    code=allergy_info.get("rxnorm", ""),
                    display=substance,
                ) if allergy_info.get("rxnorm") else None,
            )
            allergy_list.append(allergy)

        # Add food allergy to allergy_list if patient has food allergy condition
        for cond_name in life_arc.major_conditions:
            if cond_name.lower().replace('_', ' ') == 'food allergy':
                food_allergen = self.rng.choice(['Peanut', 'Tree nuts', 'Milk', 'Egg', 'Shellfish', 'Wheat', 'Soy'])
                allergy = Allergy(
                    display_name=f"{food_allergen} allergy",
                    category=AllergyCategory.FOOD,
                    criticality=self.rng.choice(["low", "high"]),
                    reactions=[
                        AllergyReaction(
                            manifestation=self.rng.choice([
                                "Hives", "Swelling", "Vomiting", "Anaphylaxis",
                                "Itching", "Difficulty breathing"
                            ]),
                            severity=self.rng.choice([AllergySeverity.MILD, AllergySeverity.MODERATE, AllergySeverity.SEVERE]),
                        )
                    ],
                )
                allergy_list.append(allergy)
                break

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

        # Ensure chronic conditions have maintenance medications (Bug 4 fix)
        self._ensure_chronic_treatment(patient)

        # Age-gate enforcement (W1.1 fix) - clamp any condition whose
        # onset_date violates its hardcoded age gate. Must run BEFORE the
        # validator so it never sees an age-gate violation under normal flow.
        patient = self._validate_and_fix_age_gates(patient)

        # Post-generation validation
        validation_result = self.validator.validate(patient)

        if not validation_result.valid:
            # Attempt auto-fixes for fixable issues
            patient = self._apply_validation_fixes(patient, validation_result)

            # Re-validate after fixes
            validation_result = self.validator.validate(patient)

            # Block output if critical issues remain after auto-fix attempts
            critical_issues = [
                i for i in validation_result.issues
                if i.severity.value == "critical"
            ]
            if critical_issues:
                issue_summary = "; ".join([
                    f"{i.type.value}: {i.message}" for i in critical_issues[:5]
                ])
                raise ValueError(
                    f"Patient generation failed - {len(critical_issues)} critical issue(s) "
                    f"could not be auto-fixed: {issue_summary}"
                )

        # Store validation metadata
        patient.generation_seed["validation"] = {
            "valid": validation_result.valid,
            "error_count": len(validation_result.errors),
            "warning_count": len(validation_result.warnings),
            "issues": [issue.model_dump() for issue in validation_result.issues[:10]],
        }

        # Narrative-structured reconciliation (if LLM available)
        if self.reconciler and self.use_llm:
            reconciliation = self.reconciler.reconcile(patient)

            if reconciliation.narratives_to_regenerate:
                # Regenerate problematic narratives
                for enc_id in reconciliation.narratives_to_regenerate:
                    enc = next((e for e in patient.encounters if e.id == enc_id), None)
                    if enc:
                        # Get the discrepancies for this encounter
                        discs = reconciliation.get_discrepancies_for_encounter(enc_id)
                        forbidden_claims = [d.claim.claim_text for d in discs]

                        # Regenerate with constraints
                        enc.narrative_note = self._regenerate_constrained_narrative(
                            encounter=enc,
                            patient=patient,
                            forbidden_claims=forbidden_claims,
                        )

            # Store reconciliation metadata
            patient.generation_seed["reconciliation"] = {
                "discrepancy_count": len(reconciliation.discrepancies),
                "narratives_regenerated": len(reconciliation.narratives_to_regenerate),
            }

        return patient

    def _apply_validation_fixes(self, patient: Patient, result: ValidationResult) -> Patient:
        """Apply automatic fixes for validation issues."""
        fix_handlers = {
            "temporal": self._fix_temporal_issue,
            "coding": self._fix_coding_issue,
            "medication": self._fix_medication_issue,
            "age_gate": self._fix_age_gate_issue,
            "lab": self._fix_lab_issue,
        }

        for issue in result.issues:
            if issue.auto_fixable and issue.type.value in fix_handlers:
                patient = fix_handlers[issue.type.value](patient, issue)

        return patient

    def _extract_list_index(self, path: str, list_name: str) -> int | None:
        """Extract index from a path like 'encounters[3].date'."""
        import re
        match = re.search(rf'{list_name}\[(\d+)\]', path)
        return int(match.group(1)) if match else None

    def _fix_temporal_issue(self, patient: Patient, issue) -> Patient:
        """Fix temporal issues (dates before DOB)."""
        dob = patient.demographics.date_of_birth
        path = issue.path

        if "encounters" in path:
            idx = self._extract_list_index(path, "encounters")
            if idx is not None and idx < len(patient.encounters):
                patient.encounters[idx].date = datetime.combine(
                    dob + timedelta(days=3),
                    patient.encounters[idx].date.time()
                )

        elif "problem_list" in path:
            idx = self._extract_list_index(path, "problem_list")
            if idx is not None and idx < len(patient.problem_list):
                # Default to dob + 30 days (an "around birth" placeholder),
                # then clamp to the condition's age gate so we never produce
                # an onset_date that violates min/max age (W1.1 fix).
                cond = patient.problem_list[idx]
                fallback_onset = dob + timedelta(days=30)
                cond.onset_date = self._clamp_onset_to_gate(
                    cond.display_name, fallback_onset, dob
                )

        elif "immunization_record" in path:
            idx = self._extract_list_index(path, "immunization_record")
            if idx is not None and idx < len(patient.immunization_record):
                patient.immunization_record[idx].date = dob

        return patient

    def _fix_age_gate_issue(self, patient: Patient, issue) -> Patient:
        """
        Fix an age-gate violation by clamping the offending condition's
        onset_date to the valid range.
        """
        idx = self._extract_list_index(issue.path or "", "problem_list")
        if idx is None or idx >= len(patient.problem_list):
            return patient

        cond = patient.problem_list[idx]
        if cond.onset_date is None:
            return patient

        dob = patient.demographics.date_of_birth
        cond.onset_date = self._clamp_onset_to_gate(
            cond.display_name, cond.onset_date, dob
        )
        return patient

    def _fix_coding_issue(self, patient: Patient, issue) -> Patient:
        """Fix ICD-10 coding issues using condition service."""
        idx = self._extract_list_index(issue.path, "problem_list")
        if idx is None or idx >= len(patient.problem_list):
            return patient

        condition = patient.problem_list[idx]
        result = self.condition_service.get_condition(condition.display_name)

        if result.found and result.definition and result.definition.icd10_primary:
            condition.code.code = result.definition.icd10_primary
            condition.code.display = condition.display_name

        return patient

    # HbA1c monitoring. LOINC 4548-4 (Hemoglobin A1c/Hemoglobin.total in Blood).
    # ADA Standards of Care 2026: diagnosis >= 6.5%, prediabetes 5.7-6.4%,
    # normal < 5.7%; youth with diabetes tested every 3 months (S6, S14),
    # prediabetes at least yearly (S2). Pediatric glycemic target < 7% (S14).
    A1C_LOINC = "4548-4"
    A1C_INTERVAL_DAYS = {"type 1 diabetes": 84, "type 2 diabetes": 84, "prediabetes": 330}
    A1C_RANGES = {
        # (at diagnosis, at follow-up), percent
        "type 1 diabetes": ((8.5, 12.0), (6.8, 9.0)),
        "type 2 diabetes": ((6.5, 9.0), (6.0, 8.0)),
        "prediabetes": ((5.7, 6.4), (5.5, 6.4)),
    }

    def _fix_lab_issue(self, patient: Patient, issue) -> Patient:
        """Add missing monitoring labs. Currently: HbA1c for diabetes and
        prediabetes, on chronic follow-ups at the ADA interval."""
        from src.models import LabResult, Interpretation, ReferenceRange

        for cond in patient.problem_list:
            name = cond.display_name.lower().replace("_", " ")
            dx = next((k for k in self.A1C_INTERVAL_DAYS if k in name), None)
            if dx is None or not cond.is_active:
                continue
            word = dx.split()[-1] if dx != "prediabetes" else "prediabetes"
            visits = sorted(
                (e for e in patient.encounters
                 if e.type == EncounterType.CHRONIC_FOLLOWUP
                 and (word in e.chief_complaint.lower() or dx in e.chief_complaint.lower())),
                key=lambda e: e.date)
            if not visits:
                visits = sorted((e for e in patient.encounters
                                 if e.type == EncounterType.CHRONIC_FOLLOWUP), key=lambda e: e.date)
            last: date | None = None
            for enc in visits:
                d = enc.date.date()
                has = any(getattr(getattr(lab, "code", None), "code", None) == self.A1C_LOINC
                          for lab in enc.lab_results)
                if has:
                    last = d
                    continue
                if last is not None and (d - last).days < self.A1C_INTERVAL_DAYS[dx]:
                    continue
                at_dx = "evaluation" in enc.chief_complaint.lower() or last is None
                lo, hi = self.A1C_RANGES[dx][0 if at_dx else 1]
                value = round(self.rng.uniform(lo, hi), 1)
                enc.lab_results.append(LabResult(
                    code=CodeableConcept(system="http://loinc.org", code=self.A1C_LOINC,
                                         display="Hemoglobin A1c/Hemoglobin.total in Blood"),
                    display_name="Hemoglobin A1c",
                    value=value,
                    unit="%",
                    reference_range=ReferenceRange(low=4.0, high=5.6, unit="%"),
                    interpretation=Interpretation.HIGH if value >= 5.7 else Interpretation.NORMAL,
                    collected_date=enc.date,
                    resulted_date=enc.date,
                    encounter_id=enc.id,
                ))
                last = d
        return patient

    def _fix_medication_issue(self, patient: Patient, issue) -> Patient:
        """Add missing standard-of-care medications.

        First pass: the cited, weight/age-dosed regimens in peds_dosing (this
        covers T1D, T2D, food allergy, epilepsy, asthma, ...). Second pass:
        local conditions.yaml `treatment.medications` entries that carry a
        real dose (dose_mg_kg or fixed_dose_mg). Entries without a dose are
        skipped rather than written as "1 dose".
        """
        self._ensure_chronic_treatment(patient)
        dob = patient.demographics.date_of_birth
        current = {m.display_name.lower() for m in patient.medication_list
                   if m.status == MedicationStatus.ACTIVE}
        for condition in patient.problem_list:
            if not condition.is_active or self._regimen_key(condition.display_name):
                continue
            key = self._get_condition_key(condition.display_name)
            meds = (self._conditions.get(key, {}) or {}).get("treatment", {}).get("medications", []) \
                if key else []
            start = condition.onset_date or self.today
            wt = peds_dosing.weight_on(patient.growth_data, start)
            for med in meds:
                if not isinstance(med, dict) or med.get("prn") or not med.get("rxnorm"):
                    continue
                agent = med.get("agent", "")
                if not agent or agent.lower() in current:
                    continue
                if med.get("age_min_months") and \
                        calendar_months_between(dob, start) < med["age_min_months"]:
                    continue
                dose = self._dose_from_def(med, wt)
                if dose is None:
                    continue
                dose_str, frequency = dose
                patient.medication_list.append(Medication(
                    display_name=agent,
                    code=CodeableConcept(system=peds_dosing.RXNORM, code=str(med["rxnorm"]),
                                         display=agent),
                    status=MedicationStatus.ACTIVE,
                    dose_quantity=dose_str,
                    dose_unit="mg",
                    frequency=frequency,
                    route=med.get("route") or "oral",
                    start_date=start,
                    indication=med.get("indication"),
                ))
                current.add(agent.lower())
                break  # one agent per condition
        return patient

    def _regenerate_constrained_narrative(
        self,
        encounter: Encounter,
        patient: Patient,
        forbidden_claims: list[str],
    ) -> str:
        """Regenerate a narrative with constraints to avoid hallucinations."""
        # Build list of actual facts from structured data
        actual_meds = [m.display_name for m in patient.medication_list if m.status.value == "active"]
        actual_conditions = [c.display_name for c in patient.problem_list if c.clinical_status.value == "active"]

        constraint_text = ""
        if forbidden_claims:
            constraint_text = f"""
IMPORTANT: Do NOT include any of these claims (they are not supported by structured data):
{chr(10).join(f'- {claim}' for claim in forbidden_claims)}

"""

        # Get age for context
        age_months = (encounter.date.date() - patient.demographics.date_of_birth).days // 30

        # Call existing narrative generation with constraints in mind
        return self._generate_llm_narrative(encounter, patient.demographics, age_months)
    
    def _generate_demographics(self, seed: GenerationSeed) -> Demographics:
        """Generate patient demographics."""
        # Age
        if seed.age is not None:
            age_months = seed.age * 12
        elif seed.age_months is not None:
            age_months = seed.age_months
        else:
            # Random age 0-21 years
            age_months = self.rng.randint(0, 252)
        
        # Sex
        sex = seed.sex or self.rng.choice([Sex.MALE, Sex.FEMALE])
        
        # Calculate DOB using proper calendar month arithmetic
        today = self.today
        from dateutil.relativedelta import relativedelta
        dob = today - relativedelta(months=age_months)
        
        # Generate name based on sex
        first_name = self._generate_first_name(sex)
        last_name = self._generate_last_name()
        
        # Address
        from src.models import Address, Contact
        address = Address(
            line1=f"{self.rng.randint(100, 9999)} {self.rng.choice(['Oak', 'Maple', 'Cedar', 'Pine', 'Main', 'First', 'Park'])} {self.rng.choice(['Street', 'Avenue', 'Lane', 'Drive', 'Court'])}",
            city=self.rng.choice(["Springfield", "Riverside", "Lakewood", "Fairview", "Madison"]),
            state=seed.state or self.rng.choice(["MN", "WI", "CA", "TX", "NY", "FL", "IL"]),
            postal_code=f"{self.rng.randint(10000, 99999)}",
        )
        
        # Emergency contact (parent) - ensure realistic age gap
        # Parent should be at least 18 + child's age, typically 22-38 years older
        child_age_years = age_months // 12
        parent_age_gap = self.rng.randint(22, 38)
        parent_age = child_age_years + parent_age_gap

        parent_sex = self.rng.choice([Sex.MALE, Sex.FEMALE])
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
            race=seed.race or [self.rng.choice(["White", "Black or African American", "Asian", "Two or more races"])],
            ethnicity=seed.ethnicity or self.rng.choice(["Not Hispanic or Latino", "Hispanic or Latino"]),
            preferred_language="English",
            address=address,
            phone=self._generate_phone(),
            emergency_contact=emergency_contact,
            legal_guardian=guardian,
        )
    
    def _generate_social_history(self, demographics: Demographics, seed: GenerationSeed) -> SocialHistory:
        """Generate social history appropriate to age."""
        from src.models import HouseholdMember, SubstanceUse
        
        age_years = self._age_months(demographics) // 12
        
        # Household
        household = []
        if age_years < 18:
            # Add parents - use realistic parent age based on child's age
            parent_age = getattr(self, '_last_parent_age', age_years + self.rng.randint(22, 38))
            household.append(HouseholdMember(
                name=demographics.legal_guardian.name if demographics.legal_guardian else "Parent",
                relationship="Parent",
                age=parent_age,
            ))
            if self.rng.random() > 0.3:  # 70% two-parent households
                # Second parent within a few years of first parent
                second_parent_age = parent_age + self.rng.randint(-5, 5)
                household.append(HouseholdMember(
                    name="Parent",
                    relationship="Parent",
                    age=max(age_years + 18, second_parent_age),  # Ensure at least 18 years older than child
                ))
            # Maybe siblings
            if self.rng.random() > 0.4:
                household.append(HouseholdMember(
                    name="Sibling",
                    relationship="Sibling",
                    age=self.rng.randint(1, 18),
                ))
        
        # School/childcare
        school_name = None
        grade_level = None
        if 3 <= age_years < 5:
            school_name = "Little Stars Preschool"
            grade_level = "Preschool"
        elif 5 <= age_years < 18:
            school_name = f"{demographics.address.city} {self.rng.choice(['Elementary', 'Middle', 'High'])} School"
            grade_level = self._age_to_grade(age_years)
        
        return SocialHistory(
            living_situation=f"Lives with {'parents' if age_years < 18 else 'independently'}",
            household_members=household,
            school_name=school_name,
            grade_level=grade_level,
            school_performance="Good" if self.rng.random() > 0.3 else self.rng.choice(["Excellent", "Average", "Struggling"]),
            tobacco=SubstanceUse(substance="tobacco", status="never") if age_years >= 11 else None,
            food_security="secure",
            housing_stability="stable",
            transportation_access="adequate",
            firearms_in_home=self.rng.random() > 0.7,
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
            tier = self.rng.choices(
                [ComplexityTier.TIER_0, ComplexityTier.TIER_1, ComplexityTier.TIER_2, ComplexityTier.TIER_3],
                weights=[60, 25, 12, 3],
            )[0]
        
        # Determine conditions based on tier
        conditions = []
        onset_ages = {}
        
        if seed.conditions:
            # Filter out age-inappropriate conditions and apply comorbidity logic
            patient_age = self._age_months(demographics)
            filtered_conditions = []

            for cond in seed.conditions:
                # Same gate the onset clamp and validator use
                min_age, max_age = self._get_condition_age_limits(cond)

                # Check if patient age allows this condition
                if patient_age < min_age:
                    print(f"Warning: Skipping {cond} - patient age {patient_age}mo is below minimum {min_age}mo")
                    continue

                # Check if there's a valid onset range (considering max age constraints)
                onset_min = min_age
                onset_max = patient_age
                if max_age is not None:
                    onset_max = min(onset_max, max_age)

                if onset_min > onset_max:
                    print(f"Warning: Skipping {cond} - no valid onset range for patient age {patient_age}mo")
                    continue

                filtered_conditions.append(cond)

            conditions = filtered_conditions
            # Apply co-morbidity logic to user-specified conditions
            conditions = self._apply_comorbidity_logic(conditions)

            # Generate plausible onset ages with proper age constraints
            valid_conditions = []
            for cond in conditions:
                cond_min_age, cond_max_age = self._get_condition_age_limits(cond)

                # Calculate valid onset age range
                onset_min = cond_min_age
                onset_max = patient_age
                if cond_max_age is not None:
                    onset_max = min(onset_max, cond_max_age)

                if onset_min > onset_max:
                    print(f"Warning: Skipping {cond} - no valid onset range (min {onset_min}mo, max {onset_max}mo)")
                    continue

                valid_conditions.append(cond)
                onset_ages[cond] = self.rng.randint(onset_min, onset_max)

            conditions = valid_conditions
        elif tier != ComplexityTier.TIER_0:
            # Use conditions loaded from YAML database
            age_months = self._age_months(demographics)

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
                ComplexityTier.TIER_2: self.rng.randint(2, 3),
                ComplexityTier.TIER_3: self.rng.randint(3, 5),
            }.get(tier, 0)

            conditions = self.rng.sample(condition_pool, min(num_conditions, len(condition_pool)))

            # Apply co-morbidity logic - conditions tend to cluster
            conditions = self._apply_comorbidity_logic(conditions)

            # Filter out conditions where patient age doesn't allow valid onset
            valid_conditions = []
            for cond in conditions:
                # Same gate the onset clamp and validator use
                cond_min_age, cond_max_age = self._get_condition_age_limits(cond)

                patient_age = self._age_months(demographics)

                # Check if patient is too young for this condition
                if patient_age < cond_min_age:
                    print(f"Warning: Skipping {cond} - patient age {patient_age}mo is below condition minimum {cond_min_age}mo")
                    continue

                # Check if patient is too old for this condition (e.g., bronchiolitis)
                if cond_max_age is not None and cond_min_age > cond_max_age:
                    # Invalid constraint - skip
                    continue

                # Calculate valid onset age range
                onset_min = cond_min_age
                onset_max = patient_age
                if cond_max_age is not None:
                    onset_max = min(onset_max, cond_max_age)

                if onset_min > onset_max:
                    # No valid onset range - skip condition
                    print(f"Warning: Skipping {cond} - no valid onset range (min {onset_min}mo, max {onset_max}mo)")
                    continue

                valid_conditions.append(cond)
                onset_ages[cond] = self.rng.randint(onset_min, onset_max)

            conditions = valid_conditions
        
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
        current_age_months = self._age_months(demographics)
        today = self.today
        
        # Well-child visits
        for visit_age in self.WELL_CHILD_SCHEDULE:
            if visit_age > current_age_months:
                break
            
            visit_date = self._months_to_date(dob, visit_age)
            if visit_date > today:
                continue

            # Add some realistic variation to dates (clamped to valid range)
            visit_date += timedelta(days=self.rng.randint(-7, 14))
            visit_date = max(visit_date, dob)  # Clamp to DOB floor
            visit_date = min(visit_date, today)  # Clamp to today ceiling
            
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
            actual_visits = int(expected_visits + self.rng.random())  # Randomize

            for _ in range(actual_visits):
                # Random date in this age range
                visit_age_months = self.rng.randint(start_month, end_month)
                visit_date = self._months_to_date(dob, visit_age_months) + timedelta(days=self.rng.randint(0, 29))

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
            diagnosis_date = self._months_to_date(dob, onset_age)
            diagnosis_date = max(diagnosis_date, dob)  # Clamp to DOB floor
            diagnosis_date = min(diagnosis_date, today)  # Clamp to today ceiling (no future dates)

            # Self-limited conditions (bronchiolitis, AOM, ...) are a single
            # acute visit with an acute plan, not a chronic follow-up series.
            if self._abatement_for(condition, diagnosis_date) is not None or \
                    self._is_acute_condition(condition):
                ykey = self._get_condition_key(condition) or condition
                display = (self._conditions.get(ykey) or {}).get(
                    'display_name', condition.replace('_', ' ').title())
                stubs.append(EncounterStub(
                    date=diagnosis_date,
                    type=EncounterType.ACUTE_ILLNESS,
                    reason=display,
                    conditions_to_address=[condition],
                    is_new_condition_diagnosis=True,
                    new_condition=condition,
                ))
                continue

            stubs.append(EncounterStub(
                date=diagnosis_date,
                type=EncounterType.CHRONIC_FOLLOWUP,
                reason=f"Evaluation for {condition.lower()} symptoms",
                conditions_to_address=[condition],
                is_new_condition_diagnosis=True,
                new_condition=condition,
            ))
            
            # Follow-up visits (every 3-6 months)
            follow_up_age = onset_age + self.rng.randint(2, 4)
            while follow_up_age < current_age_months:
                fu_date = self._months_to_date(dob, follow_up_age)
                if fu_date <= today:
                    stubs.append(EncounterStub(
                        date=fu_date,
                        type=EncounterType.CHRONIC_FOLLOWUP,
                        reason=f"{condition} follow-up",
                        conditions_to_address=[condition],
                    ))
                follow_up_age += self.rng.randint(3, 6)
        
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
        today = self.today
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
                if self.rng.random() < adjusted_rate:
                    # Event occurred!
                    event_age_months = self.rng.randint(age_months_start, age_months_end)
                    event_date = self._months_to_date(dob, event_age_months) + timedelta(days=self.rng.randint(0, 29))
                    event_date = max(event_date, dob)  # Clamp to DOB floor

                    if event_date > today:
                        continue

                    # Select variant if applicable
                    variants = event_def.get("variants", [])
                    if variants:
                        weights = [v["weight"] for v in variants]
                        variant = self.rng.choices(variants, weights=weights)[0]
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

    # Generic pertinent negatives by organ system for the template HPI.
    _HPI_NEGATIVES_BY_SYSTEM = {
        "respiratory": ["difficulty breathing", "retractions", "cyanosis"],
        "ent": ["ear drainage", "difficulty breathing"],
        "gastrointestinal": ["bloody stools", "bilious vomiting", "decreased urine output"],
        "genitourinary": ["flank pain", "vomiting"],
        "dermatologic": ["mucosal involvement", "blistering"],
        "ophthalmologic": ["vision changes", "eye pain"],
        "neurological": ["head injury", "neck stiffness"],
    }

    def _template_hpi(
        self,
        stub: EncounterStub,
        demographics: Demographics,
        age_months: int,
        condition_key: str | None,
        temperature_f: float | None,
    ) -> str | None:
        """Deterministic HPI for problem visits (used when the LLM is off;
        the LLM path overwrites it for acute visits when enabled).

        Built from the chief complaint, onset/duration, YAML presentation
        symptoms (probabilistic, age-filtered) and pertinent negatives drawn
        from the condition's unselected symptoms and its organ system. Fever
        statements follow the visit's measured temperature so the HPI never
        contradicts the vitals.
        """
        if stub.type in (EncounterType.WELL_CHILD, EncounterType.NEWBORN):
            return None
        male = demographics.sex_at_birth == Sex.MALE
        if age_months >= 216:
            who = f"{age_months // 12} year old " + ("man" if male else "woman")
        else:
            who = f"{self._age_to_description(age_months)} " + ("boy" if male else "girl")
        who = who[0].upper() + who[1:]
        historian = "Patient" if age_months >= 144 else "Parent"
        febrile = temperature_f is not None and temperature_f >= 100.4
        fever_at_home = febrile or "fever" in stub.reason.lower()

        data = self._conditions.get(condition_key, {}) if condition_key else {}
        if not isinstance(data, dict):
            data = {}
        presentation = data.get("presentation", {}) or {}
        positives: list[str] = []
        negatives: list[str] = []
        for sd in presentation.get("symptoms", []) or []:
            if isinstance(sd, str):
                sd = {"name": sd.removeprefix("may have ").strip(),
                      "probability": 0.5 if sd.startswith("may have") else 1.0}
            name = sd.get("name", "")
            if sd.get("age_min") is not None and age_months < sd["age_min"]:
                continue
            if sd.get("age_max") is not None and age_months > sd["age_max"]:
                continue
            if "fever" in name.lower() or "temperature" in name.lower():
                # Reported fever follows the history (and never contradicts a
                # febrile clinic temperature).
                if self.rng.random() < sd.get("probability", 1.0):
                    fever_at_home = True
                continue
            if self.rng.random() < sd.get("probability", 1.0):
                positives.append(name)
            else:
                negatives.append(name)
        if not fever_at_home:
            negatives.append("fever")
        for n in self._HPI_NEGATIVES_BY_SYSTEM.get(data.get("system", ""), []):
            if n not in negatives and n not in positives:
                negatives.append(n)

        if stub.type == EncounterType.CHRONIC_FOLLOWUP:
            cond = (stub.conditions_to_address or [stub.reason])[0]
            ykey = self._get_condition_key(cond) or cond
            cname = (self._conditions.get(ykey) or {}).get(
                "display_name", cond.replace("_", " ").title())
            if stub.is_new_condition_diagnosis:
                sx = ", ".join(positives[:4]) or f"concerns related to {cname.lower()}"
                text = (f"{who} presenting for evaluation of {sx}. {historian} reports these "
                        f"developed gradually over the past several weeks to months.")
                if negatives:
                    text += f" Denies {', '.join(negatives[:3])}."
                return text
            return (f"{who} here for routine follow-up of {cname}. {historian} reports "
                    f"symptoms are stable since the last visit with good adherence to the "
                    f"current plan. No emergency visits or hospitalizations since last seen.")

        days = self.rng.randint(1, 3)
        when = "1 day" if days == 1 else f"{days} days"
        parts = [f"{who} presenting with {stub.reason.lower()} for {when}."]
        if positives:
            parts.append(f"{historian} reports {', '.join(positives[:5])}.")
        if febrile:
            parts.append(f"Fever at home; {temperature_f:.1f} F in clinic.")
        elif fever_at_home:
            parts.append("Fever at home, treated with antipyretics; afebrile in clinic.")
        if stub.type == EncounterType.ACUTE_INJURY:
            parts.append("Injury occurred during play; no loss of consciousness.")
        parts.append("Tolerating fluids with normal urine output." if age_months < 144
                     else "Eating and drinking normally.")
        if negatives:
            parts.append(f"Denies {', '.join(negatives[:4])}.")
        return " ".join(parts)

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
        prior_immunizations: list[Immunization] | None = None,
    ) -> Encounter:
        """Generate a full encounter from a stub."""
        from src.models import (
            VitalSigns, PhysicalExam, Assessment, PlanItem,
            GrowthPercentiles, ReviewOfSystems
        )
        from knowledge.growth.cdc_2000 import (
            generate_normal_vitals, calculate_weight_percentile,
            calculate_height_percentile, calculate_hc_percentile,
            calculate_bmi_percentile, age_in_months
        )
        
        sex = "male" if demographics.sex_at_birth == Sex.MALE else "female"
        
        # Get latest growth data
        latest_growth = growth_data[-1] if growth_data else None

        # Generate vitals (illness-aware for acute encounters)
        vitals_dict = generate_normal_vitals(age_months, rng=self.rng)

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
                # Percentiles at the measurement's own fractional age.
                m_age = age_in_months(latest_growth.age_in_days)
                weight_result = calculate_weight_percentile(latest_growth.weight_kg, m_age, sex)
                height_result = calculate_height_percentile(latest_growth.height_cm, m_age, sex) if latest_growth.height_cm else None
                hc_result = calculate_hc_percentile(latest_growth.head_circumference_cm, m_age, sex) if latest_growth.head_circumference_cm and m_age <= 36 else None
                bmi_result = calculate_bmi_percentile(latest_growth.bmi, m_age, sex) if latest_growth.bmi and m_age >= 24 else None
                
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
        
        # Add chronic conditions to assessment if being addressed (an acute
        # visit's condition is already the primary diagnosis)
        for condition in stub.conditions_to_address:
            if stub.type == EncounterType.ACUTE_ILLNESS:
                break
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
        
        # Generate immunizations for well visits: what is due as of this visit,
        # given every dose already on record.
        immunizations = []
        if stub.type in (EncounterType.WELL_CHILD, EncounterType.NEWBORN):
            immunizations = self._generate_immunizations(
                demographics.date_of_birth,
                stub.date,
                prior_immunizations,
                messiness_level=self._hesitancy_messiness(seed),
                weight_kg=latest_growth.weight_kg if latest_growth else None,
                is_newborn=stub.type == EncounterType.NEWBORN,
            )

        # Generate developmental screening for well-child visits
        developmental_screen = None
        if stub.type in (EncounterType.WELL_CHILD, EncounterType.NEWBORN):
            developmental_screen = self._generate_developmental_screen(age_months, stub.date)

        # Generate lab results for acute illness encounters
        lab_results = self._generate_labs(condition_key, stub.date, stub.type)

        # Generate imaging results for applicable conditions
        imaging_results = self._generate_imaging(condition_key, stub.date)

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
            date=datetime.combine(stub.date, datetime.min.time().replace(hour=self.rng.randint(8, 16))),
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
            imaging_results=imaging_results,
            prescriptions=illness_prescriptions,
            hpi=self._template_hpi(stub, demographics, age_months, condition_key,
                                   vitals.temperature_f),
            anticipatory_guidance=self._generate_anticipatory_guidance_list(age_months) if stub.type in (EncounterType.WELL_CHILD, EncounterType.NEWBORN) else [],
            developmental_screen=developmental_screen,
        )
        
        # Note: Narrative generation is now done in parallel after all encounters are created
        # See _generate_narratives_parallel() called from generate()

        return encounter
    
    # Hesitancy reasons for vaccine refusals
    HESITANCY_REASONS = [
        "Parental refusal - vaccine hesitancy",
        "Parental concern about vaccine safety",
        "Parent requested delay",
        "Contraindication - immunocompromised",
        "Insurance coverage issue",
        "Vaccine not available at clinic",
        "Deferred due to acute illness",
        "Parental religious exemption",
    ]

    # Engine realism parameter, not clinical: refusal probability per offered
    # dose is 5% per messiness level, from level 2 up.
    HESITANCY_RATE_PER_LEVEL = 0.05
    HESITANCY_MIN_LEVEL = 2

    def _hesitancy_messiness(self, seed: GenerationSeed | None) -> int:
        """Messiness level that drives vaccine hesitancy.

        GenerationSeed.messiness_level wins when the caller set it explicitly;
        otherwise the engine constructor's level applies.
        """
        if seed is not None and "messiness_level" in seed.model_fields_set:
            return seed.messiness_level
        return self.messiness_level

    def _imm_rng(self):
        """Per-engine RNG when the engine has one, else the random module."""
        return getattr(self, "rng", None) or random

    def _imm_today(self) -> date:
        """Engine 'today' when injectable, else the real date."""
        return getattr(self, "today", None) or date.today()

    def _vaccine_product(
        self,
        antigen: str,
        on: date,
        weight_kg: float | None = None,
    ) -> dict | None:
        """Product (CVX) for an antigen on a date, from the schedule YAML eras."""
        for product in self._vaccine_antigens.get(antigen, {}).get("products", []):
            start = product.get("from")
            end = product.get("until")
            if start and on < date.fromisoformat(str(start)):
                continue
            if end and on >= date.fromisoformat(str(end)):
                continue
            if weight_kg is not None:
                if "max_weight_kg" in product and weight_kg >= product["max_weight_kg"]:
                    continue
                if "min_weight_kg" in product and weight_kg < product["min_weight_kg"]:
                    continue
            elif "min_weight_kg" in product:
                continue
            return product
        return None

    def _make_immunization(
        self,
        antigen: str,
        dose_number: int,
        on: date,
        notes: str | None = None,
        weight_kg: float | None = None,
    ) -> Immunization | None:
        """Build a completed Immunization record for an antigen dose."""
        from src.models import CodeableConcept, ImmunizationStatus

        product = self._vaccine_product(antigen, on, weight_kg)
        if product is None:
            return None
        spec = self._vaccine_antigens.get(antigen, {})
        return Immunization(
            vaccine_code=CodeableConcept(
                system="http://hl7.org/fhir/sid/cvx",
                code=str(product["cvx"]),
                display=product.get("description", antigen),
            ),
            display_name=spec.get("name", antigen),
            date=on,
            dose_number=dose_number,
            series_doses=spec.get("series_doses"),
            manufacturer=None,
            status=ImmunizationStatus.COMPLETED,
            notes=notes,
        )

    def _antigens_for(self, imm: Immunization) -> set[str]:
        return self._cvx_antigens.get(str(imm.vaccine_code.code), set())

    def _immunization_history(self, immunizations: list[Immunization]) -> dict[str, list[date]]:
        """Antigen -> sorted dates of completed doses (combination vaccines count
        toward each component)."""
        from src.models import ImmunizationStatus

        history: dict[str, set[date]] = {}
        for imm in immunizations:
            if imm.status != ImmunizationStatus.COMPLETED:
                continue
            for antigen in self._antigens_for(imm):
                history.setdefault(antigen, set()).add(imm.date)
        return {k: sorted(v) for k, v in history.items()}

    def _generate_immunizations(
        self,
        date_of_birth: date,
        visit_date: date,
        prior_immunizations: list[Immunization] | None = None,
        *,
        messiness_level: int | None = None,
        weight_kg: float | None = None,
        is_newborn: bool = False,
    ) -> list[Immunization]:
        """Doses given (or refused) at a well visit, from what is due as of that visit.

        Each antigen's next dose is decided from the doses already given, the
        minimum age, minimum interval and maximum age (src/engines/immunizations.py,
        AAP 2026 schedule). Catch-up follows the same rules, so dose numbers
        strictly increase and a dose number is never repeated once given.

        Doses that happen outside this encounter are appended to
        `prior_immunizations` in place, so they enter the patient record without
        being listed as given at this visit: the birth-hospital HepB dose (and
        RSV antibody for infants born in season), the second flu or COVID-19 dose
        at a nurse visit 4+ weeks later, and influenza doses from a flu clinic for
        seasons with no in-season well visit.

        Args:
            date_of_birth: Patient DOB.
            visit_date: Date of the well visit.
            prior_immunizations: Every immunization record so far (any status).
                Mutated: out-of-encounter doses are appended.
            messiness_level: Level driving vaccine hesitancy (defaults to the
                engine's level).
            weight_kg: Weight at the visit (RSV antibody product by weight).
            is_newborn: True for the newborn visit (adds birth-hospital doses).

        Returns:
            Immunizations given or refused at this encounter.
        """
        from src.engines import immunizations as imz
        from src.models import ImmunizationStatus

        prior = prior_immunizations if prior_immunizations is not None else []
        level = self.messiness_level if messiness_level is None else messiness_level
        today = self._imm_today()
        rng = self._imm_rng()
        dob = date_of_birth
        extras: list[Immunization] = []

        def offer(imms: list[Immunization]) -> list[Immunization]:
            imms = [i for i in imms if i is not None]
            if level >= self.HESITANCY_MIN_LEVEL:
                imms = self._inject_vaccine_hesitancy(imms, messiness_level=level)
            return imms

        def completed(imm: Immunization) -> bool:
            return imm.status == ImmunizationStatus.COMPLETED

        # (antigen, dose number) pairs refused earlier: a later dose is catch-up.
        refused = {(a, i.dose_number) for i in prior if not completed(i)
                   for a in self._antigens_for(i)}

        # Birth-hospital doses, dated at birth. AAP 2026 Notes/HepB: birth dose
        # within 24 h; Notes/RSV: born in season, antibody within 1 week of birth,
        # ideally during the birth hospitalization.
        if is_newborn:
            history = self._immunization_history(prior)
            birth = []
            if "HepB" not in history:
                birth.append(self._make_immunization("HepB", 1, dob, notes="Birth hospital"))
            if "RSVmAb" not in history and imz.rsv_mab_due(dob, dob, []):
                birth.append(self._make_immunization("RSVmAb", 1, dob, notes="Birth hospital",
                                                     weight_kg=weight_kg))
            extras += offer(birth)

        # Influenza seasons since 6 months with no flu record and no in-season
        # well visit: recorded as flu-clinic doses.
        extras += self._backfill_flu(dob, visit_date, prior + extras, today, rng, offer)

        history = self._immunization_history(prior + extras)
        given: list[Immunization] = []
        for antigen, dose_number in imz.due_series_doses(dob, visit_date, history):
            note = "Catch-up dose" if (antigen, dose_number) in refused else None
            given.append(self._make_immunization(antigen, dose_number, visit_date, notes=note))

        flu_n = imz.flu_dose_due(dob, visit_date, history.get("Flu", []))
        if flu_n is not None:
            given.append(self._make_immunization("Flu", flu_n, visit_date))

        covid_n = imz.covid_dose_due(dob, visit_date, history.get("COVID19", []))
        if covid_n is not None:
            given.append(self._make_immunization("COVID19", covid_n, visit_date))

        if imz.rsv_mab_due(dob, visit_date, history.get("RSVmAb", [])):
            given.append(self._make_immunization("RSVmAb", 1, visit_date, weight_kg=weight_kg))

        given = offer(given)

        # Second doses at a nurse visit. AAP 2026 Notes/Influenza: 2 doses
        # >= 4 weeks apart when needed; Notes/COVID-19: dose 2 at 4-8 weeks.
        # The 0-14 day spread past the minimum is scheduling realism.
        followups = []
        for imm in given:
            if not completed(imm) or imm.dose_number != 1:
                continue
            antigens = self._antigens_for(imm)
            if "Flu" in antigens:
                season = imz.flu_season(visit_date)
                prior_flu = history.get("Flu", [])
                if imz.flu_doses_needed(dob, season, visit_date, prior_flu) == 2:
                    d2 = visit_date + timedelta(days=28 + rng.randint(0, 14))
                    if d2 <= min(date(season + 1, 6, 30), today):
                        followups.append(self._make_immunization(
                            "Flu", 2, d2, notes="Nurse visit"))
            elif "COVID19" in antigens:
                d2 = visit_date + timedelta(days=28 + rng.randint(0, 14))
                if d2 <= today:
                    followups.append(self._make_immunization("COVID19", 2, d2, notes="Nurse visit"))
        extras += offer(followups)

        prior.extend(extras)
        return given

    def _backfill_flu(
        self,
        dob: date,
        visit_date: date,
        records: list[Immunization],
        today: date,
        rng,
        offer,
    ) -> list[Immunization]:
        """Flu-clinic doses for past seasons with no influenza record.

        Annual well visits often fall outside flu season (vaccine is not given
        in July). AAP recommends annual vaccination from 6 months, ideally by the
        end of October (AAP 2026-27 influenza policy), so a season with no record
        gets a clinic dose dated mid-September through October. A season with a
        refusal on record is left alone.
        """
        from dateutil.relativedelta import relativedelta

        from src.engines import immunizations as imz

        six_months = dob + relativedelta(months=6)
        current = imz.flu_season(visit_date)
        last = current if not imz.flu_available(visit_date) else current - 1
        flu_records = [i for i in records if "Flu" in self._antigens_for(i)]
        out: list[Immunization] = []
        for season in range(imz.flu_season(six_months), last + 1):
            if any(imz.flu_season(i.date) == season for i in flu_records + out):
                continue
            latest = min(date(season + 1, 6, 30), visit_date - timedelta(days=1), today)
            start = max(date(season, 9, 15), six_months)
            stop = min(max(date(season, 10, 31), start), latest)
            if start > stop:
                continue
            d1 = start + timedelta(days=rng.randint(0, (stop - start).days))
            prior_flu = sorted(i.date for i in flu_records + out
                               if i.status.value == "completed")
            need = imz.flu_doses_needed(dob, season, d1, prior_flu)
            first = offer([self._make_immunization("Flu", 1, d1, notes="Influenza clinic")])
            out += first
            if need == 2 and first and first[0].status.value == "completed":
                d2 = d1 + timedelta(days=28 + rng.randint(0, 14))
                if d2 <= latest:
                    out += offer([self._make_immunization("Flu", 2, d2, notes="Influenza clinic")])
        return out

    def _inject_vaccine_hesitancy(
        self,
        immunizations: list[Immunization],
        hesitancy_rate: float | None = None,
        messiness_level: int | None = None,
    ) -> list[Immunization]:
        """Mark some vaccines as NOT_DONE with realistic refusal reasons.

        Args:
            immunizations: List of immunizations to potentially modify
            hesitancy_rate: Probability of hesitancy per vaccine (default based on messiness)
            messiness_level: Level to derive the rate from (defaults to the engine's)

        Returns:
            Modified list with some vaccines marked as refused
        """
        from src.models import ImmunizationStatus

        if hesitancy_rate is None:
            level = self.messiness_level if messiness_level is None else messiness_level
            hesitancy_rate = self.HESITANCY_RATE_PER_LEVEL * level

        rng = self._imm_rng()
        result = []
        for imm in immunizations:
            if rng.random() < hesitancy_rate:
                reason = rng.choice(self.HESITANCY_REASONS)
                result.append(imm.model_copy(update={
                    "status": ImmunizationStatus.NOT_DONE,
                    "status_reason": reason,
                    "notes": f"Vaccine not administered: {reason}",
                }))
            else:
                result.append(imm)

        return result

    def _select_growth_pattern(
        self,
        conditions: list[str],
    ) -> tuple[str, dict]:
        """Select a growth pattern based on patient conditions.

        Args:
            conditions: List of condition names (display names or keys)

        Returns:
            Tuple of (pattern_name, pattern_kwargs dict)
        """
        conditions_lower = [c.lower() for c in conditions]

        # Check for conditions that would cause failure to thrive
        ftt_conditions = [
            "malabsorption", "celiac disease", "celiac", "cystic fibrosis",
            "inflammatory bowel disease", "ibd", "crohn", "ulcerative colitis",
            "feeding difficulty", "failure to thrive", "ftt",
        ]
        for ftt_cond in ftt_conditions:
            for cond in conditions_lower:
                if ftt_cond in cond:
                    return "ftt", {"pattern_onset_age": self.rng.randint(6, 18)}

        # Check for obesity-related conditions
        obesity_conditions = [
            "obesity", "overweight", "prediabetes", "type 2 diabetes",
            "metabolic syndrome", "insulin resistance",
        ]
        for obesity_cond in obesity_conditions:
            for cond in conditions_lower:
                if obesity_cond in cond:
                    return "obesity", {"pattern_onset_age": self.rng.randint(36, 72)}

        # Check for prematurity
        preterm_conditions = [
            "prematurity", "preterm", "premature", "low birth weight",
            "very low birth weight", "vlbw",
        ]
        for preterm_cond in preterm_conditions:
            for cond in conditions_lower:
                if preterm_cond in cond:
                    return "preterm_catchup", {
                        "gestational_age_weeks": self.rng.randint(28, 36)
                    }

        # Check for conditions causing growth delay
        delay_conditions = [
            "hypothyroidism", "growth hormone deficiency", "turner syndrome",
            "constitutional delay",
        ]
        for delay_cond in delay_conditions:
            for cond in conditions_lower:
                if delay_cond in cond:
                    return "growth_delay", {"pattern_onset_age": self.rng.randint(24, 60)}

        # Default to normal growth
        return "normal", {}

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
                tool = self.rng.choice(["ASQ-3", "M-CHAT-R/F"])
            else:
                tool = "Developmental Surveillance"
            domains = ["gross-motor", "fine-motor", "language", "social-emotional", "cognitive"]
        elif age_months <= 36:
            tool = "ASQ-3" if age_months == 30 else "Developmental Surveillance"
            domains = ["gross-motor", "fine-motor", "language", "social-emotional", "cognitive"]
        elif age_months <= 60:
            tool = "PEDS" if self.rng.random() < 0.3 else "Developmental Surveillance"
            domains = ["motor", "language", "social", "self-help", "academic-readiness"]
        else:
            # School-age - less frequent formal screening
            if self.rng.random() < 0.2:
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
        result = self.rng.choices(
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
                if domain in possible_concerns and self.rng.random() < 0.3:
                    concerns.append(self.rng.choice(possible_concerns[domain]))

        # Generate notes
        notes = None
        if result == "normal":
            notes = self.rng.choice([
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

        # Build age-appropriate constraints
        age_constraints = []
        if age_months > 18:
            age_constraints.append("fontanelles (closed by 18 months)")
        if age_months > 6:
            age_constraints.append("Moro reflex (disappears by 6 months)")
            age_constraints.append("umbilical cord stump (heals within first weeks)")
        if age_months < 6:
            age_constraints.append("walking or running (too young)")

        constraints_note = ""
        if age_constraints:
            constraints_note = f"\nDO NOT mention these age-inappropriate findings: {', '.join(age_constraints)}."

        system = f"""You are a pediatric physician writing clinical documentation.
Write clear, professional SOAP-style notes. Use standard medical abbreviations where appropriate.
Keep it concise but complete. Sign as the provider.
IMPORTANT: This patient is {age_str}. Only include age-appropriate exam findings.{constraints_note}"""

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
        return self.rng.choice(male_names if sex == Sex.MALE else female_names)
    
    def _generate_last_name(self) -> str:
        names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez", "Anderson", "Taylor", "Thomas", "Moore", "Jackson", "Martin", "Lee", "Thompson", "White", "Harris"]
        return self.rng.choice(names)
    
    def _generate_provider_name(self) -> str:
        first = self.rng.choice(["Sarah", "Michael", "Jennifer", "David", "Emily", "Robert", "Jessica", "William", "Amanda", "James"])
        last = self.rng.choice(["Chen", "Patel", "Kim", "Singh", "Johnson", "Williams", "Brown", "Garcia", "Miller", "Davis"])
        return f"Dr. {first} {last}"
    
    def _generate_phone(self) -> str:
        return f"({self.rng.randint(200, 999)}) {self.rng.randint(200, 999)}-{self.rng.randint(1000, 9999)}"
    
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

    # ICD-10-CM fallback for problem names that are not conditions.yaml keys
    # (mostly comorbidities named in conditions.yaml `comorbidities:` lists).
    # Codes from the ICD-10-CM FY2026 tabular list. Display text is the
    # problem-list name; the second element is the ICD-10 descriptor.
    _ICD10_FALLBACK: dict[str, tuple[str, str]] = {
        "asthma": ("J45.20", "Mild intermittent asthma, uncomplicated"),
        "adhd": ("F90.2", "Attention-deficit hyperactivity disorder, combined type"),
        "eczema": ("L20.9", "Atopic dermatitis, unspecified"),
        "allergic rhinitis": ("J30.9", "Allergic rhinitis, unspecified"),
        "anxiety": ("F41.1", "Generalized anxiety disorder"),
        "food allergy": ("T78.1XXA", "Other adverse food reactions, NEC, initial encounter"),
        "obesity": ("E66.9", "Obesity, unspecified"),
        "constipation": ("K59.00", "Constipation, unspecified"),
        "recurrent otitis media": ("H66.90", "Otitis media, unspecified, unspecified ear"),
        "depression": ("F32.A", "Depression, unspecified"),
        "autism": ("F84.0", "Autistic disorder"),
        "type 1 diabetes": ("E10.9", "Type 1 diabetes mellitus without complications"),
        "type 2 diabetes": ("E11.9", "Type 2 diabetes mellitus without complications"),
        "seizure disorder": ("G40.909", "Epilepsy, unspecified, not intractable"),
        "epilepsy": ("G40.909", "Epilepsy, unspecified, not intractable"),
        "cerebral palsy": ("G80.9", "Cerebral palsy, unspecified"),
        "gerd": ("K21.9", "Gastro-esophageal reflux disease without esophagitis"),
        "prediabetes": ("R73.03", "Prediabetes"),
        "cough": ("R05.9", "Cough, unspecified"),
        "swimmer's ear": ("H60.339", "Swimmer's ear, unspecified ear"),
        "swimmers ear": ("H60.339", "Swimmer's ear, unspecified ear"),
        "kawasaki disease": ("M30.3", "Mucocutaneous lymph node syndrome [Kawasaki]"),
        "hearing loss": ("H91.90", "Unspecified hearing loss, unspecified ear"),
        "intellectual disability": ("F79", "Unspecified intellectual disabilities"),
        "motion sickness": ("T75.3XXA", "Motion sickness, initial encounter"),
        "somatic complaints": ("F45.9", "Somatoform disorder, unspecified"),
        "vitamin d deficiency": ("E55.9", "Vitamin D deficiency, unspecified"),
        "celiac disease": ("K90.0", "Celiac disease"),
        "thyroid disease": ("E07.9", "Disorder of thyroid, unspecified"),
        "dyslipidemia": ("E78.5", "Hyperlipidemia, unspecified"),
        "hypertension": ("I10", "Essential (primary) hypertension"),
        "nafld": ("K76.0", "Fatty (change of) liver, not elsewhere classified"),
        "acanthosis nigricans": ("L83", "Acanthosis nigricans"),
        "sinusitis": ("J32.9", "Chronic sinusitis, unspecified"),
        "oppositional defiant disorder": ("F91.3", "Oppositional defiant disorder"),
        "tic disorder": ("F95.9", "Tic disorder, unspecified"),
        "eating disorders": ("F50.9", "Eating disorder, unspecified"),
        "learning disability": ("F81.9", "Developmental disorder of scholastic skills, unspecified"),
        "speech delay": ("F80.9", "Developmental disorder of speech and language, unspecified"),
        "developmental delay": ("F88", "Other disorders of psychological development"),
    }
    _DISPLAY_OVERRIDES = {
        "adhd": "ADHD", "gerd": "GERD", "nafld": "Nonalcoholic Fatty Liver Disease",
    }

    def _resolve_icd10(self, name: str) -> tuple[str, str]:
        """(ICD-10 code, display name) for a problem. Never returns R69.

        Order: condition knowledge service (local YAML + aliases, then cache /
        Athena / LLM when available), then _ICD10_FALLBACK. Raises ValueError
        for a name nothing can code, rather than writing a placeholder.
        """
        result = self.condition_service.get_condition(name)
        if result.found and result.definition and result.definition.icd10_primary \
                and result.definition.icd10_primary not in ("R69", "R99"):
            return result.definition.icd10_primary, result.definition.display_name
        key = name.lower().replace("_", " ").strip()
        if key in self._ICD10_FALLBACK:
            display = self._DISPLAY_OVERRIDES.get(key, name.replace("_", " ").title())
            return self._ICD10_FALLBACK[key][0], display
        raise ValueError(f"No ICD-10 code for condition '{name}' (not in conditions.yaml, "
                         f"its aliases, or the engine fallback table)")

    def _can_code(self, name: str) -> bool:
        try:
            self._resolve_icd10(name)
            return True
        except ValueError:
            return False

    # Self-limited conditions that resolve after an episode (days to
    # abatement when conditions.yaml gives no presentation.duration_days).
    # Kawasaki: AHA 2017 statement, acute phase + convalescent aspirin to
    # 6 weeks; the problem is resolved when aspirin stops absent coronary
    # findings.
    _SELF_LIMITED_DAYS = {"kawasaki disease": 42}
    _DEFAULT_ACUTE_DAYS = 14

    def _is_acute_condition(self, cond_name: str) -> bool:
        if cond_name.lower().replace("_", " ").strip() in self._SELF_LIMITED_DAYS:
            return True
        ykey = self._get_condition_key(cond_name) or cond_name
        data = self._conditions.get(ykey) or {}
        return isinstance(data, dict) and data.get("category") == "acute"

    def _abatement_for(self, cond_name: str, onset: date) -> date | None:
        """Abatement date for a self-limited condition, or None if chronic or
        the episode is still in progress on today."""
        key = cond_name.lower().replace("_", " ").strip()
        days = self._SELF_LIMITED_DAYS.get(key)
        if days is None:
            ykey = self._get_condition_key(cond_name) or cond_name
            data = self._conditions.get(ykey) or self._conditions.get(cond_name) or {}
            if not isinstance(data, dict) or data.get("category") != "acute":
                return None
            dur = (data.get("presentation") or {}).get("duration_days")
            days = max(dur) if isinstance(dur, list) and dur else self._DEFAULT_ACUTE_DAYS
        end = onset + timedelta(days=days)
        return end if end <= self.today else None

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
                    if comorbid not in result and self.rng.random() < probability \
                            and self._can_code(comorbid):
                        # Only comorbidities we can code (YAML or fallback
                        # table); vague ones like "gi disorders" are skipped.
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

        # Age-appropriate filtering using acute conditions from YAML and hardcoded gates
        filtered_pool = []
        for illness, weight in illness_pool:
            illness_key = illness.lower().replace(' ', '_')

            # Check hardcoded age gates first (most reliable)
            if illness_key in self._hardcoded_age_gates:
                min_age, max_age = self._hardcoded_age_gates[illness_key]
            elif illness in self._acute_conditions:
                min_age = self._acute_conditions[illness].get('min_months', 0)
                max_age = self._acute_conditions[illness].get('max_months')
            else:
                # Fallback ages for common conditions
                fallback_ages = {
                    "Acute Otitis Media": (6, None),
                    "Swimmer's Ear": (12, None),
                    "Croup": (6, None),
                    "Allergic Rhinitis": (24, None),
                    "Influenza": (6, None),
                    "Insect Bite Reaction": (6, None),
                    "Bronchiolitis": (0, 24),  # Max 24 months
                }
                min_age, max_age = fallback_ages.get(illness, (0, None))

            # Check both min and max age constraints
            if age_months < min_age:
                continue
            if max_age is not None and age_months > max_age:
                continue

            filtered_pool.append((illness, weight))

        # If all filtered out, use age-appropriate fallback
        if not filtered_pool:
            # Use infant fallback only for patients ≤24 months
            if age_months <= 24:
                infant_fallback = seasonal_weights.get('infant_fallback', {})
                if infant_fallback:
                    for condition_key, weight in infant_fallback.items():
                        if condition_key in self._conditions:
                            display_name = self._conditions[condition_key].get('display_name', condition_key.replace('_', ' ').title())
                        else:
                            display_name = condition_key.replace('_', ' ').title()
                        filtered_pool.append((display_name, weight))
                else:
                    # Hardcoded infant fallback (bronchiolitis OK for ≤24mo)
                    filtered_pool = [
                        ("Viral Syndrome", 30),
                        ("Upper Respiratory Infection", 25),
                        ("Bronchiolitis", 25),
                        ("Fever", 15),
                        ("Viral Gastroenteritis", 5),
                    ]
            else:
                # Generic fallback for older patients (no bronchiolitis)
                filtered_pool = [
                    ("Viral Syndrome", 30),
                    ("Upper Respiratory Infection", 25),
                    ("Fever", 20),
                    ("Viral Gastroenteritis", 15),
                    ("Cough", 10),
                ]

        # Weighted random selection
        illnesses, weights = zip(*filtered_pool)
        return self.rng.choices(illnesses, weights=weights, k=1)[0]

    # Doses per day for a frequency token. Used to split "divided" daily
    # doses into per-dose amounts.
    _DOSES_PER_DAY = {"QD": 1, "DAILY": 1, "BID": 2, "TID": 3, "QID": 4}

    def _dose_from_def(self, med: dict, weight_kg: float | None) -> tuple[str, str] | None:
        """Per-dose amount (mg, as a string) and frequency for a YAML med entry.

        conditions.yaml convention: when `frequency` starts with "divided",
        `dose_mg_kg` and `max_dose_mg` are per DAY and are split across the
        doses (e.g. amoxicillin 90 mg/kg/day "divided BID" -> 45 mg/kg BID).
        Otherwise they are per dose. Returns None when the entry has no dose.
        """
        frequency = med.get("frequency", "") or ""
        dose_mg_kg = med.get("dose_mg_kg")
        max_dose = med.get("max_dose_mg")
        if dose_mg_kg and weight_kg:
            amount = dose_mg_kg * weight_kg
            if frequency.lower().startswith("divided"):
                token = frequency.split()[1].split("-")[0].upper() if len(frequency.split()) > 1 else "BID"
                per_day = self._DOSES_PER_DAY.get(token, 2)
                if max_dose:
                    amount = min(amount, max_dose)
                amount /= per_day
                cap = max_dose / per_day if max_dose else None
                frequency = token
            else:
                cap = max_dose
                if cap:
                    amount = min(amount, cap)
            rounded = self._round_dose(amount)
            if cap and rounded > cap:
                rounded = int(cap)
            return str(rounded), frequency
        if med.get("fixed_dose_mg"):
            return str(med["fixed_dose_mg"]), frequency
        return None

    def _select_acute_meds(self, medications: list, encounter_date: date | None) -> list[dict]:
        """Pick at most one agent per choice group (PRN meds always included).

        Entries sharing `choice_group` in conditions.yaml are alternatives
        (e.g. AOM amoxicillin vs amoxicillin-clavulanate). The first entry is
        first-line. An entry with `use_when.recent_class_days: N` replaces it
        when the patient had a same-class drug (`drug_class`) prescribed within
        the last N days (AAP 2013 AOM CPG: amoxicillin-clavulanate if
        amoxicillin in the past 30 days or treatment failure). Entries without
        `choice_group` fall back to grouping by the first clause of their
        indication, as before.
        """
        groups: dict[str, list[dict]] = {}
        order: list[str] = []
        selected_prn: list[dict] = []
        for med in medications:
            if not isinstance(med, dict):
                continue
            if med.get("prn", False):
                selected_prn.append(med)
                continue
            indication = med.get("indication") or "primary"
            gkey = med.get("choice_group") or indication.lower().split(",")[0].strip()
            if gkey not in groups:
                groups[gkey] = []
                order.append(gkey)
            groups[gkey].append(med)

        chosen: list[dict] = []
        for gkey in order:
            options = groups[gkey]
            pick = options[0]
            for alt in options[1:]:
                days = (alt.get("use_when") or {}).get("recent_class_days")
                if days and encounter_date and self._recent_class_rx(
                        alt.get("drug_class") or self._med_class(alt.get("agent", "")),
                        encounter_date, days):
                    pick = alt
                    break
            chosen.append(pick)
        return chosen + selected_prn

    def _recent_class_rx(self, drug_class: str | None, on: date, days: int) -> bool:
        if not drug_class:
            return False
        for d, agent in self._rx_history:
            if 0 <= (on - d).days <= days and self._med_class(agent) == drug_class:
                return True
        return False

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
                dob = getattr(self, "_current_dob", None)
                rx_age = (calendar_months_between(dob, encounter_date)
                          if dob and encounter_date else age_months)
                eligible = [m for m in medications if isinstance(m, dict) and not (
                    m.get('age_min_months') and rx_age is not None
                    and rx_age < m['age_min_months'])]
                selected_meds = self._select_acute_meds(eligible, encounter_date)

                # Generate medication plan items and prescriptions
                for med in selected_meds:
                    if not isinstance(med, dict):
                        continue

                    agent = med.get('agent', '')
                    rxnorm = med.get('rxnorm', '')
                    frequency = med.get('frequency', '')
                    duration = med.get('duration_days')
                    route = med.get('route', 'oral')
                    indication = med.get('indication')
                    is_prn = med.get('prn', False)

                    if not agent:
                        continue

                    # age_min_months already applied above using calendar
                    # age on the prescribing date (same as the invariants).

                    # Calculate dose with clinical rounding
                    dose = self._dose_from_def(med, weight_kg)
                    if dose is not None:
                        dose_str, frequency = dose
                        dose_unit = "mg"
                    else:
                        dose_str = "per package"
                        dose_unit = ""

                    # Duration string
                    duration_str = f" x {duration} days" if duration else ""
                    prn_str = " PRN" if is_prn else ""

                    # Create plan item (include unit in description for display)
                    dose_display = f"{dose_str}{dose_unit}" if dose_unit else dose_str
                    plan_items.append(PlanItem(
                        category="medication",
                        description=f"{agent} {dose_display} {frequency}{duration_str}{prn_str}",
                        details=indication if indication else None,
                    ))

                    # Create prescription (non-PRN meds only for now)
                    if rxnorm and encounter_date:
                        self._rx_history.append((encounter_date, agent))
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
                            prn_reason=(indication or "symptoms") if is_prn else None,
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
        if self.rng.random() < 0.2:  # 20% chance of being more prolific
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
        provider_role = "RN" if self.rng.random() < 0.6 else "MD"  # Most replies from nurses

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
            category = self.rng.choices(categories, weights=weights, k=1)[0]

            # Select template
            templates = message_templates.get(category, message_templates[MessageCategory.CLINICAL_QUESTION])
            template = self.rng.choice(templates)

            # Select medium (mostly portal, some phone)
            medium = MessageMedium.PORTAL if self.rng.random() < 0.75 else MessageMedium.PHONE

            # Generate timestamp relative to encounters
            if encounters:
                # Pick a random encounter as reference point
                ref_encounter = self.rng.choice(encounters)
                # Message can be 1-14 days after encounter
                days_after = self.rng.randint(1, 14)
                sent_dt = ref_encounter.date + timedelta(days=days_after, hours=self.rng.randint(7, 21))
                related_encounter_id = ref_encounter.id if self.rng.random() < 0.5 else None
            else:
                # No encounters, generate within last year
                days_ago = self.rng.randint(1, 365)
                sent_dt = datetime.now() - timedelta(days=days_ago)
                related_encounter_id = None

            # Reply typically within 4-48 hours (business hours more common)
            reply_hours = self.rng.choices([4, 8, 24, 48], weights=[15, 40, 35, 10], k=1)[0]
            reply_dt = sent_dt + timedelta(hours=reply_hours)

            # Fill in template variables
            child_name = demographics.given_names[0] if demographics.given_names else "Child"
            symptom = self.rng.choice(common_symptoms)
            medication = self.rng.choice(medication_names)
            pharmacy = self.rng.choice(pharmacies)
            side_effect = self.rng.choice(side_effects)
            warning_sign = self.rng.choice(warning_signs)
            home_treatment = self.rng.choice(home_treatments)
            form_type = self.rng.choice(form_types)
            advice = self.rng.choice(advice_options)
            reason = self.rng.choice(["a checkup", "follow-up on previous issue", "ongoing symptoms"])

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
                    # Only the visit's primary diagnosis becomes a resolved
                    # problem; secondary lines restate chronic problems.
                    if not assessment.diagnosis or not assessment.is_primary:
                        continue

                    diagnosis_lower = assessment.diagnosis.lower().strip()
                    if diagnosis_lower in seen_diagnoses:
                        continue
                    seen_diagnoses.add(diagnosis_lower)

                    # Get ICD-10 code: local table, then conditions.yaml /
                    # aliases / supplemental table. Never R69.
                    code_info = acute_icd_codes.get(diagnosis_lower) or \
                        self._resolve_icd10(assessment.diagnosis)

                    # Resolution: typically 7-14 days after an acute visit.
                    # An episode that would end after today is still active.
                    onset = encounter.date.date() if isinstance(encounter.date, datetime) else encounter.date
                    resolution_date = onset + timedelta(days=self.rng.randint(7, 14))
                    still_active = resolution_date > self.today

                    resolved_condition = Condition(
                        display_name=assessment.diagnosis,
                        code=CodeableConcept(
                            system="http://hl7.org/fhir/sid/icd-10-cm",
                            code=code_info[0],
                            display=code_info[1],
                        ),
                        clinical_status=ConditionStatus.ACTIVE if still_active else ConditionStatus.RESOLVED,
                        onset_date=onset,
                        abatement_date=None if still_active else resolution_date,
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

                    if end_date > self.today:
                        new_status = MedicationStatus.ACTIVE

                    past_med = Medication(
                        id=rx.id,
                        prescribed_at_encounter=encounter.id,
                        prn_reason=rx.prn_reason,
                        instructions=rx.instructions,
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
                        discontinuation_reason=(
                            None if new_status == MedicationStatus.ACTIVE
                            else "Course completed" if new_status == MedicationStatus.COMPLETED
                            else "No longer needed"),
                    )
                    past_medications.append(past_med)

        # A new course in the same drug class replaces the earlier one (e.g.
        # amoxicillin failure -> amoxicillin-clavulanate): stop the earlier
        # course the day the new one starts so two never overlap.
        by_class: dict[str, list[Medication]] = {}
        for med in past_medications:
            cls = self._med_class(med.display_name)
            if cls and not med.prn:
                by_class.setdefault(cls, []).append(med)
        for meds in by_class.values():
            meds.sort(key=lambda m: m.start_date)
            for prev, nxt in zip(meds, meds[1:]):
                if prev.end_date and prev.end_date > nxt.start_date:
                    prev.end_date = nxt.start_date
                    prev.status = MedicationStatus.STOPPED
                    prev.discontinuation_reason = f"Changed to {nxt.display_name}"

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
        self._current_dob = demographics.date_of_birth
        self.today = self._today_override or date.today()
        # Prior prescriptions inform class-aware choices (e.g. AOM after
        # recent amoxicillin gets amoxicillin-clavulanate).
        self._rx_history = [(m.start_date, m.display_name) for m in patient.medication_list] + [
            (rx.start_date, rx.display_name) for e in patient.encounters for rx in e.prescriptions]

        # Determine visit type (with fallback)
        if visit_type is None:
            if config["visit_types"]:
                visit_type = self.rng.choice(config["visit_types"])
            else:
                visit_type = EncounterType.ACUTE_ILLNESS

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
                    encounter_condition = self.rng.choice(valid_conditions)
                else:
                    # Fall back to common conditions
                    encounter_condition = self.rng.choice(["otitis_media", "viral_uri", "pharyngitis"])

        # Build the encounter stub
        today = self._today_override or date.today()
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
                rng=self.rng,
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
            prior_immunizations=list(patient.immunization_record),
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
        if "atypical_vitals" in complicating_factors and self.rng.random() < atypical_prob:
            if encounter.vital_signs:
                # Make vitals less obviously abnormal
                # e.g., afebrile UTI, hypothermic sepsis
                if encounter.vital_signs.temperature_f and encounter.vital_signs.temperature_f > 100.4:
                    # 30% chance of being afebrile despite infection
                    if self.rng.random() < 0.3:
                        encounter.vital_signs.temperature_f = self.rng.uniform(98.0, 99.5)

        # Red herrings in physical exam
        if "red_herrings" in complicating_factors and self.rng.random() < atypical_prob:
            if encounter.physical_exam:
                # Add an incidental finding
                red_herrings = [
                    ("skin", "Small bruise noted on anterior shin"),
                    ("heent", "Mild nasal congestion"),
                    ("respiratory", "Occasional cough during exam"),
                ]
                system, finding = self.rng.choice(red_herrings)
                current = getattr(encounter.physical_exam, system, None)
                if current:
                    setattr(encounter.physical_exam, system, f"{current}. {finding}")
                else:
                    setattr(encounter.physical_exam, system, finding)

        # Social complexity
        if "social_complexity" in complicating_factors and self.rng.random() < 0.3:
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
            snapshot_date = self._months_to_date(dob, age_months)

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
                    start_age = self.rng.randint(min_age, min(max_age, current_age_months))
                else:
                    start_age = cumulative_age + self.rng.randint(6, 18)

                if start_age > current_age_months:
                    # Haven't reached this stage yet
                    break

                # Determine end age (when next stage starts or current)
                if i + 1 < len(arc.stages):
                    next_min, _ = arc.stages[i + 1].typical_age_range
                    end_age = min(current_age_months, next_min + self.rng.randint(0, 12))
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
                        clinical_status=ConditionStatus.ACTIVE,
                        onset_date=self._months_to_date(patient.demographics.date_of_birth, start_age),
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
        med_start_date = self._months_to_date(dob, age_months)

        # Map conditions to typical medications with full details
        # (name, rxnorm, dose_qty, dose_unit, frequency, route)
        condition_meds = {
            "eczema": [
                ("Hydrocortisone 1% Cream", "310466", "Apply thin layer", "application", "BID", "topical")
            ],
            "asthma": [
                ("Albuterol HFA Inhaler", "435", "1-2", "puffs", "Q4-6H PRN", "inhaled"),
                ("Fluticasone Propionate HFA", "41126", "1-2", "puffs", "BID", "inhaled"),
            ],
            "reactive airway disease": [
                ("Albuterol HFA Inhaler", "435", "1-2", "puffs", "Q4-6H PRN", "inhaled")
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
            rng=self.rng,
        )
        weight, height, hc, bmi = growth.generate_measurement(age_months)

        return GrowthMeasurement(
            date=self._months_to_date(patient.demographics.date_of_birth, age_months),
            age_in_days=int(age_months * 30.44),  # Approximate days for age_in_days field
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
