#!/usr/bin/env python3
"""
Work Assistant: polls Slack, Gmail, Obsidian, JIRA, Confluence, and Google Meet
transcripts on a schedule, loads rolling memory context, sends everything
to the configured LLM backend, writes tasks, memory updates, experiment
results, and decisions back to Obsidian.

Usage:
  python main.py                  # single run
  python main.py --dry-run        # fetch + process, print results, don't write anything
  python main.py --sources-only   # fetch only, print raw data, skip GPT
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path

import yaml

AGENT_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(AGENT_DIR))

import gpt_processor
from config_loader import get_source_config, is_source_enabled, load_config
import timeutil
from embeddings import EmbeddingStore
from heartbeat import Heartbeat
from notify import notify
from memory import Memory
from output.decisions_log import DecisionsLog
from output.experiment_log import ExperimentLog
from output.task_output import TaskOutput
from people import PeopleDirectory
from sources.confluence_source import ConfluenceSource
from sources.gmail_source import GmailSource
from sources.jira_source import JiraSource
from sources.meeting_source import MeetingSource
from sources.obsidian import ObsidianSource
from sources.slack import SlackSource
from sources.taskflow_sync import TaskFlowSource
from state import StateDB


def _add_new_slack_contacts(slack_source, yaml_path: Path, logger):
    """Auto-add any Slack senders not already in people.yaml."""
    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
        people = data.get("people", [])
        known_slack_ids = {p.get("slack_id", "") for p in people}

        new_contacts = slack_source.unknown_contacts(known_slack_ids)
        if not new_contacts:
            return

        for profile in new_contacts:
            entry = {"name": profile["name"]}
            nick = profile.get("nickname", "")
            if nick and nick.lower() != profile["name"].lower():
                entry["nickname"] = nick
            entry["slack_id"] = profile["slack_id"]
            if profile.get("email"):
                entry["email"] = profile["email"]
            if profile.get("role"):
                entry["role"] = profile["role"]
            people.append(entry)
            logger.info("Auto-added new person from Slack: %s (%s)", profile["name"], profile["slack_id"])

        data["people"] = people
        with open(yaml_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        logger.info("Added %d new people to people.yaml", len(new_contacts))
    except Exception as e:
        logger.error("Failed to add new Slack contacts: %s", e)


def _git_commit_vault(vault_path: str, logger):
    """Stage all changes in the Obsidian vault and commit with a timestamp."""
    try:
        vault = Path(vault_path).expanduser()
        msg = f"auto: agent run {timeutil.now().strftime('%Y-%m-%dT%H:%M:%S')} {timeutil.tz_label()}"
        subprocess.run(["git", "add", "-A"], cwd=vault, check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=vault, capture_output=True
        )
        if result.returncode == 0:
            logger.info("Obsidian vault: nothing to commit")
            return
        subprocess.run(["git", "commit", "-m", msg], cwd=vault, check=True, capture_output=True)
        logger.info("Obsidian vault committed: %s", msg)
    except subprocess.CalledProcessError as e:
        logger.error("Obsidian git commit failed: %s", e.stderr.decode().strip())
    except Exception as e:
        logger.error("Obsidian git commit failed: %s", e)


def _apply_people_updates(updates: list[dict], yaml_path: Path, logger):
    """Write trajectory updates from GPT back to people.yaml."""
    if not updates:
        return
    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        people = data.get("people", [])
        updated = 0
        for update in updates:
            name = update.get("name", "").strip().lower()
            trajectory = update.get("trajectory")
            if not name or not trajectory:
                continue
            for person in people:
                if person.get("name", "").strip().lower() == name:
                    person["trajectory"] = trajectory
                    updated += 1
                    break
        if updated:
            with open(yaml_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            logger.info("Updated %d people trajectories in people.yaml", updated)
    except Exception as e:
        logger.error("Failed to apply people updates: %s", e)


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


def run(dry_run: bool = False, sources_only: bool = False, force_tiers: list[str] = None):
    config = load_config()
    timeutil.configure(config)
    setup_logging(config.get("agent", {}).get("log_file"))
    logger = logging.getLogger("agent")

    state = StateDB(config.get("agent", {}).get("state_db", "~/.config/agent/state.db"))
    embedding_store = EmbeddingStore(config.get("agent", {}).get("state_db", "~/.config/agent/state.db"))

    logger.info("=== Agent run started ===")

    # ── Load memory ──
    obsidian_config = get_source_config(config, "obsidian")
    memory = Memory(obsidian_config)
    memories = memory.load_all()
    open_tasks = state.get_open_tasks()
    if force_tiers:
        # Only update the explicitly requested tiers — don't let daily get clobbered
        # as a side effect of a mid-day backfill run.
        tiers_due = list(dict.fromkeys(force_tiers))
        logger.info("Force-tiers mode: updating only %s", tiers_due)
    else:
        tiers_due = memory.tiers_due_for_update()
    logger.info("Memory loaded. Tiers due for update: %s", tiers_due)

    # ── Load knowledge logs ──
    experiment_log = ExperimentLog(obsidian_config, embedding_store=embedding_store)
    decisions_log = DecisionsLog(obsidian_config, embedding_store=embedding_store)
    experiment_context = experiment_log.get_context()
    decisions_context = decisions_log.get_context()

    # Load decay-sweep candidates (scoped entries only; standing policy never decays)
    stale_decisions = decisions_log.get_decay_candidates(10) if "sprintly" in tiers_due else []
    if stale_decisions:
        logger.info("Sprint boundary: loaded %d oldest decisions for decay sweep", len(stale_decisions))

    # ── Fetch from sources ──
    slack_msgs = []
    emails = []
    notes = []
    jira_tickets = []
    confluence_pages = []
    meetings = []

    slack = None
    gmail = None
    jira = None
    confluence = None
    meeting_source = None

    slack_conversations = None
    if is_source_enabled(config, "slack"):
        try:
            slack = SlackSource(get_source_config(config, "slack"))
            since = state.get_last_checked("slack")
            slack_msgs = slack.fetch(since)
            if slack_msgs:
                slack_conversations, slack_synth_usage = gpt_processor.synthesize_slack(
                    slack_msgs, config
                )
                state.record_run_usage(
                    "slack_synthesize",
                    slack_synth_usage["chars_in"],
                    slack_synth_usage["chars_out"],
                    slack_synth_usage["duration_ms"],
                )
        except Exception as e:
            logger.error("Slack fetch failed: %s", e)

    if is_source_enabled(config, "gmail"):
        try:
            gmail = GmailSource(get_source_config(config, "gmail"))
            since = state.get_last_checked("gmail")
            emails = gmail.fetch(since)
        except Exception as e:
            logger.error("Gmail fetch failed: %s", e)

    if is_source_enabled(config, "obsidian"):
        try:
            obsidian = ObsidianSource(obsidian_config)
            since = state.get_last_checked("obsidian")
            notes = obsidian.fetch(since)
            if notes:
                notes, summarize_usage = gpt_processor.summarize_notes(notes, config)
                state.record_run_usage(
                    "obsidian_summarize",
                    summarize_usage["chars_in"],
                    summarize_usage["chars_out"],
                    summarize_usage["duration_ms"],
                )
        except Exception as e:
            logger.error("Obsidian fetch failed: %s", e)

    if is_source_enabled(config, "jira"):
        try:
            jira = JiraSource(get_source_config(config, "jira"))
            since = state.get_last_checked("jira")
            jira_tickets = jira.fetch(since)
        except Exception as e:
            logger.error("JIRA fetch failed: %s", e)

    if is_source_enabled(config, "confluence"):
        try:
            confluence = ConfluenceSource(get_source_config(config, "confluence"))
            since = state.get_last_checked("confluence")
            confluence_pages = confluence.fetch(since)
        except Exception as e:
            logger.error("Confluence fetch failed: %s", e)

    if is_source_enabled(config, "meetings"):
        try:
            meeting_source = MeetingSource(get_source_config(config, "meetings"))
            since = state.get_last_checked("meetings")
            meetings = meeting_source.fetch(since)
            if meetings:
                meetings, meeting_synth_usage = gpt_processor.synthesize_meeting_docs(
                    meetings, config
                )
                state.record_run_usage(
                    "meeting_synthesize",
                    meeting_synth_usage["chars_in"],
                    meeting_synth_usage["chars_out"],
                    meeting_synth_usage["duration_ms"],
                )
        except Exception as e:
            logger.error("Meetings fetch failed: %s", e)

    taskflow_tasks = []
    if is_source_enabled(config, "taskflow"):
        try:
            taskflow = TaskFlowSource(get_source_config(config, "taskflow"))
            taskflow_tasks = taskflow.fetch_and_clear()
        except Exception as e:
            logger.error("TaskFlow fetch failed: %s", e)

    # ── Auto-add new Slack contacts before loading people directory ──
    if slack:
        _add_new_slack_contacts(slack, AGENT_DIR / "people.yaml", logger)

    # ── Heartbeat: investigate standing questions ──
    heartbeat_results = []
    people = PeopleDirectory(AGENT_DIR)
    try:
        hb = Heartbeat(obsidian_config)
        questions = hb.load_questions()
        if questions:
            heartbeat_results = hb.investigate(
                questions, slack, gmail, jira, confluence, meeting_source, people=people,
                embedding_store=embedding_store,
            )
    except Exception as e:
        logger.error("Heartbeat failed: %s", e)

    total_items = (
        len(slack_msgs)
        + len(emails)
        + len(notes)
        + len(jira_tickets)
        + len(confluence_pages)
        + len(meetings)
    )
    logger.info(
        "Collected: %d slack, %d emails, %d notes, %d jira, %d confluence, %d meetings, %d heartbeat questions",
        len(slack_msgs),
        len(emails),
        len(notes),
        len(jira_tickets),
        len(confluence_pages),
        len(meetings),
        len(heartbeat_results),
    )

    if sources_only:
        print(
            json.dumps(
                {
                    "slack": slack_msgs,
                    "gmail": emails,
                    "obsidian": notes,
                    "jira": jira_tickets,
                    "confluence": confluence_pages,
                    "meetings": meetings,
                    "heartbeat": heartbeat_results,
                    "memory": {k: v[:200] + "..." for k, v in memories.items()},
                },
                indent=2,
                default=str,
            )
        )
        return

    if total_items == 0 and not tiers_due and not heartbeat_results:
        logger.info(
            "Nothing new, no heartbeat, no memory updates due. Skipping GPT call."
        )
        state.log_run("empty", 0)
        return

    # ── Process with GPT ──
    task_output = TaskOutput(obsidian_config)

    # ── Apply user resolutions from markdown before GPT runs ──
    resolved_titles = task_output.get_checked_titles()
    if resolved_titles:
        logger.info("User resolved %d tasks: %s", len(resolved_titles), resolved_titles)
        for title in resolved_titles:
            state.resolve_task_by_title(title)
        state.persist_resolved_tasks(resolved_titles)
        # Refresh open tasks after applying resolutions
        open_tasks = state.get_open_tasks()
    historical_resolved = state.get_recent_resolved(days=7)
    all_resolved_titles = list(set(resolved_titles + historical_resolved))

    # Snapshot of what was already open before this run's reconciliation, so we
    # can tell a genuinely new high-priority task from one just carried forward.
    pre_run_task_ids = {t["id"] for t in open_tasks}

    close_rate_summary = state.get_close_rate_summary()

    try:
        result = gpt_processor.process(
            slack_msgs,
            emails,
            notes,
            jira_tickets,
            confluence_pages,
            meetings,
            heartbeat_results,
            memories,
            open_tasks,
            all_resolved_titles,
            tiers_due,
            people,
            experiment_context,
            decisions_context,
            config,
            close_rate_summary=close_rate_summary,
            stale_decisions_for_review=stale_decisions,
            slack_conversations=slack_conversations,
            embedding_store=embedding_store,
        )
    except Exception as e:
        logger.error("GPT processing failed: %s", e)
        state.log_run("error", error=str(e))
        return

    tasks = result.get("tasks", [])
    memory_updates = result.get("memory_updates", {})
    experiment_entries = result.get("experiment_entries", [])
    decision_entries = result.get("decision_entries", [])
    people_updates = result.get("people_updates", [])
    main_usage = result.get("usage", {})

    # Record main pass usage
    state.record_run_usage(
        "main",
        main_usage.get("chars_in", 0),
        main_usage.get("chars_out", 0),
        main_usage.get("duration_ms", 0),
    )

    # ── Absence sweep (second pass) ──
    slack_senders = [m.get("sender", "") for m in slack_msgs if m.get("sender")]
    email_senders = [e.get("from", "").split("<")[0].strip() for e in emails if e.get("from")]
    meeting_attendees = [
        a for m in meetings for a in m.get("attendees", [])
    ]
    people_context_for_sweep = people.get_context_block() if people else ""

    try:
        absence_result = gpt_processor.run_absence_sweep(
            tasks,
            people_context_for_sweep,
            jira_tickets,
            slack_senders,
            email_senders,
            meeting_attendees,
            config,
            resolved_titles=all_resolved_titles,
        )
        absence_tasks = absence_result.get("tasks", [])
        absence_usage = absence_result.get("usage", {})
        state.record_run_usage(
            "absence_sweep",
            absence_usage.get("chars_in", 0),
            absence_usage.get("chars_out", 0),
            absence_usage.get("duration_ms", 0),
        )
        if absence_tasks:
            logger.info("Absence sweep added %d tasks", len(absence_tasks))
            tasks = tasks + absence_tasks
    except Exception as e:
        logger.error("Absence sweep failed: %s", e)

    logger.info(
        "GPT identified %d tasks, %d memory updates, %d experiments, %d decisions",
        len(tasks),
        len(memory_updates),
        len(experiment_entries),
        len(decision_entries),
    )

    if dry_run:
        print("\n=== DRY RUN: Tasks ===")
        print(json.dumps(tasks, indent=2))
        print("\n=== DRY RUN: Memory updates ===")
        for tier, content in memory_updates.items():
            if content:
                print(f"\n--- {tier}.md ---")
                print(content[:500])
        if experiment_entries:
            print("\n=== DRY RUN: New experiment entries ===")
            print(json.dumps(experiment_entries, indent=2))
        if decision_entries:
            print("\n=== DRY RUN: New decision entries ===")
            print(json.dumps(decision_entries, indent=2))
        if people_updates:
            print("\n=== DRY RUN: People trajectory updates ===")
            print(json.dumps(people_updates, indent=2))
        print(f"\n=== DRY RUN: Close rate summary ===\n{close_rate_summary}")
        state.log_run("dry_run", len(tasks))
        return

    # ── Write memory updates ──
    if memory_updates:
        memory.write_updates(memory_updates)

    # ── Reconcile tasks into DB ──
    all_gpt_tasks = tasks + (taskflow_tasks if taskflow_tasks else [])
    if taskflow_tasks:
        logger.info("Merged %d TaskFlow tasks", len(taskflow_tasks))

    seen_ids = set()
    for task in all_gpt_tasks:
        task_id = task.get("id")
        if not task_id:
            sources = task.get("sources", [])
            title = task.get("title", "")
            if "absence-detection" in sources or title.startswith("[ABSENCE]"):
                # Stable ID so the same absence tracks to one DB row and stays resolved once dismissed
                task_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, title.lower().strip()))
            else:
                task_id = str(uuid.uuid4())
            task["id"] = task_id
        seen_ids.add(task_id)
        state.upsert_task(task)

    state.mark_tasks_stale(seen_ids)

    final_open_tasks = state.get_open_tasks()
    written = task_output.write_from_db(final_open_tasks)
    logger.info("Wrote %d tasks to Obsidian", written)

    # ── Notify on genuinely new high-priority tasks (includes absence-sweep hits —
    # those are just tasks with priority:high by the time they get here) ──
    try:
        new_high_priority = [
            t for t in final_open_tasks
            if t["id"] not in pre_run_task_ids and t.get("priority") == "high"
        ]
        if new_high_priority:
            if len(new_high_priority) == 1:
                notify("New high-priority task", new_high_priority[0]["title"])
            else:
                titles = "; ".join(t["title"] for t in new_high_priority[:3])
                if len(new_high_priority) > 3:
                    titles += f" (+{len(new_high_priority) - 3} more)"
                notify(f"{len(new_high_priority)} new high-priority tasks", titles)
            logger.info("Notified on %d new high-priority task(s)", len(new_high_priority))
    except Exception as e:
        logger.error("Task notification failed: %s", e)

    # ── Record task close rate ──
    state.record_task_metrics(tasks_open=len(final_open_tasks), tasks_resolved=len(resolved_titles))

    # ── Write experiment entries ──
    if experiment_entries:
        exp_written = experiment_log.write_entries(experiment_entries)
        logger.info("Wrote %d experiment entries to Obsidian", exp_written)

    # ── Write decision entries ──
    if decision_entries:
        dec_written = decisions_log.write_entries(decision_entries)
        logger.info("Wrote %d decision entries to Obsidian", dec_written)

    # ── Apply people trajectory updates ──
    if people_updates:
        _apply_people_updates(people_updates, AGENT_DIR / "people.yaml", logger)

    # ── Advance checkpoints only after successful write ──
    state.set_last_checked("slack")
    state.set_last_checked("gmail")
    state.set_last_checked("obsidian")
    state.set_last_checked("jira")
    state.set_last_checked("confluence")
    state.set_last_checked("meetings")
    state.log_run("success", written)

    # ── Commit Obsidian vault ──
    _git_commit_vault(obsidian_config["vault_path"], logger)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inbox monitoring agent")
    parser.add_argument(
        "--dry-run", action="store_true", help="Process but don't write tasks or memory"
    )
    parser.add_argument(
        "--sources-only", action="store_true", help="Fetch sources only, skip GPT"
    )
    parser.add_argument(
        "--force-tiers",
        metavar="TIERS",
        help="Comma-separated tiers to force-update regardless of schedule (e.g. monthly,sprintly)",
    )
    args = parser.parse_args()

    force_tiers = [t.strip() for t in args.force_tiers.split(",")] if args.force_tiers else None
    run(dry_run=args.dry_run, sources_only=args.sources_only, force_tiers=force_tiers)
