"""
PatientContext export conformance (lane I).

`patient_to_context(..., echo_compat=False)` must validate against the
shared PatientContext model generated from Metis `shared/models`. The
default Echo-compatible shape served at `/api/patients/{id}/context` must
keep the fields Metis Dashboard and Echo read, same-shaped.
"""

import pytest

from src.exporters import patient_to_context
from src.models import Allergy, AllergyReaction, CodeableConcept, GenerationSeed
from src.models._generated import PatientContext


@pytest.fixture(scope="module")
def peds_patient():
  from src.engines import PedsEngine
  patient = PedsEngine(use_llm=False).generate(
    GenerationSeed(age=10, conditions=["asthma", "food_allergy"], random_seed=5)
  )
  # The engine doesn't yet add an AllergyIntolerance for food_allergy
  # (lane P); add one so the allergy mapping is exercised.
  if not patient.allergy_list:
    patient.allergy_list.append(Allergy(
      display_name="Peanut",
      code=CodeableConcept(
        system="http://snomed.info/sct", code="91935009", display="Allergy to peanut",
      ),
      category="food",
      criticality="high",
      reactions=[AllergyReaction(manifestation="Urticaria", severity="moderate")],
    ))
  return patient


@pytest.fixture(scope="module")
def adult_patient():
  from adult.adult_engine import AdultEngine
  return AdultEngine(use_llm=False).generate(GenerationSeed(
    age=57, conditions=["type2_diabetes", "hypertension"], random_seed=3,
  ))


@pytest.fixture(params=["peds", "adult"])
def patient(request, peds_patient, adult_patient):
  return peds_patient if request.param == "peds" else adult_patient


def test_shared_shape_validates_against_generated_model(patient):
  context = patient_to_context(patient, echo_compat=False)
  model = PatientContext.model_validate(context)

  assert model.source == "oread"
  assert model.patient_id == patient.id
  assert model.date_of_birth == patient.demographics.date_of_birth
  assert len(model.problem_list) == len(patient.problem_list)
  for entry, condition in zip(model.problem_list, patient.problem_list):
    assert entry.id == condition.id
    assert entry.code.code == condition.code.code
    assert entry.code.system == condition.code.system
    assert entry.onset_date == condition.onset_date


def test_cohort_has_content_to_validate(peds_patient, adult_patient):
  # Guard against a vacuous pass on empty lists.
  assert peds_patient.problem_list and peds_patient.encounters
  assert peds_patient.allergy_list
  assert adult_patient.problem_list and adult_patient.encounters


def test_echo_compat_shape_keeps_consumer_fields(patient):
  context = patient_to_context(patient)

  # Fields Metis Dashboard / Echo read (echo/src/models/context.py,
  # echo/src/core/tutor.py): flat demographics + legacy list fields.
  for key in ("patient_id", "source", "name", "age_years", "age_months", "sex"):
    assert key in context
  for c in context["problem_list"]:
    assert isinstance(c["display_name"], str)
    assert isinstance(c["is_active"], bool)
    assert c["code"] is None or isinstance(c["code"], str)
    assert "code_system" in c
  for m in context["medication_list"]:
    assert isinstance(m["dose"], str)
    assert m["is_active"] is True
    assert m["code"] is None or isinstance(m["code"], str)
  for a in context["allergy_list"]:
    assert "reaction" in a and "severity" in a
  for e in context["recent_encounters"]:
    assert isinstance(e["chief_complaint"], str)
    assert len(e["date"]) == 10  # ISO date


def test_echo_compat_differs_from_shared_only_in_code(patient):
  compat = patient_to_context(patient)
  shared = patient_to_context(patient, echo_compat=False)

  # Re-expanding the flattened codes yields the shared shape exactly.
  for key in ("problem_list", "medication_list"):
    for c_entry, s_entry in zip(compat[key], shared[key]):
      assert c_entry["code"] == s_entry["code"]["code"]
      assert c_entry["code_system"] == s_entry["code"]["system"]
      rest_c = {k: v for k, v in c_entry.items() if k not in ("code", "code_system")}
      rest_s = {k: v for k, v in s_entry.items() if k != "code"}
      assert rest_c == rest_s
  assert {k: v for k, v in compat.items() if k not in ("problem_list", "medication_list", "sex")} == \
    {k: v for k, v in shared.items() if k not in ("problem_list", "medication_list", "sex")}
