"""WebSocket broadcast hub: one bounded queue per client, safe to call from any thread."""

import asyncio
import threading
import time
from typing import Any

from causaltwin.config import WEBSOCKET_QUEUE_SIZE


class StreamHub:
    """Fan-out of messages ({"type": ..., "data": ...}) to every connected WebSocket client."""

    def __init__(self) -> None:
        """Create a hub; `bind` must be called from the event loop before clients connect."""
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_thread: int | None = None
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self._pending_logs: list[dict[str, Any]] = []
        self._log_lock = threading.Lock()
        self.sent_counts: dict[str, int] = {}

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the event loop that owns the client queues."""
        self.loop = loop
        self.loop_thread = threading.get_ident()

    def register(self) -> asyncio.Queue[dict[str, Any]]:
        """Add a client and return its queue."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=WEBSOCKET_QUEUE_SIZE)
        self.clients.add(queue)
        return queue

    def unregister(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a client."""
        self.clients.discard(queue)

    def broadcast(self, message_type: str, data: Any) -> None:
        """Send a message to every client (drops a client's oldest message when its queue is full)."""
        message = {"type": message_type, "time": time.time(), "data": data}
        self.sent_counts[message_type] = self.sent_counts.get(message_type, 0) + 1
        if self.loop is None:
            return
        if threading.get_ident() == self.loop_thread:
            self._deliver(message)
        else:
            self.loop.call_soon_threadsafe(self._deliver, message)

    def _deliver(self, message: dict[str, Any]) -> None:
        """Put a message on every client queue (runs on the event loop)."""
        for queue in list(self.clients):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(message)

    def queue_log(self, entry: dict[str, Any]) -> None:
        """Collect an activity log entry; entries are sent in batches by `flush_logs`."""
        with self._log_lock:
            self._pending_logs.append(entry)

    def flush_logs(self) -> None:
        """Send all collected log entries as one batched "log" message."""
        with self._log_lock:
            entries, self._pending_logs = self._pending_logs, []
        if entries:
            self.broadcast("log", entries)
