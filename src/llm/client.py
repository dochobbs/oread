"""
Claude API client for Oread.

Provides structured output generation, caching, and batch processing.
Uses Claude (Anthropic API) for LLM-enhanced patient generation.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, TypeVar

from anthropic import Anthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """
    Client for Claude API with structured output support.
    """
    
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
        cache_dir: Path | None = None,
        enable_cache: bool = True,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        
        self.client = Anthropic(api_key=self.api_key)
        self.model = model
        self.cache_dir = cache_dir or Path.home() / ".oread" / "cache"
        self.enable_cache = enable_cache
        
        if self.enable_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _cache_key(self, system: str, prompt: str, schema: dict | None) -> str:
        """Generate a cache key for the request."""
        content = json.dumps({
            "model": self.model,
            "system": system,
            "prompt": prompt,
            "schema": schema,
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def _get_cached(self, cache_key: str) -> str | None:
        """Get a cached response."""
        if not self.enable_cache:
            return None
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            return cache_file.read_text()
        return None
    
    def _set_cached(self, cache_key: str, response: str) -> None:
        """Cache a response."""
        if not self.enable_cache:
            return
        cache_file = self.cache_dir / f"{cache_key}.json"
        cache_file.write_text(response)
    
    def generate(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """
        Generate a free-form text response.
        """
        messages = [{"role": "user", "content": prompt}]
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "You are a helpful assistant.",
            messages=messages,
            temperature=temperature,
        )
        
        return response.content[0].text
    
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
        
        Uses Claude's tool use for reliable structured output.
        """
        schema_dict = schema.model_json_schema()
        
        # Check cache
        if use_cache:
            cache_key = self._cache_key(system or "", prompt, schema_dict)
            cached = self._get_cached(cache_key)
            if cached:
                return schema.model_validate_json(cached)
        
        # Build the tool
        tool = {
            "name": "output",
            "description": "Output the structured response",
            "input_schema": schema_dict,
        }
        
        messages = [{"role": "user", "content": prompt}]
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "You are a helpful assistant. Use the provided tool to output your response.",
            messages=messages,
            tools=[tool],
            tool_choice={"type": "tool", "name": "output"},
            temperature=temperature,
        )
        
        # Extract the tool call result
        for block in response.content:
            if block.type == "tool_use" and block.name == "output":
                result_json = json.dumps(block.input)
                
                # Cache it
                if use_cache:
                    self._set_cached(cache_key, result_json)
                
                return schema.model_validate(block.input)
        
        raise ValueError("No tool call in response")
    
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


def get_client() -> LLMClient:
    """Get the singleton LLM client."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def set_client(client: LLMClient) -> None:
    """Set the singleton LLM client."""
    global _client
    _client = client
