"""
Chart Messiness Injection System

Introduces realistic EHR artifacts and errors to synthetic patient data
for training robust ML systems and teaching chart review skills.

Messiness Levels (Difficulty-Based):
  0 - Pristine: Teaching ideal - learn what "right" looks like
  1 - Real World: Minor inconsistencies, abbreviations, nothing dangerous
  2 - Busy Clinic: Copy-forward artifacts, stale data, workflow noise
  3 - Needs Reconciliation: Conflicts requiring clinical judgment to resolve
  4 - Safety Landmines: Hidden dangers among the noise - must triage
  5 - Chart From Hell: Threading errors, near-misses, medicolegal nightmares

Error Categories:
  A) Safety-critical (8-12 at level 5): Things that could change clinical decisions
  B) Workflow-realistic (10-15): Copy-forward, template defaults, reconciliation misses
  C) Data-integrity (8-12): Duplication, stale vitals, wrong mappings
  D) Coding/billing (3-6): ICD/CVX/CPT mismatches
  E) Medicolegal cringe (2-4): Truncation, wrong patient info, contradictions

Timeline Distribution:
  - Early (infancy): Dosing/age errors + 1-2 documentation glitches
  - Middle: Workflow mess (copy-forward, stale vitals, problem list drift)
  - Recent: Reconciliation failures (allergies, meds, immunizations)
"""

import random
from typing import Any
from dataclasses import dataclass, field
from enum import Enum


class ErrorCategory(Enum):
  """Categories of chart errors."""
  SAFETY_CRITICAL = "safety_critical"
  WORKFLOW = "workflow"
  DATA_INTEGRITY = "data_integrity"
  CODING_BILLING = "coding_billing"
  MEDICOLEGAL = "medicolegal"


@dataclass
class ChartError:
  """Represents a single chart error."""
  category: ErrorCategory
  name: str
  description: str
  severity: int  # 1-5, 5 being most dangerous
  applicable_ages: tuple[int, int] = (0, 216)  # age range in months


@dataclass
class ThreadingError:
  """A multi-visit error that evolves incorrectly across the chart."""
  name: str
  description: str
  stages: list[dict] = field(default_factory=list)


