# Condition Schema Reference

This document describes the YAML schema for defining conditions in Oread. Conditions are stored in `knowledge/conditions/conditions.yaml`.

## Schema Version

Current schema version: **2.0** (December 2025)

Major changes from v1.0:
- Added `billing_codes` with SNOMED CT codes
- Added `vitals_impact` for illness-aware vital signs
- Added `presentation.symptoms` with probabilities
- Added `presentation.physical_exam` with probabilistic findings
- Added `diagnostics.labs` with LOINC codes
- Added `treatment.medications` with RxNorm codes and weight-based dosing

## Complete Schema

```yaml
condition_key:                    # Unique identifier (snake_case)
  display_name: "Condition Name"  # Human-readable name
  aliases: ["Alt Name 1", "Alt Name 2"]  # Alternative names

  billing_codes:
    icd10: ["X00.0", "X00.1"]     # ICD-10-CM codes (array)
    snomed: "123456789"           # SNOMED CT concept ID (string)

  category: acute|chronic         # Condition type
  system: respiratory|ent|gi|...  # Body system

  demographics:
    age_months:
      min: 0                      # Minimum age in months
      peak: [6, 36]               # Peak incidence range
      max: 216                    # Maximum age in months
    gender_bias:
      male: 0.5                   # Probability for males
      female: 0.5                 # Probability for females
    risk_factors:                 # Risk factors that increase probability
      - "daycare"
      - "smoke_exposure"

  vitals_impact:                  # How condition affects vital signs
    temp_f: [100.4, 102.5]        # Temperature range (Fahrenheit)
    hr_multiplier: 1.1            # Heart rate multiplier (1.0 = normal)
    rr_multiplier: 1.2            # Respiratory rate multiplier
    spo2_min: 92                  # Minimum SpO2 percentage

  presentation:
    symptoms:                     # Probabilistic symptom list
      - name: "symptom_name"
        probability: 0.9          # 0.0-1.0, likelihood of occurrence
        description: "detailed description"
        age_min: 6                # Optional: minimum age in months
        age_max: 120              # Optional: maximum age in months

    duration_days: [3, 7]         # Typical duration range

    physical_exam:                # Probabilistic PE findings
      - system: "heent"           # Body system (matches PhysicalExam model)
        finding: "Finding text"
        probability: 0.85

  diagnostics:
    labs:                         # Laboratory tests
      - name: "Test Name"
        loinc: "12345-6"          # LOINC code
        result_positive: "Positive result text"
        result_negative: "Negative result text"
        probability_positive: 0.7  # Probability of positive result

  treatment:
    medications:                  # Prescription medications
      - agent: "Drug Name"
        rxnorm: "12345"           # RxNorm code
        dose_mg_kg: 10            # mg per kg body weight
        max_dose_mg: 500          # Maximum single dose
        frequency: "TID"          # Dosing frequency
        duration_days: 10         # Treatment duration
        route: "oral"             # Route of administration
        prn: false                # As-needed medication
        indication: "For pain"    # Optional indication text

    patient_instructions:         # Patient education
      - "Instruction 1"
      - "Instruction 2"
```

## Field Reference

### billing_codes

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `icd10` | array | Yes | ICD-10-CM codes for billing |
| `snomed` | string | Yes | SNOMED CT concept ID for clinical coding |

