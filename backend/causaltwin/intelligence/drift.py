"""Causal drift detection: notice that cause-effect structure changed, rediscover, rewire.

1. Every fitted equation predicts its variable one step ahead from the actual lagged
   values. Errors are standardized by the equation's residual spread, clipped (so a
   single outlier such as a tool chip cannot raise an alarm alone) and fed to a
   two-sided CUSUM test. Crossing the threshold raises a "suspected" alert.
2. The detector then collects DRIFT_REDISCOVERY_WINDOW post-alarm steps (re-discovery
   right at the alarm would mostly see pre-change data), and reruns discovery on them.
3. Old and new structure are compared per equation: edges removed or added, and
   coefficients that moved outside their bootstrap interval by a practically relevant
   amount. The graph is rewired, the linear equations refitted, and recommendations
   that relied on a changed edge are marked invalid ("confirmed" alert).
"""

import copy
import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

from causaltwin.config import (
    BOOTSTRAP_BLOCK_LENGTH,
    BOOTSTRAP_MIN_CONFIDENCE,
    DRIFT_BOOTSTRAP_RESAMPLES,
    DRIFT_CUSUM_H,
    DRIFT_CUSUM_K,
    DRIFT_FAULT_STEP,
    DRIFT_MIN_RELATIVE_CHANGE,
    DRIFT_REDISCOVERY_WINDOW,
    DRIFT_Z_CLIP,
    EVENT_INDICATOR_PREFIX,
    EVENT_TYPES,
    LIVE_BUFFER_ROWS,
    MEASURED_VARIABLES,
    REGIME_BREAK_EVENTS,
    TAU_MAX,
)
from causaltwin.connectors.base import SourceInfo
from causaltwin.engine.causal_model import CONSTANT_STD, CausalModel
from causaltwin.engine.confidence import block_resample
from causaltwin.engine.discovery import fast_mci, significant_lagged_edges, tigramite_mci
from causaltwin.engine.preprocessing import PreparedData, lagged_design, prepare_data
from causaltwin.engine.schemas import CausalEdge, CausalGraph
from causaltwin.engine.twin import CausalTwinEngine
from causaltwin.intelligence.optimizer import shortest_lag_path
from causaltwin.intelligence.schemas import DriftAlert, EdgeChange, Recommendation
from causaltwin.simulator import MachineSimulator, build_simulator
from causaltwin.utils.timing import timed_step

DRIFT_RNG_SEED = 99


