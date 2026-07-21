"""
Tracks last-checked timestamps per source so we don't reprocess old messages.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import timeutil


class StateDB:
    def __init__(self, db_path: str):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS last_checked (
                    source TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL
                )
            """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT,
                    tasks_created INTEGER DEFAULT 0,
                    error TEXT
                )
            """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_timestamp TEXT NOT NULL,
                    tasks_open INTEGER DEFAULT 0,
                    tasks_resolved INTEGER DEFAULT 0,
                    close_rate REAL DEFAULT 0.0
                )
            """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_timestamp TEXT NOT NULL,
                    pass_name TEXT NOT NULL,
                    chars_in INTEGER DEFAULT 0,
                    chars_out INTEGER DEFAULT 0,
                    duration_ms INTEGER DEFAULT 0
                )
            """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS resolved_tasks (
                    title TEXT NOT NULL,
                    resolved_at TEXT NOT NULL
                )
            """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS briefed_meetings (
                    event_id TEXT PRIMARY KEY,
                    briefed_at TEXT NOT NULL
                )
            """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'medium',
                    status TEXT NOT NULL DEFAULT 'open',
                    why TEXT,
                    context TEXT,
                    sources TEXT,
                    links TEXT,
                    route_to TEXT,
                    suggested_response TEXT,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    resolved_at TEXT
                )
            """
            )

    def get_last_checked(self, source: str) -> datetime:
        """Returns last checked time for a source, or 1 hour ago if never checked."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT timestamp FROM last_checked WHERE source = ?", (source,)
            ).fetchone()
        if row:
            return datetime.fromisoformat(row[0])
        # Default: 1 hour ago
        from datetime import timedelta

        return timeutil.now() - timedelta(hours=1)

    def set_last_checked(self, source: str, ts: datetime = None):
        ts = ts or timeutil.now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO last_checked (source, timestamp) VALUES (?, ?)",
                (source, ts.isoformat()),
            )

    def log_run(self, status: str, tasks_created: int = 0, error: str = None):
        now = timeutil.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO run_log (started_at, finished_at, status, tasks_created, error) VALUES (?, ?, ?, ?, ?)",
                (now, now, status, tasks_created, error),
            )

    def record_task_metrics(self, tasks_open: int, tasks_resolved: int):
        now = timeutil.now().isoformat()
        total = tasks_open + tasks_resolved
        close_rate = tasks_resolved / total if total > 0 else 0.0
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO task_metrics (run_timestamp, tasks_open, tasks_resolved, close_rate) VALUES (?, ?, ?, ?)",
                (now, tasks_open, tasks_resolved, close_rate),
            )

    def get_close_rate_summary(self, n: int = 10) -> str:
        """Returns a brief text summary of recent close rates for GPT context."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT tasks_open, tasks_resolved, close_rate FROM task_metrics ORDER BY run_timestamp DESC LIMIT ?",
                (n,),
            ).fetchall()
        if not rows:
            return "No close rate history yet."
        rates = [f"{r[2]:.0%}" for r in rows]
        avg = sum(r[2] for r in rows) / len(rows)
        return f"Last {len(rows)} runs: avg close rate {avg:.0%}. Per-run (newest first): {', '.join(rates)}"

    def get_open_tasks(self) -> list[dict]:
        """Return all open tasks."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = 'open' ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_task(self, task: dict):
        """Insert a new task or update an existing one by id. Links and sources are accumulated."""
        import json
        now = timeutil.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT sources, links, created_at FROM tasks WHERE id = ?", (task["id"],)
            ).fetchone()

            if existing:
                merged_sources = list(dict.fromkeys(
                    json.loads(existing["sources"] or "[]") + task.get("sources", [])
                ))
                merged_links = list(dict.fromkeys(
                    json.loads(existing["links"] or "[]") + task.get("links", [])
                ))
                created_at = existing["created_at"]
            else:
                merged_sources = task.get("sources", [])
                merged_links = task.get("links", [])
                created_at = task.get("created_at", now)

            conn.execute(
                """
                INSERT INTO tasks (id, title, priority, status, why, context, sources, links,
                    route_to, suggested_response, created_at, last_seen_at)
                VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    priority = excluded.priority,
                    why = excluded.why,
                    context = excluded.context,
                    sources = excluded.sources,
                    links = excluded.links,
                    route_to = excluded.route_to,
                    suggested_response = excluded.suggested_response,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    task["id"], task["title"], task.get("priority", "medium"),
                    task.get("why", ""), task.get("context", ""),
                    json.dumps(merged_sources), json.dumps(merged_links),
                    task.get("route_to"), task.get("suggested_response"),
                    created_at, now,
                ),
            )

    def mark_tasks_stale(self, seen_ids: set):
        """Mark any open tasks not in seen_ids as stale."""
        if not seen_ids:
            return
        with sqlite3.connect(self.db_path) as conn:
            open_ids = [
                r[0] for r in conn.execute(
                    "SELECT id FROM tasks WHERE status = 'open'"
                ).fetchall()
            ]
        stale_ids = [i for i in open_ids if i not in seen_ids]
        if stale_ids:
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany(
                    "UPDATE tasks SET status = 'stale' WHERE id = ?",
                    [(i,) for i in stale_ids],
                )

    def resolve_task_by_title(self, title: str):
        """Mark a task resolved by title match (from markdown [x] mechanic)."""
        now = timeutil.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'resolved', resolved_at = ? WHERE lower(title) = lower(?) AND status = 'open'",
                (now, title),
            )

    def persist_resolved_tasks(self, titles: list[str]):
        """Store newly resolved task titles so they stay suppressed across future runs."""
        if not titles:
            return
        now = timeutil.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO resolved_tasks (title, resolved_at) VALUES (?, ?)",
                [(t, now) for t in titles],
            )

    def get_recent_resolved(self, days: int = 7) -> list[str]:
        """Return all resolved task titles from the past N days."""
        from datetime import timedelta
        cutoff = (timeutil.now() - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT title FROM resolved_tasks WHERE resolved_at >= ?",
                (cutoff,),
            ).fetchall()
        return [r[0] for r in rows]

    def record_run_usage(
        self, pass_name: str, chars_in: int, chars_out: int, duration_ms: int
    ):
        now = timeutil.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO run_usage (run_timestamp, pass_name, chars_in, chars_out, duration_ms) VALUES (?, ?, ?, ?, ?)",
                (now, pass_name, chars_in, chars_out, duration_ms),
            )

    def has_been_briefed(self, event_id: str) -> bool:
        """Whether a pre-meeting brief has already been generated for this calendar event."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM briefed_meetings WHERE event_id = ?", (event_id,)
            ).fetchone()
        return row is not None

    def mark_briefed(self, event_id: str):
        now = timeutil.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO briefed_meetings (event_id, briefed_at) VALUES (?, ?)",
                (event_id, now),
            )
