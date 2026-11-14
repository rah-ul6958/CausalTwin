"""The simulated CNC milling spindle with a KNOWN causal structure.

Every causal effect acts with a lag of 1 to 3 steps (no same-step effects). The
true graph is documented in `true_graph.py`. All constants are illustrative.

Important design property (the Correlation Trap): vibration is driven by load,
speed and tool wear, and failure is driven by bearing temperature and tool wear.
Vibration is therefore strongly correlated with failure, but it is NOT an input to
the hazard function, so changing vibration never changes the failure risk.
"""

import copy
import json
import math
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

import pandas as pd

from causaltwin.config import (
    DEFAULT_CONFIG,
    DEFAULT_SEED,
    FAULT_NAMES,
    INTERNAL_SENSOR_NAMES,
    LEVER_NAMES,
    MEASURED_VARIABLES,
    PHYSICAL_BOUNDS,
    SECONDS_PER_STEP,
    SIMULATION_START_TIME,
    STEPS_PER_DAY,
    STEPS_PER_SHIFT,
    SimulatorConfig,
)
from causaltwin.simulator.operator import OperatingProfile, OperatorModel
from causaltwin.simulator.random_stream import RandomStream


def sigmoid(value: float) -> float:
    """Numerically safe logistic function."""
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def compute_hazard(bearing_temp: float, tool_wear: float, config: SimulatorConfig) -> float:
    """Per-step failure probability. Depends ONLY on bearing temperature and tool wear.

    Vibration is deliberately not an argument: it has no causal effect on failure.
    """
    logit = (
        config.hazard_temp_coef * (bearing_temp - config.hazard_temp_threshold)
        + config.hazard_wear_coef * (tool_wear - config.hazard_wear_threshold)
        + config.hazard_intercept
    )
    return sigmoid(logit)


def failure_probability(hazards: Iterable[float]) -> float:
    """Probability of at least one failure over a sequence of per-step hazards.

    P(at least one) = 1 - product of (1 - hazard). Used with the simulator's true
    hazard path to get ground-truth risk over a horizon (validation only).
    """
    survival = 1.0
    for hazard in hazards:
        survival *= 1.0 - hazard
    return 1.0 - survival


def _clip(variable: str, value: float) -> float:
    """Keep a value inside its physically sensible bounds."""
    low, high = PHYSICAL_BOUNDS[variable]
    return min(high, max(low, value))


