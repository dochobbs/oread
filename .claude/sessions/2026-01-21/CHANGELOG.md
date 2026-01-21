# Changelog - 2026-01-21

## Features

- `dcc8f69`: FEATURE: Enhance AdultEngine with condition-aware generation and disease arcs
  - Condition-aware vitals, symptoms, physical exam, labs
  - 8 adult vaccines with CVX codes and realistic uptake
  - 6 disease arcs for Time Travel feature
  - Timeline generation with PatientTimeline model

## Files Changed

### New Files
- `adult/adult_disease_arcs.yaml` - Adult disease arc definitions (262 lines)

### Modified Files
- `adult/adult_engine.py` - Major enhancements (+1485 lines)
  - Condition-aware generation methods
  - Immunization generation
  - Disease arc and timeline generation
  - Model compliance fixes