class SimulatorStepper:
    """Steps a simulator and returns each new row with its event indicator flags."""

    def __init__(self, simulator: MachineSimulator) -> None:
        """Start reading events from the simulator's current position."""
        self.simulator = simulator
        self._event_index = len(simulator._events)

    @property
    def current_step(self) -> int:
        """The simulator's next step index."""
        return self.simulator.current_step

    def describe(self) -> SourceInfo:
        """The built-in simulated spindle (illustrative constants, ground truth available)."""
        return SourceInfo(
            kind="simulator",
            description="Simulated CNC spindle (illustrative constants)",
            has_simulator=True,
            simulated_data=True,
        )

    def step(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Advance one step. Returns (row, events that happened in this step)."""
        step_index = self.simulator.current_step
        measured = self.simulator.step()
        records = self.simulator._sensor_records
        new_events = self.simulator._events[self._event_index :]
        self._event_index = len(self.simulator._events)
        row: dict[str, Any] = {"step": step_index, **{name: measured[name] for name in MEASURED_VARIABLES}}
        row["failure"] = int(records["failure"][-1])
        row["machine_running"] = int(records["machine_running"][-1])
        for event_type in EVENT_TYPES:
            row[f"{EVENT_INDICATOR_PREFIX}{event_type}"] = int(any(e["event_type"] == event_type for e in new_events))
        return row, [dict(event) for event in new_events]


class RollingBuffer:
    """The most recent rows, as a list of dicts that can be turned into a dataframe."""

    def __init__(self, max_rows: int = LIVE_BUFFER_ROWS) -> None:
        """Create an empty buffer."""
        self.rows: deque[dict[str, Any]] = deque(maxlen=max_rows)

    def append(self, row: dict[str, Any]) -> None:
        """Add one row."""
        self.rows.append(row)

    def dataframe(self, last: int | None = None) -> pd.DataFrame:
        """The last `last` rows (all if None) as a dataframe."""
        rows = list(self.rows) if last is None else list(self.rows)[-last:]
        return pd.DataFrame(rows)

    def __len__(self) -> int:
        """Number of stored rows."""
        return len(self.rows)


def row_is_usable(row: dict[str, Any]) -> bool:
    """A row is normal dynamics if the machine runs, no regime-break event happened, and every
    model variable has a value (a real data source may not have delivered one yet)."""
    return bool(
        row.get("machine_running", 1) == 1
        and not any(row.get(f"{EVENT_INDICATOR_PREFIX}{event}", 0) for event in REGIME_BREAK_EVENTS)
        and all(math.isfinite(float(row.get(name, 0.0))) for name in MEASURED_VARIABLES)
    )


@dataclass
class _Cusum:
    """Two-sided CUSUM statistics for one equation."""

    positive: float = 0.0
    negative: float = 0.0
    last_zero_step: int = 0


class DriftMonitor:
    """One-step-ahead error CUSUM for every fitted equation."""

    def __init__(
        self, model: CausalModel, k: float = DRIFT_CUSUM_K, h: float = DRIFT_CUSUM_H, clip: float = DRIFT_Z_CLIP
    ) -> None:
        """Start monitoring a model."""
        self.k, self.h, self.clip = k, h, clip
        self.reset(model)

    def reset(self, model: CausalModel) -> None:
        """Monitor a (new) model from scratch."""
        self.model = model
        self.index = {name: position for position, name in enumerate(model.variables)}
        self.history_length = max(model.max_lag, 1) + 1
        self.history: deque[tuple[np.ndarray, bool]] = deque(maxlen=self.history_length)
        self.statistics = {name: _Cusum() for name in model.equations}
        self.latest_z: dict[str, float] = {}

    def update(self, row: dict[str, Any]) -> tuple[str, float, int] | None:
        """Add a row; return (equation, CUSUM value, change point step) if an alarm fires."""
        values = np.array([float(row[name]) for name in self.model.variables])
        self.history.append((values, row_is_usable(row)))
        if len(self.history) < self.history_length or not all(usable for _, usable in self.history):
            return None
        step = int(row["step"])
        alarm = None
        for name, equation in self.model.equations.items():
            prediction = equation.intercept + sum(
                coefficient * self.history[-1 - lag][0][self.index[cause]]
                for (cause, lag), coefficient in zip(equation.terms, equation.coefficients, strict=True)
            )
            spread = equation.residual_std if equation.residual_std > 0 else 1.0
            z_value = float(np.clip((values[self.index[name]] - prediction) / spread, -self.clip, self.clip))
            self.latest_z[name] = z_value
            statistic = self.statistics[name]
            statistic.positive = max(0.0, statistic.positive + z_value - self.k)
            statistic.negative = max(0.0, statistic.negative - z_value - self.k)
            if statistic.positive == 0.0 and statistic.negative == 0.0:
                statistic.last_zero_step = step
            value = max(statistic.positive, statistic.negative)
            if value > self.h and (alarm is None or value > alarm[1]):
                alarm = (name, value, statistic.last_zero_step)
        return alarm


def _coefficient_sums(prepared: PreparedData, target: str, terms: list[tuple[str, int]]) -> dict[str, float]:
    """OLS on the given lagged terms; coefficients summed over lags per cause."""
    design, values, _ = lagged_design(prepared, target, terms, first_row=0)
    if values.size < len(terms) + 10:
        return {}
    design = np.column_stack([np.ones(values.size), design])
    coefficients, *_ = np.linalg.lstsq(design, values, rcond=None)
    sums: dict[str, float] = {}
    for (cause, _), coefficient in zip(terms, coefficients[1:], strict=True):
        sums[cause] = sums.get(cause, 0.0) + float(coefficient)
    return sums


def rediscover(engine: CausalTwinEngine, recent: pd.DataFrame) -> tuple[CausalGraph, list[EdgeChange]]:
    """Discover the graph on recent data and compare it with the engine's current structure."""
    with timed_step("rediscovery", f"Re-discovered causal graph on {len(recent)} recent rows") as timer:
        old_graph, old_model = engine.get_graph(), engine._require_fitted()
        assert engine.window is not None
        window = prepare_data(recent, engine.window.roles, True)
        settings = engine.settings
        if settings.discovery_backend == "tigramite":
            lagged = tigramite_mci(window, settings.tau_max, settings.discovery_n_jobs)
        else:
            lagged = fast_mci(window, settings.tau_max)
        new_edges = significant_lagged_edges(lagged, settings.alpha)
        rng = np.random.default_rng(DRIFT_RNG_SEED)
        resamples = [block_resample(window, BOOTSTRAP_BLOCK_LENGTH, rng) for _ in range(DRIFT_BOOTSTRAP_RESAMPLES)]
        counts: dict[tuple[str, str], int] = {}
        for resample in resamples:
            for edge in significant_lagged_edges(fast_mci(resample, settings.tau_max), settings.alpha):
                counts[(edge.cause, edge.effect)] = counts.get((edge.cause, edge.effect), 0) + 1
        for position, edge in enumerate(new_edges):
            frequency = counts.get((edge.cause, edge.effect), 0) / DRIFT_BOOTSTRAP_RESAMPLES
            new_edges[position] = edge.model_copy(
                update={"confidence": frequency, "uncertain": frequency < BOOTSTRAP_MIN_CONFIDENCE}
            )
        outcome_edges = [edge for edge in old_graph.edges if window.outcome and edge.effect == window.outcome]
        new_graph = old_graph.model_copy(
            update={
                "edges": new_edges + outcome_edges,
                "window_steps": window.n_rows,
                "notes": [
                    *old_graph.notes,
                    f"Rewired after drift on the most recent {window.n_rows} steps; "
                    "failure causes kept from the long-history failure model.",
                ],
            }
        )
        changes = _compare(old_graph, old_model, new_graph, window, resamples)
        timer.details.update({"new_edges": len(new_edges), "changes": len(changes)})
    return new_graph, changes


def _compare(
    old_graph: CausalGraph,
    old_model: CausalModel,
    new_graph: CausalGraph,
    window: PreparedData,
    resamples: list[PreparedData],
) -> list[EdgeChange]:
    """Edges added/removed and coefficients that moved beyond their bootstrap interval."""

    def cross(graph: CausalGraph) -> dict[tuple[str, str], CausalEdge]:
        return {(e.cause, e.effect): e for e in graph.edges if not e.is_self_loop and e.effect in old_model.equations}

    old_edges, new_edges = cross(old_graph), cross(new_graph)
    changes: list[EdgeChange] = []
    for target, equation in old_model.equations.items():
        terms = list(
            dict.fromkeys(
                equation.terms + [(e.cause, lag) for (c, t), e in new_edges.items() if t == target for lag in e.lags]
            )
        )
        new_sums = _coefficient_sums(window, target, terms)
        sampled = [_coefficient_sums(resample, target, terms) for resample in resamples]
        causes = {cause for cause, _ in terms if cause != target}
        for cause in sorted(causes):
            kind: Literal["added", "removed", "weakened", "strengthened"]
            if window.values[~window.row_mask, window.index_of(cause)].std() < CONSTANT_STD:
                continue  # held constant (for example under a do-intervention): its effect cannot be assessed
            old_value = equation.cause_coefficient_sum(cause) if (cause, target) in old_edges else 0.0
            new_value = new_sums.get(cause, 0.0)
            draws = [sample[cause] for sample in sampled if cause in sample]
            if len(draws) < 5:
                continue
            low, high = (float(v) for v in np.percentile(draws, [5, 95]))
            outside = not (low <= old_value <= high)
            scale = max(abs(old_value), abs(new_value), 1e-12)
            relevant = abs(new_value - old_value) / scale >= DRIFT_MIN_RELATIVE_CHANGE
            in_old, in_new = (cause, target) in old_edges, (cause, target) in new_edges
            if not (outside and relevant):
                continue
            if in_old and not in_new:
                kind, text = (
                    "removed",
                    f"{cause} no longer affects {target} (effect {old_value:+.3g} -> {new_value:+.3g}).",
                )
            elif in_new and not in_old:
                if not new_edges[(cause, target)].confidence or new_edges[(cause, target)].uncertain:
                    continue
                kind, text = "added", f"New link: {cause} now affects {target} (effect {new_value:+.3g})."
            elif not in_old:
                continue
            elif abs(new_value) < abs(old_value):
                kind = "weakened"
                text = (
                    f"{cause} no longer strongly controls {target}: its effect fell from {old_value:+.3g} to "
                    f"{new_value:+.3g} ({100 * (1 - abs(new_value) / abs(old_value)):.0f}% weaker)."
                )
            else:
                kind = "strengthened"
                text = (
                    f"{cause} now drives {target} more strongly: effect {old_value:+.3g} -> {new_value:+.3g} "
                    f"({100 * (abs(new_value) / abs(old_value) - 1):.0f}% stronger)."
                )
            changes.append(
                EdgeChange(
                    cause=cause,
                    effect=target,
                    change=kind,
                    old_coefficient=old_value,
                    new_coefficient=new_value,
                    new_interval=(low, high),
                    explanation=text,
                )
            )
    return changes


def invalidated(recommendation: Recommendation | None, graph: CausalGraph, changes: list[EdgeChange]) -> list[str]:
    """Recommendations whose causal path uses an edge that changed."""
    if recommendation is None or recommendation.action_type == "none":
        return []
    changed = {(change.cause, change.effect): change for change in changes}
    reasons = []
    sources = [c.lever for c in recommendation.changes] + (["tool_wear"] if recommendation.tool_change else [])
    for source in sources:
        found = shortest_lag_path(graph, source)
        if not found:
            continue
        path = found[0]
        for cause, effect in zip(path, path[1:], strict=False):
            if (cause, effect) in changed:
                reasons.append(
                    f"The recommendation to change {source} relied on {cause} -> {effect}, which "
                    f"{changed[(cause, effect)].change}; recompute it."
                )
    return reasons


class DriftManager:
    """Runs the monitor on incoming rows and handles re-discovery and rewiring."""

    def __init__(
        self, engine: CausalTwinEngine, buffer: RollingBuffer | None = None, window: int = DRIFT_REDISCOVERY_WINDOW
    ) -> None:
        """Watch an engine's model; rows are also kept in the buffer for re-discovery."""
        self.engine = engine
        self.buffer = buffer if buffer is not None else RollingBuffer()
        self.window = window
        self.monitor = DriftMonitor(engine._require_fitted())
        self.pending: DriftAlert | None = None
        self.collected = 0
        self.current_recommendation: Recommendation | None = None
        self.alerts: list[DriftAlert] = []

    @property
    def state(self) -> str:
        """ "monitoring" or "collecting" (waiting for post-change data)."""
        return "collecting" if self.pending is not None else "monitoring"

    def process(self, row: dict[str, Any], append_to_buffer: bool = True) -> list[DriftAlert]:
        """Handle one new row; returns any new alerts (suspected, then confirmed)."""
        if append_to_buffer:
            self.buffer.append(row)
        alerts: list[DriftAlert] = []
        if self.pending is None:
            alarm = self.monitor.update(row)
            if alarm is not None:
                equation, value, change_point = alarm
                self.pending = DriftAlert(
                    detected_step=int(row["step"]),
                    change_point_step=change_point,
                    equation=equation,
                    cusum_value=value,
                    status="suspected",
                    explanation=[
                        f"The {equation} equation's prediction errors drifted (CUSUM {value:.1f} > "
                        f"{self.monitor.h:.0f}); collecting {self.window} steps to rediscover the structure."
                    ],
                )
                self.collected = 0
                alerts.append(self.pending)
        else:
            self.collected += 1
            if self.collected >= self.window:
                alerts.append(self._confirm(int(row["step"])))
        self.alerts.extend(alerts)
        return alerts

    def _confirm(self, step: int) -> DriftAlert:
        """Rediscover on the collected window, rewire and refit if the structure changed."""
        assert self.pending is not None
        recent = self.buffer.dataframe(self.window)
        new_graph, all_changes = rediscover(self.engine, recent)
        # Only the equation whose predictions drifted has evidence of a change. Differences found
        # elsewhere in a short window are not confirmed: a real change there raises its own alarm.
        changes = [change for change in all_changes if change.effect == self.pending.equation]
        alert = self.pending.model_copy(
            update={
                "rediscovery_step": step,
                "window_steps": len(recent),
                "edge_changes": changes,
            }
        )
        if changes:
            alert.invalidated_recommendations = invalidated(
                self.current_recommendation, self.engine.get_graph(), changes
            )
            changed_targets = {change.effect for change in changes}
            old_graph = self.engine.get_graph()
            merged_edges = [e for e in old_graph.edges if e.effect not in changed_targets] + [
                e for e in new_graph.edges if e.effect in changed_targets
            ]
            new_graph = new_graph.model_copy(update={"edges": merged_edges})
            self.engine.update_structure(recent, new_graph, changed_targets)
            alert.status = "confirmed"
            alert.explanation = [change.explanation for change in changes] + [
                "The causal graph was rewired and the equations refitted on the recent data."
            ]
        else:
            alert.status = "no_structural_change"
            alert.explanation = [
                "Re-discovery found no significant structural change; the alarm was a false alarm "
                "or a change in operating conditions only. The model is kept."
            ]
        self.monitor.reset(self.engine._require_fitted())
        self.pending = None
        return alert


def evaluate_fault(
    engine: CausalTwinEngine, fault: str, seed: int, fault_step: int = DRIFT_FAULT_STEP, max_steps_after: int = 3000
) -> dict[str, Any]:
    """Inject a fault in a normal run and measure detection delay and what was found."""
    engine = copy.deepcopy(engine)
    simulator = build_simulator("normal", n_steps=fault_step + max_steps_after, seed=seed)
    stepper, manager = SimulatorStepper(simulator), DriftManager(engine)
    result: dict[str, Any] = {
        "fault": fault,
        "seed": seed,
        "fault_step": fault_step,
        "false_alarms_before_fault": 0,
        "detected_step": None,
        "confirmed_step": None,
    }
    for step in range(fault_step + max_steps_after):
        if step == fault_step:
            simulator.inject_fault(fault)
        row, _ = stepper.step()
        for alert in manager.process(row):
            if alert.status == "suspected":
                if step < fault_step:
                    result["false_alarms_before_fault"] += 1
                elif result["detected_step"] is None:
                    result["detected_step"] = alert.detected_step
                    result["equation"] = alert.equation
            elif step >= fault_step and result["detected_step"] is not None:
                # A re-discovery that finds no change keeps the model and resumes monitoring; the detector
                # gets another chance on later data, so count the attempts until a confirmed rewiring.
                result["rediscovery_attempts"] = result.get("rediscovery_attempts", 0) + 1
                result["status"] = alert.status
                result["edge_changes"] = [change.model_dump() for change in alert.edge_changes]
                result["explanation"] = alert.explanation
                if alert.status == "confirmed":
                    result["confirmed_step"] = alert.rediscovery_step
        if result["confirmed_step"] is not None:
            break
    if result["detected_step"] is not None:
        result["detection_delay_steps"] = result["detected_step"] - fault_step
    if result["confirmed_step"] is not None:
        result["rewire_delay_steps"] = result["confirmed_step"] - fault_step
    return result


def count_false_alarms(engine: CausalTwinEngine, n_steps: int, seed: int, scenario: str = "normal") -> dict[str, Any]:
    """Run without any fault and count alarms and wrongly confirmed structural changes."""
    engine = copy.deepcopy(engine)
    simulator = build_simulator(scenario, n_steps=n_steps, seed=seed)
    stepper, manager = SimulatorStepper(simulator), DriftManager(engine)
    suspected, confirmed, alarm_steps = 0, 0, []
    for _ in range(n_steps):
        row, _ = stepper.step()
        for alert in manager.process(row):
            if alert.status == "suspected":
                suspected += 1
                alarm_steps.append(alert.detected_step)
            elif alert.status == "confirmed":
                confirmed += 1
    return {
        "scenario": scenario,
        "seed": seed,
        "n_steps": n_steps,
        "suspected_alarms": suspected,
        "wrongly_confirmed_changes": confirmed,
        "alarm_steps": alarm_steps,
    }


__all__ = [
    "DriftManager",
    "DriftMonitor",
    "RollingBuffer",
    "SimulatorStepper",
    "count_false_alarms",
    "evaluate_fault",
    "rediscover",
    "TAU_MAX",
]
