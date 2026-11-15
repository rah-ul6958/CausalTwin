"""Application state shared by all routes: trained models, sessions, jobs and limits.

Each viewer has its own live simulation (a session, see sessions.py). What is global stays
here: the trained models, the runtime settings, the limit on heavy computations, and the
benchmark job. `live`, `demo`, `hub` and `demo_lock` point at the shared session, which is
what requests without a session id (scripts, health checks, tests) use.
"""

import asyncio
from typing import Any

from causaltwin.api.hub import StreamHub
from causaltwin.api.live import DemoScript, LiveLoop
from causaltwin.api.runtime import RuntimeSettings
from causaltwin.api.sessions import Session, SessionManager
from causaltwin.config import MAX_CONCURRENT_HEAVY_REQUESTS
from causaltwin.engine.twin import CausalTwinEngine
from causaltwin.intelligence.training import TrainedModels


class AppState:
    """Everything the API needs at runtime."""

    def __init__(self, models: TrainedModels, runtime: RuntimeSettings | None = None) -> None:
        """Create the session manager (with the paused shared session)."""
        self.models = models
        self.runtime = runtime or RuntimeSettings.from_environment()
        # At most this many heavy computations run at once, over all sessions; more get a "busy" answer.
        self.heavy_slots = asyncio.Semaphore(MAX_CONCURRENT_HEAVY_REQUESTS)
        self.sessions = SessionManager(models, self.runtime)
        self.benchmark_running = False
        self.benchmark_progress: dict[str, Any] = {}
        self.last_benchmark_run: dict[str, Any] | None = None
        self.byod_engine: CausalTwinEngine | None = None
        self.requests_served = 0

    @property
    def live(self) -> LiveLoop:
        """The shared session's live loop."""
        return self.sessions.shared.live

    @property
    def demo(self) -> DemoScript:
        """The shared session's guided demo."""
        return self.sessions.shared.demo

    @property
    def hub(self) -> StreamHub:
        """The shared session's message hub."""
        return self.sessions.shared.hub

    @property
    def demo_lock(self) -> asyncio.Lock:
        """The shared session's demo lock."""
        return self.sessions.shared.demo_lock

    def status(self, session: Session | None = None, requested_session: str | None = None) -> dict[str, Any]:
        """Live status of a session (the shared one by default) for responses."""
        session = session or self.sessions.shared
        live = session.live
        simulator = live.simulator
        source = live.source_info
        return {
            "running": live.running,
            "run_id": live.run_id,
            "step": live.step_count,
            "steps_per_second": live.steps_per_second,
            "scenario": live.scenario,
            "active_faults": [] if simulator is None else sorted(simulator.active_faults),
            "interventions": {} if simulator is None else dict(simulator.interventions),
            "data_source": source.as_dict(),
            "simulated": source.simulated_data,
            "drift_state": live.drift.state,
            "session": self.sessions.describe(session, requested_session),
        }
