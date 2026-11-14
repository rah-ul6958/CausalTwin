"""Tests for the simulated spindle: determinism, speed, bounds, interventions, trap, faults."""

import ast
import inspect
import json
import textwrap
import time
from dataclasses import replace

import numpy as np
import pandas as pd

from causaltwin.config import (
    DEFAULT_CONFIG,
    LEVER_NAMES,
    MEASURED_VARIABLES,
    PHYSICAL_BOUNDS,
    TRUE_GRAPH_PATH,
    VARIABLE_INFO,
)
from causaltwin.simulator import (
    SCENARIOS,
    MachineSimulator,
    SimulationResult,
    compute_hazard,
    generate_dataset,
    get_true_graph,
)

STRESSED_SCENARIOS = ("overload", "cooling_failure", "heat_wave", "tool_wear")
NO_FAILURE_CONFIG = replace(DEFAULT_CONFIG, failures_enabled=False)


def _ols_coefficients(target: np.ndarray, predictors: list[np.ndarray]) -> np.ndarray:
    """Least-squares coefficients (intercept first) of target on the predictors."""
    design = np.column_stack([np.ones(len(target)), *predictors])
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    return coefficients


def test_simulator_is_deterministic_for_fixed_seed() -> None:
    """Same seed gives identical data; a different seed gives different data."""
    first_sensors, first_events = generate_dataset("normal", 3000, seed=11)
    second_sensors, second_events = generate_dataset("normal", 3000, seed=11)
    other_sensors, _ = generate_dataset("normal", 3000, seed=12)
    pd.testing.assert_frame_equal(first_sensors, second_sensors)
    pd.testing.assert_frame_equal(first_events, second_events)
    assert not first_sensors["bearing_temp"].equals(other_sensors["bearing_temp"])


def test_twenty_thousand_steps_under_two_seconds() -> None:
    """Performance budget: 20,000 steps (including building the tables) in < 2 s."""
    start_time = time.perf_counter()
    sensors, _ = generate_dataset("normal", 20_000)
    elapsed_seconds = time.perf_counter() - start_time
    print(f"20,000 steps simulated in {elapsed_seconds:.3f} s")
    assert len(sensors) == 20_000
    assert elapsed_seconds < 2.0


def test_variables_stay_inside_physical_bounds(scenario_results: dict[str, SimulationResult]) -> None:
    """Every measured variable stays physically sensible; levers stay in their normal range while running."""
    for name, result in scenario_results.items():
        sensors = result.sensors
        assert not sensors[list(MEASURED_VARIABLES)].isna().any().any(), name
        for variable in MEASURED_VARIABLES:
            low, high = PHYSICAL_BOUNDS[variable]
            assert sensors[variable].between(low, high).all(), f"{name}: {variable} out of bounds"
        running = sensors[sensors["machine_running"] == 1]
        for lever in LEVER_NAMES:
            low, high = VARIABLE_INFO[lever]["normal_range"]  # type: ignore[misc]
            assert running[lever].between(low, high).all(), f"{name}: {lever} outside normal range"
        assert set(sensors["failure"].unique()) <= {0, 1}


def test_normal_bearing_temperature_mostly_in_normal_range(scenario_results: dict[str, SimulationResult]) -> None:
    """In normal operation the bearing runs about 40-65 deg C most of the time."""
    running = scenario_results["normal"].sensors.query("machine_running == 1")
    share_in_range = running["bearing_temp"].between(40.0, 65.0).mean()
    print(f"normal: {share_in_range:.1%} of running steps have bearing_temp in 40-65 deg C")
    assert share_in_range > 0.85


