"""
Writes tasks to an Obsidian markdown file in the agent-memory folder.
The DB is the source of truth. This module renders DB rows to markdown
and reads user signals ([x] checkoffs) back from markdown.
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import timeutil

logger = logging.getLogger(__name__)

PRIORITY_EMOJI = {
    "high": "🔴",
    "medium": "🟡",
    "low": "🔵",
}


class TaskOutput:
    def __init__(self, config: dict):
        vault = os.path.expanduser(config["vault_path"])
        memory_folder = config.get("memory_folder", "agent-memory")
        self.tasks_path = Path(vault) / memory_folder / "tasks.md"
        self.tasks_path.parent.mkdir(parents=True, exist_ok=True)

    def _format_task(self, task: dict) -> str:
        priority = task.get("priority", "medium")
        emoji = PRIORITY_EMOJI.get(priority, "⚪")
        title = task.get("title", "Untitled")
        context = task.get("context", "")
        why = task.get("why", "")
        response = task.get("suggested_response")
        route_to = task.get("route_to")
        created_at = task.get("created_at", "")
        last_seen_at = task.get("last_seen_at", "")

        # Normalize timestamps to date+time for display
        def _fmt(ts):
            if not ts:
                return ""
            try:
                return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
            except Exception:
                return ts

        sources = json.loads(task["sources"]) if isinstance(task.get("sources"), str) else task.get("sources") or []
        links = json.loads(task["links"]) if isinstance(task.get("links"), str) else task.get("links") or []

        lines = [f"- [ ] {emoji} **{title}**"]
        lines.append(f"  - ID: {task['id']}")
        lines.append(f"  - First seen: {_fmt(created_at)} · Last seen: {_fmt(last_seen_at)}")
        if why:
            lines.append(f"  - Why: {why}")
        if sources:
            lines.append(f"  - Sources: {', '.join(sources)}")
        for link in links:
            if link:
                lines.append(f"  - [Open]({link})")
        if context:
            lines.append(f"  - Context: {context}")
        if route_to:
            lines.append(f"  - Route to: {route_to}")
        if response:
            lines.append(f"  - Suggested response: {response}")

        return "\n".join(lines)

    def get_checked_titles(self) -> list[str]:
        """Parse tasks.md for checked-off items. Returns list of titles."""
        if not self.tasks_path.exists():
            return []
        try:
            content = self.tasks_path.read_text(encoding="utf-8")
            titles = []
            for line in content.splitlines():
                match = re.match(r"^-\s*\[x\]\s*[^\s]*\s*\*\*(.+?)\*\*", line, re.IGNORECASE)
                if match:
                    titles.append(match.group(1).strip())
            return titles
        except Exception:
            return []

    def write_from_db(self, tasks: list[dict]) -> int:
        """Render open tasks from DB rows to tasks.md. Primary write path."""
        now = timeutil.now().strftime(f"%Y-%m-%d %H:%M {timeutil.tz_label()}")
        lines = ["# Active Tasks", f"*Last updated: {now}*\n"]

        if not tasks:
            lines.append("No active items.\n")
        else:
            order = {"high": 0, "medium": 1, "low": 2}
            sorted_tasks = sorted(tasks, key=lambda t: order.get(t.get("priority", "medium"), 1))
            for task in sorted_tasks:
                lines.append(self._format_task(task))
                lines.append("")

        self.tasks_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Wrote %d tasks to %s", len(tasks), self.tasks_path)
        return len(tasks)
