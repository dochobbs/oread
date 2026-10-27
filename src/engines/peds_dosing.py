"""Pediatric maintenance-medication regimens, dosed by weight and age.

Every number here carries its source. If a drug isn't in this module, the
engine does not invent a dose for it.

Each regimen function takes the child's age (completed calendar months) and
weight (kg) on the prescribing date and returns a list of `Rx` dicts. Doses
are rounded to a strength that exists (tablet, chewable, or a measurable
volume of the oral solution) and never exceed the adult maximum.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"


@dataclass
class Rx:
  """A prescription the engine turns into a Medication."""
  agent: str
  rxnorm: str
  dose_quantity: str
  dose_unit: str
  frequency: str
  route: str = "oral"
  prn: bool = False
  prn_reason: str | None = None
  instructions: str | None = None
  indication: str | None = None
  drug_class: str | None = None
  start_offset_days: int = 0
  duration_days: int | None = None
  extra: dict = field(default_factory=dict)


def _fmt(x: float) -> str:
  """Format a dose without trailing zeros (2.5, 10, 0.15)."""
  return f"{x:g}"


def weight_on(growth_data, on: date) -> float | None:
  """Most recent measured weight on or before `on`; else the earliest after."""
  before = [g for g in growth_data if g.weight_kg and g.date <= on]
  if before:
    return max(before, key=lambda g: g.date).weight_kg
  after = [g for g in growth_data if g.weight_kg]
  return min(after, key=lambda g: g.date).weight_kg if after else None


# --------------------------------------------------------------------------- #
# Levetiracetam                                                               #
# --------------------------------------------------------------------------- #
# Source: KEPPRA (levetiracetam) FDA label, Dosage & Administration 2.2,
# partial-onset seizures (rev. 2023):
#   1 mo to <6 mo : start 7 mg/kg BID, max 21 mg/kg BID (42 mg/kg/day)
#   6 mo to <4 y  : start 10 mg/kg BID, max 25 mg/kg BID (50 mg/kg/day)
#   4 y to <16 y  : start 10 mg/kg BID, max 30 mg/kg BID (60 mg/kg/day)
#   >=16 y        : start 500 mg BID, max 1500 mg BID (3000 mg/day)
# Strengths: oral solution 100 mg/mL; tablets 250, 500, 750, 1000 mg.
LEV_START_MG_KG_DOSE = [(1, 7.0), (6, 10.0), (48, 10.0)]
LEV_MAX_MG_KG_DAY = [(1, 42.0), (6, 50.0), (48, 60.0)]
LEV_ADULT_START_MG = 500
LEV_ADULT_MAX_MG_DOSE = 1500
LEV_SOLUTION_MG_PER_ML = 100
LEV_TABLET_MG = 250
# Tablets once a child can reliably swallow them; school age is the usual
# point (clinical convention, not a label requirement).
TABLET_MIN_AGE_MONTHS = 72


def _band(table: list[tuple[int, float]], age_months: int) -> float | None:
  val = None
  for min_age, v in table:
    if age_months >= min_age:
      val = v
  return val


def levetiracetam(age_months: int, weight_kg: float) -> list[Rx]:
  if age_months < 1:
    return []  # Not labeled < 1 month
  if age_months >= 192:
    per_dose = LEV_ADULT_START_MG
  else:
    per_dose = _band(LEV_START_MG_KG_DOSE, age_months) * weight_kg
    cap = _band(LEV_MAX_MG_KG_DAY, age_months) * weight_kg / 2
    per_dose = min(per_dose, cap, LEV_ADULT_MAX_MG_DOSE)
  if age_months >= TABLET_MIN_AGE_MONTHS and per_dose >= LEV_TABLET_MG:
    mg = math.floor(per_dose / LEV_TABLET_MG) * LEV_TABLET_MG
    instructions = f"{mg // LEV_TABLET_MG} x {LEV_TABLET_MG} mg tablet"
  else:
    # Oral solution measured to 0.1 mL = 10 mg; round down so we never
    # exceed the mg/kg ceiling.
    mg = max(10, math.floor(per_dose / 10) * 10)
    instructions = f"{mg / LEV_SOLUTION_MG_PER_ML:g} mL of 100 mg/mL oral solution"
  return [Rx("Levetiracetam", "310422", _fmt(mg), "mg", "BID",
             instructions=instructions, drug_class="antiseizure")]


# --------------------------------------------------------------------------- #
# Cetirizine                                                                  #
# --------------------------------------------------------------------------- #
# Source: ZYRTEC (cetirizine) FDA label: 6-23 mo 2.5 mg once daily;
# 2-5 y 2.5 mg once daily, may increase to 5 mg/day; >=6 y 5-10 mg once daily.
# Not labeled < 6 months. Strengths: 1 mg/mL syrup; 5 and 10 mg tablets.
# Start at the label's starting dose for each band.
CETIRIZINE_BY_AGE = [(6, 2.5), (72, 10.0)]


def cetirizine(age_months: int) -> list[Rx]:
  mg = _band(CETIRIZINE_BY_AGE, age_months)
  if mg is None:
    return []
  instr = f"{mg:g} mL of 1 mg/mL syrup" if mg < 5 else f"{mg:g} mg tablet"
  return [Rx("Cetirizine", "20610", _fmt(mg), "mg", "daily",
             instructions=instr, drug_class="H1 antihistamine")]


# --------------------------------------------------------------------------- #
# Methylphenidate (ADHD)                                                      #
# --------------------------------------------------------------------------- #
# Source: AAP Clinical Practice Guideline for ADHD (Wolraich et al.,
# Pediatrics 2019;144:e20192528). KAS 5a: ages 4-5, parent training in
# behavior management (PTBM) first; methylphenidate only if behavioral
# interventions do not give significant improvement.
PRESCHOOL_MAX_AGE_MONTHS = 72
# Behavioral trial before a preschooler's first stimulant. The CPG gives no
# fixed length; PATS required a 10-week parent-training course first
# (Greenhill et al., JAACAP 2006;45:1284). 6 months is a conservative
# synthetic default.
PRESCHOOL_BEHAVIORAL_TRIAL_DAYS = 182
# Preschool start: PATS titrated IR methylphenidate 1.25-7.5 mg TID; the CPG
# advises lower doses and slower titration in preschoolers. Start 2.5 mg BID
# (5 mg/day), within PATS's range.
PRESCHOOL_MPH_IR_MG = 2.5
# School age: CONCERTA (methylphenidate ER) FDA label, 6-17 y, start 18 mg
# once daily; max 54 mg/day (6-12 y) or 72 mg/day (13-17 y, <=2 mg/kg/day).
MPH_ER_START_MG = 18


def methylphenidate(age_months: int) -> list[Rx]:
  if age_months < 48:
    return []  # No ADHD pharmacotherapy below 4 y (AAP 2019 scope is 4-18 y)
  if age_months < PRESCHOOL_MAX_AGE_MONTHS:
    return [Rx("Methylphenidate IR", "6901", _fmt(PRESCHOOL_MPH_IR_MG), "mg", "BID",
               instructions="2.5 mg tablet (half of 5 mg) in AM and early afternoon",
               drug_class="stimulant")]
  return [Rx("Methylphenidate ER", "866924", _fmt(MPH_ER_START_MG), "mg", "daily in AM",
             drug_class="stimulant")]


# --------------------------------------------------------------------------- #
# SSRIs (anxiety, depression) - one agent covers both                         #
# --------------------------------------------------------------------------- #
# Fluoxetine: PROZAC FDA label, pediatric MDD 8-18 y, start 10 mg/day
# (lower-weight children may stay at 10 mg); max 20 mg/day for pediatric MDD.
FLUOXETINE_MIN_AGE_MONTHS = 96
FLUOXETINE_START_MG = 10
# Sertraline: ZOLOFT FDA label, pediatric OCD: 6-12 y start 25 mg/day,
# 13-17 y start 50 mg/day; max 200 mg/day. AACAP Clinical Practice Guideline
# for Anxiety Disorders (Walter et al., JAACAP 2020) recommends SSRIs for
# ages 6-18 (CAMS trial used sertraline, ages 7-17).
SERTRALINE_MIN_AGE_MONTHS = 72
SERTRALINE_START_BY_AGE = [(72, 25), (156, 50)]


def ssri(age_months: int, *, depression: bool, anxiety: bool) -> list[Rx]:
  """One SSRI for anxiety and/or depression (never two concurrently)."""
  ind = " and ".join(x for x, on in (("depression", depression), ("anxiety", anxiety)) if on)
  if depression and age_months >= FLUOXETINE_MIN_AGE_MONTHS:
    return [Rx("Fluoxetine", "310385", _fmt(FLUOXETINE_START_MG), "mg", "daily",
               indication=ind, drug_class="SSRI")]
  if anxiety and age_months >= SERTRALINE_MIN_AGE_MONTHS:
    mg = _band(SERTRALINE_START_BY_AGE, age_months)
    return [Rx("Sertraline", "312938", _fmt(mg), "mg", "daily",
               indication=ind, drug_class="SSRI")]
  return []  # Too young for a labeled/guideline SSRI: therapy only


# --------------------------------------------------------------------------- #
# Type 1 diabetes: basal-bolus MDI                                            #
# --------------------------------------------------------------------------- #
# Source: ISPAD Clinical Practice Consensus Guidelines 2022, Ch. 9 "Insulin
# treatment in children and adolescents with diabetes" (Cengiz et al.,
# Pediatr Diabetes 2022): total daily dose (TDD) after partial remission
# ~0.7-1.0 U/kg/day prepubertal, higher (1.2-2) in puberty; basal insulin
# 30-50% of TDD on MDI; carbohydrate ratio by the "500 rule" (500/TDD g/U)
# and insulin sensitivity by the "1800 rule" (1800/TDD mg/dL per U).
# ADA Standards of Care in Diabetes 2026, Section 14: MDI or pump for all
# children with T1D; glucagon prescribed for everyone on insulin.
T1D_TDD_U_KG_PREPUBERTAL = 0.7
T1D_TDD_U_KG_PUBERTAL = 1.0  # low end of ISPAD's pubertal range at start
PUBERTY_AGE_MONTHS = 132  # ~11 y; synthetic proxy for pubertal onset
T1D_BASAL_FRACTION = 0.4
T1D_CORRECTION_TARGET_MG_DL = 150
# Glucagon: Lilly Glucagon Emergency Kit FDA label: 0.5 mg for children
# < 20 kg, 1 mg for >= 20 kg (IM/SC).
GLUCAGON_WEIGHT_CUTOFF_KG = 20


def type_1_diabetes(age_months: int, weight_kg: float) -> list[Rx]:
  per_kg = T1D_TDD_U_KG_PUBERTAL if age_months >= PUBERTY_AGE_MONTHS else T1D_TDD_U_KG_PREPUBERTAL
  tdd = per_kg * weight_kg
  basal = max(1, round(tdd * T1D_BASAL_FRACTION))
  icr = max(5, round(500 / tdd))
  isf = max(10, int(5 * round((1800 / tdd) / 5)))
  glucagon_mg = 0.5 if weight_kg < GLUCAGON_WEIGHT_CUTOFF_KG else 1.0
  return [
    Rx("Insulin Glargine", "261551", str(basal), "units", "once daily at bedtime",
       route="subcutaneous", drug_class="basal insulin",
       instructions=f"{basal} units SC nightly (~{T1D_BASAL_FRACTION:.0%} of TDD "
                    f"{tdd:.0f} U/day at {weight_kg:.1f} kg)"),
    Rx("Insulin Lispro", "86009", f"1 unit per {icr} g carbohydrate", "units",
       "with meals", route="subcutaneous", drug_class="rapid-acting insulin",
       instructions=f"Carb ratio 1:{icr} g; correction 1 unit per {isf} mg/dL "
                    f"above {T1D_CORRECTION_TARGET_MG_DL} mg/dL"),
    Rx("Glucagon Emergency Kit", "4832", _fmt(glucagon_mg), "mg", "once PRN",
       route="intramuscular", prn=True, prn_reason="severe hypoglycemia", drug_class="glucagon",
       instructions="For severe hypoglycemia (unable to take oral glucose); call 911"),
  ]


# --------------------------------------------------------------------------- #
# Type 2 diabetes                                                             #
# --------------------------------------------------------------------------- #
# Source: AAP Clinical Practice Guideline, Management of Newly Diagnosed T2DM
# in Children and Adolescents (Copeland et al., Pediatrics 2013;131:364)
# KAS 2: metformin first-line; start 500 mg daily, increase by 500 mg every
# 1-2 weeks to 1000 mg BID (2000 mg/day max, GLUCOPHAGE label 10-16 y).
METFORMIN_START_MG = 500


def type_2_diabetes(age_months: int) -> list[Rx]:
  if age_months < 120:
    return []  # Metformin labeled >= 10 y
  return [Rx("Metformin", "6809", _fmt(METFORMIN_START_MG), "mg", "daily with evening meal",
             drug_class="biguanide",
             instructions="Titrate by 500 mg every 1-2 weeks to 1000 mg BID as tolerated")]


# --------------------------------------------------------------------------- #
# Food allergy: epinephrine auto-injector                                     #
# --------------------------------------------------------------------------- #
# Source: AAP Clinical Report, Epinephrine for First-aid Management of
# Anaphylaxis (Sicherer & Simons, Pediatrics 2017;139:e20164006) and device
# labels: AUVI-Q 0.1 mg for 7.5-15 kg; EpiPen Jr / 0.15 mg for 15-30 kg;
# EpiPen / 0.3 mg for >= 30 kg. Below 7.5 kg the 0.1 mg device is the
# smallest auto-injector (the AAP report discusses this trade-off).
EPI_BANDS_KG = [(0.0, 0.1), (15.0, 0.15), (30.0, 0.3)]


def epinephrine(weight_kg: float) -> list[Rx]:
  mg = 0.1
  for min_kg, dose in EPI_BANDS_KG:
    if weight_kg >= min_kg:
      mg = dose
  return [Rx("Epinephrine Auto-Injector", "3992", _fmt(mg), "mg", "once PRN",
             route="intramuscular", prn=True, prn_reason="anaphylaxis", drug_class="epinephrine",
             instructions="Inject into anterolateral thigh for anaphylaxis; may repeat "
                          "in 5-15 min; call 911. Dispense 2 devices.")]


# --------------------------------------------------------------------------- #
# Asthma (stepwise)                                                           #
# --------------------------------------------------------------------------- #
# Sources: NAEPP Expert Panel Report 4, 2020 Focused Updates to the Asthma
# Management Guidelines (EPR-4), and EPR-3 (2007) dose tables; GINA 2024.
#   Intermittent (step 1, all ages): SABA PRN.
#   Mild persistent (step 2): daily low-dose ICS + SABA PRN.
#   Moderate persistent (step 3):
#     0-4 y: daily medium-dose ICS + SABA PRN
#     5-11 y and >=12 y: SMART, ICS-formoterol daily and PRN (EPR-4
#     recommendation 7 and 8); no separate SABA.
# Low/medium ICS doses (EPR-3, Fig. 4-4b/4-8b):
#   0-4 y budesonide nebulized low 0.25-0.5 mg/day, medium >0.5-1.0 mg/day
#     (PULMICORT RESPULES label 12 mo-8 y).
#   5-11 y and >=12 y fluticasone HFA low 88-176 (5-11) / 88-264 (>=12) mcg/day
#     (FLOVENT HFA labeled >= 4 y).
# SMART max: 8 puffs/day 5-11 y, 12 puffs/day >=12 y (EPR-4).
ICD10_ASTHMA = {
  "intermittent": ("J45.20", "Mild intermittent asthma, uncomplicated"),
  "mild_persistent": ("J45.30", "Mild persistent asthma, uncomplicated"),
  "moderate_persistent": ("J45.40", "Moderate persistent asthma, uncomplicated"),
}
FLOVENT_MIN_AGE_MONTHS = 48
SMART_MIN_AGE_MONTHS = 60


def _albuterol() -> Rx:
  return Rx("Albuterol HFA Inhaler", "351137", "2", "puffs", "Q4H PRN",
            route="inhaled", prn=True, prn_reason="cough, wheeze or shortness of breath",
            drug_class="SABA",
            instructions="90 mcg/puff with spacer; 2 puffs every 4 hours as needed")


def asthma(age_months: int, severity: str) -> list[Rx]:
  if severity == "intermittent":
    return [_albuterol()]
  if severity == "moderate_persistent" and age_months >= SMART_MIN_AGE_MONTHS:
    if age_months >= 144:
      strength, puffs, max_puffs = "160/4.5", "2", 12
    else:
      strength, puffs, max_puffs = "80/4.5", "1", 8
    # RxNorm: ingredient-level budesonide code; combination SCD unverified.
    return [Rx(f"Budesonide-Formoterol HFA {strength} mcg", "19831", puffs, "puffs",
               "BID and PRN", route="inhaled", drug_class="ICS-LABA",
               instructions=f"SMART: {puffs} puff(s) twice daily plus 1 puff as needed "
                            f"for symptoms; max {max_puffs} puffs/day")]
  medium = severity == "moderate_persistent"
  if age_months < FLOVENT_MIN_AGE_MONTHS:
    mg = "0.5" if medium else "0.25"
    ics = Rx("Budesonide Inhalation Suspension", "19831", mg, "mg", "BID via nebulizer",
             route="inhaled", drug_class="ICS")
  else:
    ics = Rx("Fluticasone Propionate HFA 44 mcg", "966524", "2", "puffs", "BID",
             route="inhaled", drug_class="ICS",
             instructions="176 mcg/day (low dose) with spacer; rinse mouth after use")
  return [ics, _albuterol()]


# --------------------------------------------------------------------------- #
# Kawasaki disease                                                            #
# --------------------------------------------------------------------------- #
# Source: AHA Scientific Statement, Diagnosis, Treatment, and Long-Term
# Management of Kawasaki Disease (McCrindle et al., Circulation 2017;135:e927):
# low-dose aspirin 3-5 mg/kg/day once daily, continued until no evidence of
# coronary changes at 4-6 weeks after onset. Strength: 81 mg chewable
# (half tablet = 40.5 mg).
KAWASAKI_ASPIRIN_MG_KG_DAY_MAX = 5
KAWASAKI_ASPIRIN_DAYS = 42  # 6 weeks, no coronary findings


def kawasaki_aspirin(weight_kg: float) -> list[Rx]:
  mg = 81 if weight_kg * KAWASAKI_ASPIRIN_MG_KG_DAY_MAX >= 81 else 40.5
  return [Rx("Aspirin", "1191", _fmt(mg), "mg", "daily", drug_class="antiplatelet",
             duration_days=KAWASAKI_ASPIRIN_DAYS,
             instructions="Low-dose aspirin; stop at 6 weeks if echo shows no coronary changes")]


def end_date_for(rx: Rx, start: date) -> date | None:
  return start + timedelta(days=rx.duration_days) if rx.duration_days else None
