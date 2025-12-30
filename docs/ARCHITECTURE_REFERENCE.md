# Oread Architecture Reference

**Generated:** 2025-12-29
**Purpose:** Complete reference for patient generation system

---

## 1. Knowledge Directory Structure

```
knowledge/
├── __init__.py                          # 188 bytes
├── conditions/
│   ├── _schema.yaml                     # 3,523 bytes - Schema definition
│   ├── conditions.yaml                  # 79,917 bytes - Main conditions database
│   ├── disease_arcs.yaml               # 9,477 bytes - Time Travel progressions
│   └── peds/
│       ├── behavioral/
│       │   └── adhd.yaml               # 13,045 bytes
│       └── respiratory/
│           └── asthma.yaml             # 12,001 bytes
├── growth/
│   ├── __init__.py                      # 771 bytes
│   └── cdc_2000.py                      # 25,790 bytes - CDC growth charts
└── immunizations/
    └── aap_schedule.yaml               # 11,025 bytes - AAP vaccine schedule
```

---

## 2. Condition YAML Schema

**File:** `knowledge/conditions/conditions.yaml`

```yaml
# Example condition structure (otitis_media)
otitis_media:
  display_name: "Acute Otitis Media"
  aliases: ["Ear infection", "AOM", "Middle ear infection"]

  billing_codes:
    icd10: ["H66.90", "H66.91", "H66.92"]
    snomed: "65363002"  # Acute otitis media

  category: acute
  system: ent

  demographics:
    age_months: { min: 6, peak: [6, 36], max: 144 }
    gender_bias: { male: 0.52, female: 0.48 }
    risk_factors: ["daycare", "bottle_propping", "smoke_exposure"]

  prevalence:
    lifetime_by_age_3: 0.83
    notes: "Most common reason for pediatric antibiotic prescriptions"

  seasonality:
    peak_months: [10, 11, 12, 1, 2, 3]
    weight_multiplier: 1.8

  vitals_impact:
    temp_f: [100.4, 102.5]
    hr_multiplier: 1.10
    rr_multiplier: 1.0
    spo2_min: 97

  presentation:
    symptoms:
      - { name: "ear pain", probability: 0.90, description: "otalgia, pulling/tugging at ear", age_min: 6 }
      - { name: "fever", probability: 0.70, description: "temperature 100-102F" }
      - { name: "irritability", probability: 0.85, description: "fussiness, poor sleep, crying" }
    duration_days: [3, 7]
    physical_exam:
      - { system: "heent", finding: "TM erythematous and bulging with loss of light reflex", probability: 0.85 }
      - { system: "heent", finding: "Decreased TM mobility on pneumatic otoscopy", probability: 0.90 }

  diagnostics:
    labs: []  # Clinical diagnosis
    imaging:
      - name: "Imaging Name"
        modality: "X-ray"
        loinc: "36643-5"
        finding_positive: "Abnormal finding description"
        finding_negative: "Normal finding description"
        impression_positive: "IMPRESSION: Abnormal"
        impression_negative: "IMPRESSION: Normal"
        probability_positive: 0.85

  treatment:
    approach: "antibiotic_or_watchful_waiting"
    medications:
      - agent: "Amoxicillin"
        rxnorm: "723"
        dose_mg_kg: 90
        max_dose_mg: 3000
        frequency: "divided BID"
        duration_days: 10
        route: "oral"
        indication: "first-line for AOM"
      - agent: "Ibuprofen"
        rxnorm: "5640"
        dose_mg_kg: 10
        max_dose_mg: 400
        frequency: "Q6H PRN"
        indication: "pain management"
        prn: true
    patient_instructions:
      - "Complete full course of antibiotics"
      - "Return if no improvement in 48-72 hours"

  # Time Travel: Disease progression
  progression:
    - to: "another_condition_key"
      trigger:
        type: "age_reached"
        age_months: 18
      probability: 0.30
      risk_factors: ["risk1", "risk2"]
      decision_point:
        description: "Clinical decision description"
        alternatives: ["Option 1", "Option 2"]
        typical_decision: "What usually happens"
```

