# Oread Evolution Spec: Quality & Dynamic Knowledge

**Purpose:** Implementation specification for Claude Code to evolve the synthetic patient generation system from YAML-dependent to dynamically-grounded with robust validation.

**Priority:** High - current system produces clinically indefensible errors (wrong ICD-10 codes, missing labs for chronic conditions, narrative/structured data inconsistencies, temporal impossibilities).

---

## Problem Statement

The current Oread system has critical quality gaps:

1. **YAML Bottleneck:** If a condition isn't in `conditions.yaml`, it gets garbage ICD-10 code `R69`, no labs, no specific treatment. This doesn't scale to rare conditions.

2. **Lab Generation Gate Bug:** In `engine.py` line ~678, labs only generate for `ACUTE_ILLNESS`, `URGENT_CARE`, `ED_VISIT` encounter types. Chronic conditions like leukemia get zero monitoring labs at follow-up visits.

3. **No Validation:** Nothing checks that encounters occur after DOB, that ICD-10 codes are valid, or that conditions requiring medications actually have them.

4. **Narrative Hallucination:** LLM-generated notes can claim "patient is on active chemotherapy" when no chemo meds exist in structured data.

5. **Treatment Selection Bugs:** Medications are selected without checking patient allergies or tracking what's already been tried this episode (double-antibiotic problem).

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Knowledge Layer                               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │ Curated YAML    │  │ Web Search      │  │ LLM Medical     │ │
│  │ (common 50-100) │  │ (authoritative) │  │ Knowledge       │ │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘ │
│           └────────────────────┼────────────────────┘          │
│                                ▼                                │
│              ┌─────────────────────────────────┐               │
│              │   ConditionKnowledgeService     │               │
│              │   (unified interface + cache)   │               │
│              └─────────────────────────────────┘               │
└────────────────────────────────┬────────────────────────────────┘
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Generation Layer (existing)                    │
│  PedsEngine.generate() → encounters, conditions, meds, etc.     │
└────────────────────────────────┬────────────────────────────────┘
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Validation Layer (NEW)                         │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │ PatientValidator│  │ TemporalChecker │  │ ClinicalRules   │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
└────────────────────────────────┬────────────────────────────────┘
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Reconciliation Layer (NEW)                     │
│  ┌─────────────────────────┐  ┌─────────────────────────────┐  │
│  │ NarrativeClaimExtractor │  │ StructuredDataReconciler    │  │
│  └─────────────────────────┘  └─────────────────────────────┘  │
└────────────────────────────────┬────────────────────────────────┘
                                 ▼
                          Validated Patient
```

---

## Implementation Tasks

### Task 1: Create Validation Module

**New files to create:**
- `src/validators/__init__.py`
- `src/validators/patient_validator.py`
- `src/validators/models.py`

#### src/validators/models.py

```python
"""Validation result models."""

from enum import Enum
from pydantic import BaseModel, Field


class ValidationSeverity(str, Enum):
    CRITICAL = "critical"  # Breaks clinical plausibility
    ERROR = "error"        # Incorrect but recoverable
    WARNING = "warning"    # Suspicious but possibly valid


class ValidationType(str, Enum):
    TEMPORAL = "temporal"
    CODING = "coding"
    MEDICATION = "medication"
    LAB = "lab"
    GROWTH = "growth"
    IMMUNIZATION = "immunization"
    NARRATIVE = "narrative"
    CONSISTENCY = "consistency"


class ValidationIssue(BaseModel):
    """A single validation issue found in a patient record."""
    type: ValidationType
    severity: ValidationSeverity
    message: str
    path: str | None = None  # JSONPath to the problematic field
    suggested_fix: str | None = None
    auto_fixable: bool = False


class ValidationResult(BaseModel):
    """Complete validation result for a patient."""
    valid: bool = Field(description="True if no critical/error issues")
    issues: list[ValidationIssue] = Field(default_factory=list)
    
    @property
    def critical_issues(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.CRITICAL]
    
    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.ERROR]
    
    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]
```

#### src/validators/patient_validator.py

```python
"""
Post-generation validation for synthetic patients.

Catches clinical implausibilities, coding errors, and internal inconsistencies
that would make a patient record unusable for training or testing.
"""

from datetime import date, timedelta
from typing import TYPE_CHECKING

from .models import ValidationIssue, ValidationResult, ValidationSeverity, ValidationType

if TYPE_CHECKING:
    from src.models import Patient, Encounter, Condition, GrowthMeasurement


