"""
Sends aggregated source data + memory context to the configured LLM backend.
Returns structured tasks, memory updates, experiment entries, and decision entries.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import timeutil
from config_loader import get_llm_config
from llm.base import LLMError
from llm.factory import create_llm
from prompts import load_prompt

logger = logging.getLogger(__name__)

def _summarize_single_note(note: dict, config: dict) -> tuple[dict, int, int, int]:
    """Summarize one note. Returns (updated_note, chars_in, chars_out, duration_ms)."""
    llm_config = get_llm_config(config)
    prompt_prefix = load_prompt("preprocess/summarize_note", config)
    prompt = f"{prompt_prefix}\n\nNOTE: {note['filename']}\n\n{note['content']}"
    gpt = create_llm(
        llm_config,
        temperature=0.2,
        max_tokens=800,
        identifier="work-assistant-note-summary",
    )
    t0 = time.monotonic()
    summary = gpt.completion(prompt)
    duration_ms = int((time.monotonic() - t0) * 1000)
    if summary and summary.strip():
        updated = {**note, "content": summary.strip(), "summarized": True}
        return updated, len(prompt), len(summary), duration_ms
    return note, len(prompt), 0, duration_ms


def summarize_notes(
    notes: list[dict], config: dict, threshold_chars: int = 1500
) -> tuple[list[dict], dict]:
    """Summarize Obsidian notes longer than threshold_chars before the main pass.

    Long notes get an individual LLM call (run in parallel) that extracts decisions,
    action items, people context, and key background. Short notes pass through unchanged.

    Returns (updated_notes, usage_totals).
    """
    to_summarize = [n for n in notes if len(n["content"]) > threshold_chars]

    if not to_summarize:
        return notes, {"chars_in": 0, "chars_out": 0, "duration_ms": 0}

    logger.info(
        "Summarizing %d / %d Obsidian notes (threshold: %d chars)",
        len(to_summarize),
        len(notes),
        threshold_chars,
    )

    result_map: dict[str, dict] = {}
    total_chars_in = total_chars_out = total_duration_ms = 0

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_summarize_single_note, note, config): note["path"]
            for note in to_summarize
        }
        for future in as_completed(futures):
            path = futures[future]
            try:
                updated, chars_in, chars_out, duration_ms = future.result()
                result_map[path] = updated
                total_chars_in += chars_in
                total_chars_out += chars_out
                total_duration_ms += duration_ms
                logger.info(
                    "Summarized '%s': %d → %d chars (%dms)",
                    path,
                    chars_in,
                    chars_out,
                    duration_ms,
                )
            except Exception as e:
                logger.warning("Failed to summarize note '%s': %s", path, e)
                # fall back to full content for this note
                for n in to_summarize:
                    if n["path"] == path:
                        result_map[path] = n
                        break

    updated_notes = [result_map.get(n["path"], n) for n in notes]
    return updated_notes, {
        "chars_in": total_chars_in,
        "chars_out": total_chars_out,
        "duration_ms": total_duration_ms,
    }




def _synthesize_single_meeting_doc(
    doc: dict, meeting_title: str, config: dict
) -> tuple[dict, int, int, int]:
    """Synthesize one meeting document. Returns (updated_doc, chars_in, chars_out, duration_ms)."""
    llm_config = get_llm_config(config)
    content = doc.get("content", "")
    prompt_prefix = load_prompt("preprocess/meeting_synthesis", config)
    prompt = (
        f"{prompt_prefix}\n\n"
        f"MEETING: {meeting_title}\n"
        f"DOCUMENT: {doc.get('title', '(untitled)')}\n\n"
        f"{content}"
    )
    gpt = create_llm(
        llm_config,
        temperature=0.2,
        max_tokens=800,
        identifier="work-assistant-meeting-synthesis",
    )
    t0 = time.monotonic()
    summary = gpt.completion(prompt)
    duration_ms = int((time.monotonic() - t0) * 1000)

    if summary and summary.strip():
        updated = {**doc, "content": summary.strip(), "synthesized": True}
        return updated, len(content), len(summary.strip()), duration_ms
    # Fallback: truncate to 3000 chars (original behaviour) rather than passing full content
    updated = {**doc, "content": content[:3000]}
    return updated, len(content), len(content[:3000]), duration_ms


def synthesize_meeting_docs(
    meetings: list[dict], config: dict, threshold_chars: int = 1500
) -> tuple[list[dict], dict]:
    """Synthesize document content for all meetings.

    Documents shorter than threshold_chars pass through unchanged.
    Longer ones get an individual LLM call (run in parallel across all meetings).

    Returns (updated_meetings, usage_totals).
    """
    # Flatten to (meeting_idx, doc_idx, doc) for parallel processing
    jobs = [
        (mi, di, doc, meetings[mi]["title"])
        for mi, meeting in enumerate(meetings)
        for di, doc in enumerate(meeting.get("documents", []))
        if doc.get("has_content") and len(doc.get("content", "")) > threshold_chars
    ]

    if not jobs:
        return meetings, {"chars_in": 0, "chars_out": 0, "duration_ms": 0}

    logger.info(
        "Synthesizing %d meeting documents (threshold: %d chars)",
        len(jobs),
        threshold_chars,
    )

    # Deep-copy meetings so we can update in place without mutating the original
    import copy

    updated_meetings = copy.deepcopy(meetings)

    result_map: dict[tuple[int, int], dict] = {}
    total_chars_in = total_chars_out = total_duration_ms = 0

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_synthesize_single_meeting_doc, doc, title, config): (
                mi,
                di,
            )
            for mi, di, doc, title in jobs
        }
        for future in as_completed(futures):
            key = futures[future]
            mi, di = key
            try:
                updated_doc, chars_in, chars_out, duration_ms = future.result()
                result_map[key] = updated_doc
                total_chars_in += chars_in
                total_chars_out += chars_out
                total_duration_ms += duration_ms
                logger.info(
                    "Synthesized '%s' / '%s': %d → %d chars (%dms)",
                    updated_meetings[mi]["title"],
                    updated_doc.get("title", ""),
                    chars_in,
                    chars_out,
                    duration_ms,
                )
            except Exception as e:
                logger.warning(
                    "Failed to synthesize meeting doc (%d, %d): %s", mi, di, e
                )
                # Fallback: truncate to 3000 chars
                original = meetings[mi]["documents"][di]
                result_map[key] = {
                    **original,
                    "content": original.get("content", "")[:3000],
                }

    for (mi, di), updated_doc in result_map.items():
        updated_meetings[mi]["documents"][di] = updated_doc

    return updated_meetings, {
        "chars_in": total_chars_in,
        "chars_out": total_chars_out,
        "duration_ms": total_duration_ms,
    }


def _synthesize_single_conversation(
    key: str, conv_msgs: list[dict], config: dict
) -> tuple[dict, int, int, int]:
    """Synthesize one Slack conversation. Returns (conv_dict, chars_in, chars_out, duration_ms)."""
    lines = []
    for m in conv_msgs:
        ts_short = m["ts"][11:16]  # HH:MM
        lines.append(f"{m['sender']} ({ts_short}): {m['text']}")
    raw_text = "\n".join(lines)

    sample = conv_msgs[0]
    if sample["is_group_dm"]:
        label = f"[group-dm] {key}"
    elif sample["is_dm"]:
        senders = list(dict.fromkeys(m["sender"] for m in conv_msgs))
        label = f"[dm] {', '.join(senders[:3])}"
    else:
        label = f"[#{key}]"

    links = list(dict.fromkeys(m["link"] for m in conv_msgs if m.get("link")))

    llm_config = get_llm_config(config)
    prompt_prefix = load_prompt("preprocess/slack_synthesis", config)
    prompt = f"{prompt_prefix}\n\nCONVERSATION: {label}\n\n{raw_text}"
    gpt = create_llm(
        llm_config,
        temperature=0.2,
        max_tokens=400,
        identifier="work-assistant-slack-synthesis",
    )
    t0 = time.monotonic()
    summary = gpt.completion(prompt)
    duration_ms = int((time.monotonic() - t0) * 1000)

    chars_in = len(raw_text)
    if summary and summary.strip():
        return (
            {"label": label, "summary": summary.strip(), "links": links},
            chars_in,
            len(summary.strip()),
            duration_ms,
        )
    # Fallback: raw text
    return (
        {"label": label, "summary": raw_text, "links": links},
        chars_in,
        chars_in,
        duration_ms,
    )


def synthesize_slack(
    messages: list[dict], config: dict, threshold_chars: int = 200
) -> tuple[list[dict], dict]:
    """Synthesize Slack messages by grouping into conversations and summarizing each one.

    Conversations shorter than threshold_chars (raw transcript) pass through unchanged.
    Longer ones get an individual LLM call (run in parallel).

    Returns (conversations, usage_totals) where each conversation dict has:
      label, summary, links
    """
    from collections import defaultdict

    if not messages:
        return [], {"chars_in": 0, "chars_out": 0, "duration_ms": 0}

    groups: dict[str, list[dict]] = defaultdict(list)
    for m in messages:
        groups[m["channel"]].append(m)
    for key in groups:
        groups[key].sort(key=lambda m: m["ts"])

    to_synthesize = {}
    pass_throughs = {}
    for key, conv_msgs in groups.items():
        raw = "\n".join(f"{m['sender']}: {m['text']}" for m in conv_msgs)
        if len(raw) >= threshold_chars:
            to_synthesize[key] = conv_msgs
        else:
            pass_throughs[key] = conv_msgs

    logger.info(
        "Synthesizing %d / %d Slack conversations (threshold: %d chars)",
        len(to_synthesize),
        len(groups),
        threshold_chars,
    )

    result_map: dict[str, dict] = {}

    def _make_passthrough(key, conv_msgs):
        sample = conv_msgs[0]
        if sample["is_group_dm"]:
            label = f"[group-dm] {key}"
        elif sample["is_dm"]:
            senders = list(dict.fromkeys(m["sender"] for m in conv_msgs))
            label = f"[dm] {', '.join(senders[:3])}"
        else:
            label = f"[#{key}]"
        links = list(dict.fromkeys(m["link"] for m in conv_msgs if m.get("link")))
        raw = "\n".join(f"{m['sender']}: {m['text']}" for m in conv_msgs)
        return {"label": label, "summary": raw, "links": links}

    for key, conv_msgs in pass_throughs.items():
        result_map[key] = _make_passthrough(key, conv_msgs)

    total_chars_in = total_chars_out = total_duration_ms = 0

    if to_synthesize:
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(
                    _synthesize_single_conversation, key, conv_msgs, config
                ): key
                for key, conv_msgs in to_synthesize.items()
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    conv_dict, chars_in, chars_out, duration_ms = future.result()
                    result_map[key] = conv_dict
                    total_chars_in += chars_in
                    total_chars_out += chars_out
                    total_duration_ms += duration_ms
                    logger.info(
                        "Synthesized '%s': %d → %d chars (%dms)",
                        conv_dict["label"],
                        chars_in,
                        chars_out,
                        duration_ms,
                    )
                except Exception as e:
                    logger.warning("Failed to synthesize conversation '%s': %s", key, e)
                    result_map[key] = _make_passthrough(key, to_synthesize[key])

    conversations = sorted(result_map.values(), key=lambda c: c["label"])
    return conversations, {
        "chars_in": total_chars_in,
        "chars_out": total_chars_out,
        "duration_ms": total_duration_ms,
    }


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def _format_slack(messages: list[dict], max_chars: int) -> str:
    if not messages:
        return "No new Slack messages."
    lines = []
    for m in messages:
        prefix = ""
        if m.get("is_dm") and not m.get("is_group_dm"):
            prefix = "[DM] "
        elif m.get("is_group_dm"):
            prefix = "[GROUP-DM] "
        elif m.get("is_mention"):
            prefix = "[MENTION] "
        link = m.get("link", "")
        lines.append(
            f"{prefix}#{m['channel']} | {m['sender']}: {m['text']}\n  link: {link}"
        )
    return _truncate("\n".join(lines), max_chars)


def _format_slack_synthesized(conversations: list[dict], max_chars: int) -> str:
    if not conversations:
        return "No new Slack messages."
    lines = []
    for c in conversations:
        label = c["label"]
        summary = c["summary"]
        links = c.get("links", [])
        block = f"## {label}\n{summary}"
        if links:
            link_str = ", ".join(links[:3])
            if len(links) > 3:
                link_str += f" (+{len(links) - 3} more)"
            block += f"\nlinks: {link_str}"
        lines.append(block)
    return _truncate("\n\n".join(lines), max_chars)


def _format_gmail(emails: list[dict], max_chars: int) -> str:
    if not emails:
        return "No new emails."
    lines = []
    for e in emails:
        link = e.get("link", "")
        lines.append(
            f"From: {e['from']}\nSubject: {e['subject']}\nlink: {link}\n{e['body_preview']}\n---"
        )
    return _truncate("\n".join(lines), max_chars)


def _format_obsidian(notes: list[dict], max_chars: int) -> str:
    if not notes:
        return "No recently modified notes."
    lines = []
    for n in notes:
        lines.append(
            f"## {n['filename']} (modified {n['modified_at']})\n{n['content']}\n---"
        )
    return _truncate("\n".join(lines), max_chars)


def _format_jira(tickets: list[dict], max_chars: int) -> str:
    if not tickets:
        return "No updated JIRA tickets."
    lines = []
    for t in tickets:
        line = f"[{t['key']}] {t['summary']}\n  Status: {t['status']} | Priority: {t['priority']} | Assignee: {t['assignee']} | Type: {t['type']}\n  link: {t['link']}"
        if t.get("recent_comments"):
            line += f"\n  Recent comments:\n{t['recent_comments']}"
        lines.append(line)
    return _truncate("\n".join(lines), max_chars)


def _format_confluence(pages: list[dict], max_chars: int) -> str:
    if not pages:
        return "No updated Confluence pages."
    lines = []
    for p in pages:
        line = f"[{p['space']}] {p['title']} (v{p['version']})\n  Editor: {p['editor']} | Type: {p['type']}\n  link: {p['link']}"
        if p.get("version_message"):
            line += f"\n  Version note: {p['version_message']}"
        lines.append(line)
    return _truncate("\n".join(lines), max_chars)


def _format_meetings(meetings: list[dict], max_chars: int) -> str:
    if not meetings:
        return "No meetings."
    lines = []
    for m in meetings:
        attendee_str = ", ".join(m.get("attendees", [])[:10])
        if len(m.get("attendees", [])) > 10:
            attendee_str += f" (+{len(m['attendees']) - 10} more)"

        line = f"## {m['title']}\n  Time: {m['start']} to {m['end']}\n  Attendees: {attendee_str}"
        if m.get("calendar_link"):
            line += f"\n  calendar: {m['calendar_link']}"

        docs = m.get("documents", [])
        if not docs:
            line += "\n  (no notes or transcript found)"
        else:
            for doc in docs:
                if doc.get("has_content"):
                    line += f"\n  --- {doc['title']} ({doc['source_method']}) ---"
                    line += f"\n  link: {doc['link']}"
                    line += f"\n{doc['content']}"
                    line += "\n  --- end document ---"
                else:
                    line += f"\n  (attachment found but could not extract content: {doc['title']} — {doc['link']})"

        lines.append(line)
    return _truncate("\n".join(lines), max_chars)


def _format_heartbeat(results: list[dict]) -> str:
    if not results:
        return "No standing questions."
    lines = []
    for r in results:
        q = r["question"]
        findings = r.get("findings", [])
        lines.append(f"QUESTION: {q}")
        if findings:
            for f in findings:
                link_str = f" ({f['link']})" if f.get("link") else ""
                lines.append(f"  [{f['source']}] {f['text']}{link_str}")
        else:
            lines.append("  No findings across any source.")
        lines.append("")
    return "\n".join(lines)


def _format_open_tasks(tasks: list[dict]) -> str:
    """Format open tasks from DB for the GPT prompt, including UUIDs."""
    import json

    if not tasks:
        return "No prior tasks."
    lines = []
    for t in tasks:
        sources = (
            json.loads(t["sources"])
            if isinstance(t.get("sources"), str)
            else t.get("sources") or []
        )
        links = (
            json.loads(t["links"])
            if isinstance(t.get("links"), str)
            else t.get("links") or []
        )
        line = f"- id:{t['id']} [{t.get('priority','medium')}] {t['title']}"
        if t.get("why"):
            line += f" | why: {t['why']}"
        if sources:
            line += f" | sources: {', '.join(sources)}"
        if links:
            line += f" | links: {', '.join(links)}"
        if t.get("created_at"):
            line += f" | first_seen: {t['created_at'][:10]}"
        lines.append(line)
    return "\n".join(lines)


def _format_memory(memories: dict[str, str], max_chars_per_tier: int = 4000) -> str:
    sections = []
    for tier in ["quarterly", "monthly", "sprintly", "weekly", "daily"]:
        content = memories.get(tier, "(not loaded)")
        sections.append(
            f"### {tier.upper()} MEMORY\n{_truncate(content, max_chars_per_tier)}"
        )
    return "\n\n".join(sections)


def _build_semantic_query(
    slack_msgs: list[dict],
    emails: list[dict],
    jira_tickets: list[dict],
    confluence_pages: list[dict],
    meetings: list[dict],
    slack_conversations: list[dict] | None = None,
) -> str:
    """Compact aggregate text representing today's new signal, used as a semantic
    query against the decisions/experiments embedding store."""
    parts = []
    if slack_conversations:
        parts.extend(c.get("summary", "")[:200] for c in slack_conversations)
    else:
        parts.extend(m.get("text", "")[:200] for m in slack_msgs)
    parts.extend(e.get("subject", "") for e in emails)
    parts.extend(t.get("summary", "") for t in jira_tickets)
    parts.extend(p.get("title", "") for p in confluence_pages)
    parts.extend(m.get("title", "") for m in meetings)
    return " ".join(filter(None, parts))[:8000]


def _format_semantic_matches(dec_hits: list[dict], exp_hits: list[dict], min_score: float = 0.35) -> str:
    lines = []
    for h in dec_hits:
        if h["score"] >= min_score:
            lines.append(f"- [{h['date']}] {h['text']}")
    for h in exp_hits:
        if h["score"] >= min_score:
            lines.append(f"- [{h['date']}] (experiment) {h['text']}")
    if not lines:
        return ""
    return (
        "\n=== SEMANTICALLY RELATED PAST DECISIONS & EXPERIMENTS "
        "(outside the recency window above — check before treating anything as new) ===\n"
        + "\n".join(lines)
    )


def process(
    slack_msgs: list[dict],
    emails: list[dict],
    notes: list[dict],
    jira_tickets: list[dict],
    confluence_pages: list[dict],
    meetings: list[dict],
    heartbeat_results: list[dict],
    memories: dict[str, str],
    open_tasks: list[dict],
    resolved_titles: list[str],
    tiers_due: list[str],
    people,
    experiment_context: str,
    decisions_context: str,
    config: dict,
    close_rate_summary: str = "",
    stale_decisions_for_review: list[dict] = None,
    slack_conversations: list[dict] | None = None,
    embedding_store=None,
) -> dict:
    """
    Send aggregated data + memory + prior tasks + knowledge logs to the configured LLM.
    Returns {"tasks": [...], "memory_updates": {...}, "experiment_entries": [...], "decision_entries": [...]}
    """
    llm_config = get_llm_config(config)
    max_chars = llm_config.get("max_chars_per_source", config.get("max_chars_per_source", 12000))

    _priority_order = {"high": 0, "medium": 1, "low": 2}
    capped_tasks = sorted(
        open_tasks, key=lambda t: _priority_order.get(t.get("priority", "low"), 2)
    )[:30]
    if len(open_tasks) > 30:
        logger.info(
            "Capped prior tasks from %d to 30 (dropped %d lowest-priority)",
            len(open_tasks),
            len(open_tasks) - 30,
        )
    prior_tasks_section = (
        _format_open_tasks(capped_tasks) if capped_tasks else "No prior tasks."
    )
    people_context = people.get_context_block() if people else ""
    now_str = timeutil.now().strftime("%Y-%m-%d %H:%M (%A)")

    resolved_section = "None."
    if resolved_titles:
        resolved_section = "\n".join(f"- {t}" for t in resolved_titles)

    decay_section = ""
    if stale_decisions_for_review:
        decay_lines = [
            f"- {e['decision']} (logged {e['date']})"
            for e in stale_decisions_for_review
        ]
        decay_section = (
            "\n=== DECISION DECAY SWEEP (sprint boundary) ===\n"
            "These are scoped decisions most likely to be stale (overdue deadlines first, then oldest-unreviewed). "
            "For each one, assess: is it still operative given current context? "
            "If it may need revisiting, create a task with [DECISION REVIEW] prefix summarizing what to reassess and why.\n"
            + "\n".join(decay_lines)
        )

    close_rate_section = ""
    if close_rate_summary:
        close_rate_section = (
            f"\n=== TASK CLOSE RATE (for your awareness) ===\n{close_rate_summary}"
        )

    semantic_section = ""
    if embedding_store:
        query = _build_semantic_query(
            slack_msgs, emails, jira_tickets, confluence_pages, meetings, slack_conversations
        )
        if query.strip():
            try:
                dec_hits = embedding_store.search(query, k=6, source="decision")
                exp_hits = embedding_store.search(query, k=4, source="experiment")
                semantic_section = _format_semantic_matches(dec_hits, exp_hits)
            except Exception as e:
                logger.warning("Semantic decision/experiment search failed: %s", e)

    system_prompt = load_prompt("system_prompt", config)
    user_content = f"""{system_prompt}

