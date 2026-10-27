"""Calendar-month age arithmetic shared by the engine and the validator.

One definition of "age in months" so an onset the engine clamps to a gate is
never re-measured differently by the validator (the old validator used
days / 30.44, which put a boundary onset at 47.97 months for a 48-month gate).
"""

from __future__ import annotations

from datetime import date

from dateutil.relativedelta import relativedelta


def add_months(d: date, months: int) -> date:
  """Calendar month addition (Jan 31 + 1 month = Feb 28/29)."""
  return d + relativedelta(months=months)


def calendar_months_between(dob: date, on: date) -> int:
  """Completed calendar months from dob to on (never negative)."""
  months = (on.year - dob.year) * 12 + (on.month - dob.month)
  if on.day < dob.day:
    months -= 1
  return max(0, months)


def meets_min_age(dob: date, on: date, min_months: int) -> bool:
  """True when `on` is on or after the date the child turns min_months."""
  return on >= add_months(dob, min_months)


def within_max_age(dob: date, on: date, max_months: int) -> bool:
  """True when `on` is on or before the date the child turns max_months."""
  return on <= add_months(dob, max_months)
