"""
Slack integration using browser-extracted xoxc token + d cookie.

Token refresh: These tokens expire. When they do, repeat the dev tools extraction.
The agent will log a clear error when authentication fails so you know to refresh.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import timeutil

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://slack.com/api"

# Max parallel requests. Slack's rate limit is ~50 req/min for tier 3 methods.
# 10 workers keeps us well under that while still being fast.
MAX_WORKERS = 10


class SlackSource:
    def __init__(self, config: dict):
        self.token = config["token"]
        self.cookie_d = config["cookie_d"]
        self.workspace = config.get("workspace", "your-workspace")
        self.priority_channels = config.get("priority_channels", [])
        self.include_dms = config.get("include_dms", True)
        self.include_mentions = config.get("include_mentions", True)
        self._seen_profiles: dict[str, dict] = {}
        self._my_user_id: str | None = None

    @property
    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Cookie": f"d={self.cookie_d}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _api(self, method: str, **kwargs) -> dict:
        resp = requests.post(
            f"{BASE_URL}/{method}", headers=self._headers, data=kwargs, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            error = data.get("error", "unknown")
            if error in ("token_revoked", "invalid_auth", "not_authed"):
                logger.error(
                    "Slack auth failed (%s). Re-extract your xoxc token and d cookie from browser dev tools.",
                    error,
                )
            raise RuntimeError(f"Slack API error: {error}")
        return data

    def _get_user_id(self) -> str:
        """Get the authenticated user's ID for filtering mentions."""
        data = self._api("auth.test")
        self._my_user_id = data["user_id"]
        return data["user_id"]

    def _get_dm_channels(self) -> tuple[list[str], list[str]]:
        """Get DM channel IDs, separated into 1-1 and group.
        Returns (im_ids, mpim_ids)."""
        im_ids = []
        mpim_ids = []

        try:
            data = self._api("conversations.list", types="im", limit=200)
            im_ids = [ch["id"] for ch in data.get("channels", [])]
        except Exception as e:
            logger.warning("Failed to fetch 1-1 DM channels: %s", e)

        try:
            data = self._api("conversations.list", types="mpim", limit=200)
            mpim_ids = [ch["id"] for ch in data.get("channels", [])]
        except Exception as e:
            logger.warning("Failed to fetch group DM channels: %s", e)

        return im_ids, mpim_ids

    def _get_channel_name(self, channel_id: str) -> str:
        """Best-effort channel name lookup."""
        try:
            data = self._api("conversations.info", channel=channel_id)
            ch = data.get("channel", {})
            return ch.get("name") or ch.get("id", channel_id)
        except Exception:
            return channel_id

    def _get_username(self, user_id: str, user_cache: dict) -> str:
        if user_id in user_cache:
            return user_cache[user_id]
        try:
            data = self._api("users.info", user=user_id)
            user = data["user"]
            profile = user.get("profile", {})
            display_name = profile.get("display_name", "")
            real_name = user.get("real_name", user_id)
            name = display_name or real_name
            user_cache[user_id] = name
            self._seen_profiles[user_id] = {
                "slack_id": user_id,
                "name": real_name,
                "nickname": display_name,
                "email": profile.get("email", ""),
                "role": profile.get("title", ""),
                "is_bot": user.get("is_bot", False),
            }
            return name
        except Exception:
            return user_id

    def _fetch_channel_history(
        self, channel_id: str, oldest: str
    ) -> tuple[str, list[dict]]:
        """Fetch history for a single channel. Returns (channel_id, messages).
        Designed to be called from a thread pool."""
        try:
            data = self._api(
                "conversations.history", channel=channel_id, oldest=oldest, limit=50
            )
            return (channel_id, data.get("messages", []))
        except Exception as e:
            logger.warning("Failed to fetch channel %s: %s", channel_id, e)
            return (channel_id, [])

    def _fetch_thread_replies(
        self, channel_id: str, thread_ts: str, oldest: str
    ) -> tuple[str, str, list[dict]]:
        """Fetch replies in a thread since oldest. Returns (channel_id, thread_ts, replies).
        Designed to be called from a thread pool."""
        try:
            data = self._api(
                "conversations.replies",
                channel=channel_id,
                ts=thread_ts,
                oldest=oldest,
                limit=50,
            )
            msgs = data.get("messages", [])
            # conversations.replies includes the parent as the first item; skip it
            replies = [m for m in msgs if m.get("ts") != thread_ts]
            return (channel_id, thread_ts, replies)
        except Exception as e:
            logger.warning("Failed to fetch thread %s in %s: %s", thread_ts, channel_id, e)
            return (channel_id, thread_ts, [])

    def fetch(self, since: datetime) -> list[dict]:
        """
        Fetch messages since the given timestamp.
        - Priority channels: all messages
        - 1-1 DMs: all messages
        - Group DMs: only messages that @mention you

        Uses thread pool to fetch channel histories in parallel.
        """
        messages = []
        user_cache = {}
        oldest = str(since.timestamp())

        # Track channel types for filtering
        group_dm_ids = set()

        channels_to_check = list(self.priority_channels)

        if self.include_dms:
            try:
                im_ids, mpim_ids = self._get_dm_channels()
                channels_to_check.extend(im_ids)
                channels_to_check.extend(mpim_ids)
                group_dm_ids = set(mpim_ids)
            except Exception as e:
                logger.warning("Failed to fetch DM channels: %s", e)

        my_user_id = None
        if self.include_mentions:
            try:
                my_user_id = self._get_user_id()
            except Exception as e:
                logger.warning("Failed to get own user ID: %s", e)

        # Deduplicate channel IDs
        seen_channels = set()
        unique_channels = []
        for ch in channels_to_check:
            if ch not in seen_channels:
                seen_channels.add(ch)
                unique_channels.append(ch)

        logger.info(
            "Fetching history from %d channels (%d workers)...",
            len(unique_channels),
            MAX_WORKERS,
        )

        # Parallel fetch of all channel histories
        channel_results = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._fetch_channel_history, ch_id, oldest): ch_id
                for ch_id in unique_channels
            }
            for future in as_completed(futures):
                ch_id, msgs = future.result()
                if msgs:
                    channel_results[ch_id] = msgs

        logger.info(
            "Got messages from %d / %d channels",
            len(channel_results),
            len(unique_channels),
        )

        # Fetch thread replies for any top-level messages that have them
        thread_jobs = [
            (ch_id, msg["ts"])
            for ch_id, raw_msgs in channel_results.items()
            for msg in raw_msgs
            if int(msg.get("reply_count", 0)) > 0
        ]
        thread_results: dict[tuple[str, str], list[dict]] = {}
        if thread_jobs:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {
                    executor.submit(self._fetch_thread_replies, ch_id, ts, oldest): (ch_id, ts)
                    for ch_id, ts in thread_jobs
                }
                for future in as_completed(futures):
                    ch_id, ts = futures[future]
                    _, _, replies = future.result()
                    if replies:
                        thread_results[(ch_id, ts)] = replies
            logger.info(
                "Fetched replies from %d / %d threads",
                len(thread_results),
                len(thread_jobs),
            )

        def _build_message(msg, channel_id, channel_name, is_group_dm, is_thread_reply=False, thread_ts=None):
            subtype = msg.get("subtype")
            if subtype and subtype not in ("bot_message",):
                return None
            text = msg.get("text", "")
            sender = self._get_username(msg.get("user", "unknown"), user_cache)
            is_mention = my_user_id is not None and f"<@{my_user_id}>" in text
            raw_ts = msg["ts"]
            permalink = f"https://{self.workspace}.slack.com/archives/{channel_id}/p{raw_ts.replace('.', '')}"
            if is_thread_reply and thread_ts:
                permalink += f"?thread_ts={thread_ts}&cid={channel_id}"
            return {
                "source": "slack",
                "channel": channel_name,
                "sender": sender,
                "text": text,
                "ts": datetime.fromtimestamp(float(raw_ts), tz=timeutil.get_timezone()).isoformat(),
                "is_dm": channel_id.startswith("D"),
                "is_group_dm": is_group_dm,
                "is_mention": is_mention,
                "is_thread_reply": is_thread_reply,
                "link": permalink,
            }

        # Process results: resolve names and apply filters
        # Channel name lookups only for channels that had messages (much smaller set)
        for channel_id, raw_msgs in channel_results.items():
            channel_name = self._get_channel_name(channel_id)
            is_group_dm = channel_id in group_dm_ids

            for msg in raw_msgs:
                built = _build_message(msg, channel_id, channel_name, is_group_dm)
                if built:
                    messages.append(built)

                # Append any thread replies under this message
                for reply in thread_results.get((channel_id, msg["ts"]), []):
                    built_reply = _build_message(
                        reply, channel_id, channel_name, is_group_dm,
                        is_thread_reply=True, thread_ts=msg["ts"]
                    )
                    if built_reply:
                        messages.append(built_reply)

        messages.sort(key=lambda m: m["ts"])
        logger.info(
            "Fetched %d Slack messages since %s", len(messages), since.isoformat()
        )
        return messages

    def unknown_contacts(self, known_slack_ids: set) -> list[dict]:
        """Return profiles seen this run that aren't in the known set, excluding bots and self."""
        return [
            p for uid, p in self._seen_profiles.items()
            if uid not in known_slack_ids
            and not p.get("is_bot")
            and uid != self._my_user_id
        ]
