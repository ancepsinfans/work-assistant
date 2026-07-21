#!/usr/bin/env python3
"""
Pre-meeting briefs: polls upcoming calendar events every few minutes and, for
any real meeting starting soon that hasn't been briefed yet, assembles a note
with attendee trajectories, semantically related past decisions/experiments,
and open tasks touching those attendees — then fires a notification pointing
at it. Runs on its own frequent-polling schedule, separate from main.py's
4x/day synthesis (see com.work-assistant.meeting-brief.plist, StartInterval-based).

Usage:
  python meeting_brief.py                 # single poll, brief anything due
  python meeting_brief.py --dry-run        # print what would be briefed, write nothing
"""

import argparse
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(AGENT_DIR))

import timeutil
from config_loader import get_source_config, is_source_enabled, load_config
from embeddings import EmbeddingStore
from notify import notify
from people import PeopleDirectory
from sources.meeting_source import MeetingSource
from state import StateDB

LOOKAHEAD_MINUTES = 15


def setup_logging(log_file: str = None):
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_path = os.path.expanduser(log_file)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        handlers.append(logging.FileHandler(log_path))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def build_brief(
    meeting: dict, people: PeopleDirectory, embedding_store: EmbeddingStore, state: StateDB
) -> str:
    logger = logging.getLogger("meeting_brief")
    lines = [f"# Meeting Brief: {meeting['title']}", f"*{meeting['start']} - {meeting['end']}*", ""]

    attendee_names = [a.strip() for a in meeting.get("attendees", []) if a.strip()]

    if attendee_names:
        lines.append("## Attendees")
        for name in attendee_names:
            person = people.resolve(name)
            if person and person.get("trajectory"):
                traj = person["trajectory"]
                text = traj.get("text", "") if isinstance(traj, dict) else str(traj)
                lines.append(f"- **{person.get('name', name)}**: {text}")
            else:
                lines.append(f"- {name}")
        lines.append("")

    try:
        dec_hits = embedding_store.search(meeting["title"], k=5, source="decision")
        exp_hits = embedding_store.search(meeting["title"], k=3, source="experiment")
        relevant = sorted(
            [h for h in dec_hits + exp_hits if h["score"] >= 0.35],
            key=lambda h: -h["score"],
        )
        if relevant:
            lines.append("## Relevant past decisions & experiments")
            for h in relevant:
                lines.append(f"- [{h['date']}] {h['text']}")
            lines.append("")
    except Exception as e:
        logger.warning("Semantic search failed for '%s': %s", meeting["title"], e)

    try:
        open_tasks = state.get_open_tasks()
        first_names = [n.split()[0].lower() for n in attendee_names if n.split()]
        related_tasks = [
            t
            for t in open_tasks
            if first_names
            and any(fn in (t.get("title", "") + " " + t.get("context", "")).lower() for fn in first_names)
        ]
        if related_tasks:
            lines.append("## Open tasks touching these attendees")
            for t in related_tasks[:8]:
                lines.append(f"- [{t.get('priority', 'medium')}] {t['title']}")
            lines.append("")
    except Exception as e:
        logger.warning("Task matching failed for '%s': %s", meeting["title"], e)

    if meeting.get("meet_link"):
        lines.append(f"[Join meeting]({meeting['meet_link']})")
    lines.append(f"[Calendar event]({meeting['calendar_link']})")

    return "\n".join(lines)


def _sanitize_filename(title: str) -> str:
    return re.sub(r"[^\w\-]+", "-", title).strip("-")[:60] or "meeting"


def run(dry_run: bool = False):
    config = load_config()
    timeutil.configure(config)
    setup_logging(config.get("agent", {}).get("log_file"))
    logger = logging.getLogger("meeting_brief")

    state_db_path = config.get("agent", {}).get("state_db", "~/.config/agent/state.db")
    state = StateDB(state_db_path)
    embedding_store = EmbeddingStore(state_db_path)
    people = PeopleDirectory(AGENT_DIR)

    if not is_source_enabled(config, "meetings"):
        logger.info("Meetings source disabled in config")
        return

    obsidian_config = get_source_config(config, "obsidian")
    meetings_config = get_source_config(config, "meetings")

    try:
        meeting_source = MeetingSource(meetings_config)
        upcoming = meeting_source.fetch_upcoming(within_minutes=LOOKAHEAD_MINUTES)
    except Exception as e:
        logger.error("Failed to fetch upcoming meetings: %s", e)
        return

    due = [m for m in upcoming if m["event_id"] and not state.has_been_briefed(m["event_id"])]
    if not due:
        logger.info("No unbriefed meetings in the next %d minutes", LOOKAHEAD_MINUTES)
        return

    vault = Path(os.path.expanduser(obsidian_config["vault_path"]))
    inbox_dir = vault / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    for meeting in due:
        brief = build_brief(meeting, people, embedding_store, state)
        today = timeutil.now().strftime("%Y-%m-%d")
        filename = f"meeting-brief-{today}-{_sanitize_filename(meeting['title'])}.md"
        path = inbox_dir / filename

        if dry_run:
            print(f"\n=== Would write {path} ===")
            print(brief)
            continue

        path.write_text(brief, encoding="utf-8")
        logger.info("Wrote brief for '%s' to %s", meeting["title"], path)

        start_time = meeting["start"][11:16] if len(meeting["start"]) >= 16 else ""
        notify("Meeting brief ready", meeting["title"], subtitle=f"starts {start_time}" if start_time else "")
        state.mark_briefed(meeting["event_id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-meeting brief poller")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print briefs, write nothing, don't mark as briefed"
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
