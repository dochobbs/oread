"""
Source precedence in ConditionKnowledgeService (lane I, D4/D5).

Order: local YAML -> Athena -> web search / LLM. A cached LLM or web-search
definition is provisional and loses to a later Athena hit. LLM-only
definitions are flagged needs_verification and cached for one day.
Generated content must not depend on whether Athena is up for conditions
defined locally. Athena and the LLM are mocked; no network.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from unittest import mock

import pytest

from src.knowledge.cache import ConditionCache
from src.knowledge.condition_service import (
  LLM_ONLY_CACHE_TTL,
  ConditionKnowledgeService,
)
from src.knowledge.models import ConditionDefinition


LOCAL_YAML = {
  "_meta": {"skip": True},
  "asthma": {
    "display_name": "Asthma",
    "billing_codes": {"icd10": ["J45.909"]},
    "category": "chronic",
  },
}

ATHENA_KAWASAKI = {
  "id": "kawasaki_disease",
  "display_name": "Kawasaki Disease",
  "icd10": ["M30.3"],
  "category": "acute",
}


def _llm_definition(name="kawasaki_disease"):
  return ConditionDefinition(
    condition_key=name,
    display_name="Kawasaki (LLM)",
    icd10_codes=["R69"],
    icd10_primary="R69",
    source="llm",
  )


@pytest.fixture()
def service(tmp_path):
  svc = ConditionKnowledgeService(
    yaml_conditions=LOCAL_YAML,
    llm_client=object(),  # truthy; retrieval methods are stubbed per test
    cache_dir=tmp_path / "cache",
  )
  svc._retrieve_via_llm_only = lambda name: _llm_definition()
  return svc


def test_llm_only_definition_flagged_and_short_lived(service):
  service._athena_lookup = lambda name: None

  result = service.get_condition("kawasaki disease")

  assert result.source == "llm"
  assert result.definition.needs_verification is True
  raw = json.loads(service.cache._get_cache_path("kawasaki disease").read_text())
  assert raw["_ttl_seconds"] == LLM_ONLY_CACHE_TTL.total_seconds()
  assert LLM_ONLY_CACHE_TTL <= timedelta(days=1)


def test_llm_cache_entry_expires_after_ttl(service):
  service._athena_lookup = lambda name: None
  service.get_condition("kawasaki disease")
  path = service.cache._get_cache_path("kawasaki disease")
  raw = json.loads(path.read_text())
  raw["_cached_at"] = (datetime.now() - timedelta(days=2)).isoformat()
  path.write_text(json.dumps(raw))

  assert service.cache.get("kawasaki disease") is None


def test_later_athena_hit_overrides_cached_llm_definition(service):
  # First lookup: Athena down -> LLM definition gets cached.
  service._athena_lookup = lambda name: None
  assert service.get_condition("kawasaki disease").source == "llm"

  # Athena comes back: it must win over the cached LLM definition.
  service._athena_lookup = lambda name: ATHENA_KAWASAKI
  result = service.get_condition("kawasaki disease")
  assert result.source == "athena"
  assert result.definition.icd10_primary == "M30.3"
  assert result.definition.needs_verification is False

  # ... and replaces the cache entry, so it survives Athena going down again.
  service._athena_lookup = lambda name: None
  again = service.get_condition("kawasaki disease")
  assert again.cached is True
  assert again.definition.source == "athena"


def test_cached_llm_definition_served_with_warning_when_athena_down(service, caplog):
  service._athena_lookup = lambda name: None
  service.get_condition("kawasaki disease")
  service._retrieve_via_llm_only = lambda name: pytest.fail("should hit cache")

  with caplog.at_level(logging.WARNING, logger="src.knowledge.condition_service"):
    result = service.get_condition("kawasaki disease")

  assert result.cached is True
  assert result.definition.needs_verification is True
  assert "unverified" in caplog.text


def test_local_yaml_beats_athena(service):
  service._athena_lookup = mock.Mock(return_value={
    "id": "asthma", "display_name": "ATHENA asthma", "icd10": ["Z99.89"],
  })

  result = service.get_condition("Asthma")

  assert result.source == "yaml"
  assert result.definition.icd10_primary == "J45.909"
  service._athena_lookup.assert_not_called()


def test_athena_hit_on_local_key_via_unknown_alias_uses_local_content(service):
  # "reactive airway disease" isn't in the YAML index; Athena resolves it
  # to a key Oread defines locally. Local content wins (D4).
  service._athena_lookup = lambda name: {
    "id": "asthma", "display_name": "ATHENA asthma", "icd10": ["Z99.89"],
  }

  result = service.get_condition("reactive airway disease")

  assert result.source == "yaml"
  assert result.definition.icd10_primary == "J45.909"


def test_athena_queried_with_snake_case_key(service):
  seen = []
  service._athena_lookup = lambda name: seen.append(name) or None
  service._retrieve_via_llm_only = lambda name: None

  service.get_condition("Kawasaki disease - incomplete")

  assert seen == ["kawasaki_disease_incomplete"]


# --------------------------------------------------------------------------- #
# Engine-level: generated content is identical with Athena up or down for
# conditions defined locally.
# --------------------------------------------------------------------------- #

_ID_RE = re.compile(r'"[0-9a-f]{8}"')
_GENERATED_AT_RE = re.compile(r'"generated_at":"[^"]*"')


def _normalized(patient) -> str:
  text = patient.model_dump_json()
  return _GENERATED_AT_RE.sub('"generated_at":"X"', _ID_RE.sub('"ID"', text))


def test_generation_identical_with_athena_up_or_down(tmp_path):
  from src.engines import PedsEngine
  from src.models import GenerationSeed

  engine = PedsEngine(use_llm=False)
  local_keys = {k for k in engine._conditions if not k.startswith("_")}
  athena_calls = []

  def athena_up(name, specialty="pediatrics"):
    # Athena mirrors Oread's conditions but with different (worse) content.
    athena_calls.append(name)
    if name not in local_keys:
      return None
    return {
      "id": name,
      "display_name": f"ATHENA {name}",
      "icd10": ["Z99.89"],
      "category": "acute",
      "treatment": {"medications": [{"agent": "Athena-only drug"}]},
    }

  def generate(cache_name, fetch):
    # Fresh cache per run so one run can't feed the other.
    engine.condition_service.cache = ConditionCache(tmp_path / cache_name)
    with mock.patch(
      "src.knowledge.condition_service.fetch_condition_from_athena",
      side_effect=fetch,
    ):
      return _normalized(engine.generate(GenerationSeed(
        age=8, conditions=["asthma", "adhd"], random_seed=42,
      )))

  down = generate("down", lambda name, specialty="pediatrics": None)
  up = generate("up", athena_up)

  assert athena_calls, "Athena layer was never consulted; test proves nothing"
  assert "ATHENA" not in up and "Z99.89" not in up
  assert up == down
