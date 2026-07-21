"""
Quick Q&A against the knowledge base. Designed to be called from Raycast.

Usage:
  python ask.py "What's the status of the native help API?"

Searches Slack, Gmail, JIRA, Confluence, and meeting notes in parallel,
loads rolling memory context, and returns a plain-text answer via the
configured LLM backend.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Suppress all logging so stdout is clean for Raycast
logging.disable(logging.CRITICAL)

AGENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(AGENT_DIR))

from config_loader import get_llm_config, get_source_config, is_source_enabled, load_config
from llm.factory import create_llm
from prompts import load_prompt
import timeutil


def load_memory_context(config: dict, question: str) -> str:
    """Load memory context for Q&A: small tier files in full (they're rewritten
    wholesale each cycle, so there's no history to search — always current),
    plus a semantic search over the permanent decisions/experiments logs keyed
    to the question, instead of dumping those files in full. Those two logs are
    the only thing in agent-memory/ that actually grows without bound; loading
    them whole on every question doesn't scale and isn't what "relevant" means
    for a specific question anyway.
    """
    obsidian_config = get_source_config(config, "obsidian")
    vault = Path(os.path.expanduser(obsidian_config["vault_path"]))
    memory_dir = vault / obsidian_config.get("memory_folder", "agent-memory")

    tiers = ["daily", "weekly", "sprintly", "monthly", "quarterly", "tasks"]
    parts = []
    for tier in tiers:
        path = memory_dir / f"{tier}.md"
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"## {tier.capitalize()} memory\n{content}")
            except Exception:
                pass

    try:
        from embeddings import EmbeddingStore

        store = EmbeddingStore(config["agent"]["state_db"])
        for source, label in (("decision", "Relevant past decisions"), ("experiment", "Relevant past experiments")):
            hits = store.search(question, k=8, source=source)
            lines = [f"- [{h['date']}] {h['text']}" for h in hits if h["score"] >= 0.3]
            if lines:
                parts.append(f"## {label}\n" + "\n".join(lines))
    except Exception:
        pass  # tier files above still give partial context if this fails

    # Load any extra files from other vault directories
    for extra in obsidian_config.get("memory_extra_files", []):
        path = vault / extra["path"]
        label = extra.get("label", path.stem)
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"## {label}\n{content}")
            except Exception:
                pass

    return "\n\n".join(parts)


def ask(question: str) -> str:
    config = load_config()
    timeutil.configure(config)
    llm_config = get_llm_config(config)
    obsidian_config = get_source_config(config, "obsidian")

    from heartbeat import Heartbeat
    from people import PeopleDirectory
    from sources.confluence_source import ConfluenceSource
    from sources.gmail_source import GmailSource
    from sources.jira_source import JiraSource
    from sources.meeting_source import MeetingSource
    from sources.slack import SlackSource

    people = PeopleDirectory(AGENT_DIR)

    # Initialise sources, skip any that fail (e.g. expired token)
    slack = gmail = jira = confluence = meetings = None
    if is_source_enabled(config, "slack"):
        try:
            slack = SlackSource(get_source_config(config, "slack"))
        except Exception:
            pass
    if is_source_enabled(config, "gmail"):
        try:
            gmail = GmailSource(get_source_config(config, "gmail"))
        except Exception:
            pass
    if is_source_enabled(config, "jira"):
        try:
            jira = JiraSource(get_source_config(config, "jira"))
        except Exception:
            pass
    if is_source_enabled(config, "confluence"):
        try:
            confluence = ConfluenceSource(get_source_config(config, "confluence"))
        except Exception:
            pass
    if is_source_enabled(config, "meetings"):
        try:
            meetings = MeetingSource(get_source_config(config, "meetings"))
        except Exception:
            pass

    # Search all sources for the question
    hb = Heartbeat(obsidian_config)
    findings = hb.investigate(
        [question], slack, gmail, jira, confluence, meetings, people=people
    )
    hits = findings[0]["findings"] if findings else []

    # Format findings for the prompt
    if hits:
        findings_text = "\n".join(
            f"[{h['source']}] {h.get('text', '')} {h.get('link', '')}".strip()
            for h in hits
        )
    else:
        findings_text = "No results found across sources."

    memory_context = load_memory_context(config, question)

    today = timeutil.format_date_long()
    ask_instructions = load_prompt(
        "ask_prompt",
        config,
        assistant_name=config.get("assistant", {}).get("name", "Work Assistant"),
    )
    prompt = f"""{ask_instructions}

Today's date: {today}

If a sprint or release calendar appears in rolling memory context, prefer it over stale tier summaries for sprint timing. Otherwise use today's date and the most recent memory tiers.

Question: {question}

Search results from knowledge base:
{findings_text}

Rolling memory context:
{memory_context}"""

    gpt = create_llm(
        llm_config,
        temperature=llm_config.get("temperature", 0.3),
        max_tokens=1000,
        identifier="work-assistant-ask",
    )
    response = gpt.completion(prompt)
    answer = response.strip() if response else "No response from model."

    _cache_to_obsidian(question, answer, obsidian_config)
    return answer


def _cache_to_obsidian(question: str, answer: str, obsidian_config: dict) -> None:
    """Append the Q&A to a daily note in the Obsidian inbox."""
    try:
        vault = os.path.expanduser(obsidian_config["vault_path"])
        inbox = Path(vault) / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        note_path = inbox / f"ask-{today}.md"

        timestamp = datetime.now().strftime("%H:%M")
        entry = f"\n## {timestamp}\n**Q:** {question}\n\n{answer}\n"

        if not note_path.exists():
            note_path.write_text(f"# Ask log {today}\n{entry}", encoding="utf-8")
        else:
            with note_path.open("a", encoding="utf-8") as f:
                f.write(entry)
    except Exception:
        pass  # Never let caching break the main flow


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python ask.py "your question here"')
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    try:
        answer = ask(question)
        print(answer)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