### Numeric Lab Definition (New)

```yaml
diagnostics:
  labs:
    # Numeric labs
    - name: "White Blood Cell Count"
      loinc: "6690-2"
      value_type: numeric
      unit: "K/uL"
      normal_range_low: 4.5
      normal_range_high: 13.5
      abnormal_high_min: 13.6
      abnormal_high_max: 25.0
      probability_abnormal: 0.70
      age_ranges:
        - { age_min: 0, age_max: 12, normal_low: 6.0, normal_high: 17.5 }
        - { age_min: 12, age_max: 48, normal_low: 5.5, normal_high: 15.5 }
        - { age_min: 48, age_max: 216, normal_low: 4.5, normal_high: 13.5 }

    # Binary labs
    - name: "Urine Culture"
      loinc: "630-4"
      value_type: binary
      result_positive: ">50,000 CFU/mL E. coli"
      result_negative: "No growth at 48 hours"
      probability_positive: 0.85
```

---

## 3. LLM Prompts/Templates

**File:** `src/llm/client.py`

```python
class LLMClient:
    """Client for Claude API with structured output support."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-haiku-4-5-20251001",
        cache_dir: Path | None = None,
        enable_cache: bool = True,
    ):
        self.client = Anthropic(api_key=self.api_key)
        self.model = model

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.5,
    ) -> T:
        """Generate structured response using Claude's tool use."""
        tool = {
            "name": "output",
            "description": "Output the structured response",
            "input_schema": schema.model_json_schema(),
        }

        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "You are a helpful assistant.",
            messages=[{"role": "user", "content": prompt}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "output"},
            temperature=temperature,
        )

        # Extract tool call result
        for block in response.content:
            if block.type == "tool_use" and block.name == "output":
                return schema.model_validate(block.input)
```

### LLM Prompts in engine.py

**1. Description Parser (Line ~307):**
```python
prompt = f"""Parse this patient description and extract structured data.

Description: "{seed.description}"

Extract:
- age_months: Patient age in months (e.g., "6 month old" = 6, "2 year old" = 24)
- sex: "male" or "female"
- conditions: List of medical conditions mentioned
- complexity_tier: "tier-0" for healthy, "tier-1" for single chronic, "tier-2" for multiple

Examples:
- "healthy 6 month old girl" -> {{"age_months": 6, "sex": "female", "conditions": [], "complexity_tier": "tier-0"}}
- "3 year old boy with asthma" -> {{"age_months": 36, "sex": "male", "conditions": ["asthma"], "complexity_tier": "tier-1"}}"""

system = "You are a medical data parser. Extract structured patient information from natural language."
```

**2. Clinical Note Generation (Line ~2441):**
```python
prompt = f"""Write a concise pediatric clinical note in natural medical prose.
Do not use bullet points. Write flowing sentences.

HPI: {encounter.hpi or ""}
Physical Exam: (summarize key findings)
Assessment: {assessment_str}
Plan: {plan_str}

Write the clinical note (start with HPI, end with signature):"""

system = """You are a pediatric physician writing clinical documentation.
Write clear, professional SOAP-style notes. Use standard medical abbreviations."""
```

**3. Parent Narrative / HPI (Line ~2507):**
```python
prompt = f"""Write a realistic parent narrative (HPI) for a pediatric visit.

Patient: {age_str} {demographics.sex_at_birth.value} named {demographics.given_names[0]}
Chief Complaint: {encounter.chief_complaint}
Duration: {encounter.illness_duration_days or 'today'} days
{symptoms_context}

Write 2-3 sentences as if a parent is describing their child's symptoms.
Use natural parent language like "started yesterday", "won't eat", "seems fussy"."""

system = """You are helping document a pediatric visit. Write natural, realistic
parent descriptions of childhood illness. Keep it brief and clinical-appropriate."""
```

