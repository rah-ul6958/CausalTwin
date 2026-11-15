"""Live simulation loop and the guided demo.

The loop runs the simulator at an accelerated speed (default 5 steps per second). Each
step it appends the data, estimates the twin's failure risk, and runs the drift check;
every LIVE_ROOT_CAUSE_EVERY steps it refreshes the root-cause analysis. Everything is
pushed to WebSocket clients through the hub. The simulator's true hazard is sent on a
separate "validation" field, clearly labelled as ground truth: the twin never uses it.

Thread safety: the background loop, the demo and API requests run in different threads,
so every change to the simulator, buffer or engine happens while holding `LiveLoop.lock`
(a re-entrant lock). Long computations that read the engine hold it too, so a drift
rewiring can never happen half way through a recommendation.
"""

import asyncio
import copy
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from causaltwin.api.hub import StreamHub
from causaltwin.config import (
    DEMO_SEED,
    LIVE_LOG_FLUSH_SECONDS,
    LIVE_MAX_FRAMES_PER_SECOND,
    LIVE_RISK_HISTORY_POINTS,
    LIVE_RISK_ROLLOUTS,
    LIVE_ROOT_CAUSE_EVERY,
    LIVE_SEED,
    LIVE_STEPS_PER_SECOND,
    LIVE_TRUTH_EVERY,
    LIVE_TRUTH_SEEDS,
    MEASURED_VARIABLES,
    OPTIMIZER_TARGET_RISK,
    ROOT_CAUSE_RISK_THRESHOLD,
    SECONDS_PER_STEP,
    SIMULATION_START_TIME,
    VARIABLE_INFO,
    WHATIF_HORIZON,
)
from causaltwin.engine.schemas import RiskEstimate, RootCauseResult
from causaltwin.intelligence.drift import DriftManager, RollingBuffer, SimulatorStepper, row_is_usable
from causaltwin.intelligence.optimizer import recommend
from causaltwin.intelligence.schemas import BaselineAction, DriftAlert, Recommendation
from causaltwin.intelligence.training import TrainedModels
from causaltwin.simulator import MachineSimulator, build_simulator, failure_probability, switch_scenario
from causaltwin.utils.activity_log import get_activity_logger
from causaltwin.utils.formatting import format_value
from causaltwin.utils.timing import timed_step

GROUND_TRUTH_LABEL = "Simulator ground truth (validation only; not used by the twin)"
WARM_UP_STEPS = 20
LIVE_PROFILE_STEPS = 20_000
FAST_RISK_EVERY = 10  # in fast-forward, the twin's risk is estimated every N steps
# A hot afternoon plus an aggressive job: at normal ambient temperature even extreme lever settings keep the
# bearing near 80 deg C, so high risk would only come from tool wear; the heat makes the thermal path matter.
DEMO_PUSH_SETTINGS = {
    "ambient_temp": 40.0,
    "load_pct": 96.0,
    "spindle_speed": 4300.0,
    "coolant_flow": 13.0,
    "lube_rate": 0.75,
}
DEMO_FAULT = "coolant_filter_clog"


def _timestamp(step: int) -> str:
    """Wall-clock time of a simulation step."""
    import pandas as pd

    return str(pd.Timestamp(SIMULATION_START_TIME) + pd.Timedelta(seconds=step * SECONDS_PER_STEP))


