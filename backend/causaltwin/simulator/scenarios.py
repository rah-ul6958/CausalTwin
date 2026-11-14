"""Named, reproducible operating scenarios for the simulated spindle.

Each scenario fixes a seed, an operating profile (how lever ranges and the
environment change over the run) and an optional fault schedule.
"""

from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from causaltwin.config import DEFAULT_CONFIG, DEFAULT_N_STEPS, SCENARIO_SEEDS, SimulatorConfig
from causaltwin.simulator.machine import MachineSimulator
from causaltwin.simulator.operator import OperatingProfile
from causaltwin.utils.activity_log import get_activity_logger
from causaltwin.utils.timing import timed_step

# Scenario shaping constants (illustrative).
OVERLOAD_FINAL_LOAD_MIN = 90.0  # by the end of the run the operator never goes below this load
OVERLOAD_FINAL_SPEED_MIN = 3600.0
COOLING_FINAL_FLOW_MAX = 10.0  # coolant flow ceiling at the end of the cooling_failure run
COOLING_CLOG_PROGRESS = 0.5  # filter clogs halfway through the run
HEAT_WAVE_START, HEAT_WAVE_PEAK, HEAT_WAVE_END = 0.25, 0.45, 0.9
HEAT_WAVE_EXTRA_DEG = 14.0
TOOL_WEAR_CHANGE_LIMIT = (400.0, 600.0)  # tools are run far beyond normal before replacement
# In normal operation the operator avoids the extremes of each lever's allowed range.
NORMAL_COMFORT_RANGES: dict[str, tuple[float, float]] = {
    "load_pct": (40.0, 85.0),
    "spindle_speed": (1500.0, 4000.0),
    "coolant_flow": (12.0, 30.0),
    "lube_rate": (0.7, 1.5),
}


class NormalProfile(OperatingProfile):
    """Stable operation: setpoints stay inside a comfortable part of each lever's range."""

    def lever_range(self, lever: str, step: int) -> tuple[float, float]:
        """Intersect the allowed range with the comfortable range."""
        low, high = super().lever_range(lever, step)
        comfort_low, comfort_high = NORMAL_COMFORT_RANGES[lever]
        return max(low, comfort_low), min(high, comfort_high)


class OverloadProfile(OperatingProfile):
    """Load and speed creep upward over the run."""

    def lever_range(self, lever: str, step: int) -> tuple[float, float]:
        """Raise the lower bound of load and speed as the run progresses."""
        low, high = super().lever_range(lever, step)
        progress = self.progress(step)
        if lever == "load_pct":
            low = low + progress * (OVERLOAD_FINAL_LOAD_MIN - low)
        elif lever == "spindle_speed":
            low = low + progress * (OVERLOAD_FINAL_SPEED_MIN - low)
        return low, high


class CoolingFailureProfile(OperatingProfile):
    """Available coolant flow drops over the run (pump degrading)."""

    def lever_range(self, lever: str, step: int) -> tuple[float, float]:
        """Lower the ceiling on coolant flow as the run progresses."""
        low, high = super().lever_range(lever, step)
        if lever == "coolant_flow":
            high = high - self.progress(step) * (high - COOLING_FINAL_FLOW_MAX)
        return low, high


class HeatWaveProfile(OperatingProfile):
    """Ambient temperature rises sharply, holds, then recovers."""

    def ambient_offset(self, step: int) -> float:
        """Piecewise-linear heat-wave bump in deg C."""
        progress = self.progress(step)
        if progress < HEAT_WAVE_START or progress > HEAT_WAVE_END:
            return 0.0
        if progress < HEAT_WAVE_PEAK:
            return HEAT_WAVE_EXTRA_DEG * (progress - HEAT_WAVE_START) / (HEAT_WAVE_PEAK - HEAT_WAVE_START)
        return HEAT_WAVE_EXTRA_DEG * (HEAT_WAVE_END - progress) / (HEAT_WAVE_END - HEAT_WAVE_PEAK)


@dataclass(frozen=True)
class ScenarioDefinition:
    """Static description of one scenario."""

    name: str
    description: str
    profile_class: type[OperatingProfile]
    config_overrides: dict[str, Any]  # values of mixed types, checked by the dataclass itself
    faults: tuple[tuple[float, str, str], ...] = ()  # (progress 0..1, "inject"/"clear", fault name)


