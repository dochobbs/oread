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


# --------------------------------------------------------------------------- #
# Dosing tables (src/engines/peds_dosing.py)                                  #
# --------------------------------------------------------------------------- #

from src.engines import peds_dosing as pd  # noqa: E402


@pytest.mark.parametrize("kg,mg", [(6.0, "0.1"), (14.9, "0.1"), (15.0, "0.15"),
                                   (29.9, "0.15"), (30.0, "0.3"), (60.0, "0.3")])
def test_epinephrine_weight_bands(kg, mg):
    assert pd.epinephrine(kg)[0].dose_quantity == mg


def test_cetirizine_age_bands():
    assert pd.cetirizine(5) == []
    assert pd.cetirizine(12)[0].dose_quantity == "2.5"
    assert pd.cetirizine(48)[0].dose_quantity == "2.5"
    assert pd.cetirizine(80)[0].dose_quantity == "10"


def test_levetiracetam_never_exceeds_label_ceiling():
    for age, kg in [(2, 4.0), (10, 8.5), (60, 18.0), (150, 40.0), (200, 70.0)]:
        rx = pd.levetiracetam(age, kg)[0]
        daily = float(rx.dose_quantity) * 2
        assert daily <= 3000
        if age < 192:
            assert daily / kg <= pd._band(pd.LEV_MAX_MG_KG_DAY, age)


def test_one_ssri_for_anxiety_and_depression():
    assert [r.agent for r in pd.ssri(150, depression=True, anxiety=True)] == ["Fluoxetine"]
    assert [r.agent for r in pd.ssri(80, depression=False, anxiety=True)] == ["Sertraline"]
    assert pd.ssri(50, depression=False, anxiety=True) == []


def test_preschool_stimulant_starts_low():
    rx = pd.methylphenidate(54)[0]
    assert float(rx.dose_quantity) * 2 <= 10
    assert pd.methylphenidate(80)[0].dose_quantity == "18"


def test_aom_amox_clav_only_after_recent_amoxicillin():
    from datetime import timedelta
    e = PedsEngine(use_llm=False, today=date(2026, 10, 27))
    meds = e._conditions["otitis_media"]["treatment"]["medications"]
    d = date(2026, 1, 10)
    e._rx_history = []
    assert [m["agent"] for m in e._select_acute_meds(meds, d) if not m.get("prn")] == ["Amoxicillin"]
    e._rx_history = [(d - timedelta(days=12), "Amoxicillin")]
    assert [m["agent"] for m in e._select_acute_meds(meds, d) if not m.get("prn")] == \
        ["Amoxicillin-Clavulanate"]
    e._rx_history = [(d - timedelta(days=45), "Amoxicillin")]
    assert [m["agent"] for m in e._select_acute_meds(meds, d) if not m.get("prn")] == ["Amoxicillin"]


def test_divided_daily_dose_is_split():
    e = PedsEngine(use_llm=False)
    amox = e._conditions["otitis_media"]["treatment"]["medications"][0]
    dose, freq = e._dose_from_def(amox, 10.0)  # 90 mg/kg/day -> 450 mg BID
    assert (dose, freq) == ("450", "BID")
    dose, _ = e._dose_from_def(amox, 60.0)  # capped at 4000 mg/day -> 2000 BID
    assert dose == "2000"
