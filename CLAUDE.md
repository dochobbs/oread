# CLAUDE.md - SynthPatient Project Context

## Project Overview

**SynthPatient** is a comprehensive synthetic patient generator that produces clinically coherent, longitudinal medical records. It generates FHIR R4 bundles, structured JSON, and human-readable Markdown documentation for AI evaluation, EMR demos, and medical education.

### Quick Start

```bash
# Install dependencies
pip install -e .

# Generate a patient
python cli.py generate --age 5

# Run the web server
python server.py
```

## Architecture

### Core Components

```
synthpatient/
├── cli.py                 # Click-based CLI (working)
├── server.py              # FastAPI web server (working)
├── web/                   # Web UI (working)
│
├── src/
│   ├── models/            # Pydantic data models (complete)
│   ├── llm/               # Claude API client (complete)
│   ├── engines/           # Generation engines
│   │   └── engine.py      # PedsEngine (working), AdultEngine (stub)
│   └── exporters/         # FHIR, JSON, Markdown (complete)
│
├── knowledge/
│   ├── conditions/        # Condition YAML definitions
│   ├── growth/            # CDC 2000 growth curves (complete)
│   └── immunizations/     # AAP schedule (complete)
│
└── prompts/               # LLM prompt templates (to build)
```

### Data Flow

1. **GenerationSeed** → Specifies constraints (age, conditions, complexity)
2. **PedsEngine.generate()** → Orchestrates generation
3. **Demographics** → Random or constrained patient identity
4. **LifeArc** → High-level health trajectory
5. **EncounterStubs** → Timeline of encounters to generate
6. **Encounters** → Full clinical encounters with vitals, exams, notes
7. **Patient** → Complete patient record
8. **Exporters** → FHIR R4, JSON, Markdown output

## What's Built (Working)

### Core Data Models (`src/models/patient.py`)
- Complete Pydantic v2 schema for all clinical entities
- Demographics, conditions, medications, allergies, immunizations
- Encounters with full clinical content
- Growth measurements with percentiles
- FHIR-compatible structure

### Generation Engine (`src/engines/engine.py`)
- **PedsEngine**: Generates pediatric patients birth-21 years
  - Well-child visit scheduling (AAP Bright Futures)
  - Acute illness distribution by age
  - Growth trajectory with percentile tracking
  - Immunization generation
  - Narrative note generation
  - Condition-based follow-up visits

### Knowledge Base
- **Growth curves** (`knowledge/growth/cdc_2000.py`): Full LMS method implementation
- **Immunization schedule** (`knowledge/immunizations/aap_schedule.yaml`): Current AAP/CDC schedule
- **Conditions**: Asthma and ADHD fully defined as examples

### Exporters
- **JSON**: Clean Pydantic export
- **FHIR R4**: Full bundle with Patient, Conditions, Medications, Immunizations, Encounters, Observations
- **Markdown**: Human-readable documentation

### CLI & Web
- Full CLI with generate, batch, view, export commands
- FastAPI server with complete REST API
- Web UI with generation, patient browsing, export

## What Needs Building

### Priority 1: More Conditions
The knowledge base needs more condition definitions. Each should follow the schema in `knowledge/conditions/_schema.yaml`.

**Pediatric conditions to add** (in `knowledge/conditions/peds/`):
- `allergy/eczema.yaml` - Atopic dermatitis
- `allergy/food_allergy.yaml` - Food allergies (peanut, milk, egg)
- `allergy/allergic_rhinitis.yaml` - Seasonal/perennial allergies
- `behavioral/anxiety.yaml` - Anxiety disorders
- `behavioral/depression.yaml` - Depression
- `behavioral/autism.yaml` - Autism spectrum disorder
- `gi/constipation.yaml` - Functional constipation
- `gi/gerd.yaml` - Reflux
- `ent/otitis_media.yaml` - Recurrent ear infections
- `ent/pharyngitis.yaml` - Strep throat
- `respiratory/croup.yaml` - Croup
- `respiratory/bronchiolitis.yaml` - Bronchiolitis
- `growth/obesity.yaml` - Obesity
- `growth/failure_to_thrive.yaml` - FTT

### Priority 2: LLM-Enhanced Generation
Currently the engine uses rule-based generation. Add LLM calls for:

1. **Persona Generation** (`src/generators/persona.py`)
   - Use Claude to create rich, coherent patient identities
   - Family dynamics, social situations, backstories
   - Cultural/demographic variation

