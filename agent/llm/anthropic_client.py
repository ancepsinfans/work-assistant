"""Anthropic Messages API client."""

from __future__ import annotations

import os
from typing import Optional

from llm.base import LLMError


class AnthropicClient:
    """LLM client backed by the Anthropic Messages API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: Optional[str] = None,
        temperature: float = 0.4,
        max_tokens: int = 8192,
        identifier: Optional[str] = None,
    ) -> None:
        """
        Initialize the Anthropic client.

        Args:
            model: Model identifier (e.g. claude-opus-4-6).
            api_key: API key; if omitted, read from ANTHROPIC_API_KEY.
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens.
            identifier: Optional tag for logging (unused by API).
        """
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.identifier = identifier
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            raise LLMError(
                "Anthropic API key not set. Set ANTHROPIC_API_KEY or llm.api_key in config."
            )

    def completion(
        self,
        message: str,
        *,
        timeout: int = 180,
        system: Optional[str] = None,
    ) -> str:
        """
        Send a user message and return the assistant's text response.

        Args:
            message: User prompt content.
            timeout: Request timeout in seconds.
            system: Optional system instruction.

        Returns:
            Model response text.

        Raises:
            LLMError: On API failure or empty response.
        """
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError(
                "anthropic package not installed. Run: pip install anthropic"
            ) from exc

        client = anthropic.Anthropic(api_key=self._api_key, timeout=timeout)
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": message}],
        }
        if system:
            kwargs["system"] = system

        try:
            response = client.messages.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"Anthropic request failed: {exc}") from exc

        parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)

        text = "".join(parts).strip()
        if not text:
            raise LLMError("Anthropic returned an empty response")
        return text