---

Current time: {now_str} {timeutil.tz_label()}

=== PEOPLE DIRECTORY ===
{people_context}

Here is what happened since the last check-in:

=== SLACK ===
{_format_slack_synthesized(slack_conversations, max_chars) if slack_conversations is not None else _format_slack(slack_msgs, max_chars)}

=== EMAIL ===
{_format_gmail(emails, max_chars)}

=== JIRA ===
{_format_jira(jira_tickets, max_chars)}

=== CONFLUENCE ===
{_format_confluence(confluence_pages, max_chars)}

=== MEETINGS (transcripts and notes) ===
{_format_meetings(meetings, max_chars)}

=== STANDING QUESTIONS (investigated across all sources) ===
{_format_heartbeat(heartbeat_results)}

=== OBSIDIAN NOTES ===
{_format_obsidian(notes, max_chars)}

=== PREVIOUSLY SURFACED TASKS (carry forward, update, or drop) ===
Each task has an id. If you carry a task forward, include its id in your output unchanged. If you drop a task, simply omit it. If you create a new task, omit the id field entirely.
{prior_tasks_section}

=== RESOLVED BY USER (drop these entirely) ===
{resolved_section}

=== YOUR MEMORY ===
{_format_memory(memories)}

=== EXPERIMENT LOG (reference only, do not re-log existing entries) ===
{_truncate(experiment_context, 6000) if experiment_context else "No experiments logged yet."}