class MessinessInjector:
  """Injects realistic chart messiness into synthetic patient data."""

  # ==========================================================================
  # LEVEL DESCRIPTIONS
  # ==========================================================================
  LEVEL_DESCRIPTIONS = {
    0: ("Pristine", "Teaching ideal - learn what 'right' looks like"),
    1: ("Real World", "Minor inconsistencies, abbreviations, nothing dangerous"),
    2: ("Busy Clinic", "Copy-forward artifacts, stale data, workflow noise"),
    3: ("Needs Reconciliation", "Conflicts requiring clinical judgment to resolve"),
    4: ("Safety Landmines", "Hidden dangers among the noise - must triage"),
    5: ("Chart From Hell", "Threading errors, near-misses, medicolegal nightmares"),
  }

  # ==========================================================================
  # A) SAFETY-CRITICAL ERRORS (8-12 at level 5)
  # ==========================================================================
  SAFETY_CRITICAL_ERRORS = [
    ChartError(
      ErrorCategory.SAFETY_CRITICAL,
      "allergy_nkda_contradiction",
      "Allergy list says 'NKDA' but note mentions reaction to medication",
      severity=5,
    ),
    ChartError(
      ErrorCategory.SAFETY_CRITICAL,
      "allergy_reaction_drift",
      "Note says 'diarrhea with amox' but allergy list says 'anaphylaxis'",
      severity=5,
    ),
    ChartError(
      ErrorCategory.SAFETY_CRITICAL,
      "weight_dosing_mismatch",
      "Current weight 12.5 kg but dose calculated for 9 kg or 16 kg",
      severity=5,
    ),
    ChartError(
      ErrorCategory.SAFETY_CRITICAL,
      "concentration_mismatch",
      "Instructions assume 400 mg/5 mL but order is 250 mg/5 mL",
      severity=5,
    ),
    ChartError(
      ErrorCategory.SAFETY_CRITICAL,
      "duplicate_med_different_sigs",
      "Same med listed twice with different sigs (BID x10 vs TID x7)",
      severity=4,
    ),
    ChartError(
      ErrorCategory.SAFETY_CRITICAL,
      "age_cutoff_violation",
      "Ibuprofen prescribed under 6 months",
      severity=5,
      applicable_ages=(0, 6),
    ),
    ChartError(
      ErrorCategory.SAFETY_CRITICAL,
      "conditional_plan_both_ordered",
      "Option A/Option B plan but both accidentally ordered as active",
      severity=4,
    ),
    ChartError(
      ErrorCategory.SAFETY_CRITICAL,
      "med_stopped_still_active",
      "Note says 'discontinue albuterol' but remains on active list",
      severity=4,
    ),
    ChartError(
      ErrorCategory.SAFETY_CRITICAL,
      "wrong_vaccine_route",
      "Vaccine documented IM but entered as SC",
      severity=3,
    ),
    ChartError(
      ErrorCategory.SAFETY_CRITICAL,
      "wrong_patient_instruction",
      "'Return to school tomorrow' for toddler, or 'avoid driving' in pediatric chart",
      severity=2,
    ),
    ChartError(
      ErrorCategory.SAFETY_CRITICAL,
      "red_flag_buried",
      "'Lethargic, poor perfusion' in HPI but plan says 'reassurance'",
      severity=5,
    ),
    ChartError(
      ErrorCategory.SAFETY_CRITICAL,
      "phantom_lab_reference",
      "'Strep positive' referenced but no lab result exists",
      severity=4,
    ),
  ]

  # ==========================================================================
  # B) WORKFLOW-REALISTIC ERRORS (10-15 at level 5)
  # ==========================================================================
  WORKFLOW_ERRORS = [
    ChartError(
      ErrorCategory.WORKFLOW,
      "cc_narrative_mismatch",
      "Chief complaint 'well child' but narrative describes acute illness",
      severity=2,
    ),
    ChartError(
      ErrorCategory.WORKFLOW,
      "ros_contradiction",
      "Copy-forward ROS says 'no fever' but same note says 'fever 102'",
      severity=3,
    ),
    ChartError(
      ErrorCategory.WORKFLOW,
      "pe_self_contradiction",
      "'No murmur' and 'murmur present' in same physical exam",
      severity=4,
    ),
    ChartError(
      ErrorCategory.WORKFLOW,
      "problem_list_outdated",
      "AOM stays 'Active' for 12+ months",
      severity=2,
    ),
    ChartError(
      ErrorCategory.WORKFLOW,
      "resolved_problem_reappears",
      "Resolved problem reappears when template reused",
      severity=2,
    ),
    ChartError(
      ErrorCategory.WORKFLOW,
      "wrong_counseling",
      "Sleep counseling template inserted into otitis visit",
      severity=1,
    ),
    ChartError(
      ErrorCategory.WORKFLOW,
      "social_history_mismatch",
      "Lives with both parents but household members says 1",
      severity=1,
    ),
    ChartError(
      ErrorCategory.WORKFLOW,
      "wrong_pronoun_sibling",
      "Wrong pronoun or sibling name appears once",
      severity=2,
    ),
    ChartError(
      ErrorCategory.WORKFLOW,
      "orphan_followup",
      "'Follow up with cardiology' with no cardiac issue documented",
      severity=2,
    ),
    ChartError(
      ErrorCategory.WORKFLOW,
      "referral_not_ordered",
      "Referral mentioned in note but never actually ordered",
      severity=2,
    ),
    ChartError(
      ErrorCategory.WORKFLOW,
      "diagnosis_name_drift",
      "'Reactive airway disease' vs 'asthma' vs 'wheezing' across visits",
      severity=2,
    ),
    ChartError(
      ErrorCategory.WORKFLOW,
      "listening_failure",
      "Parent reports X but assessment describes Y",
      severity=3,
    ),
    ChartError(
      ErrorCategory.WORKFLOW,
      "vaccine_note_no_entry",
      "'Vaccines given today' in note but no immunization table entry",
      severity=3,
    ),
  ]

  # ==========================================================================
  # C) DATA-INTEGRITY ERRORS (8-12 at level 5)
  # ==========================================================================
  DATA_INTEGRITY_ERRORS = [
    ChartError(
      ErrorCategory.DATA_INTEGRITY,
      "duplicate_encounter",
      "Two encounters same date with slightly different vitals/assessment",
      severity=2,
    ),
    ChartError(
      ErrorCategory.DATA_INTEGRITY,
      "stale_height",
      "Height unchanged for 18+ months despite growth",
      severity=2,
    ),
    ChartError(
      ErrorCategory.DATA_INTEGRITY,
      "temp_over_precision",
      "Temperature 98.64732°F (import artifact)",
      severity=1,
    ),
    ChartError(
      ErrorCategory.DATA_INTEGRITY,
      "percentile_value_mismatch",
      "Weight 12.5 kg but percentile shows '99th'",
      severity=2,
    ),
    ChartError(
      ErrorCategory.DATA_INTEGRITY,
      "growth_age_label_off",
      "Growth measurement labeled wrong age",
      severity=2,
    ),
    ChartError(
      ErrorCategory.DATA_INTEGRITY,
      "address_mismatch",
      "City/state/ZIP don't match (Phoenix, AZ 90210)",
      severity=1,
    ),
    ChartError(
      ErrorCategory.DATA_INTEGRITY,
      "encounter_date_order",
      "'Follow-up visit' dated before 'initial visit'",
      severity=2,
    ),
    ChartError(
      ErrorCategory.DATA_INTEGRITY,
      "duplicate_vaccine_dose",
      "Dose #3 of DTaP recorded twice on different dates",
      severity=3,
    ),
    ChartError(
      ErrorCategory.DATA_INTEGRITY,
      "historical_med_reactivated",
      "Medication from 2 years ago suddenly reappears as active",
      severity=2,
    ),
    ChartError(
      ErrorCategory.DATA_INTEGRITY,
      "placeholder_text",
      "Problem list has '...' or truncated diagnosis name",
      severity=1,
    ),
  ]

  # ==========================================================================
  # D) CODING/BILLING ERRORS (3-6 at level 5)
  # ==========================================================================
  CODING_BILLING_ERRORS = [
    ChartError(
      ErrorCategory.CODING_BILLING,
      "icd_too_vague",
      "R69 (unspecified illness) where specific code exists",
      severity=1,
    ),
    ChartError(
      ErrorCategory.CODING_BILLING,
      "icd_visit_mismatch",
      "Viral URI visit billed as AOM",
      severity=2,
    ),
    ChartError(
      ErrorCategory.CODING_BILLING,
      "cvx_mismatch",
      "Vaccine name says DTaP but CVX corresponds to Tdap",
      severity=3,
    ),
    ChartError(
      ErrorCategory.CODING_BILLING,
      "cpt_note_conflict",
      "Counseling level suggests high complexity but note is thin",
      severity=2,
    ),
  ]

  # ==========================================================================
  # E) MEDICOLEGAL CRINGE ERRORS (2-4 at level 5)
  # ==========================================================================
  MEDICOLEGAL_ERRORS = [
    ChartError(
      ErrorCategory.MEDICOLEGAL,
      "note_truncation",
      "Note cuts off mid-sentence",
      severity=2,
    ),
    ChartError(
      ErrorCategory.MEDICOLEGAL,
      "missing_signature",
      "Note missing clinician signature/attestation line",
      severity=2,
    ),
    ChartError(
      ErrorCategory.MEDICOLEGAL,
      "time_statement_nonsense",
      "'Spent 45 min face-to-face' on a two-sentence note",
      severity=2,
    ),
    ChartError(
      ErrorCategory.MEDICOLEGAL,
      "data_wrong_section",
      "Assessment text pasted into HPI section",
      severity=2,
    ),
  ]

  # ==========================================================================
  # THREADING ERRORS (Boss Level)
  # ==========================================================================
  THREADING_ERRORS = [
    ThreadingError(
      name="amox_allergy_escalation",
      description="Amoxicillin rash → allergy escalation → unsafe re-exposure",
      stages=[
        {"visit": "A", "content": "Rash on day 7 of amoxicillin; likely viral exanthem vs drug rash. Completed course."},
        {"visit": "B", "content": "Allergy list updated: 'Penicillin - anaphylaxis' (over-escalation)"},
        {"visit": "C", "content": "Augmentin prescribed for AOM. (Re-exposure to penicillin-class without comment)"},
      ],
    ),
    ThreadingError(
      name="rad_asthma_undertreatment",
      description="Reactive airway → asthma diagnosis drift → undertreated persistent asthma",
      stages=[
        {"visit": "A", "content": "Post-viral wheeze, albuterol PRN prescribed."},
        {"visit": "B", "content": "'RAD' added to problem list. Continue albuterol PRN."},
        {"visit": "C", "content": "'Asthma' mentioned in assessment but no controller discussed."},
        {"visit": "D", "content": "Third albuterol refill in 6 months. Still no ICS prescribed."},
      ],
    ),
    ThreadingError(
      name="weight_tracking_failure",
      description="Weight gain missed → obesity develops → metabolic consequences",
      stages=[
        {"visit": "A", "content": "Weight 75th percentile. 'Healthy weight.'"},
        {"visit": "B", "content": "Weight 85th percentile. No comment on trajectory."},
        {"visit": "C", "content": "Weight 95th percentile. 'Well-nourished.'"},
        {"visit": "D", "content": "BMI >99th percentile. First mention: 'Discuss healthy eating.'"},
      ],
    ),
    ThreadingError(
      name="developmental_delay_missed",
      description="Subtle delays → parental concerns dismissed → late intervention",
      stages=[
        {"visit": "A", "content": "12mo: Not pointing yet. 'Will monitor.'"},
        {"visit": "B", "content": "15mo: No words. Mom concerned. 'Boys talk later.'"},
        {"visit": "C", "content": "18mo: M-CHAT positive. 'Rescreen at 24 months.'"},
        {"visit": "D", "content": "30mo: Still no referral placed. 'Speech delay noted.'"},
      ],
    ),
  ]

  # ==========================================================================
  # EXISTING ERROR BANKS (preserved from original)
  # ==========================================================================

  # Level 1: Abbreviations and shorthand
  ABBREVIATIONS = {
    "patient": ["pt", "pt.", "patient"],
    "history": ["hx", "h/o", "history"],
    "diagnosis": ["dx", "Dx", "diagnosis"],
    "treatment": ["tx", "Tx", "treatment"],
    "prescription": ["rx", "Rx", "prescription"],
    "symptoms": ["sx", "Sx", "symptoms"],
    "without": ["w/o", "without"],
    "with": ["w/", "c̄", "with"],
    "before": ["b/f", "pre", "before"],
    "after": ["a/f", "post", "after"],
    "times": ["x", "×", "times"],
    "bilateral": ["b/l", "bilat", "bilateral"],
    "temperature": ["temp", "T", "temperature"],
    "blood pressure": ["BP", "b/p", "blood pressure"],
    "heart rate": ["HR", "heart rate"],
    "respiratory rate": ["RR", "resp rate", "respiratory rate"],
    "years old": ["y/o", "yo", "years old"],
    "months old": ["m/o", "mo", "months old"],
    "complains of": ["c/o", "complains of"],
    "no known allergies": ["NKA", "NKDA", "no known allergies"],
    "within normal limits": ["WNL", "wnl", "within normal limits"],
    "as needed": ["prn", "PRN", "as needed"],
    "twice daily": ["BID", "bid", "twice daily"],
    "three times daily": ["TID", "tid", "three times daily"],
    "four times daily": ["QID", "qid", "four times daily"],
    "every": ["q", "Q", "every"],
    "hours": ["h", "hr", "hrs", "hours"],
    "milligrams": ["mg", "milligrams"],
    "milliliters": ["mL", "ml", "cc", "milliliters"],
    "by mouth": ["PO", "po", "by mouth"],
    "nothing by mouth": ["NPO", "npo", "nothing by mouth"],
  }

  # Level 2: Zombie note fragments (outdated copy-forward text)
  ZOMBIE_FRAGMENTS = {
    "infant": [
      "Fontanelle is soft and flat.",
      "Anterior fontanelle open and flat.",
      "Umbilical cord stump clean and dry.",
      "Moro reflex present and symmetric.",
      "Primitive reflexes intact.",
    ],
    "toddler": [
      "Walking with assistance.",
      "Says 2-3 words.",
      "Still in diapers.",
    ],
    "child": [
      "Mother reports child is potty trained.",
      "Attends preschool.",
    ],
    "universal": [
      "Follow up in 2 weeks.",  # From 3 years ago
      "Labs pending.",  # Never ordered
      "Referral to specialist sent.",  # Never completed
      "Patient counseled on smoking cessation.",  # For a 5-year-old
      "Continue current medications.",  # Even if changed
    ],
  }

  # Level 3: Implicit diagnoses (treating without coding)
  IMPLICIT_DIAGNOSIS_PATTERNS = {
    "asthma": {
      "medications": ["albuterol", "flovent", "advair", "singulair"],
      "phrases": ["wheezing improved", "breathing treatments", "rescue inhaler used"],
      "wrong_codes": ["R05", "R06.2", "J06.9"],  # Cough, Wheezing, URI instead of J45.x
    },
    "adhd": {
      "medications": ["adderall", "ritalin", "vyvanse", "concerta"],
      "phrases": ["focus improved", "behavior better on medication"],
      "wrong_codes": ["R41.840", "F90.9"],  # Attention deficit, Unspecified ADHD
    },
    "allergic_rhinitis": {
      "medications": ["zyrtec", "claritin", "flonase", "allegra"],
      "phrases": ["seasonal allergies", "runny nose improved"],
      "wrong_codes": ["J00", "R09.81"],  # Common cold, Nasal congestion
    },
    "eczema": {
      "medications": ["hydrocortisone", "triamcinolone", "eucerin"],
      "phrases": ["rash improved", "dry skin", "itching better"],
      "wrong_codes": ["L29.9", "R21"],  # Pruritus, Rash
    },
  }

  # Level 4: Dictation errors (voice-to-text mistakes)
  DICTATION_ERRORS = {
    "ileum": ["ilium"],  # Gut vs hip bone
    "peroneal": ["perineal"],  # Leg vs groin
    "hypotension": ["hypertension"],  # Opposite conditions!
    "dysphagia": ["dysphasia"],  # Swallowing vs speech
    "prostrate": ["prostate"],
    "reflex": ["reflux"],
    "ante": ["anti"],
    "hyper": ["hypo"],
    "inter": ["intra"],
    "oral": ["aural"],  # Mouth vs ear
    "iliac": ["ileac"],
    "mucous": ["mucus"],
    "discrete": ["discreet"],
    "palpation": ["palpitation"],
    "perfusion": ["profusion"],
    "celexa": ["celebrex"],  # Antidepressant vs painkiller
    "zantac": ["zyrtec"],  # Acid reducer vs antihistamine
    "lamictal": ["lamisil"],  # Seizure med vs antifungal
    "klonopin": ["clonidine"],  # Anxiety vs blood pressure
  }

  # Level 4: Pronoun mismatches (template errors)
  PRONOUN_SWAPS = {
    "male_to_female": {
      "He ": "She ",
      "he ": "she ",
      "His ": "Her ",
      "his ": "her ",
      "him": "her",
      "boy": "girl",
      "son": "daughter",
      "male": "female",
    },
    "female_to_male": {
      "She ": "He ",
      "she ": "he ",
      "Her ": "His ",
      "her ": "his ",
      "girl": "boy",
      "daughter": "son",
      "female": "male",
    },
  }

  # Level 4: Sex-inappropriate exam findings (template copy-paste)
  WRONG_SEX_FINDINGS = {
    "male": [
      "Ovaries non-palpable.",
      "Uterus not enlarged.",
      "Last menstrual period: N/A",
      "Breast exam: no masses.",
    ],
    "female": [
      "Testes descended bilaterally.",
      "Prostate exam deferred.",
      "Penis: circumcised, no lesions.",
    ],
  }

  # Level 5: ISMP dangerous abbreviations
  ISMP_VIOLATIONS = {
    "trailing_zero": [
      ("5 mg", "5.0 mg"),  # Could be read as 50mg
      ("1 mg", "1.0 mg"),
      ("2 mg", "2.0 mg"),
    ],
    "no_leading_zero": [
      ("0.5 mg", ".5 mg"),  # Could be read as 5mg
      ("0.25 mg", ".25 mg"),
      ("0.1 mg", ".1 mg"),
    ],
    "u_for_units": [
      ("10 units", "10U"),  # Could be read as 100
      ("4 units", "4U"),  # Could be read as 40
      ("6 units", "6U"),
    ],
    "qd_confusion": [
      ("daily", "QD"),  # Could be read as QID (4x daily)
      ("daily", "qd"),
      ("every day", "q.d."),
    ],
    "qod_confusion": [
      ("every other day", "QOD"),  # Could be read as QD
      ("every other day", "q.o.d."),
    ],
    "iu_confusion": [
      ("international units", "IU"),  # Could be read as IV
      ("10 international units", "10 IU"),
    ],
    "mcg_ug": [
      ("mcg", "μg"),  # μ could be read as m (1000x overdose)
      ("100 mcg", "100 μg"),
    ],
    "drug_name_abbrev": [
      ("morphine sulfate", "MS"),  # Could be magnesium sulfate
      ("magnesium sulfate", "MgSO4"),
      ("hydrochlorothiazide", "HCTZ"),
    ],
  }

  # Level 5: Allergy-prescription conflicts
  ALLERGY_RX_CONFLICTS = {
    "penicillin": ["amoxicillin", "ampicillin", "augmentin", "penicillin VK"],
    "sulfa": ["bactrim", "septra", "sulfamethoxazole"],
    "cephalosporins": ["cephalexin", "keflex", "cefdinir"],
    "nsaids": ["ibuprofen", "naproxen", "meloxicam"],
    "aspirin": ["aspirin", "excedrin"],
  }

  # Level 3: Structural contradictions
  CONTRADICTIONS = {
    "ros_vs_note": [
      {
        "structured": "Review of Systems: All negative",
        "free_text": "Patient complains of severe ear pain and fever.",
      },
      {
        "structured": "Review of Systems: [x] No fever",
        "free_text": "Temperature 102.4°F, febrile.",
      },
      {
        "structured": "Review of Systems: [x] No pain",
        "free_text": "Patient rates pain 8/10.",
      },
    ],
    "vitals_vs_note": [
      {
        "structured_bp": "120/80",
        "note_bp": "hypertensive at 160/100",
      },
      {
        "structured_temp": "98.6",
        "note_temp": "febrile to 103",
      },
    ],
  }

  def __init__(self, level: int = 0, seed: int | None = None):
    """
    Initialize messiness injector.

    Args:
      level: Messiness level 0-5
      seed: Random seed for reproducibility
    """
    self.level = max(0, min(5, level))
    self.rng = random.Random(seed)
    self._selected_threading_error = None
    self._injected_errors = []  # Track what we've injected

  def get_level_description(self) -> tuple[str, str]:
    """Get the name and description for current level."""
    return self.LEVEL_DESCRIPTIONS.get(self.level, ("Unknown", ""))

  def get_error_distribution(self) -> dict[str, int]:
    """
    Get target error counts by category for current level.

    Returns dict with target counts for each category.
    """
    distributions = {
      0: {"safety": 0, "workflow": 0, "data": 0, "coding": 0, "medicolegal": 0},
      1: {"safety": 0, "workflow": 2, "data": 2, "coding": 0, "medicolegal": 0},
      2: {"safety": 0, "workflow": 5, "data": 4, "coding": 1, "medicolegal": 0},
      3: {"safety": 2, "workflow": 8, "data": 6, "coding": 2, "medicolegal": 1},
      4: {"safety": 5, "workflow": 10, "data": 8, "coding": 3, "medicolegal": 2},
      5: {"safety": 10, "workflow": 12, "data": 10, "coding": 5, "medicolegal": 3},
    }
    return distributions.get(self.level, distributions[0])

  def select_threading_error(self) -> ThreadingError | None:
    """Select a threading error for level 5 charts."""
    if self.level < 5:
      return None
    if self._selected_threading_error is None:
      self._selected_threading_error = self.rng.choice(self.THREADING_ERRORS)
    return self._selected_threading_error

  def get_errors_for_timeline_position(
    self,
    position: str,  # "early", "middle", "recent"
    age_months: int,
  ) -> list[ChartError]:
    """
    Get appropriate errors for a position in the patient's timeline.

    Args:
      position: "early" (infancy), "middle", or "recent"
      age_months: Current age of patient

    Returns:
      List of applicable ChartError objects
    """
    if self.level == 0:
      return []

    distribution = self.get_error_distribution()
    selected_errors = []

    if position == "early":
      # Infancy: dosing/age errors + 1-2 documentation glitches
      safety_pool = [e for e in self.SAFETY_CRITICAL_ERRORS
                     if e.applicable_ages[0] <= age_months <= e.applicable_ages[1]
                     and e.name in ("weight_dosing_mismatch", "age_cutoff_violation", "concentration_mismatch")]
      if safety_pool and self.level >= 3:
        selected_errors.extend(self.rng.sample(safety_pool, min(2, len(safety_pool))))

      if self.level >= 2:
        data_pool = [e for e in self.DATA_INTEGRITY_ERRORS if e.severity <= 2]
        selected_errors.extend(self.rng.sample(data_pool, min(1, len(data_pool))))

    elif position == "middle":
      # Middle: workflow mess (copy-forward, stale vitals, problem list drift)
      if self.level >= 2:
        workflow_pool = [e for e in self.WORKFLOW_ERRORS
                         if e.name in ("cc_narrative_mismatch", "ros_contradiction", "problem_list_outdated",
                                        "wrong_counseling", "diagnosis_name_drift")]
        count = min(distribution["workflow"] // 2, len(workflow_pool))
        selected_errors.extend(self.rng.sample(workflow_pool, count))

      if self.level >= 2:
        data_pool = [e for e in self.DATA_INTEGRITY_ERRORS
                     if e.name in ("stale_height", "temp_over_precision", "growth_age_label_off")]
        selected_errors.extend(self.rng.sample(data_pool, min(2, len(data_pool))))

    elif position == "recent":
      # Recent: reconciliation failures (allergies, meds, immunizations)
      if self.level >= 3:
        safety_pool = [e for e in self.SAFETY_CRITICAL_ERRORS
                       if e.name in ("allergy_nkda_contradiction", "allergy_reaction_drift",
                                      "med_stopped_still_active", "duplicate_med_different_sigs")]
        count = min(distribution["safety"], len(safety_pool))
        selected_errors.extend(self.rng.sample(safety_pool, count))

      if self.level >= 2:
        workflow_pool = [e for e in self.WORKFLOW_ERRORS
                         if e.name in ("vaccine_note_no_entry", "referral_not_ordered", "listening_failure")]
        selected_errors.extend(self.rng.sample(workflow_pool, min(3, len(workflow_pool))))

      if self.level >= 4:
        medicolegal_pool = self.MEDICOLEGAL_ERRORS
        selected_errors.extend(self.rng.sample(medicolegal_pool, min(2, len(medicolegal_pool))))

      if self.level >= 3:
        coding_pool = self.CODING_BILLING_ERRORS
        selected_errors.extend(self.rng.sample(coding_pool, min(2, len(coding_pool))))

    return selected_errors

  def inject_text(self, text: str, context: dict[str, Any] | None = None) -> str:
    """
    Inject messiness into text based on level.

    Args:
      text: Original clean text
      context: Optional context (sex, age_months, conditions, etc.)

    Returns:
      Text with appropriate messiness injected
    """
    if self.level == 0 or not text or len(text.strip()) < 20:
      return text

    context = context or {}

    # Level 1: Abbreviations
    if self.level >= 1:
      text = self._inject_abbreviations(text)

    # Level 2: Copy-forward artifacts
    if self.level >= 2:
      text = self._inject_zombie_notes(text, context)

    # Level 4: Dictation errors
    if self.level >= 4:
      text = self._inject_dictation_errors(text)
      text = self._inject_pronoun_errors(text, context)

    # Level 5: ISMP violations in text
    if self.level >= 5:
      text = self._inject_ismp_text_errors(text)

    return text

  def _inject_abbreviations(self, text: str) -> str:
    """Replace some words with medical abbreviations."""
    for word, abbrevs in self.ABBREVIATIONS.items():
      if word.lower() in text.lower() and self.rng.random() < 0.4:
        abbrev = self.rng.choice(abbrevs[:-1]) if len(abbrevs) > 1 else abbrevs[0]
        import re
        text = re.sub(re.escape(word), abbrev, text, count=1, flags=re.IGNORECASE)
    return text

  def _inject_zombie_notes(self, text: str, context: dict) -> str:
    """Insert outdated copy-forward fragments."""
    # Don't inject into very short texts
    if len(text.strip()) < 50:
      return text

    age_months = context.get("age_months", 60)

    fragments_to_add = []

    if age_months > 24 and self.rng.random() < 0.3:
      fragments_to_add.extend(self.rng.sample(
        self.ZOMBIE_FRAGMENTS["infant"],
        min(2, len(self.ZOMBIE_FRAGMENTS["infant"]))
      ))

    if self.rng.random() < 0.4:
      fragments_to_add.extend(self.rng.sample(
        self.ZOMBIE_FRAGMENTS["universal"],
        min(2, len(self.ZOMBIE_FRAGMENTS["universal"]))
      ))

    if fragments_to_add:
      sentences = text.split(". ")
      for frag in fragments_to_add:
        if sentences and self.rng.random() < 0.5:
          pos = self.rng.randint(0, len(sentences))
          sentences.insert(pos, frag.rstrip("."))
        else:
          sentences.append(frag.rstrip("."))
      text = ". ".join(sentences)

    return text

  def _inject_dictation_errors(self, text: str) -> str:
    """Introduce voice-to-text type errors."""
    for correct, wrongs in self.DICTATION_ERRORS.items():
      if correct.lower() in text.lower() and self.rng.random() < 0.3:
        wrong = self.rng.choice(wrongs)
        import re
        text = re.sub(
          re.escape(correct),
          wrong,
          text,
          count=1,
          flags=re.IGNORECASE
        )
    return text

  def _inject_pronoun_errors(self, text: str, context: dict) -> str:
    """Swap pronouns to wrong sex (template copy-paste error)."""
    sex = context.get("sex", "").lower()
    if not sex or self.rng.random() > 0.3:
      return text

    swap_map = self.PRONOUN_SWAPS.get(
      "male_to_female" if sex == "male" else "female_to_male",
      {}
    )

    for correct, wrong in swap_map.items():
      if correct in text and self.rng.random() < 0.3:
        text = text.replace(correct, wrong, 1)
        break

    return text

  def _inject_ismp_text_errors(self, text: str) -> str:
    """Inject dangerous ISMP abbreviation violations."""
    import re

    for safe, dangerous in self.ISMP_VIOLATIONS["trailing_zero"]:
      if safe in text and self.rng.random() < 0.4:
        text = text.replace(safe, dangerous, 1)

    for safe, dangerous in self.ISMP_VIOLATIONS["no_leading_zero"]:
      if safe in text and self.rng.random() < 0.4:
        text = text.replace(safe, dangerous, 1)

    for safe, dangerous in self.ISMP_VIOLATIONS["qd_confusion"]:
      if safe.lower() in text.lower() and self.rng.random() < 0.3:
        text = re.sub(re.escape(safe), dangerous, text, count=1, flags=re.IGNORECASE)

    return text

  def get_wrong_sex_finding(self, sex: str) -> str | None:
    """Get an inappropriate exam finding for the patient's sex."""
    if self.level < 4:
      return None

    findings = self.WRONG_SEX_FINDINGS.get(sex.lower(), [])
    if findings and self.rng.random() < 0.3:
      return self.rng.choice(findings)
    return None

  def should_omit_diagnosis_code(self, condition_name: str) -> bool:
    """Determine if diagnosis code should be omitted (implicit diagnosis)."""
    if self.level < 3:
      return False

    condition_lower = condition_name.lower()
    for pattern_name in self.IMPLICIT_DIAGNOSIS_PATTERNS:
      if pattern_name in condition_lower:
        return self.rng.random() < 0.4

    return self.rng.random() < 0.15

  def get_wrong_diagnosis_code(self, condition_name: str) -> str | None:
    """Get an incorrect/vague diagnosis code instead of the specific one."""
    if self.level < 3:
      return None

    condition_lower = condition_name.lower()
    for pattern_name, pattern_data in self.IMPLICIT_DIAGNOSIS_PATTERNS.items():
      if pattern_name in condition_lower:
        if self.rng.random() < 0.3:
          return self.rng.choice(pattern_data["wrong_codes"])
    return None

  def get_allergy_rx_conflict(self, allergies: list[str]) -> str | None:
    """Return a medication that conflicts with the patient's allergies."""
    if self.level < 5:
      return None

    for allergy in allergies:
      allergy_lower = allergy.lower()
      for allergen, conflicting_meds in self.ALLERGY_RX_CONFLICTS.items():
        if allergen in allergy_lower:
          if self.rng.random() < 0.2:
            return self.rng.choice(conflicting_meds)
    return None

  def inject_vitals_contradiction(self, vitals: dict) -> tuple[dict, str]:
    """
    Potentially introduce contradiction between structured vitals and note text.

    Returns:
      Tuple of (potentially modified vitals, contradicting text or empty string)
    """
    if self.level < 3:
      return vitals, ""

    if self.rng.random() > 0.25:
      return vitals, ""

    if "temperature_f" in vitals and self.rng.random() < 0.5:
      original_temp = vitals.get("temperature_f", 98.6)
      if original_temp < 100:
        return vitals, f"febrile to {self.rng.uniform(101, 104):.1f}"
      else:
        return {**vitals, "temperature_f": 98.6}, "afebrile today"

    if "blood_pressure_systolic" in vitals and self.rng.random() < 0.5:
      bp_s = vitals.get("blood_pressure_systolic", 120)
      if bp_s < 140:
        return vitals, f"hypertensive at {self.rng.randint(150, 180)}/{self.rng.randint(90, 110)}"

    return vitals, ""

  def get_unit_error(self) -> dict | None:
    """Generate a unit conversion error (kg vs lbs, C vs F)."""
    if self.level < 3:
      return None

    if self.rng.random() < 0.2:
      error_type = self.rng.choice(["weight", "temp"])
      if error_type == "weight":
        return {"type": "weight", "error": "kg_as_lbs"}
      else:
        return {"type": "temp", "error": "c_as_f"}
    return None

  def inject_incomplete_sentence(self, text: str) -> str:
    """Randomly truncate a sentence (dictation cut-off)."""
    if self.level < 4:
      return text

    if self.rng.random() > 0.15:
      return text

    sentences = text.split(". ")
    if len(sentences) > 2:
      idx = self.rng.randint(1, len(sentences) - 1)
      words = sentences[idx].split()
      if len(words) > 4:
        cut_point = self.rng.randint(2, len(words) - 2)
        sentences[idx] = " ".join(words[:cut_point])
      return ". ".join(sentences)

    return text

  def add_redundant_text(self, text: str) -> str:
    """Add copy-paste redundancy (same text repeated)."""
    if self.level < 2:
      return text

    if self.rng.random() > 0.2:
      return text

    sentences = text.split(". ")
    if len(sentences) > 3:
      idx = self.rng.randint(0, len(sentences) - 1)
      sentences.insert(idx + 1, sentences[idx])
      return ". ".join(sentences)

    return text

  def get_threading_stage_content(self, visit_index: int) -> str | None:
    """
    Get content for a threading error at a specific visit.

    Args:
      visit_index: Which visit in the patient's history (0-indexed)

    Returns:
      Threading error content for this visit, or None
    """
    threading = self.select_threading_error()
    if not threading:
      return None

    # Map visit index to threading stage
    if visit_index < len(threading.stages):
      return threading.stages[visit_index].get("content")
    return None
