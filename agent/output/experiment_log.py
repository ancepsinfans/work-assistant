"""
Writes experiment results to an Obsidian markdown file in the agent-memory folder.
APPEND-ONLY: new entries are added when detected; old entries are never modified.

The GPT processor identifies concluded experiments from any source (Slack discussions,
meeting transcripts, email reports) and returns structured entries. This module
deduplicates by experiment key and appends new ones.

The experiment log serves two purposes:
1. Queryable decision record: "what have we tried on checkout?" is answerable.
2. Agent context: loaded into the GPT prompt so it can connect new signals
   to prior learnings instead of treating each test in isolation.
"""

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import timeutil

logger = logging.getLogger(__name__)


class ExperimentLog:
    def __init__(self, config: dict, embedding_store=None):
        vault = os.path.expanduser(config["vault_path"])
        memory_folder = config.get("memory_folder", "agent-memory")
        self.log_path = Path(vault) / memory_folder / "experiments.md"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.embeddings = embedding_store

        if not self.log_path.exists():
            self.log_path.write_text(
                "# Experiment Log\n\n"
                "Structured record of A/B test results and learnings.\n"
                "Auto-maintained by inbox agent. Manual entries welcome.\n\n"
                "---\n\n",
                encoding="utf-8",
            )
            logger.info("Created experiment log: %s", self.log_path)

    def _parse_existing_keys(self) -> set[str]:
        """Extract experiment keys already in the log to prevent duplicates."""
        content = self.log_path.read_text(encoding="utf-8")
        keys = set()
        for match in re.finditer(r"^###\s+(.+)$", content, re.MULTILINE):
            keys.add(match.group(1).strip().lower())
        return keys

    def _format_entry(self, entry: dict) -> str:
        key = entry.get("key", "Unknown Experiment")
        portal = entry.get("portal", "")
        section = entry.get("section", "")
        variant = entry.get("variant", "")
        result = entry.get("result", "")
        stat_sig = entry.get("stat_sig", "")
        metric_impact = entry.get("metric_impact", "")
        learning = entry.get("learning", "")
        constraints = entry.get("constraints_revealed", "")
        date_concluded = entry.get(
            "date_concluded",
            timeutil.now().strftime("%Y-%m-%d"),
        )
        sources = entry.get("sources", [])
        links = entry.get("links", [])

        lines = [f"### {key}"]
        lines.append(f"- **Date concluded:** {date_concluded}")
        if portal:
            lines.append(f"- **Portal:** {portal}")
        if section:
            lines.append(f"- **Section:** {section}")
        if variant:
            lines.append(f"- **What we tested:** {variant}")
        if result:
            lines.append(f"- **Result:** {result}")
        if stat_sig:
            lines.append(f"- **Stat sig:** {stat_sig}")
        if metric_impact:
            lines.append(f"- **Metric impact:** {metric_impact}")
        if learning:
            lines.append(f"- **Key learning:** {learning}")
        if constraints:
            lines.append(f"- **Constraints revealed:** {constraints}")
        if sources:
            lines.append(f"- **Sources:** {', '.join(sources)}")
        for link in links:
            if link:
                lines.append(f"- [Source]({link})")

        lines.append("")
        lines.append("---")
        lines.append("")

        return "\n".join(lines)

    def get_context(self, max_chars: int = 6000) -> str:
        """Load the experiment log for inclusion in the GPT prompt.

        Returns the most recent entries when the log exceeds max_chars — this was
        previously truncating from the head, which silently hid every recent result
        behind whatever was oldest in an append-only, oldest-at-top file.
        """
        try:
            content = self.log_path.read_text(encoding="utf-8")
            if len(content) <= max_chars:
                return content
            return "... [earlier experiments truncated]\n\n" + content[-max_chars:]
        except Exception:
            return ""

    def write_entries(self, entries: list[dict]) -> int:
        """Append new experiment entries, skipping duplicates by key."""
        if not entries:
            return 0

        existing_keys = self._parse_existing_keys()
        new_entries = []

        for entry in entries:
            key = entry.get("key", "").strip().lower()
            if key and key not in existing_keys:
                new_entries.append(entry)
                existing_keys.add(key)
            elif key:
                logger.debug("Skipping duplicate experiment: %s", key)

        if not new_entries:
            logger.info("No new experiments to log (all duplicates)")
            return 0

        content = self.log_path.read_text(encoding="utf-8")
        additions = "\n".join(self._format_entry(e) for e in new_entries)
        content = content.rstrip() + "\n\n" + additions + "\n"
        self.log_path.write_text(content, encoding="utf-8")

        logger.info(
            "Appended %d experiment entries to %s", len(new_entries), self.log_path
        )

        if self.embeddings:
            try:
                items = []
                for e in new_entries:
                    date = e.get(
                        "date_concluded",
                        timeutil.now().strftime("%Y-%m-%d"),
                    )
                    text = " ".join(filter(None, [
                        e.get("key", ""), e.get("portal", ""), e.get("section", ""),
                        e.get("variant", ""), e.get("learning", ""), e.get("constraints_revealed", ""),
                    ]))
                    items.append({
                        "id": e.get("key", "").strip().lower(),
                        "source": "experiment",
                        "text": text,
                        "date": date,
                    })
                self.embeddings.upsert_many(items)
            except Exception as exc:
                logger.error("Failed to embed new experiment entries: %s", exc)

        return len(new_entries)