class PatientValidator:
    """
    Validates synthetic patient records for clinical plausibility.
    
    Validation categories:
    1. Temporal consistency (all events after DOB)
    2. Code quality (no garbage ICD-10 codes)
    3. Condition-medication consistency
    4. Chronic condition monitoring requirements
    5. Growth trajectory plausibility
    6. Immunization series integrity
    """
    
    # ICD-10 codes that should never be primary diagnoses
    GARBAGE_CODES = {
        "R69",    # Illness, unspecified
        "R99",    # Ill-defined and unknown cause of mortality
        "Z00.00", # Encounter for general adult medical examination without abnormal findings
    }
    
    # Conditions that require medications when active
    # Maps condition keywords to medication keywords that should be present
    CONDITIONS_REQUIRING_MEDS = {
        "leukemia": ["mercaptopurine", "methotrexate", "vincristine", "chemotherapy", "6-mp"],
        "lymphoma": ["chemotherapy", "rituximab", "prednisone"],
        "type 1 diabetes": ["insulin"],
        "type 2 diabetes": ["metformin", "insulin", "glipizide", "jardiance"],
        "hypothyroidism": ["levothyroxine", "synthroid"],
        "hyperthyroidism": ["methimazole", "ptu"],
        "seizure": ["levetiracetam", "keppra", "valproic", "lamotrigine", "phenobarbital"],
        "epilepsy": ["levetiracetam", "keppra", "valproic", "lamotrigine", "phenobarbital"],
        "asthma": ["albuterol", "fluticasone", "budesonide", "montelukast"],
        "adhd": ["methylphenidate", "adderall", "vyvanse", "strattera", "guanfacine"],
    }
    
    # Conditions that require labs at chronic follow-up
    CONDITIONS_REQUIRING_LABS = {
        "leukemia": ["cbc", "complete blood count", "metabolic panel", "cmp"],
        "lymphoma": ["cbc", "complete blood count", "metabolic panel"],
        "type 1 diabetes": ["hemoglobin a1c", "hba1c", "glucose"],
        "type 2 diabetes": ["hemoglobin a1c", "hba1c", "glucose", "lipid"],
        "hypothyroidism": ["tsh", "thyroid"],
        "chronic kidney disease": ["creatinine", "bun", "gfr", "cmp"],
    }
    
    def validate(self, patient: "Patient") -> ValidationResult:
        """
        Run all validation checks on a patient.
        
        Returns ValidationResult with all issues found.
        """
        issues: list[ValidationIssue] = []
        
        # Run all validators
        issues.extend(self._validate_temporal_consistency(patient))
        issues.extend(self._validate_icd10_codes(patient))
        issues.extend(self._validate_condition_medications(patient))
        issues.extend(self._validate_chronic_condition_labs(patient))
        issues.extend(self._validate_growth_trajectory(patient))
        issues.extend(self._validate_immunization_series(patient))
        issues.extend(self._validate_encounter_internal_consistency(patient))
        
        # Determine overall validity (no critical or error issues)
        has_blocking_issues = any(
            i.severity in (ValidationSeverity.CRITICAL, ValidationSeverity.ERROR)
            for i in issues
        )
        
        return ValidationResult(
            valid=not has_blocking_issues,
            issues=issues,
        )
    
    def _validate_temporal_consistency(self, patient: "Patient") -> list[ValidationIssue]:
        """Ensure all events occur after date of birth."""
        issues = []
        dob = patient.demographics.date_of_birth
        
        # Check encounters
        for i, enc in enumerate(patient.encounters):
            enc_date = enc.date.date() if hasattr(enc.date, 'date') else enc.date
            if enc_date < dob:
                issues.append(ValidationIssue(
                    type=ValidationType.TEMPORAL,
                    severity=ValidationSeverity.CRITICAL,
                    message=f"Encounter dated {enc_date} is before DOB {dob}",
                    path=f"encounters[{i}].date",
                    suggested_fix=f"Set encounter date to {dob + timedelta(days=3)} or later",
                    auto_fixable=True,
                ))
        
        # Check conditions
        for i, cond in enumerate(patient.problem_list):
            if cond.onset_date and cond.onset_date < dob:
                issues.append(ValidationIssue(
                    type=ValidationType.TEMPORAL,
                    severity=ValidationSeverity.CRITICAL,
                    message=f"Condition '{cond.display_name}' onset {cond.onset_date} before DOB {dob}",
                    path=f"problem_list[{i}].onset_date",
                    auto_fixable=True,
                ))
        
        # Check immunizations
        for i, imm in enumerate(patient.immunization_record):
            if imm.date and imm.date < dob:
                issues.append(ValidationIssue(
                    type=ValidationType.TEMPORAL,
                    severity=ValidationSeverity.CRITICAL,
                    message=f"Immunization '{imm.vaccine_name}' dated {imm.date} before DOB {dob}",
                    path=f"immunization_record[{i}].date",
                    auto_fixable=True,
                ))
        
        return issues
    
    def _validate_icd10_codes(self, patient: "Patient") -> list[ValidationIssue]:
        """Check for garbage/placeholder ICD-10 codes."""
        issues = []
        
        for i, cond in enumerate(patient.problem_list):
            code = cond.code.code if cond.code else None
            if code in self.GARBAGE_CODES:
                issues.append(ValidationIssue(
                    type=ValidationType.CODING,
                    severity=ValidationSeverity.ERROR,
                    message=f"Invalid ICD-10 code '{code}' for '{cond.display_name}'",
                    path=f"problem_list[{i}].code.code",
                    suggested_fix=f"Look up correct ICD-10 for '{cond.display_name}'",
                    auto_fixable=True,  # Can be fixed via ConditionKnowledgeService
                ))
        
        return issues
    
    def _validate_condition_medications(self, patient: "Patient") -> list[ValidationIssue]:
        """Check that conditions requiring medications have them."""
        issues = []
        
        # Get active condition names (lowercase for matching)
        active_conditions = [
            c.display_name.lower() 
            for c in patient.problem_list 
            if c.clinical_status.value == "active"
        ]
        
        # Get active medication names (lowercase)
        active_meds = [
            m.display_name.lower() 
            for m in patient.medication_list 
            if m.status.value == "active"
        ]
        
        for condition_keyword, required_med_keywords in self.CONDITIONS_REQUIRING_MEDS.items():
            # Check if patient has this condition
            has_condition = any(condition_keyword in c for c in active_conditions)
            
            if has_condition:
                # Check if they have any of the required medications
                has_required_med = any(
                    any(med_kw in med for med_kw in required_med_keywords)
                    for med in active_meds
                )
                
                if not has_required_med:
                    issues.append(ValidationIssue(
                        type=ValidationType.MEDICATION,
                        severity=ValidationSeverity.ERROR,
                        message=f"Active '{condition_keyword}' condition has no expected medications",
                        path="medication_list",
                        suggested_fix=f"Add appropriate medications for {condition_keyword}",
                        auto_fixable=True,
                    ))
        
        return issues
    
    def _validate_chronic_condition_labs(self, patient: "Patient") -> list[ValidationIssue]:
        """Check that chronic conditions have monitoring labs at follow-up visits."""
        issues = []
        
        # Get active chronic condition keywords
        active_conditions = [
            c.display_name.lower()
            for c in patient.problem_list
            if c.clinical_status.value == "active"
        ]
        
        # Find chronic follow-up encounters
        chronic_followups = [
            enc for enc in patient.encounters
            if enc.type.value == "chronic-followup"
        ]
        
        for condition_keyword, required_lab_keywords in self.CONDITIONS_REQUIRING_LABS.items():
            has_condition = any(condition_keyword in c for c in active_conditions)
            
            if has_condition and chronic_followups:
                # Check if ANY follow-up has labs
                has_any_labs = False
                for enc in chronic_followups:
                    if enc.lab_results:
                        lab_names = [l.display_name.lower() for l in enc.lab_results]
                        if any(
                            any(kw in lab for kw in required_lab_keywords)
                            for lab in lab_names
                        ):
                            has_any_labs = True
                            break
                
                if not has_any_labs:
                    issues.append(ValidationIssue(
                        type=ValidationType.LAB,
                        severity=ValidationSeverity.ERROR,
                        message=f"'{condition_keyword}' has {len(chronic_followups)} follow-up(s) but no monitoring labs",
                        path="encounters[].lab_results",
                        suggested_fix=f"Add {required_lab_keywords[0]} labs to chronic follow-up encounters",
                        auto_fixable=True,
                    ))
        
        return issues
    
    def _validate_growth_trajectory(self, patient: "Patient") -> list[ValidationIssue]:
        """Check for implausible growth patterns."""
        issues = []
        
        if len(patient.growth_data) < 2:
            return issues
        
        # Sort by date
        sorted_growth = sorted(patient.growth_data, key=lambda g: g.date)
        
        for i in range(1, len(sorted_growth)):
            prev = sorted_growth[i - 1]
            curr = sorted_growth[i]
            
            days_diff = (curr.date - prev.date).days
            if days_diff <= 0:
                continue
            
            # Check for identical weights over >14 days (implausible)
            if prev.weight_kg and curr.weight_kg:
                if abs(curr.weight_kg - prev.weight_kg) < 0.01 and days_diff > 14:
                    issues.append(ValidationIssue(
                        type=ValidationType.GROWTH,
                        severity=ValidationSeverity.WARNING,
                        message=f"Identical weight ({curr.weight_kg}kg) across {days_diff} days",
                        path=f"growth_data[{i}]",
                        suggested_fix="Regenerate growth measurement with appropriate trajectory",
                    ))
            
            # Check for weight loss in infants (usually pathological)
            if prev.weight_kg and curr.weight_kg:
                age_months = (curr.date - patient.demographics.date_of_birth).days // 30
                weight_change = curr.weight_kg - prev.weight_kg
                
                if age_months < 12 and weight_change < -0.2 and days_diff > 7:
                    issues.append(ValidationIssue(
                        type=ValidationType.GROWTH,
                        severity=ValidationSeverity.WARNING,
                        message=f"Infant weight loss ({weight_change:.2f}kg) over {days_diff} days",
                        path=f"growth_data[{i}]",
                        suggested_fix="Verify this is intentional (e.g., FTT scenario)",
                    ))
        
        return issues
    
    def _validate_immunization_series(self, patient: "Patient") -> list[ValidationIssue]:
        """Check for gaps in immunization series."""
        issues = []
        
        # Group immunizations by vaccine
        by_vaccine: dict[str, list] = {}
        for imm in patient.immunization_record:
            name = imm.vaccine_name
            if name not in by_vaccine:
                by_vaccine[name] = []
            by_vaccine[name].append(imm)
        
        # Check for series gaps
        expected_doses = {
            "DTaP": 5,
            "Hib": 4,
            "PCV": 4,
            "IPV": 4,
            "HepB": 3,
            "MMR": 2,
            "VAR": 2,
            "HepA": 2,
            "RV": 3,
        }
        
        for vaccine, expected in expected_doses.items():
            if vaccine in by_vaccine:
                doses = by_vaccine[vaccine]
                dose_numbers = sorted([d.dose_number for d in doses if d.dose_number])
                
                # Check for gaps (e.g., [1, 2, 4, 5] missing 3)
                if dose_numbers:
                    for i in range(1, max(dose_numbers)):
                        if i not in dose_numbers:
                            issues.append(ValidationIssue(
                                type=ValidationType.IMMUNIZATION,
                                severity=ValidationSeverity.WARNING,
                                message=f"{vaccine} series has dose #{i} missing (has doses {dose_numbers})",
                                path="immunization_record",
                                suggested_fix=f"Add {vaccine} dose #{i} or remove later doses",
                            ))
        
        return issues
    
    def _validate_encounter_internal_consistency(self, patient: "Patient") -> list[ValidationIssue]:
        """Check internal consistency within encounters."""
        issues = []
        
        for i, enc in enumerate(patient.encounters):
            # Check for narrative mentioning things not in structured data
            if enc.narrative_note:
                narrative_lower = enc.narrative_note.lower()
                
                # Check for chemotherapy mention without chemo meds
                chemo_keywords = ["chemotherapy", "chemo protocol", "maintenance therapy", "6-mp", "methotrexate"]
                mentions_chemo = any(kw in narrative_lower for kw in chemo_keywords)
                
                if mentions_chemo:
                    # Check if prescriptions or patient meds include chemo
                    rx_names = [p.display_name.lower() for p in (enc.prescriptions or [])]
                    all_meds = [m.display_name.lower() for m in patient.medication_list]
                    
                    has_chemo_med = any(
                        any(kw in med for kw in ["mercaptopurine", "methotrexate", "vincristine", "6-mp"])
                        for med in (rx_names + all_meds)
                    )
                    
                    if not has_chemo_med:
                        issues.append(ValidationIssue(
                            type=ValidationType.NARRATIVE,
                            severity=ValidationSeverity.ERROR,
                            message=f"Encounter narrative mentions chemotherapy but no chemo meds in record",
                            path=f"encounters[{i}].narrative_note",
                            suggested_fix="Add chemotherapy medications or regenerate narrative",
                            auto_fixable=True,
                        ))
        
        return issues
