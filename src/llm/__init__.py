"""
LLM integration for SynthPatient.
"""

from .client import LLMClient, PromptBuilder, get_client, set_client

__all__ = ["LLMClient", "PromptBuilder", "get_client", "set_client"]
