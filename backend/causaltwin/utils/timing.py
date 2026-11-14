"""Timing helpers: measure how long a pipeline step takes and log it."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from causaltwin.utils.activity_log import ActivityLogger, get_activity_logger


class StepTimer:
    """Holds the measured duration of a timed block (filled in when the block ends)."""

    def __init__(self) -> None:
        """Start with no duration and an empty details dict."""
        self.duration_ms: float = 0.0
        self.details: dict[str, Any] = {}


@contextmanager
def timed_step(
    step_name: str,
    message: str,
    logger: ActivityLogger | None = None,
    details: dict[str, Any] | None = None,
    routine: bool = False,
) -> Iterator[StepTimer]:
    """Time the enclosed block and write one activity log entry when it finishes.

    Code inside the block may add entries to `timer.details` to include results
    (for example row counts) in the log entry. `routine=True` marks a high-frequency
    step whose entry goes to the logger's separate routine buffer.
    """
    active_logger = logger or get_activity_logger()
    timer = StepTimer()
    timer.details.update(details or {})
    start_time = time.perf_counter()
    try:
        yield timer
    finally:
        timer.duration_ms = (time.perf_counter() - start_time) * 1000.0
        active_logger.log(step_name, message, duration_ms=timer.duration_ms, details=timer.details, routine=routine)
