# Oread Quality Assessment Findings
**Date:** 2025-12-31
**Source:** `quality_assessment_10patients.md` (26,186 lines, 10 patients, zero messiness)
**Purpose:** Identify generation bugs for Claude Code to fix

---

## Executive Summary

The validation layer designed to catch clinical plausibility issues is **either not running, not auto-fixing, or running after output serialization**. Critical issues that should have been blocked or auto-fixed are appearing in final patient records.

**Priority:** Trace the pipeline to determine why validation isn't preventing these issues.

---

## Critical Issues Found

### Issue 1: Age-Impossible Condition Assignments

**Patient 1 (Luke Jackson, DOB: 2025-10-31, ~2 months old)**

| Condition | Onset | Age at Onset | Problem |
|-----------|-------|--------------|---------|
| ADHD | 2025-11-30 | 1 month | **IMPOSSIBLE** — ADHD cannot be diagnosed before age 4-6 |
| Asthma | 2025-10-31 | Birth | **IMPOSSIBLE** — requires recurrent episodes over time |
| Epilepsy | 2025-11-30 | 1 month | Possible but rare |
| GERD | 2025-11-30 | 1 month | Plausible |

**Medications assigned to 1-month-old:**
- Methylphenidate ER 18mg daily — **NEVER appropriate for infants**
- Levetiracetam 250mg BID — plausible if epilepsy confirmed
- Albuterol HFA — rarely appropriate for newborns

**Expected behavior:** `_validate_age_appropriate_conditions()` should have:
1. Flagged ADHD as impossible for age < 48 months
2. Flagged Asthma as impossible for age < 12 months
3. Blocked these conditions or removed them via auto-fix

**Location in file:** Lines 51-78

---

### Issue 2: Type 1 Diabetes Without Insulin

**Patient 7 (William Thomas, 8y, T1D diagnosed 2019)**

**Problem List shows:**
```
- type_1_diabetes - Active
  - ICD-10: E10.9
  - Onset: 2019-10-03
```

**Medications section shows:**
```
### Past Medications
- Ondansetron (stopped)
- Dexamethasone (completed)
- Erythromycin Ophthalmic Ointment (completed)
- Amoxicillin (completed)
...
```

**NO ACTIVE MEDICATIONS.** Yet every narrative claims ongoing insulin therapy:
> "Continue current insulin regimen"
> "good compliance with insulin regimen"

**Expected behavior:** `_validate_medication_condition_coherence()` should have:
1. Detected T1D requires insulin
2. Auto-added insulin to active medications OR flagged as critical error

**Location in file:** Lines 8681-8741 (problem list & meds), Lines 8803+ (narratives mentioning insulin)

---

### Issue 3: R69 Fallback Codes Persisting

R69 ("Illness, unspecified") is a garbage code indicating failed ICD-10 lookup:

| Patient | Condition | Current Code | Correct Code |
|---------|-----------|--------------|--------------|
| 1 | GERD | R69 | K21.0 |
| 6 | Cough | R69 | R05.9 |
| 6 | Swimmer's Ear | R69 | H60.339 |
| 6 | Allergic Rhinitis | R69 | J30.9 |
| 7 | Allergic Rhinitis | R69 | J30.9 |
| 8 | Eczema | R69 | L30.9 or L20.9 |
| 9 | Cough | R69 | R05.9 |
| 9 | Allergic Rhinitis | R69 | J30.9 |
| 10 | Cough | R69 | R05.9 |
| 10 | Allergic Rhinitis | R69 | J30.9 |

**Expected behavior:** `_validate_coding_standards()` should have:
1. Detected R69 as invalid
2. Called `_fix_coding_issue()` to lookup correct code via ConditionKnowledgeService
3. Replaced R69 with valid code

**Location in file:** Lines 57, 5925, 5931, 5940, 8720, 12165, 15969, 15975, 21024, 21048

---

### Issue 4: Temporal Violations (Dates Before DOB)

**Patient 1:**
- DOB: October 31, 2025
- HepB #1: October 30, 2025 (**1 day BEFORE birth**)
- Growth entry shows age "-1y 11mo"

**Expected behavior:** `_validate_temporal_consistency()` should have:
1. Detected immunization date < DOB
2. Called `_fix_temporal_issue()` to clamp date to DOB

**Location in file:** Lines 86, 92

---

### Issue 5: Age-Inappropriate Exam Findings in Narratives

**Patient 7 (age 3 at time of encounter):**
> "HEENT examination shows normocephalic head, **intact fontanelles**, and no evidence of trauma"

Fontanelles close by 18-24 months. A 3-year-old has no fontanelles.

**Expected behavior:** Narrative generation should be constrained by patient age, OR reconciliation should catch this mismatch.

**Location in file:** Line 10416

---

## Investigation Checklist for Claude Code

### 1. Verify Validation is Being Called

```python
# In engine.py or wherever generate() lives, look for:
patient = self._generate_patient(seed)
issues = self.validator.validate(patient)  # <-- Is this being called?
```

