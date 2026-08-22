"""
Tests for the Athena knowledge-source layer in ConditionKnowledgeService.

The Athena layer sits between the disk cache and web-search fallback:
local YAML -> cache -> Athena -> web search -> LLM. These tests pin the
ordering, the payload mapping, and graceful degradation when Athena is
unreachable or disabled.
"""

from unittest import mock

import httpx
import pytest

from src.knowledge.athena_source import (
  athena_condition_to_definition,
  athena_enabled,
  fetch_condition_from_athena,
)
from src.knowledge.condition_service import ConditionKnowledgeService


ATHENA_OTITIS = {
  "id": "otitis_media",
  "display_name": "Otitis Media",
  "aliases": ["middle ear infection", "AOM"],
  "snomed": "65363002",
  "icd10": ["H66.90"],
  "rxnorm_primary": "723",
  "category": "acute",
  "system": "ent",
  "specialties": ["peds", "shared"],
  "presentation": {
    "symptoms": [
      {"name": "ear pain", "probability": 0.9},
      {"name": "fever", "probability": 0.6},
    ],
    "physical_exam": [
      {"system": "ent", "finding": "Bulging tympanic membrane", "probability": 0.9},
    ],
  },
  "diagnostics": {
    "labs": [
      {"name": "Rapid strep", "loinc": "78012-2", "value_type": "binary"},
    ],
    "imaging": [],
  },
  "treatment": {
    "approach": "Amoxicillin first-line unless recent amoxicillin use",
    "medications": [
      {
        "agent": "Amoxicillin",
        "rxnorm": "723",
        "dose_mg_kg": 45,
        "max_dose_mg": 1000,
        "frequency": "BID",
        "route": "oral",
      },
    ],
  },
}


@pytest.fixture()
def service(tmp_path):
  return ConditionKnowledgeService(
    yaml_conditions={"_meta": {"skip": True}},
    llm_client=None,
    cache_dir=tmp_path / "cache",
  )


class TestMapper:
  def test_maps_full_athena_payload(self):
    definition = athena_condition_to_definition(ATHENA_OTITIS)

    assert definition.condition_key == "otitis_media"
    assert definition.display_name == "Otitis Media"
    assert definition.snomed_code == "65363002"
    assert definition.icd10_primary == "H66.90"
    assert definition.category == "acute"
    assert definition.body_system == "ent"
    assert definition.typical_symptoms == ["ear pain", "fever"]
    assert len(definition.physical_exam_findings) == 1
    assert definition.physical_exam_findings[0].probability == 0.9
    assert definition.labs[0].loinc == "78012-2"
    assert definition.medications[0].agent == "Amoxicillin"
    assert definition.medications[0].dose_mg_kg == 45
    assert definition.treatment_approach.startswith("Amoxicillin")
    assert definition.source == "athena"
    assert definition.confidence == 0.95

  def test_normalizes_newborn_category_to_acute(self):
    data = {**ATHENA_OTITIS, "category": "newborn"}
    assert athena_condition_to_definition(data).category == "acute"

  def test_handles_minimal_payload(self):
    definition = athena_condition_to_definition(
      {"id": "x", "display_name": "X", "category": "chronic"}
    )
    assert definition.category == "chronic"
    assert definition.typical_symptoms == []
    assert definition.medications == []


class TestServiceLayer:
  def test_athena_hit_returns_source_athena(self, service):
    service._athena_lookup = lambda name: ATHENA_OTITIS

    result = service.get_condition("otitis media")

    assert result.found is True
    assert result.source == "athena"
    assert result.definition.snomed_code == "65363002"

  def test_athena_miss_falls_through_to_web_search(self, service, tmp_path):
    calls = []

    def spy_web_search(query):
      calls.append(query)
      return None

    service.web_search = lambda q: []
    service.llm = object()  # truthy: layer 3 requires both search and llm
    service._retrieve_via_web_search = spy_web_search
    service._retrieve_via_llm_only = lambda name: None
    service._athena_lookup = lambda name: None

    result = service.get_condition("mystery condition")

    assert calls, "web search layer should be reached after Athena miss"
    assert result.found is False

  def test_mapping_failure_falls_through_not_raises(self, service):
    # Payload missing required fields must not crash the lookup chain.
    service._athena_lookup = lambda name: {"id": None}

    result = service.get_condition("whatever")

    assert result.found is False

  def test_lookup_exception_swallowed(self, service):
    def boom(name):
      raise RuntimeError("network on fire")

    service._athena_lookup = boom
    result = service.get_condition("otitis media")
    assert result.found is False


class TestFetchFunction:
  def test_disabled_by_env(self):
    with mock.patch.dict(__import__("os").environ, {"OREAD_DISABLE_ATHENA": "1"}):
      assert athena_enabled() is False
      assert fetch_condition_from_athena("otitis_media") is None

  def test_connection_error_returns_none(self):
    with mock.patch(
      "src.knowledge.athena_source.httpx.get",
      side_effect=httpx.ConnectError("refused"),
    ):
      assert fetch_condition_from_athena("otitis_media") is None

  def test_non_200_returns_none(self):
    response = mock.Mock(status_code=404)
    with mock.patch(
      "src.knowledge.athena_source.httpx.get", return_value=response
    ):
      assert fetch_condition_from_athena("not_a_real_condition") is None

  def test_success_returns_payload(self):
    response = mock.Mock(status_code=200)
    response.json.return_value = ATHENA_OTITIS
    with mock.patch(
      "src.knowledge.athena_source.httpx.get", return_value=response
    ) as mock_get:
      result = fetch_condition_from_athena("otitis_media")
      assert result == ATHENA_OTITIS
      assert "specialty" in mock_get.call_args.kwargs["params"] or (
        mock_get.call_args.kwargs.get("params")
      )
