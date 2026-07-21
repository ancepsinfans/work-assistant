"""Timezone helpers driven by ``agent.timezone`` in config."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/New_York"

_configured_tz: ZoneInfo | None = None


def configure(config: dict[str, Any] | None) -> ZoneInfo:
    """
    Set the active timezone from assistant config.

    Call once at process startup (``main.py``, ``ask.py``, ``meeting_brief.py``).

    Args:
        config: Full assistant config or ``None`` for the default timezone.

    Returns:
        The configured ``ZoneInfo`` instance.
    """
    global _configured_tz
    _configured_tz = resolve_timezone(config)
    return _configured_tz


def resolve_timezone(config: dict[str, Any] | None) -> ZoneInfo:
    """
    Resolve a ``ZoneInfo`` from config without mutating global state.

    Args:
        config: Full assistant config.

    Returns:
        Configured timezone, or ``DEFAULT_TIMEZONE`` when unset.
    """
    tz_name = DEFAULT_TIMEZONE
    if config:
        tz_name = config.get("agent", {}).get("timezone", DEFAULT_TIMEZONE)
    return ZoneInfo(tz_name)


def get_timezone() -> ZoneInfo:
    """
    Return the configured timezone.

    Falls back to ``DEFAULT_TIMEZONE`` if ``configure()`` has not been called.
    """
    if _configured_tz is not None:
        return _configured_tz
    return ZoneInfo(DEFAULT_TIMEZONE)


def now() -> datetime:
    """Current time in the configured timezone."""
    return datetime.now(get_timezone())


def tz_label() -> str:
    """
    Short timezone label for display in prompts and commit messages.

    Uses the locale abbreviation when available (e.g. ``EST``), otherwise
    the last segment of the IANA name (e.g. ``New_York``).
    """
    current = now()
    abbrev = current.strftime("%Z")
    if abbrev:
        return abbrev
    return get_timezone().key.split("/")[-1].replace("_", " ")


def format_date_long() -> str:
    """Cross-platform long date string for prompts (e.g. ``Monday, July 20, 2026``)."""
    current = now()
    # Avoid %-d / %#d — those are platform-specific strftime extensions.
    day = str(current.day)
    return current.strftime(f"%A, %B {day}, %Y")
