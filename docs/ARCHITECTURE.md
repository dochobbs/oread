# Oread Architecture

This document describes the system architecture, data flow, and key components of Oread.

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Interfaces                          │
├─────────────────┬─────────────────┬─────────────────────────────┤
│    CLI (cli.py) │ Web UI (web/)   │     API (server.py)         │
└────────┬────────┴────────┬────────┴────────────┬────────────────┘
         │                 │                     │
         └─────────────────┼─────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Generation Engine                             │
│                   (src/engines/engine.py)                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ Demographics│  │ Encounters  │  │ Conditions  │              │
│  │  Generator  │  │  Generator  │  │  Processor  │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└────────────────────────────┬────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  Knowledge Base │ │   Data Models   │ │    Exporters    │
│   (knowledge/)  │ │ (src/models/)   │ │ (src/exporters/)│
│                 │ │                 │ │                 │
│ - Conditions    │ │ - Patient       │ │ - JSON          │
│ - Growth Charts │ │ - Encounter     │ │ - FHIR R4       │
│ - Immunizations │ │ - Medication    │ │ - C-CDA 2.1     │
│                 │ │ - LabResult     │ │ - Markdown      │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

## Component Details

### 1. User Interfaces

#### CLI (`cli.py`)
Command-line interface using Click. Supports:
- `generate` - Create a single patient
- `batch` - Generate multiple patients
- `view` - Display patient data
- `export` - Export to file

#### Web UI (`web/`)
Static HTML/JS interface served by FastAPI. Features:
- Interactive patient generation
- Patient browsing and search
- Export to all formats
- Real-time display

#### API (`server.py`)
FastAPI REST API. Key endpoints:
- `POST /api/generate` - Generate patient
- `GET /api/patients/{id}` - Get patient data
- `GET /api/patients/{id}/export/{format}` - Export patient

### 2. Generation Engine (`src/engines/engine.py`)

The `PedsEngine` class orchestrates patient generation:

```python
class PedsEngine:
    def generate(self, seed: GenerationSeed) -> Patient:
        # 1. Create demographics
        demographics = self._generate_demographics(seed)

        # 2. Build encounter timeline
        encounter_stubs = self._build_encounter_timeline(seed, demographics)

        # 3. Generate each encounter
        encounters = []
        for stub in encounter_stubs:
            encounter = self._generate_encounter_from_stub(stub, ...)
            encounters.append(encounter)

        # 4. Assemble patient record
        return Patient(demographics=demographics, encounters=encounters, ...)
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `_generate_demographics()` | Create patient identity |
| `_build_encounter_timeline()` | Schedule well-visits and illness visits |
| `_generate_encounter_from_stub()` | Generate full encounter from stub |
| `_generate_vitals()` | Create age-appropriate vital signs |
| `_apply_vitals_impact()` | Modify vitals based on illness |
| `_select_symptoms()` | Probabilistically select symptoms |
| `_generate_condition_physical_exam()` | Create condition-specific PE findings |
| `_generate_labs()` | Generate lab results with LOINC codes |
| `_generate_acute_illness_plan()` | Create treatment plan with RxNorm meds |

### 3. Knowledge Base (`knowledge/`)

#### Conditions (`knowledge/conditions/conditions.yaml`)

YAML-based condition definitions with:
- SNOMED/ICD-10 codes
- Vitals impact modifiers
- Probabilistic symptoms and PE findings
- LOINC-coded lab definitions
- RxNorm-coded treatment protocols

See [CONDITION_SCHEMA.md](CONDITION_SCHEMA.md) for complete schema.

#### Growth Charts (`knowledge/growth/`)

CDC 2000 growth charts with LMS method for:
- Weight-for-age
- Length/height-for-age
- Head circumference-for-age
- BMI-for-age (2+ years)

#### Immunizations (`knowledge/immunizations/`)

AAP/CDC immunization schedule including:
- Vaccine timing by age
- CVX codes
- Dose numbers and series

### 4. Data Models (`src/models/patient.py`)

Pydantic v2 models for all clinical entities:

```python
# Core models
Patient              # Complete patient record
Demographics         # Patient identity
Encounter            # Clinical visit

# Clinical content
Condition            # Diagnosis with codes
Medication           # Prescription with RxNorm
Allergy              # Allergy/intolerance
Immunization         # Vaccine with CVX
LabResult            # Lab with LOINC
VitalSigns           # Vital measurements
PhysicalExam         # PE findings

