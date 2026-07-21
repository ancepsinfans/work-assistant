"""
Desktop notifications for task and meeting-brief alerts.

- macOS: ``osascript`` (Notification Center)
- Windows: PowerShell toast (Windows 10+)
- Other platforms: log and skip (agent continues normally)
"""

from __future__ import annotations

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)


def _esc_applescript(text: str) -> str:
    """Escape a string for safe embedding in an AppleScript double-quoted literal."""
    text = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
    return f'"{text}"'


def _esc_powershell(text: str) -> str:
    """Escape a string for single-quoted PowerShell literals."""
    return text.replace("'", "''")[:400]


def _notify_macos(title: str, message: str, subtitle: str) -> bool:
    """Fire a macOS notification via osascript."""
    script = f"display notification {_esc_applescript(message)} with title {_esc_applescript(title)}"
    if subtitle:
        script += f" subtitle {_esc_applescript(subtitle)}"
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=5)
    return True


def _notify_windows(title: str, message: str, subtitle: str) -> bool:
    """
    Fire a Windows 10+ toast notification via PowerShell.

    Uses built-in WinRT APIs — no extra packages required.
    """
    body = message if not subtitle else f"{subtitle}\n{message}"
    ps_title = _esc_powershell(title)
    ps_body = _esc_powershell(body)
    ps_app = _esc_powershell("Work Assistant")
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
    [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$nodes = $template.GetElementsByTagName('text')
$nodes.Item(0).AppendChild($template.CreateTextNode('{ps_title}')) | Out-Null
$nodes.Item(1).AppendChild($template.CreateTextNode('{ps_body}')) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{ps_app}').Show($toast)
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True,
        capture_output=True,
        timeout=10,
    )
    return True


def notify(title: str, message: str, subtitle: str = "") -> bool:
    """
    Show a desktop notification when supported on the current OS.

    Returns True when the notification backend ran without error.
    Failures are logged and return False — the agent run is never aborted.
    """
    title = (title or "")[:200]
    message = (message or "")[:400]
    subtitle = (subtitle or "")[:200]

    try:
        if sys.platform == "darwin":
            return _notify_macos(title, message, subtitle)
        if sys.platform == "win32":
            return _notify_windows(title, message, subtitle)
        logger.info("Notifications not supported on %s — skipped", sys.platform)
        return False
    except Exception as exc:
        logger.warning("Notification failed: %s", exc)
        return False