```

#### src/validators/__init__.py

```python
"""Patient validation module."""

from .patient_validator import PatientValidator
from .models import ValidationResult, ValidationIssue, ValidationSeverity, ValidationType

__all__ = [
    "PatientValidator",
    "ValidationResult", 
    "ValidationIssue",
    "ValidationSeverity",
    "ValidationType",
]
```

---

### Task 2: Create Condition Knowledge Service

**New files to create:**
- `src/knowledge/__init__.py`
- `src/knowledge/condition_service.py`
- `src/knowledge/models.py`
- `src/knowledge/cache.py`

This service provides a unified interface for condition knowledge, falling back from YAML → cache → web search → LLM when needed.

#### src/knowledge/models.py

```python
"""Models for condition knowledge."""

from pydantic import BaseModel, Field


class LabDefinition(BaseModel):
    """Definition of a lab test."""
    name: str
    loinc: str | None = None
    value_type: str = "binary"  # "binary" or "numeric"
    unit: str | None = None
    normal_range_low: float | None = None
    normal_range_high: float | None = None
    probability_abnormal: float = 0.3
    required_at_followup: bool = False
    monitoring_frequency: str | None = None


class MedicationDefinition(BaseModel):
    """Definition of a medication for a condition."""
    agent: str
    rxnorm: str | None = None
    dose_mg_kg: float | None = None
    max_dose_mg: float | None = None
    frequency: str | None = None
    route: str = "oral"
    indication: str | None = None
    line: str = "first"  # "first", "second", "alternative"
    contraindicated_by: list[str] = Field(default_factory=list)
    escalation_from: list[str] = Field(default_factory=list)


class ExamFinding(BaseModel):
    """Physical exam finding."""
    system: str
    finding: str
    probability: float = 0.8


class ConditionDefinition(BaseModel):
    """
    Complete condition definition that can come from YAML or dynamic retrieval.
    
    This is the unified interface - both curated YAML conditions and 
    dynamically-retrieved conditions produce this structure.
    """
    condition_key: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    
    # Coding
    icd10_codes: list[str] = Field(default_factory=list)
    icd10_primary: str | None = None  # Preferred code
    snomed_code: str | None = None
    
    # Classification
    category: str = "acute"  # "acute" or "chronic"
    body_system: str | None = None
    
    # Clinical presentation
    typical_symptoms: list[str] = Field(default_factory=list)
    physical_exam_findings: list[ExamFinding] = Field(default_factory=list)
    
    # Diagnostics
    labs: list[LabDefinition] = Field(default_factory=list)
    imaging: list[dict] = Field(default_factory=list)
    
    # Treatment
    medications: list[MedicationDefinition] = Field(default_factory=list)
    treatment_approach: str | None = None
    managed_by_specialty: bool = False
    specialty: str | None = None
    
    # Monitoring (for chronic conditions)
    requires_monitoring_labs: bool = False
    monitoring_lab_types: list[str] = Field(default_factory=list)
    followup_frequency: str | None = None
    
    # Metadata
    source: str = "yaml"  # "yaml", "cache", "web_search", "llm"
    needs_verification: bool = False
    confidence: float = 1.0


class ConditionLookupResult(BaseModel):
    """Result of a condition lookup."""
    found: bool
    definition: ConditionDefinition | None = None
    source: str | None = None  # Where it came from
    cached: bool = False
```

#### src/knowledge/cache.py

```python
"""Disk cache for dynamically-retrieved condition knowledge."""

import json
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any


class ConditionCache:
    """
    Persistent cache for condition definitions retrieved via web search.
    
    Stores JSON files on disk with TTL-based expiration.
    """
    
    DEFAULT_TTL_DAYS = 30  # Cache entries valid for 30 days
    
    def __init__(self, cache_dir: Path, ttl_days: int = DEFAULT_TTL_DAYS):
        self.cache_dir = cache_dir
        self.ttl = timedelta(days=ttl_days)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_cache_key(self, condition_name: str) -> str:
        """Generate a filesystem-safe cache key."""
        normalized = condition_name.lower().strip()
        hash_suffix = hashlib.md5(normalized.encode()).hexdigest()[:8]
        safe_name = "".join(c if c.isalnum() else "_" for c in normalized)[:50]
        return f"{safe_name}_{hash_suffix}"
    
    def _get_cache_path(self, condition_name: str) -> Path:
        return self.cache_dir / f"{self._get_cache_key(condition_name)}.json"
    
    def get(self, condition_name: str) -> dict | None:
        """Get cached condition definition if valid."""
        path = self._get_cache_path(condition_name)
        
        if not path.exists():
            return None
        
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            
            # Check TTL
            cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
            if datetime.now() - cached_at > self.ttl:
                path.unlink()  # Expired, delete
                return None
            
            return data.get("definition")
        except (json.JSONDecodeError, KeyError):
            return None
    
    def set(self, condition_name: str, definition: dict) -> None:
        """Cache a condition definition."""
        path = self._get_cache_path(condition_name)
        
        data = {
            "_cached_at": datetime.now().isoformat(),
            "_condition_name": condition_name,
            "definition": definition,
        }
        
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
    
    def invalidate(self, condition_name: str) -> bool:
        """Remove a cached entry."""
        path = self._get_cache_path(condition_name)
        if path.exists():
            path.unlink()
            return True
        return False
    
    def clear_all(self) -> int:
        """Clear entire cache. Returns count of entries removed."""
        count = 0
        for path in self.cache_dir.glob("*.json"):
            path.unlink()
            count += 1
        return count
```

#### src/knowledge/condition_service.py

```python
"""
Condition Knowledge Service.

Provides unified access to condition definitions from multiple sources:
1. Curated YAML (authoritative, no network calls)
2. Disk cache (previously retrieved conditions)
3. Web search (grounded retrieval for unknown conditions)
4. LLM knowledge (fallback when web search unavailable)

The service handles the complexity of multi-source retrieval so the
rest of the system can just call `get_condition(name)`.
"""

from pathlib import Path
from typing import Callable, Any
import re

from .models import ConditionDefinition, ConditionLookupResult, LabDefinition, MedicationDefinition, ExamFinding
from .cache import ConditionCache

# Type for web search function
WebSearchFn = Callable[[str], list[dict]]  # query -> list of {url, title, snippet}