**Finding SNOMED codes:** Use the [SNOMED CT Browser](https://browser.ihtsdotools.org/)

**Finding ICD-10 codes:** Use [ICD-10 Data](https://www.icd10data.com/)

### vitals_impact

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `temp_f` | [min, max] | null | Temperature range in Fahrenheit |
| `hr_multiplier` | float | 1.0 | Multiplier for heart rate (1.2 = 20% increase) |
| `rr_multiplier` | float | 1.0 | Multiplier for respiratory rate |
| `spo2_min` | int | null | Minimum SpO2 percentage (92-100) |

Example for bronchiolitis (respiratory illness):
```yaml
vitals_impact:
  temp_f: [99.0, 102.0]
  hr_multiplier: 1.15
  rr_multiplier: 1.30
  spo2_min: 88
```

### presentation.symptoms

Each symptom is probabilistically selected based on its `probability` field.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Symptom identifier |
| `probability` | float | Yes | Probability of occurrence (0.0-1.0) |
| `description` | string | No | Detailed symptom description |
| `age_min` | int | No | Minimum age in months for this symptom |
| `age_max` | int | No | Maximum age in months for this symptom |

### presentation.physical_exam

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `system` | string | Yes | Body system (see below) |
| `finding` | string | Yes | Physical exam finding text |
| `probability` | float | Yes | Probability of finding (0.0-1.0) |

**Valid systems:** `general`, `heent`, `cardiovascular`, `respiratory`, `abdominal`, `skin`, `neurological`, `musculoskeletal`, `lymphatic`, `psychiatric`

### diagnostics.labs

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Lab test name |
| `loinc` | string | Yes | LOINC code |
| `result_positive` | string | Yes | Result text when positive |
| `result_negative` | string | Yes | Result text when negative |
| `probability_positive` | float | Yes | Probability of positive result |

**Finding LOINC codes:** Use [LOINC Search](https://loinc.org/search/)

### treatment.medications

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `agent` | string | Yes | Drug name |
| `rxnorm` | string | Yes | RxNorm code |
| `dose_mg_kg` | float | No | Dose in mg per kg body weight |
| `max_dose_mg` | float | No | Maximum single dose in mg |
| `frequency` | string | Yes | Dosing frequency (BID, TID, Q6H, etc.) |
| `duration_days` | int | No | Treatment duration in days |
| `route` | string | No | Route (oral, topical, ophthalmic, etc.) |
| `prn` | bool | No | As-needed medication (default: false) |
| `indication` | string | No | Indication text |
| `age_min_months` | int | No | Minimum age for this medication |

**Finding RxNorm codes:** Use [RxNav](https://mor.nlm.nih.gov/RxNav/)

## Complete Example

```yaml
otitis_media:
  display_name: "Acute Otitis Media"
  aliases: ["Ear infection", "AOM", "Middle ear infection"]

  billing_codes:
    icd10: ["H66.90", "H66.91", "H66.92", "H66.93"]
    snomed: "65363002"

  category: acute
  system: ent

  demographics:
    age_months: { min: 6, peak: [6, 36], max: 144 }
    gender_bias: { male: 0.55, female: 0.45 }
    risk_factors: ["daycare", "smoke_exposure", "pacifier_use", "bottle_propping"]

  vitals_impact:
    temp_f: [100.4, 102.5]
    hr_multiplier: 1.1
    rr_multiplier: 1.0
    spo2_min: 97

  presentation:
    symptoms:
      - { name: "ear pain", probability: 0.9, description: "ear tugging or pain" }
      - { name: "fever", probability: 0.7, description: "temperature 100-102F" }
      - { name: "irritability", probability: 0.8, description: "fussy, crying" }
      - { name: "decreased appetite", probability: 0.6 }
      - { name: "sleep disturbance", probability: 0.7 }

    duration_days: [7, 14]

    physical_exam:
      - { system: "heent", finding: "TM erythematous and bulging with loss of light reflex", probability: 0.85 }
      - { system: "heent", finding: "Decreased TM mobility on pneumatic otoscopy", probability: 0.9 }
      - { system: "heent", finding: "Purulent middle ear effusion visible", probability: 0.6 }

  diagnostics:
    labs: []  # No labs typically for AOM

  treatment:
    medications:
      - agent: "Amoxicillin"
        rxnorm: "723"
        dose_mg_kg: 90
        max_dose_mg: 3000
        frequency: "divided BID"
        duration_days: 10
        route: "oral"
        indication: "First-line antibiotic for AOM"

      - agent: "Amoxicillin-Clavulanate"
        rxnorm: "1043"
        dose_mg_kg: 90
        max_dose_mg: 3000
        frequency: "divided BID"
        duration_days: 10
        route: "oral"
        indication: "If treatment failure or recent antibiotics"

      - agent: "Ibuprofen"
        rxnorm: "5640"
        dose_mg_kg: 10
        max_dose_mg: 400
        frequency: "Q6H PRN"
        route: "oral"
        prn: true
        indication: "Pain and fever"

    patient_instructions:
      - "Complete full course of antibiotics even if feeling better"
      - "Use pain medication as needed for comfort"
      - "Return if no improvement in 48-72 hours"
      - "Return immediately for high fever, neck stiffness, or severe pain"
```

## Key Medical Code Resources

| Code System | Browser/Search | Description |
|-------------|----------------|-------------|
| SNOMED CT | [browser.ihtsdotools.org](https://browser.ihtsdotools.org/) | Clinical terminology |
| ICD-10-CM | [icd10data.com](https://www.icd10data.com/) | Diagnosis codes |
| RxNorm | [mor.nlm.nih.gov/RxNav](https://mor.nlm.nih.gov/RxNav/) | Medication codes |
| LOINC | [loinc.org/search](https://loinc.org/search/) | Lab test codes |
| CVX | [cdc.gov/vaccines/programs/iis/cvx](https://www.cdc.gov/vaccines/programs/iis/cvx/) | Vaccine codes |

## Common RxNorm Codes

| Medication | RxNorm | Common Use |
|------------|--------|------------|
| Acetaminophen | 161 | Fever, pain |
| Ibuprofen | 5640 | Fever, pain, inflammation |
| Amoxicillin | 723 | Bacterial infections |
| Amoxicillin-Clavulanate | 1043 | Resistant bacterial infections |
| Azithromycin | 18631 | Bacterial infections (PCN allergy) |
| Ceftriaxone | 2193 | Severe bacterial infections |
| Dexamethasone | 3264 | Croup, inflammation |
| Albuterol | 435 | Asthma, wheezing |
| Fluticasone | 41126 | Asthma controller |
| Ondansetron | 26225 | Nausea/vomiting |
| Prednisolone | 8638 | Asthma exacerbation |

## Common LOINC Codes

| Test | LOINC | Use |
|------|-------|-----|
| Rapid Strep | 78012-2 | Pharyngitis workup |
| Urinalysis | 5767-9 | UTI workup |
| Urine Culture | 630-4 | UTI confirmation |
| CBC | 58410-2 | Infection workup |
| Hemoglobin | 718-7 | Anemia screening |
| Lead (capillary) | 10368-9 | Lead screening |
| Chest X-ray | 36643-5 | Respiratory illness |
| RSV Rapid | 40988-8 | Bronchiolitis |
| Influenza Rapid | 80382-5 | Influenza |

## Adding a New Condition

1. **Choose a unique key** - Use snake_case (e.g., `viral_gastroenteritis`)
2. **Find the SNOMED code** - Search the SNOMED CT Browser
3. **Find ICD-10 codes** - Include all relevant codes
4. **Define vitals_impact** - Based on clinical presentation
5. **List symptoms** - With realistic probabilities
6. **Add PE findings** - Match to correct body systems
7. **Include labs** - If applicable, with LOINC codes
8. **Define treatment** - With RxNorm codes and weight-based dosing
9. **Add patient instructions** - Standard discharge instructions

## Validation

The engine validates conditions at load time. Required fields:
- `display_name`
- `billing_codes.snomed` or `billing_codes.icd10`
- `category` (acute or chronic)
- `system`

Optional but recommended:
- `vitals_impact` for acute conditions
- `presentation.physical_exam` for conditions with distinct findings
- `treatment.medications` with complete RxNorm data
