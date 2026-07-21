"""Local Ollama chat API client."""

from __future__ import annotations

from typing import Optional

import requests

from llm.base import LLMError


class OllamaClient:
    """LLM client backed by a local Ollama server."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://localhost:11434",
        temperature: float = 0.4,
        max_tokens: int = 8192,
        identifier: Optional[str] = None,
    ) -> None:
        """
        Initialize the Ollama client.

        Args:
            model: Ollama model tag (e.g. llama3.1:latest).
            base_url: Ollama server URL.
            temperature: Sampling temperature passed via options.
            max_tokens: Maximum output tokens (num_predict).
            identifier: Optional tag for logging (unused by API).
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.identifier = identifier

    def completion(
        self,
        message: str,
        *,
        timeout: int = 180,
        system: Optional[str] = None,
    ) -> str:
        """
        Send a chat message to Ollama and return the response text.

        Args:
            message: User prompt content.
            timeout: Request timeout in seconds.
            system: Optional system instruction.

        Returns:
            Model response text.

        Raises:
            LLMError: On connection failure or empty response.
        """
        url = f"{self.base_url}/api/chat"
        payload: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": message}],
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        if system:
            payload["messages"].insert(0, {"role": "system", "content": system})

        try:
            response = requests.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            body = response.json()
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        message_obj = body.get("message") or {}
        text = (message_obj.get("content") or "").strip()
        if not text:
            raise LLMError("Ollama returned an empty response")
        return text
