"""
JIRA integration via REST API with personal API token.

Setup:
  1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
  2. Create API token
  3. Add email, token, and base_url to config.yaml
"""

import logging
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

MAX_RESULTS = 50


class JiraSource:
    def __init__(self, config: dict):
        self.email = config["email"]
        self.token = config["token"]
        self.base_url = config["base_url"].rstrip("/")
        self.jql_filter = config.get("jql_filter", "assignee = currentUser()")

        auth = b64encode(f"{self.email}:{self.token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        }

    def _api_get(self, path: str, params: dict = None) -> dict:
        resp = requests.get(
            f"{self.base_url}{path}",
            headers=self._headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def _get_comments(self, issue_key: str, since: datetime) -> list[dict]:
        """Fetch comments on an issue that were added after the since timestamp."""
        try:
            data = self._api_get(f"/rest/api/3/issue/{issue_key}/comment")
            comments = []
            for c in data.get("comments", []):
                created = c.get("created", "")
                # JIRA timestamps: "2026-03-06T14:30:00.000+0000"
                try:
                    ct = datetime.fromisoformat(created.replace("+0000", "+00:00"))
                    if ct <= since:
                        continue
                except Exception:
                    continue

                # Extract plain text from ADF body
                body_text = ""
                body = c.get("body", {})
                if isinstance(body, dict):
                    for block in body.get("content", []):
                        for inline in block.get("content", []):
                            if inline.get("type") == "text":
                                body_text += inline.get("text", "")
                    body_text = body_text.strip()

                author = c.get("author", {}).get("displayName", "Unknown")
                comments.append(
                    {
                        "author": author,
                        "body": body_text[:500],
                        "created": created,
                    }
                )
            return comments
        except Exception as e:
            logger.warning("Failed to fetch comments for %s: %s", issue_key, e)
            return []

    def fetch(self, since: datetime) -> list[dict]:
        """
        Fetch tickets updated since the given timestamp.
        Returns list of dicts with ticket info and recent comments.
        """
        since_str = since.strftime("%Y-%m-%d %H:%M")
        jql = f"({self.jql_filter}) AND updated >= '{since_str}' ORDER BY updated DESC"

        try:
            data = self._api_get(
                "/rest/api/3/search/jql",
                params={
                    "jql": jql,
                    "maxResults": MAX_RESULTS,
                    "fields": "summary,status,updated,priority,assignee,project,issuetype,description",
                },
            )
        except Exception as e:
            logger.error("JIRA API error: %s", e)
            return []

        issues = data.get("issues", [])
        if not issues:
            logger.info("No updated JIRA tickets since %s", since_str)
            return []

        # Fetch comments in parallel for issues that came back
        comment_map = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self._get_comments, issue["key"], since): issue["key"]
                for issue in issues
            }
            for future in as_completed(futures):
                key = futures[future]
                comment_map[key] = future.result()

        tickets = []
        for issue in issues:
            fields = issue.get("fields", {})
            key = issue["key"]
            status = fields.get("status", {}).get("name", "Unknown")
            summary = fields.get("summary", "")
            priority = (
                fields.get("priority", {}).get("name", "None")
                if fields.get("priority")
                else "None"
            )
            project = fields.get("project", {}).get("key", "")
            issue_type = fields.get("issuetype", {}).get("name", "")
            assignee = (
                fields.get("assignee", {}).get("displayName", "Unassigned")
                if fields.get("assignee")
                else "Unassigned"
            )
            updated = fields.get("updated", "")

            comments = comment_map.get(key, [])
            comment_summary = ""
            if comments:
                comment_lines = [f"  {c['author']}: {c['body']}" for c in comments[:3]]
                comment_summary = "\n".join(comment_lines)

            # Extract plain text from ADF description
            description_text = ""
            desc = fields.get("description")
            if isinstance(desc, dict):
                for block in desc.get("content", []):
                    for inline in block.get("content", []):
                        if inline.get("type") == "text":
                            description_text += inline.get("text", "")
                description_text = description_text.strip()[:1000]

            link = f"{self.base_url}/browse/{key}"

            tickets.append(
                {
                    "source": "jira",
                    "key": key,
                    "project": project,
                    "type": issue_type,
                    "summary": summary,
                    "status": status,
                    "priority": priority,
                    "assignee": assignee,
                    "updated": updated,
                    "description": description_text,
                    "recent_comments": comment_summary,
                    "link": link,
                }
            )

        tickets.sort(key=lambda t: t["updated"], reverse=True)
        logger.info("Fetched %d JIRA tickets since %s", len(tickets), since_str)
        return tickets
