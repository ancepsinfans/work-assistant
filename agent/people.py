"""
Loads the people directory and provides lookup functions.
Used by heartbeat to expand nicknames into searchable names/emails.
"""

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class PeopleDirectory:
    def __init__(self, config_dir: Path = None):
        if config_dir is None:
            config_dir = Path(__file__).resolve().parent
        self.path = config_dir / "people.yaml"
        self._people = []
        self._by_nickname = {}
        self._by_name = {}
        self._by_slack_id = {}
        self._by_email = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            logger.warning("people.yaml not found at %s", self.path)
            return

        try:
            with open(self.path) as f:
                data = yaml.safe_load(f)
            self._people = data.get("people", [])

            for p in self._people:
                nick = p.get("nickname", "").lower()
                name = p.get("name", "").lower()
                sid = p.get("slack_id", "")
                email = p.get("email", "").lower()

                if nick:
                    self._by_nickname[nick] = p
                if name:
                    self._by_name[name] = p
                if sid:
                    self._by_slack_id[sid] = p
                if email:
                    self._by_email[email] = p

            logger.info("Loaded %d people from directory", len(self._people))
        except Exception as e:
            logger.error("Failed to load people.yaml: %s", e)

    def resolve(self, text: str) -> dict | None:
        """Look up a person by nickname, full name, email, or Slack ID.
        Email lookup matters for calendar attendees in particular — Google
        Calendar frequently reports attendees by raw email with no display
        name set, and that's the only field available to match against."""
        key = text.strip().lower()
        return (
            self._by_nickname.get(key)
            or self._by_name.get(key)
            or self._by_email.get(key)
            or self._by_slack_id.get(text.strip())
        )

    def expand_for_search(self, text: str) -> list[str]:
        """Given a nickname or name, return all searchable variants.
        E.g., 'Alex' -> ['Alex Chen', 'Alex', 'alex.chen@example.com']"""
        person = self.resolve(text)
        if not person:
            return [text]

        variants = [person.get("name", ""), person.get("nickname", "")]
        email = person.get("email", "")
        if email:
            variants.append(email)

        return [v for v in variants if v]

    def expand_query(self, query: str) -> str:
        """Expand any recognized nicknames in a search query to full names."""
        words = query.split()
        expanded = []
        for word in words:
            person = self.resolve(word)
            if person:
                expanded.append(person.get("name", word))
            else:
                expanded.append(word)
        return " ".join(expanded)

    def get_context_block(self) -> str:
        """Format the directory as context for the GPT prompt."""
        if not self._people:
            return ""
        from datetime import date as _date
        today = _date.today()
        lines = ["Key people:"]
        for p in self._people:
            name = p.get("name", "")
            nick = p.get("nickname", "")
            role = p.get("role", "")
            team = p.get("team", "")
            parts = [name]
            if nick and nick.lower() != name.lower():
                parts[0] = f"{name} ({nick})"
            if role:
                parts.append(role)
            if team:
                parts.append(team)
            line = f"- {', '.join(parts)}"

            trajectory = p.get("trajectory")
            if trajectory:
                if isinstance(trajectory, dict):
                    traj_text = trajectory.get("text", "")
                    basis = trajectory.get("basis", "")
                    confidence = trajectory.get("confidence", "")
                    last_updated_str = trajectory.get("last_updated", "")

                    stale_note = ""
                    if last_updated_str:
                        try:
                            last_updated = _date.fromisoformat(last_updated_str)
                            if (today - last_updated).days > 14:
                                stale_note = f" [STALE - not updated since {last_updated_str}]"
                        except ValueError:
                            pass

                    meta = []
                    if basis:
                        meta.append(f"basis:{basis}")
                    if confidence:
                        meta.append(f"confidence:{confidence}")
                    meta_str = f" ({', '.join(meta)})" if meta else ""
                    line += f"\n  Trajectory: {traj_text}{meta_str}{stale_note}"
                else:
                    line += f"\n  Trajectory: {trajectory}"

            lines.append(line)
        return "\n".join(lines)
