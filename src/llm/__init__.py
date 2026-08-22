"""
LLM integration for SynthPatient.
"""

from .client import LLMClient, PromptBuilder, get_client, llm_enabled, set_client

__all__ = ["LLMClient", "PromptBuilder", "get_client", "llm_enabled", "set_client"]