class LiveLoop:
    """Owns the live simulator, its data buffer, the live engine copy, and the drift manager."""

    def __init__(self, models: TrainedModels, hub: StreamHub) -> None:
        """Create the loop (paused) on the normal scenario."""
        self.models = models
        self.hub = hub
        self.running = False
        self.steps_per_second = LIVE_STEPS_PER_SECOND
        self.task: asyncio.Task[None] | None = None
        self.run_id = 0  # increases on every reset, so clients can tell runs apart
        self.lock = threading.RLock()  # guards the simulator, buffer and engine (see module docstring)
        self.loop_errors = 0  # steps of the background loop that failed (it pauses and reports each one)
        self.reset(LIVE_SEED, "normal")

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def reset(self, seed: int, scenario: str) -> None:
        """Start a fresh simulator and a fresh copy of the trained engine."""
        with self.lock:
            self._reset(seed, scenario)

    def _reset(self, seed: int, scenario: str) -> None:
        """Body of reset (the caller holds the lock)."""
        self.run_id += 1
        self.seed = seed
        self.scenario = scenario
        self.engine = copy.deepcopy(self.models.engine)
        self.simulator: MachineSimulator = build_simulator(scenario, n_steps=LIVE_PROFILE_STEPS, seed=seed)
        self.stepper = SimulatorStepper(self.simulator)
        self.buffer = RollingBuffer()
        self.drift = DriftManager(self.engine, self.buffer)
        self.latest_risk: RiskEstimate | None = None
        self.latest_true_risk: float | None = None
        # Step at which latest_true_risk was computed (it is refreshed only every few steps).
        self.latest_true_risk_step: int | None = None
        self.latest_root_cause: RootCauseResult | None = None
        self.latest_recommendation: Recommendation | None = None
        self.latest_baseline_action: BaselineAction | None = None
        self.alerts: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.applied: list[dict[str, Any]] = []
        self.risk_was_high = False
        # Recent published risk estimates, so a page opened later can draw the risk chart from the start.
        self.risk_history: deque[dict[str, Any]] = deque(maxlen=LIVE_RISK_HISTORY_POINTS)
        initial_graph = self.engine.get_graph()
        self.graph_versions: list[dict[str, Any]] = [
            {
                "version": 1,
                "step": 0,
                "reason": "initial fit on training data",
                "n_edges": len(initial_graph.edges),
                "edge_changes": [],
            }
        ]
        self._last_frame_time = 0.0
        self._last_flush = time.time()
        for _ in range(WARM_UP_STEPS):
            self._step(publish=False, estimate_risk=False)

    @property
    def step_count(self) -> int:
        """Current simulation step."""
        return self.simulator.current_step

    def history(self, rows: int | None = None) -> Any:
        """Recent rows of the model variables as a dataframe."""
        model = self.engine._require_fitted()
        needed = rows or max(model.max_lag, 1) + 1
        return self.buffer.dataframe(needed)[model.variables]

    def history_is_usable(self) -> bool:
        """True if the last few rows are normal running dynamics (risk can be estimated)."""
        model = self.engine._require_fitted()
        rows = list(self.buffer.rows)[-(max(model.max_lag, 1) + 1) :]
        return len(rows) > max(model.max_lag, 1) and all(row_is_usable(row) for row in rows)

    # ------------------------------------------------------------------
    # Stepping
    # ------------------------------------------------------------------
    def advance(self, n_steps: int = 1, fast: bool = False) -> None:
        """Run n steps. In fast mode the risk is estimated only every FAST_RISK_EVERY steps."""
        with self.lock:
            for index in range(n_steps):
                last = index == n_steps - 1
                estimate = (not fast) or last or (self.step_count % FAST_RISK_EVERY == 0)
                self._step(publish=True, estimate_risk=estimate, force_frame=last, estimate_truth=(not fast) or last)
        self.flush_logs(force=True)

    def _step(self, publish: bool, estimate_risk: bool, force_frame: bool = False, estimate_truth: bool = True) -> None:
        """One simulation step with risk estimate, drift check and messages."""
        row, events = self.stepper.step()
        alerts = self.drift.process(row)
        step = int(row["step"])
        for event in events:
            event_record = {**event, "timestamp": _timestamp(int(event["step"]))}
            self.events.append(event_record)
            if publish:
                self.hub.broadcast("event", event_record)
        if estimate_truth and (step % LIVE_TRUTH_EVERY == 0 or force_frame):
            self.latest_true_risk = self._true_risk()
            self.latest_true_risk_step = step
        if estimate_risk:
            self._estimate_risk(step, publish)
        if publish and step % LIVE_ROOT_CAUSE_EVERY == 0 and self.history_is_usable():
            self.latest_root_cause = self.engine.root_cause(self.history())
        for alert in alerts:
            self._handle_drift_alert(alert, publish)
        if publish:
            now = time.time()
            if force_frame or now - self._last_frame_time >= 1.0 / LIVE_MAX_FRAMES_PER_SECOND:
                self._last_frame_time = now
                self.hub.broadcast(
                    "sensor_frame",
                    {
                        "run_id": self.run_id,
                        "step": step,
                        "timestamp": _timestamp(step),
                        "values": {name: row[name] for name in MEASURED_VARIABLES},
                        "machine_running": row["machine_running"],
                        "failure": row["failure"],
                        "active_faults": sorted(self.simulator.active_faults),
                        "interventions": dict(self.simulator.interventions),
                        "simulated": True,
                    },
                )
            self.flush_logs()

    def _estimate_risk(self, step: int, publish: bool) -> None:
        """The twin's risk of failure within the horizon from the current state."""
        if not self.history_is_usable():
            self.latest_risk = None
        else:
            result = self.engine.what_if([], history=self.history(), n_rollouts=LIVE_RISK_ROLLOUTS)
            self.latest_risk = result.baseline_risk
        if not publish:
            return
        risk = self.latest_risk
        self.risk_history.append(
            {
                "step": step,
                "twin_risk": None if risk is None else risk.model_dump(),
                "true_risk_within_horizon": self.latest_true_risk,
                "true_risk_step": self.latest_true_risk_step,
            }
        )
        self.hub.broadcast(
            "risk_update",
            {
                "run_id": self.run_id,
                "step": step,
                "twin_risk": None if risk is None else risk.model_dump(),
                "machine_running": self.history_is_usable(),
                "horizon_steps": WHATIF_HORIZON,
                "root_cause": None if self.latest_root_cause is None else self.latest_root_cause.model_dump(),
                "validation": {
                    "label": GROUND_TRUTH_LABEL,
                    "true_hazard_per_step": self.simulator.get_true_hazard(),
                    "true_risk_within_horizon": self.latest_true_risk,
                    "true_risk_step": self.latest_true_risk_step,
                },
            },
        )
        high = risk is not None and risk.probability >= ROOT_CAUSE_RISK_THRESHOLD
        if high and not self.risk_was_high:
            self._alert(
                {
                    "kind": "risk",
                    "step": step,
                    "risk": risk.model_dump() if risk else None,
                    "message": f"Predicted failure risk {risk.probability:.0%} crossed the "
                    f"{ROOT_CAUSE_RISK_THRESHOLD:.0%} threshold."
                    if risk
                    else "",
                }
            )
        self.risk_was_high = high

    def _true_risk(self) -> float | None:
        """Ground-truth risk within the horizon from forks of the real simulator (validation only)."""
        if not self.simulator.get_state()["machine_running"]:
            return None
        risks = []
        for seed in range(LIVE_TRUTH_SEEDS):
            future = self.simulator.fork(seed=seed + 1, config_overrides={"failures_enabled": False})
            future.run(WHATIF_HORIZON)
            risks.append(failure_probability(future.get_ground_truth_dataframe()["true_hazard"]))
        return float(np.mean(risks))

    def _handle_drift_alert(self, alert: DriftAlert, publish: bool) -> None:
        """Publish drift alerts; after a confirmed change, push the new graph."""
        record = {"kind": "drift", **alert.model_dump()}
        self._alert(record, publish)
        if alert.status == "confirmed":
            graph = self.engine.get_graph()
            self.graph_versions.append(
                {
                    "version": len(self.graph_versions) + 1,
                    "step": alert.rediscovery_step or self.step_count,
                    "reason": "drift: " + "; ".join(alert.explanation[:-1] or alert.explanation),
                    "n_edges": len(graph.edges),
                    "edge_changes": [c.model_dump() for c in alert.edge_changes],
                }
            )
        if alert.status == "confirmed" and publish:
            self.hub.broadcast(
                "graph_update",
                {
                    "reason": "drift",
                    "graph": self.engine.get_graph().model_dump(),
                    "edge_changes": [c.model_dump() for c in alert.edge_changes],
                },
            )

    def _alert(self, record: dict[str, Any], publish: bool = True) -> None:
        """Store and publish an alert (also written to the activity log as a warning)."""
        self.alerts.append(record)
        message = record.get("message") or "; ".join(record.get("explanation", [])[:1]) or record.get("kind", "alert")
        get_activity_logger().log(
            f"alert_{record.get('kind', 'other')}",
            str(message),
            level="warning",
            details={"step": record.get("step") or record.get("detected_step")},
        )
        if publish:
            self.hub.broadcast("alert", record)

    def flush_logs(self, force: bool = False) -> None:
        """Send batched activity log entries at most every LIVE_LOG_FLUSH_SECONDS."""
        if force or time.time() - self._last_flush >= LIVE_LOG_FLUSH_SECONDS:
            self._last_flush = time.time()
            self.hub.flush_logs()

    async def run_forever(self) -> None:
        """Background task: step while running, otherwise idle.

        Steps run in a worker thread so the event loop (WebSockets, health checks) stays
        responsive. If a step fails, the loop pauses and reports the error instead of dying.
        """
        while True:
            if self.running:
                try:
                    await asyncio.to_thread(self.advance, 1)
                except Exception as error:  # noqa: BLE001 - any failure must pause the loop, not kill it
                    self._pause_after_error(error)
                await asyncio.sleep(1.0 / self.steps_per_second)
            else:
                self.flush_logs()
                await asyncio.sleep(0.1)

    def _pause_after_error(self, error: Exception) -> None:
        """Pause the loop and tell the user (activity log and an alert) that a step failed."""
        self.running = False
        self.loop_errors += 1
        message = (
            f"The live simulation paused after an internal error at step {self.step_count} "
            f"({type(error).__name__}: {error}). Press Resume to continue or Reset to start again."
        )
        get_activity_logger().log("live_loop_error", message, level="error")
        self.alerts.append({"kind": "error", "step": self.step_count, "message": message})
        self.hub.broadcast("alert", self.alerts[-1])
        self.flush_logs(force=True)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def compute_recommendation(self, target_risk: float = OPTIMIZER_TARGET_RISK) -> Recommendation:
        """Run the optimizer on the current state and remember the result."""
        with self.lock:
            return self._compute_recommendation(target_risk)

    def _compute_recommendation(self, target_risk: float) -> Recommendation:
        """Body of compute_recommendation (the caller holds the lock)."""
        model = self.engine._require_fitted()
        history = self.history(max(model.max_lag, 1) + 1).to_numpy(dtype=float)
        recommendation = recommend(self.engine, history, target_risk=target_risk)
        self.latest_recommendation = recommendation
        self.drift.current_recommendation = recommendation
        latest = self.buffer.dataframe(1).iloc[-1]
        magnitude = max((abs(c.change_pct) for c in recommendation.changes), default=20.0)
        self.latest_baseline_action = self.models.baseline.recommend(latest, magnitude)
        return recommendation

    def apply_recommendation(self) -> dict[str, Any]:
        """Apply the latest recommendation to the live simulator."""
        with self.lock:
            return self._apply_recommendation()

    def _apply_recommendation(self) -> dict[str, Any]:
        """Body of apply_recommendation (the caller holds the lock)."""
        recommendation = self.latest_recommendation
        if recommendation is None:
            raise ValueError("No recommendation to apply; call /recommend first")
        with timed_step("apply", "Applied recommendation to the live simulator"):
            for change in recommendation.changes:
                self.simulator.intervene(change.lever, change.to_value)
            if recommendation.tool_change:
                self.simulator.replace_tool()
        applied = {
            "step": self.step_count,
            "changes": [c.model_dump() for c in recommendation.changes],
            "tool_change": recommendation.tool_change,
            "action_type": recommendation.action_type,
        }
        self.applied.append(applied)
        self.hub.broadcast(
            "event", {"step": self.step_count, "event_type": "recommendation_applied", "details": applied}
        )
        return applied

    def switch_scenario(self, scenario: str) -> None:
        """Switch the live simulator's scenario without resetting the machine."""
        with self.lock:
            switch_scenario(self.simulator, scenario, n_steps=LIVE_PROFILE_STEPS)
            self.scenario = scenario

    def set_fault(self, fault: str, active: bool) -> None:
        """Inject (active=True) or clear a structural fault."""
        with self.lock:
            if active:
                self.simulator.inject_fault(fault)
            else:
                self.simulator.clear_fault(fault)

    def release_all(self) -> None:
        """Hand every lever back to the simulated operator."""
        with self.lock:
            for variable in list(self.simulator.interventions):
                self.simulator.release(variable)


