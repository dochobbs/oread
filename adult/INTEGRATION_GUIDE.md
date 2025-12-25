# Adult Conditions Registry Integration Guide

## Overview

This update expands the adult conditions registry from ~80 to **218 conditions** (82 chronic, 136 acute) with a moderate-complexity schema that enables:

- Prevalence-weighted condition selection
- Age and sex filtering
- Risk factor clustering (comorbidity patterns)
- Seasonal variation for acute conditions
- Comprehensive medication and lab associations

## Files Changed

### 1. `knowledge/conditions/adult_conditions.yaml`
Complete conditions database with two schemas:

**Chronic conditions** (categories: cardiovascular, metabolic, respiratory, mental_health, musculoskeletal, gastrointestinal, renal, neurologic, genitourinary, dermatologic, infectious, other_chronic, chronic_additional):
```yaml
condition_name:
  icd10: I10                    # ICD-10-CM code
  display: Essential hypertension
  prevalence: 0.47              # Selection weight 0.01-0.50
  onset_age: [25, 75]           # Typical onset range
  sex: null                     # null | male | female
  risk_factors: [obesity, ckd]  # Conditions that increase risk
  meds: [lisinopril 10mg, ...]  # Medication options
  labs: [bmp, cmp, lipid_panel] # Monitoring tests
  visits: quarterly_then_annual # Follow-up pattern
```

**Acute conditions** (categories: acute_respiratory, acute_gi, acute_gu, acute_skin, acute_msk, acute_neuro, acute_eye_ear, acute_other, acute_dental, acute_sti, acute_pain_syndromes, acute_skin_additional, acute_gi_additional, acute_gu_additional, acute_neuro_additional, acute_eye_ear_additional, acute_misc):
```yaml
condition_name:
  icd10: J06.9
  display: Acute upper respiratory infection
  frequency: 10.0               # Selection weight (higher = more common)
  seasonality: [winter, fall]   # null or list of seasons
  duration_days: [5, 14]        # Typical duration range
  age_bias: null                # null | young | middle | older
  recurrence: 2.5               # Episodes per year
  treatment:
    symptomatic: [acetaminophen 500mg, ...]
    antibiotics: [...]          # If applicable
  workup: clinical              # Labs/imaging or "clinical"
```

### 2. `src/engines/adult_engine.py`
Updated `ConditionRegistry` class with new methods:

```python
class ConditionRegistry:
    CHRONIC_CATEGORIES = [...]  # 13 categories
    ACUTE_CATEGORIES = [...]    # 16 categories
    
    # Existing methods
    get_condition(name) -> dict
    get_icd10(name) -> tuple[str, str]
    get_meds(name) -> list[str]
    get_onset_range(name) -> tuple[int, int]
    get_visit_pattern(name) -> str
    get_sex_specific(name) -> str | None
    all_chronic_conditions() -> list[str]
    conditions_by_category(category) -> list[str]
    
    # NEW methods
    reload()  # Force reload of YAML data
    get_prevalence(name) -> float
    get_risk_factors(name) -> list[str]
    get_labs(name) -> list[str]
    all_acute_conditions() -> list[str]
    get_acute_condition(name) -> dict
    get_weighted_chronic_conditions(age, sex) -> list[tuple[str, float]]
    get_weighted_acute_conditions(age, season) -> list[tuple[str, float]]
```

## Integration Steps

### Step 1: Update condition selection logic

Replace hardcoded condition lists with registry-based selection:

```python
from src.engines.adult_engine import ConditionRegistry
from src.models import Sex
import random

reg = ConditionRegistry.get()

def select_chronic_conditions(patient_age: int, patient_sex: Sex, target_count: int = 3):
    """Select realistic chronic conditions for a patient."""
    weighted = reg.get_weighted_chronic_conditions(patient_age, patient_sex)
    
    # Normalize weights to probabilities
    total = sum(w for _, w in weighted)
    probs = [(name, w/total) for name, w in weighted]
    
    selected = []
    for name, prob in probs:
        if random.random() < prob * 2:  # Scale factor for desired density
            selected.append(name)
            if len(selected) >= target_count:
                break
    
    return selected
```

### Step 2: Add risk factor clustering

Make comorbidities realistic by checking risk factors:

```python
def add_related_conditions(patient_conditions: list[str], patient_age: int, patient_sex: Sex):
    """Add conditions that commonly co-occur based on risk factors."""
    additional = []
    
    for condition in patient_conditions:
        # Check what conditions list this one as a risk factor
        for other in reg.all_chronic_conditions():
            if condition in reg.get_risk_factors(other):
                # This condition increases risk of 'other'
                base_prev = reg.get_prevalence(other)
                if random.random() < base_prev * 3:  # 3x increased risk
                    if other not in patient_conditions and other not in additional:
                        additional.append(other)
    
    return patient_conditions + additional
```

### Step 3: Generate acute encounters

Use weighted acute selection for visit generation:

```python
def generate_acute_visit(patient_age: int, visit_date: date):
    """Generate an acute care visit with appropriate condition."""
    # Determine season from date
    month = visit_date.month
    if month in [12, 1, 2]:
        season = "winter"
    elif month in [3, 4, 5]:
        season = "spring"
    elif month in [6, 7, 8]:
        season = "summer"
    else:
        season = "fall"
    
    weighted = reg.get_weighted_acute_conditions(patient_age, season)
    
    # Weighted random selection
    total = sum(w for _, w in weighted)
    r = random.random() * total
    cumulative = 0
    for name, weight in weighted:
        cumulative += weight
        if r <= cumulative:
            return name
    
    return weighted[0][0]  # Fallback
```

