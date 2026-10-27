"""
LLM integration for SynthPatient.
"""

from .client import (
    DEFAULT_MODEL,
    LLMClient,
    LLMResponseError,
    PromptBuilder,
    get_client,
    llm_enabled,
    resolve_model,
    set_client,
)

__all__ = [
    "DEFAULT_MODEL",
    "LLMClient",
    "LLMResponseError",
    "PromptBuilder",
    "get_client",
    "llm_enabled",
    "resolve_model",
    "set_client",
]
