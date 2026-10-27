"""
Clinical invariant suite: generate a seeded cohort, assert on patient content.

The rest of the suite checks that code runs. This one checks that what it
produces would hold up in a real chart. Each test aggregates one invariant
across the whole cohort and reports every violation.

Known failures from the 2026-10-27 review are marked xfail(strict=True) with
the finding ID from docs/superpowers/specs/2026-10-27-oread-clinical-hardening-goal.md.
Strict means a fix flips the test to XPASS -> failure, so the marker must be
removed in the same change that fixes the bug.

Cohort size: a smoke cohort runs by default. Set OREAD_COHORT=full for the
larger cohort (about 120 peds + 40 adults).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from adult.adult_engine import AdultEngine
from src.engines import PedsEngine
from src.exporters import export_markdown
from src.exporters.ccda import export_to_ccda
from src.exporters.fhir import export_to_fhir
from src.models import GenerationSeed, Patient

import invariants as inv


# --------------------------------------------------------------------------- #
# Cohort                                                                      #
# --------------------------------------------------------------------------- #

# (age_months, conditions, messiness). Messiness >= 3 injects vaccine refusals.
PEDS_SMOKE: list[tuple[int, list[str] | None, int]] = [
    (1, None, 0),
    (5, None, 0),
    (9, ["bronchiolitis"], 0),
    (15, ["otitis_media"], 0),
    (20, None, 0),
    (30, ["eczema", "food_allergy"], 0),
    (54, None, 0),
    (60, ["food_allergy"], 0),
    (66, ["adhd"], 0),
    (90, ["asthma", "adhd"], 0),
    (100, ["type_1_diabetes"], 0),
    (110, ["epilepsy"], 0),
    (130, ["obesity", "type_2_diabetes"], 0),
    (150, ["allergic_rhinitis", "asthma"], 0),
    (168, ["anxiety_disorder", "depression"], 0),
    (200, None, 0),
    (40, None, 3),
    (96, None, 3),
    (180, ["anxiety_disorder"], 3),
    (24, ["recurrent_otitis_media"], 0),
]

ADULT_SMOKE: list[tuple[int, list[str] | None]] = [
    (25, None),
    (34, ["asthma", "anxiety"]),
    (48, ["hypertension", "hyperlipidemia"]),
    (57, ["type2_diabetes", "hypertension"]),
    (63, ["rheumatoid_arthritis"]),
    (67, ["copd", "heart_failure"]),
    (75, ["ckd_stage3", "hypertension"]),
    (88, ["hypothyroidism", "osteoporosis"]),
]

FULL = os.environ.get("OREAD_COHORT", "").lower() == "full"
SEEDS = range(6) if FULL else range(1)
ADULT_SEEDS = range(5) if FULL else range(1)


@dataclass
class Member:
    label: str
    patient: Patient | None = None
    error: str | None = None
    age_months: int | None = None
    age_years: int | None = None
    exports: dict = field(default_factory=dict)


def _build() -> list[Member]:
    # Hesitancy reads the engine's messiness, not GenerationSeed.messiness_level
    # (known finding), so build one engine per level.
    peds_by_mess: dict[int, PedsEngine] = {}
    adult = AdultEngine(use_llm=False)
    members: list[Member] = []
    for s in SEEDS:
        for i, (age, conds, mess) in enumerate(PEDS_SMOKE):
            seed = 1000 * s + i
            peds = peds_by_mess.setdefault(mess, PedsEngine(use_llm=False, messiness_level=mess))
            m = Member(label=f"peds {age}mo {conds or '-'} m{mess} seed={seed}", age_months=age)
            try:
                m.patient = peds.generate(GenerationSeed(
                    age_months=age, conditions=conds, messiness_level=mess, random_seed=seed))
            except Exception as exc:  # noqa: BLE001 - the error is the finding
                m.error = f"{type(exc).__name__}: {exc}"
            members.append(m)
    for s in ADULT_SEEDS:
        for i, (age, conds) in enumerate(ADULT_SMOKE):
            seed = 5000 + 1000 * s + i
            m = Member(label=f"adult {age}y {conds or '-'} seed={seed}", age_years=age)
            try:
                m.patient = adult.generate(GenerationSeed(
                    age=age, conditions=conds, random_seed=seed))
            except Exception as exc:  # noqa: BLE001
                m.error = f"{type(exc).__name__}: {exc}"
            members.append(m)
    return members


@pytest.fixture(scope="module")
def cohort() -> list[Member]:
    return _build()


@pytest.fixture(scope="module")
def generated(cohort) -> list[Member]:
    return [m for m in cohort if m.patient is not None]


@pytest.fixture(scope="module")
def peds(generated) -> list[Member]:
    return [m for m in generated if m.age_months is not None]


@pytest.fixture(scope="module")
def adults(generated) -> list[Member]:
    return [m for m in generated if m.age_years is not None]


@pytest.fixture(scope="module")
def exported(generated) -> list[Member]:
    for m in generated:
        if not m.exports:
            m.exports = {
                "fhir": export_to_fhir(m.patient),
                "ccda": export_to_ccda(m.patient),
                "markdown": export_markdown(m.patient),
            }
    return generated


def _assert_none(members: list[Member], check, *args_fn) -> None:
    """Run `check` over members and fail listing every violation."""
    report = []
    for m in members:
        args = args_fn[0](m) if args_fn else (m.patient,)
        for v in check(*args):
            report.append(f"  {m.label}: {v}")
    if report:
        shown = "\n".join(report[:40])
        more = f"\n  ... and {len(report) - 40} more" if len(report) > 40 else ""
        pytest.fail(f"{check.__name__}: {len(report)} violation(s)\n{shown}{more}", pytrace=False)


def xfail(finding: str):
    return pytest.mark.xfail(strict=True, reason=f"known: {finding}")


# --------------------------------------------------------------------------- #
# Generation                                                                  #
# --------------------------------------------------------------------------- #

def test_cohort_generates(cohort):
    errors = [f"  {m.label}: {m.error}" for m in cohort if m.error]
    if errors:
        pytest.fail("generation failed:\n" + "\n".join(errors), pytrace=False)


GATE_BOUNDARIES = [("adhd", 48), ("anxiety_disorder", 36), ("depression", 72),
                   ("asthma", 12), ("prediabetes", 120), ("type_2_diabetes", 120),
                   ("enuresis", 60), ("obesity", 24), ("obstructive_sleep_apnea", 24),
                   ("learning_disorder", 60)]


def test_gate_boundary_ages_generate():
    """A condition requested at exactly its minimum age generates without error."""
    engine = PedsEngine(use_llm=False)
    errors = []
    for cond, min_age in GATE_BOUNDARIES:
        for seed in (1, 2):
            try:
                engine.generate(GenerationSeed(age_months=min_age, conditions=[cond],
                                               random_seed=seed))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"  {cond} at {min_age}mo seed={seed}: {exc}")
    if errors:
        pytest.fail("\n".join(errors), pytrace=False)


@pytest.mark.xfail(FULL, strict=True, reason="known: future dates (see sweep below)")
def test_dates_within_lifetime(generated):
    _assert_none(generated, inv.check_dates)


@xfail("P abatement dates after today; A encounters/med starts after today")
def test_no_future_dates_sweep():
    """Seed sweep that reliably reaches the future-date paths the smoke cohort can miss."""
    peds, adult = PedsEngine(use_llm=False), AdultEngine(use_llm=False)
    report = []
    for seed in range(10):
        for label, p in (
            (f"peds 20mo seed={seed}",
             peds.generate(GenerationSeed(age_months=20, random_seed=3000 + seed))),
            (f"adult 25y seed={seed}",
             adult.generate(GenerationSeed(age=25, random_seed=7000 + seed))),
        ):
            report += [f"  {label}: {v}" for v in inv.check_dates(p)]
    if report:
        pytest.fail("\n".join(report), pytrace=False)


def test_seed_messiness_drives_hesitancy():
    """GenerationSeed.messiness_level alone produces vaccine refusals at high levels."""
    engine = PedsEngine(use_llm=False)
    refused = 0
    for seed in range(5):
        p = engine.generate(GenerationSeed(age_months=96, messiness_level=5, random_seed=seed))
        refused += sum(1 for i in p.immunization_record if i.status.value == "not-done")
    if refused == 0:
        pytest.xfail("known: P hesitancy reads engine messiness, ignores seed.messiness_level")


def test_cohort_has_refusals(generated):
    """Guard: the export refusal checks are vacuous unless the cohort has refusals."""
    assert any(i.status.value == "not-done"
               for m in generated for i in m.patient.immunization_record)


def test_peds_requested_age(peds):
    report = [f"  {m.label}: {v}" for m in peds
              for v in inv.check_requested_age(m.patient, age_months=m.age_months)]
    if report:
        pytest.fail("\n".join(report), pytrace=False)


@xfail("A adult DOB = age*365 days ignores leap days")
def test_adult_requested_age():
    """Adult DOB yields the requested age, across enough seeds to hit leap-day drift."""
    engine = AdultEngine(use_llm=False)
    report = []
    for seed in range(60):
        age = 20 + seed
        p = engine.generate(GenerationSeed(age=age, random_seed=seed))
        report += [f"  {age}y seed={seed}: {v}" for v in inv.check_requested_age(p, age_years=age)]
    if report:
        pytest.fail("\n".join(report), pytrace=False)


# --------------------------------------------------------------------------- #
# Immunizations                                                               #
# --------------------------------------------------------------------------- #

@xfail("P immunizations: no prior doses passed; nearest-age matching skips dose 3")
def test_vaccine_series_complete_for_age(peds):
    _assert_none(peds, inv.check_vaccine_series)


@xfail("P immunizations: 12mo visit at 11.8mo gives MMR/VAR/HepA early; no RV max age")
def test_vaccine_min_max_ages(peds):
    _assert_none(peds, inv.check_vaccine_ages)


@xfail("P immunizations: catch-up gives 'DTaP #1' after doses 2 and 3")
def test_vaccine_dose_numbers_ordered(peds):
    _assert_none(peds, inv.check_vaccine_dose_numbers)


@xfail("P immunizations: influenza deduped on (name, dose) so only one ever")
def test_annual_flu(peds):
    _assert_none(peds, inv.check_annual_flu)


# --------------------------------------------------------------------------- #
# Medications                                                                 #
# --------------------------------------------------------------------------- #

# P side (AOM amox + amox-clav, SSRI x2) fixed; adult duplicate statins remain and
# only surface in the full cohort.
@pytest.mark.xfail(FULL, strict=True, reason="known: A duplicate statins")
def test_no_duplicate_therapy(generated):
    _assert_none(generated, inv.check_duplicate_therapy)


def test_chronic_doses_in_range(peds):
    _assert_none(peds, inv.check_chronic_doses)


def test_med_minimum_ages(peds):
    _assert_none(peds, inv.check_med_min_age)


def test_required_meds(peds):
    _assert_none(peds, inv.check_required_meds)


def test_basal_insulin_not_sliding_scale(peds):
    _assert_none(peds, inv.check_sliding_scale_basal)


# --------------------------------------------------------------------------- #
# Conditions / coding / labs                                                  #
# --------------------------------------------------------------------------- #

def test_self_limited_conditions_resolve(peds):
    _assert_none(peds, inv.check_self_limited_resolve)


@xfail("A RxNorm 000000 on every adult med; R69 fallback")
def test_no_placeholder_codes(generated):
    _assert_none(generated, inv.check_no_placeholder_codes)


def test_diabetes_has_a1c(peds):
    _assert_none(peds, inv.check_diabetes_a1c)


# --------------------------------------------------------------------------- #
# Encounters                                                                  #
# --------------------------------------------------------------------------- #

def test_peds_encounter_vitals(peds):
    _assert_none(peds, inv.check_encounter_vitals)


@xfail("A vitals= kwarg silently dropped (field is vital_signs)")
def test_adult_encounter_vitals(adults):
    _assert_none(adults, inv.check_encounter_vitals)


@xfail("A history_of_present_illness= kwarg silently dropped (field is hpi)")
def test_adult_encounter_hpi(adults):
    _assert_none(adults, inv.check_encounter_hpi)


def test_peds_encounter_hpi(peds):
    _assert_none(peds, inv.check_encounter_hpi)


def test_exam_age_appropriate(peds):
    _assert_none(peds, inv.check_exam_age_appropriate)


# --------------------------------------------------------------------------- #
# Exports                                                                     #
# --------------------------------------------------------------------------- #

@xfail("X FHIR datetimes lack timezone; Bundle.timestamp not an instant")
def test_fhir_bundles_validate(exported):
    _assert_none(exported, inv.check_fhir_valid, lambda m: (m.exports["fhir"],))


@xfail("X C-CDA: duplicate Vitals, [cm_i], bare results, imm negation, no high, q24h")
def test_ccda_structure(exported):
    _assert_none(exported, inv.check_ccda_structure,
                 lambda m: (m.exports["ccda"], m.patient))


@xfail("X Markdown shows refused vaccines as given")
def test_format_parity(exported):
    _assert_none(exported, inv.check_format_parity,
                 lambda m: (m.patient, m.exports["fhir"], m.exports["ccda"],
                            m.exports["markdown"]))


# --------------------------------------------------------------------------- #
# Standalone (D5)                                                             #
# --------------------------------------------------------------------------- #

STANDALONE_SCRIPT = textwrap.dedent("""
    from fastapi.testclient import TestClient
    import server
    c = TestClient(server.app)
    assert c.get("/api/health").status_code == 200
    for body in ({"age_months": 30, "use_llm": False},
                 {"age": 45, "specialty": "internal_medicine", "use_llm": False}):
        r = c.post("/api/generate", json=body)
        assert r.status_code == 200, (body, r.status_code, r.text[:300])
        pid = r.json()["id"]
        for fmt in ("json", "fhir", "markdown", "ccda"):
            e = c.get(f"/api/patients/{pid}/export/{fmt}")
            assert e.status_code == 200, (fmt, e.status_code)
        assert c.get(f"/api/patients/{pid}/context").status_code == 200
    print("STANDALONE_OK")
""")


def test_runs_standalone():
    """Server generates and exports with no Supabase, no API keys, Athena unreachable."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SUPABASE_", "ANTHROPIC_", "EXA_"))}
    # server.py calls load_dotenv(), which never overrides an existing variable,
    # so empty values here keep the repo's .env from re-injecting credentials.
    for key in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY",
                "ANTHROPIC_API_KEY", "EXA_API_KEY"):
        env[key] = ""
    env["ATHENA_URL"] = "http://127.0.0.1:9"  # discard port: always refused
    env["OREAD_DISABLE_LLM"] = "1"
    proc = subprocess.run([sys.executable, "-c", STANDALONE_SCRIPT], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    assert "STANDALONE_OK" in proc.stdout, proc.stderr[-2000:]
