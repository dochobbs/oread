"""
Claude API client for Oread.

Provides structured output generation, caching, and batch processing.
Uses Claude (Anthropic API) for LLM-enhanced patient generation.

Responses are validated before they are cached: a truncated, refused or
schema-invalid response raises LLMResponseError and never reaches the disk
cache, so one bad response cannot poison later runs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, TypeVar

from anthropic import Anthropic
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

# Bounded so a slow/unreachable API can never hang generation indefinitely.
LLM_TIMEOUT_SECONDS = 60.0
LLM_MAX_RETRIES = 2

# Default model; override with OREAD_LLM_MODEL.
DEFAULT_MODEL = "claude-haiku-5-5"
MODEL_ENV_VAR = "OREAD_LLM_MODEL"

# Models that reject a forced tool_choice ("tool"/"any") with a 400. For
# these, structured output uses tool_choice "auto" plus the system-prompt
# instruction to call the tool, and a missing tool call is a failure.
_NO_FORCED_TOOL_CHOICE_PREFIXES = (
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-fable-5-1",
    "claude-mythos-5-1",
)


class LLMResponseError(ValueError):
    """The API answered, but the response is unusable.

    Raised for truncated (max_tokens), refused, empty, tool-less or
    schema-invalid responses. Subclasses ValueError so existing
    `except ValueError` fallbacks keep working.
    """


def llm_enabled() -> bool:
    """Check whether live LLM calls are enabled.

    Set OREAD_DISABLE_LLM=1 to force rule-based generation (used by tests
    and offline development).
    """
    return os.environ.get("OREAD_DISABLE_LLM", "").strip().lower() not in {
        "1", "true", "yes", "on",
    }


def resolve_model(model: str | None = None) -> str:
    """Pick the model id: explicit argument, then OREAD_LLM_MODEL, then default."""
    return model or os.environ.get(MODEL_ENV_VAR, "").strip() or DEFAULT_MODEL


def _response_text(response: Any) -> str:
    """Join the text blocks of a response (skips thinking/tool blocks)."""
    return "".join(
        getattr(block, "text", "")
        for block in response.content
        if getattr(block, "type", None) == "text"
    )


class LLMClient:
    """
    Client for Claude API with structured output support.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        cache_dir: Path | None = None,
        enable_cache: bool = True,
        timeout: float = LLM_TIMEOUT_SECONDS,
        max_retries: int = LLM_MAX_RETRIES,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        self.client = Anthropic(
            api_key=self.api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.model = resolve_model(model)
        self.cache_dir = cache_dir or Path.home() / ".oread" / "cache"
        self.enable_cache = enable_cache

        if self.enable_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(
        self,
        system: str,
        prompt: str,
        schema: dict | None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Generate a cache key covering every input that shapes the output."""
        content = json.dumps({
            "model": self.model,
            "system": system,
            "prompt": prompt,
            "schema": schema,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def _cache_path(self, cache_key: str) -> Path:
        return self.cache_dir / f"{cache_key}.json"

    def _get_cached(self, cache_key: str) -> str | None:
        """Get a cached response."""
        if not self.enable_cache:
            return None
        cache_file = self._cache_path(cache_key)
        if cache_file.exists():
            return cache_file.read_text()
        return None

    def _set_cached(self, cache_key: str, response: str) -> None:
        """Cache a response (atomic write, so a crash can't leave half a file)."""
        if not self.enable_cache:
            return
        cache_file = self._cache_path(cache_key)
        tmp_file = cache_file.with_suffix(".tmp")
        tmp_file.write_text(response)
        tmp_file.replace(cache_file)

    def _drop_cached(self, cache_key: str) -> None:
        """Remove a cache entry (used when a stored entry fails validation)."""
        self._cache_path(cache_key).unlink(missing_ok=True)

    def _request_options(self) -> dict[str, Any]:
        """Per-model request options.

        Claude Haiku 5.5 thinks by default and thinking counts toward
        max_tokens; Oread's calls are short prose/JSON with small caps, so
        turn it off there (accepted at the default effort).
        """
        if self.model.startswith("claude-haiku-5"):
            return {"thinking": {"type": "disabled"}}
        return {}

    @staticmethod
    def _check_stop_reason(response: Any, *, allow_truncation: bool) -> None:
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            raise LLMResponseError("LLM declined the request (stop_reason=refusal)")
        if stop_reason == "max_tokens" and not allow_truncation:
            raise LLMResponseError(
                "LLM response truncated at max_tokens; refusing to use partial output"
            )

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        require_complete: bool = False,
    ) -> str:
        """
        Generate a free-form text response.

        Args:
            prompt: User prompt.
            system: System prompt.
            max_tokens: Output cap.
            temperature: Kept for API compatibility and the cache key. Not
                sent: current models and API version 2026-10-21 reject
                sampling parameters.
            require_complete: Raise LLMResponseError on max_tokens truncation.
                Set this for machine-parsed output (JSON). Prose callers get
                the partial text plus a logged warning.

        Raises:
            LLMResponseError: refusal, empty text, or truncation when
                require_complete is set.
        """
        messages = [{"role": "user", "content": prompt}]

        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "You are a helpful assistant.",
            messages=messages,
            **self._request_options(),
        )

        self._check_stop_reason(response, allow_truncation=not require_complete)
        if getattr(response, "stop_reason", None) == "max_tokens":
            logger.warning("LLM text truncated at max_tokens=%s", max_tokens)

        text = _response_text(response)
        if not text.strip():
            raise LLMResponseError("LLM returned no text")
        return text

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.5,
        use_cache: bool = True,
    ) -> T:
        """
        Generate a structured response conforming to a Pydantic model.

        Uses Claude's tool use for reliable structured output. The response
        is validated against `schema` before it is cached; anything that
        fails validation raises LLMResponseError and is not cached. A cache
        entry that no longer validates is deleted and regenerated.
        """
        schema_dict = schema.model_json_schema()
        system_text = system or "You are a helpful assistant. Use the provided tool to output your response."
        cache_key = self._cache_key(system_text, prompt, schema_dict, max_tokens, temperature)

        # Check cache
        if use_cache:
            cached = self._get_cached(cache_key)
            if cached:
                try:
                    return schema.model_validate_json(cached)
                except ValidationError:
                    logger.warning("Dropping invalid LLM cache entry %s", cache_key)
                    self._drop_cached(cache_key)

        # Build the tool
        tool = {
            "name": "output",
            "description": "Output the structured response",
            "input_schema": schema_dict,
        }

        if self.model.startswith(_NO_FORCED_TOOL_CHOICE_PREFIXES):
            tool_choice = {"type": "auto"}
        else:
            tool_choice = {"type": "tool", "name": "output"}

        messages = [{"role": "user", "content": prompt}]

        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_text,
            messages=messages,
            tools=[tool],
            tool_choice=tool_choice,
            **self._request_options(),
        )

        # A truncated tool call can still parse into a schema-valid but
        # incomplete object, so truncation is always a failure here.
        self._check_stop_reason(response, allow_truncation=False)

        # Extract the tool call result
        for block in response.content:
            if block.type == "tool_use" and block.name == "output":
                try:
                    result = schema.model_validate(block.input)
                except ValidationError as e:
                    raise LLMResponseError(
                        f"LLM output failed {schema.__name__} validation: {e}"
                    ) from e

                # Cache only what validated.
                if use_cache:
                    self._set_cached(cache_key, json.dumps(block.input))

                return result

        raise LLMResponseError("No tool call in response")
    
    def generate_with_context(
        self,
        prompt: str,
        context: dict[str, Any],
        schema: type[T],
        system: str | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.5,
    ) -> T:
        """
        Generate structured output with patient context.
        
        The context dict is serialized and included in the prompt.
        """
        context_str = json.dumps(context, indent=2, default=str)
        
        full_prompt = f"""<context>
{context_str}
</context>

{prompt}"""
        
        return self.generate_structured(
            prompt=full_prompt,
            schema=schema,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            use_cache=False,  # Context makes caching less useful
        )
    
    def clear_cache(self) -> int:
        """Clear the response cache. Returns number of files deleted."""
        if not self.cache_dir.exists():
            return 0
        
        count = 0
        for f in self.cache_dir.glob("*.json"):
            f.unlink()
            count += 1
        return count


