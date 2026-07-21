"""
Obsidian source: reads recently modified markdown files from specified vault folders.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import timeutil

logger = logging.getLogger(__name__)


class ObsidianSource:
    def __init__(self, config: dict):
        self.vault_path = Path(os.path.expanduser(config["vault_path"]))
        self.watch_folders = config.get("watch_folders", ["inbox", "daily"])
        self.recency_minutes = config.get("recency_minutes", 65)
        # Always exclude the agent's own memory folder to prevent feedback loops
        memory_folder = config.get("memory_folder", "agent-memory")
        self.exclude_folders = {memory_folder}

    def fetch(self, since: datetime) -> list[dict]:
        """
        Find markdown files modified since the given timestamp.
        Returns list of dicts: {source, filename, path, content, modified_at}
        """
        notes = []

        for folder_name in self.watch_folders:
            folder = self.vault_path / folder_name
            if not folder.exists():
                logger.warning("Obsidian watch folder does not exist: %s", folder)
                continue

            for md_file in folder.rglob("*.md"):
                # Skip files inside excluded folders
                rel = md_file.relative_to(self.vault_path)
                if any(part in self.exclude_folders for part in rel.parts):
                    continue

                try:
                    mtime = datetime.fromtimestamp(
                        md_file.stat().st_mtime, tz=timeutil.get_timezone()
                    )
                    if mtime <= since:
                        continue

                    content = md_file.read_text(encoding="utf-8", errors="replace")

                    # Skip very short files (likely empty templates)
                    if len(content.strip()) < 10:
                        continue

                    notes.append(
                        {
                            "source": "obsidian",
                            "filename": md_file.name,
                            "path": str(md_file.relative_to(self.vault_path)),
                            "content": content,  # full content; long notes are summarized by gpt_processor
                            "modified_at": mtime.isoformat(),
                        }
                    )
                except Exception as e:
                    logger.warning("Failed to read %s: %s", md_file, e)

        notes.sort(key=lambda n: n["modified_at"])
        logger.info("Found %d modified notes since %s", len(notes), since.isoformat())
        return notes