class ConditionKnowledgeService:
    """
    Unified condition knowledge retrieval service.
    
    Usage:
        service = ConditionKnowledgeService(
            yaml_conditions=loaded_yaml,
            llm_client=claude_client,
            cache_dir=Path("./cache/conditions"),
            web_search_fn=my_web_search,  # Optional
        )
        
        result = service.get_condition("acute lymphoblastic leukemia")
        if result.found:
            icd10 = result.definition.icd10_primary
    """
    
    # Authoritative medical domains for web search filtering
    AUTHORITATIVE_DOMAINS = [
        "aap.org", "aappublications.org",
        "cdc.gov", "nih.gov", "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov",
        "who.int",
        "uptodate.com",
        "mayoclinic.org", "clevelandclinic.org",
        "childrenshospital.org", "chop.edu", "seattlechildrens.org",
        "acponline.org", "aafp.org",
        "merckmanuals.com",
    ]
    
    def __init__(
        self,
        yaml_conditions: dict,
        llm_client: Any,  # LLMClient from src.llm
        cache_dir: Path,
        web_search_fn: WebSearchFn | None = None,
        web_fetch_fn: Callable[[str], str] | None = None,
    ):
        self.yaml_conditions = yaml_conditions
        self.llm = llm_client
        self.cache = ConditionCache(cache_dir)
        self.web_search = web_search_fn
        self.web_fetch = web_fetch_fn
        
        # Build lookup index from YAML
        self._name_to_key: dict[str, str] = {}
        self._build_yaml_index()
    
    def _build_yaml_index(self) -> None:
        """Build reverse lookup from display names and aliases to condition keys."""
        for key, data in self.yaml_conditions.items():
            if key.startswith("_") or not isinstance(data, dict):
                continue
            
            # Index the key itself
            self._name_to_key[key.lower()] = key
            
            # Index display name
            display = data.get("display_name", "")
            if display:
                self._name_to_key[display.lower()] = key
            
            # Index aliases
            for alias in data.get("aliases", []):
                self._name_to_key[alias.lower()] = key
    
    def _normalize_name(self, name: str) -> str:
        """Normalize a condition name for lookup."""
        return name.lower().strip()
    
    def get_condition(self, name: str) -> ConditionLookupResult:
        """
        Get a condition definition by name.
        
        Tries sources in order:
        1. Curated YAML
        2. Disk cache
        3. Web search + LLM structuring
        4. LLM knowledge only
        
        Returns ConditionLookupResult with found=True/False and definition if found.
        """
        normalized = self._normalize_name(name)
        
        # Layer 1: Check curated YAML
        if yaml_key := self._name_to_key.get(normalized):
            definition = self._yaml_to_definition(yaml_key)
            return ConditionLookupResult(
                found=True,
                definition=definition,
                source="yaml",
                cached=False,
            )
        
        # Layer 2: Check cache
        if cached_data := self.cache.get(normalized):
            try:
                definition = ConditionDefinition.model_validate(cached_data)
                return ConditionLookupResult(
                    found=True,
                    definition=definition,
                    source="cache",
                    cached=True,
                )
            except Exception:
                pass  # Invalid cache entry, continue to retrieval
        
        # Layer 3: Web search + LLM (if web search available)
        if self.web_search:
            definition = self._retrieve_via_web_search(name)
            if definition:
                # Cache for future use
                self.cache.set(normalized, definition.model_dump())
                return ConditionLookupResult(
                    found=True,
                    definition=definition,
                    source="web_search",
                    cached=False,
                )
        
        # Layer 4: LLM knowledge only (no grounding)
        definition = self._retrieve_via_llm_only(name)
        if definition:
            # Cache but mark as lower confidence
            definition.confidence = 0.7
            definition.needs_verification = True
            self.cache.set(normalized, definition.model_dump())
            return ConditionLookupResult(
                found=True,
                definition=definition,
                source="llm",
                cached=False,
            )
        
        # Not found anywhere
        return ConditionLookupResult(found=False)
    
    def _yaml_to_definition(self, key: str) -> ConditionDefinition:
        """Convert YAML condition data to ConditionDefinition."""
        data = self.yaml_conditions[key]
        
        # Extract ICD-10 codes
        billing = data.get("billing_codes", {})
        icd10_raw = billing.get("icd10", [])
        icd10_codes = icd10_raw if isinstance(icd10_raw, list) else [icd10_raw]
        
        # Extract labs
        labs = []
        for lab_data in data.get("diagnostics", {}).get("labs", []):
            if isinstance(lab_data, dict):
                labs.append(LabDefinition(
                    name=lab_data.get("name", ""),
                    loinc=lab_data.get("loinc"),
                    value_type=lab_data.get("value_type", "binary"),
                    unit=lab_data.get("unit"),
                    normal_range_low=lab_data.get("normal_range_low"),
                    normal_range_high=lab_data.get("normal_range_high"),
                    probability_abnormal=lab_data.get("probability_abnormal", 0.3),
                    required_at_followup=lab_data.get("required_at_followup", False),
                ))
        
        # Extract medications
        medications = []
        for med_data in data.get("treatment", {}).get("medications", []):
            if isinstance(med_data, dict):
                medications.append(MedicationDefinition(
                    agent=med_data.get("agent", ""),
                    rxnorm=med_data.get("rxnorm"),
                    dose_mg_kg=med_data.get("dose_mg_kg"),
                    max_dose_mg=med_data.get("max_dose_mg"),
                    frequency=med_data.get("frequency"),
                    route=med_data.get("route", "oral"),
                    indication=med_data.get("indication"),
                ))
        
        # Extract physical exam findings
        exam_findings = []
        for finding in data.get("presentation", {}).get("physical_exam", []):
            if isinstance(finding, dict):
                exam_findings.append(ExamFinding(
                    system=finding.get("system", ""),
                    finding=finding.get("finding", ""),
                    probability=finding.get("probability", 0.8),
                ))
        
        # Determine if this condition requires monitoring labs
        category = data.get("category", "acute")
        monitoring = data.get("monitoring_requirements", {})
        requires_monitoring = bool(monitoring.get("required_labs_at_followup"))
        
        return ConditionDefinition(
            condition_key=key,
            display_name=data.get("display_name", key.replace("_", " ").title()),
            aliases=data.get("aliases", []),
            icd10_codes=icd10_codes,
            icd10_primary=icd10_codes[0] if icd10_codes else None,
            snomed_code=billing.get("snomed"),
            category=category,
            body_system=data.get("system"),
            typical_symptoms=[s.get("name", "") for s in data.get("presentation", {}).get("symptoms", [])],
            physical_exam_findings=exam_findings,
            labs=labs,
            imaging=data.get("diagnostics", {}).get("imaging", []),
            medications=medications,
            treatment_approach=data.get("treatment", {}).get("approach"),
            managed_by_specialty=data.get("treatment", {}).get("managed_by_specialty", False),
            specialty=data.get("treatment", {}).get("specialty"),
            requires_monitoring_labs=requires_monitoring,
            monitoring_lab_types=monitoring.get("required_labs_at_followup", []),
            followup_frequency=monitoring.get("followup_frequency"),
            source="yaml",
            confidence=1.0,
        )
    
    def _retrieve_via_web_search(self, condition_name: str) -> ConditionDefinition | None:
        """Use web search to retrieve and structure condition knowledge."""
        
        # Execute searches for different aspects
        search_queries = [
            f"{condition_name} ICD-10 code",
            f"{condition_name} pediatric treatment guidelines",
            f"{condition_name} diagnosis criteria symptoms",
        ]
        
        all_results = []
        for query in search_queries:
            try:
                results = self.web_search(query)
                all_results.extend(results[:5])  # Top 5 per query
            except Exception:
                continue
        
        if not all_results:
            return None
        
        # Filter for authoritative sources
        authoritative = [
            r for r in all_results
            if any(domain in r.get("url", "") for domain in self.AUTHORITATIVE_DOMAINS)
        ]
        
        # Use all results if no authoritative ones found
        sources_to_use = authoritative if authoritative else all_results[:10]
        
        # Fetch full content from top sources if fetch function available
        source_content = []
        if self.web_fetch:
            for result in sources_to_use[:3]:  # Fetch top 3
                try:
                    content = self.web_fetch(result["url"])
                    source_content.append({
                        "url": result["url"],
                        "title": result.get("title", ""),
                        "content": content[:5000],  # Limit content length
                    })
                except Exception:
                    # Fall back to snippet
                    source_content.append({
                        "url": result["url"],
                        "title": result.get("title", ""),
                        "content": result.get("snippet", ""),
                    })
        else:
            # Use snippets only
            source_content = [
                {
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "content": r.get("snippet", ""),
                }
                for r in sources_to_use
            ]
        
        # Structure with LLM
        return self._structure_with_llm(condition_name, source_content, grounded=True)
    
    def _retrieve_via_llm_only(self, condition_name: str) -> ConditionDefinition | None:
        """Use LLM's training knowledge only (no grounding)."""
        return self._structure_with_llm(condition_name, [], grounded=False)
    
    def _structure_with_llm(
        self, 
        condition_name: str, 
        sources: list[dict],
        grounded: bool,
    ) -> ConditionDefinition | None:
        """Use LLM to create structured condition definition."""
        
        if grounded and sources:
            source_text = "\n\n".join([
                f"Source: {s['title']} ({s['url']})\n{s['content']}"
                for s in sources
            ])
            grounding_instruction = f"""Based on these medical sources:

{source_text}

"""
        else:
            grounding_instruction = """Based on your medical knowledge, """
        
        prompt = f"""{grounding_instruction}Create a structured condition definition for "{condition_name}" in pediatric patients.

Return a JSON object with these fields:
{{
  "condition_key": "snake_case_key",
  "display_name": "Standard Clinical Name",
  "aliases": ["other names", "abbreviations"],
  "icd10_codes": ["X00.0", "X00.1"],  // All applicable ICD-10-CM codes
  "icd10_primary": "X00.0",  // Most commonly used code
  "snomed_code": "12345678",  // SNOMED-CT code if known, null if not
  "category": "acute or chronic",
  "body_system": "e.g., respiratory, hematology_oncology, neurology",
  "typical_symptoms": ["symptom1", "symptom2"],
  "physical_exam_findings": [
    {{"system": "heent", "finding": "finding text", "probability": 0.8}}
  ],
  "labs": [
    {{
      "name": "Lab Name",
      "loinc": "12345-6",  // LOINC code if known
      "value_type": "numeric or binary",
      "unit": "unit if numeric",
      "normal_range_low": 0.0,  // if numeric
      "normal_range_high": 10.0,  // if numeric
      "required_at_followup": true/false
    }}
  ],
  "medications": [
    {{
      "agent": "Medication Name",
      "rxnorm": "12345",  // RxNorm code if known
      "dose_mg_kg": 10.0,  // typical dose
      "frequency": "BID",
      "route": "oral",
      "indication": "why prescribed",
      "line": "first, second, or alternative"
    }}
  ],
  "treatment_approach": "brief description",
  "managed_by_specialty": true/false,
  "specialty": "specialty name if managed_by_specialty is true",
  "requires_monitoring_labs": true/false,
  "monitoring_lab_types": ["cbc", "cmp"],  // if requires_monitoring_labs
  "followup_frequency": "e.g., monthly, every 3 months",
  "needs_verification": true/false  // true if uncertain about any codes
}}

Be precise with medical codes. If you're uncertain about a specific ICD-10 or LOINC code, set needs_verification to true.
For pediatric-specific conditions, include age-appropriate dosing and monitoring.
For chronic conditions (like malignancies, diabetes, etc.), always set requires_monitoring_labs to true."""

        try:
            response = self.llm.generate(
                prompt,
                system="You are a pediatric medical knowledge system. Provide accurate, structured condition information. Always use real ICD-10-CM, SNOMED-CT, LOINC, and RxNorm codes - never invent codes.",
                temperature=0.2,  # Low temperature for factual content
            )
            
            # Parse JSON from response
            # Handle potential markdown code blocks
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response
            
            import json
            data = json.loads(json_str)
            
            # Add metadata
            data["source"] = "web_search" if grounded else "llm"
            data["confidence"] = 0.9 if grounded else 0.7
            
            return ConditionDefinition.model_validate(data)
            
        except Exception as e:
            # Log error but don't crash
            print(f"Warning: Failed to structure condition '{condition_name}': {e}")
            return None
    
    def get_condition_key(self, name: str) -> str | None:
        """
        Get the condition key for a name (for backward compatibility).
        
        Returns the YAML key if found, or a generated key for dynamic conditions.
        """
        result = self.get_condition(name)
        if result.found and result.definition:
            return result.definition.condition_key
        return None


