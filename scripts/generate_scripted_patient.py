#!/usr/bin/env python3
"""
Generate a scripted patient with specific encounters for medical education.

This script creates a 10-year-old patient with Type 1 Diabetes and Asthma,
including 5 specific educational encounters:
1. Well-child checkup (9 years)
2. Cold/URI (acute illness)
3. Pneumonia follow-up (9 days after cold, secondary bacterial infection)
4. Combined chronic care follow-up (asthma + T1D)
5. Injury visit (laceration)

Usage:
  cd /path/to/synpat
  source .venv/bin/activate
  python scripts/generate_scripted_patient.py
"""

from __future__ import annotations

import sys
from datetime import datetime, date, timedelta
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engines.engine import PedsEngine
from src.models.patient import (
  GenerationSeed,
  Patient,
  Encounter,
  EncounterType,
  EncounterStatus,
  EncounterClass,
  Assessment,
  PlanItem,
  VitalSigns,
  PhysicalExam,
  Provider,
  Location,
  CodeableConcept,
  Medication,
  MedicationStatus,
  Order,
  OrderStatus,
  LabResult,
  ReferenceRange,
  Allergy,
  AllergyReaction,
  AllergySeverity,
  AllergyCategory,
  Sex,
  ComplexityTier,
)
from src.models.patient import generate_id
from src.exporters import export_json, export_ccda


# =============================================================================
# CONFIGURATION
# =============================================================================

PATIENT_CONFIG = {
  "age_months": 120,  # 10 years
  "sex": Sex.MALE,
  "conditions": ["type_1_diabetes", "asthma"],
  "t1d_onset_months": 48,  # 4 years old
  "asthma_onset_months": 24,  # 2 years old
}

# Provider and location for encounters
DEFAULT_PROVIDER = Provider(
  name="Dr. Sarah Chen",
  credentials="MD, FAAP",
  specialty="Pediatrics",
  npi="1234567890",
  organization="Pediatric Partners",
)

DEFAULT_LOCATION = Location(
  name="Pediatric Partners - Main Office",
  type="Outpatient",
  phone="(555) 123-4567",
)

URGENT_CARE_LOCATION = Location(
  name="Pediatric Urgent Care",
  type="Urgent Care",
  phone="(555) 987-6543",
)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def months_to_date(dob: date, age_months: int) -> datetime:
  """Convert an age in months to a date given DOB."""
  years = age_months // 12
  months = age_months % 12

  new_year = dob.year + years
  new_month = dob.month + months

  if new_month > 12:
    new_year += 1
    new_month -= 12

  # Handle day overflow
  day = min(dob.day, 28)

  return datetime(new_year, new_month, day, 10, 0, 0)


def calculate_weight_for_age(age_months: int, sex: Sex) -> float:
  """Approximate weight in kg for age (50th percentile)."""
  # Simplified CDC-based approximation
  if age_months < 12:
    return 3.5 + (age_months * 0.5)
  elif age_months < 24:
    return 9 + ((age_months - 12) * 0.2)
  elif age_months < 60:
    return 11 + ((age_months - 24) * 0.15)
  else:
    # School age
    return 17 + ((age_months - 60) * 0.25)


def calculate_height_for_age(age_months: int, sex: Sex) -> float:
  """Approximate height in cm for age (50th percentile)."""
  if age_months < 12:
    return 50 + (age_months * 2)
  elif age_months < 24:
    return 74 + ((age_months - 12) * 1)
  elif age_months < 60:
    return 86 + ((age_months - 24) * 0.7)
  else:
    return 111 + ((age_months - 60) * 0.5)


def create_vitals(
  encounter_date: datetime,
  age_months: int,
  sex: Sex,
  temp_f: float = 98.6,
  hr_modifier: float = 1.0,
  rr_modifier: float = 1.0,
  spo2: float = 99.0,
) -> VitalSigns:
  """Create age-appropriate vital signs."""
  weight = calculate_weight_for_age(age_months, sex)
  height = calculate_height_for_age(age_months, sex)

  # Age-appropriate heart rate (resting)
  if age_months < 12:
    base_hr = 120
  elif age_months < 36:
    base_hr = 110
  elif age_months < 72:
    base_hr = 100
  else:
    base_hr = 85

  # Age-appropriate respiratory rate
  if age_months < 12:
    base_rr = 35
  elif age_months < 36:
    base_rr = 28
  elif age_months < 72:
    base_rr = 24
  else:
    base_rr = 18

  # Age-appropriate BP
  if age_months < 12:
    bp_sys, bp_dia = 80, 50
  elif age_months < 72:
    bp_sys, bp_dia = 95, 60
  else:
    bp_sys, bp_dia = 105, 65

  return VitalSigns(
    date=encounter_date,
    temperature_f=temp_f,
    heart_rate=int(base_hr * hr_modifier),
    respiratory_rate=int(base_rr * rr_modifier),
    blood_pressure_systolic=bp_sys,
    blood_pressure_diastolic=bp_dia,
    oxygen_saturation=spo2,
    weight_kg=weight,
    height_cm=height,
    bmi=weight / ((height / 100) ** 2),
  )


# =============================================================================
# ENCOUNTER GENERATORS
# =============================================================================

