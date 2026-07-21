"""
Google Meet transcript/notes integration via Calendar + Drive APIs.

Finds meetings you attended, then retrieves any linked transcripts or
meeting notes (Google Docs / Slides created by Meet's built-in notetaking
or Gemini, or attached manually to the calendar event).

First-time setup:
  1. Uses the same OAuth credentials JSON as Gmail.
  2. Run this module directly: python -m sources.meeting_source
     It will open a browser for OAuth consent (Calendar + Drive scopes)
     and save a SEPARATE token file from Gmail.
  3. Subsequent runs use the saved token (auto-refreshes).

Note: You can only access transcripts/notes that were shared with you
or where you were the organizer. If someone else organized the meeting
and hasn't shared the doc, it won't appear.
"""

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import timeutil

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/presentations.readonly",
]

# Max characters of content to fetch per document.
# Synthesis in gpt_processor will compress this before it reaches the main prompt.
# Kept high so the synthesizer has the full document to work from.
MAX_CONTENT_CHARS = 25000

# Google Workspace MIME types we can extract text from
MIME_DOC = "application/vnd.google-apps.document"
MIME_SLIDES = "application/vnd.google-apps.presentation"
EXTRACTABLE_MIMES = {MIME_DOC, MIME_SLIDES}


class MeetingSource:
    def __init__(self, config: dict):
        self.credentials_path = os.path.expanduser(config["credentials_path"])
        self.token_path = os.path.expanduser(config["token_path"])
        self.max_results = config.get("max_results", 20)
        self._calendar_service = None
        self._drive_service = None
        self._docs_service = None
        self._slides_service = None

    def _get_credentials(self) -> Credentials:
        creds = None
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(
                        f"OAuth credentials not found at {self.credentials_path}. "
                        "Download OAuth client JSON from Google Cloud Console."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)

            os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
            with open(self.token_path, "w") as f:
                f.write(creds.to_json())

        return creds

    def _get_calendar_service(self):
        if self._calendar_service:
            return self._calendar_service
        creds = self._get_credentials()
        self._calendar_service = build("calendar", "v3", credentials=creds)
        return self._calendar_service

    def _get_drive_service(self):
        if self._drive_service:
            return self._drive_service
        creds = self._get_credentials()
        self._drive_service = build("drive", "v3", credentials=creds)
        return self._drive_service

    def _get_docs_service(self):
        if self._docs_service:
            return self._docs_service
        creds = self._get_credentials()
        self._docs_service = build("docs", "v1", credentials=creds)
        return self._docs_service

    def _get_slides_service(self):
        if self._slides_service:
            return self._slides_service
        creds = self._get_credentials()
        self._slides_service = build("slides", "v1", credentials=creds)
        return self._slides_service

    def _extract_doc_text(self, doc_id: str) -> str:
        """Extract plain text from a Google Doc."""
        try:
            docs = self._get_docs_service()
            doc = docs.documents().get(documentId=doc_id).execute()
            text_parts = []
            for element in doc.get("body", {}).get("content", []):
                paragraph = element.get("paragraph", {})
                for run in paragraph.get("elements", []):
                    text_run = run.get("textRun", {})
                    content = text_run.get("content", "")
                    if content:
                        text_parts.append(content)
            return "".join(text_parts).strip()
        except Exception as e:
            logger.debug("Failed to extract doc %s: %s", doc_id, e)
            return ""

    def _extract_slides_text(self, presentation_id: str) -> str:
        """Extract plain text from a Google Slides presentation."""
        try:
            slides_svc = self._get_slides_service()
            presentation = (
                slides_svc.presentations()
                .get(presentationId=presentation_id)
                .execute()
            )
            text_parts = []
            for slide in presentation.get("slides", []):
                for element in slide.get("pageElements", []):
                    shape = element.get("shape", {})
                    for text_elem in shape.get("text", {}).get("textElements", []):
                        content = text_elem.get("textRun", {}).get("content", "")
                        if content.strip():
                            text_parts.append(content)
            return "\n".join(text_parts).strip()
        except Exception as e:
            logger.debug("Failed to extract slides %s: %s", presentation_id, e)
            return ""

    def _extract_content(self, doc_id: str, mime: str) -> str:
        """Dispatch to the right extractor based on MIME type."""
        if mime == MIME_SLIDES:
            return self._extract_slides_text(doc_id)
        return self._extract_doc_text(doc_id)

    def _find_meeting_docs(self, event: dict) -> list[dict]:
        """
        Find docs/slides associated with a calendar event.

        Checks in order:
        1. Event attachments (Docs and Slides)
        2. Event description (Google Doc and Slides URLs)
        3. Drive search (time window + Gemini naming patterns + title match)
        """
        docs_found = []
        seen_ids = set()
        summary = event.get("summary", "(no title)")

        # 1. Event attachments — accept Docs and Slides
        for att in event.get("attachments", []):
            file_id = att.get("fileId", "")
            mime = att.get("mimeType", "")
            title = att.get("title", "")
            if file_id and mime in EXTRACTABLE_MIMES and file_id not in seen_ids:
                seen_ids.add(file_id)
                link = (
                    f"https://docs.google.com/presentation/d/{file_id}"
                    if mime == MIME_SLIDES
                    else f"https://docs.google.com/document/d/{file_id}"
                )
                docs_found.append(
                    {"doc_id": file_id, "mime": mime, "title": title,
                     "source_method": "attachment", "link": link}
                )

        # 2. Description links — Docs and Slides URLs
        description = event.get("description", "")
        if description:
            for doc_id in re.findall(
                r"https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)", description
            ):
                if doc_id not in seen_ids:
                    seen_ids.add(doc_id)
                    docs_found.append(
                        {"doc_id": doc_id, "mime": MIME_DOC,
                         "title": "(linked in description)", "source_method": "description_link",
                         "link": f"https://docs.google.com/document/d/{doc_id}"}
                    )
            for slide_id in re.findall(
                r"https://docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)", description
            ):
                if slide_id not in seen_ids:
                    seen_ids.add(slide_id)
                    docs_found.append(
                        {"doc_id": slide_id, "mime": MIME_SLIDES,
                         "title": "(linked in description)", "source_method": "description_link",
                         "link": f"https://docs.google.com/presentation/d/{slide_id}"}
                    )

        # 3. Drive search fallback
        if not docs_found:
            docs_found.extend(self._search_drive_for_meeting(summary, event, seen_ids))

        logger.debug("Event '%s': found %d docs total", summary, len(docs_found))
        return docs_found

    def _search_drive_for_meeting(
        self, meeting_title: str, event: dict, seen_ids: set
    ) -> list[dict]:
        """
        Search Drive for notes/transcripts associated with a meeting.

        Query order (stops at first hit):
        1. Time-window: keyword names (notes/transcript/gemini) in Doc or Slides
        2. Gemini pattern: "Notes from [title]" anywhere in Drive
        3. Title match: doc/slides named after the meeting
        4. Broad time-window: any Doc or Slides modified that day (last resort)
        """
        drive = self._get_drive_service()
        results = []

        # Build time filter for the meeting's calendar day
        start_str = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", ""))
        time_filter = ""
        if start_str and "T" in start_str:
            try:
                from dateutil import parser as dtparser
                start_dt = dtparser.parse(start_str).astimezone(timezone.utc)
                day_start = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = start_dt.replace(hour=23, minute=59, second=59, microsecond=0)
                time_filter = (
                    f" and modifiedTime >= '{day_start.strftime('%Y-%m-%dT%H:%M:%SZ')}'"
                    f" and modifiedTime <= '{day_end.strftime('%Y-%m-%dT%H:%M:%SZ')}'"
                )
            except Exception as e:
                logger.debug("Could not parse event time: %s", e)

        base_mime = (
            "(mimeType = 'application/vnd.google-apps.document'"
            " or mimeType = 'application/vnd.google-apps.presentation')"
        )
        escaped_title = self._escape_drive_query(meeting_title[:40])

        queries = []

        # 1. Time-window + keyword names (notes/transcript/gemini)
        if time_filter:
            queries.append((
                f"(name contains 'notes' or name contains 'transcript'"
                f" or name contains 'gemini' or name contains 'meeting')"
                f" and {base_mime}{time_filter}",
                5,
            ))

        # 2. Gemini "Notes from [title]" pattern — no time restriction
        if escaped_title:
            queries.append((
                f"name contains 'Notes from {escaped_title[:30]}' and {base_mime}",
                3,
            ))

        # 3. Title match in name — no time restriction
        if escaped_title:
            queries.append((
                f"name contains '{escaped_title}' and {base_mime}",
                3,
            ))

        # 4. Broad: any Doc/Slides modified that day (last resort, small page)
        if time_filter:
            queries.append((f"{base_mime}{time_filter}", 3))

        for query, page_size in queries:
            try:
                resp = (
                    drive.files()
                    .list(
                        q=query,
                        fields="files(id,name,mimeType,modifiedTime,webViewLink)",
                        pageSize=page_size,
                        orderBy="modifiedTime desc",
                    )
                    .execute()
                )
                files = resp.get("files", [])
                logger.debug(
                    "Drive query for '%s' → %d files: %s",
                    meeting_title,
                    len(files),
                    [f.get("name") for f in files],
                )
                for f in files:
                    file_id = f["id"]
                    if file_id not in seen_ids:
                        seen_ids.add(file_id)
                        mime = f.get("mimeType", MIME_DOC)
                        results.append(
                            {
                                "doc_id": file_id,
                                "mime": mime,
                                "title": f.get("name", ""),
                                "source_method": "drive_search",
                                "link": f.get(
                                    "webViewLink",
                                    f"https://docs.google.com/document/d/{file_id}",
                                ),
                            }
                        )
                if results:
                    break
            except Exception as e:
                logger.warning("Drive search failed for '%s': %s", meeting_title, e)

        return results

    @staticmethod
    def _escape_drive_query(text: str) -> str:
        return text.replace("\\", "\\\\").replace("'", "\\'")

    def _is_real_meeting(self, event: dict, meet_link: str) -> bool:
        """
        Return False for calendar blocks that aren't real meetings:
        all-day blocks, OOO entries, personal blocks with no attendees
        and no Meet link.
        """
        # All-day events have a 'date' key instead of 'dateTime'
        if "date" in event.get("start", {}) and "dateTime" not in event.get("start", {}):
            return False
        has_other_attendees = any(
            not a.get("self") for a in event.get("attendees", [])
        )
        has_attachments = bool(event.get("attachments"))
        return bool(meet_link or has_other_attendees or has_attachments)

    def _process_event(self, event: dict, since: datetime) -> dict | None:
        """Process a single calendar event. Returns meeting dict or None."""
        summary = event.get("summary", "(no title)")

        start_str = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", ""))
        end_str = event.get("end", {}).get("dateTime", event.get("end", {}).get("date", ""))

        attendees = [
            f"{a.get('displayName', a.get('email', 'unknown'))} ({a.get('responseStatus', '')})"
            for a in event.get("attendees", [])
            if not a.get("self")
        ]

        meet_link = ""
        for ep in event.get("conferenceData", {}).get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri", "")
                break

        if not self._is_real_meeting(event, meet_link):
            logger.debug("Skipping non-meeting event '%s'", summary)
            return None

        meeting_docs = self._find_meeting_docs(event)

        doc_contents = []
        for doc in meeting_docs:
            text = self._extract_content(doc["doc_id"], doc.get("mime", MIME_DOC))
            doc_contents.append(
                {
                    "title": doc["title"],
                    "content": text[:MAX_CONTENT_CHARS] if text else "",
                    "has_content": bool(text),
                    "link": doc["link"],
                    "source_method": doc["source_method"],
                }
            )

        cal_link = f"https://calendar.google.com/calendar/event?eid={event.get('id', '')}"

        return {
            "source": "meeting",
            "title": summary,
            "start": start_str,
            "end": end_str,
            "attendees": attendees,
            "meet_link": meet_link,
            "calendar_link": cal_link,
            "documents": doc_contents,
        }

    def fetch(self, since: datetime) -> list[dict]:
        """
        Fetch meetings since the given timestamp.
        Returns all real meetings (with attendees or Meet link), whether or not
        notes/transcripts were found. Meetings without docs are surfaced with
        an empty documents list so the agent knows they happened.
        """
        calendar = self._get_calendar_service()

        time_min = since.isoformat()
        time_max = timeutil.now().isoformat()

        try:
            events_result = (
                calendar.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=self.max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except Exception as e:
            logger.error("Calendar API error: %s", e)
            return []

        events = events_result.get("items", [])
        if not events:
            logger.info("No calendar events since %s", since.isoformat())
            return []

        # Pre-initialize all API clients before spawning threads. Each build()
        # makes HTTPS requests; concurrent lazy init from worker threads corrupts
        # shared OpenSSL state and causes a segfault in tls_get_more_records.
        self._get_drive_service()
        self._get_docs_service()
        self._get_slides_service()

        meetings = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self._process_event, event, since): event.get("summary", "")
                for event in events
            }
            for future in as_completed(futures):
                title = futures[future]
                try:
                    result = future.result()
                    if result:
                        meetings.append(result)
                except Exception as e:
                    logger.warning("Failed to process meeting '%s': %s", title, e)

        meetings.sort(key=lambda m: m["start"])

        with_docs = sum(1 for m in meetings if m["documents"])
        logger.info(
            "Fetched %d meetings (%d with docs, %d without) from %d calendar events since %s",
            len(meetings),
            with_docs,
            len(meetings) - with_docs,
            len(events),
            since.isoformat(),
        )
        return meetings

    def fetch_upcoming(self, within_minutes: int = 15) -> list[dict]:
        """
        Fetch real meetings starting within the next `within_minutes`. Unlike
        fetch(), this looks forward, not back, and never tries to find
        transcripts/notes — those don't exist yet for a meeting that hasn't
        happened. Used for pre-meeting briefs, not post-meeting synthesis.
        """
        from datetime import timedelta

        calendar = self._get_calendar_service()
        now = timeutil.now()
        time_min = now.isoformat()
        time_max = (now + timedelta(minutes=within_minutes)).isoformat()

        try:
            events_result = (
                calendar.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=20,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except Exception as e:
            logger.error("Calendar API error (fetch_upcoming): %s", e)
            return []

        meetings = []
        for event in events_result.get("items", []):
            summary = event.get("summary", "(no title)")
            meet_link = ""
            for ep in event.get("conferenceData", {}).get("entryPoints", []):
                if ep.get("entryPointType") == "video":
                    meet_link = ep.get("uri", "")
                    break

            if not self._is_real_meeting(event, meet_link):
                continue

            attendees = [
                a.get("displayName", a.get("email", "unknown"))
                for a in event.get("attendees", [])
                if not a.get("self")
            ]
            meetings.append({
                "event_id": event.get("id", ""),
                "title": summary,
                "start": event.get("start", {}).get("dateTime", ""),
                "end": event.get("end", {}).get("dateTime", ""),
                "attendees": attendees,
                "meet_link": meet_link,
                "calendar_link": f"https://calendar.google.com/calendar/event?eid={event.get('id', '')}",
            })

        return meetings

    def search(self, query: str, max_results: int = 3) -> list[dict]:
        """Search for meeting-related docs in Drive. Used by Heartbeat."""
        drive = self._get_drive_service()
        base_mime = (
            "(mimeType = 'application/vnd.google-apps.document'"
            " or mimeType = 'application/vnd.google-apps.presentation')"
        )
        try:
            escaped = self._escape_drive_query(query)
            resp = (
                drive.files()
                .list(
                    q=(
                        f"fullText contains '{escaped}' and {base_mime}"
                        f" and (name contains 'transcript' or name contains 'notes'"
                        f" or name contains 'meeting' or name contains 'gemini')"
                    ),
                    fields="files(id,name,modifiedTime,webViewLink)",
                    pageSize=max_results,
                    orderBy="modifiedTime desc",
                )
                .execute()
            )
            return [
                {"text": f.get("name", ""), "ts": f.get("modifiedTime", ""),
                 "link": f.get("webViewLink", "")}
                for f in resp.get("files", [])
            ]
        except Exception as e:
            logger.debug("Meeting doc search failed for '%s': %s", query, e)
            return []


if __name__ == "__main__":
    """Run directly to complete OAuth setup: python -m sources.meeting_source"""
    import yaml

    with open("agent/config.yaml") as f:
        cfg = yaml.safe_load(f)

    meetings = MeetingSource(cfg["meetings"])
    meetings._get_credentials()
    print("Meetings OAuth setup complete. Token saved.")
    print(f"Scopes: {SCOPES}")
