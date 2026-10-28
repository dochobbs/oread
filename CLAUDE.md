# CLAUDE.md - Oread Development Context

**Last Updated:** April 2026

This file provides context for AI assistants (Claude Code, Cursor, etc.) working on the Oread project.

## Project Overview

**Oread** is a synthetic pediatric patient generator and **learning platform** that creates clinically coherent, longitudinal medical records. It produces FHIR R4 bundles, C-CDA 2.1 documents, structured JSON, and human-readable Markdown.

### Learning Platform Features (December 2025)

Oread is evolving from a patient generator into a **pediatric learning platform** with:

1. **User Authentication** - Supabase-based login with learner levels (NP student → Attending)
2. **Patient Panels** - Mass-generate patient panels for continuity clinic simulation
3. **Single Case Generation** - Generate new encounters for existing patients with difficulty scaling
4. **Time Travel** - See how conditions evolve across a patient's lifespan (killer feature!)

### Quick Commands

```bash
# Start development
cd /path/to/oread
source .venv/bin/activate

# Run the web server
python server.py
# or with reload:
uvicorn server:app --reload

# Generate a test patient
python cli.py generate --age 5 --conditions asthma

# Run tests
pytest tests/
```

## Project Structure

```
oread/
├── cli.py                 # Click-based CLI
├── server.py              # FastAPI web server
├── web/                   # Web UI (HTML/JS)
│
├── src/
│   ├── models/            # Pydantic v2 data models
│   │   └── patient.py     # All clinical models + condition schema models
│   ├── engines/           # Generation engines
│   │   └── engine.py      # PedsEngine - main generation logic
│   ├── exporters/         # Output formatters
│   │   ├── json_export.py # JSON export
│   │   ├── fhir.py        # FHIR R4 Bundle
│   │   ├── ccda.py        # C-CDA 2.1 document
│   │   └── markdown.py    # Human-readable Markdown
│   └── llm/               # Claude API client (optional)
│
├── knowledge/
│   ├── conditions/        # Condition definitions
│   │   ├── conditions.yaml # Main conditions with SNOMED/RxNorm/LOINC + progression rules
│   │   └── disease_arcs.yaml # Disease arc definitions for Time Travel
│   ├── growth/            # WHO 0-24mo, CDC 2000 2-20y, CDC 2022 extended BMI (data/ = vendored source CSVs)
│   └── immunizations/     # 2026 AAP schedule (rules engine: src/engines/immunizations.py)
│
├── src/db/                # Database layer (Supabase)
│   ├── client.py          # Supabase connection
│   └── repositories.py    # User, Panel, Patient repos
│
├── docs/                  # Documentation
│   ├── QUICKSTART.md      # Getting started guide
│   ├── CONDITION_SCHEMA.md # Condition YAML schema reference
│   ├── ARCHITECTURE.md    # System architecture
│   └── TIME_TRAVEL_DESIGN.md # Time Travel feature design
│
└── tests/                 # Test suite
```

## What's Built (Working)

### Time Travel Feature (December 2025)

The **killer feature** for medical education - learners can "scrub" through time to watch diseases progress.

**Backend:**
- `TimeSnapshot` model - Patient state at a point in time
- `DiseaseArc` model - Progression of related conditions with stages
- `generate_timeline()` method in PedsEngine
- API endpoints: `GET /timeline`, `GET /timeline/at/{age}`

**Frontend:**
- Timeline slider in patient header (scrub from birth to current age)
- Orange markers for key moments (new conditions, medication changes)
- "What Changed" panel showing transitions
- Timeline tab with disease arc visualization + clinical pearls

**Disease Arcs Implemented:**
| Arc | Stages |
|-----|--------|
| Atopic March | Eczema → Food allergy → Asthma → Allergic rhinitis |
| RSV → Asthma | Bronchiolitis → Reactive Airway → Asthma |
| Obesity Cascade | Overweight → Obesity → Prediabetes → T2DM |
| ADHD + Internalizing | ADHD → Anxiety → Depression |
| Recurrent AOM | First AOM → Recurrent AOM → Tubes |
| Functional GI | Infant reflux → Constipation → Functional abdominal pain |