def create_well_child_encounter(
  patient: Patient,
  age_months: int,
) -> Encounter:
  """Create a well-child checkup encounter."""
  dob = patient.demographics.date_of_birth
  encounter_date = months_to_date(dob, age_months)

  age_years = age_months // 12

  return Encounter(
    date=encounter_date,
    type=EncounterType.WELL_CHILD,
    status=EncounterStatus.FINISHED,
    encounter_class=EncounterClass.AMBULATORY,
    chief_complaint=f"Well-child visit - {age_years} year old",
    provider=DEFAULT_PROVIDER,
    location=DEFAULT_LOCATION,
    hpi=f"Patient presents for routine {age_years}-year well-child examination. "
        f"Parents report child is doing well overall. Active in school and sports. "
        f"Managing diabetes with insulin pump therapy. Asthma well-controlled on current regimen.",
    vital_signs=create_vitals(encounter_date, age_months, patient.demographics.sex_at_birth),
    physical_exam=PhysicalExam(
      general="Well-appearing, well-nourished child in no acute distress",
      heent="Normocephalic, atraumatic. Pupils equal, reactive. TMs clear bilaterally. Oropharynx clear.",
      neck="Supple, no lymphadenopathy, thyroid normal",
      cardiovascular="Regular rate and rhythm, no murmur",
      respiratory="Clear to auscultation bilaterally, no wheezes or crackles",
      abdomen="Soft, non-tender, non-distended, no hepatosplenomegaly",
      skin="Insulin pump insertion site on abdomen, no erythema or induration. No rashes.",
      neurological="Alert and oriented, grossly intact",
    ),
    assessment=[
      Assessment(
        diagnosis="Well-child visit",
        code=CodeableConcept(
          system="http://snomed.info/sct",
          code="410620009",
          display="Well-child visit",
        ),
        is_primary=True,
      ),
      Assessment(
        diagnosis="Type 1 Diabetes - stable, well-controlled",
        code=CodeableConcept(
          system="http://hl7.org/fhir/sid/icd-10-cm",
          code="E10.9",
          display="Type 1 diabetes mellitus without complications",
        ),
      ),
      Assessment(
        diagnosis="Asthma - well-controlled on current therapy",
        code=CodeableConcept(
          system="http://hl7.org/fhir/sid/icd-10-cm",
          code="J45.20",
          display="Mild intermittent asthma, uncomplicated",
        ),
      ),
    ],
    plan=[
      PlanItem(category="medication", description="Continue current insulin regimen via pump"),
      PlanItem(category="medication", description="Continue fluticasone inhaler 44mcg 2 puffs BID"),
      PlanItem(category="medication", description="Continue albuterol PRN for rescue"),
      PlanItem(category="order", description="HbA1c ordered - last was 7.2%"),
      PlanItem(category="education", description="Reviewed diabetes management, carb counting"),
      PlanItem(category="education", description="Reviewed asthma action plan, trigger avoidance"),
      PlanItem(category="follow-up", description="Return in 3 months for diabetes follow-up"),
    ],
    orders=[
      Order(
        type="laboratory",
        code=CodeableConcept(
          system="http://loinc.org",
          code="4548-4",
          display="Hemoglobin A1c/Hemoglobin.total in Blood",
        ),
        display_name="Hemoglobin A1c",
        status=OrderStatus.COMPLETED,
        ordered_date=encounter_date,
        completed_date=encounter_date,
        reason="Type 1 Diabetes monitoring",
      ),
    ],
    lab_results=[
      LabResult(
        code=CodeableConcept(
          system="http://loinc.org",
          code="4548-4",
          display="Hemoglobin A1c",
        ),
        display_name="Hemoglobin A1c",
        value=7.2,
        unit="%",
        reference_range=ReferenceRange(high=7.5, text="<7.5%"),
        interpretation="normal",
        collected_date=encounter_date,
        resulted_date=encounter_date,
      ),
    ],
    anticipatory_guidance=[
      "Screen time should be limited to 2 hours per day",
      "Encourage 60 minutes of physical activity daily",
      "Dental visit every 6 months",
      "Bicycle helmet required for all riding",
      "Continue annual eye exam for diabetes screening",
    ],
    follow_up_instructions="Return in 3 months for diabetes check, or sooner if concerns",
    follow_up_interval="3 months",
  )