### Step 4: Generate medications from conditions

```python
def get_medications_for_patient(conditions: list[str]):
    """Generate medication list based on patient's conditions."""
    meds = []
    for condition in conditions:
        condition_meds = reg.get_meds(condition)
        if condition_meds:
            # Pick 1-2 meds per condition
            selected = random.sample(condition_meds, min(2, len(condition_meds)))
            meds.extend(selected)
    return list(set(meds))  # Deduplicate
```

### Step 5: Generate labs from conditions

```python
def get_monitoring_labs(conditions: list[str]):
    """Get labs needed to monitor patient's conditions."""
    all_labs = set()
    for condition in conditions:
        labs = reg.get_labs(condition)
        all_labs.update(labs)
    return list(all_labs)
```

## Condition Categories Reference

### Chronic (82 conditions)
- **Cardiovascular (6):** hypertension, hyperlipidemia, CAD, AFib, CHF, PAD
- **Metabolic (7):** T2DM, prediabetes, obesity, hypothyroidism, hyperthyroidism, gout, vitamin D deficiency
- **Respiratory (4):** COPD, asthma, sleep apnea, allergic rhinitis
- **Mental Health (10):** depression, anxiety, bipolar, PTSD, ADHD, insomnia, AUD, OUD, etc.
- **Musculoskeletal (6):** OA, RA, osteoporosis, low back pain, fibromyalgia, neck pain
- **GI (8):** GERD, IBS, constipation, fatty liver, Crohn's, UC, diverticulosis
- **Renal (2):** CKD stage 3, kidney stones
- **Neurologic (6):** migraine, epilepsy, Parkinson's, dementia, neuropathy, essential tremor
- **GU (5):** BPH, OAB, menopause, ED, PCOS
- **Dermatologic (4):** psoriasis, eczema, rosacea, acne
- **Infectious (3):** HIV, Hep C, Hep B
- **Other (3):** anemia, chronic pain, chronic fatigue
- **Additional (21):** chronic sinusitis, glaucoma, cataracts, macular degeneration, hearing loss, RLS, IC, endometriosis, cirrhosis, Barrett's, celiac, MS, lupus, Sjogren's, hidradenitis, chronic urticaria, alopecia areata, vitiligo, etc.

### Acute (136 conditions)
- **Respiratory (10):** URI, sinusitis, pharyngitis, bronchitis, flu, COVID, pneumonia, laryngitis, asthma/COPD exacerbations
- **GI (6+):** gastroenteritis, food poisoning, gastritis, constipation, hemorrhoids, diverticulitis, dyspepsia, N/V, diarrhea, rectal bleeding, anal fissure
- **GU (10+):** UTI, pyelonephritis, renal colic, vaginitis, epididymitis, prostatitis, hematuria, dysuria, testicular pain, dysmenorrhea, AUB, mastitis
- **Skin (15+):** cellulitis, abscess, contact dermatitis, urticaria, zoster, HSV, insect bite, burns, sunburn, impetigo, paronychia, fungal, ingrown nail, folliculitis, wound infection
- **MSK (15+):** sprains (ankle, wrist, knee), strains, contusions, lacerations, tendinitis, bursitis, carpal tunnel, plantar fasciitis, shoulder impingement, rotator cuff, epicondylitis, de Quervain's, trigger finger
- **Neuro (8+):** tension headache, migraine, vertigo, Bell's palsy, concussion, post-concussion, BPPV, cluster headache
- **Eye/Ear (12+):** conjunctivitis, otitis externa/media, corneal abrasion, stye, cerumen impaction, foreign bodies, allergic conjunctivitis, blepharitis, chalazion, dry eye, hearing loss, tinnitus
- **Dental (3):** abscess, toothache, TMJ
- **STI (4):** chlamydia, gonorrhea, trichomoniasis, genital herpes
- **Pain syndromes (12):** sciatica, cervical radiculopathy, various tendinitis/bursitis locations
- **Other (20+):** allergic reaction, chest pain, abdominal pain, syncope, panic attack, gout flare, epistaxis, tick bite, heat exhaustion, costochondritis, palpitations, viral syndrome, mono, strep, pertussis, DVT, SOB, cough, hoarseness, fatigue workup, weight loss, lymphadenopathy, night sweats, leg swelling, insomnia, RLS, muscle cramps

## Testing

```bash
# Verify registry loads correctly
python3 -c "
from src.engines.adult_engine import ConditionRegistry
reg = ConditionRegistry.get()
print(f'Chronic: {len(reg.all_chronic_conditions())}')
print(f'Acute: {len(reg.all_acute_conditions())}')
"

# Run existing tests
python3 -m pytest tests/ -v
```

## Notes

- Prevalence values are approximate US adult population rates
- Risk factors enable realistic comorbidity clustering (e.g., obesity → HTN, T2DM, sleep apnea)
- Seasonality applies to acute conditions (respiratory peaks in winter, skin issues in summer)
- Age bias affects acute condition selection (young → sprains, older → pneumonia)
- The registry is a singleton; use `ConditionRegistry.reload()` if YAML is modified at runtime
