"""
Confluence integration via REST API, sharing auth with JIRA.

Monitors pages the user is watching or has created, surfacing
recent edits with version comments and editor info.

Uses the same email/token/base_url as the JIRA config since
Atlassian Cloud shares auth across products.
"""

import logging
from base64 import b64encode
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

MAX_RESULTS = 25


class ConfluenceSource:
    def __init__(self, config: dict):
        self.email = config["email"]
        self.token = config["token"]
        self.base_url = config["base_url"].rstrip("/")
        self.cql_filter = config.get(
            "cql_filter", "watcher = currentUser() OR creator = currentUser()"
        )

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

    def fetch(self, since: datetime) -> list[dict]:
        """
        Fetch Confluence pages updated since the given timestamp.
        Returns list of dicts with page info, version details, and links.
        """
        since_str = since.strftime("%Y-%m-%d %H:%M")
        cql = f'({self.cql_filter}) AND lastModified >= "{since_str}"'

        try:
            data = self._api_get(
                "/wiki/rest/api/content/search",
                params={
                    "cql": cql,
                    "limit": MAX_RESULTS,
                    "expand": "version,space,history.lastUpdated",
                },
            )
        except Exception as e:
            logger.error("Confluence API error: %s", e)
            return []

        results = data.get("results", [])
        if not results:
            logger.info("No updated Confluence pages since %s", since_str)
            return []

        pages = []
        for page in results:
            page_id = page.get("id", "")
            title = page.get("title", "")
            space = page.get("space", {}).get("key", "")
            page_type = page.get("type", "page")

            version = page.get("version", {})
            version_number = version.get("number", 0)
            version_message = version.get("message", "")
            editor = version.get("by", {}).get("displayName", "Unknown")
            updated = version.get("when", "")

            link = f"{self.base_url}/wiki/spaces/{space}/pages/{page_id}"

            # Use _links if available for a cleaner URL
            web_link = page.get("_links", {}).get("webui", "")
            if web_link:
                link = f"{self.base_url}/wiki{web_link}"

            pages.append(
                {
                    "source": "confluence",
                    "page_id": page_id,
                    "title": title,
                    "space": space,
                    "type": page_type,
                    "version": version_number,
                    "version_message": version_message,
                    "editor": editor,
                    "updated": updated,
                    "link": link,
                }
            )

        pages.sort(key=lambda p: p["updated"], reverse=True)
        logger.info("Fetched %d Confluence pages since %s", len(pages), since_str)
        return pages

    def search(self, query: str, max_results: int = 3) -> list[dict]:
        """
        Search Confluence content by text. Used by Heartbeat.
        """
        try:
            cql = f'text ~ "{query}" ORDER BY lastModified DESC'
            data = self._api_get(
                "/wiki/rest/api/content/search",
                params={
                    "cql": cql,
                    "limit": max_results,
                    "expand": "version,space",
                },
            )
            results = []
            for page in data.get("results", []):
                space = page.get("space", {}).get("key", "")
                page_id = page.get("id", "")
                version = page.get("version", {})

                web_link = page.get("_links", {}).get("webui", "")
                link = (
                    f"{self.base_url}/wiki{web_link}"
                    if web_link
                    else f"{self.base_url}/wiki/spaces/{space}/pages/{page_id}"
                )

                results.append(
                    {
                        "text": f"[{space}] {page.get('title', '')} (v{version.get('number', '?')}, edited by {version.get('by', {}).get('displayName', 'unknown')})",
                        "ts": version.get("when", ""),
                        "link": link,
                    }
                )
            return results
        except Exception as e:
            logger.debug("Confluence search failed for '%s': %s", query, e)
            return []