SCENARIOS: dict[str, ScenarioDefinition] = {
    "normal": ScenarioDefinition("normal", "Stable operation, rare failures.", NormalProfile, {}),
    "overload": ScenarioDefinition("overload", "Load and speed creep up over time.", OverloadProfile, {}),
    "cooling_failure": ScenarioDefinition(
        "cooling_failure",
        "Coolant flow drops, then the coolant filter clogs mid-run.",
        CoolingFailureProfile,
        {},
        faults=((COOLING_CLOG_PROGRESS, "inject", "coolant_filter_clog"),),
    ),
    "heat_wave": ScenarioDefinition("heat_wave", "Ambient temperature rises sharply.", HeatWaveProfile, {}),
    "tool_wear": ScenarioDefinition(
        "tool_wear",
        "Long run without tool changes.",
        OperatingProfile,
        {"tool_change_limit_range": TOOL_WEAR_CHANGE_LIMIT},
    ),
}


@dataclass
class SimulationResult:
    """Everything a simulation run produces. `ground_truth` is for validation only."""

    sensors: pd.DataFrame
    events: pd.DataFrame
    ground_truth: pd.DataFrame
    simulator: MachineSimulator


def _get_definition(scenario_name: str) -> ScenarioDefinition:
    """Look up a scenario, with a clear error for unknown names."""
    if scenario_name not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario_name}'. Known: {', '.join(SCENARIOS)}")
    return SCENARIOS[scenario_name]


def _schedule_faults(
    simulator: MachineSimulator, definition: ScenarioDefinition, start_step: int, n_steps: int
) -> None:
    """Schedule a scenario's faults relative to the step where the scenario starts."""
    for progress, action, fault_name in definition.faults:
        fault_step = start_step + int(progress * n_steps)
        if action == "inject":
            simulator.inject_fault(fault_name, start_step=fault_step)
        else:
            simulator.clear_fault(fault_name, start_step=fault_step)


def build_simulator(
    scenario_name: str,
    n_steps: int = DEFAULT_N_STEPS,
    seed: int | None = None,
    base_config: SimulatorConfig = DEFAULT_CONFIG,
) -> MachineSimulator:
    """Create a simulator configured for a scenario (faults scheduled, not yet run)."""
    definition = _get_definition(scenario_name)
    config = replace(base_config, **definition.config_overrides)
    profile = definition.profile_class(config, total_steps=n_steps)
    simulator = MachineSimulator(config, seed=SCENARIO_SEEDS[scenario_name] if seed is None else seed, profile=profile)
    _schedule_faults(simulator, definition, 0, n_steps)
    return simulator


def switch_scenario(
    simulator: MachineSimulator,
    scenario_name: str,
    n_steps: int = DEFAULT_N_STEPS,
    base_config: SimulatorConfig = DEFAULT_CONFIG,
) -> None:
    """Switch a running simulator to another scenario without resetting the machine.

    The physical state, active faults and interventions carry over. The new
    scenario's progress (and its fault schedule) counts from the current step.
    """
    definition = _get_definition(scenario_name)
    config = replace(base_config, **definition.config_overrides)
    profile = definition.profile_class(config, total_steps=n_steps, start_step=simulator.current_step)
    simulator.set_profile(profile, config)
    _schedule_faults(simulator, definition, simulator.current_step, n_steps)
    get_activity_logger().log(
        "switch_scenario", f"Switched to scenario '{scenario_name}'", details={"step": simulator.current_step}
    )


def run_scenario(
    scenario_name: str,
    n_steps: int = DEFAULT_N_STEPS,
    seed: int | None = None,
    base_config: SimulatorConfig = DEFAULT_CONFIG,
) -> SimulationResult:
    """Run a scenario and return sensors, events and ground truth."""
    with timed_step("simulate", f"Simulated scenario '{scenario_name}'") as timer:
        simulator = build_simulator(scenario_name, n_steps, seed, base_config)
        simulator.run(n_steps)
        sensors = simulator.get_sensor_dataframe()
        events = simulator.get_event_dataframe()
        ground_truth = simulator.get_ground_truth_dataframe()
        timer.details.update({"n_steps": n_steps, "failures": int(sensors["failure"].sum())})
    return SimulationResult(sensors, events, ground_truth, simulator)


def generate_dataset(
    scenario_name: str, n_steps: int = DEFAULT_N_STEPS, seed: int | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (sensor_dataframe, event_dataframe) for a scenario. Simulated data."""
    result = run_scenario(scenario_name, n_steps, seed)
    return result.sensors, result.events
