# SynthPatient

**Generate realistic, clinically coherent synthetic patient records**

SynthPatient creates longitudinal medical records for AI evaluation, EMR demos, and medical education. It produces FHIR R4 bundles, structured JSON, and human-readable Markdown documentation.

![SynthPatient](https://img.shields.io/badge/version-0.1.0-blue)
![Python](https://img.shields.io/badge/python-3.11+-green)
![License](https://img.shields.io/badge/license-MIT-gray)

---

## Features

- **Clinically coherent** — Growth curves, immunizations, and conditions follow realistic patterns
- **Longitudinal records** — Full visit history from birth through adulthood
- **Multiple outputs** — FHIR R4, JSON, and Markdown export
- **Flexible generation** — From healthy patients to complex multi-system disease
- **Web interface** — Beautiful UI for interactive generation
- **CLI tool** — Scriptable batch generation

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/synthpatient.git
cd synthpatient

# Install dependencies
pip install -e .

# Set your Anthropic API key (optional, for LLM-enhanced generation)
export ANTHROPIC_API_KEY=your-key-here
```

### Generate a Patient

```bash
# Quick random generation
python cli.py generate

# Specific age and sex
python cli.py generate --age 5 --sex female

# With conditions
python cli.py generate --age 10 --conditions "asthma,adhd"

# Complex patient
python cli.py generate --complexity tier-3

# Natural language description
python cli.py generate --describe "A 14yo boy with poorly controlled type 1 diabetes"
```

### Web Interface

```bash
# Start the web server
python server.py

# Open http://localhost:8000
```

### Batch Generation

```bash
# Generate 50 patients with a distribution
python cli.py batch --count 50 \
  --distribution "healthy:60,tier1:25,tier2:12,tier3:3" \
  --age-range 0-18 \
  --output ./patients/
```

## Output Formats

### JSON

Clean, structured patient data:

```json
{
  "id": "abc123",
  "demographics": {
    "full_name": "Emma Johnson",
    "date_of_birth": "2019-06-15",
    "sex_at_birth": "female"
  },
  "problem_list": [...],
  "encounters": [...]
}
```

### FHIR R4

Standards-compliant FHIR Bundle with:
- Patient resource (US Core profile)
- Condition resources
- MedicationStatement resources
- Immunization resources
- Encounter resources
- Observation resources (vitals, growth)

### Markdown

Human-readable documentation perfect for review:

```markdown
# Patient Record: Emma Johnson

**Age:** 5 years
**Sex:** Female

## Problem List
- Asthma, mild persistent (J45.30) - Active

## Encounter History
### 2024-06-15 - Well-Child Visit
**Chief Complaint:** 5 year well child check
...
```

## Complexity Tiers

| Tier | Description | Example |
|------|-------------|---------|
| **Tier 0** | Healthy | Well visits only, no chronic conditions |
| **Tier 1** | Single chronic | Asthma, ADHD, or eczema |
| **Tier 2** | Multiple conditions | Asthma + allergies + anxiety |
| **Tier 3** | Complex/fragile | Multiple specialists, technology-dependent |

## API Reference

### Generate Patient

```http
POST /api/generate
Content-Type: application/json

{
  "age": 5,
  "sex": "female",
  "conditions": ["asthma"],
  "complexity_tier": "tier-1"
}
```

### Get Patient

```http
GET /api/patients/{id}?format=json
```

### Export Patient

```http
GET /api/patients/{id}/export/fhir
```

See `/docs` for full API documentation.

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Claude API key for LLM features | None |
| `SYNTHPATIENT_CACHE_DIR` | Cache directory | `~/.synthpatient/cache` |

### Generation Options

| Option | Type | Description |
|--------|------|-------------|
| `--age` | int | Patient age in years |
| `--age-months` | int | Patient age in months (infants) |
| `--sex` | str | "male" or "female" |
| `--conditions` | str | Comma-separated condition list |
| `--complexity` | str | "tier-0", "tier-1", "tier-2", "tier-3" |
| `--encounters` | int | Approximate encounter count |
| `--seed` | int | Random seed for reproducibility |
| `--format` | str | "json", "fhir", "markdown", or "all" |

## Clinical Accuracy

SynthPatient generates medically accurate content including:

- **Growth tracking** — CDC 2000 growth charts with LMS method
- **Immunizations** — Current AAP/CDC schedule with proper timing
- **Vital signs** — Age-appropriate normal ranges
- **Medications** — Weight-based pediatric dosing
- **Conditions** — Realistic presentation, workup, and treatment

## Use Cases

### AI Evaluation

Generate diverse patient cohorts to benchmark AI systems:

```bash
python cli.py batch --count 1000 \
  --distribution "tier0:40,tier1:30,tier2:20,tier3:10" \
  --output ./evaluation_cohort/
```

### EMR Demos

Create realistic demo data for EMR presentations:

```bash
python cli.py generate --describe "A technology-dependent former 24-week preemie, now 2 years old"
```

### Medical Education

Generate teaching cases:

```bash
python cli.py generate --conditions "new-onset type 1 diabetes" --age 8
```

## Development

### Project Structure

```
synthpatient/
├── cli.py              # Command-line interface
├── server.py           # FastAPI web server
├── web/                # Web UI
├── src/
│   ├── models/         # Pydantic data models
│   ├── engines/        # Generation engines
│   ├── exporters/      # Output formatters
│   └── llm/            # Claude API client
├── knowledge/
│   ├── conditions/     # Condition definitions
│   ├── growth/         # Growth charts
│   └── immunizations/  # Vaccine schedules
└── tests/
```

### Running Tests

```bash
pytest tests/
```

### Adding Conditions

Create a YAML file in `knowledge/conditions/`:

```yaml
name: "My Condition"
icd10_code: "X00.0"
age_of_onset:
  typical_range: "5-15 years"
treatment:
  first_line:
    - name: "Medication"
      dose: "..."
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- CDC for growth chart data
- AAP for immunization schedules
- Anthropic for Claude API

---

**Built for advancing healthcare AI** 🏥
