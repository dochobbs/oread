"""
Due-dose logic for the pediatric immunization schedule.

Given a child's date of birth, a visit date and the doses already given, work
out which doses are due *as of that visit*. This replaces nearest-age matching
against a fixed table: every decision checks the dose number already reached,
the ACIP/AAP minimum age, the minimum interval since the prior dose, and any
maximum age.

Clinical source for every rule below unless noted:
  AAP. Recommended Child and Adolescent Immunization Schedule for Ages 18 Years
  or Younger, United States, 2026 (updated September 2, 2026). Table 1 (routine
  ages), Table 2 (catch-up minimum ages and intervals) and Notes.
  https://www.aap.org/ImmunizationSchedule ; policy statement Pediatrics
  2026;157(3):e2025075754.
Cited below as "AAP 2026 T1", "AAP 2026 T2" and "AAP 2026 Notes/<vaccine>".

The module is pure (no engine state, no randomness) so the rules can be unit
tested directly. The engine decides products, refusals and record keeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Iterable

from dateutil.relativedelta import relativedelta


# --------------------------------------------------------------------------- #
# Time arithmetic                                                             #
# --------------------------------------------------------------------------- #

# AAP 2026 Notes "Additional information": doses given <=4 days before the
# minimum age or interval are valid; 4 weeks = 28 days; intervals of >=4
# months are calendar months.
GRACE_DAYS = 4

# Engine scheduling tolerance, NOT a clinical threshold. Well visits are
# generated up to 7 days before their nominal age (engine
# generate_encounter_timeline jitter), so a dose whose recommended age is
# "6 months" is treated as at its recommended age when the visit falls within
# two weeks before it. Validity is always enforced separately by the ACIP/AAP
# minimum age and interval checks.
VISIT_WINDOW_DAYS = 14


def weeks(n: int) -> relativedelta:
  return relativedelta(days=7 * n)


def months(n: int) -> relativedelta:
  return relativedelta(months=n)


def years(n: int) -> relativedelta:
  return relativedelta(years=n)


@dataclass(frozen=True)
class Ctx:
  """Context for one due-dose evaluation."""
  dob: date
  visit: date

  def age_at(self, d: date) -> relativedelta:
    return relativedelta(d, self.dob)

  def age_lt(self, d: date, span: relativedelta) -> bool:
    """True if age on date d is strictly less than span."""
    return d < self.dob + span

  def age_ge(self, d: date, span: relativedelta) -> bool:
    return d >= self.dob + span

  def given_at_or_after(self, d: date, span: relativedelta) -> bool:
    """A prior dose on date d counts as given at >= span of age.

    Uses the 4-day grace period, as for any validity decision about a dose
    already given (AAP 2026 Notes "Additional information").
    """
    return d >= self.dob + span - timedelta(days=GRACE_DAYS)

  @staticmethod
  def spaced(later: date, earlier: date, span: relativedelta) -> bool:
    """Two prior doses are at least `span` apart (4-day grace)."""
    return later >= earlier + span - timedelta(days=GRACE_DAYS)

  def min_age_ok(self, span: relativedelta) -> bool:
    """Visit is at or after a minimum age (4-day grace applies)."""
    return self.visit >= self.dob + span - timedelta(days=GRACE_DAYS)

  def interval_ok(self, prior: date, span: relativedelta) -> bool:
    """Visit is at least `span` after a prior dose (4-day grace applies)."""
    return self.visit >= prior + span - timedelta(days=GRACE_DAYS)

  def routine_ok(self, routine_months: float) -> bool:
    """Visit is at (or within the visit window before) the routine age."""
    whole = int(routine_months)
    target = self.dob + months(whole)
    return self.visit >= target - timedelta(days=VISIT_WINDOW_DAYS)


# --------------------------------------------------------------------------- #
# Per-antigen rules                                                           #
# --------------------------------------------------------------------------- #
#
# Each rule takes (ctx, prior dose dates sorted ascending) and returns the
# dose number due at this visit, or None. A rule never returns a number <=
# len(prior), so dose numbers strictly increase by construction.

Rule = Callable[[Ctx, list[date]], "int | None"]


def hepb(c: Ctx, d: list[date]) -> int | None:
  """HepB 3-dose series at 0, 1-2, 6-18 months.

  AAP 2026 Notes/HepB: birth dose within 24h; final dose age 6-18 mo,
  minimum age 24 weeks. AAP 2026 T2: dose 1->2 4 weeks; dose 2->3 8 weeks
  and at least 16 weeks after dose 1.
  """
  n = len(d)
  if c.age_ge(c.visit, years(19)):
    return None
  if n == 0:
    return 1
  if n == 1:
    if c.routine_ok(1) and c.interval_ok(d[0], weeks(4)):
      return 2
    return None
  if n == 2:
    if (c.routine_ok(6) and c.min_age_ok(weeks(24))
        and c.interval_ok(d[1], weeks(8)) and c.interval_ok(d[0], weeks(16))):
      return 3
  return None


# Rotavirus maximum ages. AAP 2026 T2 + Notes/Rotavirus: maximum age for
# first dose 14 weeks 6 days ("do not start the series on or after age
# 15 weeks, 0 days"); maximum age for final dose 8 months 0 days.
RV_FIRST_DOSE_MAX = weeks(14) + relativedelta(days=6)
RV_ANY_DOSE_MAX = months(8)
RV5_DOSES = 3  # AAP 2026 Notes/Rotavirus: RotaTeq (RV5) 3-dose series 2, 4, 6 mo


def rotavirus(c: Ctx, d: list[date]) -> int | None:
  """RV5 (RotaTeq) at 2, 4, 6 months. Minimum age 6 weeks, interval 4 weeks."""
  n = len(d)
  if n >= RV5_DOSES or c.visit > c.dob + RV_ANY_DOSE_MAX:
    return None
  if n == 0:
    # No grace on maximum ages: a maximum is a hard stop.
    if c.visit > c.dob + RV_FIRST_DOSE_MAX:
      return None
    return 1 if (c.routine_ok(2) and c.min_age_ok(weeks(6))) else None
  routine_age = (2, 4, 6)[n]
  if c.routine_ok(routine_age) and c.interval_ok(d[-1], weeks(4)):
    return n + 1
  return None


def dtap(c: Ctx, d: list[date]) -> int | None:
  """DTaP 5-dose series at 2, 4, 6, 15-18 months, 4-6 years (age < 7 years).

  AAP 2026 Notes/DTaP: minimum age 6 weeks; dose 4 may be given as early as
  12 months if 6 months have elapsed since dose 3; dose 5 not necessary if
  dose 4 was given at >= 4 years and >= 6 months after dose 3.
  AAP 2026 T2: intervals 4 weeks, 4 weeks, 6 months, 6 months.
  Children >= 7 years who are behind are handled by `tdap`.
  """
  n = len(d)
  if not c.age_lt(c.visit, years(7)):
    return None
  if n == 0:
    return 1 if (c.routine_ok(2) and c.min_age_ok(weeks(6))) else None
  if n in (1, 2):
    routine_age = (None, 4, 6)[n]
    return n + 1 if (c.routine_ok(routine_age) and c.interval_ok(d[-1], weeks(4))) else None
  if n == 3:
    if c.routine_ok(15) and c.min_age_ok(months(12)) and c.interval_ok(d[2], months(6)):
      return 4
    return None
  if n == 4:
    if c.given_at_or_after(d[3], years(4)) and c.spaced(d[3], d[2], months(6)):
      return None  # dose 5 not necessary
    # Minimum age 4 years for dose 5 (DTaP-IPV products are licensed >= 4y;
    # AAP 2026 Notes/DTaP "4 years for Kinrix or Quadracel").
    if c.routine_ok(48) and c.min_age_ok(years(4)) and c.interval_ok(d[3], months(6)):
      return 5
  return None


def dtap_complete(c: Ctx, d: list[date]) -> bool:
  """'Fully vaccinated' with DTaP per AAP 2026 Notes/Tdap footnote: 5 valid
  doses, or 4 if dose 4 was given at age >= 4 years."""
  if len(d) >= 5:
    return True
  return len(d) == 4 and c.given_at_or_after(d[3], years(4))


def tdap(c: Ctx, dtap_doses: list[date], tdap_doses: list[date]) -> int | None:
  """Tdap: adolescent booster at 11-12 years, and catch-up for age 7-18.

  AAP 2026 Notes/Tdap: routine 1 dose at 11-12 years (minimum age 11 years
  routine, 7 years catch-up). Age 7-18 not fully vaccinated with DTaP: 1 dose
  Tdap as part of the catch-up series (if more doses are needed, Td or Tdap;
  this engine uses Tdap). Tdap at age 7-9 still needs the adolescent dose at
  11-12; Tdap at age 10 does not. AAP 2026 T2 (7-18 years): dose 1->2
  4 weeks; dose 2->3 4 weeks if first DTaP/DT dose before the 1st birthday,
  otherwise 6 months (final); dose 3->4 6 months if first dose before the
  1st birthday.
  Returns the Tdap dose number (count of Tdap doses + 1) or None.
  """
  if c.age_lt(c.visit, years(7)) or c.age_ge(c.visit, years(19)):
    return None
  n_tdap = len(tdap_doses)
  if not dtap_complete(c, dtap_doses):
    tet = sorted(dtap_doses + tdap_doses)
    first_before_1y = bool(tet) and c.age_lt(tet[0], years(1))
    needed = 4 if first_before_1y else 3
    if len(tet) < needed:
      if not tet:
        return n_tdap + 1
      k = len(tet)  # next tetanus-containing dose is k + 1
      if k == 1:
        gap = weeks(4)
      elif k == 2:
        gap = weeks(4) if first_before_1y else months(6)
      else:
        gap = months(6)
      return n_tdap + 1 if c.interval_ok(tet[-1], gap) else None
  # Adolescent booster: needed unless a Tdap was given at age >= 10 years.
  had_adolescent = any(c.given_at_or_after(t, years(10)) for t in tdap_doses)
  if not had_adolescent and c.routine_ok(132) and c.min_age_ok(years(11)):
    return n_tdap + 1
  return None


def ipv(c: Ctx, d: list[date]) -> int | None:
  """IPV at 2, 4, 6-18 months, 4-6 years.

  AAP 2026 Notes/Polio: final dose on or after age 4 years and at least
  6 months after the previous dose; a 4th dose is not necessary if dose 3 was
  given at >= 4 years and >= 6 months after dose 2. Not routinely recommended
  for US residents >= 18 years. AAP 2026 T2: intervals 4 weeks, 4 weeks,
  6 months (minimum age 4 years for final dose).
  """
  n = len(d)
  if c.age_ge(c.visit, years(18)):
    return None
  if n == 0:
    return 1 if (c.routine_ok(2) and c.min_age_ok(weeks(6))) else None
  if n in (1, 2):
    routine_age = (None, 4, 6)[n]
    return n + 1 if (c.routine_ok(routine_age) and c.interval_ok(d[-1], weeks(4))) else None
  # n >= 3: complete once the latest dose is >= 4y and >= 6 months after the prior.
  if c.given_at_or_after(d[-1], years(4)) and c.spaced(d[-1], d[-2], months(6)):
    return None
  if c.routine_ok(48) and c.min_age_ok(years(4)) and c.interval_ok(d[-1], months(6)):
    return n + 1
  return None


def hib(c: Ctx, d: list[date]) -> int | None:
  """Hib, PRP-T product (ActHIB): 2, 4, 6 months + booster at 12-15 months.

  AAP 2026 T2 and Notes/Hib (catch-up):
  - Not needed for healthy children >= 60 months.
  - Any dose at >= 15 months: no further doses. Unvaccinated 15-59 months:
    1 dose.
  - Dose 1->2: 4 weeks if dose 1 before 1st birthday; 8 weeks (final) if
    dose 1 at 12-14 months.
  - Dose 2->3: 4 weeks if current age < 12 months and dose 1 < 7 months;
    8 weeks and age 12-59 months (final) if current age < 12 months and dose 1
    at 7-11 months, OR current age 12-59 months, dose 1 before the 1st
    birthday and dose 2 before 15 months.
  - Dose 3->4: 8 weeks (final), only for age 12-59 months with 3 doses before
    the 1st birthday.
  """
  n = len(d)
  if c.age_ge(c.visit, months(60)):
    return None
  if any(c.given_at_or_after(x, months(15)) for x in d):
    return None
  if n == 0:
    if c.age_ge(c.visit, months(15)):
      return 1
    return 1 if (c.routine_ok(2) and c.min_age_ok(weeks(6))) else None
  if n == 1:
    if c.age_lt(d[0], months(12)):
      return 2 if (c.routine_ok(4) and c.interval_ok(d[0], weeks(4))) else None
    return 2 if c.interval_ok(d[0], weeks(8)) else None  # dose 1 at 12-14 mo
  if n == 2:
    now_lt_12 = c.age_lt(c.visit, months(12))
    if now_lt_12 and c.age_lt(d[0], months(7)):
      return 3 if (c.routine_ok(6) and c.interval_ok(d[1], weeks(4))) else None
    if now_lt_12:
      return None  # dose 1 at 7-11 mo: final dose waits until 12 months
    if c.age_lt(d[0], months(12)) and c.age_lt(d[1], months(15)):
      return 3 if c.interval_ok(d[1], weeks(8)) else None
    return None
  if n == 3:
    if all(c.age_lt(x, months(12)) for x in d):
      if c.routine_ok(12) and c.min_age_ok(months(12)) and c.interval_ok(d[2], weeks(8)):
        return 4
    return None
  return None


def pcv_complete(c: Ctx, d: list[date]) -> bool:
  """Healthy-child PCV series complete under AAP 2026 T2: 4 doses; 3 doses
  with at least one at >= 12 months; or dose 1 at 12-23 months followed by a
  final dose 8 weeks later."""
  n = len(d)
  if n >= 4:
    return True
  if n == 3:
    return any(c.given_at_or_after(x, months(12)) for x in d)
  if n == 2:
    return c.given_at_or_after(d[0], months(12))
  return False


def pcv(c: Ctx, d: list[date]) -> int | None:
  """PCV (PCV15/PCV20) at 2, 4, 6, 12-15 months, healthy child.

  AAP 2026 T2 and Notes/Pneumococcal:
  - Not needed for healthy children >= 5 years.
  - Any dose at >= 24 months: no further doses (healthy). Healthy 2-4 years
    with any incomplete series: 1 dose (8 weeks after the last dose).
  - Dose 1->2: 4 weeks if dose 1 before 1st birthday; 8 weeks (final) if
    dose 1 at >= 12 months.
  - Dose 2->3: 4 weeks if current age < 12 months and previous dose < 7
    months; 8 weeks (final) if previous dose at 7-11 months (wait until
    >= 12 months) or current age >= 12 months and at least 1 dose before 12
    months.
  - Dose 3->4: 8 weeks (final), only for age 12-59 months with 3 doses before
    12 months.
  """
  n = len(d)
  if c.age_ge(c.visit, years(5)):
    return None
  if any(c.given_at_or_after(x, months(24)) for x in d) or pcv_complete(c, d):
    return None
  if c.age_ge(c.visit, months(24)):
    # Healthy 2-4 years with any incomplete series: 1 dose (AAP 2026
    # Notes/Pneumococcal), at least 8 weeks after the last dose (T2).
    return n + 1 if (n == 0 or c.interval_ok(d[-1], weeks(8))) else None
  if n == 0:
    return 1 if (c.routine_ok(2) and c.min_age_ok(weeks(6))) else None
  if n == 1:
    if c.age_lt(d[0], months(12)):
      return 2 if (c.routine_ok(4) and c.interval_ok(d[0], weeks(4))) else None
    return 2 if c.interval_ok(d[0], weeks(8)) else None
  if n == 2:
    now_lt_12 = c.age_lt(c.visit, months(12))
    if now_lt_12 and c.age_lt(d[1], months(7)):
      return 3 if (c.routine_ok(6) and c.interval_ok(d[1], weeks(4))) else None
    if now_lt_12:
      return None  # previous dose at 7-11 months: wait until 12 months
    if any(c.age_lt(x, months(12)) for x in d):
      return 3 if c.interval_ok(d[1], weeks(8)) else None
    return None  # dose 1 at >= 12 months and dose 2 final: complete
  if n == 3:
    if all(c.age_lt(x, months(12)) for x in d):
      if c.routine_ok(12) and c.min_age_ok(months(12)) and c.interval_ok(d[2], weeks(8)):
        return 4
    return None
  return None


def mmr(c: Ctx, d: list[date]) -> int | None:
  """MMR at 12-15 months and 4-6 years. AAP 2026 Notes/MMR: minimum age
  12 months; catch-up 2 doses at least 4 weeks apart."""
  n = len(d)
  if n == 0:
    return 1 if c.min_age_ok(months(12)) else None
  if n == 1:
    return 2 if (c.routine_ok(48) and c.interval_ok(d[0], weeks(4))) else None
  return None


def varicella(c: Ctx, d: list[date]) -> int | None:
  """VAR at 12-15 months and 4-6 years. AAP 2026 Notes/Varicella: minimum
  age 12 months; dose 2 >= 3 months after dose 1 (age < 13 years), 4 weeks
  for age >= 13 years."""
  n = len(d)
  if n == 0:
    return 1 if c.min_age_ok(months(12)) else None
  if n == 1:
    gap = months(3) if c.age_lt(c.visit, years(13)) else weeks(4)
    return 2 if (c.routine_ok(48) and c.interval_ok(d[0], gap)) else None
  return None


def hepa(c: Ctx, d: list[date]) -> int | None:
  """HepA 2-dose series at 12-23 months, minimum interval 6 months; catch-up
  through 18 years. AAP 2026 Notes/HepA."""
  n = len(d)
  if c.age_ge(c.visit, years(19)):
    return None
  if n == 0:
    return 1 if c.min_age_ok(months(12)) else None
  if n == 1:
    return 2 if c.interval_ok(d[0], months(6)) else None
  return None


# HPV routine start age. AAP 2026 Notes/HPV: "starting the series between the
# ages of 9 and 12 years"; minimum age 9 years. The engine starts at the
# 9-year well visit.
HPV_ROUTINE_START_YEARS = 9


def hpv(c: Ctx, d: list[date]) -> int | None:
  """HPV9. AAP 2026 Notes/HPV: start 9-12 years, catch-up through 18 years.
  Initial dose at 9-14 years: 2 doses at 0, 6-12 months (minimum interval
  5 months). Initial dose at >= 15 years: 3 doses at 0, 1-2, 6 months
  (minimum intervals 4 weeks, 12 weeks, and 5 months dose 1->3)."""
  n = len(d)
  if c.age_ge(c.visit, years(19)):
    return None
  if n == 0:
    if c.routine_ok(HPV_ROUTINE_START_YEARS * 12) and c.min_age_ok(years(9)):
      return 1
    return None
  two_dose = c.age_lt(d[0], years(15))
  if two_dose:
    if n == 1 and c.interval_ok(d[0], months(6)):
      return 2  # routine 6-12 months; minimum 5 months
    return None
  if n == 1:
    return 2 if c.interval_ok(d[0], weeks(4)) else None
  if n == 2:
    if c.interval_ok(d[1], weeks(12)) and c.interval_ok(d[0], months(6)):
      return 3  # routine 6 months after dose 1; minimum 5 months
  return None


def menacwy(c: Ctx, d: list[date]) -> int | None:
  """MenACWY. AAP 2026 Notes/MenACWY: 2-dose series at 11-12 years and
  16 years. Catch-up age 13-15: 1 dose now and booster at 16-18 (minimum
  interval 8 weeks); age 16-18: 1 dose (no booster)."""
  n = len(d)
  if c.age_ge(c.visit, years(19)):
    return None
  if n == 0:
    return 1 if c.routine_ok(132) and c.min_age_ok(years(11)) else None
  if n == 1:
    if c.given_at_or_after(d[0], years(16)):
      return None
    if c.routine_ok(192) and c.min_age_ok(years(16)) and c.interval_ok(d[0], weeks(8)):
      return 2
  return None


# Routine/catch-up series evaluated at every well visit, in display order.
SERIES_RULES: dict[str, Rule] = {
  "HepB": hepb,
  "RV": rotavirus,
  "DTaP": dtap,
  "Hib": hib,
  "PCV": pcv,
  "IPV": ipv,
  "MMR": mmr,
  "VAR": varicella,
  "HepA": hepa,
  "HPV": hpv,
  "MenACWY": menacwy,
}


def due_series_doses(dob: date, visit: date,
                     history: dict[str, list[date]]) -> list[tuple[str, int]]:
  """Routine and catch-up doses due at a visit.

  Args:
    dob: Date of birth.
    visit: Visit date.
    history: antigen -> dates of valid, completed prior doses (any order).

  Returns:
    (antigen, dose_number) pairs due at this visit. Influenza, COVID-19 and
    RSV antibody are seasonal and handled by their own functions.
  """
  c = Ctx(dob, visit)
  prior = {k: sorted(x for x in v if x < visit) for k, v in history.items()}
  out = []
  for antigen, rule in SERIES_RULES.items():
    n = rule(c, prior.get(antigen, []))
    if n is not None:
      out.append((antigen, n))
  t = tdap(c, prior.get("DTaP", []), prior.get("Tdap", []))
  if t is not None:
    out.append(("Tdap", t))
  return out


# --------------------------------------------------------------------------- #
# Seasonal: influenza, COVID-19, RSV monoclonal antibody                      #
# --------------------------------------------------------------------------- #

def flu_season(d: date) -> int:
  """Influenza season year: Aug 1 of year Y through Jul 31 of Y+1 is season Y.

  Northern-hemisphere vaccine is shipped from late summer; the July 1 cutoff
  for prior-dose counting is AAP 2026 Notes/Influenza. US influenza vaccines
  expire June 30, so the engine does not vaccinate in July.
  """
  return d.year if d.month >= 8 else d.year - 1


def flu_available(d: date) -> bool:
  """Season vaccine on hand Aug-Jun (see flu_season)."""
  return d.month != 7


# AAP 2026 Notes/Influenza: minimum age 6 months (IIV3).
FLU_MIN_AGE = months(6)
# Two doses >= 4 weeks apart for age 6 months-8 years with < 2 prior doses
# before July 1 of the season year (AAP 2026 Notes/Influenza).
FLU_TWO_DOSE_MAX_AGE = years(9)
FLU_DOSE_INTERVAL = weeks(4)


def flu_doses_needed(dob: date, season: int, first_dose_date: date,
                     prior_flu: Iterable[date]) -> int:
  """Number of flu doses needed in a season (1 or 2)."""
  cutoff = date(season, 7, 1)
  before = sum(1 for x in prior_flu if x < cutoff)
  if first_dose_date < dob + FLU_TWO_DOSE_MAX_AGE and before < 2:
    return 2
  return 1


def flu_dose_due(dob: date, visit: date, prior_flu: list[date]) -> int | None:
  """Season dose number of influenza vaccine due at this visit, if any."""
  if visit < dob + FLU_MIN_AGE - timedelta(days=GRACE_DAYS) or not flu_available(visit):
    return None
  s = flu_season(visit)
  this_season = sorted(x for x in prior_flu if flu_season(x) == s and x < visit)
  first = this_season[0] if this_season else visit
  need = flu_doses_needed(dob, s, first, prior_flu)
  if len(this_season) >= need:
    return None
  if this_season and visit < this_season[-1] + FLU_DOSE_INTERVAL - timedelta(days=GRACE_DAYS):
    return None
  return len(this_season) + 1


# COVID-19. AAP 2026 Notes/COVID-19: everyone age 6-23 months; unvaccinated:
# 2 doses Moderna Spikevax at 0, 4-8 weeks; completed initial series: 1 dose
# of the current (2026-2027) vaccine at least 8 weeks after the most recent.
# Age 2-18 outside risk groups: offered if the parent desires protection
# (not generated by this engine).
COVID_MIN_AGE = months(6)
COVID_ROUTINE_MAX_AGE = months(24)
COVID_SERIES_INTERVAL = weeks(4)
COVID_SEASONAL_INTERVAL = weeks(8)


def covid_dose_due(dob: date, visit: date, prior: list[date]) -> int | None:
  """COVID-19 dose number due at this visit for a 6-23 month old, if any.

  Season boundary reuses flu_season (Aug 1); the AAP schedule names the
  "2026-2027 vaccine" without a calendar boundary, so this is an engine
  approximation.
  """
  if not (dob + COVID_MIN_AGE - timedelta(days=GRACE_DAYS) <= visit < dob + COVID_ROUTINE_MAX_AGE):
    return None
  prior = sorted(x for x in prior if x < visit)
  n = len(prior)
  if n == 0:
    return 1
  if n == 1:
    ok = visit >= prior[0] + COVID_SERIES_INTERVAL - timedelta(days=GRACE_DAYS)
    return 2 if ok else None
  if any(flu_season(x) == flu_season(visit) for x in prior):
    return None
  ok = visit >= prior[-1] + COVID_SEASONAL_INTERVAL - timedelta(days=GRACE_DAYS)
  return n + 1 if ok else None


# RSV monoclonal antibody (nirsevimab / clesrovimab). AAP 2026 Notes/RSV:
# infants < 8 months born during or entering their first RSV season if the
# pregnant parent did not receive RSV vaccine, status unknown, or birth < 14
# days after maternal vaccination. Born in season: within 1 week of birth.
# Season: October through end of March in most of the continental US.
RSV_MAB_MAX_AGE = months(8)
RSV_SEASON_MONTHS = (10, 11, 12, 1, 2, 3)


def rsv_season_month(d: date) -> bool:
  return d.month in RSV_SEASON_MONTHS


def rsv_mab_due(dob: date, visit: date, prior: list[date],
                maternal_rsv_vaccine: bool = False) -> bool:
  """True if an infant RSV monoclonal antibody dose is due at this visit."""
  if maternal_rsv_vaccine or prior:
    return False
  return visit < dob + RSV_MAB_MAX_AGE and rsv_season_month(visit)
