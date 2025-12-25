"""
Core data models for SynthPatient.

These Pydantic models define the internal representation of synthetic patients.
All generation, validation, and export operations work with these models.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


def generate_id() -> str:
    """Generate a unique identifier."""
    return str(uuid4())[:8]


# =============================================================================
# ENUMS
# =============================================================================


class Sex(str, Enum):
    MALE = "male"
    FEMALE = "female"
    INTERSEX = "intersex"
    UNKNOWN = "unknown"


class ConditionStatus(str, Enum):
    ACTIVE = "active"
    RECURRENCE = "recurrence"
    RELAPSE = "relapse"
    INACTIVE = "inactive"
    REMISSION = "remission"
    RESOLVED = "resolved"


class VerificationStatus(str, Enum):
    CONFIRMED = "confirmed"
    PROVISIONAL = "provisional"
    DIFFERENTIAL = "differential"
    REFUTED = "refuted"
    UNCONFIRMED = "unconfirmed"


class Severity(str, Enum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


class MedicationStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    STOPPED = "stopped"
    ON_HOLD = "on-hold"


class AllergyCategory(str, Enum):
    FOOD = "food"
    MEDICATION = "medication"
    ENVIRONMENT = "environment"
    BIOLOGIC = "biologic"


class AllergySeverity(str, Enum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    LIFE_THREATENING = "life-threatening"


class AllergyReactionType(str, Enum):
    ALLERGY = "allergy"
    INTOLERANCE = "intolerance"


class EncounterType(str, Enum):
    # Pediatric-specific
    NEWBORN = "newborn"
    WELL_CHILD = "well-child"
    
    # Universal
    ANNUAL_PHYSICAL = "annual-physical"
    ACUTE_ILLNESS = "acute-illness"
    ACUTE_INJURY = "acute-injury"
    CHRONIC_FOLLOWUP = "chronic-followup"
    MEDICATION_CHECK = "medication-check"
    MENTAL_HEALTH = "mental-health"
    PRE_OP = "pre-operative"
    POST_OP = "post-operative"
    URGENT_CARE = "urgent-care"
    ED_VISIT = "emergency"
    HOSPITAL_ADMISSION = "inpatient"
    HOSPITAL_DISCHARGE = "discharge"
    TELEHEALTH = "telehealth"
    NURSE_VISIT = "nurse-visit"
    SPECIALIST_CONSULT = "specialist-consult"
    PROCEDURE = "procedure"
    IMMUNIZATION_ONLY = "immunization-only"
    LAB_ONLY = "lab-only"


class EncounterClass(str, Enum):
    AMBULATORY = "ambulatory"
    EMERGENCY = "emergency"
    INPATIENT = "inpatient"
    VIRTUAL = "virtual"
    HOME = "home"


class EncounterStatus(str, Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in-progress"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class OrderStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ResultStatus(str, Enum):
    PRELIMINARY = "preliminary"
    FINAL = "final"
    AMENDED = "amended"
    CANCELLED = "cancelled"


class Interpretation(str, Enum):
    NORMAL = "normal"
    ABNORMAL = "abnormal"
    CRITICAL = "critical"
    HIGH = "high"
    LOW = "low"
    POSITIVE = "positive"
    NEGATIVE = "negative"


class ImmunizationStatus(str, Enum):
    COMPLETED = "completed"
    NOT_DONE = "not-done"
    ENTERED_IN_ERROR = "entered-in-error"


class ComplexityTier(str, Enum):
    TIER_0 = "tier-0"  # Healthy
    TIER_1 = "tier-1"  # Single chronic condition
    TIER_2 = "tier-2"  # Multiple conditions
    TIER_3 = "tier-3"  # Complex/fragile


# =============================================================================
# CODED CONCEPTS
# =============================================================================


class CodeableConcept(BaseModel):
    """A coded concept with system, code, and display text."""
    system: str = Field(description="The coding system (e.g., ICD-10, SNOMED, LOINC, RxNorm)")
    code: str = Field(description="The code value")
    display: str = Field(description="Human-readable display text")
    
    def __str__(self) -> str:
        return f"{self.display} ({self.code})"


class ReferenceRange(BaseModel):
    """Reference range for lab values."""
    low: float | None = None
    high: float | None = None
    unit: str | None = None
    text: str | None = None
    age_low: int | None = Field(default=None, description="Minimum age in years")
    age_high: int | None = Field(default=None, description="Maximum age in years")
    sex: Sex | None = None


# =============================================================================
# DEMOGRAPHICS & SOCIAL
# =============================================================================


class Address(BaseModel):
    """Physical address."""
    line1: str
    line2: str | None = None
    city: str
    state: str
    postal_code: str
    country: str = "US"
    
    @computed_field
    @property
    def full_address(self) -> str:
        parts = [self.line1]
        if self.line2:
            parts.append(self.line2)
        parts.append(f"{self.city}, {self.state} {self.postal_code}")
        return "\n".join(parts)


class Contact(BaseModel):
    """Contact information for a person."""
    name: str
    relationship: str | None = None
    phone: str | None = None
    email: str | None = None
    address: Address | None = None


class HouseholdMember(BaseModel):
    """A member of the patient's household."""
    name: str
    relationship: str
    age: int | None = None
    occupation: str | None = None
    health_notes: str | None = None


