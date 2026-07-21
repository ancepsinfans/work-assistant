"""
Memory system backed by Obsidian markdown files.

Files live in a designated vault folder (e.g., vault/agent-memory/):
  - daily.md      → updated every run
  - weekly.md     → updated on the last run of each day (4:30 PM)
  - sprintly.md   → updated on Friday's last run
  - monthly.md    → updated on the last Friday of each month
  - quarterly.md  → updated on the last Friday of each quarter

Synthesis happens at the END of each cycle so that the next cycle
starts with a fresh summary already waiting.
"""

import logging
import os
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import timeutil

logger = logging.getLogger(__name__)

TIERS = ["daily", "weekly", "sprintly", "monthly", "quarterly"]

# Fallback staleness thresholds: if a tier hasn't been written in this long, refresh
# it on the next run regardless of the natural trigger window. Without this, a single
# missed run (VPN down, laptop asleep through all four scheduled times, GPT error) at
# exactly the trigger moment silently stalls that tier until the next matching
# calendar window — a missed Friday means a full week of silence, a missed last-Friday-
# of-month means a full month. This makes the miss self-heal on the next successful run.
STALE_AFTER = {
    "weekly": timedelta(hours=36),
    "sprintly": timedelta(days=9),
    "monthly": timedelta(days=35),
    "quarterly": timedelta(days=100),
}


def _is_last_run_of_day(now: datetime) -> bool:
    """True if current hour is 16 or later (catches the 4:30 PM run)."""
    return now.hour >= 16


def _is_friday(now: datetime) -> bool:
    """True if today is Friday (weekday 4)."""
    return now.weekday() == 4


def _is_last_friday_of_month(now: datetime) -> bool:
    """True if today is Friday and no more Fridays remain this month."""
    if not _is_friday(now):
        return False
    next_friday = now + timedelta(days=7)
    return next_friday.month != now.month


def _is_last_friday_of_quarter(now: datetime) -> bool:
    """True if this is the last Friday of a quarter-ending month (Mar, Jun, Sep, Dec)."""
    if not _is_last_friday_of_month(now):
        return False
    return now.month in (3, 6, 9, 12)


class Memory:
    def __init__(self, config: dict):
        vault = os.path.expanduser(config["vault_path"])
        self.memory_dir = Path(vault) / config.get("memory_folder", "agent-memory")
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Ensure all files exist with sensible defaults
        for tier in TIERS:
            path = self._path(tier)
            if not path.exists():
                path.write_text(self._default_content(tier), encoding="utf-8")
                logger.info("Created memory file: %s", path)

    def _path(self, tier: str) -> Path:
        return self.memory_dir / f"{tier}.md"

    def _default_content(self, tier: str) -> str:
        return (
            f"# {tier.capitalize()} Memory\n\n"
            f"*Auto-managed by inbox agent. Last updated: never*\n\n"
            f"No context yet.\n"
        )

    def load_all(self) -> dict[str, str]:
        """Load all memory tiers. Returns dict of tier_name -> content."""
        memories = {}
        for tier in TIERS:
            path = self._path(tier)
            try:
                memories[tier] = path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to read %s memory: %s", tier, e)
                memories[tier] = f"(failed to load {tier} memory)"
        return memories

    def load_tasks(self) -> str:
        """Load current tasks.md content so GPT can see what was previously surfaced."""
        path = self.memory_dir / "tasks.md"
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read tasks.md: %s", e)
            return ""

    def _tier_age(self, tier: str, now: datetime) -> timedelta | None:
        """Time since this tier's file was last written, or None if it's never existed."""
        path = self._path(tier)
        if not path.exists():
            return None
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
        return now - mtime

    def tiers_due_for_update(self) -> list[str]:
        """
        Determine which memory tiers should be updated this run.
        Synthesis happens at the END of each cycle:
          - daily:     every run
          - weekly:    last run of the day (4:30 PM)
          - sprintly:  Friday last run
          - monthly:   last Friday of the month, last run
          - quarterly: last Friday of the quarter, last run

        Each tier also fires if it's simply overdue (see STALE_AFTER), independent of
        the natural trigger window, so a missed run doesn't stall a tier indefinitely.
        """
        now = timeutil.now()

        due = ["daily"]  # always

        natural = {
            "weekly": _is_last_run_of_day(now),
            "sprintly": _is_last_run_of_day(now) and _is_friday(now),
            "monthly": _is_last_run_of_day(now) and _is_last_friday_of_month(now),
            "quarterly": _is_last_run_of_day(now) and _is_last_friday_of_quarter(now),
        }

        for tier in ["weekly", "sprintly", "monthly", "quarterly"]:
            if natural[tier]:
                due.append(tier)
                continue
            age = self._tier_age(tier, now)
            if age is not None and age > STALE_AFTER[tier]:
                due.append(tier)
                logger.info(
                    "%s memory is %s old (past the %s catch-up threshold) — "
                    "refreshing to recover from a missed window",
                    tier, age, STALE_AFTER[tier],
                )

        logger.info(
            "Memory tiers due for update: %s (local time: %s)",
            due,
            now.strftime("%A %H:%M"),
        )
        return due

    def write_tier(self, tier: str, content: str):
        """Write updated content back to a memory file."""
        path = self._path(tier)
        try:
            path.write_text(content, encoding="utf-8")
            logger.info("Updated %s memory (%d chars)", tier, len(content))
        except Exception as e:
            logger.error("Failed to write %s memory: %s", tier, e)

    def write_updates(self, updates: dict[str, str]):
        """Write multiple tier updates. Keys are tier names, values are new content."""
        for tier, content in updates.items():
            if tier in TIERS and content:
                self.write_tier(tier, content)