def create_cold_encounter(
  patient: Patient,
  age_months: int,
) -> Encounter:
  """Create an acute illness encounter for a cold/URI."""
  dob = patient.demographics.date_of_birth
  encounter_date = months_to_date(dob, age_months)

  cold_encounter_id = generate_id()

  return Encounter(
    id=cold_encounter_id,
    date=encounter_date,
    type=EncounterType.ACUTE_ILLNESS,
    status=EncounterStatus.FINISHED,
    encounter_class=EncounterClass.AMBULATORY,
    chief_complaint="Congestion, runny nose, mild cough for 3 days",
    provider=DEFAULT_PROVIDER,
    location=DEFAULT_LOCATION,
    hpi="10-year-old male presents with 3-day history of nasal congestion, clear rhinorrhea, "
        "and mild dry cough. Low-grade fever to 100.2F yesterday. "
        "Decreased appetite but drinking fluids well. No ear pain, sore throat, or difficulty breathing. "
        "Parents gave ibuprofen for fever with good response. "
        "History of asthma - no increase in rescue inhaler use. "
        "Type 1 diabetes - blood sugars slightly elevated but no ketones.",
    vital_signs=create_vitals(
      encounter_date, age_months, patient.demographics.sex_at_birth,
      temp_f=99.8, hr_modifier=1.05, spo2=98.0
    ),
    physical_exam=PhysicalExam(
      general="Well-appearing child, mild nasal congestion, no respiratory distress",
      heent="Erythematous nasal mucosa with clear discharge. TMs clear bilaterally. "
            "Pharynx mildly erythematous without exudate. No tonsillar enlargement.",
      neck="Supple, small anterior cervical lymphadenopathy bilaterally",
      cardiovascular="Regular rate and rhythm, no murmur",
      respiratory="Clear to auscultation bilaterally, no wheezes, no crackles, good air movement",
      abdomen="Soft, non-tender",
      skin="Warm, dry, no rashes",
    ),
    assessment=[
      Assessment(
        diagnosis="Acute upper respiratory infection, viral",
        code=CodeableConcept(
          system="http://hl7.org/fhir/sid/icd-10-cm",
          code="J06.9",
          display="Acute upper respiratory infection, unspecified",
        ),
        clinical_notes="Typical viral URI presentation. No bacterial superinfection.",
        is_primary=True,
      ),
    ],
    plan=[
      PlanItem(category="medication", description="Continue ibuprofen PRN for fever/discomfort"),
      PlanItem(category="medication", description="Saline nasal spray as needed for congestion"),
      PlanItem(category="education", description="Increase fluid intake, rest"),
      PlanItem(category="education", description="Monitor blood sugars more frequently during illness"),
      PlanItem(category="education", description="Continue current asthma and diabetes medications"),
      PlanItem(category="other", description="Return if fever persists >5 days, develops ear pain, "
               "difficulty breathing, worsening cough, or blood sugar concerns"),
    ],
    follow_up_instructions="Return if symptoms worsen or persist beyond 7-10 days",
    follow_up_interval="As needed",
  )