**4. Clinical Reasoning (Line ~2605):**
```python
prompt = f"""Write brief clinical reasoning for this pediatric assessment.

Patient: {age_str} {demographics.sex_at_birth.value}
Chief Complaint: {encounter.chief_complaint}
Diagnosis: {diagnosis}
Supporting Evidence: {evidence_str}

Write 1-2 sentences explaining how the findings support the diagnosis."""

system = """You are a pediatrician documenting clinical reasoning.
Be concise and use standard medical abbreviations."""
```

**5. Anticipatory Guidance (Line ~2659):**
```python
prompt = f"""Generate 4-5 anticipatory guidance points for a well-child visit.

Patient: {age_str} {demographics.sex_at_birth.value}
Developmental stage: {stage}{conditions_str}

Topics to address:
- Safety/injury prevention
- Nutrition and feeding
- Sleep
- Development milestones
- Social/emotional development

Be specific (e.g., "introduce finger foods" not "nutrition guidance")."""

system = """You are a pediatrician giving anticipatory guidance.
Be specific and practical. Reference AAP recommendations where relevant."""
```

---

## 4. Markdown Exporter

**File:** `src/exporters/markdown.py`

```python
def export_markdown(
    patient: Patient,
    output_path: Path | None = None,
    include_full_notes: bool = True,
) -> str:
    """Export a patient to Markdown format."""
    lines = []
    d = patient.demographics

    # Header
    lines.append(f"# Patient Record: {d.full_name}")
    lines.append(f"**Generated:** {patient.generated_at.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Complexity:** {patient.complexity_tier.value}")

    # Demographics
    lines.append("## Demographics")
    lines.append(f"- **Name:** {d.full_name}")
    lines.append(f"- **Date of Birth:** {d.date_of_birth.strftime('%B %d, %Y')}")
    lines.append(f"- **Age:** {_format_age(d.age_years, d.age_months)}")
    lines.append(f"- **Sex at Birth:** {d.sex_at_birth.value.title()}")

    # Problem List with codes
    lines.append("## Problem List")
    for condition in patient.problem_list:
        status = condition.clinical_status.value.title()
        severity = f" ({condition.severity.value})" if condition.severity else ""
        lines.append(f"- **{condition.display_name}**{severity} - {status}")
        if condition.code:
            code_system = "SNOMED" if "snomed" in condition.code.system.lower() else "ICD-10"
            lines.append(f"  - {code_system}: {condition.code.code}")

    # Medications with RxNorm
    lines.append("## Medications")
    for med in active_meds:
        rxnorm_str = ""
        if med.code and "rxnorm" in med.code.system.lower():
            rxnorm_str = f" (RxNorm: {med.code.code})"
        lines.append(f"- **{med.display_name}**{rxnorm_str} {med.dose_quantity} {med.dose_unit} {med.frequency}")

    # Encounters
    for enc in patient.encounters:
        lines.append(f"### {enc.date.strftime('%Y-%m-%d')} - {_format_encounter_type(enc.type)}")
        lines.append(f"**Chief Complaint:** {enc.chief_complaint}")
        lines.append(f"**Provider:** {enc.provider.name}, {enc.provider.credentials or ''}")
        lines.append(f"**Vitals:** {' | '.join(vitals_parts)}")

        # Full narrative note (collapsible)
        if include_full_notes and enc.narrative_note:
            lines.append("<details>")
            lines.append("<summary>Full Narrative Note</summary>")
            lines.append(f"```\n{enc.narrative_note}\n```")
            lines.append("</details>")

    return "\n".join(lines)
```

---

## 5. Validation (Pydantic Models)

**File:** `src/models/patient.py`

