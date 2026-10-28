"""
Tests for LLMClient response validation, caching and model selection.

No network: the Anthropic SDK client is replaced with a fake whose
`messages.create` returns canned responses.
"""

import json
import os
from types import SimpleNamespace
from unittest import mock

import pytest
from pydantic import BaseModel

from src.llm import client as client_mod
from src.llm.client import DEFAULT_MODEL, LLMClient, LLMResponseError, get_client


class Parsed(BaseModel):
    age_months: int
    sex: str


def _tool_response(payload, stop_reason="tool_use"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="tool_use", name="output", input=payload)],
    )


def _text_response(text, stop_reason="end_turn", thinking=False):
    blocks = []
    if thinking:
        blocks.append(SimpleNamespace(type="thinking", thinking=""))
    blocks.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(stop_reason=stop_reason, content=blocks)


class FakeMessages:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


@pytest.fixture()
def make_client(tmp_path, monkeypatch):
    monkeypatch.delenv("OREAD_LLM_MODEL", raising=False)

    def _make(*responses, model=None):
        c = LLMClient(api_key="test-key-not-real", model=model, cache_dir=tmp_path)
        fake = FakeMessages(*responses)
        c.client = SimpleNamespace(messages=fake)
        return c, fake

    return _make


def _cache_files(c):
    return list(c.cache_dir.glob("*.json"))


class TestModelSelection:
    def test_default_model(self, monkeypatch):
        monkeypatch.delenv("OREAD_LLM_MODEL", raising=False)
        assert DEFAULT_MODEL == "claude-haiku-5-5"
        assert LLMClient(api_key="k", enable_cache=False).model == "claude-haiku-5-5"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("OREAD_LLM_MODEL", "claude-sonnet-5-5")
        assert LLMClient(api_key="k", enable_cache=False).model == "claude-sonnet-5-5"

    def test_explicit_argument_wins(self, monkeypatch):
        monkeypatch.setenv("OREAD_LLM_MODEL", "claude-sonnet-5-5")
        assert LLMClient(api_key="k", model="x-model", enable_cache=False).model == "x-model"


class TestStructuredValidation:
    def test_invalid_response_is_not_cached(self, make_client):
        c, fake = make_client(
            _tool_response({"age_months": "not a number", "sex": "female"}),
            _tool_response({"age_months": 6, "sex": "female"}),
        )
        with pytest.raises(LLMResponseError):
            c.generate_structured("p", Parsed)
        assert _cache_files(c) == []

        # The retry reaches the API (no poisoned cache) and caches the good answer.
        assert c.generate_structured("p", Parsed).age_months == 6
        assert len(fake.calls) == 2
        assert len(_cache_files(c)) == 1

    def test_valid_response_served_from_cache(self, make_client):
        c, fake = make_client(_tool_response({"age_months": 6, "sex": "female"}))
        c.generate_structured("p", Parsed)
        again = c.generate_structured("p", Parsed)
        assert again.age_months == 6
        assert len(fake.calls) == 1

    def test_truncated_response_rejected_and_not_cached(self, make_client):
        c, _ = make_client(
            _tool_response({"age_months": 6, "sex": "female"}, stop_reason="max_tokens")
        )
        with pytest.raises(LLMResponseError, match="max_tokens"):
            c.generate_structured("p", Parsed)
        assert _cache_files(c) == []

    def test_refusal_rejected(self, make_client):
        c, _ = make_client(SimpleNamespace(stop_reason="refusal", content=[]))
        with pytest.raises(LLMResponseError, match="refusal"):
            c.generate_structured("p", Parsed)

    def test_missing_tool_call_rejected(self, make_client):
        c, _ = make_client(_text_response("no tool here"))
        with pytest.raises(LLMResponseError):
            c.generate_structured("p", Parsed)
        assert _cache_files(c) == []

    def test_corrupt_cache_entry_is_dropped_and_regenerated(self, make_client):
        c, fake = make_client(_tool_response({"age_months": 9, "sex": "male"}))
        system = "You are a helpful assistant. Use the provided tool to output your response."
        key = c._cache_key(system, "p", Parsed.model_json_schema(), 8192, 0.5)
        c._set_cached(key, json.dumps({"age_months": "bad"}))

        assert c.generate_structured("p", Parsed).age_months == 9
        assert len(fake.calls) == 1
        assert json.loads(c._get_cached(key)) == {"age_months": 9, "sex": "male"}

    def test_forced_tool_choice_on_haiku_auto_on_opus_5_5(self, make_client):
        c, fake = make_client(_tool_response({"age_months": 1, "sex": "male"}))
        c.generate_structured("p", Parsed, use_cache=False)
        assert fake.calls[0]["tool_choice"] == {"type": "tool", "name": "output"}

        c, fake = make_client(
            _tool_response({"age_months": 1, "sex": "male"}), model="claude-opus-5-5"
        )
        c.generate_structured("p", Parsed, use_cache=False)
        assert fake.calls[0]["tool_choice"] == {"type": "auto"}


class TestCacheKey:
    def test_key_covers_all_inputs(self, make_client):
        c, _ = make_client()
        base = dict(system="s", prompt="p", schema={"a": 1}, max_tokens=100, temperature=0.5)
        k0 = c._cache_key(**base)
        for field, value in [
            ("system", "other"),
            ("max_tokens", 200),
            ("temperature", 0.1),
            ("prompt", "q"),
        ]:
            assert c._cache_key(**{**base, field: value}) != k0, field
        c.model = "claude-sonnet-5-5"
        assert c._cache_key(**base) != k0, "model"


class TestFreeText:
    def test_text_skips_thinking_blocks(self, make_client):
        c, _ = make_client(_text_response("HPI text", thinking=True))
        assert c.generate("p") == "HPI text"

    def test_no_sampling_params_sent(self, make_client):
        # SDK 2.x / API 2026-10-21 reject temperature; it must not be sent.
        c, fake = make_client(_text_response("ok"))
        c.generate("p", temperature=0.2)
        assert "temperature" not in fake.calls[0]
        assert fake.calls[0]["model"] == "claude-haiku-5-5"
        assert fake.calls[0]["thinking"] == {"type": "disabled"}

    def test_truncation_raises_when_complete_required(self, make_client):
        c, _ = make_client(_text_response('{"a": ', stop_reason="max_tokens"))
        with pytest.raises(LLMResponseError):
            c.generate("p", require_complete=True)

    def test_truncated_prose_returned(self, make_client):
        c, _ = make_client(_text_response("partial note", stop_reason="max_tokens"))
        assert c.generate("p") == "partial note"

    def test_empty_text_raises(self, make_client):
        c, _ = make_client(_text_response("   "))
        with pytest.raises(LLMResponseError):
            c.generate("p")


class TestGracefulDegradation:
    def test_no_key_returns_none_client(self, monkeypatch):
        monkeypatch.setenv("OREAD_DISABLE_LLM", "0")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with mock.patch.object(client_mod, "_client", None):
            assert get_client() is None

    def test_disabled_flag_still_wins(self):
        with mock.patch.dict(os.environ, {"OREAD_DISABLE_LLM": "1", "ANTHROPIC_API_KEY": "k"}):
            assert get_client() is None