def test_coolant_intervention_acts_with_expected_lags() -> None:
    """do(coolant_flow = max) lowers coolant_temp after 1 step and bearing_temp after 3 steps."""
    baseline = MachineSimulator(NO_FAILURE_CONFIG, seed=7)
    baseline.run(1200)
    baseline_sensors = baseline.get_sensor_dataframe()
    baseline_truth = baseline.get_ground_truth_dataframe()
    start_step = int(baseline_sensors.index[(baseline_sensors.index >= 500) & (baseline_sensors["coolant_flow"] < 25)][0])

    treated = MachineSimulator(NO_FAILURE_CONFIG, seed=7)
    treated.intervene("coolant_flow", 30.0, start_step=start_step)
    treated.run(1200)
    treated_sensors = treated.get_sensor_dataframe()
    treated_truth = treated.get_ground_truth_dataframe()

    assert (treated_sensors["coolant_flow"].iloc[start_step:] == 30.0).all()
    coolant_difference = treated_truth["true_coolant_temp"] - baseline_truth["true_coolant_temp"]
    bearing_difference = treated_truth["true_bearing_temp"] - baseline_truth["true_bearing_temp"]
    # No effect before the lag has passed.
    assert np.allclose(coolant_difference.iloc[: start_step + 1], 0.0)
    assert np.allclose(bearing_difference.iloc[: start_step + 3], 0.0)
    # coolant_flow -> coolant_temp has lag 1; coolant_temp -> bearing_temp has lag 2 (total 3).
    assert coolant_difference.iloc[start_step + 1] < 0
    assert bearing_difference.iloc[start_step + 3] < 0
    # Sustained effect over the following period, also visible in the noisy measurements.
    window = slice(start_step + 20, start_step + 400)
    assert coolant_difference.iloc[window].mean() < -1.0
    assert bearing_difference.iloc[window].mean() < -1.0
    measured_bearing_difference = treated_sensors["bearing_temp"] - baseline_sensors["bearing_temp"]
    assert measured_bearing_difference.iloc[window].mean() < -1.0


def test_correlation_trap_exists(scenario_results: dict[str, SimulationResult]) -> None:
    """Vibration correlates with risk, more than the true cause tool_wear does."""
    for name, result in scenario_results.items():
        sensors = result.sensors.assign(true_hazard=result.ground_truth["true_hazard"])
        running = sensors[sensors["machine_running"] == 1]
        vibration_correlation = running["vibration"].corr(running["true_hazard"])
        wear_correlation = running["tool_wear"].corr(running["true_hazard"])
        print(f"{name}: corr(vibration, hazard)={vibration_correlation:+.3f} corr(tool_wear, hazard)={wear_correlation:+.3f}")
        assert vibration_correlation > 0.1, name
        assert vibration_correlation > abs(wear_correlation), name

    pooled = pd.concat([result.sensors for result in scenario_results.values()])
    pooled = pooled[pooled["machine_running"] == 1]
    vibration_failure_correlation = pooled["vibration"].corr(pooled["failure"])
    for true_cause in ("tool_wear", "lube_rate", "coolant_flow"):
        assert vibration_failure_correlation > abs(pooled[true_cause].corr(pooled["failure"])), true_cause


def test_vibration_is_not_an_input_to_the_hazard() -> None:
    """Code inspection: the hazard function never reads vibration."""
    parameter_names = list(inspect.signature(compute_hazard).parameters)
    assert parameter_names == ["bearing_temp", "tool_wear", "config"]
    function_tree = ast.parse(textwrap.dedent(inspect.getsource(compute_hazard)))
    referenced_names = {node.id for node in ast.walk(function_tree) if isinstance(node, ast.Name)}
    referenced_names |= {node.attr for node in ast.walk(function_tree) if isinstance(node, ast.Attribute)}
    assert not any("vibration" in name for name in referenced_names)


def test_intervening_on_vibration_changes_nothing_about_failure() -> None:
    """do(vibration = extreme) leaves the true hazard and the failures exactly unchanged."""
    baseline = MachineSimulator(seed=21)
    baseline.run(8000)
    treated = MachineSimulator(seed=21)
    treated.intervene("vibration", 25.0, start_step=100)
    treated.run(8000)

    baseline_truth = baseline.get_ground_truth_dataframe()
    treated_truth = treated.get_ground_truth_dataframe()
    assert (treated.get_sensor_dataframe()["vibration"].iloc[100:] > 20).all()
    np.testing.assert_array_equal(baseline_truth["true_hazard"], treated_truth["true_hazard"])
    np.testing.assert_array_equal(baseline.get_sensor_dataframe()["failure"], treated.get_sensor_dataframe()["failure"])
    np.testing.assert_array_equal(baseline_truth["true_bearing_temp"], treated_truth["true_bearing_temp"])

    # In contrast, intervening on a true cause (bearing temperature) does change the hazard.
    hot = MachineSimulator(seed=21)
    hot.intervene("bearing_temp", 85.0, start_step=100)
    hot.run(500)
    assert hot.get_ground_truth_dataframe()["true_hazard"].iloc[200:].mean() > baseline_truth["true_hazard"].iloc[200:500].mean() * 10