```python
class VitalsImpact(BaseModel):
    """Vitals modification for illness-aware generation."""
    temp_f: tuple[float, float] | None = Field(default=None)
    hr_multiplier: float = Field(default=1.0, ge=0.5, le=2.0)
    rr_multiplier: float = Field(default=1.0, ge=0.5, le=2.0)
    spo2_min: int | None = Field(default=None, ge=70, le=100)


class SymptomDefinition(BaseModel):
    """Symptom with probability and age constraints."""
    name: str = Field(description="Symptom name")
    probability: float = Field(ge=0.0, le=1.0, default=1.0)
    description: str | None = None
    age_min: int | None = None  # months
    age_max: int | None = None  # months


class LabDefinition(BaseModel):
    """Laboratory test definition with LOINC coding."""
    name: str
    loinc: str
    # Binary results
    result_positive: str | None = None
    result_negative: str | None = None
    probability_positive: float = Field(ge=0.0, le=1.0, default=0.5)
    # Numeric results
    value_type: str = Field(default="binary")  # "binary" or "numeric"
    unit: str | None = None
    normal_range_low: float | None = None
    normal_range_high: float | None = None
    abnormal_low_min: float | None = None
    abnormal_low_max: float | None = None
    abnormal_high_min: float | None = None
    abnormal_high_max: float | None = None
    probability_abnormal: float = Field(ge=0.0, le=1.0, default=0.3)
    age_ranges: list[dict] | None = None


class MedicationDefinition(BaseModel):
    """Medication with RxNorm coding and dosing."""
    agent: str
    rxnorm: str
    dose_mg_kg: float | None = None
    max_dose_mg: float | None = None
    frequency: str | None = None
    duration_days: int | None = None
    route: str = Field(default="oral")
    indication: str | None = None
    prn: bool = Field(default=False)
    age_min_months: int | None = None
```

---

## 6. Condition Key Lookup

**File:** `src/engines/engine.py` (Line ~274)

```python
def _get_condition_key(self, reason: str) -> str | None:
    """Map a display name or reason text to a condition key.

    This connects encounter reasons/chief complaints back to
    condition definitions in conditions.yaml.
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

    return None
```

The `_display_to_key` dictionary is built during engine initialization:

```python
# In __init__ or _load_conditions:
self._display_to_key = {}
for key, condition in self.conditions.items():
    display_name = condition.get('display_name', key)
    self._display_to_key[display_name.lower()] = key
    # Also add aliases
    for alias in condition.get('aliases', []):
        self._display_to_key[alias.lower()] = key
```

---

## 7. AAP Immunization Schedule

**File:** `knowledge/immunizations/aap_schedule.yaml`

```yaml
# Vaccine definitions with CVX codes
vaccines:
  HepB:
    name: Hepatitis B
    cvx_code: "08"
    series_doses: 3
    brand_examples: ["Engerix-B", "Recombivax HB"]

  DTaP:
    name: Diphtheria, Tetanus, Pertussis
    cvx_code: "20"
    series_doses: 5

  Hib:
    name: Haemophilus influenzae type b
    cvx_code: "17"
    series_doses: 4

  PCV:
    name: Pneumococcal conjugate
    cvx_code: "152"  # PCV15
    series_doses: 4

  IPV:
    name: Inactivated Poliovirus
    cvx_code: "10"
    series_doses: 4

  MMR:
    name: Measles, Mumps, Rubella
    cvx_code: "03"
    series_doses: 2

  VAR:
    name: Varicella
    cvx_code: "21"
    series_doses: 2

# Schedule by age
schedule:
  birth:
    - vaccine: HepB
      dose_number: 1
      notes: "Within 24 hours of birth"

  2_months:
    - { vaccine: DTaP, dose_number: 1 }
    - { vaccine: Hib, dose_number: 1 }
    - { vaccine: PCV, dose_number: 1 }
    - { vaccine: IPV, dose_number: 1 }
    - { vaccine: RV, dose_number: 1 }

  4_months:
    - { vaccine: DTaP, dose_number: 2 }
    - { vaccine: Hib, dose_number: 2 }
    - { vaccine: PCV, dose_number: 2 }
    - { vaccine: IPV, dose_number: 2 }
    - { vaccine: RV, dose_number: 2 }

  6_months:
    - { vaccine: DTaP, dose_number: 3 }
    - { vaccine: Hib, dose_number: 3 }
    - { vaccine: PCV, dose_number: 3 }
    - { vaccine: HepB, dose_number: 3 }
    - { vaccine: RV, dose_number: 3 }
    - { vaccine: Influenza, dose_number: 1, notes: "Annual, start at 6 months" }

  12_months:
    - { vaccine: MMR, dose_number: 1 }
    - { vaccine: VAR, dose_number: 1 }
    - { vaccine: HepA, dose_number: 1 }
    - { vaccine: Hib, dose_number: 4 }
    - { vaccine: PCV, dose_number: 4 }

  4_years:
    - { vaccine: DTaP, dose_number: 5 }
    - { vaccine: IPV, dose_number: 4 }
    - { vaccine: MMR, dose_number: 2 }
    - { vaccine: VAR, dose_number: 2 }

# Well-child visit schedule
well_child_schedule:
  - { age_description: "Newborn", age_days: 3 }
  - { age_description: "2 weeks", age_days: 14 }
  - { age_description: "1 month", age_months: 1 }
  - { age_description: "2 months", age_months: 2, immunizations: [DTaP, Hib, PCV, IPV, RV] }
  - { age_description: "4 months", age_months: 4 }
  - { age_description: "6 months", age_months: 6 }
  - { age_description: "9 months", age_months: 9 }
  - { age_description: "12 months", age_months: 12, immunizations: [MMR, VAR, HepA, Hib, PCV] }
  - { age_description: "15 months", age_months: 15 }
  - { age_description: "18 months", age_months: 18 }
  - { age_description: "2 years", age_months: 24 }
  - { age_description: "3 years", age_months: 36 }
  - { age_description: "4 years", age_months: 48, immunizations: [DTaP, IPV, MMR, VAR] }
  # Annual visits 5-21 years
```