# Convenience function for engine integration
def create_condition_service(
    yaml_conditions: dict,
    llm_client: Any,
    cache_dir: Path,
    web_search_fn: WebSearchFn | None = None,
    web_fetch_fn: Callable | None = None,
) -> ConditionKnowledgeService:
    """Factory function to create a ConditionKnowledgeService."""
    return ConditionKnowledgeService(
        yaml_conditions=yaml_conditions,
        llm_client=llm_client,
        cache_dir=cache_dir,
        web_search_fn=web_search_fn,
        web_fetch_fn=web_fetch_fn,
    )
```

#### src/knowledge/__init__.py

```python
"""Knowledge retrieval module."""

from .condition_service import ConditionKnowledgeService, create_condition_service
from .models import ConditionDefinition, ConditionLookupResult, LabDefinition, MedicationDefinition
from .cache import ConditionCache

__all__ = [
    "ConditionKnowledgeService",
    "create_condition_service",
    "ConditionDefinition",
    "ConditionLookupResult",
    "LabDefinition",
    "MedicationDefinition",
    "ConditionCache",
]
```

---

### Task 3: Create Reconciliation Module

**New files to create:**
- `src/reconciliation/__init__.py`
- `src/reconciliation/claim_extractor.py`
- `src/reconciliation/reconciler.py`
- `src/reconciliation/models.py`

#### src/reconciliation/models.py

```python
"""Models for narrative-structured reconciliation."""

from enum import Enum
from pydantic import BaseModel, Field


class ClaimType(str, Enum):
    MEDICATION = "medication"
    CONDITION = "condition"
    PROCEDURE = "procedure"
    LAB = "lab"
    VITAL = "vital"
    TREATMENT_STATUS = "treatment_status"
    SOCIAL = "social"


class NarrativeClaim(BaseModel):
    """A factual claim extracted from a narrative note."""
    claim_type: ClaimType
    claim_text: str  # The exact text making the claim
    structured_value: dict = Field(default_factory=dict)  # Parsed interpretation
    confidence: float = 0.8


class DiscrepancyType(str, Enum):
    MEDICATION_NOT_IN_STRUCTURED = "medication_not_in_structured"
    CONDITION_NOT_IN_STRUCTURED = "condition_not_in_structured"
    LAB_NOT_IN_STRUCTURED = "lab_not_in_structured"
    TREATMENT_STATUS_MISMATCH = "treatment_status_mismatch"
    VITAL_MISMATCH = "vital_mismatch"


class Discrepancy(BaseModel):
    """A discrepancy between narrative and structured data."""
    encounter_id: str
    claim: NarrativeClaim
    discrepancy_type: DiscrepancyType
    can_add_to_structured: bool = False
    requires_narrative_regeneration: bool = False
    suggested_fix: str | None = None


class ReconciliationResult(BaseModel):
    """Result of reconciling a patient's narratives with structured data."""
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    narratives_to_regenerate: list[str] = Field(default_factory=list)  # Encounter IDs
    
    @property
    def has_issues(self) -> bool:
        return len(self.discrepancies) > 0
    
    def get_discrepancies_for_encounter(self, encounter_id: str) -> list[Discrepancy]:
        return [d for d in self.discrepancies if d.encounter_id == encounter_id]
```

#### src/reconciliation/claim_extractor.py

```python
"""
Narrative claim extraction.

Extracts verifiable factual claims from clinical narrative notes
so they can be compared against structured data.
"""

import json
import re
from typing import Any

from .models import NarrativeClaim, ClaimType