**Key Files:**
- `knowledge/conditions/disease_arcs.yaml` - Arc definitions
- `src/engines/engine.py:3292` - `generate_timeline()` method
- `server.py:1064` - Timeline API endpoints
- `web/index.html` - Timeline UI components

### Learning Platform (December 2025)

- **Authentication**: Supabase login with JWT tokens, learner levels
- **Patient Panels**: Mass-generate panels, associate with users
- **Single Case Generation**: New encounters with 5 difficulty levels
  - Level 1 (Routine): Straightforward presentations
  - Level 2 (Standard): Common illness, clear diagnosis
  - Level 3 (Complex): Multiple factors, decisions needed
  - Level 4 (Challenging): Atypical presentations
  - Level 5 (Zebra): Rare or unexpected diagnoses

### Condition-Aware Generation (v2.0 - December 2025)

The engine now generates clinically accurate content based on condition definitions:

1. **Illness-Aware Vitals** (`engine.py:_apply_vitals_impact`)
   - Fever ranges based on condition
   - Heart rate and respiratory rate multipliers
   - Low SpO2 for respiratory conditions

2. **Probabilistic Symptoms** (`engine.py:_select_symptoms`)
   - Symptoms selected based on probability
   - Age-appropriate filtering

3. **Condition-Specific Physical Exam** (`engine.py:_generate_condition_physical_exam`)
   - PE findings matched to diagnosis
   - Probabilistic selection

4. **Lab Results** (`engine.py:_generate_labs`)
   - LOINC-coded lab tests
   - Positive/negative results based on probability

5. **Weight-Based Prescriptions** (`engine.py:_generate_acute_illness_plan`)
   - RxNorm-coded medications
   - Dose calculated from mg/kg
   - Max dose limits enforced

### Updated Conditions (10 Priority Conditions)

All in `knowledge/conditions/conditions.yaml`:

| Condition | SNOMED | Key Features |
|-----------|--------|--------------|
| otitis_media | 65363002 | TM findings, Amoxicillin |
| pharyngitis | 405737000 | Rapid strep (LOINC), antibiotics |
| bronchiolitis | 4120002 | Low SpO2, supportive care |
| pneumonia | 233604007 | CXR, elevated RR, antibiotics |
| urinary_tract_infection | 68566005 | UA/culture, antibiotics |
| viral_gastroenteritis | 25374005 | Ondansetron, hydration |
| asthma | 195967001 | Albuterol, steroids |
| eczema | 43116000 | Topical steroids, moisturizers |
| conjunctivitis | 9826008 | Ophthalmic drops |
| croup | 71186008 | Dexamethasone, stridor |

### Exporters

| Exporter | Status | Notes |
|----------|--------|-------|
| JSON | Working | Clean Pydantic export |
| FHIR R4 | Working | US Core compatible |
| C-CDA 2.1 | Working | Proper OID mappings |
| Markdown | Working | Shows SNOMED/RxNorm codes |

### Pydantic Models (src/models/patient.py)

New models added for condition schema:
- `VitalsImpact` - Vitals modifiers
- `SymptomDefinition` - Probabilistic symptoms
- `PhysicalExamFindingDef` - PE findings
- `LabDefinition` - Lab tests with LOINC
- `MedicationDefinition` - Meds with RxNorm
- `ConditionDefinition` - Complete condition schema

### Quality & Validation System (January 2026)

The engine now includes comprehensive validation to ensure clinical accuracy:

**Age Gates (`engine.py:_hardcoded_age_gates`):**
| Condition | Min Age | Max Age |
|-----------|---------|---------|
| ADHD | 48mo | - |
| Anxiety | 36mo | - |
| Depression | 72mo | - |
| Asthma | 12mo | - |
| Bronchiolitis | 0mo | 24mo |
| Prediabetes | 120mo | - |
| Learning Disorder | 60mo | - |
| Enuresis | 60mo | - |
| Obesity | 24mo | - |
| OSA | 24mo | - |

