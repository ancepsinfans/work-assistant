"""Configuration loading and normalization for the work assistant."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from llm.factory import normalize_llm_config

AGENT_DIR = Path(__file__).resolve().parent


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """
    Load and normalize assistant configuration.

    Resolution order for the config file path:
    1. Explicit ``path`` argument
    2. ``WORK_ASSISTANT_CONFIG`` environment variable
    3. ``config.yaml`` in the agent directory

    Args:
        path: Optional explicit config file path.

    Returns:
        Normalized configuration dictionary.
    """
    if path is None:
        env_path = os.environ.get("WORK_ASSISTANT_CONFIG")
        config_path = Path(env_path) if env_path else AGENT_DIR / "config.yaml"
    else:
        config_path = Path(path)
        if not config_path.is_absolute():
            config_path = AGENT_DIR / config_path

    with open(config_path, encoding="utf-8") as handle:
        config: dict[str, Any] = yaml.safe_load(handle) or {}

    _normalize_paths(config, AGENT_DIR)
    _normalize_llm_section(config)
    _normalize_assistant_section(config)
    _normalize_sources_section(config)
    return config


def get_llm_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return the LLM subsection, including legacy ``gpt`` key support."""
    return normalize_llm_config(config)


def is_source_enabled(config: dict[str, Any], name: str) -> bool:
    """
    Check whether a data source is enabled.

    Sources default to enabled when their config block exists.
    """
    sources = config.get("sources", {})
    source_cfg = sources.get(name)
    if source_cfg is not None:
        return bool(source_cfg.get("enabled", True))
    return name in config


def get_source_config(config: dict[str, Any], name: str) -> dict[str, Any]:
    """
    Return configuration for a named source, merging top-level legacy keys.

    Args:
        config: Full assistant config.
        name: Source name (slack, gmail, jira, etc.).

    Returns:
        Source-specific settings dict.
    """
    sources = config.get("sources", {})
    if name in sources:
        merged = dict(sources[name])
        merged.pop("enabled", None)
        return merged
    return dict(config.get(name, {}))


def _normalize_paths(config: dict[str, Any], agent_dir: Path) -> None:
    """Expand user paths and resolve credential paths relative to agent dir."""
    config_dir = agent_dir / ".config" / "agent"

    for section_name in ("gmail", "meetings"):
        section = _section_dict(config, section_name)
        for key in ("credentials_path", "token_path"):
            if key in section:
                section[key] = _resolve_path(section[key], agent_dir, config_dir)

    obsidian = _section_dict(config, "obsidian")
    if "vault_path" in obsidian:
        env_override = os.environ.get("OBSIDIAN_VAULT_PATH")
        obsidian["vault_path"] = os.path.expanduser(
            env_override or obsidian["vault_path"]
        )

    agent = _section_dict(config, "agent")
    for key in ("state_db", "log_file"):
        if key in agent:
            agent[key] = os.path.expanduser(agent[key])


def _normalize_llm_section(config: dict[str, Any]) -> None:
    """Ensure ``llm`` exists and absorbs legacy ``gpt`` settings."""
    llm = normalize_llm_config(config)
    assistant = _section_dict(config, "assistant")
    if assistant.get("id") and not llm.get("identifier"):
        llm["identifier"] = assistant["id"]
    config["llm"] = llm


def _normalize_assistant_section(config: dict[str, Any]) -> None:
    """Apply defaults for assistant metadata."""
    assistant = _section_dict(config, "assistant")
    assistant.setdefault("name", "Work Assistant")
    assistant.setdefault("id", "work-assistant")
    assistant.setdefault("role_description", "Product Manager")
    assistant.setdefault("timezone", "America/New_York")
    config["assistant"] = assistant


def _normalize_sources_section(config: dict[str, Any]) -> None:
    """Mirror legacy top-level source keys under ``sources`` when missing."""
    sources = dict(config.get("sources", {}))
    for name in ("slack", "gmail", "jira", "confluence", "meetings", "obsidian", "taskflow"):
        if name in config and name not in sources:
            entry = dict(config[name])
            sources[name] = entry
    config["sources"] = sources


def _section_dict(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a mutable subsection dict, creating it if absent."""
    section = config.setdefault(name, {})
    if not isinstance(section, dict):
        section = {}
        config[name] = section
    return section


def _resolve_path(value: str, agent_dir: Path, default_dir: Path) -> str:
    """Expand and resolve a config path string."""
    expanded = os.path.expanduser(value)
    path = Path(expanded)
    if path.is_absolute():
        return str(path)
    candidate = agent_dir / path
    if candidate.exists():
        return str(candidate.resolve())
    return str((default_dir / path.name).resolve())