def create_pneumonia_followup_encounter(
  patient: Patient,
  age_months: int,
  cold_encounter: Encounter,
) -> Encounter:
  """Create a follow-up encounter for pneumonia (secondary bacterial infection)."""
  # 9 days after the cold visit
  encounter_date = cold_encounter.date + timedelta(days=9)

  return Encounter(
    date=encounter_date,
    type=EncounterType.ACUTE_ILLNESS,
    status=EncounterStatus.FINISHED,
    encounter_class=EncounterClass.AMBULATORY,
    chief_complaint="Worsening cough, new fever after cold",
    provider=DEFAULT_PROVIDER,
    location=DEFAULT_LOCATION,
    hpi=f"Patient was seen 9 days ago for upper respiratory infection symptoms. "
        f"Initially improved over first 4-5 days, but over past 2-3 days developed worsening cough "
        f"that is now productive. New fever to 102.4F starting yesterday. Decreased activity level, "
        f"not wanting to eat. Some increased work of breathing noted by parents. "
        f"Blood sugars running higher than usual despite increased insulin. "
        f"Increased albuterol use to every 4 hours for cough/wheeze. "
        f"\n\nPrior encounter ID: {cold_encounter.id}",
    vital_signs=create_vitals(
      encounter_date, age_months, patient.demographics.sex_at_birth,
      temp_f=102.1, hr_modifier=1.2, rr_modifier=1.3, spo2=94.0
    ),
    physical_exam=PhysicalExam(
      general="Ill-appearing child, appears fatigued, mild tachypnea at rest",
      heent="Nasal congestion improved. TMs clear. Pharynx mildly erythematous.",
      neck="Supple, shotty anterior cervical lymphadenopathy",
      cardiovascular="Tachycardic, regular rhythm, no murmur",
      respiratory="Decreased breath sounds at right base. Crackles (rales) on right lower lobe. "
                  "Mild expiratory wheezing diffusely. Subcostal retractions present.",
      abdomen="Soft, non-tender",
      skin="Warm, flushed, capillary refill 2 seconds",
    ),
    assessment=[
      Assessment(
        diagnosis="Community-acquired pneumonia, right lower lobe",
        code=CodeableConcept(
          system="http://hl7.org/fhir/sid/icd-10-cm",
          code="J18.9",
          display="Pneumonia, unspecified organism",
        ),
        clinical_notes="Secondary bacterial pneumonia following viral URI. "
                       "Focal findings on exam, clinical syndrome consistent with CAP.",
        is_primary=True,
      ),
      Assessment(
        diagnosis="Asthma exacerbation, mild",
        code=CodeableConcept(
          system="http://hl7.org/fhir/sid/icd-10-cm",
          code="J45.21",
          display="Mild intermittent asthma with acute exacerbation",
        ),
        clinical_notes="Triggered by respiratory infection",
      ),
      Assessment(
        diagnosis="Type 1 Diabetes with illness-related hyperglycemia",
        code=CodeableConcept(
          system="http://hl7.org/fhir/sid/icd-10-cm",
          code="E10.65",
          display="Type 1 diabetes mellitus with hyperglycemia",
        ),
      ),
    ],
    plan=[
      PlanItem(category="medication", description="Amoxicillin 90mg/kg/day divided BID x 5 days "
               f"({int(calculate_weight_for_age(age_months, patient.demographics.sex_at_birth) * 45)}mg BID)"),
      PlanItem(category="medication", description="Continue albuterol nebulizer Q4H while awake x 48-72 hours, "
               "then transition back to MDI PRN"),
      PlanItem(category="medication", description="Prednisone 1mg/kg daily x 5 days for asthma exacerbation"),
      PlanItem(category="order", description="Chest X-ray ordered to confirm pneumonia"),
      PlanItem(category="education", description="Increase insulin by 10-20% during illness with steroid use"),
      PlanItem(category="education", description="Check blood sugars every 3-4 hours, check ketones if BG >300"),
      PlanItem(category="education", description="Push fluids, rest"),
      PlanItem(category="follow-up", description="Recheck in 48-72 hours or sooner if worsening"),
      PlanItem(category="other", description="Return immediately or go to ED if: increasing work of breathing, "
               "SpO2 <92%, unable to take oral medications, persistent vomiting, or lethargy"),
    ],
    orders=[
      Order(
        type="imaging",
        code=CodeableConcept(
          system="http://loinc.org",
          code="36643-5",
          display="Chest X-ray 2 views",
        ),
        display_name="Chest X-ray PA and Lateral",
        status=OrderStatus.COMPLETED,
        ordered_date=encounter_date,
        completed_date=encounter_date,
        reason="Suspected pneumonia",
        priority="urgent",
      ),
    ],
    prescriptions=[
      Medication(
        display_name="Amoxicillin",
        code=CodeableConcept(
          system="http://www.nlm.nih.gov/research/umls/rxnorm",
          code="723",
          display="Amoxicillin",
        ),
        dose_quantity="1000",
        dose_unit="mg",
        frequency="BID",
        route="oral",
        status=MedicationStatus.ACTIVE,
        start_date=encounter_date.date(),
        end_date=encounter_date.date() + timedelta(days=5),
        indication="Community-acquired pneumonia",
        instructions="Take with food. Complete entire course (5 days).",
      ),
      Medication(
        display_name="Prednisone",
        code=CodeableConcept(
          system="http://www.nlm.nih.gov/research/umls/rxnorm",
          code="8640",
          display="Prednisone",
        ),
        dose_quantity="30",
        dose_unit="mg",
        frequency="Daily",
        route="oral",
        status=MedicationStatus.ACTIVE,
        start_date=encounter_date.date(),
        end_date=encounter_date.date() + timedelta(days=5),
        indication="Asthma exacerbation",
        instructions="Take in the morning with breakfast. Will affect blood sugars. 5 day course.",
      ),
    ],
    follow_up_instructions="Recheck in 48-72 hours. Return immediately if difficulty breathing worsens.",
    follow_up_interval="48-72 hours",
    narrative_note="""
ASSESSMENT AND PLAN:

10-year-old male with history of Type 1 Diabetes and asthma presents with worsening
respiratory symptoms 9 days after initial URI. Clinical presentation concerning for
secondary bacterial pneumonia with mild asthma exacerbation.

CLINICAL REASONING:
History of viral URI followed by initial improvement, then return of fever with productive
cough and focal lung findings (decreased breath sounds and crackles at right base) is
classic presentation for secondary bacterial pneumonia. The "double sickening" pattern -
initial improvement followed by worsening - is highly suggestive of bacterial superinfection.

Given concurrent asthma history with current wheezing and increased work of breathing,
treating concurrent asthma exacerbation with systemic steroids. Patient's diabetes
management will need close attention given illness and steroid use.

Signed: Dr. Sarah Chen, MD, FAAP
""",
  )


