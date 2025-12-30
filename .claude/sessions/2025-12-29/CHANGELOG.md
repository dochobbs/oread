# Changelog - 2025-12-29

## Features (Uncommitted - Ready for Commit)

### Vaccine Engine Enhancements
- Load AAP immunization schedule from YAML instead of hardcoded dictionary
- Catch-up vaccine logic for missed doses
- Vaccine hesitancy injection with 8 reason types
- Uses CVX codes from YAML schedule

### Growth Pattern Variations
- New `GrowthPattern` enum (NORMAL, FTT, OBESITY, PRETERM_CATCHUP, GROWTH_DELAY)
- Pattern-aware percentile drift in GrowthTrajectory
- Condition-based growth pattern selection

### Labs Expansion
- Numeric lab values with units and reference ranges
- Age-specific pediatric reference ranges
- Probability-based abnormal value generation
- Added WBC, CRP to pneumonia
- Added urinalysis panel to UTI

### Imaging Generation
- New `_generate_imaging()` method
- Imaging definitions with positive/negative findings and impressions
- Chest X-ray for pneumonia
- AP Neck X-ray for croup

### FHIR Export
- Lab results exported as FHIR Observations
- Imaging results exported as FHIR DiagnosticReports

## Recent Commits (Already Pushed)

- `573ce53`: FEATURE: Enhanced messiness system with timeline-aware error injection
- `4caf947`: FIX: Medication attribute names in timeline endpoint
- `a537a86`: FIX: Auto-create user profile on first authenticated request
- `20ea6f5`: FIX: Database repository error handling
- `53c59b5`: FIX: Panel patient viewing and new encounter flow
