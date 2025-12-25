# CLAUDE.md - Oread Development Context

**Last Updated:** December 2025

This file provides context for AI assistants (Claude Code, Cursor, etc.) working on the Oread project.

## Project Overview

**Oread** is a synthetic pediatric patient generator that creates clinically coherent, longitudinal medical records. It produces FHIR R4 bundles, C-CDA 2.1 documents, structured JSON, and human-readable Markdown.

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
│   │   └── conditions.yaml # Main conditions file with SNOMED/RxNorm/LOINC
│   ├── growth/            # CDC 2000 growth charts
│   └── immunizations/     # AAP vaccine schedule
│
├── docs/                  # Documentation
│   ├── QUICKSTART.md      # Getting started guide
│   ├── CONDITION_SCHEMA.md # Condition YAML schema reference
│   └── ARCHITECTURE.md    # System architecture
│
└── tests/                 # Test suite
```

## What's Built (Working)

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

### Priority 1: More Conditions
Add remaining pediatric conditions following the v2.0 schema:
- `allergy/food_allergy.yaml`
- `behavioral/anxiety.yaml`
- `behavioral/autism.yaml`
- `gi/constipation.yaml`
- `ent/sinusitis.yaml`
- More respiratory conditions

### Priority 2: Chronic Condition Logic
Currently acute illness is well-handled. Need:
- Chronic condition follow-up visits
- Medication refills
- Lab monitoring

### Priority 3: Adult Engine
`AdultEngine` stub exists but needs implementation.

### Priority 4: Enhanced Features
- Provider style variation
- Fragmented/incomplete records
- Multi-provider care

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

1. Restructured conditions.yaml to v2.0 schema
2. Added SNOMED, RxNorm, LOINC codes throughout
3. Implemented illness-aware vitals
4. Added probabilistic symptoms and PE findings
5. Created weight-based medication dosing
6. Updated all 4 exporters for code systems
7. Created comprehensive documentation

## Notes for Development

1. **Test with real generation** - Don't just check if code compiles
2. **Validate clinical accuracy** - Medical content should be realistic
3. **Check exports** - Verify codes appear correctly in FHIR, C-CDA
4. **Update docs** - Keep documentation current after changes
5. **Use the schema** - Follow CONDITION_SCHEMA.md for new conditions