**Validation Checks:**
- `_validate_condition_age()` - Validates onset age against min/max constraints
- Temporal validation - No dates before DOB or after today
- Medication coherence - Required meds for conditions (T1D→insulin, Kawasaki→aspirin)
- ICD-10 validation - no R69 fallback: an uncodable condition raises (`_resolve_icd10`)

**Key Files:**
- `src/validators/patient_validator.py` - Validation logic
- `src/knowledge/condition_service.py` - Condition lookup with web search fallback
- `src/knowledge/exa_client.py` - Exa API integration for ICD-10 lookup

## Key Code Locations

### Condition Processing

```
engine.py:
├── _get_condition_key()           # Line ~1050 - Map display name to YAML key
├── _apply_vitals_impact()         # Line ~1100 - Modify vitals for illness
├── _select_symptoms()             # Line ~1150 - Probabilistic symptom selection
├── _generate_condition_physical_exam() # Line ~1180 - Condition-specific PE
├── _generate_labs()               # Line ~1230 - Lab results with LOINC
└── _generate_acute_illness_plan() # Line ~1280 - Treatment with RxNorm
```

### Code System Mappings

In `src/exporters/ccda.py`:
```python
system_map = {
    "http://hl7.org/fhir/sid/icd-10-cm": ("2.16.840.1.113883.6.90", "ICD-10-CM"),
    "http://snomed.info/sct": ("2.16.840.1.113883.6.96", "SNOMED CT"),
    "http://www.nlm.nih.gov/research/umls/rxnorm": ("2.16.840.1.113883.6.88", "RxNorm"),
    "http://loinc.org": ("2.16.840.1.113883.6.1", "LOINC"),
}
```

## Common Development Tasks

### Adding a New Condition

1. Edit `knowledge/conditions/conditions.yaml`
2. Follow the schema in `docs/CONDITION_SCHEMA.md`
3. Include:
   - SNOMED code (required)
   - ICD-10 codes
   - vitals_impact (for acute conditions)
   - symptoms with probabilities
   - physical_exam findings
   - labs with LOINC codes (if applicable)
   - medications with RxNorm codes

Example:
```yaml
new_condition:
  display_name: "Condition Name"
  billing_codes:
    snomed: "123456789"
    icd10: ["X00.0"]
  category: acute
  system: respiratory
  vitals_impact:
    temp_f: [99.0, 101.0]
  presentation:
    symptoms:
      - { name: "symptom", probability: 0.8 }
    physical_exam:
      - { system: "respiratory", finding: "Finding text", probability: 0.9 }
  treatment:
    medications:
      - agent: "Drug"
        rxnorm: "12345"
        dose_mg_kg: 10
        frequency: "BID"
```

### Testing a New Condition

```bash
# Generate patients until you get the condition
python cli.py generate --age 2

# Or use the API
curl -X POST http://localhost:9104/api/generate \
  -H "Content-Type: application/json" \
  -d '{"age_months": 24}'

# Check the encounter
curl http://localhost:9104/api/patients/{id}
```

### Updating an Exporter

1. Modify the exporter in `src/exporters/`
2. Test with:
   ```bash
   curl http://localhost:9104/api/patients/{id}/export/{format}
   ```

## Code Style

- Python 3.11+
- Pydantic v2 for models
- Type hints on all functions
- 2-space indentation (project standard)
- Google-style docstrings

## Medical Coding Resources

| System | Resource |
|--------|----------|
| SNOMED CT | https://browser.ihtsdotools.org/ |
| ICD-10 | https://www.icd10data.com/ |
| RxNorm | https://mor.nlm.nih.gov/RxNav/ |
| LOINC | https://loinc.org/search/ |
| CVX | https://www.cdc.gov/vaccines/programs/iis/cvx/ |

## What Needs Building

See `docs/ROADMAP.md` for detailed phasing. Summary:

### Phase 2 (In Progress): Clinical Depth
- ✅ Time Travel - Disease arc visualization
- ⬜ Vaccine Engine - Catch-up calculator, hesitancy scenarios
- ⬜ Growth/Development - Normal variations, screening integration
- ⬜ Results Expansion - Comprehensive lab panels, radiology

