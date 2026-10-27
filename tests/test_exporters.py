"""
Exporter unit tests: one clinical fact in, the right coded shape out.

Each test builds a seeded patient, sets the fact under test directly on the
model, and checks C-CDA, FHIR and Markdown agree. No network.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from adult.adult_engine import AdultEngine
from src.engines import PedsEngine
from src.exporters import export_markdown
from src.exporters.ccda import export_to_ccda
from src.exporters.fhir import export_to_fhir
from src.exporters.sig import build_sig, parse_dose, parse_frequency
from src.models import (
    CodeableConcept,
    Condition,
    ConditionStatus,
    GenerationSeed,
    Immunization,
    ImmunizationStatus,
    LabResult,
    Medication,
)

import invariants as inv

NS = {"c": "urn:hl7-org:v3"}
C = "{urn:hl7-org:v3}"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"


@pytest.fixture(scope="module")
def base_patient():
    return PedsEngine(use_llm=False).generate(GenerationSeed(age_months=60, random_seed=42))


@pytest.fixture
def patient(base_patient):
    """A fresh deep copy with empty med/imm/problem lists to fill per test."""
    p = base_patient.model_copy(deep=True)
    p.medication_list = []
    p.immunization_record = []
    p.problem_list = []
    for enc in p.encounters:
        enc.prescriptions = []
        enc.immunizations_given = []
    return p


def _med(name="Amoxicillin", qty="1200", unit="mg", freq="divided BID", prn=False,
         code="723", **kw) -> Medication:
    return Medication(code=CodeableConcept(system=RXNORM, code=code, display=name),
                      display_name=name, dose_quantity=qty, dose_unit=unit, frequency=freq,
                      prn=prn, start_date=date(2025, 1, 10), **kw)


def _med_entries(xml: str) -> list[ET.Element]:
    root = ET.fromstring(xml)
    return [sa for sec in inv._section(root, inv.MED_SECTION)
            for sa in sec.findall("c:entry/c:substanceAdministration", NS)]


def _resources(bundle: dict, rtype: str) -> list[dict]:
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == rtype]


# --------------------------------------------------------------------------- #
# Frequency parsing                                                           #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,prn,period,period_max,unit,per_day", [
    ("BID", False, 12, None, "h", 2),
    ("bid", False, 12, None, "h", 2),
    ("twice daily", False, 12, None, "h", 2),
    ("TID", False, 8, None, "h", 3),
    ("QID", False, 6, None, "h", 4),
    ("Apply 0.5 inch ribbon QID", False, 6, None, "h", 4),
    ("daily", False, 24, None, "h", 1),
    ("once daily", False, 24, None, "h", 1),
    ("daily in AM", False, 24, None, "h", 1),
    ("q8h", False, 8, None, "h", 3),
    ("Q4-6H PRN", True, 4, 6, "h", None),
    ("q6-8h prn", True, 6, 8, "h", None),
    ("every 6 hours as needed", True, 6, None, "h", 4),
    ("weekly", False, 1, None, "wk", None),
    ("q8wk", False, 8, None, "wk", None),
])
def test_parse_frequency(text, prn, period, period_max, unit, per_day):
    t = parse_frequency(text)
    assert (t.prn, t.period, t.period_max, t.period_unit, t.per_day) == \
        (prn, period, period_max, unit, per_day)


def test_parse_frequency_bedtime_and_once():
    hs = parse_frequency("at bedtime")
    assert hs.event == "HS" and hs.period is None and hs.per_day == 1
    once = parse_frequency("x1 dose")
    assert once.once and not once.has_fixed_period
    assert parse_frequency("x1 dose PRN").prn


def test_parse_dose():
    assert parse_dose("600") == (600.0, None)
    assert parse_dose("1-2") == (1.0, 2.0)
    assert parse_dose("per package") is None
    assert parse_dose("per sliding scale") is None


def test_divided_dose_sig_is_explicit():
    sig = build_sig(_med())
    assert sig.dose == (600.0, None)
    assert sig.daily_dose == 1200.0
    assert "600 mg per dose" in sig.text and "1200 mg/day divided twice daily" in sig.text


# --------------------------------------------------------------------------- #
# Medications                                                                 #
# --------------------------------------------------------------------------- #

def test_bid_med_gets_12h_pivl_and_per_dose_quantity(patient):
    patient.medication_list = [_med()]
    (sa,) = _med_entries(export_to_ccda(patient))
    pivl = [e for e in sa.findall("c:effectiveTime", NS) if e.get(XSI_TYPE) == "PIVL_TS"]
    assert len(pivl) == 1
    period = pivl[0].find("c:period", NS)
    assert (period.get("value"), period.get("unit")) == ("12", "h")
    dose = sa.find("c:doseQuantity", NS)
    assert (dose.get("value"), dose.get("unit")) == ("600", "mg")
    max_dose = sa.find("c:maxDoseQuantity/c:numerator", NS)
    assert max_dose.get("value") == "1200"

    (ms,) = _resources(export_to_fhir(patient), "MedicationStatement")
    dosage = ms["dosage"][0]
    assert dosage["timing"]["repeat"] == {"frequency": 1, "period": 12.0, "periodUnit": "h"}
    assert dosage["doseAndRate"][0]["doseQuantity"]["value"] == 600.0
    assert dosage["maxDosePerPeriod"]["numerator"]["value"] == 1200.0


def test_prn_med_gets_precondition_not_fixed_period(patient):
    patient.medication_list = [_med("Ibuprofen", "100", "mg", "Q6-8H PRN", prn=True,
                                    code="5640", prn_reason="fever")]
    xml = export_to_ccda(patient)
    (sa,) = _med_entries(xml)
    assert sa.find("c:precondition", NS) is not None
    assert not [e for e in sa.findall("c:effectiveTime", NS) if e.get(XSI_TYPE) == "PIVL_TS"]
    assert "every 6-8 hours as needed for fever" in xml

    (ms,) = _resources(export_to_fhir(patient), "MedicationStatement")
    dosage = ms["dosage"][0]
    assert dosage["asNeededCodeableConcept"]["text"] == "fever"
    assert dosage["timing"]["repeat"]["periodMax"] == 8.0
    assert inv.check_ccda_structure(xml, patient) == []


def test_non_numeric_dose_goes_to_sig_only(patient):
    patient.medication_list = [_med("Erythromycin ophthalmic", "per package", "",
                                    "Apply 0.5 inch ribbon QID", route="ophthalmic", code="4053")]
    xml = export_to_ccda(patient)
    (sa,) = _med_entries(xml)
    assert sa.find("c:doseQuantity", NS) is None
    assert sa.find("c:routeCode", NS).get("code") == "C38287"
    assert "per package" in xml
    (ms,) = _resources(export_to_fhir(patient), "MedicationStatement")
    assert "doseAndRate" not in ms["dosage"][0]


def test_placeholder_rxnorm_is_not_emitted_as_a_code(patient):
    patient.medication_list = [_med("Lisinopril", "10", "mg", "daily", code="000000")]
    (sa,) = _med_entries(export_to_ccda(patient))
    code = sa.find("c:consumable/c:manufacturedProduct/c:manufacturedMaterial/c:code", NS)
    assert code.get("nullFlavor") == "OTH" and code.get("code") is None
    (ms,) = _resources(export_to_fhir(patient), "MedicationStatement")
    assert "coding" not in ms["medicationCodeableConcept"]


@pytest.mark.parametrize("route,code", [("intramuscular", "C28161"), ("intravenous", "C38276"),
                                        ("inhaled", "C38216"), ("subcutaneous", "C38299")])
def test_route_codes(patient, route, code):
    patient.medication_list = [_med("Drug", "1", "mg", "daily", route=route)]
    (sa,) = _med_entries(export_to_ccda(patient))
    assert sa.find("c:routeCode", NS).get("code") == code


# --------------------------------------------------------------------------- #
# Immunizations                                                               #
# --------------------------------------------------------------------------- #

def test_refused_immunization_is_negated_everywhere(patient):
    patient.immunization_record = [
        Immunization(vaccine_code=CodeableConcept(system="http://hl7.org/fhir/sid/cvx",
                                                  code="03", display="MMR"),
                     display_name="MMR", date=date(2023, 6, 1),
                     status=ImmunizationStatus.NOT_DONE,
                     status_reason="Parental religious exemption"),
        Immunization(vaccine_code=CodeableConcept(system="http://hl7.org/fhir/sid/cvx",
                                                  code="20", display="DTaP"),
                     display_name="DTaP", date=date(2021, 3, 1), dose_number=1),
    ]
    xml = export_to_ccda(patient)
    root = ET.fromstring(xml)
    sas = [sa for sec in inv._section(root, inv.IMM_SECTION)
           for sa in sec.findall("c:entry/c:substanceAdministration", NS)]
    negated = [sa for sa in sas if sa.get("negationInd") == "true"]
    assert len(negated) == 1 and len(sas) == 2
    reason = negated[0].find("c:entryRelationship/c:observation/c:code", NS)
    assert (reason.get("code"), reason.get("codeSystem")) == ("RELIG", "2.16.840.1.113883.5.8")

    imms = _resources(export_to_fhir(patient), "Immunization")
    not_done = [i for i in imms if i["status"] == "not-done"]
    assert len(not_done) == 1
    assert not_done[0]["statusReason"]["coding"][0]["code"] == "RELIG"

    md = export_markdown(patient)
    assert "MMR** 2023-06-01: NOT GIVEN: Parental religious exemption" in md
    assert "- **MMR**: #" not in md  # never listed as an administered dose


# --------------------------------------------------------------------------- #
# Problems                                                                    #
# --------------------------------------------------------------------------- #

def test_resolved_problem_has_high(patient):
    patient.problem_list = [Condition(
        code=CodeableConcept(system="http://hl7.org/fhir/sid/icd-10-cm", code="H66.90",
                             display="Otitis media, unspecified"),
        display_name="Acute otitis media", onset_date=date(2024, 2, 1),
        abatement_date=date(2024, 2, 15), clinical_status=ConditionStatus.RESOLVED)]
    root = ET.fromstring(export_to_ccda(patient))
    (act,) = [a for sec in inv._section(root, inv.PROBLEM_SECTION)
              for a in sec.findall("c:entry/c:act", NS)]
    assert act.find("c:statusCode", NS).get("code") == "completed"
    assert act.find("c:effectiveTime/c:high", NS).get("value") == "20240215"
    obs = act.find("c:entryRelationship/c:observation", NS)
    assert obs.find("c:effectiveTime/c:high", NS).get("value") == "20240215"

    (cond,) = _resources(export_to_fhir(patient), "Condition")
    assert cond["abatementDateTime"] == "2024-02-15"
    assert cond["category"][0]["coding"][0]["code"] == "problem-list-item"
    assert "Resolved: 2024-02-15" in export_markdown(patient)


# --------------------------------------------------------------------------- #
# Results / vitals / document                                                 #
# --------------------------------------------------------------------------- #

def test_single_lab_is_wrapped_in_result_organizer(patient):
    enc = patient.encounters[-1]
    enc.lab_results = [LabResult(
        code=CodeableConcept(system="http://loinc.org", code="4548-4", display="Hemoglobin A1c"),
        display_name="Hemoglobin A1c", value="6.1", unit="%",
        resulted_date=datetime(2025, 5, 1, 9, 30))]
    root = ET.fromstring(export_to_ccda(patient))
    entries = [e for sec in inv._section(root, inv.RESULTS_SECTION)
               for e in sec.findall("c:entry", NS)]
    assert entries and all(e.find("c:organizer", NS) is not None for e in entries)
    value = [o for o in root.iter(f"{C}observation")
             if o.find("c:code", NS) is not None and o.find("c:code", NS).get("code") == "4548-4"]
    assert value[0].find("c:value", NS).get(XSI_TYPE) == "PQ"
    obs = [o for o in _resources(export_to_fhir(patient), "Observation")
           if o["code"].get("coding", [{}])[0].get("code") == "4548-4"]
    assert obs[0]["valueQuantity"] == {"value": 6.1, "unit": "%", "system": "http://unitsofmeasure.org",
                                       "code": "%"}


def test_one_vitals_section_and_one_weight_per_measurement(base_patient):
    xml = export_to_ccda(base_patient)
    root = ET.fromstring(xml)
    assert len(inv._section(root, inv.VITALS_SECTION)) == 1
    assert '[cm_i]' not in xml
    bundle = export_to_fhir(base_patient)
    weights = [o["effectiveDateTime"][:10] for o in _resources(bundle, "Observation")
               if o["code"]["coding"][0]["code"] == "29463-7"]
    assert weights and len(weights) == len(set(weights))
    bp = [o for o in _resources(bundle, "Observation") if o["code"]["coding"][0]["code"] == "85354-9"]
    assert all(len(o["component"]) == 2 for o in bp)
    assert not [o for o in _resources(bundle, "Observation")
                if o["code"]["coding"][0]["code"] in ("8480-6", "8462-4")]


def test_header_and_ids(base_patient):
    xml = export_to_ccda(base_patient)
    root = ET.fromstring(xml)
    roots = {t.get("root"): t.get("extension") for t in root.findall("c:templateId", NS)}
    assert roots["2.16.840.1.113883.10.20.22.1.1"] == "2015-08-01"
    assert root.find("c:languageCode", NS).get("code") == "en"
    assert "urn:oread" not in xml
    race = root.find(".//c:patient/c:raceCode", NS)
    assert race.get("code") or race.get("nullFlavor")


def test_control_characters_are_stripped(patient):
    enc = patient.encounters[-1]
    enc.chief_complaint = "cough\x0b and \x01fever"
    xml = export_to_ccda(patient)
    assert "cough and fever" in xml


def test_no_encounter_narrative_cap(base_patient):
    root = ET.fromstring(export_to_ccda(base_patient))
    sec = inv._section(root, "2.16.840.1.113883.10.20.22.2.22.1")[0]
    rows = sec.findall("c:text/c:table/c:tbody/c:tr", NS)
    assert len(rows) == len(base_patient.encounters)


# --------------------------------------------------------------------------- #
# FHIR R4 validity                                                            #
# --------------------------------------------------------------------------- #

def _no_empty(value) -> bool:
    if isinstance(value, dict):
        return all(v not in (None, "", [], {}) and _no_empty(v) for v in value.values())
    if isinstance(value, list):
        return all(_no_empty(v) for v in value)
    return True


@pytest.mark.parametrize("make", [
    lambda: PedsEngine(use_llm=False).generate(GenerationSeed(age_months=30, random_seed=3)),
    lambda: AdultEngine(use_llm=False).generate(GenerationSeed(age=57, conditions=[
        "type2_diabetes", "hypertension"], random_seed=9)),
], ids=["peds", "adult"])
def test_fhir_validates_r4(make):
    bundle = export_to_fhir(make())
    assert inv.check_fhir_valid(bundle) == []
    assert _no_empty(bundle)
    stamp = datetime.fromisoformat(bundle["timestamp"])
    assert stamp.tzinfo is not None
    assert stamp - datetime.now(stamp.tzinfo) < timedelta(minutes=1)
    for enc in _resources(bundle, "Encounter"):
        assert datetime.fromisoformat(enc["period"]["start"]).tzinfo is not None
