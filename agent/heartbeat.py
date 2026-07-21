"""
Heartbeat: standing questions that the agent proactively investigates each run.

Lives in agent-memory/heartbeat.md as a simple markdown checklist:

  - [ ] Has Alex responded to the API proposal?
  - [ ] Did the platform team finish the hotfix?
  - [ ] Any updates on the MBR coverage gap experiment?

Checked items (- [x]) are skipped. The agent searches Slack, Gmail, JIRA,
Confluence, and meeting transcripts for each unchecked question and includes
the findings in the GPT prompt.
"""

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_questions(content: str) -> list[str]:
    """Extract unchecked questions from markdown checklist."""
    questions = []
    for line in content.splitlines():
        line = line.strip()
        match = re.match(r"^-\s*\[\s*\]\s*(.+)$", line)
        if match:
            questions.append(match.group(1).strip())
    return questions


def _search_keywords(question: str, people=None) -> str:
    """Extract search terms from a question.
    Expands recognized nicknames to full names via the people directory."""
    stopwords = {
        "has",
        "have",
        "did",
        "does",
        "do",
        "is",
        "are",
        "was",
        "were",
        "any",
        "the",
        "a",
        "an",
        "on",
        "in",
        "to",
        "for",
        "from",
        "with",
        "about",
        "been",
        "yet",
        "still",
        "there",
        "updates",
        "update",
        "responded",
        "response",
        "reply",
        "replied",
        "finished",
        "finish",
        "completed",
        "complete",
        "progress",
        "status",
    }
    words = re.findall(r"[A-Za-z0-9][\w\-]*", question)
    keywords = []
    for w in words:
        if w.lower() in stopwords or len(w) <= 1:
            continue
        if people:
            person = people.resolve(w)
            if person:
                keywords.append(person.get("name", w))
                continue
        keywords.append(w)
    return " ".join(keywords[:6])