=== DECISIONS LOG (reference only, do not re-log existing entries) ===
{decisions_context if decisions_context else "No decisions logged yet."}

=== MEMORY TIERS DUE FOR UPDATE ===
{', '.join(tiers_due) if tiers_due else 'none'}{close_rate_section}{decay_section}{semantic_section}

Produce a consolidated, reprioritized task list (merging old and new, dropping resolved items), update every memory tier listed above, extract any new experiment results or decisions detected in today's inputs, and update relationship trajectories for any person where you have meaningful new signal. Return JSON only."""

    gpt = create_llm(
        llm_config,
        temperature=llm_config.get("temperature", 0.4),
        max_tokens=llm_config.get("max_tokens", 8000),
        identifier=llm_config.get("identifier", "work-assistant"),
    )

    _empty = {
        "tasks": [],
        "memory_updates": {},
        "experiment_entries": [],
        "decision_entries": [],
        "people_updates": [],
        "usage": {"chars_in": len(user_content), "chars_out": 0, "duration_ms": 0},
    }

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        content = None
        try:
            t0 = time.monotonic()
            content = gpt.completion(user_content, timeout=300)
            duration_ms = int((time.monotonic() - t0) * 1000)

            logger.info(
                "Raw response type: %s, length: %s, duration: %dms (attempt %d)",
                type(content).__name__,
                len(content) if content else 0,
                duration_ms,
                attempt,
            )

            if not content:
                logger.error(
                    "Empty response from model (attempt %d). Raw value: %r",
                    attempt,
                    content,
                )
                if attempt < max_attempts:
                    logger.info("Retrying...")
                    continue
                return _empty

            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            logger.debug("Cleaned content (first 300 chars): %s", content[:300])

            result = json.loads(content)

            tasks = result.get("tasks", [])
            memory_updates = result.get("memory_updates", {})
            experiment_entries = result.get("experiment_entries", [])
            decision_entries = result.get("decision_entries", [])
            people_updates = result.get("people_updates", [])

            logger.info(
                "GPT returned %d tasks, %d memory updates, %d experiments, %d decisions, %d people updates",
                len(tasks),
                len([v for v in memory_updates.values() if v]),
                len(experiment_entries),
                len(decision_entries),
                len(people_updates),
            )
            return {
                "tasks": tasks,
                "memory_updates": memory_updates,
                "experiment_entries": experiment_entries,
                "decision_entries": decision_entries,
                "people_updates": people_updates,
                "usage": {
                    "chars_in": len(user_content),
                    "chars_out": len(content),
                    "duration_ms": duration_ms,
                },
            }

        except json.JSONDecodeError as e:
            logger.error(
                "JSON parse failed (attempt %d): %s\nFirst 500 chars of response:\n%s",
                attempt,
                e,
                content[:500] if content else "(empty)",
            )
            if attempt < max_attempts:
                logger.info("Retrying...")
                continue
            return _empty
        except LLMError as e:
            logger.error("LLM request failed (attempt %d): %s", attempt, e)
            if attempt < max_attempts:
                logger.info("Retrying...")
                continue
            return _empty
        except Exception as e:
            logger.error(
                "GPT processing failed (attempt %d): %s", attempt, e, exc_info=True
            )
            return _empty

    return _empty


