"""Factory for creating LLM clients from configuration."""

from __future__ import annotations

import os
from typing import Any, Optional

from llm.anthropic_client import AnthropicClient
from llm.base import LLMClient, LLMError
from llm.ollama_client import OllamaClient
from llm.openai_client import OpenAIClient


def normalize_llm_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    Return the LLM config subsection, supporting legacy ``gpt:`` keys.

    Args:
        config: Full assistant config or an LLM subsection.

    Returns:
        Normalized LLM settings dict.
    """
    if "provider" in config or "model" in config:
        llm = dict(config)
    elif "llm" in config:
        llm = dict(config["llm"])
    elif "gpt" in config:
        # Legacy block from pre-standalone installs.
        llm = dict(config["gpt"])
        llm.setdefault("provider", "anthropic")
        if "model" in llm and "claude" not in llm["model"]:
            llm.setdefault("provider", "openai")
    else:
        llm = {}

    llm.setdefault("provider", "anthropic")
    llm.setdefault("model", "claude-sonnet-4-20250514")
    llm.setdefault("temperature", 0.4)
    llm.setdefault("max_tokens", 8192)
    llm.setdefault("api_key_env", _default_api_key_env(llm["provider"]))
    llm.setdefault("base_url", "http://localhost:11434")
    return llm


def _default_api_key_env(provider: str) -> str:
    """Map provider name to its conventional environment variable."""
    mapping = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "ollama": "",
    }
    return mapping.get(provider, "LLM_API_KEY")


def _resolve_api_key(llm: dict[str, Any]) -> Optional[str]:
    """Read API key from config or environment."""
    if llm.get("api_key"):
        return llm["api_key"]
    env_name = llm.get("api_key_env") or _default_api_key_env(llm["provider"])
    if not env_name:
        return None
    return os.environ.get(env_name)


def create_llm(
    config: dict[str, Any],
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    identifier: Optional[str] = None,
    model: Optional[str] = None,
) -> LLMClient:
    """
    Build an LLM client from config with optional per-call overrides.

    Args:
        config: Full assistant config or LLM subsection.
        max_tokens: Override default max output tokens for this client.
        temperature: Override default temperature for this client.
        identifier: Optional run label (for logging parity with old Prompter).
        model: Override model name for this client.

    Returns:
        Configured LLMClient instance.

    Raises:
        LLMError: If provider is unknown or required credentials are missing.
    """
    llm = normalize_llm_config(config)
    provider = llm["provider"].lower()
    resolved_model = model or llm["model"]
    resolved_temperature = temperature if temperature is not None else llm["temperature"]
    resolved_max_tokens = max_tokens if max_tokens is not None else llm["max_tokens"]
    resolved_identifier = identifier or llm.get("identifier")

    if provider == "anthropic":
        return AnthropicClient(
            model=resolved_model,
            api_key=_resolve_api_key(llm),
            temperature=resolved_temperature,
            max_tokens=resolved_max_tokens,
            identifier=resolved_identifier,
        )
    if provider == "openai":
        return OpenAIClient(
            model=resolved_model,
            api_key=_resolve_api_key(llm),
            temperature=resolved_temperature,
            max_tokens=resolved_max_tokens,
            identifier=resolved_identifier,
            base_url=llm.get("openai_base_url"),
        )
    if provider == "ollama":
        return OllamaClient(
            model=resolved_model,
            base_url=llm.get("base_url", "http://localhost:11434"),
            temperature=resolved_temperature,
            max_tokens=resolved_max_tokens,
            identifier=resolved_identifier,
        )

    raise LLMError(f"Unknown LLM provider: {provider!r}. Use anthropic, openai, or ollama.")
