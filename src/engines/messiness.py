"""
Chart Messiness Injection System

Introduces realistic EHR artifacts and errors to synthetic patient data
for training robust ML systems.

Messiness Levels:
  0 - Pristine: Clean, well-formatted data
  1 - Light: Abbreviations, medical jargon, shorthand
  2 - Moderate: Copy-forward artifacts, zombie notes, outdated info
  3 - Heavy: Missing codes, structural contradictions, implicit diagnoses
  4 - Severe: Dictation errors, homophones, pronoun mismatches
  5 - Hostile: ISMP violations, dangerous abbreviations, safety hazards
"""

import random
from typing import Any


class MessinessInjector:
  """Injects realistic chart messiness into synthetic patient data."""

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
    # Medical homophones with clinical significance
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
    # Drug name confusions
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

  def inject_text(self, text: str, context: dict[str, Any] | None = None) -> str:
    """
    Inject messiness into text based on level.

    Args:
      text: Original clean text
      context: Optional context (sex, age_months, conditions, etc.)

    Returns:
      Text with appropriate messiness injected
    """
    if self.level == 0:
      return text

    context = context or {}

    # Level 1: Abbreviations
    if self.level >= 1:
      text = self._inject_abbreviations(text)

    # Level 2: Copy-forward artifacts
    if self.level >= 2:
      text = self._inject_zombie_notes(text, context)

    # Level 3: Structural issues handled at data level, not text
    # (see inject_structured_data)

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
        # Pick a random abbreviation (not the full word)
        abbrev = self.rng.choice(abbrevs[:-1]) if len(abbrevs) > 1 else abbrevs[0]
        # Case-insensitive replace (first occurrence only for variety)
        import re
        text = re.sub(re.escape(word), abbrev, text, count=1, flags=re.IGNORECASE)
    return text

  def _inject_zombie_notes(self, text: str, context: dict) -> str:
    """Insert outdated copy-forward fragments."""
    age_months = context.get("age_months", 60)

    # Pick inappropriate fragments based on current age
    fragments_to_add = []

    if age_months > 24 and self.rng.random() < 0.3:
      # Add infant-specific text to older child
      fragments_to_add.extend(self.rng.sample(
        self.ZOMBIE_FRAGMENTS["infant"],
        min(2, len(self.ZOMBIE_FRAGMENTS["infant"]))
      ))

    if self.rng.random() < 0.4:
      # Add universal zombie fragments
      fragments_to_add.extend(self.rng.sample(
        self.ZOMBIE_FRAGMENTS["universal"],
        min(2, len(self.ZOMBIE_FRAGMENTS["universal"]))
      ))

    if fragments_to_add:
      # Insert at random position or end
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

    # Swap to opposite sex
    swap_map = self.PRONOUN_SWAPS.get(
      "male_to_female" if sex == "male" else "female_to_male",
      {}
    )

    for correct, wrong in swap_map.items():
      if correct in text and self.rng.random() < 0.3:
        text = text.replace(correct, wrong, 1)
        break  # Only one swap per text block

    return text

  def _inject_ismp_text_errors(self, text: str) -> str:
    """Inject dangerous ISMP abbreviation violations."""
    import re

    # Trailing zeros
    for safe, dangerous in self.ISMP_VIOLATIONS["trailing_zero"]:
      if safe in text and self.rng.random() < 0.4:
        text = text.replace(safe, dangerous, 1)

    # Missing leading zeros
    for safe, dangerous in self.ISMP_VIOLATIONS["no_leading_zero"]:
      if safe in text and self.rng.random() < 0.4:
        text = text.replace(safe, dangerous, 1)

    # QD/QOD confusion
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

    # Check if this is a condition that's commonly treated without coding
    condition_lower = condition_name.lower()
    for pattern_name in self.IMPLICIT_DIAGNOSIS_PATTERNS:
      if pattern_name in condition_lower:
        return self.rng.random() < 0.4  # 40% chance of missing code

    return self.rng.random() < 0.15  # 15% baseline for any condition

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
          if self.rng.random() < 0.2:  # 20% chance
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

    if self.rng.random() > 0.25:  # 25% chance
      return vitals, ""

    # Pick a contradiction type
    if "temperature_f" in vitals and self.rng.random() < 0.5:
      # Structured says normal, note says febrile
      original_temp = vitals.get("temperature_f", 98.6)
      if original_temp < 100:
        return vitals, f"febrile to {self.rng.uniform(101, 104):.1f}"
      else:
        # Or vice versa
        return {**vitals, "temperature_f": 98.6}, f"afebrile today"

    if "blood_pressure_systolic" in vitals and self.rng.random() < 0.5:
      bp_s = vitals.get("blood_pressure_systolic", 120)
      bp_d = vitals.get("blood_pressure_diastolic", 80)
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
        return {"type": "weight", "error": "kg_as_lbs"}  # Treating kg as lbs
      else:
        return {"type": "temp", "error": "c_as_f"}  # Celsius value in Fahrenheit field
    return None

  def inject_incomplete_sentence(self, text: str) -> str:
    """Randomly truncate a sentence (dictation cut-off)."""
    if self.level < 4:
      return text

    if self.rng.random() > 0.15:  # 15% chance
      return text

    sentences = text.split(". ")
    if len(sentences) > 2:
      idx = self.rng.randint(1, len(sentences) - 1)
      words = sentences[idx].split()
      if len(words) > 4:
        # Cut off mid-sentence
        cut_point = self.rng.randint(2, len(words) - 2)
        sentences[idx] = " ".join(words[:cut_point])
      return ". ".join(sentences)

    return text

  def add_redundant_text(self, text: str) -> str:
    """Add copy-paste redundancy (same text repeated)."""
    if self.level < 2:
      return text

    if self.rng.random() > 0.2:  # 20% chance
      return text

    sentences = text.split(". ")
    if len(sentences) > 3:
      # Repeat a sentence
      idx = self.rng.randint(0, len(sentences) - 1)
      sentences.insert(idx + 1, sentences[idx])
      return ". ".join(sentences)

    return text