def _fault_split_run(fault_name: str) -> tuple[pd.DataFrame, int]:
    """Run 20,000 steps without failures and inject a fault halfway."""
    fault_step = 10_000
    simulator = MachineSimulator(NO_FAILURE_CONFIG, seed=33)
    simulator.inject_fault(fault_name, start_step=fault_step)
    simulator.run(20_000)
    return simulator.get_sensor_dataframe(), fault_step


def test_coolant_filter_clog_weakens_flow_effect() -> None:
    """After the clog, the measured effect of coolant_flow on coolant_temp drops sharply."""
    sensors, fault_step = _fault_split_run("coolant_filter_clog")
    flow_coefficients = []
    for segment in (slice(10, fault_step), slice(fault_step + 10, len(sensors))):
        data = sensors.iloc[segment]
        previous = data.shift(1)
        valid = previous.notna().all(axis=1)
        coefficients = _ols_coefficients(
            data["coolant_temp"][valid].to_numpy(),
            [previous[column][valid].to_numpy() for column in ("coolant_temp", "coolant_flow", "ambient_temp")],
        )
        flow_coefficients.append(coefficients[2])
    ratio = flow_coefficients[1] / flow_coefficients[0]
    print(f"coolant_flow -> coolant_temp coefficient before={flow_coefficients[0]:.4f} after={flow_coefficients[1]:.4f} ratio={ratio:.2f}")
    assert flow_coefficients[0] < 0
    assert 0.0 < ratio < 0.4


def _bearing_coefficients(data: pd.DataFrame) -> dict[str, float]:
    """Regress bearing_temp on its true parents at their true lags (running steps only)."""
    lag1 = data.shift(1)
    lag2 = data.shift(2)
    valid = lag2.notna().all(axis=1) & (data["machine_running"] == 1) & (lag2["machine_running"] == 1)
    names = ["bearing_temp_lag1", "motor_current_lag2", "spindle_speed_lag1", "coolant_temp_lag2", "lube_rate_lag2"]
    predictors = [
        lag1["bearing_temp"][valid].to_numpy(),
        lag2["motor_current"][valid].to_numpy(),
        lag1["spindle_speed"][valid].to_numpy(),
        lag2["coolant_temp"][valid].to_numpy(),
        lag2["lube_rate"][valid].to_numpy(),
    ]
    coefficients = _ols_coefficients(data["bearing_temp"][valid].to_numpy(), predictors)
    return dict(zip(names, coefficients[1:]))


def test_lube_leak_removes_lube_effect() -> None:
    """After the leak, lube_rate no longer affects bearing_temp."""
    sensors, fault_step = _fault_split_run("lube_leak")
    before = _bearing_coefficients(sensors.iloc[:fault_step])
    after = _bearing_coefficients(sensors.iloc[fault_step:])
    print(f"lube_rate -> bearing_temp before={before['lube_rate_lag2']:.3f} after={after['lube_rate_lag2']:.3f}")
    assert before["lube_rate_lag2"] < -1.0
    assert abs(after["lube_rate_lag2"]) < 0.2 * abs(before["lube_rate_lag2"])


def test_bearing_damage_strengthens_current_effect() -> None:
    """After bearing damage, the same motor current heats the bearing more."""
    sensors, fault_step = _fault_split_run("bearing_damage")
    before = _bearing_coefficients(sensors.iloc[:fault_step])
    after = _bearing_coefficients(sensors.iloc[fault_step:])
    print(f"motor_current -> bearing_temp before={before['motor_current_lag2']:.3f} after={after['motor_current_lag2']:.3f}")
    assert after["motor_current_lag2"] > 1.3 * before["motor_current_lag2"]