def create_chronic_care_encounter(
  patient: Patient,
  age_months: int,
) -> Encounter:
  """Create a combined chronic care follow-up for asthma and T1D."""
  dob = patient.demographics.date_of_birth
  encounter_date = months_to_date(dob, age_months)

  return Encounter(
    date=encounter_date,
    type=EncounterType.CHRONIC_FOLLOWUP,
    status=EncounterStatus.FINISHED,
    encounter_class=EncounterClass.AMBULATORY,
    chief_complaint="Diabetes and asthma follow-up",
    provider=DEFAULT_PROVIDER,
    location=DEFAULT_LOCATION,
    hpi="10-year-old male with Type 1 Diabetes (diagnosed age 4) and asthma (diagnosed age 2) "
        "presents for combined chronic disease management follow-up. "
        "\n\nDIABETES: Using insulin pump (Omnipod), doing well with current settings. "
        "Downloading pump data shows time in range 65%, slightly below goal of 70%. "
        "Some afternoon lows noted, may need basal adjustment. "
        "Using Dexcom G7 CGM, calibrating regularly. Last HbA1c was 7.4% (goal <7.5%). "
        "No episodes of DKA or severe hypoglycemia. Sees endocrinology every 3 months."
        "\n\nASTHMA: Well-controlled on current regimen. Using fluticasone 44mcg 2 puffs BID "
        "and albuterol PRN. Rescue inhaler use 1-2x per week, usually with exercise. "
        "Recent cold 2 months ago triggered mild exacerbation treated with oral steroids. "
        "No ED visits or hospitalizations this year. Peak flows at baseline.",
    vital_signs=create_vitals(encounter_date, age_months, patient.demographics.sex_at_birth),
    physical_exam=PhysicalExam(
      general="Well-appearing, well-nourished child in no distress",
      heent="Normocephalic, PERRLA, TMs clear, oropharynx clear",
      neck="Supple, thyroid normal, no lymphadenopathy",
      cardiovascular="RRR, no murmur",
      respiratory="Clear to auscultation bilaterally, no wheezes, good air exchange",
      abdomen="Soft, NT/ND. Insulin pump site left abdomen - no erythema. "
              "CGM site right arm - healthy appearing.",
      skin="Multiple prior pump/CGM sites examined, no lipohypertrophy. No rashes.",
      neurological="Alert, appropriate, no focal deficits",
    ),
    assessment=[
      Assessment(
        diagnosis="Type 1 Diabetes mellitus - stable, fair control",
        code=CodeableConcept(
          system="http://hl7.org/fhir/sid/icd-10-cm",
          code="E10.9",
          display="Type 1 diabetes mellitus without complications",
        ),
        clinical_notes="HbA1c 7.4%, time in range 65%. Some afternoon hypoglycemia.",
        is_primary=True,
      ),
      Assessment(
        diagnosis="Asthma - well-controlled",
        code=CodeableConcept(
          system="http://hl7.org/fhir/sid/icd-10-cm",
          code="J45.20",
          display="Mild intermittent asthma, uncomplicated",
        ),
        clinical_notes="Minimal rescue inhaler use, no nocturnal symptoms, "
                       "no activity limitation.",
      ),
    ],
    plan=[
      # Diabetes management
      PlanItem(category="medication", description="Continue current insulin pump settings; "
               "decrease afternoon basal by 0.05 units/hr from 2-5pm to address lows"),
      PlanItem(category="order", description="HbA1c today - target <7.5%"),
      PlanItem(category="order", description="Comprehensive metabolic panel"),
      PlanItem(category="order", description="Thyroid panel (annual screening)"),
      PlanItem(category="order", description="Celiac panel (annual screening)"),
      PlanItem(category="order", description="Urine microalbumin/creatinine ratio"),
      PlanItem(category="referral", description="Annual ophthalmology exam due - referral placed"),
      PlanItem(category="education", description="Reviewed sick day rules, ketone management"),

      # Asthma management
      PlanItem(category="medication", description="Continue fluticasone 44mcg 2 puffs BID"),
      PlanItem(category="medication", description="Continue albuterol MDI 2 puffs PRN"),
      PlanItem(category="education", description="Reviewed asthma action plan - updated copy provided"),
      PlanItem(category="education", description="Pre-treat with albuterol 15 min before exercise"),

      # General
      PlanItem(category="follow-up", description="Return in 3 months for diabetes follow-up"),
      PlanItem(category="follow-up", description="Endocrinology appointment in 6 weeks"),
    ],
    orders=[
      Order(
        type="laboratory",
        code=CodeableConcept(
          system="http://loinc.org",
          code="4548-4",
          display="Hemoglobin A1c",
        ),
        display_name="Hemoglobin A1c",
        status=OrderStatus.COMPLETED,
        ordered_date=encounter_date,
        completed_date=encounter_date,
        reason="Type 1 Diabetes monitoring",
      ),
      Order(
        type="laboratory",
        code=CodeableConcept(
          system="http://loinc.org",
          code="24323-8",
          display="Comprehensive metabolic panel",
        ),
        display_name="Comprehensive Metabolic Panel",
        status=OrderStatus.COMPLETED,
        ordered_date=encounter_date,
        completed_date=encounter_date,
        reason="Annual diabetes screening",
      ),
      Order(
        type="laboratory",
        code=CodeableConcept(
          system="http://loinc.org",
          code="3016-3",
          display="TSH",
        ),
        display_name="Thyroid Panel",
        status=OrderStatus.COMPLETED,
        ordered_date=encounter_date,
        completed_date=encounter_date,
        reason="Annual thyroid screening for T1D",
      ),
    ],
    lab_results=[
      LabResult(
        code=CodeableConcept(
          system="http://loinc.org",
          code="4548-4",
          display="Hemoglobin A1c",
        ),
        display_name="Hemoglobin A1c",
        value=7.4,
        unit="%",
        reference_range=ReferenceRange(high=7.5, text="<7.5%"),
        interpretation="normal",
        collected_date=encounter_date,
        resulted_date=encounter_date,
      ),
      LabResult(
        code=CodeableConcept(
          system="http://loinc.org",
          code="3016-3",
          display="TSH",
        ),
        display_name="TSH",
        value=2.1,
        unit="mIU/L",
        reference_range=ReferenceRange(low=0.5, high=4.5, text="0.5-4.5 mIU/L"),
        interpretation="normal",
        collected_date=encounter_date,
        resulted_date=encounter_date,
      ),
    ],
    follow_up_instructions="Return in 3 months for diabetes follow-up. "
                           "Call if blood sugars consistently out of range or concerns.",
    follow_up_interval="3 months",
  )