class NarrativeClaimExtractor:
    """
    Extracts factual claims from clinical narratives using LLM.
    
    These claims can then be verified against structured data to
    ensure narrative-structured consistency.
    """
    
    EXTRACTION_PROMPT = """Extract all factual clinical claims from this note that could be verified against structured data.

Clinical Note:
{narrative}

Return a JSON array of claims. For each claim include:
- claim_type: One of "medication", "condition", "procedure", "lab", "vital", "treatment_status", "social"
- claim_text: The exact phrase from the note making this claim
- structured_value: Your interpretation as structured data (a dict)
- confidence: 0.0 to 1.0

Focus on extracting:
1. MEDICATION claims: "on chemotherapy", "taking amoxicillin", "current medications include..."
2. CONDITION claims: "has diabetes", "asthma well-controlled", "history of..."
3. TREATMENT_STATUS claims: "tolerating treatment well", "in remission", "on active protocol"
4. LAB claims: "labs normal", "CBC showed...", "recent bloodwork"
5. VITAL claims: specific vital sign values mentioned

Example output:
[
  {{
    "claim_type": "treatment_status",
    "claim_text": "currently on active chemotherapy protocol",
    "structured_value": {{"treatment_type": "chemotherapy", "status": "active"}},
    "confidence": 0.95
  }},
  {{
    "claim_type": "medication",
    "claim_text": "tolerating treatment well",
    "structured_value": {{"implies_active_medications": true}},
    "confidence": 0.8
  }}
]

Be exhaustive but precise. Only extract claims that could theoretically be verified against a patient record."""

    def __init__(self, llm_client: Any):
        self.llm = llm_client
    
    def extract(self, narrative: str) -> list[NarrativeClaim]:
        """
        Extract verifiable claims from a clinical narrative.
        
        Args:
            narrative: The clinical note text
            
        Returns:
            List of NarrativeClaim objects
        """
        if not narrative or len(narrative.strip()) < 20:
            return []
        
        try:
            response = self.llm.generate(
                self.EXTRACTION_PROMPT.format(narrative=narrative),
                system="You are a clinical NLP system. Extract factual claims precisely. Return only valid JSON.",
                temperature=0.2,
            )
            
            # Parse JSON from response
            json_str = self._extract_json(response)
            claims_data = json.loads(json_str)
            
            claims = []
            for item in claims_data:
                try:
                    # Convert claim_type string to enum
                    claim_type_str = item.get("claim_type", "").lower()
                    claim_type = self._map_claim_type(claim_type_str)
                    
                    claims.append(NarrativeClaim(
                        claim_type=claim_type,
                        claim_text=item.get("claim_text", ""),
                        structured_value=item.get("structured_value", {}),
                        confidence=item.get("confidence", 0.8),
                    ))
                except Exception:
                    continue  # Skip malformed claims
            
            return claims
            
        except Exception as e:
            # Don't crash on extraction failure
            print(f"Warning: Claim extraction failed: {e}")
            return []
    
    def _extract_json(self, response: str) -> str:
        """Extract JSON from response, handling markdown code blocks."""
        # Try to find JSON in code block
        json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            return json_match.group(1)
        
        # Try to find raw JSON array
        array_match = re.search(r'\[.*\]', response, re.DOTALL)
        if array_match:
            return array_match.group(0)
        
        return response
    
    def _map_claim_type(self, claim_type_str: str) -> ClaimType:
        """Map string to ClaimType enum."""
        mapping = {
            "medication": ClaimType.MEDICATION,
            "condition": ClaimType.CONDITION,
            "procedure": ClaimType.PROCEDURE,
            "lab": ClaimType.LAB,
            "vital": ClaimType.VITAL,
            "treatment_status": ClaimType.TREATMENT_STATUS,
            "social": ClaimType.SOCIAL,
        }
        return mapping.get(claim_type_str, ClaimType.CONDITION)
```

#### src/reconciliation/reconciler.py

```python
"""
Patient reconciliation.

Ensures bidirectional consistency between narrative notes and structured data.
When discrepancies are found, either the structured data is augmented or
the narrative is flagged for regeneration.
"""

from typing import TYPE_CHECKING, Any

from .models import (
    Discrepancy, 
    DiscrepancyType, 
    NarrativeClaim, 
    ClaimType,
    ReconciliationResult,
)
from .claim_extractor import NarrativeClaimExtractor

if TYPE_CHECKING:
    from src.models import Patient, Encounter


class PatientReconciler:
    """
    Reconciles narrative notes with structured patient data.
    
    Process:
    1. Extract claims from each encounter's narrative
    2. Compare claims to structured data
    3. Identify discrepancies
    4. Determine if structured data should be augmented or narrative regenerated
    
    Usage:
        reconciler = PatientReconciler(llm_client)
        result = reconciler.reconcile(patient)
        
        if result.narratives_to_regenerate:
            # Handle narratives that need regeneration
            pass
    """
    
    # Keywords that indicate chemotherapy
    CHEMO_KEYWORDS = [
        "chemotherapy", "chemo protocol", "maintenance therapy",
        "oncology protocol", "cancer treatment", "leukemia treatment",
    ]
    
    # Medication keywords that indicate actual chemotherapy agents
    CHEMO_MED_KEYWORDS = [
        "mercaptopurine", "6-mp", "methotrexate", "vincristine",
        "dexamethasone", "prednisone", "asparaginase", "daunorubicin",
        "cytarabine", "cyclophosphamide",
    ]
    
    def __init__(self, llm_client: Any):
        self.llm = llm_client
        self.claim_extractor = NarrativeClaimExtractor(llm_client)
    
    def reconcile(self, patient: "Patient") -> ReconciliationResult:
        """
        Reconcile all narrative notes with structured data.
        
        Args:
            patient: The Patient object to reconcile
            
        Returns:
            ReconciliationResult with discrepancies and action items
        """
        all_discrepancies: list[Discrepancy] = []
        
        for encounter in patient.encounters:
            if not encounter.narrative_note:
                continue
            
            # Extract claims from narrative
            claims = self.claim_extractor.extract(encounter.narrative_note)
            
            # Get structured facts for comparison
            structured = self._get_structured_facts(encounter, patient)
            
            # Find discrepancies
            discrepancies = self._find_discrepancies(
                encounter_id=encounter.id,
                claims=claims,
                structured=structured,
            )
            
            all_discrepancies.extend(discrepancies)
        
        # Determine which narratives need regeneration
        narratives_to_regen = list(set(
            d.encounter_id for d in all_discrepancies
            if d.requires_narrative_regeneration
        ))
        
        return ReconciliationResult(
            discrepancies=all_discrepancies,
            narratives_to_regenerate=narratives_to_regen,
        )
    
    def _get_structured_facts(
        self, 
        encounter: "Encounter", 
        patient: "Patient"
    ) -> dict:
        """Extract structured facts from encounter and patient for comparison."""
        return {
            # Encounter-specific data
            "encounter_prescriptions": [
                p.display_name.lower() for p in (encounter.prescriptions or [])
            ],
            "encounter_labs": [
                l.display_name.lower() for l in (encounter.lab_results or [])
            ],
            "encounter_diagnoses": [
                a.diagnosis.lower() for a in (encounter.assessment or [])
            ],
            
            # Patient-level data
            "active_medications": [
                m.display_name.lower() for m in patient.medication_list
                if m.status.value == "active"
            ],
            "all_medications": [
                m.display_name.lower() for m in patient.medication_list
            ],
            "active_conditions": [
                c.display_name.lower() for c in patient.problem_list
                if c.clinical_status.value == "active"
            ],
            "all_conditions": [
                c.display_name.lower() for c in patient.problem_list
            ],
        }
    
    def _find_discrepancies(
        self,
        encounter_id: str,
        claims: list[NarrativeClaim],
        structured: dict,
    ) -> list[Discrepancy]:
        """Compare claims to structured data and identify discrepancies."""
        discrepancies = []
        
        for claim in claims:
            if claim.claim_type == ClaimType.TREATMENT_STATUS:
                disc = self._check_treatment_status_claim(encounter_id, claim, structured)
                if disc:
                    discrepancies.append(disc)
            
            elif claim.claim_type == ClaimType.MEDICATION:
                disc = self._check_medication_claim(encounter_id, claim, structured)
                if disc:
                    discrepancies.append(disc)
            
            elif claim.claim_type == ClaimType.CONDITION:
                disc = self._check_condition_claim(encounter_id, claim, structured)
                if disc:
                    discrepancies.append(disc)
        
        return discrepancies
    
    def _check_treatment_status_claim(
        self,
        encounter_id: str,
        claim: NarrativeClaim,
        structured: dict,
    ) -> Discrepancy | None:
        """Check claims about treatment status (e.g., 'on chemotherapy')."""
        claim_lower = claim.claim_text.lower()
        
        # Check for chemotherapy mentions
        mentions_chemo = any(kw in claim_lower for kw in self.CHEMO_KEYWORDS)
        
        if mentions_chemo:
            # Verify patient has oncology condition
            has_onc_condition = any(
                any(kw in cond for kw in ["leukemia", "lymphoma", "cancer", "tumor", "malignancy"])
                for cond in structured["active_conditions"]
            )
            
            # Verify patient has chemo medications
            all_meds = structured["active_medications"] + structured["all_medications"]
            has_chemo_meds = any(
                any(kw in med for kw in self.CHEMO_MED_KEYWORDS)
                for med in all_meds
            )
            
            if has_onc_condition and not has_chemo_meds:
                return Discrepancy(
                    encounter_id=encounter_id,
                    claim=claim,
                    discrepancy_type=DiscrepancyType.TREATMENT_STATUS_MISMATCH,
                    can_add_to_structured=True,
                    requires_narrative_regeneration=False,
                    suggested_fix="Add chemotherapy medications to patient medication list",
                )
            
            if not has_onc_condition:
                return Discrepancy(
                    encounter_id=encounter_id,
                    claim=claim,
                    discrepancy_type=DiscrepancyType.TREATMENT_STATUS_MISMATCH,
                    can_add_to_structured=False,
                    requires_narrative_regeneration=True,
                    suggested_fix="Narrative mentions chemotherapy but patient has no oncology diagnosis",
                )
        
        return None
    
    def _check_medication_claim(
        self,
        encounter_id: str,
        claim: NarrativeClaim,
        structured: dict,
    ) -> Discrepancy | None:
        """Check claims about specific medications."""
        # Extract medication name from claim if possible
        med_name = claim.structured_value.get("medication_name", "").lower()
        
        if not med_name:
            return None
        
        # Check if medication exists in structured data
        all_meds = (
            structured["encounter_prescriptions"] + 
            structured["active_medications"] +
            structured["all_medications"]
        )
        
        med_found = any(med_name in med for med in all_meds)
        
        if not med_found:
            return Discrepancy(
                encounter_id=encounter_id,
                claim=claim,
                discrepancy_type=DiscrepancyType.MEDICATION_NOT_IN_STRUCTURED,
                can_add_to_structured=True,  # Could potentially add the medication
                requires_narrative_regeneration=False,
                suggested_fix=f"Add '{med_name}' to medication list or remove from narrative",
            )
        
        return None
    
    def _check_condition_claim(
        self,
        encounter_id: str,
        claim: NarrativeClaim,
        structured: dict,
    ) -> Discrepancy | None:
        """Check claims about conditions/diagnoses."""
        condition_name = claim.structured_value.get("condition_name", "").lower()
        
        if not condition_name:
            return None
        
        # Check if condition exists
        all_conditions = structured["active_conditions"] + structured["all_conditions"]
        condition_found = any(condition_name in cond for cond in all_conditions)
        
        if not condition_found:
            return Discrepancy(
                encounter_id=encounter_id,
                claim=claim,
                discrepancy_type=DiscrepancyType.CONDITION_NOT_IN_STRUCTURED,
                can_add_to_structured=False,
                requires_narrative_regeneration=True,
                suggested_fix=f"Narrative mentions '{condition_name}' not in problem list",
            )
        
        return None