# Condition schema models
VitalsImpact         # How condition affects vitals
SymptomDefinition    # Symptom with probability
PhysicalExamFindingDef # PE finding with probability
LabDefinition        # Lab test definition
MedicationDefinition # Medication with dosing
ConditionDefinition  # Complete condition schema
```

### 5. Exporters (`src/exporters/`)

#### JSON (`json_export.py`)
Clean Pydantic export using `model_dump()`.

#### FHIR R4 (`fhir.py`)
Generates FHIR Bundle with:
- Patient (US Core profile)
- Condition (with SNOMED/ICD-10)
- MedicationStatement (with RxNorm)
- Immunization (with CVX)
- Encounter
- Observation (vitals, growth)

#### C-CDA 2.1 (`ccda.py`)
Generates HL7 C-CDA document with:
- Proper OID mapping for code systems
- Problem List section
- Medications section
- Immunizations section
- Results section (labs)
- Encounters section

#### Markdown (`markdown.py`)
Human-readable documentation with:
- Full patient summary
- Encounter history
- Growth charts
- Code annotations (SNOMED, RxNorm)

## Data Flow

### Patient Generation Flow

```
GenerationSeed
    │
    ├─ age_months: 18
    ├─ sex: female
    └─ complexity: tier-1
           │
           ▼
    ┌──────────────┐
    │ PedsEngine   │
    │ .generate()  │
    └──────┬───────┘
           │
    ┌──────┴──────┐
    ▼             ▼
Demographics  Encounter Timeline
    │             │
    │      ┌──────┴──────────────────┐
    │      │                         │
    │      ▼                         ▼
    │  Well Visits              Illness Visits
    │  (scheduled)              (probabilistic)
    │      │                         │
    └──────┼─────────────────────────┘
           │
           ▼
    For each encounter:
    ┌────────────────────────────────────────┐
    │ 1. Generate vitals (age-appropriate)   │
    │ 2. Apply vitals_impact (if illness)    │
    │ 3. Select symptoms (probabilistic)     │
    │ 4. Generate PE (condition-specific)    │
    │ 5. Generate labs (if applicable)       │
    │ 6. Create treatment plan (RxNorm)      │
    │ 7. Generate narrative note             │
    └────────────────────────────────────────┘
           │
           ▼
    ┌──────────────┐
    │   Patient    │
    │   Object     │
    └──────────────┘
           │
           ▼
    ┌──────────────┐
    │  Exporters   │
    ├──────────────┤
    │ JSON │ FHIR  │
    │ CCDA │ MD    │
    └──────────────┘
```

### Condition Processing Flow

```
Encounter with "Acute Otitis Media"
           │
           ▼
    _get_condition_key()
    Maps "Acute Otitis Media" → "otitis_media"
           │
           ▼
    Load from conditions.yaml
    ┌─────────────────────────────────────┐
    │ otitis_media:                       │
    │   vitals_impact:                    │
    │     temp_f: [100.4, 102.5]          │
    │   presentation:                     │
    │     physical_exam:                  │
    │       - system: heent               │
    │         finding: "TM bulging"       │
    │   treatment:                        │
    │     medications:                    │
    │       - agent: Amoxicillin          │
    │         rxnorm: "723"               │
    └─────────────────────────────────────┘
           │
    ┌──────┴──────────────────┐
    │                         │
    ▼                         ▼
_apply_vitals_impact()   _generate_condition_physical_exam()
Temp: 101.2°F            HEENT: "TM erythematous and bulging"
HR: 115 (1.1x normal)
           │                         │
           └─────────┬───────────────┘
                     ▼
         _generate_acute_illness_plan()
         ┌────────────────────────────────┐
         │ Amoxicillin (RxNorm:723)       │
         │ Dose: 90 mg/kg → 810mg         │
         │ (patient weight: 9kg)          │
         │ Frequency: BID                 │
         │ Duration: 10 days              │
         └────────────────────────────────┘
```

## Code Systems Mapping

### Internal to FHIR

| Internal | FHIR System URI |
|----------|-----------------|
| SNOMED | `http://snomed.info/sct` |
| ICD-10 | `http://hl7.org/fhir/sid/icd-10-cm` |
| RxNorm | `http://www.nlm.nih.gov/research/umls/rxnorm` |
| LOINC | `http://loinc.org` |
| CVX | `http://hl7.org/fhir/sid/cvx` |

### FHIR to C-CDA OIDs

| FHIR System | C-CDA OID |
|-------------|-----------|
| SNOMED CT | 2.16.840.1.113883.6.96 |
| ICD-10-CM | 2.16.840.1.113883.6.90 |
| RxNorm | 2.16.840.1.113883.6.88 |
| LOINC | 2.16.840.1.113883.6.1 |
| CVX | 2.16.840.1.113883.12.292 |

## Extension Points

### Adding New Conditions

1. Add to `knowledge/conditions/conditions.yaml`
2. Follow schema in [CONDITION_SCHEMA.md](CONDITION_SCHEMA.md)
3. Engine automatically picks up new conditions

### Adding New Exporters

1. Create `src/exporters/my_exporter.py`
2. Implement export function taking `Patient` object
3. Add to `src/exporters/__init__.py`
4. Add route in `server.py`

### Adding New Encounter Types

1. Add to `EncounterType` enum in `src/models/patient.py`
2. Add generation logic in `src/engines/engine.py`
3. Add scheduling logic in `_build_encounter_timeline()`

## Performance Considerations

- Condition YAML loaded once at engine initialization
- Display name to key mapping cached in `_display_to_key`
- Growth chart calculations use vectorized numpy operations
- Patient objects stored in memory during server session
