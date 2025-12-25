# Oread Quick Start Guide

Get up and running with Oread in 5 minutes.

## Prerequisites

- Python 3.11 or higher
- pip (Python package manager)

## Installation

```bash
# 1. Clone or download the repository
cd /path/to/oread

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate the virtual environment
source .venv/bin/activate   # macOS/Linux
# or: .venv\Scripts\activate  # Windows

# 4. Install dependencies
pip install -e .
```

## Generate Your First Patient

### Using the CLI

```bash
# Generate a random patient
python cli.py generate

# Generate a 2-year-old
python cli.py generate --age 2

# Generate an infant (6 months)
python cli.py generate --age-months 6

# Generate with a specific condition
python cli.py generate --age 5 --conditions asthma
```

### Using the Web Interface

```bash
# Start the server
python server.py

# Open your browser to http://localhost:8000
```

The web UI lets you:
- Generate patients interactively
- Browse generated patients
- Export in any format (JSON, FHIR, C-CDA, Markdown)

### Using the API

```bash
# Start the server (if not already running)
python server.py

# Generate a patient
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"age_months": 18}'

# Response includes patient ID
# {"id": "abc123", "name": "Emma Johnson", ...}

# Get full patient data
curl http://localhost:8000/api/patients/abc123

# Export as FHIR
curl http://localhost:8000/api/patients/abc123/export/fhir -o patient.fhir.json

# Export as C-CDA
curl http://localhost:8000/api/patients/abc123/export/ccda -o patient.ccda.xml
```

## Understanding the Output

### Patient Summary

When you generate a patient, you get a summary:

```json
{
  "id": "abc123",
  "name": "Emma Johnson",
  "date_of_birth": "2022-06-15",
  "age_years": 2,
  "sex": "female",
  "complexity_tier": "tier-1",
  "active_conditions": ["Asthma"],
  "encounter_count": 12
}
```

### Full Patient Record

The full record includes:
- **Demographics** - Name, DOB, address, emergency contact
- **Problem List** - Active and resolved conditions with SNOMED/ICD-10 codes
- **Medications** - Active prescriptions with RxNorm codes and dosing
- **Allergies** - Known allergies and reactions
- **Immunizations** - Complete vaccine history with CVX codes
- **Encounters** - Full visit history with:
  - Vitals (illness-aware: fever, tachycardia, low SpO2)
  - Physical exam findings (condition-specific)
  - Assessment and plan
  - Lab results (with LOINC codes)
  - Prescriptions (weight-based dosing)
- **Growth Data** - Weight, height, head circumference with percentiles

## Export Formats

| Format | Use Case | Command |
|--------|----------|---------|
| **JSON** | Integration with applications | `/export/json` |
| **FHIR R4** | Interoperability, EHR integration | `/export/fhir` |
| **C-CDA 2.1** | Health information exchange | `/export/ccda` |
| **Markdown** | Human review, documentation | `/export/markdown` |

## Common Scenarios

### Generate a Healthy Infant

```bash
python cli.py generate --age-months 6 --complexity tier-0
```

### Generate a Complex Teenager

```bash
python cli.py generate --age 14 --complexity tier-2 --conditions "asthma,adhd,anxiety"
```

### Generate Multiple Patients

```bash
python cli.py batch --count 10 --output ./patients/
```

### Generate with Specific Sex

```bash
python cli.py generate --age 8 --sex female
```

## Troubleshooting

### "Module not found" Error

Make sure you activated the virtual environment:
```bash
source .venv/bin/activate
```

### Port 8000 Already in Use

Use a different port:
```bash
uvicorn server:app --port 3000
```

### Slow Generation

First-time generation may be slower as caches warm up. Subsequent generations are faster.

## Next Steps

- Read [CONDITION_SCHEMA.md](CONDITION_SCHEMA.md) to understand condition definitions
- Check [ARCHITECTURE.md](ARCHITECTURE.md) for system design details
- Explore the API docs at http://localhost:8000/docs