def create_injury_encounter(
  patient: Patient,
  age_months: int,
) -> Encounter:
  """Create an injury encounter (laceration from bicycle fall)."""
  dob = patient.demographics.date_of_birth
  encounter_date = months_to_date(dob, age_months)

  return Encounter(
    date=encounter_date,
    type=EncounterType.ACUTE_INJURY,
    status=EncounterStatus.FINISHED,
    encounter_class=EncounterClass.AMBULATORY,
    chief_complaint="Laceration to forearm from bicycle fall",
    provider=DEFAULT_PROVIDER,
    location=URGENT_CARE_LOCATION,
    hpi="10-year-old male presents after falling off bicycle approximately 1 hour ago. "
        "Was riding on sidewalk, hit a crack, and fell onto concrete. "
        "Landed on outstretched right arm. Sustained laceration to right forearm. "
        "No loss of consciousness, no head strike, was wearing helmet. "
        "Denies numbness/tingling in hand or fingers. Moving all digits well. "
        "Bleeding controlled with pressure by parents. "
        "\n\nPMH: Type 1 Diabetes, Asthma. No bleeding disorders. "
        "Immunizations up to date including tetanus (DTaP at 4 years, Tdap at age 10).",
    vital_signs=create_vitals(
      encounter_date, age_months, patient.demographics.sex_at_birth,
      hr_modifier=1.1  # Slightly elevated from pain/anxiety
    ),
    physical_exam=PhysicalExam(
      general="Alert, tearful but consolable, holding right arm",
      heent="No head trauma, PERRLA",
      cardiovascular="Tachycardic but regular, no murmur",
      respiratory="Clear bilaterally",
      musculoskeletal="RIGHT FOREARM: 4cm linear laceration on volar aspect of mid-forearm. "
                      "Wound edges clean, no debris visible. Bleeding controlled. "
                      "Depth appears through dermis, subcutaneous tissue visible, "
                      "no muscle or tendon involvement. Full ROM of wrist and fingers. "
                      "Sensation intact to light touch in median, ulnar, radial distributions. "
                      "Radial pulse 2+, capillary refill <2 seconds.",
      skin="Laceration as described. Multiple abrasions to right palm and knee, superficial. "
           "No other injuries noted.",
    ),
    assessment=[
      Assessment(
        diagnosis="Laceration of forearm, right",
        code=CodeableConcept(
          system="http://hl7.org/fhir/sid/icd-10-cm",
          code="S51.819A",
          display="Laceration without foreign body of unspecified forearm, initial encounter",
        ),
        clinical_notes="4cm linear laceration, clean edges, requires suture repair",
        is_primary=True,
      ),
      Assessment(
        diagnosis="Abrasions, right hand and knee",
        code=CodeableConcept(
          system="http://hl7.org/fhir/sid/icd-10-cm",
          code="S00.81XA",
          display="Abrasion of other part of head, initial encounter",
        ),
        clinical_notes="Superficial, cleaned and dressed",
      ),
      Assessment(
        diagnosis="Bicycle accident",
        code=CodeableConcept(
          system="http://hl7.org/fhir/sid/icd-10-cm",
          code="V19.4XXA",
          display="Pedal cyclist injured in noncollision transport accident",
        ),
      ),
    ],
    plan=[
      PlanItem(category="procedure", description="Wound irrigation with normal saline"),
      PlanItem(category="procedure", description="Laceration repair with 4-0 nylon sutures, 8 sutures placed"),
      PlanItem(category="medication", description="Lidocaine 1% with epinephrine for local anesthesia"),
      PlanItem(category="medication", description="Ibuprofen 400mg PO for pain (given in office)"),
      PlanItem(category="medication", description="Ibuprofen 400mg PO Q6H PRN pain x 3 days"),
      PlanItem(category="education", description="Wound care instructions provided: "
               "Keep dry x 24 hours, then may shower. No soaking or swimming."),
      PlanItem(category="education", description="Signs of infection to watch for: "
               "increasing redness, warmth, swelling, drainage, fever"),
      PlanItem(category="other", description="Tetanus up to date - no booster needed"),
      PlanItem(category="follow-up", description="Return in 10-12 days for suture removal"),
      PlanItem(category="education", description="Monitor blood sugars - stress can cause elevation"),
    ],
    prescriptions=[
      Medication(
        display_name="Ibuprofen",
        code=CodeableConcept(
          system="http://www.nlm.nih.gov/research/umls/rxnorm",
          code="5640",
          display="Ibuprofen",
        ),
        dose_quantity="400",
        dose_unit="mg",
        frequency="Q6H PRN",
        route="oral",
        prn=True,
        prn_reason="Pain",
        status=MedicationStatus.ACTIVE,
        start_date=encounter_date.date(),
        end_date=encounter_date.date() + timedelta(days=3),
        indication="Pain from laceration",
        instructions="Take with food. Do not exceed 4 doses per day.",
      ),
    ],
    follow_up_instructions="Return in 10-12 days for suture removal. "
                           "Return sooner if signs of infection develop.",
    follow_up_interval="10-12 days",
    narrative_note="""
PROCEDURE NOTE:

Informed consent obtained from parent. Procedure, risks, benefits, and alternatives discussed.

The wound was examined and found to be clean with well-approximated edges.
Wound irrigated with 200mL normal saline. No foreign bodies identified.

Local anesthesia achieved with 3mL of 1% lidocaine with epinephrine via wound edge infiltration.
Adequate anesthesia confirmed.

Wound closed with 8 simple interrupted sutures using 4-0 nylon.
Good approximation achieved. Hemostasis confirmed.

Sterile dressing applied.

Patient tolerated procedure well. Vital signs stable throughout.
Discharge instructions reviewed with parent.

Signed: Dr. Sarah Chen, MD, FAAP
""",
  )


