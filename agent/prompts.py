"""Load markdown prompt templates with variable substitution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATES_DIR = AGENT_DIR / "templates"


def _templates_dir(config: dict[str, Any] | None) -> Path:
    """Resolve the templates directory from config or the default."""
    if config:
        templates_cfg = config.get("templates", {})
        custom = templates_cfg.get("directory")
        if custom:
            path = Path(custom)
            if not path.is_absolute():
                path = AGENT_DIR / path
            return path
    return DEFAULT_TEMPLATES_DIR


def _template_path(name: str, config: dict[str, Any] | None) -> Path:
    """
    Resolve a template file path.

    ``name`` may be a bare stem (``system_prompt``) or a relative path
    (``preprocess/summarize_note``).
    """
    templates_cfg = (config or {}).get("templates", {})
    overrides = {
        "system_prompt": templates_cfg.get("system_prompt"),
        "absence_prompt": templates_cfg.get("absence_prompt"),
        "ask_prompt": templates_cfg.get("ask_prompt"),
        "summarize_note": templates_cfg.get("summarize_note"),
        "slack_synthesis": templates_cfg.get("slack_synthesis"),
        "meeting_synthesis": templates_cfg.get("meeting_synthesis"),
        "domain_rules": templates_cfg.get("domain_rules"),
    }

    override = overrides.get(name.replace("preprocess/", "").replace(".md", ""))
    if override:
        path = Path(override)
        if not path.is_absolute():
            path = AGENT_DIR / path
        return path

    stem = name if name.endswith(".md") else f"{name}.md"
    return _templates_dir(config) / stem


def load_template_file(path: Path) -> str:
    """Read a template file, returning empty string if missing."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def load_domain_rules(config: dict[str, Any] | None) -> str:
    """
    Load optional domain-specific rules for injection into the system prompt.

    Combines JIRA rules (when enabled) with any custom_context.md from setup.
    """
    parts: list[str] = []

    custom_path = _template_path("domain/custom_context", config)
    custom = load_template_file(custom_path)
    if custom:
        parts.append(custom)

    if config and _source_enabled_for_domain(config, "jira"):
        rules_path = _template_path("domain/jira_rules", config)
        jira_rules = load_template_file(rules_path)
        if jira_rules:
            parts.append(jira_rules)

    if not parts:
        return ""
    return "\n\n## Domain-specific rules\n\n" + "\n\n".join(parts)


def _source_enabled_for_domain(config: dict[str, Any], name: str) -> bool:
    """Check whether a source is enabled (mirrors config_loader.is_source_enabled)."""
    sources = config.get("sources", {})
    source_cfg = sources.get(name)
    if source_cfg is not None:
        return bool(source_cfg.get("enabled", True))
    return name in config


def _substitute(text: str, variables: dict[str, str]) -> str:
    """Replace ``{key}`` placeholders without interpreting other braces (e.g. JSON examples)."""
    result = text
    for key, value in variables.items():
        result = result.replace("{" + key + "}", value)
    return result


def load_prompt(
    name: str,
    config: dict[str, Any] | None = None,
    **variables: str,
) -> str:
    """
    Load a markdown template and substitute ``{placeholders}``.

    Args:
        name: Template stem or relative path under ``templates/``.
        config: Optional full assistant config for path overrides.
        **variables: Values to substitute into the template body.

    Returns:
        Rendered prompt text.
    """
    path = _template_path(name, config)
    text = load_template_file(path)
    if not text:
        raise FileNotFoundError(f"Prompt template not found: {path}")

    merged = dict(variables)
    if "domain_rules" not in merged and config is not None:
        merged["domain_rules"] = load_domain_rules(config)
    if "assistant_name" not in merged and config is not None:
        merged["assistant_name"] = config.get("assistant", {}).get("name", "Work Assistant")
    if "role_description" not in merged and config is not None:
        merged["role_description"] = config.get("assistant", {}).get(
            "role_description", "Product Manager"
        )

    return _substitute(text, merged)