```

#### src/reconciliation/__init__.py

```python
"""Narrative-structured reconciliation module."""

from .reconciler import PatientReconciler
from .claim_extractor import NarrativeClaimExtractor
from .models import (
    NarrativeClaim,
    ClaimType,
    Discrepancy,
    DiscrepancyType,
    ReconciliationResult,
)

__all__ = [
    "PatientReconciler",
    "NarrativeClaimExtractor",
    "NarrativeClaim",
    "ClaimType",
    "Discrepancy",
    "DiscrepancyType",
    "ReconciliationResult",
]
```

---

### Task 4: Integrate Into Engine

**File to modify:** `src/engines/engine.py`

Add these changes to the `PedsEngine` class:

#### 4.1 Update imports at top of engine.py

```python
# Add these imports
from src.validators import PatientValidator, ValidationResult
from src.knowledge import ConditionKnowledgeService, create_condition_service, ConditionDefinition
from src.reconciliation import PatientReconciler, ReconciliationResult
```

#### 4.2 Update PedsEngine.__init__

```python
def __init__(self, *args, use_llm: bool = True, messiness_level: int = 0, 
             web_search_fn=None, web_fetch_fn=None, **kwargs):
    super().__init__(*args, **kwargs)
    
    # Existing initialization...
    self._conditions = self._load_conditions(self.knowledge_dir)
    self._immunization_schedule = self._load_immunization_schedule(self.knowledge_dir)
    self._build_condition_lookups()
    self._build_immunization_lookups()
    self.use_llm = use_llm and self.llm is not None
    self.messiness_level = messiness_level
    self.messiness = MessinessInjector(level=messiness_level)
    
    # NEW: Initialize condition knowledge service
    cache_dir = self.knowledge_dir.parent / "cache" / "conditions"
    self.condition_service = create_condition_service(
        yaml_conditions=self._conditions,
        llm_client=self.llm,
        cache_dir=cache_dir,
        web_search_fn=web_search_fn,
        web_fetch_fn=web_fetch_fn,
    )
    
    # NEW: Initialize validator
    self.validator = PatientValidator()
    
    # NEW: Initialize reconciler (if LLM available)
    self.reconciler = PatientReconciler(self.llm) if self.llm else None
```

#### 4.3 Update the generate() method

Add validation and reconciliation at the end of the generate() method, before returning the patient:

```python
def generate(self, seed: GenerationSeed) -> Patient:
    # ... existing generation code ...
    
    # After creating the Patient object but before returning:
    
    # ===== NEW: Post-generation validation =====
    validation_result = self.validator.validate(patient)
    
    if not validation_result.valid:
        # Attempt auto-fixes for fixable issues
        patient = self._apply_validation_fixes(patient, validation_result)
        
        # Re-validate after fixes
        validation_result = self.validator.validate(patient)
    
    # Store validation metadata
    patient.generation_seed["validation"] = {
        "valid": validation_result.valid,
        "error_count": len(validation_result.errors),
        "warning_count": len(validation_result.warnings),
        "issues": [issue.model_dump() for issue in validation_result.issues[:10]],  # First 10
    }
    
    # ===== NEW: Narrative-structured reconciliation =====
    if self.reconciler and self.use_llm:
        reconciliation = self.reconciler.reconcile(patient)
        
        if reconciliation.narratives_to_regenerate:
            # Regenerate problematic narratives
            for enc_id in reconciliation.narratives_to_regenerate:
                enc = patient.get_encounter_by_id(enc_id)
                if enc:
                    # Get the discrepancies for this encounter
                    discs = reconciliation.get_discrepancies_for_encounter(enc_id)
                    forbidden_claims = [d.claim.claim_text for d in discs]
                    
                    # Regenerate with constraints
                    enc.narrative_note = self._regenerate_constrained_narrative(
                        encounter=enc,
                        patient=patient,
                        forbidden_claims=forbidden_claims,
                    )
        
        # Store reconciliation metadata
        patient.generation_seed["reconciliation"] = {
            "discrepancy_count": len(reconciliation.discrepancies),
            "narratives_regenerated": len(reconciliation.narratives_to_regenerate),
        }
    
    return patient


def _apply_validation_fixes(self, patient: "Patient", result: ValidationResult) -> "Patient":
    """Apply automatic fixes for validation issues."""
    
    for issue in result.issues:
        if not issue.auto_fixable:
            continue
        
        if issue.type.value == "temporal":
            # Fix temporal issues by adjusting dates
            patient = self._fix_temporal_issue(patient, issue)
        
        elif issue.type.value == "coding":
            # Fix coding issues using condition service
            patient = self._fix_coding_issue(patient, issue)
        
        elif issue.type.value == "medication":
            # Add missing medications for conditions
            patient = self._fix_medication_issue(patient, issue)
        
        elif issue.type.value == "lab":
            # This is harder to fix automatically - flag for now
            pass
    
    return patient


