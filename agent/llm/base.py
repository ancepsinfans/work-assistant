"""Shared types and protocol for LLM clients."""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


class LLMError(Exception):
    """Raised when an LLM provider returns an error or empty response."""


@runtime_checkable
class LLMClient(Protocol):
    """Minimal interface for chat completion used across the agent."""

    def completion(
        self,
        message: str,
        *,
        timeout: int = 180,
        system: Optional[str] = None,
    ) -> str:
        """Send a single-turn prompt and return the model's text response."""
        ...