---

## 8. Disease Arcs (Time Travel)

**File:** `knowledge/conditions/disease_arcs.yaml`

```yaml
atopic_march:
  name: "Atopic March"
  description: "Classic allergic disease progression"

  stages:
    - condition_key: "eczema"
      display_name: "Eczema"
      typical_age_range: [1, 12]  # months

    - condition_key: "food_allergy"
      display_name: "Food Allergy"
      typical_age_range: [6, 24]

    - condition_key: "asthma"
      display_name: "Asthma"
      typical_age_range: [24, 84]

    - condition_key: "allergic_rhinitis"
      display_name: "Allergic Rhinitis"
      typical_age_range: [48, 120]

  clinical_pearls:
    - "60% of infants with moderate-severe eczema develop asthma"
    - "Early aggressive eczema treatment may reduce progression"

rsv_to_asthma:
  name: "RSV to Reactive Airway to Asthma"
  stages:
    - { condition_key: "bronchiolitis", typical_age_range: [1, 12] }
    - { condition_key: "reactive_airway_disease", typical_age_range: [12, 48] }
    - { condition_key: "asthma", typical_age_range: [36, 96] }
  clinical_pearls:
    - "30-40% of infants hospitalized with RSV develop recurrent wheeze"

obesity_cascade:
  name: "Obesity to Metabolic Syndrome"
  stages:
    - { condition_key: "overweight" }
    - { condition_key: "obesity" }
    - { condition_key: "prediabetes" }
    - { condition_key: "type_2_diabetes" }

adhd_with_comorbidities:
  name: "ADHD + Internalizing Disorders"
  stages:
    - { condition_key: "adhd" }
    - { condition_key: "anxiety_disorder" }
    - { condition_key: "depression" }

recurrent_aom:
  name: "Recurrent Otitis Media"
  stages:
    - { condition_key: "otitis_media" }
    - { condition_key: "recurrent_otitis_media" }
    - { condition_key: "tympanostomy_tubes" }

functional_gi:
  name: "Functional GI Disorders"
  stages:
    - { condition_key: "infant_reflux" }
    - { condition_key: "constipation" }
    - { condition_key: "functional_abdominal_pain" }
```

---

## 9. Code System URIs