class MachineSimulator:
    """Step-by-step simulator of the spindle, with interventions and fault injection."""

    def __init__(
        self,
        config: SimulatorConfig | None = None,
        seed: int = DEFAULT_SEED,
        profile: OperatingProfile | None = None,
    ) -> None:
        """Create a simulator at a steady initial state."""
        self.config = config or DEFAULT_CONFIG
        self.seed = seed
        self.profile = profile or OperatingProfile(self.config)
        self.random_stream = RandomStream(seed)
        self.operator = OperatorModel(self.config, self.profile)
        self.current_step = 0

        self.active_faults: set[str] = set()
        self.interventions: dict[str, float] = {}
        self._scheduled_actions: list[tuple[int, str, str, float | None]] = []
        self._downtime_remaining = 0
        self._ambient_drift = 0.0
        self._current_hazard = 0.0
        self._tool_change_limit = self._draw_tool_change_limit(0.5)

        initial_state = self._initial_true_state()
        # Two previous true states are kept because some effects have lag 2.
        self._previous_states: list[dict[str, float]] = [dict(initial_state), dict(initial_state)]
        self._latest_measured: dict[str, float] = dict(initial_state)
        self._sensor_records: dict[str, list[float]] = {
            name: [] for name in ("step", *MEASURED_VARIABLES, "failure", "machine_running")
        }
        self._truth_records: dict[str, list[float]] = {
            name: [] for name in ("step", *(f"true_{v}" for v in INTERNAL_SENSOR_NAMES), "true_hazard")
        }
        self._truth_fault_records: list[str] = []
        self._events: list[dict[str, object]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def intervene(self, variable: str, value: float, start_step: int | None = None) -> None:
        """Apply do(variable = value) from `start_step` (default: the next step) onward."""
        if variable not in MEASURED_VARIABLES:
            raise ValueError(f"Cannot intervene on unknown variable '{variable}'")
        low, high = PHYSICAL_BOUNDS[variable]
        if not low <= value <= high:
            raise ValueError(f"do({variable}={value}) is outside the physical bounds [{low}, {high}]")
        if start_step is None or start_step <= self.current_step:
            self.interventions[variable] = float(value)
        else:
            self._scheduled_actions.append((start_step, "intervene", variable, float(value)))

    def release(self, variable: str) -> None:
        """Remove an intervention (active or scheduled) so the variable follows its normal dynamics."""
        self.interventions.pop(variable, None)
        self._scheduled_actions = [
            action for action in self._scheduled_actions if not (action[1] == "intervene" and action[2] == variable)
        ]

    def set_profile(self, profile: OperatingProfile, config: SimulatorConfig | None = None) -> None:
        """Switch the operating profile (and optionally the config) mid-run.

        The physical state, faults, interventions and operator setpoints carry over.
        """
        if config is not None:
            self.config = config
            self.operator.config = config
        self.profile = profile
        self.operator.profile = profile

    def fork(
        self,
        seed: int | None = None,
        config_overrides: dict[str, Any] | None = None,
        keep_history: bool = False,
    ) -> "MachineSimulator":
        """Return an independent copy that continues from the current state.

        - `seed=None` keeps the random stream, so the copy repeats this simulator's
          future exactly (useful for paired "with vs without intervention" runs).
          A seed starts a fresh random stream (useful for averaging over futures).
        - `config_overrides` changes coefficients in the copy only, for example
          {"failures_enabled": False} or {"vibration_process_noise": 0.0}.
        - `keep_history=False` starts the copy with empty tables (fast); its rows
          still carry the true step numbers.
        """
        clone = copy.copy(self)
        clone.config = replace(self.config, **config_overrides) if config_overrides else self.config
        clone.profile = copy.copy(self.profile)
        clone.profile.config = clone.config
        clone.operator = copy.copy(self.operator)
        clone.operator.config = clone.config
        clone.operator.profile = clone.profile
        clone.operator.setpoints = dict(self.operator.setpoints)
        clone.operator.adjustments = dict(self.operator.adjustments)
        clone.random_stream = RandomStream(seed) if seed is not None else copy.deepcopy(self.random_stream)
        clone.active_faults = set(self.active_faults)
        clone.interventions = dict(self.interventions)
        clone._scheduled_actions = list(self._scheduled_actions)
        clone._previous_states = [dict(state) for state in self._previous_states]
        clone._latest_measured = dict(self._latest_measured)
        if keep_history:
            clone._sensor_records = {name: list(values) for name, values in self._sensor_records.items()}
            clone._truth_records = {name: list(values) for name, values in self._truth_records.items()}
            clone._truth_fault_records = list(self._truth_fault_records)
            clone._events = list(self._events)
        else:
            clone._sensor_records = {name: [] for name in self._sensor_records}
            clone._truth_records = {name: [] for name in self._truth_records}
            clone._truth_fault_records = []
            clone._events = []
        return clone

    def inject_fault(self, name: str, start_step: int | None = None) -> None:
        """Inject a structural fault now, or schedule it for `start_step`."""
        self._validate_fault(name)
        if start_step is None or start_step <= self.current_step:
            if name not in self.active_faults:
                self.active_faults.add(name)
                self._log_event("fault_injected", {"fault": name})
        else:
            self._scheduled_actions.append((start_step, "inject", name, None))

    def clear_fault(self, name: str, start_step: int | None = None) -> None:
        """Clear a structural fault now, or schedule the clearing for `start_step`."""
        self._validate_fault(name)
        if start_step is None or start_step <= self.current_step:
            if name in self.active_faults:
                self.active_faults.discard(name)
                self._log_event("fault_cleared", {"fault": name})
        else:
            self._scheduled_actions.append((start_step, "clear", name, None))

    def replace_tool(self) -> None:
        """Maintenance action: put in a new tool now (tool wear goes back to zero)."""
        latest = self._previous_states[-1]
        worn_value = latest["tool_wear"]
        latest["tool_wear"] = 0.0
        self._latest_measured["tool_wear"] = 0.0
        self._log_event("tool_change", {"wear_at_change": round(worn_value, 2), "reason": "manual"})

    def get_true_hazard(self) -> float:
        """Return the true failure probability of the latest step (VALIDATION ONLY)."""
        return self._current_hazard

    def get_state(self) -> dict[str, object]:
        """Return a snapshot of the latest measured values and simulator status."""
        return {
            "step": self.current_step,
            "measured": dict(self._latest_measured),
            "machine_running": self._downtime_remaining == 0,
            "active_faults": sorted(self.active_faults),
            "interventions": dict(self.interventions),
        }

    def run(self, n_steps: int) -> None:
        """Advance the simulator by `n_steps` steps."""
        for _ in range(n_steps):
            self.step()

    def step(self) -> dict[str, float]:
        """Advance the machine by one step (10 seconds) and return the measured values."""
        step_index = self.current_step
        config = self.config
        random_stream = self.random_stream
        self._apply_scheduled_actions(step_index)

        previous = self._previous_states[-1]  # values at t-1
        before_previous = self._previous_states[-2]  # values at t-2
        machine_running = self._downtime_remaining == 0

        # --- environment ---
        self._ambient_drift = (
            config.ambient_drift_persistence * self._ambient_drift + config.ambient_drift_std * random_stream.normal()
        )
        daily_cycle = config.ambient_daily_amplitude * math.sin(2.0 * math.pi * step_index / STEPS_PER_DAY)
        ambient_temp = config.ambient_base + daily_cycle + self._ambient_drift + self.profile.ambient_offset(step_index)

        # --- control levers (operator, then interventions, then machine stop) ---
        lever_values, setpoint_changes = self.operator.update(step_index, random_stream)
        for lever, old_value, new_value in setpoint_changes:
            self._log_event("setpoint_change", {"lever": lever, "old": round(old_value, 3), "new": round(new_value, 3)})
        true_state: dict[str, float] = {"ambient_temp": ambient_temp, **lever_values}
        for variable in ("ambient_temp", *LEVER_NAMES):
            if variable in self.interventions:
                true_state[variable] = self.interventions[variable]
        if not machine_running:
            # A stopped machine does not cut: no spindle rotation and no load.
            true_state["load_pct"] = 0.0
            true_state["spindle_speed"] = 0.0

        # --- internal dynamics (all effects lagged by 1 or 2 steps) ---
        current_target = (
            config.current_base
            + config.current_per_load * previous["load_pct"]
            + config.current_per_rpm * previous["spindle_speed"]
        )
        true_state["motor_current"] = self._first_order(
            previous["motor_current"], current_target, config.current_inertia, config.current_process_noise
        )

        flow_factor = config.clog_flow_effect_factor if "coolant_filter_clog" in self.active_faults else 1.0
        coolant_target = (
            previous["ambient_temp"]
            + config.coolant_heat_rise
            - config.coolant_flow_gain * flow_factor * previous["coolant_flow"]
        )
        true_state["coolant_temp"] = self._first_order(
            previous["coolant_temp"], coolant_target, config.coolant_inertia, config.coolant_process_noise
        )

        damage_factor = config.bearing_damage_heat_factor if "bearing_damage" in self.active_faults else 1.0
        if "lube_leak" in self.active_faults:
            effective_lube = config.lube_leak_effective_rate  # lube no longer reaches the bearing
        else:
            effective_lube = before_previous["lube_rate"]
        bearing_target = (
            before_previous["coolant_temp"]
            + config.bearing_base_rise
            + config.bearing_per_amp * damage_factor * before_previous["motor_current"]
            + config.bearing_per_rpm * previous["spindle_speed"]
            - config.bearing_lube_gain * effective_lube
        )
        true_state["bearing_temp"] = self._first_order(
            previous["bearing_temp"], bearing_target, config.bearing_inertia, config.bearing_process_noise
        )

        vibration_target = (
            config.vibration_base
            + config.vibration_per_load * previous["load_pct"]
            + config.vibration_per_rpm * previous["spindle_speed"]
            + config.vibration_per_wear * previous["tool_wear"]
        )
        true_state["vibration"] = self._first_order(
            previous["vibration"], vibration_target, config.vibration_inertia, config.vibration_process_noise
        )

        wear_increment = (
            (SECONDS_PER_STEP / 60.0)
            * (previous["load_pct"] / config.wear_reference_load)
            * (previous["spindle_speed"] / config.wear_reference_rpm) ** config.wear_rpm_exponent
        )
        chip_draw = random_stream.uniform()
        if machine_running and chip_draw < config.wear_chip_probability:
            wear_increment += config.wear_chip_size
        true_state["tool_wear"] = previous["tool_wear"] + wear_increment

        for variable in INTERNAL_SENSOR_NAMES:
            if variable in self.interventions:
                true_state[variable] = self.interventions[variable]
            true_state[variable] = _clip(variable, true_state[variable])

        # --- failure (hazard uses bearing_temp and tool_wear at t-1) ---
        if machine_running:
            self._current_hazard = compute_hazard(previous["bearing_temp"], previous["tool_wear"], config)
        else:
            self._current_hazard = 0.0
        failure_draw = random_stream.uniform()
        failure_happened = config.failures_enabled and machine_running and failure_draw < self._current_hazard

        # --- measurement (sensor noise on every measured sensor) ---
        # Levers are logged commands, so they are recorded exactly (no sensor noise).
        measured = dict(true_state)
        ambient_noise = config.ambient_sensor_noise * random_stream.normal()
        measured["ambient_temp"] = _clip("ambient_temp", true_state["ambient_temp"] + ambient_noise)
        sensor_noise_levels = {
            "motor_current": config.current_sensor_noise,
            "coolant_temp": config.coolant_sensor_noise,
            "bearing_temp": config.bearing_sensor_noise,
            "vibration": config.vibration_sensor_noise,
            "tool_wear": config.wear_sensor_noise,
        }
        for variable, noise_level in sensor_noise_levels.items():
            measured[variable] = _clip(variable, true_state[variable] + noise_level * random_stream.normal())

        # --- operator tool change and maintenance ---
        limit_draw = random_stream.uniform()
        if failure_happened:
            self._log_event(
                "failure",
                {"bearing_temp": round(measured["bearing_temp"], 2), "tool_wear": round(measured["tool_wear"], 2)},
            )
            self._downtime_remaining = config.failure_downtime_steps
        elif not machine_running:
            self._downtime_remaining -= 1
            if self._downtime_remaining == 0:
                self._reset_tool(true_state, measured)
                self._tool_change_limit = self._draw_tool_change_limit(limit_draw)
                self._log_event("maintenance", {"action": "repair after failure, tool replaced"})
        elif measured["tool_wear"] > self._tool_change_limit and "tool_wear" not in self.interventions:
            worn_value = measured["tool_wear"]
            self._reset_tool(true_state, measured)
            self._tool_change_limit = self._draw_tool_change_limit(limit_draw)
            self._log_event("tool_change", {"wear_at_change": round(worn_value, 2)})

        if step_index > 0 and step_index % STEPS_PER_SHIFT == 0:
            self._log_event("shift_change", {"shift_number": step_index // STEPS_PER_SHIFT})

        self._record(step_index, true_state, measured, failure_happened, machine_running)
        self._previous_states = [previous, true_state]
        self._latest_measured = measured
        self.current_step += 1
        return measured

    # ------------------------------------------------------------------
    # Output tables
    # ------------------------------------------------------------------
    def get_sensor_dataframe(self) -> pd.DataFrame:
        """Return all measured sensor values recorded so far, one row per step."""
        sensor_dataframe = pd.DataFrame(self._sensor_records)
        sensor_dataframe["step"] = sensor_dataframe["step"].astype(int)
        sensor_dataframe["failure"] = sensor_dataframe["failure"].astype(int)
        sensor_dataframe["machine_running"] = sensor_dataframe["machine_running"].astype(int)
        sensor_dataframe.insert(0, "timestamp", _steps_to_timestamps(sensor_dataframe["step"]))
        return sensor_dataframe

    def get_event_dataframe(self) -> pd.DataFrame:
        """Return the event log (timestamp, step, event_type, details as JSON text)."""
        event_dataframe = pd.DataFrame(self._events, columns=["step", "event_type", "details"])
        event_dataframe["step"] = event_dataframe["step"].astype(int)
        event_dataframe.insert(0, "timestamp", _steps_to_timestamps(event_dataframe["step"]))
        return event_dataframe

    def get_ground_truth_dataframe(self) -> pd.DataFrame:
        """Return simulator ground truth (true states, true hazard, faults). VALIDATION ONLY."""
        truth_dataframe = pd.DataFrame(self._truth_records)
        truth_dataframe["step"] = truth_dataframe["step"].astype(int)
        truth_dataframe["active_faults"] = self._truth_fault_records
        return truth_dataframe

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _first_order(self, previous_value: float, target: float, inertia: float, noise_std: float) -> float:
        """First-order lag (Newton-cooling style): move part of the way toward the target."""
        return inertia * previous_value + (1.0 - inertia) * target + noise_std * self.random_stream.normal()

    def _initial_true_state(self) -> dict[str, float]:
        """Approximate steady state with every lever at mid-range."""
        config = self.config
        state = {lever: self.operator.setpoints[lever] for lever in LEVER_NAMES}
        state["ambient_temp"] = config.ambient_base
        state["motor_current"] = (
            config.current_base
            + config.current_per_load * state["load_pct"]
            + config.current_per_rpm * state["spindle_speed"]
        )
        state["coolant_temp"] = (
            state["ambient_temp"] + config.coolant_heat_rise - config.coolant_flow_gain * state["coolant_flow"]
        )
        state["bearing_temp"] = (
            state["coolant_temp"]
            + config.bearing_base_rise
            + config.bearing_per_amp * state["motor_current"]
            + config.bearing_per_rpm * state["spindle_speed"]
            - config.bearing_lube_gain * state["lube_rate"]
        )
        state["tool_wear"] = 0.0
        state["vibration"] = (
            config.vibration_base
            + config.vibration_per_load * state["load_pct"]
            + config.vibration_per_rpm * state["spindle_speed"]
        )
        return state

    def _draw_tool_change_limit(self, uniform_draw: float) -> float:
        """Pick the wear level at which the operator will replace the next tool."""
        low, high = self.config.tool_change_limit_range
        return low + uniform_draw * (high - low)

    def _reset_tool(self, true_state: dict[str, float], measured: dict[str, float]) -> None:
        """Replace the tool: wear goes back to zero."""
        true_state["tool_wear"] = 0.0
        measured["tool_wear"] = 0.0

    def _apply_scheduled_actions(self, step_index: int) -> None:
        """Run any fault injections, fault clearings or interventions due at this step."""
        due_actions = [action for action in self._scheduled_actions if action[0] <= step_index]
        if not due_actions:
            return
        self._scheduled_actions = [action for action in self._scheduled_actions if action[0] > step_index]
        for _, kind, name, value in sorted(due_actions, key=lambda action: action[0]):
            if kind == "inject":
                self.inject_fault(name)
            elif kind == "clear":
                self.clear_fault(name)
            elif kind == "intervene" and value is not None:
                self.interventions[name] = value

    def _validate_fault(self, name: str) -> None:
        """Raise a clear error for an unknown fault name."""
        if name not in FAULT_NAMES:
            raise ValueError(f"Unknown fault '{name}'. Known faults: {', '.join(FAULT_NAMES)}")

    def _log_event(self, event_type: str, details: dict[str, object]) -> None:
        """Append one entry to the event log at the current step."""
        self._events.append({"step": self.current_step, "event_type": event_type, "details": json.dumps(details)})

    def _record(
        self,
        step_index: int,
        true_state: dict[str, float],
        measured: dict[str, float],
        failure_happened: bool,
        machine_running: bool,
    ) -> None:
        """Store one row of measured data and one row of ground truth."""
        records = self._sensor_records
        records["step"].append(step_index)
        for variable in MEASURED_VARIABLES:
            records[variable].append(measured[variable])
        records["failure"].append(1 if failure_happened else 0)
        records["machine_running"].append(1 if machine_running else 0)

        truth = self._truth_records
        truth["step"].append(step_index)
        for variable in INTERNAL_SENSOR_NAMES:
            truth[f"true_{variable}"].append(true_state[variable])
        truth["true_hazard"].append(self._current_hazard)
        self._truth_fault_records.append(",".join(sorted(self.active_faults)))


def _steps_to_timestamps(steps: pd.Series) -> pd.Series:
    """Convert step numbers into wall-clock timestamps."""
    start_time = pd.Timestamp(SIMULATION_START_TIME)
    return start_time + pd.to_timedelta(steps.to_numpy() * SECONDS_PER_STEP, unit="s")
