"""
TaskFlow → Obsidian one-way sync.

Pulls inbox and today tasks from TaskFlow, converts them to the agent's
task dict schema, and marks them complete in TaskFlow.

Usage:
    from sources.taskflow_sync import TaskFlowSource

    tf = TaskFlowSource(config["taskflow"])
    tasks = tf.fetch_and_clear()
    # Returns list of task dicts compatible with TaskOutput.write_tasks()
"""

import logging
from datetime import datetime, timezone

import timeutil

import requests

logger = logging.getLogger(__name__)

# TaskFlow uses 1-5 priority; agent uses high/medium/low
PRIORITY_MAP = {
    5: "high",
    4: "high",
    3: "medium",
    2: "low",
    1: "low",
}


class TaskFlowSource:
    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "http://localhost:5174/api")
        self.statuses = config.get("statuses", ["inbox", "today"])
        self.timeout = config.get("timeout", 10)

    def _fetch_tasks(self, status: str) -> list[dict]:
        resp = requests.get(
            f"{self.base_url}/tasks",
            params={"status": status},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("data", [])

    def _mark_complete(self, task_id: str) -> bool:
        try:
            resp = requests.post(
                f"{self.base_url}/tasks/{task_id}/complete",
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error("Failed to complete TaskFlow task %s: %s", task_id, e)
            return False

    def _to_agent_task(self, tf_task: dict) -> dict:
        """Convert a TaskFlow task to the agent's task dict schema."""
        priority_num = tf_task.get("priority") or 3
        priority = PRIORITY_MAP.get(priority_num, "medium")

        title = tf_task.get("text", "").strip()

        # Build context from optional fields
        context_parts = []
        if tf_task.get("notes"):
            context_parts.append(tf_task["notes"])
        if tf_task.get("energy_level"):
            context_parts.append(f"Energy: {tf_task['energy_level']}")
        if tf_task.get("tags"):
            context_parts.append(f"Tags: {', '.join(tf_task['tags'])}")

        # Use TaskFlow created_at as first_seen
        created = tf_task.get("created_at", "")
        if created:
            first_seen = created[:16].replace("T", " ")
        else:
            first_seen = timeutil.now().strftime(
                "%Y-%m-%d %H:%M"
            )

        task = {
            "title": title,
            "priority": priority,
            "sources": ["taskflow"],
            "links": [],
            "context": "; ".join(context_parts) if context_parts else "",
            "why": "",
            "first_seen": first_seen,
        }

        if tf_task.get("due_date"):
            task["why"] = f"Due: {tf_task['due_date'][:10]}"

        return task

    def fetch_and_clear(self) -> list[dict]:
        """
        Pull tasks from TaskFlow, convert to agent schema, mark complete.

        Returns:
            List of task dicts compatible with TaskOutput.write_tasks().
            Empty list if nothing new or on failure.
        """
        all_tasks = []
        for status in self.statuses:
            try:
                tasks = self._fetch_tasks(status)
                all_tasks.extend(tasks)
                logger.info("TaskFlow: fetched %d tasks from '%s'", len(tasks), status)
            except requests.RequestException as e:
                logger.error("TaskFlow: failed to fetch '%s': %s", status, e)

        if not all_tasks:
            return []

        # Convert first, then mark complete only after conversion succeeds
        agent_tasks = [self._to_agent_task(t) for t in all_tasks]

        for tf_task in all_tasks:
            self._mark_complete(tf_task["id"])

        logger.info(
            "TaskFlow: synced %d tasks, marked complete in TaskFlow", len(agent_tasks)
        )
        return agent_tasks