2. **Life Arc Planning** (`src/generators/life_arc.py`)
   - Use Claude to plan major health events
   - Realistic condition onset patterns
   - Hospitalizations, surgeries

3. **Clinical Note Generation** (`src/generators/notes.py`)
   - Use Claude to generate realistic HPI, physical exams
   - Provider style variation
   - Assessment/plan reasoning

4. **Coherence Validation** (`src/validators/coherence.py`)
   - Use Claude to check for contradictions
   - Verify medication-diagnosis concordance
   - Check age-appropriateness

### Priority 3: Adult Engine
`src/engines/engine.py` has `AdultEngine` as a stub. Implement:
- Adult preventive care schedule
- Adult condition library (diabetes, hypertension, CAD, COPD, etc.)
- Medication patterns (chronic disease management)
- Geriatric considerations

### Priority 4: Enhanced Features
- **Archetypes**: Pre-built patient templates (`archetypes/peds/`, `archetypes/adult/`)
- **Batch generation**: Population-level cohort generation with distributions
- **Provider variation**: Different documentation styles
- **Fragmented records**: Realistic gaps and incomplete information

## Code Patterns to Follow

### Adding a New Condition

1. Create YAML in `knowledge/conditions/peds/{category}/{condition}.yaml`
2. Follow the schema structure (see asthma.yaml as example)
3. Include:
   - ICD-10 and SNOMED codes
   - Age of onset patterns
   - Comorbidities
   - Treatment protocols
   - Physical exam findings

### Adding a Generator

```python
# src/generators/my_generator.py
from src.llm import get_client
from src.models import SomeOutputModel

def generate_something(context: dict, seed: GenerationSeed) -> SomeOutputModel:
    client = get_client()
    
    prompt = f"""
    Given this patient context:
    {json.dumps(context, indent=2)}
    
    Generate [whatever] following these rules:
    - Rule 1
    - Rule 2
    """
    
    result = client.generate_structured(
        prompt=prompt,
        schema=SomeOutputModel,
        system="You are a clinical documentation expert...",
    )
    
    return result
```

### Adding an API Endpoint

```python
# In server.py
@app.post("/api/new-endpoint")
async def new_endpoint(request: RequestModel):
    # Implementation
    return ResponseModel(...)
```

## Testing

Run tests:
```bash
pytest tests/
```

Test a generation:
```bash
python cli.py generate --age 5 --conditions asthma
```

Test the web UI:
```bash
python server.py
# Open http://localhost:8000
```

## Key Design Decisions

1. **Peds cutoff at 22**: Pediatric engine handles 0-21 years (AAP definition)
2. **Complexity tiers**: 0=healthy, 1=single chronic, 2=multiple, 3=complex
3. **Growth tracking**: Uses CDC 2000 charts with LMS method
4. **Immunizations**: Current AAP/CDC schedule
5. **Note style**: Realistic clinical documentation with natural variation
6. **FHIR compliance**: US Core profiles where applicable

## Environment

- Python 3.11+
- Claude API key in `ANTHROPIC_API_KEY` environment variable
- Dependencies in `pyproject.toml`

## File Locations

- Patient JSON output: `./output/patient_{id}/patient.json`
- FHIR bundles: `./output/patient_{id}/fhir_bundle.json`
- Markdown docs: `./output/patient_{id}/patient.md`
- LLM cache: `~/.synthpatient/cache/`

## Common Tasks

### Generate a healthy infant
```bash
python cli.py generate --age-months 6 --complexity tier-0
```

### Generate a complex patient
```bash
python cli.py generate --age 14 --conditions "asthma,adhd,anxiety" --complexity tier-2
```

### Generate a batch
```bash
python cli.py batch --count 50 --distribution "healthy:60,tier1:25,tier2:12,tier3:3" -o ./patients/
```

### Run web server on different port
```bash
uvicorn server:app --port 3000 --reload
```

## Notes for Development

1. **Always test with real generation** - Don't just check if code compiles
2. **Validate clinical accuracy** - Medical content should be realistic
3. **Check FHIR compliance** - Use FHIR validators on output
4. **Maintain coherence** - Growth curves, ages, dates must align
5. **Preserve personality** - Generated patients should feel like real people

## Questions for Human Review

When making changes, consider:
- Is this clinically accurate?
- Does it maintain longitudinal coherence?
- Is the FHIR output valid?
- Does it work with the web UI?
