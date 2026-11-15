"""Per-viewer live simulations ("sessions").

Why: on a public deployment several judges may use the app at the same time. With one live
simulation for everybody, one viewer's Reset, fault injection or guided demo changed what every
other viewer saw.

How:
- Each browser tab makes up a random id and sends it with every request: the
  X-CausalTwin-Session header, or ?session= where a header cannot be sent (the WebSocket and
  download links).
- Each id gets its own Session: a live loop with its own simulator and its own copy of the
  engine, its own guided demo, and its own message hub. The trained models are shared and
  only read.
- Requests without a valid id (curl, health checks, scripts) use the shared session, which
  behaves like the single live simulation of earlier versions.

Limits (all sessions live in one Python process and share its CPU):
- At most `max_sessions` private sessions at once.
- A private session steps only while its viewer's WebSocket is connected.
- A private session without a viewer for SESSION_IDLE_SECONDS is deleted.
- When all slots are taken, a new viewer takes over the slot of the session that has gone
  unwatched the longest (at least SESSION_EVICT_AFTER_SECONDS). If every session is being
  watched, the new viewer joins the shared session, and its status says so.
- With real machine data there is one machine, so every viewer shares one session.
"""

import asyncio
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import HTTPConnection

from causaltwin.api.hub import StreamHub
from causaltwin.api.live import DemoScript, LiveLoop
from causaltwin.api.runtime import RuntimeSettings
from causaltwin.config import (
    MAX_SESSIONS_ENV,
    SECONDS_PER_STEP,
    SESSION_EVICT_AFTER_SECONDS,
    SESSION_HEADER,
    SESSION_ID_PATTERN,
    SESSION_IDLE_SECONDS,
    SESSION_JANITOR_SECONDS,
    SESSION_QUERY_PARAMETER,
    SHARED_SESSION_ID,
)
from causaltwin.intelligence.training import TrainedModels
from causaltwin.utils.activity_log import ActivityLogEntry, get_activity_logger

_VALID_ID = re.compile(SESSION_ID_PATTERN)


def valid_session_id(requested: str | None) -> str | None:
    """The requested id if it has the expected form (8 to 64 letters, digits, - or _), else None."""
    if requested is None or not _VALID_ID.match(requested):
        return None
    return requested


def requested_session_id(connection: HTTPConnection) -> str | None:
    """The session id a request or WebSocket asked for (header first, then the query parameter)."""
    return connection.headers.get(SESSION_HEADER) or connection.query_params.get(SESSION_QUERY_PARAMETER)


@dataclass
class Session:
    """One viewer's live simulation, guided demo and message stream."""

    session_id: str
    live: LiveLoop
    demo: DemoScript
    shared: bool
    demo_lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # one demo beat at a time
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def hub(self) -> StreamHub:
        """The message hub of this session's WebSocket clients."""
        return self.live.hub

    @property
    def viewers(self) -> int:
        """Connected WebSocket clients."""
        return len(self.hub.clients)

    @property
    def busy(self) -> bool:
        """True while a demo beat runs (such a session is never deleted)."""
        return self.demo_lock.locked()

    def touch(self) -> None:
        """Remember that the viewer was just active."""
        self.last_seen = time.monotonic()


@dataclass
class _SharedViewer:
    """A viewer that asked for a private session but got the shared one, because all slots were taken."""

    last_seen: float
    connections: int = 0  # open WebSockets; while one is open the viewer stays on the shared session