class Heartbeat:
    def __init__(self, config: dict):
        vault = os.path.expanduser(config["vault_path"])
        memory_folder = config.get("memory_folder", "agent-memory")
        self.heartbeat_path = Path(vault) / memory_folder / "heartbeat.md"

        if not self.heartbeat_path.exists():
            self.heartbeat_path.write_text(
                "# Heartbeat\n\n"
                "Standing questions the agent investigates each run.\n"
                "Add unchecked items. Check them off when resolved.\n\n"
                "- [ ] Example: Has the team responded to my API proposal?\n",
                encoding="utf-8",
            )
            logger.info("Created heartbeat file: %s", self.heartbeat_path)

    def load_questions(self) -> list[str]:
        try:
            content = self.heartbeat_path.read_text(encoding="utf-8")
            questions = _parse_questions(content)
            logger.info("Loaded %d heartbeat questions", len(questions))
            return questions
        except Exception as e:
            logger.warning("Failed to load heartbeat: %s", e)
            return []

    def investigate(
        self,
        questions: list[str],
        slack_source,
        gmail_source,
        jira_source,
        confluence_source=None,
        meeting_source=None,
        people=None,
        embedding_store=None,
    ) -> list[dict]:
        """
        For each question, search across available sources for relevant info.
        Returns list of {question, findings: [{source, text}]}
        """
        if not questions:
            return []

        results = []

        for question in questions:
            query = _search_keywords(question, people=people)
            if not query:
                results.append({"question": question, "findings": []})
                continue

            findings = []

            # Count active sources for worker pool sizing
            sources = []
            if slack_source:
                sources.append(("slack", self._search_slack, slack_source))
            if gmail_source:
                sources.append(("gmail", self._search_gmail, gmail_source))
            if jira_source:
                sources.append(("jira", self._search_jira, jira_source))
            if confluence_source:
                sources.append(
                    ("confluence", self._search_confluence, confluence_source)
                )
            if meeting_source:
                sources.append(("meeting", self._search_meetings, meeting_source))
            if embedding_store:
                sources.append(("knowledge_log", self._search_knowledge_log, embedding_store))

            with ThreadPoolExecutor(max_workers=len(sources)) as executor:
                futures = {}
                for source_name, search_fn, source_obj in sources:
                    futures[executor.submit(search_fn, source_obj, query)] = source_name

                for future in as_completed(futures):
                    source_name = futures[future]
                    try:
                        hits = future.result()
                        for hit in hits:
                            findings.append({"source": source_name, **hit})
                    except Exception as e:
                        logger.warning(
                            "Heartbeat search failed for %s: %s", source_name, e
                        )

            results.append({"question": question, "findings": findings})
            logger.info("Heartbeat '%s': %d findings", question[:50], len(findings))

        return results

    def _search_slack(self, slack_source, query: str) -> list[dict]:
        """Search Slack messages matching the query."""
        try:
            data = slack_source._api(
                "search.messages", query=query, count=5, sort="timestamp"
            )
            matches = data.get("messages", {}).get("matches", [])
            results = []
            for m in matches[:3]:
                results.append(
                    {
                        "text": f"{m.get('username', 'unknown')}: {m.get('text', '')[:200]}",
                        "ts": m.get("ts", ""),
                        "link": m.get("permalink", ""),
                    }
                )
            return results
        except Exception as e:
            logger.debug("Slack search failed for '%s': %s", query, e)
            return []

    def _search_gmail(self, gmail_source, query: str) -> list[dict]:
        """Search Gmail for messages matching the query."""
        try:
            service = gmail_source._get_service()
            resp = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=3)
                .execute()
            )
            results = []
            for msg_ref in resp.get("messages", []):
                msg = (
                    service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=msg_ref["id"],
                        format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                    )
                    .execute()
                )
                headers = {
                    h["name"]: h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                results.append(
                    {
                        "text": f"From: {headers.get('From', 'unknown')} | Subject: {headers.get('Subject', '')}",
                        "ts": headers.get("Date", ""),
                        "link": f"https://mail.google.com/mail/u/0/#inbox/{msg_ref['id']}",
                    }
                )
            return results
        except Exception as e:
            logger.debug("Gmail search failed for '%s': %s", query, e)
            return []

    def _search_jira(self, jira_source, query: str) -> list[dict]:
        """Search JIRA for issues matching the query."""
        try:
            data = jira_source._api_get(
                "/rest/api/3/search/jql",
                params={
                    "jql": f'text ~ "{query}" ORDER BY updated DESC',
                    "maxResults": 3,
                    "fields": "summary,status,updated",
                },
            )
            results = []
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                results.append(
                    {
                        "text": f"[{issue['key']}] {fields.get('summary', '')} ({fields.get('status', {}).get('name', '')})",
                        "ts": fields.get("updated", ""),
                        "link": f"{jira_source.base_url}/browse/{issue['key']}",
                    }
                )
            return results
        except Exception as e:
            logger.debug("JIRA search failed for '%s': %s", query, e)
            return []

    def _search_confluence(self, confluence_source, query: str) -> list[dict]:
        """Search Confluence pages matching the query."""
        return confluence_source.search(query, max_results=3)

    def _search_knowledge_log(self, embedding_store, query: str) -> list[dict]:
        """Semantic search across the permanent decisions/experiments logs,
        regardless of how long ago an entry was logged."""
        try:
            hits = embedding_store.search(query, k=3)
            results = []
            for h in hits:
                if h["score"] < 0.35:
                    continue
                label = "decision" if h["source"] == "decision" else "experiment"
                results.append(
                    {
                        "text": f"[{label}, {h['date']}] {h['text'][:200]}",
                        "ts": h.get("date", ""),
                        "link": "",
                    }
                )
            return results
        except Exception as e:
            logger.debug("Knowledge-log search failed for '%s': %s", query, e)
            return []

    def _search_meetings(self, meeting_source, query: str) -> list[dict]:
        """Search meeting transcripts/notes matching the query."""
        return meeting_source.search(query, max_results=3)
