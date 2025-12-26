# CLAUDE.md - Oread Development Context

**Last Updated:** December 2025

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
│   ├── growth/            # CDC 2000 growth charts
│   └── immunizations/     # AAP vaccine schedule
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
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"age_months": 24}'

# Check the encounter
curl http://localhost:8000/api/patients/{id}
```

### Updating an Exporter

1. Modify the exporter in `src/exporters/`
2. Test with:
   ```bash
   curl http://localhost:8000/api/patients/{id}/export/{format}
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

## Recent Changes (December 2025)

**Learning Platform (Phase 1 Complete):**
1. Supabase integration with user authentication
2. Patient panels with mass generation
3. Single case generation with 5 difficulty levels
4. Login/signup UI in web interface

**Time Travel (Phase 2 Core Feature):**
5. TimeSnapshot, DiseaseArc, DecisionPoint models
6. Disease progression rules in conditions.yaml
7. Six disease arcs (Atopic March, RSV→Asthma, Obesity, ADHD, AOM, GI)
8. Timeline API endpoints
9. Timeline slider UI with key moment markers
10. Disease arc visualization with clinical pearls

**Earlier (v2.0):**
11. Restructured conditions.yaml to v2.0 schema
12. Added SNOMED, RxNorm, LOINC codes throughout
13. Implemented illness-aware vitals
14. Created weight-based medication dosing

## Notes for Development

1. **Test with real generation** - Don't just check if code compiles
2. **Validate clinical accuracy** - Medical content should be realistic
3. **Check exports** - Verify codes appear correctly in FHIR, C-CDA
4. **Update docs** - Keep documentation current after changes
5. **Use the schema** - Follow CONDITION_SCHEMA.md for new conditions