# =============================================================================
# MAIN GENERATION
# =============================================================================

def add_allergy_if_missing(patient: Patient) -> None:
  """Add a realistic allergy if the patient doesn't have one."""
  if not patient.allergy_list:
    patient.allergy_list.append(
      Allergy(
        display_name="Penicillin",
        code=CodeableConcept(
          system="http://www.nlm.nih.gov/research/umls/rxnorm",
          code="7984",
          display="Penicillin",
        ),
        category=AllergyCategory.MEDICATION,
        criticality="high",
        reactions=[
          AllergyReaction(
            manifestation="Rash, hives",
            severity=AllergySeverity.MODERATE,
          )
        ],
        onset_date=date(
          patient.demographics.date_of_birth.year + 3,
          6, 15
        ),
        clinical_status="active",
        verification_status="confirmed",
        notes="Developed rash after amoxicillin at age 3. Avoid all penicillins.",
      )
    )


def ensure_chronic_medications(patient: Patient) -> None:
  """Ensure the patient has appropriate chronic medications for their conditions."""
  # Check for existing medications
  has_insulin = any("insulin" in m.display_name.lower() for m in patient.medication_list)
  has_controller = any(
    any(name in m.display_name.lower() for name in ["fluticasone", "budesonide", "flovent"])
    for m in patient.medication_list
  )
  has_rescue = any("albuterol" in m.display_name.lower() for m in patient.medication_list)

  dob = patient.demographics.date_of_birth

  if not has_insulin:
    # Add insulin glargine (basal)
    patient.medication_list.append(
      Medication(
        display_name="Insulin Glargine (Lantus)",
        code=CodeableConcept(
          system="http://www.nlm.nih.gov/research/umls/rxnorm",
          code="261551",
          display="Insulin Glargine",
        ),
        dose_quantity="12",
        dose_unit="units",
        frequency="Once daily at bedtime",
        route="subcutaneous",
        status=MedicationStatus.ACTIVE,
        start_date=date(dob.year + 4, 3, 15),  # At T1D diagnosis
        indication="Type 1 Diabetes - basal insulin",
        instructions="Inject subcutaneously at same time each evening. "
                     "Rotate injection sites.",
      )
    )
    # Add insulin lispro (bolus)
    patient.medication_list.append(
      Medication(
        display_name="Insulin Lispro (Humalog)",
        code=CodeableConcept(
          system="http://www.nlm.nih.gov/research/umls/rxnorm",
          code="86009",
          display="Insulin Lispro",
        ),
        dose_quantity="1",
        dose_unit="unit per 10g carbs",
        frequency="With meals and snacks",
        route="subcutaneous",
        status=MedicationStatus.ACTIVE,
        start_date=date(dob.year + 4, 3, 15),
        indication="Type 1 Diabetes - mealtime insulin",
        instructions="Dose based on carbohydrate intake. Use insulin pump.",
      )
    )

  if not has_controller:
    patient.medication_list.append(
      Medication(
        display_name="Fluticasone (Flovent) 44mcg",
        code=CodeableConcept(
          system="http://www.nlm.nih.gov/research/umls/rxnorm",
          code="746762",
          display="Fluticasone 44 MCG/ACTUATION",
        ),
        dose_quantity="44",
        dose_unit="mcg",
        frequency="2 puffs BID",
        route="inhaled",
        status=MedicationStatus.ACTIVE,
        start_date=date(dob.year + 2, 9, 10),  # At asthma diagnosis
        indication="Asthma - controller medication",
        instructions="Rinse mouth after use to prevent thrush.",
      )
    )

  if not has_rescue:
    patient.medication_list.append(
      Medication(
        display_name="Albuterol (ProAir) HFA",
        code=CodeableConcept(
          system="http://www.nlm.nih.gov/research/umls/rxnorm",
          code="745752",
          display="Albuterol 90 MCG/ACTUATION",
        ),
        dose_quantity="90",
        dose_unit="mcg",
        frequency="2 puffs Q4-6H PRN",
        route="inhaled",
        status=MedicationStatus.ACTIVE,
        start_date=date(dob.year + 2, 9, 10),
        indication="Asthma - rescue inhaler",
        instructions="Use for shortness of breath, wheezing, or before exercise.",
      )
    )


