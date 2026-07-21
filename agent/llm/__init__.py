"""Pluggable LLM backends for the work assistant."""

from llm.factory import create_llm

__all__ = ["create_llm"]