def test_faults_are_written_to_event_log_and_can_be_cleared() -> None:
    """Injecting and clearing a fault produces events and restores normal behavior."""
    simulator = MachineSimulator(NO_FAILURE_CONFIG, seed=5)
    simulator.inject_fault("lube_leak", start_step=100)
    simulator.clear_fault("lube_leak", start_step=200)
    simulator.run(300)
    events = simulator.get_event_dataframe()
    injected = events[events["event_type"] == "fault_injected"]
    cleared = events[events["event_type"] == "fault_cleared"]
    assert injected["step"].tolist() == [100]
    assert cleared["step"].tolist() == [200]
    assert json.loads(injected["details"].iloc[0])["fault"] == "lube_leak"
    faults_per_step = simulator.get_ground_truth_dataframe()["active_faults"]
    assert faults_per_step.iloc[150] == "lube_leak" and faults_per_step.iloc[250] == ""


def test_failure_counts_per_scenario(scenario_results: dict[str, SimulationResult]) -> None:
    """Normal operation has rare failures; stressed scenarios have at least 20 per 20,000 steps."""
    failure_counts = {name: int(result.sensors["failure"].sum()) for name, result in scenario_results.items()}
    print(f"failure counts per 20,000 steps: {failure_counts}")
    assert 1 <= failure_counts["normal"] < 20
    for name in STRESSED_SCENARIOS:
        assert failure_counts[name] >= 20, name


def test_failure_triggers_downtime_and_maintenance(scenario_results: dict[str, SimulationResult]) -> None:
    """After every failure the machine stops, then maintenance resets tool wear."""
    result = scenario_results["overload"]
    sensors = result.sensors
    downtime = DEFAULT_CONFIG.failure_downtime_steps
    maintenance_steps = set(result.events.query("event_type == 'maintenance'")["step"])
    for failure_step in sensors.index[sensors["failure"] == 1]:
        if failure_step + downtime >= len(sensors):
            continue
        stopped = sensors.iloc[failure_step + 1 : failure_step + downtime + 1]
        assert (stopped["machine_running"] == 0).all()
        assert (stopped["spindle_speed"] == 0).all()
        assert failure_step + downtime in maintenance_steps
        assert sensors["tool_wear"].iloc[failure_step + downtime] == 0.0


def test_scenario_specific_behavior(scenario_results: dict[str, SimulationResult]) -> None:
    """Each scenario shows its intended stress pattern."""
    normal = scenario_results["normal"]
    overload = scenario_results["overload"].sensors.query("machine_running == 1")
    assert overload["load_pct"].iloc[-2000:].mean() > overload["load_pct"].iloc[:2000].mean() + 15
    cooling_events = scenario_results["cooling_failure"].events
    assert "fault_injected" in set(cooling_events["event_type"])
    heat = scenario_results["heat_wave"].sensors
    assert heat["ambient_temp"].max() > normal.sensors["ambient_temp"].max() + 8
    tool_events = scenario_results["tool_wear"].events
    assert (tool_events["event_type"] == "tool_change").sum() == 0
    assert scenario_results["tool_wear"].sensors["tool_wear"].max() > 200
    for result in scenario_results.values():
        assert {"setpoint_change", "shift_change"} <= set(result.events["event_type"])


def test_true_graph_file_matches_code() -> None:
    """data/true_graph.json is in sync with the code and has no vibration -> failure edge."""
    stored_graph = json.loads(TRUE_GRAPH_PATH.read_text(encoding="utf-8"))
    assert stored_graph == json.loads(json.dumps(get_true_graph()))
    edges = {(edge["cause"], edge["effect"]) for edge in stored_graph["edges"]}
    assert ("vibration", "failure") not in edges
    assert {("bearing_temp", "failure"), ("tool_wear", "failure")} <= edges
    for edge in stored_graph["edges"]:
        assert 1 <= edge["lag"] <= 3 and edge["sign"] in "+-" and edge["explanation"]


def test_unknown_inputs_raise_clear_errors() -> None:
    """Invalid scenario, fault or variable names fail loudly."""
    simulator = MachineSimulator(seed=1)
    for bad_call in (
        lambda: simulator.inject_fault("not_a_fault"),
        lambda: simulator.intervene("not_a_variable", 1.0),
        lambda: generate_dataset("not_a_scenario", 10),
    ):
        try:
            bad_call()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")
    assert set(SCENARIOS) == {"normal", "overload", "cooling_failure", "heat_wave", "tool_wear"}
