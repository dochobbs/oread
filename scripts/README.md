# Oread Scripts

## ccda_summary.py

Generate Pre-Visit Executive Summaries from C-CDA files using Claude AI.

### Usage

```bash
# Random visit
python scripts/ccda_summary.py patient.xml

# Specific visit by index
python scripts/ccda_summary.py patient.xml --visit 4

# List all visits
python scripts/ccda_summary.py patient.xml --list
```

### Requirements

- Python 3.11+
- `anthropic` package
- `ANTHROPIC_API_KEY` environment variable

### Output

Generates a structured Pre-Visit Executive Summary:

1. **Critical Alerts** - Allergies, active conditions
2. **Visit Context** - Interval history, hidden patterns
3. **Disease Control** - Condition status, trends, metrics
4. **Medication Reconciliation** - Active meds, adherence
5. **Clinical Decision Support** - Care gaps, suggestions

---

## generate_scripted_patient.py

Generate a 10-year-old patient with T1D and asthma, including 5 specific educational encounters.

### Usage

```bash
python scripts/generate_scripted_patient.py
```

### Output

- `output/{id}_patient.json` - Full patient record
- `output/{id}_patient.xml` - C-CDA 2.1 document

### Scripted Encounters

1. Well-child checkup (9 years)
2. Cold/URI (acute illness)
3. Pneumonia follow-up (9 days later, linked)
4. Combined chronic care (asthma + T1D)
5. Injury (laceration from bicycle fall)

## qa_full_features.py / qa_stress.py

Manual QA generators (formerly `test_full_features.py` / `test_stress.py` at the repo root). They write reports to `output/` and assert nothing; the automated equivalent is `tests/test_clinical_invariants.py`. `qa_stress.py` reads `output/files/oread_stress_test_batch.json`, which is not in the repo. Run from the repo root:

```bash
uv run python scripts/qa_full_features.py
```

## stress_peds.py

Randomized peds stress test: every condition alone and in combinations (including requests below a condition's minimum age), ages 0-21 y, messiness 0-5, complexity tiers. Each patient gets the clinical invariants plus extra plausibility checks (vitals, growth, allergy conflicts, screening schedule, same-day duplicates), all four exports, a stored-record round trip, timeline snapshots and single-encounter generation.

```bash
uv run python scripts/stress_peds.py 800 20261027   # N patients, master seed
```

Last run (2026-10-27, three seeds, 3,800 patients): 0 crashes, 0 faults on clean charts.
