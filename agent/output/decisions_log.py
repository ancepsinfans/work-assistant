"""
Writes decisions and commitments to an Obsidian markdown file.
APPEND-ONLY: new entries are added when detected; old entries persist.

The GPT processor identifies decisions from any source (meetings, Slack,
email, JIRA comments) and returns structured entries. This module
deduplicates by a hash of decision + date and appends new ones.

The decisions log captures:
- Explicit decisions ("we agreed to X")
- Commitments with owners and deadlines ("Alex will deliver by Friday")
- Strategic direction changes ("Indy said SUMM is getting CEO attention")
- Priority shifts ("Education deprioritized for Q2")

Unlike memory tiers (which compress over time), this log is permanent.
When a decision is reversed or superseded, a NEW entry is added noting
the change, preserving the full decision history.
"""

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import timeutil

logger = logging.getLogger(__name__)

DECISION_TYPE_LABELS = {
    "decision": "📋",
    "commitment": "🤝",
    "direction": "🧭",
    "priority": "⚖️",
    "escalation": "🔺",
    "reversal": "🔄",
}


class DecisionsLog:
    def __init__(self, config: dict, embedding_store=None):
        vault = os.path.expanduser(config["vault_path"])
        memory_folder = config.get("memory_folder", "agent-memory")
        self.log_path = Path(vault) / memory_folder / "decisions.md"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.embeddings = embedding_store

        if not self.log_path.exists():
            self.log_path.write_text(
                "# Decisions Log\n\n"
                "Persistent record of decisions, commitments, and direction changes.\n"
                "Auto-maintained by inbox agent. Manual entries welcome.\n\n"
                "---\n\n",
                encoding="utf-8",
            )
            logger.info("Created decisions log: %s", self.log_path)

    def _parse_existing_hashes(self) -> set[str]:
        """Extract decision hashes already in the log to prevent duplicates."""
        content = self.log_path.read_text(encoding="utf-8")
        hashes = set()
        for match in re.finditer(r"<!-- hash:(\w+) -->", content):
            hashes.add(match.group(1))
        return hashes

    @staticmethod
    def _hash_decision(decision: str, date: str) -> str:
        """Short hash for deduplication. Based on decision text + date."""
        raw = f"{decision.strip().lower()}|{date}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def _format_entry(self, entry: dict) -> str:
        decision = entry.get("decision", "")
        decision_type = entry.get("type", "decision")
        emoji = DECISION_TYPE_LABELS.get(decision_type, "📋")
        date = entry.get(
            "date", timeutil.now().strftime("%Y-%m-%d")
        )
        # Default to "scoped" (conservative bias): entries only count as "standing"
        # when the model is confident it's a durable policy/rule with no natural expiry.
        durability = entry.get("durability", "scoped")
        if durability not in ("standing", "scoped"):
            durability = "scoped"
        context_source = entry.get("context_source", "")
        who_decided = entry.get("who_decided", "")
        who_owns = entry.get("who_owns", "")
        deadline = entry.get("deadline", "")
        stakeholders = entry.get("stakeholders", [])
        rationale = entry.get("rationale", "")
        implications = entry.get("implications", "")
        sources = entry.get("sources", [])
        links = entry.get("links", [])

        entry_hash = self._hash_decision(decision, date)

        lines = [f"{emoji} **{decision}**"]
        lines.append(f"<!-- hash:{entry_hash} -->")
        lines.append(f"- **Date:** {date}")
        lines.append(f"- **Type:** {decision_type}")
        lines.append(f"- **Durability:** {durability}")
        if context_source:
            lines.append(f"- **Where:** {context_source}")
        if who_decided:
            lines.append(f"- **Decided by:** {who_decided}")
        if who_owns:
            lines.append(f"- **Owner:** {who_owns}")
        if deadline:
            lines.append(f"- **Deadline:** {deadline}")
        if stakeholders:
            lines.append(f"- **Stakeholders:** {', '.join(stakeholders)}")
        if rationale:
            lines.append(f"- **Rationale:** {rationale}")
        if implications:
            lines.append(f"- **Implications:** {implications}")
        if sources:
            lines.append(f"- **Sources:** {', '.join(sources)}")
        for link in links:
            if link:
                lines.append(f"- [Source]({link})")

        lines.append("")
        lines.append("---")
        lines.append("")

        return "\n".join(lines)

    def _parse_entries(self) -> list[dict]:
        """Parse all logged entries into structured dicts, in file order (oldest first)."""
        content = self.log_path.read_text(encoding="utf-8")
        blocks = content.split("\n---\n")
        entries = []
        for block in blocks:
            m_hash = re.search(r"<!-- hash:(\w+) -->", block)
            m_date = re.search(r"\*\*Date:\*\*\s*(\d{4}-\d{2}-\d{2})", block)
            m_decision = re.search(r"\*\*(.+?)\*\*\n<!-- hash:", block)
            if not (m_hash and m_date and m_decision):
                continue

            def field(name):
                fm = re.search(rf"\*\*{name}:\*\*\s*(.+)", block)
                return fm.group(1).strip() if fm else ""

            durability = field("Durability") or "scoped"
            if durability not in ("standing", "scoped"):
                durability = "scoped"

            entries.append({
                "hash": m_hash.group(1),
                "decision": m_decision.group(1).strip(),
                "date": m_date.group(1),
                "type": field("Type"),
                "durability": durability,
                "deadline": field("Deadline"),
                "rationale": field("Rationale"),
                "implications": field("Implications"),
                "block": block.strip("\n"),
            })
        return entries

    def get_context(self, max_chars: int = 6000) -> str:
        """Load the decisions log for inclusion in the GPT prompt.

        Standing entries (durable policy, no natural expiry) are always included,
        rendered compactly (rule + date only) so the model never re-logs a policy
        just because it scrolled out of a recency window — full detail for standing
        entries still lives in the file, just not repeated in every prompt. Whatever
        budget remains goes to the most recent scoped entries, in full.
        """
        try:
            entries = self._parse_entries()
        except Exception:
            return ""
        if not entries:
            return ""

        standing = [e for e in entries if e["durability"] == "standing"]
        scoped = [e for e in entries if e["durability"] == "scoped"]

        standing_text = ""
        if standing:
            lines = [f"- {e['decision']} (adopted {e['date']})" for e in standing]
            standing_text = "=== STANDING POLICIES (durable, always shown) ===\n" + "\n".join(lines)

        remaining = max(max_chars - len(standing_text), 500)
        scoped_text = ""
        if scoped:
            scoped_blocks = "\n---\n".join(e["block"] for e in scoped)
            if len(scoped_blocks) > remaining:
                scoped_text = (
                    "=== RECENT SCOPED DECISIONS (older ones truncated) ===\n"
                    "... [earlier scoped decisions truncated]\n\n" + scoped_blocks[-remaining:]
                )
            else:
                scoped_text = "=== SCOPED DECISIONS ===\n" + scoped_blocks

        return (standing_text + "\n\n" + scoped_text).strip()

    def get_decay_candidates(self, n: int = 10) -> list[dict]:
        """Return scoped decisions most in need of a decay-sweep review.

        Standing entries are excluded on purpose — they don't decay with age, they
        get superseded via an explicit reversal entry instead (see DECISION_TYPE_LABELS).
        Entries with a deadline that's already passed sort first (most overdue first);
        entries with no deadline fall back to oldest-logged-first.
        """
        try:
            entries = self._parse_entries()
        except Exception:
            return []

        scoped = [e for e in entries if e["durability"] == "scoped"]
        today = timeutil.now().strftime("%Y-%m-%d")

        def sort_key(e):
            deadline = e.get("deadline", "")
            if re.match(r"^\d{4}-\d{2}-\d{2}$", deadline or ""):
                return (0, deadline) if deadline < today else (2, deadline)
            return (1, e["date"])

        scoped.sort(key=sort_key)
        return [{"decision": e["decision"], "date": e["date"]} for e in scoped[:n]]

    def get_recent(self, days: int = 14, max_chars: int = 4000) -> str:
        """Load only recent decisions for tighter context windows."""
        try:
            content = self.log_path.read_text(encoding="utf-8")
            cutoff = timeutil.now().strftime("%Y-%m-")
            # Simple heuristic: return entries from the current month
            # For more precise filtering, would need to parse dates
            if len(content) <= max_chars:
                return content
            return "... [earlier decisions truncated]\n\n" + content[-max_chars:]
        except Exception:
            return ""

    def write_entries(self, entries: list[dict]) -> int:
        """Append new decision entries, skipping duplicates by hash."""
        if not entries:
            return 0

        existing_hashes = self._parse_existing_hashes()
        new_entries = []

        for entry in entries:
            decision = entry.get("decision", "")
            date = entry.get(
                "date", timeutil.now().strftime("%Y-%m-%d")
            )
            entry_hash = self._hash_decision(decision, date)

            if entry_hash not in existing_hashes:
                new_entries.append(entry)
                existing_hashes.add(entry_hash)
            else:
                logger.debug("Skipping duplicate decision: %s", decision[:50])

        if not new_entries:
            logger.info("No new decisions to log (all duplicates)")
            return 0

        content = self.log_path.read_text(encoding="utf-8")
        additions = "\n".join(self._format_entry(e) for e in new_entries)
        content = content.rstrip() + "\n\n" + additions + "\n"
        self.log_path.write_text(content, encoding="utf-8")

        logger.info(
            "Appended %d decision entries to %s", len(new_entries), self.log_path
        )

        if self.embeddings:
            try:
                items = []
                for e in new_entries:
                    decision = e.get("decision", "")
                    date = e.get(
                        "date", timeutil.now().strftime("%Y-%m-%d")
                    )
                    text = " ".join(
                        filter(None, [decision, e.get("rationale", ""), e.get("implications", "")])
                    )
                    items.append({
                        "id": self._hash_decision(decision, date),
                        "source": "decision",
                        "text": text,
                        "date": date,
                    })
                self.embeddings.upsert_many(items)
            except Exception as exc:
                logger.error("Failed to embed new decision entries: %s", exc)

        return len(new_entries)