**Questions:**
- [ ] Is `PatientValidator` instantiated?
- [ ] Is `validate()` called after patient generation?
- [ ] What happens to the returned `issues` list?

### 2. Verify Auto-Fix is Being Applied

```python
# Look for something like:
if issues:
    patient = self._apply_validation_fixes(patient, issues)  # <-- Is this called?
```

**Questions:**
- [ ] Does `_apply_validation_fixes()` exist?
- [ ] Is it called when issues are found?
- [ ] Does it mutate the patient object or return a new one?

### 3. Check Validation Timing vs Output Serialization

```python
# Bad pattern (validation after output):
markdown = patient.to_markdown()
issues = validator.validate(patient)  # Too late!

# Good pattern (validation before output):
issues = validator.validate(patient)
patient = apply_fixes(patient, issues)
markdown = patient.to_markdown()
```

**Questions:**
- [ ] When is the patient serialized to markdown?
- [ ] Does validation happen before or after serialization?

### 4. Check Age Gate Definitions

```yaml
# In conditions.yaml, look for age restrictions:
adhd:
  display_name: "ADHD"
  demographics:
    age_months:
      min: 48  # <-- Does this exist?
      max: 216
```

**Questions:**
- [ ] Do conditions have `age_months.min` and `age_months.max` defined?
- [ ] Is the engine checking these before assigning conditions?

### 5. Check Mandatory Medication Definitions

```yaml
# In conditions.yaml or a separate config:
type_1_diabetes:
  requires_medications:
    - category: insulin  # <-- Does this exist?
```

**Questions:**
- [ ] Is there a mechanism to enforce required medications per condition?
- [ ] Does T1D definition specify insulin requirement?

### 6. Check ICD-10 Code Completeness

```yaml
# In conditions.yaml:
gerd:
  icd10_primary: K21.0  # <-- Is this populated?

swimmers_ear:
  icd10_primary: H60.339  # <-- Is this populated?

allergic_rhinitis:
  icd10_primary: J30.9  # <-- Is this populated?
```

**Questions:**
- [ ] Which conditions are missing `icd10_primary`?
- [ ] Is there a fallback to R69 when lookup fails?

---

## Suggested Fixes

### Fix 1: Add Validation Gate Before Output

```python
def generate_patient(self, seed) -> Patient:
    patient = self._build_patient(seed)
    
    # VALIDATE AND FIX BEFORE RETURNING
    issues = self.validator.validate(patient)
    critical_issues = [i for i in issues if i.severity == Severity.ERROR]
    
    if critical_issues:
        patient = self._apply_validation_fixes(patient, critical_issues)
        
        # Re-validate to ensure fixes worked
        remaining = self.validator.validate(patient)
        if any(i.severity == Severity.ERROR for i in remaining):
            raise ValidationError(f"Could not fix: {remaining}")
    
    return patient
```

### Fix 2: Enforce Age Gates at Condition Selection Time

```python
def _select_conditions(self, age_months: int, seed) -> list[str]:
    eligible = []
    for condition_key, definition in self.conditions.items():
        min_age = definition.get('demographics', {}).get('age_months', {}).get('min', 0)
        max_age = definition.get('demographics', {}).get('age_months', {}).get('max', 252)
        
        if min_age <= age_months <= max_age:
            eligible.append(condition_key)
    
    # Select only from eligible conditions
    return self._weighted_select(eligible, seed)
```

### Fix 3: Enforce Required Medications

```python
def _generate_medications(self, patient) -> list[Medication]:
    meds = []
    
    for condition in patient.active_conditions:
        required = self.condition_service.get_required_medications(condition.key)
        for req in required:
            if not any(m.category == req.category for m in meds):
                meds.append(self._create_medication(req, condition))
    
    return meds
```

### Fix 4: Clamp All Dates to DOB Floor

```python
def _generate_date(self, target: date, patient: Patient) -> date:
    return max(target, patient.date_of_birth)
```

### Fix 5: Complete ICD-10 Codes in YAML

Add missing codes:
```yaml
gerd:
  icd10_primary: "K21.0"
  
swimmers_ear:
  display_name: "Swimmer's Ear"
  icd10_primary: "H60.339"
  
allergic_rhinitis:
  icd10_primary: "J30.9"
  
cough:
  icd10_primary: "R05.9"

eczema:
  icd10_primary: "L30.9"
```

---

## Summary

| Issue | Root Cause Hypothesis | Fix Location |
|-------|----------------------|--------------|
| ADHD at 1 month | No age gate on condition selection | `engine.py` + `conditions.yaml` |
| T1D without insulin | No mandatory medication enforcement | `engine.py` + condition definitions |
| R69 codes | Missing ICD-10 in YAML | `conditions.yaml` |
| Pre-DOB dates | No temporal floor on date generation | Date generation functions |
| Fontanelle in 3yo | Narrative not age-constrained | Narrative generation / reconciliation |

**First step:** Add logging to confirm whether `PatientValidator.validate()` is being called and what it returns. If it's finding issues but they're not being fixed, the problem is in the fix application. If it's not finding issues, the problem is in the validation rules themselves.