def _format_jira_open_summary(tickets: list[dict]) -> str:
    """Compact open-ticket summary for absence sweep — excludes done/closed."""
    if not tickets:
        return "No open JIRA tickets."
    lines = []
    for t in tickets:
        status = t.get("status", "Unknown")
        if status.lower() in ("done", "closed", "resolved", "cancelled"):
            continue
        key = t.get("key", "?")
        summary = t.get("summary", "")[:80]
        assignee = t.get("assignee", "Unassigned")
        lines.append(f"- [{key}] {summary} | Status: {status} | Assignee: {assignee}")
    return "\n".join(lines) if lines else "No open JIRA tickets."


def run_absence_sweep(
    pass1_tasks: list[dict],
    people_context: str,
    jira_tickets: list[dict],
    slack_senders: list[str],
    email_senders: list[str],
    meeting_attendees: list[str],
    config: dict,
    resolved_titles: list[str] | None = None,
) -> dict:
    """
    Second-pass prompt: hunts for what SHOULD be present but isn't.
    Returns {"tasks": [...], "usage": {...}}.
    """
    now_str = timeutil.now().strftime("%Y-%m-%d %H:%M (%A)")

    task_lines = [
        f"- [{t.get('priority', '?')}] {t.get('title', '')}" for t in pass1_tasks[:30]
    ]
    task_summary = "\n".join(task_lines) if task_lines else "No tasks from main pass."

    heard_from = sorted(set(slack_senders + email_senders + meeting_attendees))
    heard_section = (
        ", ".join(heard_from) if heard_from else "No comms received this run."
    )

    resolved_section = ""
    if resolved_titles:
        resolved_section = (
            "\n=== RECENTLY RESOLVED BY USER (do not re-flag these) ===\n"
            + "\n".join(f"- {t}" for t in resolved_titles)
        )

    llm_config = get_llm_config(config)
    absence_prompt = load_prompt("absence_prompt", config)

    user_content = f"""{absence_prompt}

---

Current time: {now_str} {timeutil.tz_label()}

=== PEOPLE DIRECTORY (with trajectories) ===
{people_context}

=== WHO WAS HEARD FROM THIS RUN (Slack + Email + Meetings) ===
{heard_section}

=== OPEN JIRA TICKETS ===
{_format_jira_open_summary(jira_tickets)}

=== TASKS FROM MAIN SYNTHESIS PASS ===
{task_summary}{resolved_section}

Identify what's conspicuously absent. Return JSON only."""

    gpt = create_llm(
        llm_config,
        temperature=llm_config.get("temperature", 0.3),
        max_tokens=min(llm_config.get("max_tokens", 8000), 2000),
        identifier="work-assistant-absence",
    )

    empty = {
        "tasks": [],
        "usage": {"chars_in": len(user_content), "chars_out": 0, "duration_ms": 0},
    }

    try:
        t0 = time.monotonic()
        content = gpt.completion(user_content)
        duration_ms = int((time.monotonic() - t0) * 1000)

        if not content:
            logger.warning("Absence sweep returned empty response")
            return empty

        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        result = json.loads(content)
        tasks = result.get("tasks", [])
        logger.info(
            "Absence sweep found %d gaps, duration: %dms", len(tasks), duration_ms
        )
        return {
            "tasks": tasks,
            "usage": {
                "chars_in": len(user_content),
                "chars_out": len(content),
                "duration_ms": duration_ms,
            },
        }

    except json.JSONDecodeError as e:
        logger.error("Absence sweep JSON parse failed: %s", e)
        return empty
    except Exception as e:
        logger.error("Absence sweep failed: %s", e, exc_info=True)
        return empty
