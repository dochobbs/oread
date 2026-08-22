"""
Tests for the LLM toggle and bounded-client behavior.

Regression net for the May 2026 rot: test_basic_generation hung because
generation made live Anthropic calls with the SDK's default 10-minute
timeout whenever ANTHROPIC_API_KEY was present in the environment.
"""

import os
from unittest import mock

import pytest


class TestLLMToggle:
    def test_disabled_env_returns_none_client(self):
        """OREAD_DISABLE_LLM=1 makes get_client() return None."""
        from src.llm import get_client

        with mock.patch.dict(os.environ, {"OREAD_DISABLE_LLM": "1"}):
            assert get_client() is None

    def test_toggle_accepts_common_truthy_values(self):
        from src.llm import llm_enabled

        for value in ("1", "true", "YES", "On", " yes "):
            with mock.patch.dict(os.environ, {"OREAD_DISABLE_LLM": value}):
                assert llm_enabled() is False, f"failed for {value!r}"

    def test_enabled_when_unset_or_falsy(self):
        from src.llm import llm_enabled

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OREAD_DISABLE_LLM", None)
            assert llm_enabled() is True
        for value in ("0", "false", "no", "", "off"):
            with mock.patch.dict(os.environ, {"OREAD_DISABLE_LLM": value}):
                assert llm_enabled() is True, f"failed for {value!r}"

    def test_engine_falls_back_to_rule_based(self):
        """PedsEngine must generate without any LLM when disabled."""
        from src.engines import PedsEngine
        from src.models import GenerationSeed

        with mock.patch.dict(os.environ, {"OREAD_DISABLE_LLM": "1"}):
            engine = PedsEngine()
            assert engine.llm is None
            patient = engine.generate(GenerationSeed(age=2, random_seed=42))
            assert patient is not None
            assert len(patient.encounters) > 0


class TestBoundedClient:
    def test_client_uses_explicit_timeout_and_retries(self):
        """LLMClient must not run on SDK defaults (10 min timeout)."""
        anthropic = pytest.importorskip("anthropic")
        from src.llm import LLMClient

        client = LLMClient(api_key="test-key-not-real")
        config = anthropic.Anthropic(
            api_key="test-key-not-real",
            timeout=client.client.timeout,
            max_retries=client.client.max_retries,
        )
        assert config.timeout == client.client.timeout
        # Bounded: no call may outlive these limits.
        assert client.client.max_retries <= 2
        total = (
            client.client.timeout.read
            if hasattr(client.client.timeout, "read")
            else client.client.timeout
        )
        assert float(total) <= 120.0
