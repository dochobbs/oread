"""
Adult engine correctness (spec lane A).

Field loss under extra="forbid", medication coding/dosing, lab panels,
condition gating, date plausibility, USPSTF screening output, and adult
routing. Deterministic: every generation is seeded; no network.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from adult import adult_labs, adult_meds
from adult.adult_engine import (
    AdultEngine,
    ConditionGateError,
    ConditionRegistry,
    UnknownConditionError,
    infer_age_from_description,
    infer_conditions_from_description,
)
from src.models import (
    Encounter,
    EncounterType,
    GenerationSeed,
    ImagingResult,
    LabPanel,
    LabResult,
    Location,
    Patient,
    PhysicalExam,
    Provider,
    Sex,
    SocialHistory,
)


@pytest.fixture(scope="module")
def engine() -> AdultEngine:
    return AdultEngine(use_llm=False)


def gen(engine: AdultEngine, **kw) -> Patient:
    kw.setdefault("random_seed", 42)
    return engine.generate(GenerationSeed(**kw))


def all_labs(p: Patient):
    for enc in p.encounters:
        for lab in enc.lab_results:
            if isinstance(lab, LabPanel):
                yield from lab.results
            else:
                yield lab


# --------------------------------------------------------------------------- #
# Silent field loss                                                           #
# --------------------------------------------------------------------------- #

def test_unknown_kwargs_raise():
    """The old misspellings now fail loudly instead of vanishing."""
    base = dict(date="2026-01-01", type=EncounterType.CHRONIC_FOLLOWUP, chief_complaint="x",
                provider=Provider(name="Dr. A"), location=Location(name="Clinic"))
    with pytest.raises(ValidationError):
        Encounter(**base, vitals=None)
    with pytest.raises(ValidationError):
        Encounter(**base, history_of_present_illness="x")
    with pytest.raises(ValidationError):
        SocialHistory(living_situation="x", tobacco_use="Never")
    with pytest.raises(ValidationError):
        PhysicalExam(extremities="No edema")


def test_dump_round_trips_and_legacy_loads(engine):
    p = gen(engine, age=60, conditions=["hypertension"])
    rec = p.model_dump(mode="json")
    assert "full_name" in rec["demographics"]  # computed fields are dumped ...
    again = Patient.model_validate(rec)          # ... and accepted back
    assert again.demographics.full_name == p.demographics.full_name
    with pytest.raises(ValidationError):
        Patient.model_validate({**rec, "retired_field": 1})
    legacy = {**rec, "retired_field": 1,
              "encounters": [{**e, "vitals": {}} for e in rec["encounters"]]}
    loaded = Patient.from_stored(legacy)
    assert len(loaded.encounters) == len(p.encounters)


def test_adult_encounters_have_vitals_and_hpi(engine):
    for seed in range(5):
        p = gen(engine, age=55 + seed, conditions=["type2_diabetes"], random_seed=seed)
        for e in p.encounters:
            assert e.vital_signs is not None, e.type
            assert e.hpi, e.type
        assert p.social_history.tobacco is not None
        assert p.social_history.alcohol is not None


# --------------------------------------------------------------------------- #
# Medications                                                                 #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("token,expected", [
    ("10mg", ("10", "mg")), ("49/51mg", ("49/51", "mg")), ("250/50", ("250/50", None)),
    ("16/4mg", ("16/4", "mg")), ("5mg/kg", ("5", "mg/kg")), ("0.005%", ("0.005", "%")),
    ("10units", ("10", "units")), ("1000IU", ("1000", "IU")), ("2.4g", ("2.4", "g")),
    ("30ml", ("30", "mL")), ("10mEq", ("10", "mEq")), ("160/4.5", ("160/4.5", None)),
])
def test_parse_dose(token, expected):
    assert adult_meds.parse_dose(token) == expected


def test_every_condition_med_resolves_to_rxnorm():
    """All chronic med strings map to a coded drug, except the documented skips."""
    registry = ConditionRegistry.get()
    missing = []
    for cond in registry.all_chronic_conditions():
        for s in registry.get_meds(cond):
            spec = adult_meds.resolve(s, cond)
            if spec is None:
                missing.append(s)
                continue
            assert spec.entry["rxnorm"].isdigit(), s
            assert spec.entry["route"] and spec.entry["frequency"], s
            assert spec.entry["classes"], s
    assert sorted(set(missing)) == ["areds2 vitamins"]


@pytest.mark.parametrize("med,route,freq", [
    ("methotrexate 15mg", "oral", "once weekly"),
    ("infliximab 5mg/kg", "intravenous", "every 8 weeks"),
    ("insulin glargine 10units", "subcutaneous", "once daily"),
    ("budesonide/formoterol 160/4.5", "inhaled", "twice daily"),
    ("tiotropium 18mcg", "inhaled", "once daily"),
    ("semaglutide 1mg", "subcutaneous", "once weekly"),
    ("alendronate 70mg", "oral", "once weekly"),
    ("naltrexone 380mg", "intramuscular", "every 4 weeks"),
    ("latanoprost 0.005%", "ophthalmic", "once daily"),
])
def test_route_and_frequency(med, route, freq):
    spec = adult_meds.resolve(med)
    assert spec.entry["route"] == route
    assert freq in spec.entry["frequency"]


def test_methotrexate_pairs_with_folic_acid_and_infliximab_dose(engine):
    import random
    state = adult_meds.TherapyState()
    rng = random.Random(0)
    picks = []
    for _ in range(30):
        state = adult_meds.TherapyState()
        picks += adult_meds.select_for_condition(
            "rheumatoid_arthritis", ["methotrexate 15mg"], state, Sex.FEMALE, rng)
    keys = Counter(p.key for p in picks)
    assert keys["methotrexate"] == keys["folic_acid"] == 30
    spec = adult_meds.resolve("infliximab 5mg/kg")
    med = adult_meds.to_medication(spec, "Crohn's disease", date(2024, 1, 1), date.today(), 80.0)
    assert "400 mg" in med.instructions and med.route == "intravenous"


def test_no_duplicate_classes_or_ingredients(engine):
    table = adult_meds.med_table()
    by_code: dict[str, set[str]] = {}
    for entry in table.values():
        by_code.setdefault(str(entry["rxnorm"]), set()).update(entry["classes"])
    for seed in range(40):
        p = gen(engine, age=70, conditions=["hyperlipidemia", "coronary_artery_disease",
                                           "peripheral_artery_disease", "depression", "anxiety",
                                           "rheumatoid_arthritis", "psoriasis"],
                random_seed=seed)
        active = [m for m in p.medication_list if m.status.value == "active"]
        codes = [m.code.code for m in active]
        assert len(codes) == len(set(codes)), [m.display_name for m in active]
        seen: Counter = Counter()
        for c in codes:
            seen.update(by_code[c])
        dup = [k for k, v in seen.items() if v > 1]
        assert not dup, (dup, [m.display_name for m in active])


def test_first_line_agents(engine):
    for seed in range(15):
        p = gen(engine, age=58, conditions=["type2_diabetes", "hyperlipidemia", "asthma"],
                random_seed=seed)
        names = " ".join(m.display_name.lower() for m in p.medication_list)
        assert "metformin" in names
        assert any(s in names for s in ("statin",))
        assert "budesonide/formoterol" in names or "fluticasone" in names


def test_zolpidem_and_spironolactone_sex_rules():
    import random
    rng = random.Random(1)
    for _ in range(30):
        f = adult_meds.select_for_condition("insomnia", ["zolpidem 10mg", "zolpidem 5mg"],
                                            adult_meds.TherapyState(), Sex.FEMALE, rng)
        assert all(s.dose_quantity == "5" for s in f)
        m = adult_meds.select_for_condition("acne", ["spironolactone 50mg"],
                                            adult_meds.TherapyState(), Sex.MALE, rng)
        assert m == []


# --------------------------------------------------------------------------- #
# Labs                                                                        #
# --------------------------------------------------------------------------- #

def test_panels_are_component_results(engine):
    p = gen(engine, age=60, sex=Sex.MALE, conditions=["type2_diabetes", "hypertension"])
    panels = [lab for e in p.encounters for lab in e.lab_results if isinstance(lab, LabPanel)]
    names = {x.display_name for x in panels}
    assert {"Comprehensive metabolic panel", "Lipid panel"} <= names
    cmp_ = next(x for x in panels if x.code.code == "24323-8")
    assert len(cmp_.results) == 15
    for r in cmp_.results:
        assert r.code.system == "http://loinc.org" and r.code.code
        assert r.reference_range is not None
        assert isinstance(r.value, (int, float))
    lipid = next(x for x in panels if x.code.code == "24331-1")
    v = {r.code.code: r.value for r in lipid.results}
    # Friedewald: LDL = TC - HDL - TG/5
    assert abs(v["2093-3"] - (v["13457-7"] + v["2085-9"] + v["2571-8"] / 5)) <= 2
    a1c = [r for r in all_labs(p) if r.code.code == "4548-4"]
    assert a1c and all(isinstance(r.value, float) for r in a1c)


def test_ckd_egfr_matches_stage(engine):
    p = gen(engine, age=72, sex=Sex.FEMALE, conditions=["ckd_stage3"])
    egfr = [r.value for r in all_labs(p) if r.code.code == "98979-8"]
    assert egfr and all(30 <= v <= 60 for v in egfr), egfr


def test_imaging_is_not_a_lab():
    import random
    ctx = adult_labs.LabContext(sex=Sex.MALE, age=50, conditions=set(), drug_classes=set())
    assert adult_labs.build_lab("chest_xray", ctx, date.today(), random.Random(0)) is None
    img = adult_labs.build_imaging("chest_xray", ctx, date.today(), random.Random(0))
    assert isinstance(img, ImagingResult)
    assert img.impression == "No acute cardiopulmonary disease."
    pna = adult_labs.build_imaging("chest_xray", ctx, date.today(), random.Random(0), "pneumonia")
    assert "pneumonia" in pna.impression.lower()


def test_qualitative_labs_interpretation():
    import random
    ctx = adult_labs.LabContext(sex=Sex.FEMALE, age=40, conditions=set(), drug_classes=set())
    hcv = adult_labs.build_lab("hcv_ab", ctx, date.today(), random.Random(0))
    assert isinstance(hcv, LabResult) and hcv.value == "Nonreactive"
    assert hcv.interpretation.value == "negative"


# --------------------------------------------------------------------------- #
# Condition gating                                                            #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("alias,key,icd", [
    ("ckd", "ckd_stage3", "N18.3"), ("type_2_diabetes", "type2_diabetes", "E11.9"),
    ("t2dm", "type2_diabetes", "E11.9"), ("anxiety_disorder", "anxiety", "F41.1"),
    ("Essential hypertension", "hypertension", "I10"),
])
def test_aliases(engine, alias, key, icd):
    assert ConditionRegistry.get().resolve_key(alias) == key
    p = gen(engine, age=66, conditions=[alias])
    assert [c.code.code for c in p.problem_list] == [icd]


def test_unknown_condition_raises(engine):
    with pytest.raises(UnknownConditionError):
        gen(engine, age=40, conditions=["dragon_pox"])


@pytest.mark.parametrize("kw", [
    dict(age=30, sex=Sex.MALE, conditions=["pcos"]),
    dict(age=40, sex=Sex.MALE, conditions=["endometriosis"]),
    dict(age=60, sex=Sex.FEMALE, conditions=["bph"]),
    dict(age=55, sex=Sex.FEMALE, conditions=["erectile_dysfunction"]),
    dict(age=25, sex=Sex.MALE, conditions=["bph"]),
    dict(age=30, sex=Sex.FEMALE, conditions=["menopause"]),
    dict(conditions=["pcos", "bph"]),
])
def test_condition_gates(engine, kw):
    with pytest.raises(ConditionGateError):
        gen(engine, **kw)


def test_unspecified_sex_follows_condition(engine):
    for seed in range(10):
        assert gen(engine, conditions=["pcos"], random_seed=seed).demographics.sex_at_birth == Sex.FEMALE
        p = gen(engine, conditions=["bph"], random_seed=seed)
        assert p.demographics.sex_at_birth == Sex.MALE and p.demographics.age_years >= 40


def test_random_patients_respect_sex_restrictions(engine):
    registry = ConditionRegistry.get()
    for seed in range(60):
        p = gen(engine, random_seed=seed)
        for c in p.problem_list:
            key = registry.resolve_key(c.display_name)
            assert registry.sex_allowed(key, p.demographics.sex_at_birth), (key, seed)
            assert p.demographics.age_years >= registry.get_min_age(key)


def test_predominance_does_not_exclude(engine):
    """Depression and hypothyroidism are female-predominant, not female-only."""
    p = gen(engine, age=45, sex=Sex.MALE, conditions=["depression", "hypothyroidism"])
    assert len(p.problem_list) == 2


def test_ascvd_uses_registry_key(engine):
    risk = engine._calculate_ascvd_risk_category(["ckd_stage3", "hypertension"], 60, Sex.MALE)
    assert risk == "intermediate"


# --------------------------------------------------------------------------- #
# Plausibility                                                                #
# --------------------------------------------------------------------------- #

def test_dates_never_future_or_before_adulthood(engine):
    today = date.today()
    for seed in range(30):
        p = gen(engine, age=19 + seed * 2, random_seed=seed)
        dob = p.demographics.date_of_birth
        eighteen = date(dob.year + 18, dob.month, 28 if (dob.month, dob.day) == (2, 29) else dob.day)
        for e in p.encounters:
            assert eighteen <= e.date.date() <= today
        for m in p.medication_list:
            assert m.start_date <= today
        for i in p.immunization_record:
            assert eighteen <= i.date <= today
            if i.vaccine_code.code == "213":
                assert i.date >= date(2020, 12, 11)
        for c in p.problem_list:
            assert dob < c.onset_date <= today


def test_follow_up_density_capped(engine):
    p = gen(engine, age=90, conditions=["hypothyroidism"])
    chronic = [e for e in p.encounters if e.type == EncounterType.CHRONIC_FOLLOWUP]
    tsh = [r for r in all_labs(p) if r.code.code == "3016-3"]
    assert len(chronic) <= 20, len(chronic)
    assert len(tsh) <= 20, len(tsh)
    for seed in range(10):
        q = gen(engine, age=80, conditions=["hypertension", "type2_diabetes", "copd",
                                             "heart_failure", "atrial_fibrillation"],
                random_seed=seed)
        n = sum(e.type == EncounterType.CHRONIC_FOLLOWUP for e in q.encounters)
        assert n <= 70, n  # 15 years, never more often than every 3 months once stable


def test_onsets_spread(engine):
    years = Counter()
    for seed in range(40):
        p = gen(engine, age=80, conditions=["hypertension"], random_seed=seed)
        years[p.problem_list[0].onset_date.year // 10] += 1
    assert len(years) >= 3, years


def test_same_seed_same_patient(engine):
    a = gen(engine, age=50, random_seed=7)
    b = gen(engine, age=50, random_seed=7)
    assert a.demographics.date_of_birth == b.demographics.date_of_birth
    assert [c.display_name for c in a.problem_list] == [c.display_name for c in b.problem_list]
    assert [m.display_name for m in a.medication_list] == [m.display_name for m in b.medication_list]
    assert len(a.encounters) == len(b.encounters)


# --------------------------------------------------------------------------- #
# USPSTF screening                                                            #
# --------------------------------------------------------------------------- #

def _imaging(p: Patient) -> list[ImagingResult]:
    return [i for e in p.encounters for i in e.imaging_results]


def test_screening_produces_results(engine):
    mammo = colo = dxa = 0
    for seed in range(10):
        p = gen(engine, age=68, sex=Sex.FEMALE, random_seed=seed)
        img = _imaging(p)
        mammo += any(i.code.code == "24606-6" for i in img)
        dxa += any(i.code.code == "38269-7" for i in img)
        colo += any(pr.code.code == "18746-8" for pr in p.procedure_history)
        assert any(r.code.code == "13955-0" for r in all_labs(p))  # one-time HCV
        assert any(r.code.code == "44261-6" for r in all_labs(p))  # PHQ-9
    assert mammo >= 8 and colo >= 7 and dxa >= 8, (mammo, colo, dxa)


def test_no_breast_or_cervical_screening_for_men(engine):
    for seed in range(10):
        p = gen(engine, age=55, sex=Sex.MALE, random_seed=seed)
        assert not any(i.code.code == "24606-6" for i in _imaging(p))
        assert not any(r.code.code in ("10524-7", "30167-1") for r in all_labs(p))


def test_lung_screening_only_when_eligible(engine):
    for seed in range(40):
        p = gen(engine, age=66, random_seed=seed)
        ldct = [i for i in _imaging(p) if i.code.code == "79086-5"]
        tob = p.social_history.tobacco
        if ldct:
            assert tob.status in ("current", "former")
            assert float(tob.quantity.split()[0]) >= 20


# --------------------------------------------------------------------------- #
# Routing                                                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,adult,age", [
    ("67-year-old man with COPD", True, 67),
    ("a 45 yo woman with migraines", True, 45),
    ("19 year old college student", False, 19),
    ("18-month-old with otitis", False, 1),
    ("elderly woman with falls", True, None),
    ("middle-aged man with chest pain", True, None),
    ("toddler with fever", False, None),
    ("adult with asthma", True, None),
    ("", False, None),
])
def test_infer_age(text, adult, age):
    assert infer_age_from_description(text) == (adult, age)


def test_infer_conditions():
    keys = infer_conditions_from_description("67yo with COPD, type 2 diabetes and CKD; presented to the ED")
    assert set(keys) == {"copd", "type2_diabetes", "ckd_stage3"}


def test_description_only_request_routes_adult(engine):
    p = gen(engine, description="A 72-year-old woman with heart failure and atrial fibrillation")
    assert p.demographics.age_years == 72
    assert {c.code.code for c in p.problem_list} == {"I50.9", "I48.91"}


def test_server_routes_description_to_adult(monkeypatch):
    from fastapi.testclient import TestClient

    import server
    from src.auth import AuthenticatedUser, require_user

    server.app.dependency_overrides[require_user] = (
        lambda: AuthenticatedUser(id="u1", email="u1@test", token="t"))
    try:
        c = TestClient(server.app)
        r = c.post("/api/generate", json={"description": "elderly man with COPD",
                                          "use_llm": False, "random_seed": 3})
        assert r.status_code == 200, r.text
        assert r.json()["age_years"] >= 65
        r = c.post("/api/generate", json={"age": 40, "sex": "male", "conditions": ["pcos"],
                                          "use_llm": False})
        assert r.status_code == 422
        r = c.post("/api/generate", json={"age": 40, "conditions": ["dragon_pox"], "use_llm": False})
        assert r.status_code == 422
    finally:
        server.app.dependency_overrides.clear()
