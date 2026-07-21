"""
Gmail integration via Google API.

First-time setup:
  1. Go to Google Cloud Console > APIs & Services > Credentials
  2. Create an OAuth 2.0 Client ID (Desktop app type)
  3. Download the JSON and save it to the path in config.yaml (credentials_path)
  4. Run this module directly: python -m sources.gmail
     It will open a browser for OAuth consent and save the token.
  5. Subsequent runs use the saved token (auto-refreshes).
"""

import base64
import logging
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import timeutil

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailSource:
    def __init__(self, config: dict):
        self.credentials_path = os.path.expanduser(config["credentials_path"])
        self.token_path = os.path.expanduser(config["token_path"])
        self.query_filter = config.get("query_filter", "is:unread")
        self.max_results = config.get("max_results", 25)
        self._service = None

    def _get_service(self):
        if self._service:
            return self._service

        creds = None
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(
                        f"Gmail credentials not found at {self.credentials_path}. "
                        "Download OAuth client JSON from Google Cloud Console."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)

            os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
            with open(self.token_path, "w") as f:
                f.write(creds.to_json())

        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def _get_header(self, headers: list, name: str) -> str:
        for h in headers:
            if h["name"].lower() == name.lower():
                return h["value"]
        return ""

    def _get_body_text(self, payload: dict) -> str:
        """Extract plain text body from message payload, handling multipart."""
        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get(
            "data"
        ):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
                "utf-8", errors="replace"
            )

        parts = payload.get("parts", [])
        for part in parts:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get(
                "data"
            ):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode(
                    "utf-8", errors="replace"
                )
            # Recurse into nested multipart
            if part.get("parts"):
                result = self._get_body_text(part)
                if result:
                    return result
        return ""

    def fetch(self, since: datetime) -> list[dict]:
        """
        Fetch unread emails since the given timestamp.
        Returns list of dicts: {source, from, subject, snippet, body_preview, ts}
        """
        service = self._get_service()

        # Gmail search uses epoch seconds for after:
        after_epoch = int(since.timestamp())
        query = f"{self.query_filter} after:{after_epoch}"

        try:
            results = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=self.max_results)
                .execute()
            )
        except Exception as e:
            logger.error("Gmail API error: %s", e)
            return []

        message_ids = results.get("messages", [])
        if not message_ids:
            logger.info("No new emails since %s", since.isoformat())
            return []

        emails = []
        for msg_ref in message_ids:
            try:
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=msg_ref["id"], format="full")
                    .execute()
                )

                headers = msg.get("payload", {}).get("headers", [])
                sender = self._get_header(headers, "From")
                subject = self._get_header(headers, "Subject")
                date_str = self._get_header(headers, "Date")

                # Parse timestamp
                try:
                    ts = parsedate_to_datetime(date_str).astimezone(
                        timeutil.get_timezone()
                    )
                except Exception:
                    ts = datetime.fromtimestamp(
                        int(msg["internalDate"]) / 1000, tz=timeutil.get_timezone()
                    )

                # Get body preview (truncate to keep token budget reasonable)
                body = self._get_body_text(msg.get("payload", {}))
                body_preview = body[:1500] if body else msg.get("snippet", "")

                emails.append(
                    {
                        "source": "gmail",
                        "from": sender,
                        "subject": subject,
                        "snippet": msg.get("snippet", ""),
                        "body_preview": body_preview,
                        "ts": ts.isoformat(),
                        "gmail_id": msg_ref["id"],
                        "link": f"https://mail.google.com/mail/u/0/#inbox/{msg_ref['id']}",
                    }
                )
            except Exception as e:
                logger.warning("Failed to fetch email %s: %s", msg_ref["id"], e)

        emails.sort(key=lambda e: e["ts"])
        logger.info("Fetched %d emails since %s", len(emails), since.isoformat())
        return emails


if __name__ == "__main__":
    """Run directly to complete OAuth setup: python -m sources.gmail"""
    import yaml

    with open("agent/config.yaml") as f:
        cfg = yaml.safe_load(f)

    gmail = GmailSource(cfg["gmail"])
    service = gmail._get_service()
    print("Gmail OAuth setup complete. Token saved.")