### Phase 3: Learning Experience
- ⬜ Echo (AI Attending) - Socratic questioning, clinical feedback
- ⬜ Documentation Practice - Note writing with AI feedback
- ⬜ Billing/Coding Practice - E&M levels, ICD-10 exercises
- ⬜ Edge Cases - Expert-curated zebras

### Phase 4: Platform & Retention
- ⬜ Competency Mapping - ACGME/AAP milestones
- ⬜ Spaced Repetition - Case review scheduling
- ⬜ Artifacts - School forms, 504 plans, prior auth

## Testing

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest --cov=src tests/

# Test specific module
pytest tests/test_engine.py
```

## Runtime Modes (October 2026)

`src/runtime_config.py` picks the mode; defaults to `platform` when Supabase is configured, else `standalone`.

| | Standalone | Platform |
|---|---|---|
| Auth | None (single local user) | Bearer token on every patient route; Metis sends one |
| Bind | Loopback only; refuses `0.0.0.0` | Any |
| Accounts/panels | 501 | Supabase-backed, queries run as the caller (RLS) |

Env: `OREAD_MODE`, `OREAD_HOST`, `OREAD_PORT`, `OREAD_CORS_ORIGINS` (comma list, no `*`), `OREAD_MAX_PATIENTS`, `OREAD_MAX_JOBS`, `OREAD_MAX_CONCURRENT_GENERATIONS`, `OREAD_GENERATION_QUEUE_TIMEOUT`.

Generated patients live in an in-memory, owner-scoped LRU store (`src/store.py`); a restart loses them. Never sign in on the shared Supabase client: use `new_anon_client()` for auth flows and `user.db()` for per-user queries.

## Environment

- Python 3.11+
- Virtual environment: `.venv/`
- Dependencies: `pyproject.toml`
- Optional: `ANTHROPIC_API_KEY` for LLM features

## Documentation to Update

After significant changes, update:
1. `README.md` - User-facing features
2. `CLAUDE.md` - This file (dev context)
3. `docs/CONDITION_SCHEMA.md` - If schema changes
4. `docs/ARCHITECTURE.md` - If system design changes

## Clinical Hardening (October 2026)

Goal spec and progress: `docs/superpowers/specs/2026-10-27-oread-clinical-hardening-goal.md`.

- **Clinical invariant suite** (`tests/invariants.py`, `tests/test_clinical_invariants.py`): generates a seeded peds + adult cohort and asserts on content (vaccine series/ages, duplicate therapy, mg/kg ceilings, required meds, resolution, codes, vitals/HPI, FHIR R4B validity, C-CDA structure, cross-format parity, standalone mode). `OREAD_COHORT=full` for 120 peds + 40 adults. Known bugs go in as `xfail(strict=True)` and the marker is removed with the fix.
- **Immunizations**: due-as-of-visit rules (`src/engines/immunizations.py`) with min age / interval / max age and catch-up; 2026 AAP schedule.
- **Peds dosing**: `src/engines/peds_dosing.py` (weight/age-based, sourced). RxNorm codes pinned by `tests/test_rxnorm_codes.py`; verify new codes on RxNav before adding.
- **Adult engine**: `adult/adult_meds.py` + `adult_medications.yaml` (verified RxCUIs, per-drug sig), `adult/adult_labs.py` (component panels, LOINC).
- **Models**: clinical models are `extra="forbid"`; load stored records with `Patient.from_stored()`.
- **Exporters**: shared `sig.py`, `codes.py`, `measurements.py`; C-CDA 2.1 / FHIR R4 conformant; refusals, PRN, resolution encoded.
- **Reproducibility**: per-engine `self.rng` and injectable `self.today`; same seed gives the same patient.
- **No-LLM mode** writes templated HPI and narrative notes; messiness applies to both.
- Conditions are coded ICD-10-CM (SNOMED only where the YAML has it).

## Recent Changes (January 2026)

**Quality & Validation System (v2.1):**
1. Age gate validation with min/max constraints (ADHD ≥48mo, bronchiolitis ≤24mo)
2. Hardcoded age gates for 11 critical conditions in `_hardcoded_age_gates`
3. `_validate_condition_age()` helper method for consistent validation
4. Future date prevention (onset dates clamped to ≤ today)
5. Medication coherence enforcement (Kawasaki → aspirin, T1D → insulin)
6. Validation blocking - raises exception on unfixable critical issues
7. Exa web search integration for unknown condition ICD-10 lookup
8. Display name normalization (no more snake_case in output)
9. Comorbidity-added conditions now validated through age gates
10. Acute illness age filtering in `_get_seasonal_illness()`

**December 2025:**

**Learning Platform (Phase 1 Complete):**
- Supabase integration with user authentication
- Patient panels with mass generation
- Single case generation with 5 difficulty levels
- Login/signup UI in web interface

**Time Travel (Phase 2 Core Feature):**
- TimeSnapshot, DiseaseArc, DecisionPoint models
- Disease progression rules in conditions.yaml
- Six disease arcs (Atopic March, RSV→Asthma, Obesity, ADHD, AOM, GI)
- Timeline API endpoints
- Timeline slider UI with key moment markers
- Disease arc visualization with clinical pearls

**Earlier (v2.0):**
- Restructured conditions.yaml to v2.0 schema
- Added SNOMED, RxNorm, LOINC codes throughout
- Implemented illness-aware vitals
- Created weight-based medication dosing

## Metis Integration

Oread is part of the **MedEd Platform**, orchestrated by Metis.

### Platform Overview

| Project | Greek Name | Port | Purpose |
|---------|------------|------|---------|
| synpat | **Oread** | 9104 | Patient generation |
| synvoice | Syrinx | 9103 | Encounter scripts |
| synchart | Mneme | 9102 | EMR interface |
| echo | Echo | 9101 | AI tutor |
| metis | Metis | 9100 | Portal (planned) |
| athena | Athena | 9105 | Curriculum & knowledge |

### Shared Models

Oread uses shared models generated by Metis:

```bash
# Regenerate shared models after schema changes
cd /Users/dochobbs/consult/MedEd/metis/shared
python sync.py --project oread
```

Generated models location: `src/models/_generated/context.py`

### Starting with Metis

```bash
# Start all MedEd services at once
cd /Users/dochobbs/consult/MedEd/metis/scripts
./start-all.sh

