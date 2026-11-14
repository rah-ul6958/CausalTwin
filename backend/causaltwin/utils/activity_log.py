"""Structured activity logger.

Every pipeline step records one entry: timestamp, step name, duration in ms, a short
message, and an optional details dict. Later phases stream these entries to the UI,
so the format is a small Pydantic model that serializes cleanly to JSON.
"""

import logging
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from causaltwin.config import ACTIVITY_LOG_MAX_ENTRIES, ACTIVITY_LOG_MAX_ROUTINE_ENTRIES

_python_logger = logging.getLogger("causaltwin")


class ActivityLogEntry(BaseModel):
    """One structured log entry."""

    timestamp: str
    step_name: str
    duration_ms: float | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    level: str = "info"  # "info" or "warning"
    routine: bool = False  # high-frequency entry (per-step risk estimate), kept in its own buffer


class ActivityLogger:
    """Collects log entries in memory and notifies subscribers (for live streaming)."""

    def __init__(
        self,
        max_entries: int = ACTIVITY_LOG_MAX_ENTRIES,
        max_routine_entries: int = ACTIVITY_LOG_MAX_ROUTINE_ENTRIES,
    ) -> None:
        """Create an empty logger that keeps at most `max_entries` recent entries.

        Routine entries are kept separately (at most `max_routine_entries`).
        """
        self.max_entries = max_entries
        self.max_routine_entries = max_routine_entries
        self._entries: list[ActivityLogEntry] = []
        self._routine_entries: deque[ActivityLogEntry] = deque(maxlen=max_routine_entries)
        self._subscribers: list[Callable[[ActivityLogEntry], None]] = []

    def log(
        self,
        step_name: str,
        message: str,
        duration_ms: float | None = None,
        details: dict[str, Any] | None = None,
        level: str = "info",
        routine: bool = False,
    ) -> ActivityLogEntry:
        """Record one entry, forward it to Python logging, and notify subscribers.

        `routine=True` marks a high-frequency entry: it goes to the routine buffer and is
        logged at debug level, so it does not flood the main log.
        """
        entry = ActivityLogEntry(
            timestamp=datetime.now(UTC).isoformat(timespec="milliseconds"),
            step_name=step_name,
            duration_ms=None if duration_ms is None else round(duration_ms, 2),
            message=message,
            details=details or {},
            level=level,
            routine=routine,
        )
        duration_text = "" if duration_ms is None else f" ({duration_ms:.1f} ms)"
        if routine:
            self._routine_entries.append(entry)
            _python_logger.debug("[%s] %s%s", step_name, message, duration_text)
        else:
            self._entries.append(entry)
            if len(self._entries) > self.max_entries:
                self._entries = self._entries[-self.max_entries :]
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

    def entries(self, routine: str = "include") -> list[ActivityLogEntry]:
        """Return a copy of the stored entries, oldest first.

        `routine` is "include" (all entries, merged in time order), "exclude" (main
        buffer only) or "only" (routine buffer only).
        """
        if routine == "exclude":
            return list(self._entries)
        if routine == "only":
            return list(self._routine_entries)
        # ISO timestamps in the same format sort correctly as text.
        return sorted([*self._entries, *self._routine_entries], key=lambda entry: entry.timestamp)

    def clear(self) -> None:
        """Delete all stored entries."""
        self._entries.clear()
        self._routine_entries.clear()


_default_logger = ActivityLogger()


def get_activity_logger() -> ActivityLogger:
    """Return the shared application-wide activity logger."""
    return _default_logger
