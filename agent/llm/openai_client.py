"""OpenAI Chat Completions API client."""

from __future__ import annotations

import os
from typing import Optional

from llm.base import LLMError


class OpenAIClient:
    """LLM client backed by the OpenAI Chat Completions API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: Optional[str] = None,
        temperature: float = 0.4,
        max_tokens: int = 8192,
        identifier: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        """
        Initialize the OpenAI client.

        Args:
            model: Model identifier (e.g. gpt-4o).
            api_key: API key; if omitted, read from OPENAI_API_KEY.
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens.
            identifier: Optional tag for logging (unused by API).
            base_url: Optional custom API base URL for compatible providers.
        """
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.identifier = identifier
        self.base_url = base_url
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self._api_key:
            raise LLMError(
                "OpenAI API key not set. Set OPENAI_API_KEY or llm.api_key in config."
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
            from openai import OpenAI
        except ImportError as exc:
            raise LLMError(
                "openai package not installed. Run: pip install openai"
            ) from exc

        client_kwargs: dict = {"api_key": self._api_key, "timeout": timeout}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = OpenAI(**client_kwargs)

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": message})

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:
            raise LLMError(f"OpenAI request failed: {exc}") from exc

        choice = response.choices[0].message.content if response.choices else None
        if not choice or not choice.strip():
            raise LLMError("OpenAI returned an empty response")
        return choice.strip()