class SubstanceUse(BaseModel):
    """Substance use history."""
    substance: str
    status: Literal["never", "former", "current", "unknown"]
    frequency: str | None = None
    quantity: str | None = None
    start_date: date | None = None
    quit_date: date | None = None
    notes: str | None = None


class Demographics(BaseModel):
    """Patient demographics."""
    given_names: list[str] = Field(min_length=1)
    family_name: str
    date_of_birth: date
    sex_at_birth: Sex
    gender_identity: str | None = None
    pronouns: str | None = None
    race: list[str] = Field(default_factory=list)
    ethnicity: str | None = None
    preferred_language: str = "English"
    address: Address
    phone: str
    email: str | None = None
    emergency_contact: Contact
    legal_guardian: Contact | None = None
    
    @computed_field
    @property
    def full_name(self) -> str:
        return f"{' '.join(self.given_names)} {self.family_name}"
    
    @computed_field
    @property
    def age_years(self) -> int:
        today = date.today()
        return today.year - self.date_of_birth.year - (
            (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )
    
    @computed_field
    @property
    def age_months(self) -> int:
        today = date.today()
        months = (today.year - self.date_of_birth.year) * 12
        months += today.month - self.date_of_birth.month
        if today.day < self.date_of_birth.day:
            months -= 1
        return max(0, months)


class SocialHistory(BaseModel):
    """Social history and SDOH."""
    # Living situation
    living_situation: str = Field(description="e.g., 'Lives with parents', 'Independent'")
    household_members: list[HouseholdMember] = Field(default_factory=list)
    
    # Employment/Education (age-appropriate)
    employment_status: str | None = None
    occupation: str | None = None
    employer: str | None = None
    education_level: str | None = None
    
    # Pediatric-specific
    school_name: str | None = None
    grade_level: str | None = None
    school_performance: str | None = None
    iep_504: str | None = Field(default=None, description="IEP or 504 plan status")
    childcare: str | None = None
    custody_arrangement: str | None = None
    
    # Substance use
    tobacco: SubstanceUse | None = None
    alcohol: SubstanceUse | None = None
    substances: list[SubstanceUse] = Field(default_factory=list)
    
    # SDOH
    food_security: str = "secure"
    housing_stability: str = "stable"
    transportation_access: str = "adequate"
    social_support: str = "adequate"
    financial_concerns: str | None = None
    
    # Safety
    safety_concerns: str | None = None
    firearms_in_home: bool | None = None
    domestic_violence_screen: str | None = None
    
    # Lifestyle
    exercise_frequency: str | None = None
    diet_description: str | None = None
    sleep_hours: float | None = None
    screen_time: str | None = None


class FamilyHistoryEntry(BaseModel):
    """Family medical history entry."""
    id: str = Field(default_factory=generate_id)
    relationship: str = Field(description="e.g., 'mother', 'father', 'maternal grandmother'")
    condition: str
    code: CodeableConcept | None = None
    onset_age: int | None = None
    deceased: bool = False
    death_age: int | None = None
    notes: str | None = None


# =============================================================================
# CLINICAL ENTITIES
# =============================================================================


class Condition(BaseModel):
    """A medical condition/diagnosis."""
    id: str = Field(default_factory=generate_id)
    code: CodeableConcept
    display_name: str
    category: Literal["problem", "health-concern", "diagnosis", "encounter-diagnosis"] = "diagnosis"
    clinical_status: ConditionStatus = ConditionStatus.ACTIVE
    verification_status: VerificationStatus = VerificationStatus.CONFIRMED
    severity: Severity | None = None
    
    # Temporal
    onset_date: date
    onset_age_description: str | None = None
    abatement_date: date | None = None
    recorded_date: date | None = None
    
    # Context
    notes: str | None = None
    diagnosed_by: str | None = None
    
    # Relationships
    caused_by: str | None = Field(default=None, description="ID of causing condition")
    complications: list[str] = Field(default_factory=list, description="IDs of complication conditions")


class DoseChange(BaseModel):
    """Record of a medication dose change."""
    date: date
    previous_dose: str
    new_dose: str
    reason: str | None = None
    encounter_id: str | None = None


class Medication(BaseModel):
    """A medication on the patient's list."""
    id: str = Field(default_factory=generate_id)
    code: CodeableConcept
    display_name: str
    status: MedicationStatus = MedicationStatus.ACTIVE
    
    # Dosing
    dose_quantity: str
    dose_unit: str
    frequency: str
    route: str = "oral"
    instructions: str | None = None
    prn: bool = False
    prn_reason: str | None = None
    
    # Temporal
    start_date: date
    end_date: date | None = None
    prescribed_at_encounter: str | None = None
    
    # Context
    indication: str | None = None
    indication_condition_id: str | None = None
    prescriber: str | None = None
    pharmacy: str | None = None
    refills_remaining: int | None = None
    
    # History
    dose_changes: list[DoseChange] = Field(default_factory=list)
    discontinuation_reason: str | None = None


class AllergyReaction(BaseModel):
    """A reaction manifestation for an allergy."""
    manifestation: str
    severity: AllergySeverity | None = None


class Allergy(BaseModel):
    """An allergy or intolerance."""
    id: str = Field(default_factory=generate_id)
    code: CodeableConcept | None = None
    display_name: str
    category: AllergyCategory
    type: AllergyReactionType = AllergyReactionType.ALLERGY
    criticality: Literal["low", "high", "unable-to-assess"] = "low"
    
    # Clinical
    reactions: list[AllergyReaction] = Field(default_factory=list)
    
    # Temporal
    onset_date: date | None = None
    recorded_date: date | None = None
    
    # Status
    clinical_status: Literal["active", "inactive", "resolved"] = "active"
    verification_status: Literal["confirmed", "unconfirmed", "refuted", "entered-in-error"] = "confirmed"
    
    notes: str | None = None


class Immunization(BaseModel):
    """An immunization record."""
    id: str = Field(default_factory=generate_id)
    vaccine_code: CodeableConcept
    display_name: str
    status: ImmunizationStatus = ImmunizationStatus.COMPLETED
    
    # Administration
    date: date
    dose_number: int | None = None
    series_doses: int | None = None
    site: str | None = None
    route: str | None = None
    
    # Product
    lot_number: str | None = None
    expiration_date: date | None = None
    manufacturer: str | None = None
    
    # Context
    encounter_id: str | None = None
    performer: str | None = None
    location: str | None = None
    
    # If not done
    status_reason: str | None = None
    
    notes: str | None = None


class Procedure(BaseModel):
    """A procedure performed on the patient."""
    id: str = Field(default_factory=generate_id)
    code: CodeableConcept
    display_name: str
    status: Literal["preparation", "in-progress", "completed", "not-done", "stopped"] = "completed"
    
    # Temporal
    performed_date: date
    performed_end_date: date | None = None
    
    # Context
    reason: str | None = None
    reason_condition_id: str | None = None
    performer: str | None = None
    location: str | None = None
    encounter_id: str | None = None
    
    # Outcome
    outcome: str | None = None
    complications: str | None = None
    notes: str | None = None


class Surgery(BaseModel):
    """Surgical history entry (a type of procedure with more detail)."""
    id: str = Field(default_factory=generate_id)
    code: CodeableConcept | None = None
    display_name: str
    
    # Temporal
    date: date
    
    # Context
    indication: str | None = None
    surgeon: str | None = None
    facility: str | None = None
    anesthesia_type: str | None = None
    
    # Outcome
    outcome: str | None = None
    complications: str | None = None
    
    notes: str | None = None


# =============================================================================
# OBSERVATIONS
# =============================================================================


class Observation(BaseModel):
    """A clinical observation (vital sign, lab result, etc.)."""
    id: str = Field(default_factory=generate_id)
    category: Literal["vital-signs", "laboratory", "social-history", "imaging", "exam", "survey"]
    code: CodeableConcept
    
    # Value (various types)
    value_quantity: float | None = None
    value_string: str | None = None
    value_boolean: bool | None = None
    value_codeable: CodeableConcept | None = None
    unit: str | None = None
    
    # Interpretation
    reference_range: ReferenceRange | None = None
    interpretation: Interpretation | None = None
    
    # Temporal
    effective_date: datetime
    issued: datetime | None = None
    
    # Context
    encounter_id: str | None = None
    performer: str | None = None
    
    notes: str | None = None
    
    @computed_field
    @property
    def display_value(self) -> str:
        if self.value_quantity is not None:
            unit_str = f" {self.unit}" if self.unit else ""
            return f"{self.value_quantity}{unit_str}"
        if self.value_string is not None:
            return self.value_string
        if self.value_boolean is not None:
            return "Yes" if self.value_boolean else "No"
        if self.value_codeable is not None:
            return self.value_codeable.display
        return "N/A"


class VitalSigns(BaseModel):
    """A set of vital signs taken together."""
    id: str = Field(default_factory=generate_id)
    date: datetime
    encounter_id: str | None = None
    
    # Core vitals
    temperature_f: float | None = None
    heart_rate: int | None = None
    respiratory_rate: int | None = None
    blood_pressure_systolic: int | None = None
    blood_pressure_diastolic: int | None = None
    oxygen_saturation: float | None = None
    
    # Measurements
    weight_kg: float | None = None
    height_cm: float | None = None
    head_circumference_cm: float | None = None
    bmi: float | None = None
    
    # Context
    position: str | None = None
    notes: str | None = None


class GrowthMeasurement(BaseModel):
    """A growth measurement for pediatric patients."""
    id: str = Field(default_factory=generate_id)
    date: date
    age_in_days: int
    encounter_id: str | None = None
    
    # Measurements
    weight_kg: float | None = None
    height_cm: float | None = None
    head_circumference_cm: float | None = None
    bmi: float | None = None
    
    # Percentiles (calculated from growth charts)
    weight_percentile: float | None = None
    height_percentile: float | None = None
    hc_percentile: float | None = None
    bmi_percentile: float | None = None
    
    # Z-scores
    weight_z: float | None = None
    height_z: float | None = None
    hc_z: float | None = None
    bmi_z: float | None = None


class DevelopmentalMilestone(BaseModel):
    """A developmental milestone assessment."""
    id: str = Field(default_factory=generate_id)
    domain: Literal["gross-motor", "fine-motor", "language", "social-emotional", "cognitive"]
    milestone: str
    expected_age_months: int
    achieved: bool
    achieved_date: date | None = None
    achieved_age_months: int | None = None
    notes: str | None = None
    encounter_id: str | None = None


# =============================================================================
# ENCOUNTERS
# =============================================================================


class Provider(BaseModel):
    """A healthcare provider."""
    id: str = Field(default_factory=generate_id)
    name: str
    credentials: str | None = None
    specialty: str | None = None
    npi: str | None = None
    organization: str | None = None


class Location(BaseModel):
    """A healthcare location."""
    id: str = Field(default_factory=generate_id)
    name: str
    type: str | None = None
    address: Address | None = None
    phone: str | None = None


class ReviewOfSystems(BaseModel):
    """Review of systems findings."""
    constitutional: str | None = None
    heent: str | None = None
    cardiovascular: str | None = None
    respiratory: str | None = None
    gastrointestinal: str | None = None
    genitourinary: str | None = None
    musculoskeletal: str | None = None
    skin: str | None = None
    neurological: str | None = None
    psychiatric: str | None = None
    endocrine: str | None = None
    hematologic_lymphatic: str | None = None
    allergic_immunologic: str | None = None


class PhysicalExam(BaseModel):
    """Physical examination findings."""
    general: str | None = None
    heent: str | None = None
    neck: str | None = None
    cardiovascular: str | None = None
    respiratory: str | None = None
    abdomen: str | None = None
    genitourinary: str | None = None
    musculoskeletal: str | None = None
    skin: str | None = None
    neurological: str | None = None
    psychiatric: str | None = None
    lymphatic: str | None = None
    
    # Pediatric-specific
    fontanelle: str | None = None
    hips: str | None = None
    genitalia: str | None = None
    tanner_stage: str | None = None


class Assessment(BaseModel):
    """Clinical assessment/diagnosis for an encounter."""
    condition_id: str | None = None
    diagnosis: str
    code: CodeableConcept | None = None
    clinical_notes: str | None = None
    is_primary: bool = False


class PlanItem(BaseModel):
    """An item in the treatment plan."""
    category: Literal["medication", "order", "referral", "education", "follow-up", "procedure", "other"]
    description: str
    details: str | None = None


class Order(BaseModel):
    """A clinical order (lab, imaging, etc.)."""
    id: str = Field(default_factory=generate_id)
    type: Literal["laboratory", "imaging", "procedure", "referral", "other"]
    code: CodeableConcept | None = None
    display_name: str
    status: OrderStatus = OrderStatus.PENDING
    
    # Temporal
    ordered_date: datetime
    completed_date: datetime | None = None
    
    # Context
    reason: str | None = None
    priority: Literal["routine", "urgent", "stat"] = "routine"
    instructions: str | None = None
    encounter_id: str | None = None
    ordering_provider: str | None = None


class LabResult(BaseModel):
    """A laboratory test result."""
    id: str = Field(default_factory=generate_id)
    order_id: str | None = None
    code: CodeableConcept
    display_name: str
    status: ResultStatus = ResultStatus.FINAL
    
    # Value
    value: float | str | None = None
    unit: str | None = None
    reference_range: ReferenceRange | None = None
    interpretation: Interpretation | None = None
    
    # Temporal
    collected_date: datetime | None = None
    resulted_date: datetime
    
    # Context
    performing_lab: str | None = None
    encounter_id: str | None = None
    
    notes: str | None = None


class LabPanel(BaseModel):
    """A panel of lab results."""
    id: str = Field(default_factory=generate_id)
    order_id: str | None = None
    code: CodeableConcept
    display_name: str
    status: ResultStatus = ResultStatus.FINAL
    
    results: list[LabResult] = Field(default_factory=list)
    
    collected_date: datetime | None = None
    resulted_date: datetime
    encounter_id: str | None = None


class ImagingResult(BaseModel):
    """An imaging study result."""
    id: str = Field(default_factory=generate_id)
    order_id: str | None = None
    code: CodeableConcept | None = None
    display_name: str
    modality: str
    status: ResultStatus = ResultStatus.FINAL
    
    # Report
    findings: str
    impression: str
    
    # Temporal
    performed_date: datetime
    reported_date: datetime | None = None
    
    # Context
    performing_facility: str | None = None
    radiologist: str | None = None
    encounter_id: str | None = None


class Referral(BaseModel):
    """A referral to another provider/service."""
    id: str = Field(default_factory=generate_id)
    specialty: str
    provider: str | None = None
    reason: str
    priority: Literal["routine", "urgent", "emergent"] = "routine"
    status: Literal["pending", "scheduled", "completed", "cancelled"] = "pending"
    
    # Temporal
    referred_date: date
    scheduled_date: date | None = None
    completed_date: date | None = None
    
    # Context
    encounter_id: str | None = None
    referring_provider: str | None = None
    
    notes: str | None = None
    outcome: str | None = None


class GrowthPercentiles(BaseModel):
    """Growth percentiles for a pediatric encounter."""
    weight_percentile: float | None = None
    height_percentile: float | None = None
    hc_percentile: float | None = None
    bmi_percentile: float | None = None
    weight_for_length_percentile: float | None = None


class DevelopmentalScreen(BaseModel):
    """Developmental screening results."""
    tool: str = Field(description="e.g., 'ASQ-3', 'M-CHAT-R', 'PEDS'")
    date: date
    result: Literal["normal", "at-risk", "delayed", "not-completed"]
    domains_assessed: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    notes: str | None = None


class BillingCodes(BaseModel):
    """Billing and coding information."""
    cpt_codes: list[str] = Field(default_factory=list)
    icd_codes: list[str] = Field(default_factory=list)
    modifiers: list[str] = Field(default_factory=list)
    e_m_level: str | None = None


class Encounter(BaseModel):
    """A clinical encounter/visit."""
    id: str = Field(default_factory=generate_id)
    date: datetime
    end_date: datetime | None = None
    type: EncounterType
    status: EncounterStatus = EncounterStatus.FINISHED
    encounter_class: EncounterClass = EncounterClass.AMBULATORY
    
    # Context
    reason_codes: list[CodeableConcept] = Field(default_factory=list)
    chief_complaint: str
    provider: Provider
    location: Location
    
    # Clinical content
    hpi: str | None = None
    ros: ReviewOfSystems | None = None
    physical_exam: PhysicalExam | None = None
    assessment: list[Assessment] = Field(default_factory=list)
    plan: list[PlanItem] = Field(default_factory=list)
    
    # Vitals
    vital_signs: VitalSigns | None = None
    
    # Orders and results
    orders: list[Order] = Field(default_factory=list)
    lab_results: list[LabPanel | LabResult] = Field(default_factory=list)
    imaging_results: list[ImagingResult] = Field(default_factory=list)
    
    # Prescriptions written at this visit
    prescriptions: list[Medication] = Field(default_factory=list)
    
    # Immunizations given at this visit
    immunizations_given: list[Immunization] = Field(default_factory=list)
    
    # Referrals made at this visit
    referrals: list[Referral] = Field(default_factory=list)
    
    # Procedures performed at this visit
    procedures: list[Procedure] = Field(default_factory=list)
    
    # Full narrative note
    narrative_note: str | None = None
    
    # Billing
    billing: BillingCodes | None = None
    
    # Pediatric-specific
    growth_percentiles: GrowthPercentiles | None = None
    developmental_screen: DevelopmentalScreen | None = None
    anticipatory_guidance: list[str] = Field(default_factory=list)
    
    # Follow-up
    follow_up_instructions: str | None = None
    follow_up_interval: str | None = None


# =============================================================================
# CARE MANAGEMENT
# =============================================================================


class Coverage(BaseModel):
    """Insurance coverage."""
    id: str = Field(default_factory=generate_id)
    type: Literal["primary", "secondary", "tertiary"]
    payer: str
    plan_name: str | None = None
    member_id: str
    group_number: str | None = None
    subscriber: str | None = None
    relationship_to_subscriber: str = "self"
    effective_date: date
    termination_date: date | None = None


class CareTeamMember(BaseModel):
    """A member of the patient's care team."""
    id: str = Field(default_factory=generate_id)
    name: str
    role: str
    specialty: str | None = None
    organization: str | None = None
    phone: str | None = None
    fax: str | None = None
    email: str | None = None
    is_pcp: bool = False
    start_date: date | None = None


class CareGap(BaseModel):
    """An identified care gap."""
    id: str = Field(default_factory=generate_id)
    type: str
    description: str
    due_date: date | None = None
    status: Literal["open", "closed", "deferred"] = "open"
    last_completed: date | None = None
    notes: str | None = None


# =============================================================================
# PATIENT (ROOT MODEL)
# =============================================================================


class Patient(BaseModel):
    """
    Complete patient record.
    
    This is the root model containing all patient data.
    All generation, validation, and export operations work with this model.
    """
    id: str = Field(default_factory=generate_id)
    
    # Core demographics
    demographics: Demographics
    
    # History
    family_history: list[FamilyHistoryEntry] = Field(default_factory=list)
    social_history: SocialHistory
    
    # Administrative
    insurance: list[Coverage] = Field(default_factory=list)
    care_team: list[CareTeamMember] = Field(default_factory=list)
    
    # Clinical core
    problem_list: list[Condition] = Field(default_factory=list)
    medication_list: list[Medication] = Field(default_factory=list)
    allergy_list: list[Allergy] = Field(default_factory=list)
    immunization_record: list[Immunization] = Field(default_factory=list)
    procedure_history: list[Procedure] = Field(default_factory=list)
    surgical_history: list[Surgery] = Field(default_factory=list)
    
    # Longitudinal data
    encounters: list[Encounter] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    growth_data: list[GrowthMeasurement] = Field(default_factory=list)
    developmental_milestones: list[DevelopmentalMilestone] = Field(default_factory=list)
    
    # Care management
    care_gaps: list[CareGap] = Field(default_factory=list)
    
    # Generation metadata
    generation_seed: dict[str, Any] = Field(default_factory=dict)
    engine_version: str = "1.0.0"
    generated_at: datetime = Field(default_factory=datetime.now)
    complexity_tier: ComplexityTier = ComplexityTier.TIER_0
    
    @computed_field
    @property
    def is_pediatric(self) -> bool:
        """Patient is considered pediatric if under 22."""
        return self.demographics.age_years < 22
    
    @computed_field
    @property
    def active_conditions(self) -> list[Condition]:
        """Get all active conditions."""
        return [c for c in self.problem_list if c.clinical_status == ConditionStatus.ACTIVE]
    
    @computed_field
    @property
    def active_medications(self) -> list[Medication]:
        """Get all active medications."""
        return [m for m in self.medication_list if m.status == MedicationStatus.ACTIVE]
    
    def get_encounter_by_id(self, encounter_id: str) -> Encounter | None:
        """Get an encounter by its ID."""
        for enc in self.encounters:
            if enc.id == encounter_id:
                return enc
        return None
    
    def get_condition_by_id(self, condition_id: str) -> Condition | None:
        """Get a condition by its ID."""
        for cond in self.problem_list:
            if cond.id == condition_id:
                return cond
        return None


# =============================================================================
# GENERATION SEED
# =============================================================================


class GenerationSeed(BaseModel):
    """
    Input parameters for patient generation.
    
    All fields are optional - unspecified fields will be randomly generated.
    """
    # Demographics
    age: int | None = Field(default=None, ge=0, le=120, description="Patient age in years")
    age_months: int | None = Field(default=None, ge=0, description="Patient age in months (for infants)")
    sex: Sex | None = None
    race: list[str] | None = None
    ethnicity: str | None = None
    state: str | None = Field(default=None, description="US state abbreviation")
    
    # Complexity
    complexity_tier: ComplexityTier | None = None
    
    # Conditions
    conditions: list[str] | None = Field(default=None, description="Conditions to include (names or ICD-10 codes)")
    exclude_conditions: list[str] | None = Field(default=None, description="Conditions to exclude")
    
    # History
    years_of_history: int | None = Field(default=None, ge=0, description="Years of medical history to generate")
    encounter_count: int | None = Field(default=None, ge=1, description="Approximate number of encounters")
    
    # Specific scenarios
    description: str | None = Field(default=None, description="Natural language description of the patient")
    archetype: str | None = Field(default=None, description="Named archetype template to use")
    
    # Output control
    include_narrative_notes: bool = True
    include_billing: bool = False
    
    # Reproducibility
    random_seed: int | None = Field(default=None, description="Seed for random generation")
