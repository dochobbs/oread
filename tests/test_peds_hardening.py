"""Targeted tests for the peds clinical-hardening lane (P2).

The cohort-wide invariants live in test_clinical_invariants.py; these pin
specific behaviors (seeding, dosing tables, gate arithmetic) directly.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.engines import PedsEngine  # noqa: E402
from src.models import GenerationSeed  # noqa: E402


def _fingerprint(p):
    return (
        p.demographics.date_of_birth,
        p.demographics.given_names,
        p.demographics.family_name,
        [(c.display_name, c.onset_date) for c in p.problem_list],
        [(e.date, e.type.value, e.chief_complaint) for e in p.encounters],
        sorted(m.display_name for m in p.medication_list),
    )


def test_seed_reproduces_with_pinned_today():
    """Same seed + same injected today -> same chart, regardless of engine history."""
    today = date(2026, 10, 27)
    a = PedsEngine(use_llm=False, today=today)
    b = PedsEngine(use_llm=False, today=today)
    # Burn some draws on b so any shared/global state would show up.
    b.generate(GenerationSeed(age_months=40, random_seed=99))
    s = GenerationSeed(age_months=90, conditions=["asthma", "adhd"], random_seed=7)
    assert _fingerprint(a.generate(s)) == _fingerprint(b.generate(s))


def test_injected_today_anchors_dob():
    engine = PedsEngine(use_llm=False, today=date(2020, 3, 15))
    p = engine.generate(GenerationSeed(age_months=24, random_seed=1))
    assert p.demographics.date_of_birth == date(2018, 3, 15)
    assert all(e.date.date() <= date(2020, 3, 15) for e in p.encounters)


# --------------------------------------------------------------------------- #
# Age gates: engine clamp and validator share calendar-month arithmetic       #
# --------------------------------------------------------------------------- #

import pytest  # noqa: E402

from src.validators import PatientValidator  # noqa: E402
from src.validators.age import add_months  # noqa: E402


@pytest.mark.parametrize("gate", sorted(PatientValidator.AGE_GATES))
@pytest.mark.parametrize("dob", [date(2015, 1, 31), date(2016, 2, 29), date(2014, 6, 15)])
def test_gate_boundary_is_inclusive_and_shared(gate, dob):
    """Onset on the day the child turns min_months passes; the day before fails;
    the engine's clamp lands exactly on the boundary the validator accepts."""
    from datetime import timedelta

    from src.models import CodeableConcept, Condition, ConditionStatus

    min_m, max_m = PatientValidator.AGE_GATES[gate]
    engine = PedsEngine(use_llm=False, today=date(2026, 10, 27))
    v = PatientValidator()

    class _P:  # minimal patient stand-in for _validate_age_gates
        class demographics:
            date_of_birth = dob
        problem_list: list = []

    def issues(onset):
        _P.problem_list = [Condition(
            display_name=gate.replace("_", " ").title(),
            code=CodeableConcept(system="x", code="x", display="x"),
            clinical_status=ConditionStatus.ACTIVE, onset_date=onset)]
        return v._validate_age_gates(_P)

    boundary = add_months(dob, min_m)
    assert issues(boundary) == []
    if min_m > 0:
        assert issues(boundary - timedelta(days=1))
        clamped = engine._clamp_onset_to_gate(gate, boundary - timedelta(days=40), dob)
        assert clamped == boundary and issues(clamped) == []
    if max_m is not None:
        top = add_months(dob, max_m)
        assert issues(top) == []
        assert issues(top + timedelta(days=1))