# Check status
./status.sh

# Stop all
./stop-all.sh
```

### Standalone Mode

Oread can run independently without Metis:

```bash
cd /Users/dochobbs/consult/MedEd/synpat
source .venv/bin/activate
python server.py
# API at http://localhost:9104
```

### Cross-Service Integration (February 2026)

Oread is the **data source** for the entire platform. Key integration points:

| Endpoint | Purpose | Consumer |
|----------|---------|----------|
| `GET /api/patients/{id}/context` | Flat `PatientContext` for Echo | Metis Dashboard (Echo chat) |
| `GET /api/patients/{id}?format=json` | Full patient record | Metis → Mneme import, Metis → Syrinx import |
| `POST /api/generate` | Generate new patient | Metis Dashboard |

**New file:** `src/exporters/context_export.py` — `patient_to_context()` converts nested `Patient` to Echo's flat `PatientContext` format (flat `name`, `age_years`, `age_months`, `sex`, `source: "oread"`).

See **`docs/INTEGRATION.md`** for the full cross-service integration guide, architecture diagrams, and test procedures.

### Data Flow

```
Oread → [Patient JSON] → Mneme (import)
                       → Syrinx (patient profile)
                       → Echo (patient context)
```

## Notes for Development

1. **Test with real generation** - Don't just check if code compiles
2. **Validate clinical accuracy** - Medical content should be realistic
3. **Check exports** - Verify codes appear correctly in FHIR, C-CDA
4. **Update docs** - Keep documentation current after changes
5. **Use the schema** - Follow CONDITION_SCHEMA.md for new conditions