| System | URI | Example |
|--------|-----|---------|
| ICD-10-CM | `http://hl7.org/fhir/sid/icd-10-cm` | J06.9 |
| SNOMED CT | `http://snomed.info/sct` | 82272006 |
| RxNorm | `http://www.nlm.nih.gov/research/umls/rxnorm` | 723 |
| LOINC | `http://loinc.org` | 6690-2 |
| CVX | `http://hl7.org/fhir/sid/cvx` | 08 |

---

## 10. Key Enums

```python
class Sex(str, Enum):
    MALE = "male"
    FEMALE = "female"
    INTERSEX = "intersex"
    UNKNOWN = "unknown"

class ConditionStatus(str, Enum):
    ACTIVE = "active"
    RECURRENCE = "recurrence"
    RELAPSE = "relapse"
    INACTIVE = "inactive"
    REMISSION = "remission"
    RESOLVED = "resolved"

class Severity(str, Enum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"

class EncounterType(str, Enum):
    NEWBORN = "newborn"
    WELL_CHILD = "well-child"
    ACUTE_ILLNESS = "acute-illness"
    CHRONIC_FOLLOWUP = "chronic-followup"
    MEDICATION_CHECK = "medication-check"
    # ... more

class ComplexityTier(str, Enum):
    TIER_0 = "tier-0"  # Healthy
    TIER_1 = "tier-1"  # Single chronic
    TIER_2 = "tier-2"  # Multiple chronic
    TIER_3 = "tier-3"  # Complex
    TIER_4 = "tier-4"  # Technology-dependent
    TIER_5 = "tier-5"  # Medically fragile

class GrowthPattern(str, Enum):
    NORMAL = "normal"
    FAILURE_TO_THRIVE = "ftt"
    OBESITY = "obesity"
    PRETERM_CATCHUP = "preterm_catchup"
    GROWTH_DELAY = "growth_delay"

class Interpretation(str, Enum):
    NORMAL = "normal"
    ABNORMAL = "abnormal"
    CRITICAL = "critical"
    HIGH = "high"
    LOW = "low"
    POSITIVE = "positive"
    NEGATIVE = "negative"
```

---

## 11. Key Engine Methods

| Method | Location | Purpose |
|--------|----------|---------|
| `generate()` | engine.py:~150 | Main entry point |
| `_generate_encounter()` | engine.py:~1800 | Creates encounters |
| `_generate_immunizations()` | engine.py:~1975 | Vaccine logic (YAML-driven) |
| `_calculate_catchup_vaccines()` | engine.py:~2050 | Catch-up logic |
| `_inject_vaccine_hesitancy()` | engine.py:~2090 | Hesitancy scenarios |
| `_generate_labs()` | engine.py:~656 | Lab results (binary + numeric) |
| `_generate_numeric_lab()` | engine.py:~732 | Numeric lab helper |
| `_generate_imaging()` | engine.py:~817 | Imaging results |
| `_select_growth_pattern()` | engine.py:~2140 | Growth pattern selection |
| `_get_condition_key()` | engine.py:~274 | Map display name to key |
| `_apply_vitals_impact()` | engine.py:~1100 | Illness-aware vitals |
| `_select_symptoms()` | engine.py:~1150 | Probabilistic symptoms |
| `_generate_condition_physical_exam()` | engine.py:~1180 | Condition-specific PE |

---

## 12. File Summary

| File | Lines | Purpose |
|------|-------|---------|
| `src/engines/engine.py` | ~2700 | Main generation logic |
| `src/models/patient.py` | ~1200 | All Pydantic models |
| `src/exporters/fhir.py` | ~850 | FHIR R4 export |
| `src/exporters/markdown.py` | ~300 | Markdown export |
| `src/llm/client.py` | ~150 | Claude API client |
| `knowledge/conditions/conditions.yaml` | ~2500 | 50+ conditions |
| `knowledge/conditions/disease_arcs.yaml` | ~300 | Time Travel arcs |
| `knowledge/immunizations/aap_schedule.yaml` | ~350 | AAP schedule |
| `knowledge/growth/cdc_2000.py` | ~800 | CDC growth charts |