@dataclass
class DemoBeat:
    """One beat of the guided demo story."""

    name: str
    title: str


DEMO_BEATS = [
    DemoBeat("normal_operation", "Normal operation"),
    DemoBeat("risk_rises", "A hot afternoon and an aggressive job push the machine; risk rises"),
    DemoBeat("recommendation", "CausalTwin recommends the cheapest fix (and what standard ML would do)"),
    DemoBeat("apply", "The recommendation is applied"),
    DemoBeat("risk_drops", "Risk drops"),
    DemoBeat("fault_injected", "The coolant filter clogs"),
    DemoBeat("drift_alert", "Drift detected: the causal graph is rewired"),
    DemoBeat("new_recommendation", "A new recommendation for the changed machine"),
]


class DemoScript:
    """Plays the demo story beat by beat on the live loop (fixed seed, paused between beats)."""

    def __init__(self, loop: LiveLoop) -> None:
        """Attach to the live loop; call start() to begin."""
        self.loop = loop
        self.position = -1
        self.results: list[dict[str, Any]] = []

    @property
    def finished(self) -> bool:
        """True after the last beat."""
        return self.position >= len(DEMO_BEATS) - 1

    def start(self) -> dict[str, Any]:
        """Reset to the demo seed and play the first beat."""
        with self.loop.lock:
            self.loop.running = False
            self.loop.reset(DEMO_SEED, "normal")
            self.position = -1
            self.results = []
            return self.next()

    def next(self) -> dict[str, Any]:
        """Play the next beat and return what happened.

        The whole beat holds the live loop's lock and pauses the loop, so a Resume click or
        another request cannot interleave with the scripted steps. If a beat fails, the
        position is not advanced, so pressing Next again retries that beat.
        """
        with self.loop.lock:
            self.loop.running = False
            if self.finished:
                return {"finished": True, "beat": None, "position": self.position, "total": len(DEMO_BEATS)}
            position = self.position + 1
            beat = DEMO_BEATS[position]
            get_activity_logger().log("demo", f"Demo beat {position + 1}: {beat.title}")
            details = getattr(self, f"_{beat.name}")()
            self.position = position
            return self._publish(beat, details)

    def _publish(self, beat: DemoBeat, details: dict[str, Any]) -> dict[str, Any]:
        """Record and broadcast the result of the beat that just ran."""
        result = {
            "finished": self.finished,
            "position": self.position,
            "total": len(DEMO_BEATS),
            "beat": beat.name,
            "title": beat.title,
            **details,
            "step": self.loop.step_count,
            "twin_risk": None if self.loop.latest_risk is None else self.loop.latest_risk.model_dump(),
            "validation": {"label": GROUND_TRUTH_LABEL, "true_risk_within_horizon": self.loop.latest_true_risk},
        }
        self.results.append(result)
        self.loop.hub.broadcast("demo", result)
        return result

    def _risk_text(self) -> str:
        """Current predicted risk as text."""
        risk = self.loop.latest_risk
        return "unknown (machine stopped)" if risk is None else f"{risk.probability:.1%}"

    def _normal_operation(self) -> dict[str, Any]:
        """Run a stretch of normal operation."""
        self.loop.advance(150, fast=True)
        return {"narration": f"Normal operation. Predicted failure risk within 5 minutes: {self._risk_text()}."}

    def _push(self) -> None:
        """The operator pushes load and speed and cuts cooling and lubrication, until risk is high."""
        for lever, value in DEMO_PUSH_SETTINGS.items():
            self.loop.simulator.intervene(lever, value)
        for _ in range(40):
            self.loop.advance(10, fast=True)
            risk = self.loop.latest_risk
            if risk is not None and risk.probability >= 3 * OPTIMIZER_TARGET_RISK:
                break

    def _risk_rises(self) -> dict[str, Any]:
        """A new aggressive job starts with a fresh tool; the heat builds up."""
        self.loop.simulator.replace_tool()
        self._push()
        settings = ", ".join(
            f"{name.replace('_', ' ')} {format_value(value)} {VARIABLE_INFO[name]['unit']}"
            for name, value in DEMO_PUSH_SETTINGS.items()
        )
        return {
            "narration": f"A hot afternoon and a new aggressive job with a fresh tool ({settings}). "
            f"Bearing temperature climbs; predicted failure risk is now {self._risk_text()}."
        }

    def _recommendation(self) -> dict[str, Any]:
        """Compute CausalTwin's recommendation and the baseline's action."""
        recommendation = self.loop.compute_recommendation()
        baseline = self.loop.latest_baseline_action
        contrast = (
            ""
            if baseline is None
            else (f" Standard ML (XGBoost + SHAP) blames {baseline.blamed_feature}: {baseline.description}")
        )
        return {
            "narration": recommendation.explanation + contrast,
            "recommendation": recommendation.model_dump(exclude={"candidates"}),
            "baseline_action": None if baseline is None else baseline.model_dump(),
        }

    def _apply(self) -> dict[str, Any]:
        """Apply the recommendation."""
        applied = self.loop.apply_recommendation()
        self.loop.advance(30, fast=True)
        return {"narration": "The recommendation is applied to the machine.", "applied": applied}

    def _risk_drops(self) -> dict[str, Any]:
        """Let the effect play out."""
        self.loop.advance(60, fast=True)
        return {"narration": f"After the change, predicted failure risk is {self._risk_text()}."}

    def _fault_injected(self) -> dict[str, Any]:
        """The job ends (operator back in control), then the coolant filter clogs."""
        for variable in list(self.loop.simulator.interventions):
            self.loop.simulator.release(variable)
        self.loop.advance(50, fast=True)
        self.loop.simulator.inject_fault(DEMO_FAULT)
        for _ in range(100):
            self.loop.advance(1, fast=True)
            if self.loop.drift.state == "collecting":
                break
        suspected = next((a for a in reversed(self.loop.alerts) if a.get("status") == "suspected"), None)
        return {
            "narration": "The job ends and the operator is back in control. Then the coolant filter clogs "
            "(a hidden physical change). " + (suspected["explanation"][0] if suspected else "No drift alarm yet."),
            "alert": suspected,
        }

    def _drift_alert(self) -> dict[str, Any]:
        """Collect post-change data until re-discovery rewires the graph."""
        for _ in range(80):
            self.loop.advance(10, fast=True)
            if self.loop.drift.state == "monitoring":
                break
        confirmed = next(
            (a for a in reversed(self.loop.alerts) if a.get("status") in ("confirmed", "no_structural_change")), None
        )
        text = " ".join(confirmed["explanation"]) if confirmed else "Re-discovery has not finished yet."
        return {"narration": text, "alert": confirmed}

    def _new_recommendation(self) -> dict[str, Any]:
        """The aggressive job resumes on the changed machine; recommend with the rewired model."""
        invalid = (
            next(
                (
                    a.get("invalidated_recommendations")
                    for a in reversed(self.loop.alerts)
                    if a.get("status") == "confirmed"
                ),
                None,
            )
            or []
        )
        self.loop.simulator.replace_tool()
        self._push()
        recommendation = self.loop.compute_recommendation()
        previous = (" Earlier advice is no longer valid: " + " ".join(invalid)) if invalid else ""
        return {
            "narration": "The aggressive job resumes on the changed machine."
            + previous
            + " With the rewired model: "
            + recommendation.explanation,
            "recommendation": recommendation.model_dump(exclude={"candidates"}),
            "invalidated_recommendations": invalid,
        }
