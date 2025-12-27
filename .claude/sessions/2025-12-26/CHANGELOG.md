# Changelog - 2025-12-26

## Features

### Enhanced Messiness System
- Rewrote `messiness.py` with difficulty-based level descriptions
- Added 5 error categories with specific error types:
  - Safety-critical (12 errors): Allergy contradictions, weight/dosing mismatches
  - Workflow (13 errors): Copy-forward artifacts, ROS contradictions
  - Data-integrity (10 errors): Duplicate encounters, stale heights
  - Coding/billing (4 errors): ICD/CVX mismatches
  - Medicolegal (4 errors): Truncated notes, missing signatures
- Implemented timeline-aware error distribution (early/middle/recent)
- Created 4 threading errors for level 5 charts

### Panel Generation Enhancements
- Added natural language configuration modal
- Default to LLM for richer patient records
- Added description, conditions, and age range options

## Fixes

### Time Travel Slider
- Only shows for patients with active chronic conditions
- Filters out resolved conditions from triggering UI

### Database Credentials
- Added `.env` file support with `python-dotenv`
- Added fallback for both SUPABASE_KEY and SUPABASE_ANON_KEY naming

### Panel Modal
- Fixed modal not appearing (use classList.add('active') instead of style.display)

### Vitals Contradiction
- Skip BP comparison for young children (null blood pressure)

## Refactors

### Engine Timeline Integration
- Calculate timeline position (early/middle/recent) for each encounter
- Pass encounter index to narrative generation
- Apply messiness in parallel narrative generation

## Technical Details

### New Error Level Descriptions
- 0: Pristine - Teaching ideal
- 1: Real World - Minor inconsistencies
- 2: Busy Clinic - Copy-forward artifacts
- 3: Needs Reconciliation - Conflicts requiring judgment
- 4: Safety Landmines - Hidden dangers
- 5: Chart From Hell - Threading errors, medicolegal nightmares

### Threading Error Scenarios
1. `amox_allergy_escalation`: Rash → over-escalation → unsafe re-exposure
2. `rad_asthma_undertreatment`: RAD → asthma drift → no controller
3. `weight_tracking_failure`: Creep missed → obesity → metabolic
4. `developmental_delay_missed`: Concerns dismissed → late intervention
