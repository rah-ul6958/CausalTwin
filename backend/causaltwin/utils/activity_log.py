"""Structured activity logger.

Every pipeline step records one entry: timestamp, step name, duration in ms, a short
message, and an optional details dict. Later phases stream these entries to the UI,
so the format is a small Pydantic model that serializes cleanly to JSON.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

_python_logger = logging.getLogger("causaltwin")


class ActivityLogEntry(BaseModel):
    """One structured log entry."""

    timestamp: str
    step_name: str
    duration_ms: float | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ActivityLogger:
    """Collects log entries in memory and notifies subscribers (for live streaming)."""

    def __init__(self, max_entries: int = 5000) -> None:
        """Create an empty logger that keeps at most `max_entries` recent entries."""
        self.max_entries = max_entries
        self._entries: list[ActivityLogEntry] = []
        self._subscribers: list[Callable[[ActivityLogEntry], None]] = []

    def log(
        self,
        step_name: str,
        message: str,
        duration_ms: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> ActivityLogEntry:
        """Record one entry, forward it to Python logging, and notify subscribers."""
        entry = ActivityLogEntry(
            timestamp=datetime.now(UTC).isoformat(timespec="milliseconds"),
            step_name=step_name,
            duration_ms=None if duration_ms is None else round(duration_ms, 2),
            message=message,
            details=details or {},
        )
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]
        duration_text = "" if duration_ms is None else f" ({duration_ms:.1f} ms)"
        _python_logger.info("[%s] %s%s", step_name, message, duration_text)
        for subscriber in list(self._subscribers):
            subscriber(entry)
        return entry

    def subscribe(self, callback: Callable[[ActivityLogEntry], None]) -> None:
        """Register a callback that receives every new entry."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[ActivityLogEntry], None]) -> None:
        """Remove a previously registered callback."""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def entries(self) -> list[ActivityLogEntry]:
        """Return a copy of all stored entries, oldest first."""
        return list(self._entries)

    def clear(self) -> None:
        """Delete all stored entries."""
        self._entries.clear()


_default_logger = ActivityLogger()


def get_activity_logger() -> ActivityLogger:
    """Return the shared application-wide activity logger."""
    return _default_logger
