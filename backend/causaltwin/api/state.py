"""Application state shared by all routes: trained models, live loop, demo, hub, jobs."""

import asyncio
from typing import Any

from causaltwin.api.hub import StreamHub
from causaltwin.api.live import DemoScript, LiveLoop
from causaltwin.api.runtime import RuntimeSettings
from causaltwin.config import MAX_CONCURRENT_HEAVY_REQUESTS, SECONDS_PER_STEP
from causaltwin.engine.twin import CausalTwinEngine
from causaltwin.intelligence.training import TrainedModels


class AppState:
    """Everything the API needs at runtime."""

    def __init__(self, models: TrainedModels, hub: StreamHub, runtime: RuntimeSettings | None = None) -> None:
        """Create the live loop (paused) and the demo script."""
        self.models = models
        self.hub = hub
        self.runtime = runtime or RuntimeSettings.from_environment()
        # At most this many heavy computations run at once; more get a "busy" answer.
        self.heavy_slots = asyncio.Semaphore(MAX_CONCURRENT_HEAVY_REQUESTS)
        self.demo_lock = asyncio.Lock()  # one demo beat at a time (double clicks get a clear 409)
        self.live = LiveLoop(models, hub, self.runtime.source_factory())
        if self.runtime.data_source == "mtconnect":
            self.live.steps_per_second = 1.0 / SECONDS_PER_STEP  # a real machine: one reading per step
        self.demo = DemoScript(self.live)
        self.benchmark_running = False
        self.benchmark_progress: dict[str, Any] = {}
        self.last_benchmark_run: dict[str, Any] | None = None
        self.byod_engine: CausalTwinEngine | None = None
        self.requests_served = 0

    def status(self) -> dict[str, Any]:
        """Live simulator status for responses."""
        simulator = self.live.simulator
        source = self.live.source_info
        return {
            "running": self.live.running,
            "run_id": self.live.run_id,
            "step": self.live.step_count,
            "steps_per_second": self.live.steps_per_second,
            "scenario": self.live.scenario,
            "active_faults": [] if simulator is None else sorted(simulator.active_faults),
            "interventions": {} if simulator is None else dict(simulator.interventions),
            "data_source": source.as_dict(),
            "simulated": source.simulated_data,
            "drift_state": self.live.drift.state,
        }