class SessionManager:
    """Creates, finds and deletes sessions. Safe to call from any thread."""

    def __init__(self, models: TrainedModels, runtime: RuntimeSettings) -> None:
        """Create the shared session (paused); private ones are created on first use."""
        self.models = models
        self.runtime = runtime
        # One real machine means one live view: private simulations only exist with the simulator.
        self.max_sessions = runtime.max_sessions if runtime.data_source == "simulator" else 0
        self._lock = threading.Lock()  # guards the two dictionaries below
        self._sessions: dict[str, Session] = {}
        self._shared_viewers: dict[str, _SharedViewer] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._janitor: asyncio.Task[None] | None = None
        self.created_total = 0
        self.deleted_total = 0
        self.shared = self._new_session(SHARED_SESSION_ID, shared=True)
        if runtime.data_source == "mtconnect":
            self.shared.live.steps_per_second = 1.0 / SECONDS_PER_STEP  # a real machine: one reading per step

    # ------------------------------------------------------------------
    # Life cycle (called from the event loop by the app's lifespan)
    # ------------------------------------------------------------------
    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the event loop; must be called from it before any request arrives."""
        self._loop = loop
        self._loop_thread = threading.get_ident()
        self.shared.hub.bind(loop, self._loop_thread)

    def start(self) -> None:
        """Start the shared session's background loop and the idle-session janitor."""
        assert self._loop is not None, "bind() first"
        self._start_task(self.shared)
        self._janitor = self._loop.create_task(self._janitor_loop())

    def stop(self) -> None:
        """Cancel every background task (application shutdown)."""
        if self._janitor is not None:
            self._janitor.cancel()
        for session in self.all_sessions():
            if session.live.task is not None:
                session.live.task.cancel()

    # ------------------------------------------------------------------
    # Finding sessions
    # ------------------------------------------------------------------
    async def resolve(self, requested: str | None) -> Session:
        """The session for a requested id (created if needed, in a worker thread)."""
        session_id = valid_session_id(requested)
        if session_id is None or self.max_sessions == 0:
            return self.shared
        # Fast path without the lock (a dictionary read is atomic in CPython).
        existing = self._sessions.get(session_id)
        if existing is not None:
            existing.touch()
            return existing
        viewer = self._shared_viewers.get(session_id)
        if viewer is not None and (viewer.connections > 0 or len(self._sessions) >= self.max_sessions):
            viewer.last_seen = time.monotonic()
            return self.shared
        return await asyncio.to_thread(self.get_or_create, session_id)

    def get_or_create(self, session_id: str) -> Session:
        """The private session for a valid id; the shared one if every slot is taken and watched."""
        replaced: Session | None = None
        created: Session | None = None
        joined_shared = False  # True the first time this viewer is sent to the shared session
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                existing.touch()
                return existing
            viewer = self._shared_viewers.get(session_id)
            if viewer is not None and viewer.connections > 0:
                viewer.last_seen = time.monotonic()  # its WebSocket is on the shared session; stay there
                return self.shared
            if len(self._sessions) >= self.max_sessions:
                replaced = self._remove_longest_unwatched()
            full = len(self._sessions) >= self.max_sessions
            if full:
                if viewer is None:
                    viewer = self._shared_viewers[session_id] = _SharedViewer(time.monotonic())
                    joined_shared = True
                viewer.last_seen = time.monotonic()
            else:
                self._shared_viewers.pop(session_id, None)
                created = self._new_session(session_id, shared=False)
                self._sessions[session_id] = created
                self.created_total += 1
            in_use = len(self._sessions)
        if created is None:
            if joined_shared:
                get_activity_logger().log(
                    "session_full",
                    f"All {self.max_sessions} private simulations are in use and watched; "
                    "a new viewer joined the shared simulation.",
                    level="warning",
                    session=SHARED_SESSION_ID,
                )
            return self.shared
        self._start_task(created)
        get_activity_logger().log(
            "session_start",
            f"Started a private live simulation for this browser tab ({in_use} of {self.max_sessions} in use)."
            + (" It took over the slot of a simulation nobody was watching." if replaced is not None else ""),
            session=session_id,
        )
        return created

    def find(self, session_id: str) -> Session | None:
        """The private session with this id, if it exists (does not create one)."""
        return self._sessions.get(session_id)

    def all_sessions(self) -> list[Session]:
        """The shared session and every private one."""
        return [self.shared, *list(self._sessions.values())]

    @property
    def private_count(self) -> int:
        """Private sessions that exist now."""
        return len(self._sessions)

    # ------------------------------------------------------------------
    # Viewers (WebSocket connections)
    # ------------------------------------------------------------------
    def viewer_connected(self, requested: str | None, session: Session) -> None:
        """A WebSocket opened (keeps a viewer of the shared session there while it is open)."""
        session.touch()
        viewer = self._shared_viewers.get(valid_session_id(requested) or "")
        if session.shared and viewer is not None:
            viewer.connections += 1
            viewer.last_seen = time.monotonic()

    def viewer_disconnected(self, requested: str | None, session: Session) -> None:
        """A WebSocket closed; the idle time of a private session counts from now."""
        session.touch()
        viewer = self._shared_viewers.get(valid_session_id(requested) or "")
        if session.shared and viewer is not None:
            viewer.connections = max(0, viewer.connections - 1)
            viewer.last_seen = time.monotonic()

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def broadcast_all(self, message_type: str, data: Any) -> None:
        """Send a message to every viewer of every session (for example benchmark progress)."""
        for session in self.all_sessions():
            session.hub.broadcast(message_type, data)

    def route_log(self, entry: ActivityLogEntry) -> None:
        """Queue an activity log entry for its session's viewers (global entries go to everyone)."""
        data = entry.model_dump()
        if entry.session is None:
            targets = self.all_sessions()
        elif entry.session == SHARED_SESSION_ID:
            targets = [self.shared]
        else:
            session = self._sessions.get(entry.session)
            targets = [] if session is None else [session]
        for target in targets:
            target.hub.queue_log(data)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def describe(self, session: Session, requested: str | None) -> dict[str, Any]:
        """What the viewer should know about its session (shown in the top bar)."""
        if not session.shared:
            note = (
                "Private simulation: only this browser tab sees and controls it. It runs while the tab is "
                "open and is deleted after the tab has been closed for a while."
            )
        elif self.runtime.data_source != "simulator":
            note = "The live data comes from one machine, so every viewer shares one live view."
        elif self.max_sessions == 0:
            note = (
                f"Private simulations are turned off on this server ({MAX_SESSIONS_ENV}=0), so every viewer "
                "shares one simulation: other viewers' actions show here, and yours show for them."
            )
        elif valid_session_id(requested) is None:
            note = "No session id was sent, so this is the shared simulation used by scripts and health checks."
        else:
            note = (
                f"All {self.max_sessions} private simulations on this server are in use, so this tab shares one "
                "simulation with other viewers: their actions show here, and yours show for them. Reload the "
                "page later to get a private one."
            )
        return {
            "private": not session.shared,
            "note": note,
            "private_sessions": self.private_count,
            "max_sessions": self.max_sessions,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _new_session(self, session_id: str, shared: bool) -> Session:
        """Build a session; a private one starts running but steps only while it is watched.

        The shared session also steps only while watched when private sessions exist (measured: an
        unwatched simulation took a fifth of the CPU from four watched ones). Without private
        sessions, for example with a real machine, it always runs, so monitoring never stops.
        """
        hub = StreamHub()
        if self._loop is not None:
            hub.bind(self._loop, self._loop_thread)
        live = LiveLoop(
            self.models,
            hub,
            self.runtime.source_factory() if shared else None,
            session_id=session_id,
            needs_viewer=(not shared) or self.max_sessions > 0,
        )
        live.running = not shared  # the shared session follows the autostart setting (set by the app)
        return Session(session_id=session_id, live=live, demo=DemoScript(live), shared=shared)

    def _start_task(self, session: Session) -> None:
        """Start the session's background loop on the event loop (from any thread)."""
        if self._loop is None:
            return  # not served yet (unit tests drive the loop by hand)

        def start() -> None:
            session.live.task = asyncio.get_running_loop().create_task(session.live.run_forever())

        if threading.get_ident() == self._loop_thread:
            start()
        else:
            self._loop.call_soon_threadsafe(start)

    def _stop_task(self, session: Session) -> None:
        """Pause the session and cancel its background loop (from any thread)."""
        session.live.running = False
        if self._loop is None:
            return

        def cancel() -> None:
            if session.live.task is not None:
                session.live.task.cancel()

        if threading.get_ident() == self._loop_thread:
            cancel()
        else:
            self._loop.call_soon_threadsafe(cancel)  # runs after a pending start, so the task exists

    def _remove_longest_unwatched(self) -> Session | None:
        """Delete the private session unwatched the longest, if it has been unwatched long enough (lock held)."""
        now = time.monotonic()
        candidates = [
            session
            for session in self._sessions.values()
            if session.viewers == 0 and not session.busy and now - session.last_seen >= SESSION_EVICT_AFTER_SECONDS
        ]
        if not candidates:
            return None
        oldest = min(candidates, key=lambda session: session.last_seen)
        self._delete(oldest)
        return oldest

    def _delete(self, session: Session) -> None:
        """Forget a private session and stop its loop (lock held)."""
        del self._sessions[session.session_id]
        self._stop_task(session)
        self.deleted_total += 1

    def remove_idle(self, now: float | None = None) -> int:
        """Delete private sessions without a viewer for SESSION_IDLE_SECONDS; returns how many."""
        now = time.monotonic() if now is None else now
        with self._lock:
            idle = [
                session
                for session in self._sessions.values()
                if session.viewers == 0 and not session.busy and now - session.last_seen >= SESSION_IDLE_SECONDS
            ]
            for session in idle:
                self._delete(session)
            stale = [
                session_id
                for session_id, viewer in self._shared_viewers.items()
                if viewer.connections == 0 and now - viewer.last_seen >= SESSION_IDLE_SECONDS
            ]
            for session_id in stale:
                del self._shared_viewers[session_id]
        if idle:
            get_activity_logger().log(
                "session_cleanup",
                f"Deleted {len(idle)} private simulation(s) nobody had watched for "
                f"{SESSION_IDLE_SECONDS / 60:g} minutes.",
                session=SHARED_SESSION_ID,
            )
        return len(idle)

    async def _janitor_loop(self) -> None:
        """Background task: look for idle sessions every SESSION_JANITOR_SECONDS."""
        while True:
            await asyncio.sleep(SESSION_JANITOR_SECONDS)
            try:
                await asyncio.to_thread(self.remove_idle)
            except Exception as error:  # noqa: BLE001 - the janitor must keep running
                get_activity_logger().log("session_cleanup", f"Idle-session cleanup failed: {error}", level="error")


def session_of(connection: HTTPConnection) -> Session:
    """The session attached to a request by the session middleware (the shared one if none)."""
    session: Session | None = getattr(connection.state, "session", None)
    if session is None:
        manager: SessionManager = connection.app.state.causaltwin.sessions
        return manager.shared
    return session