def generate_patient() -> Patient:
  """Generate the complete scripted patient."""
  print("Initializing PedsEngine...")
  engine = PedsEngine()

  # Create generation seed
  seed = GenerationSeed(
    age_months=PATIENT_CONFIG["age_months"],
    sex=PATIENT_CONFIG["sex"],
    conditions=PATIENT_CONFIG["conditions"],
    include_narrative_notes=True,
    messiness_level=0,  # Clean chart for educational purposes
  )

  print("Generating base patient...")
  patient = engine.generate(seed)

  print(f"Base patient generated: {patient.demographics.full_name}")
  print(f"  DOB: {patient.demographics.date_of_birth}")
  print(f"  Age: {patient.demographics.age_years} years")
  print(f"  Conditions: {[c.display_name for c in patient.problem_list]}")
  print(f"  Encounters: {len(patient.encounters)}")

  # Add allergy if missing
  add_allergy_if_missing(patient)

  # Ensure chronic medications
  ensure_chronic_medications(patient)

  # Now create the 5 specific encounters
  print("\nCreating 5 scripted encounters...")
  dob = patient.demographics.date_of_birth

  # Encounter A: Well-child checkup at 9 years (108 months)
  print("  1. Creating well-child checkup (9 years)...")
  well_child = create_well_child_encounter(patient, age_months=108)

  # Encounter B: Cold at ~9.5 years (114 months)
  print("  2. Creating cold/URI encounter...")
  cold = create_cold_encounter(patient, age_months=114)

  # Encounter C: Pneumonia follow-up 9 days after cold
  print("  3. Creating pneumonia follow-up (9 days after cold)...")
  pneumonia = create_pneumonia_followup_encounter(patient, age_months=114, cold_encounter=cold)

  # Encounter D: Combined chronic care at ~9.75 years (117 months)
  print("  4. Creating combined chronic care follow-up...")
  chronic_care = create_chronic_care_encounter(patient, age_months=117)

  # Encounter E: Injury at ~9.9 years (119 months)
  print("  5. Creating injury encounter...")
  injury = create_injury_encounter(patient, age_months=119)

  # Add the scripted encounters to the patient
  scripted_encounters = [well_child, cold, pneumonia, chronic_care, injury]

  # Remove any existing encounters that overlap with our scripted dates
  # (within 7 days of our scripted encounters)
  scripted_dates = [e.date for e in scripted_encounters]
  filtered_encounters = []
  for enc in patient.encounters:
    overlap = False
    for sd in scripted_dates:
      if abs((enc.date - sd).days) < 7:
        overlap = True
        break
    if not overlap:
      filtered_encounters.append(enc)

  # Combine and sort all encounters by date
  all_encounters = filtered_encounters + scripted_encounters
  all_encounters.sort(key=lambda e: e.date)

  patient.encounters = all_encounters

  print(f"\nFinal patient has {len(patient.encounters)} encounters")
  print(f"  - Well-child visits: {sum(1 for e in patient.encounters if e.type == EncounterType.WELL_CHILD)}")
  print(f"  - Acute illness: {sum(1 for e in patient.encounters if e.type == EncounterType.ACUTE_ILLNESS)}")
  print(f"  - Chronic follow-up: {sum(1 for e in patient.encounters if e.type == EncounterType.CHRONIC_FOLLOWUP)}")
  print(f"  - Injuries: {sum(1 for e in patient.encounters if e.type == EncounterType.ACUTE_INJURY)}")

  return patient


def export_patient(patient: Patient, output_dir: Path) -> None:
  """Export the patient to JSON and C-CDA formats."""
  output_dir.mkdir(parents=True, exist_ok=True)

  patient_id = patient.id[:8]  # Short ID for filenames

  # Export JSON
  json_path = output_dir / f"{patient_id}_patient.json"
  print(f"\nExporting JSON to {json_path}...")
  export_json(patient, json_path)
  print(f"  JSON size: {json_path.stat().st_size / 1024:.1f} KB")

  # Export C-CDA
  ccda_path = output_dir / f"{patient_id}_patient.xml"
  print(f"Exporting C-CDA to {ccda_path}...")
  export_ccda(patient, ccda_path)
  print(f"  C-CDA size: {ccda_path.stat().st_size / 1024:.1f} KB")

  print(f"\nFiles saved to {output_dir}/")


def main():
  """Main entry point."""
  print("=" * 60)
  print("OREAD - Scripted Patient Generator")
  print("=" * 60)
  print()

  # Generate the patient
  patient = generate_patient()

  # Export to output directory
  output_dir = Path(__file__).parent.parent / "output"
  export_patient(patient, output_dir)

  # Print summary
  print("\n" + "=" * 60)
  print("GENERATION COMPLETE")
  print("=" * 60)
  print(f"\nPatient: {patient.demographics.full_name}")
  print(f"ID: {patient.id}")
  print(f"Age: {patient.demographics.age_years} years")
  print(f"DOB: {patient.demographics.date_of_birth}")
  print(f"\nConditions:")
  for c in patient.problem_list:
    print(f"  - {c.display_name}")
  print(f"\nActive Medications:")
  for m in patient.active_medications:
    print(f"  - {m.display_name}")
  print(f"\nAllergies:")
  for a in patient.allergy_list:
    reactions_str = ", ".join(r.manifestation for r in a.reactions) if a.reactions else "Unknown"
    print(f"  - {a.display_name}: {reactions_str}")
  print(f"\n5 Scripted Encounters:")
  scripted_types = [
    EncounterType.WELL_CHILD,
    EncounterType.ACUTE_ILLNESS,
    EncounterType.CHRONIC_FOLLOWUP,
    EncounterType.ACUTE_INJURY,
  ]
  for enc in patient.encounters:
    if enc.type in scripted_types:
      print(f"  - {enc.date.strftime('%Y-%m-%d')}: {enc.type.value} - {enc.chief_complaint[:50]}...")

  print("\nDone!")


if __name__ == "__main__":
  main()