class PromptBuilder:
    """
    Utility for building prompts from templates.
    """
    
    def __init__(self, prompts_dir: Path | None = None):
        self.prompts_dir = prompts_dir or Path(__file__).parent.parent.parent / "prompts"
    
    def load_template(self, path: str) -> str:
        """Load a prompt template file."""
        full_path = self.prompts_dir / path
        if not full_path.exists():
            raise FileNotFoundError(f"Prompt template not found: {full_path}")
        return full_path.read_text()
    
    def render(self, template_path: str, **kwargs: Any) -> str:
        """Load and render a template with variables."""
        template = self.load_template(template_path)
        
        # Simple variable substitution
        for key, value in kwargs.items():
            if isinstance(value, dict):
                value = json.dumps(value, indent=2, default=str)
            elif isinstance(value, list):
                value = json.dumps(value, indent=2, default=str)
            template = template.replace(f"{{{{{key}}}}}", str(value))
        
        return template


# Singleton client instance
_client: LLMClient | None = None


def get_client() -> LLMClient | None:
    """Get the singleton LLM client.

    Returns None when OREAD_DISABLE_LLM is set or no ANTHROPIC_API_KEY is
    available, so callers fall back to rule-based generation (standalone
    mode needs no key).
    """
    if not llm_enabled():
        return None
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        _client = LLMClient()
    return _client


def set_client(client: LLMClient) -> None:
    """Set the singleton LLM client."""
    global _client
    _client = client
