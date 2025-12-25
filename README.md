# Oread

**Generate realistic, clinically coherent synthetic patient records**

Oread creates longitudinal pediatric medical records for AI evaluation, EMR demos, and medical education. It produces FHIR R4 bundles, C-CDA 2.1 documents, structured JSON, and human-readable Markdown.

![Oread](https://img.shields.io/badge/version-0.2.0-blue)
![Python](https://img.shields.io/badge/python-3.11+-green)
![License](https://img.shields.io/badge/license-MIT-gray)

---

## Features

- **Clinically coherent** - Growth curves, immunizations, and conditions follow realistic patterns
- **Illness-aware vitals** - Fever, tachycardia, low SpO2 based on condition
- **Condition-specific findings** - Physical exam findings match the diagnosis
- **Medical coding standards** - SNOMED CT, RxNorm, LOINC, ICD-10, CVX codes
- **Weight-based dosing** - Pediatric medication doses calculated correctly
- **Multiple exports** - FHIR R4, C-CDA 2.1, JSON, Markdown
- **Web interface** - Interactive generation and export
- **CLI tool** - Scriptable batch generation
- **LLM-enhanced narratives** - Natural clinical notes powered by Claude API
- **Patient messages** - Portal and phone messages with office replies (FHIR Communication)
- **Medical history** - Resolved conditions and past medications from acute visits
- **Chart messiness** - Realistic artifacts like abbreviations, copy-forward errors, and dictation mistakes

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/oread.git
cd oread

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .
```

### Generate a Patient

```bash
# Quick random generation
python cli.py generate

# Specific age and sex
python cli.py generate --age 2 --sex female

# With conditions
python cli.py generate --age 10 --conditions "asthma,adhd"

# Complex patient
python cli.py generate --complexity tier-3
```

### Web Interface

```bash
# Start the web server
python server.py

# Open http://localhost:8000
```

## Output Formats

### JSON
Clean, structured patient data with full clinical detail.

### FHIR R4
Standards-compliant Bundle with Patient, Condition, MedicationStatement, Immunization, Encounter, Observation, and Communication resources.

### C-CDA 2.1
HL7 Consolidated CDA document with proper OIDs for SNOMED, RxNorm, LOINC, and ICD-10.

### Markdown
Human-readable documentation with full encounter history.

## Medical Coding

Oread uses industry-standard medical terminologies:

| System | Use | Example |
|--------|-----|---------|
| **SNOMED CT** | Diagnoses | Acute Otitis Media: 65363002 |
| **ICD-10-CM** | Billing codes | Otitis Media: H66.90 |
| **RxNorm** | Medications | Amoxicillin: 723 |
| **LOINC** | Lab tests | Rapid Strep: 78012-2 |
| **CVX** | Vaccines | DTaP: 20 |

## Condition-Aware Generation

Each condition in Oread includes:

```yaml
otitis_media:
  display_name: "Acute Otitis Media"
  billing_codes:
    snomed: "65363002"
    icd10: ["H66.90"]

  vitals_impact:
    temp_f: [100.4, 102.5]      # Fever range
    hr_multiplier: 1.1           # Mild tachycardia

  presentation:
    symptoms:
      - { name: "ear pain", probability: 0.9 }
      - { name: "fever", probability: 0.7 }
    physical_exam:
      - { system: "heent", finding: "TM erythematous and bulging", probability: 0.85 }

  treatment:
    medications:
      - agent: "Amoxicillin"
        rxnorm: "723"
        dose_mg_kg: 90
        frequency: "BID"
        duration_days: 10
```

See [docs/CONDITION_SCHEMA.md](docs/CONDITION_SCHEMA.md) for the complete schema.

## Complexity Tiers

| Tier | Description | Example |
|------|-------------|---------|
| **Tier 0** | Healthy | Well visits only, no chronic conditions |
| **Tier 1** | Single chronic | Asthma, ADHD, or eczema |
| **Tier 2** | Two conditions | Asthma + allergies |
| **Tier 3** | Three conditions | Asthma + allergies + anxiety |
| **Tier 4** | Four+ conditions | Multiple organ systems involved |
| **Tier 5** | Complex/fragile | Multiple specialists, technology-dependent |

## Chart Messiness

Simulate realistic EHR data quality issues for AI robustness testing:

| Level | Name | Artifacts |
|-------|------|-----------|
| **0** | Pristine | Clean, well-structured data |
| **1** | Light | Medical abbreviations, shorthand |
| **2** | Moderate | Copy-forward artifacts, zombie notes |
| **3** | Heavy | Missing codes, implicit diagnoses |
| **4** | Severe | Dictation errors, pronoun mismatches |
| **5** | Hostile | ISMP violations, trailing zeros, allergy conflicts |

```bash
# Generate with messiness
python cli.py generate --age 5 --messiness 3
```

## Patient Messages

Oread generates realistic patient-provider communications:

- **Message types** - Refill requests, clinical questions, appointment requests, follow-ups
- **Communication channels** - Portal messages, phone calls
- **Office replies** - Contextual responses from nurses and providers
- **FHIR export** - Messages export as FHIR Communication resources

Messages are generated based on patient complexity and a random "messaging frequency" factor.

## Project Structure

```
oread/
├── cli.py              # Command-line interface
├── server.py           # FastAPI web server
├── web/                # Web UI
├── src/
│   ├── models/         # Pydantic data models
│   ├── engines/        # Generation engines (PedsEngine)
│   ├── exporters/      # FHIR, C-CDA, JSON, Markdown
│   └── llm/            # Claude API client
├── knowledge/
│   ├── conditions/     # Condition definitions (YAML)
│   ├── growth/         # CDC 2000 growth charts
│   └── immunizations/  # AAP vaccine schedule
├── docs/               # Documentation
└── tests/              # Test suite
```

## Documentation

- [QUICKSTART.md](docs/QUICKSTART.md) - Get up and running in 5 minutes
- [CONDITION_SCHEMA.md](docs/CONDITION_SCHEMA.md) - Adding and editing conditions
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - System design and data flow
- [CLAUDE.md](CLAUDE.md) - Development context for AI assistants

## Development

```bash
# Activate virtual environment
source .venv/bin/activate

# Run the server with reload
uvicorn server:app --reload

# Run tests
pytest tests/

# Generate test patient
python cli.py generate --age 5 --conditions asthma
```

## Clinical Accuracy

Oread generates medically accurate content:

- **Growth tracking** - CDC 2000 growth charts with LMS method
- **Immunizations** - Current AAP/CDC schedule with proper timing
- **Vital signs** - Age-appropriate ranges, illness-aware modifications
- **Medications** - Weight-based pediatric dosing with max doses
- **Physical exams** - Condition-specific findings with probabilities
- **Lab results** - LOINC-coded with realistic values
- **Medical history** - Resolved conditions from past acute visits with abatement dates
- **Past medications** - Completed antibiotic courses and discontinued PRN medications

## License

MIT License - see [LICENSE](LICENSE) for details.

---

**Built for advancing healthcare AI**