def _fix_temporal_issue(self, patient: "Patient", issue) -> "Patient":
    """Fix temporal issues (dates before DOB)."""
    dob = patient.demographics.date_of_birth
    
    if "encounters" in issue.path:
        # Extract encounter index from path like "encounters[0].date"
        import re
        match = re.search(r'encounters\[(\d+)\]', issue.path)
        if match:
            idx = int(match.group(1))
            if idx < len(patient.encounters):
                # Set to 3 days after DOB (reasonable newborn visit)
                patient.encounters[idx].date = datetime.combine(
                    dob + timedelta(days=3),
                    patient.encounters[idx].date.time()
                )
    
    elif "problem_list" in issue.path:
        match = re.search(r'problem_list\[(\d+)\]', issue.path)
        if match:
            idx = int(match.group(1))
            if idx < len(patient.problem_list):
                # Set onset to DOB (congenital) or reasonable later date
                patient.problem_list[idx].onset_date = dob + timedelta(days=30)
    
    return patient


def _fix_coding_issue(self, patient: "Patient", issue) -> "Patient":
    """Fix ICD-10 coding issues using condition service."""
    import re
    match = re.search(r'problem_list\[(\d+)\]', issue.path)
    if not match:
        return patient
    
    idx = int(match.group(1))
    if idx >= len(patient.problem_list):
        return patient
    
    condition = patient.problem_list[idx]
    
    # Look up correct code via condition service
    result = self.condition_service.get_condition(condition.display_name)
    
    if result.found and result.definition and result.definition.icd10_primary:
        condition.code.code = result.definition.icd10_primary
        condition.code.display = condition.display_name
    
    return patient


def _fix_medication_issue(self, patient: "Patient", issue) -> "Patient":
    """Add missing medications for conditions that require them."""
    # Find the condition that's missing medications
    for condition in patient.problem_list:
        if condition.clinical_status.value != "active":
            continue
        
        # Look up condition to get expected medications
        result = self.condition_service.get_condition(condition.display_name)
        
        if result.found and result.definition and result.definition.medications:
            # Check if patient already has any of these meds
            current_meds = {m.display_name.lower() for m in patient.medication_list}
            
            for med_def in result.definition.medications:
                if med_def.agent.lower() not in current_meds:
                    # Add the medication
                    from src.models import Medication, MedicationStatus, CodeableConcept
                    
                    new_med = Medication(
                        display_name=med_def.agent,
                        code=CodeableConcept(
                            system="http://www.nlm.nih.gov/research/umls/rxnorm",
                            code=med_def.rxnorm or "",
                            display=med_def.agent,
                        ),
                        status=MedicationStatus.ACTIVE,
                        frequency=med_def.frequency,
                        route=med_def.route,
                        start_date=condition.onset_date,
                    )
                    patient.medication_list.append(new_med)
                    break  # Add one medication at a time
    
    return patient


def _regenerate_constrained_narrative(
    self,
    encounter: "Encounter",
    patient: "Patient",
    forbidden_claims: list[str],
) -> str:
    """Regenerate a narrative with constraints to avoid hallucinations."""
    
    # Build list of actual facts from structured data
    actual_meds = [m.display_name for m in patient.active_medications]
    actual_conditions = [c.display_name for c in patient.active_conditions]
    
    constraint_text = ""
    if forbidden_claims:
        constraint_text = f"""
IMPORTANT: Do NOT include any of these claims (they are not supported by structured data):
{chr(10).join(f'- {claim}' for claim in forbidden_claims)}
"""
    
    # ... rest of narrative generation with added constraints ...
    # (Implementation depends on your existing _generate_llm_narrative method)
    
    return self._generate_llm_narrative(encounter, patient.demographics, age_months=0)
```

#### 4.4 Update _generate_labs to handle chronic conditions

Find the `_generate_labs` method and update the gating logic:

```python
def _generate_labs(
    self,
    condition_key: str | None,
    encounter_date: date,
    encounter_type: EncounterType,
    age_months: int | None = None,
) -> list:
    """Generate condition-specific lab results."""
    from src.models import LabResult, CodeableConcept, Interpretation, ReferenceRange

    # UPDATED: Allow labs for chronic conditions at follow-up
    if encounter_type == EncounterType.CHRONIC_FOLLOWUP:
        # Get condition definition to check if labs required
        if condition_key:
            result = self.condition_service.get_condition(condition_key)
            if result.found and result.definition:
                if not result.definition.requires_monitoring_labs:
                    return []
                # Continue to generate labs for conditions that need monitoring
            else:
                return []
        else:
            return []
    elif encounter_type not in (
        EncounterType.ACUTE_ILLNESS,
        EncounterType.URGENT_CARE,
        EncounterType.ED_VISIT,
    ):
        return []

    # Rest of existing lab generation logic...
    # But now also check condition_service for lab definitions if not in YAML
    
    if condition_key:
        result = self.condition_service.get_condition(condition_key)
        if result.found and result.definition and result.definition.labs:
            # Use labs from condition definition
            labs_def = [lab.model_dump() for lab in result.definition.labs]
        else:
            # Fall back to YAML lookup
            condition_data = self._conditions.get(condition_key, {})
            diagnostics = condition_data.get('diagnostics', {})
            labs_def = diagnostics.get('labs', [])
    else:
        return []

    if not labs_def:
        return []

    # ... rest of existing lab generation ...
```

---

### Task 5: Update _get_condition_key to use ConditionKnowledgeService

Replace the existing `_get_condition_key` method:

```python
def _get_condition_key(self, display_name: str) -> str | None:
    """
    Get condition key for a display name.
    
    Now uses ConditionKnowledgeService which can dynamically retrieve
    conditions not in YAML.
    """
    if not display_name:
        return None
    
    result = self.condition_service.get_condition(display_name)
    
    if result.found and result.definition:
        return result.definition.condition_key
    
    return None
```

---

## Testing

After implementation, test with these scenarios:

### Test 1: Unknown Condition Handling
```python
seed = GenerationSeed(
    age=4,
    conditions=["acute lymphoblastic leukemia"],  # Not in YAML
    include_narrative_notes=True,
)
patient = engine.generate(seed)

# Verify:
# - ICD-10 code is NOT R69
# - Patient has oncology medications
# - Chronic follow-up encounters have labs
# - Narrative doesn't claim things not in structured data
```

### Test 2: Temporal Validation
```python
# Generate any patient and verify:
for enc in patient.encounters:
    assert enc.date.date() >= patient.demographics.date_of_birth
```

### Test 3: Narrative Consistency
```python
# Generate patient with narrative notes
# Run reconciler manually
reconciler = PatientReconciler(llm_client)
result = reconciler.reconcile(patient)
assert len(result.discrepancies) == 0
```

---

## File Structure After Implementation

```
src/
├── engines/
│   └── engine.py              # Modified
├── knowledge/
│   ├── __init__.py           # New
│   ├── condition_service.py  # New
│   ├── models.py             # New
│   └── cache.py              # New
├── validators/
│   ├── __init__.py           # New
│   ├── patient_validator.py  # New
│   └── models.py             # New
├── reconciliation/
│   ├── __init__.py           # New
│   ├── reconciler.py         # New
│   ├── claim_extractor.py    # New
│   └── models.py             # New
└── models/
    └── patient.py            # Existing (no changes needed)
```

---

## Implementation Order

1. **First:** Create `src/validators/` module (no external dependencies)
2. **Second:** Create `src/knowledge/` module 
3. **Third:** Create `src/reconciliation/` module
4. **Fourth:** Update `src/engines/engine.py` to integrate all modules
5. **Fifth:** Test with edge cases

---

## Notes for Claude Code

- All code uses Pydantic v2 syntax (model_validate, model_dump)
- LLM client interface matches existing `src/llm/client.py`
- Web search function signature: `(query: str) -> list[dict]` where dict has `url`, `title`, `snippet`
- Web fetch function signature: `(url: str) -> str` returning page content
- Cache directory should be created automatically if it doesn't exist
- Validation should not block generation - it logs issues and attempts fixes
- Reconciliation requires LLM - skip if LLM unavailable
