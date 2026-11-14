"""Application state shared by all routes: trained models, live loop, demo, hub, jobs."""

from typing import Any

from causaltwin.api.hub import StreamHub
from causaltwin.api.live import DemoScript, LiveLoop
from causaltwin.engine.twin import CausalTwinEngine
from causaltwin.intelligence.training import TrainedModels


class AppState:
    """Everything the API needs at runtime."""

    def __init__(self, models: TrainedModels, hub: StreamHub) -> None:
        """Create the live loop (paused) and the demo script."""
        self.models = models
        self.hub = hub
        self.live = LiveLoop(models, hub)
        self.demo = DemoScript(self.live)
        self.benchmark_running = False
        self.benchmark_progress: dict[str, Any] = {}
        self.last_benchmark_run: dict[str, Any] | None = None
        self.byod_engine: CausalTwinEngine | None = None
        self.requests_served = 0

    def status(self) -> dict[str, Any]:
        """Live simulator status for responses."""
        simulator = self.live.simulator
        return {
            "running": self.live.running,
            "step": self.live.step_count,
            "steps_per_second": self.live.steps_per_second,
            "scenario": self.live.scenario,
            "active_faults": sorted(simulator.active_faults),
            "interventions": dict(simulator.interventions),
            "drift_state": self.live.drift.state,
        }
