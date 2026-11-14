"""Validation of the causal engine against simulator ground truth (the proof layer).

Ground truth is only available because we built the simulator. It is used here, never
inside the engine. Results are written to backend/data/validation_results.json.

1. Graph recovery: precision, recall, F1, lag accuracy and structural Hamming distance
   against data/true_graph.json, with and without the failure edges (self-lags ignored).
2. Intervention accuracy: at random moments after the training period, a random lever
   change is predicted by the what-if engine and applied to forks of the real simulator
   (paired with and without the change, averaged over seeds).
3. Trap check: vibration must not be a discovered cause of failure, and must be listed
   as a symptom.
"""

import json
import time
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from causaltwin.config import (
    DEFAULT_CONFIG,
    LEVER_NAMES,
    MEASURED_VARIABLES,
    TAU_MAX,
    VALIDATION_CHUNK_STEPS,
    VALIDATION_INTERVENTIONS,
    VALIDATION_RESULTS_PATH,
    VALIDATION_SCENARIO_CYCLE,
    VALIDATION_SEED,
    VALIDATION_SEEDS_PER_INTERVENTION,
    VALIDATION_TRAINING_STEPS,
    WHATIF_HORIZON,
)
from causaltwin.engine.discovery import fast_mci, significant_lagged_edges, tigramite_mci
from causaltwin.engine.preprocessing import prepare_data
from causaltwin.engine.schemas import CausalEdge, CausalGraph, Intervention
from causaltwin.engine.twin import CausalTwinEngine, EngineSettings
from causaltwin.ingestion import align_events
from causaltwin.simulator import SCENARIOS, TRUE_EDGES, MachineSimulator, failure_probability, run_scenario
from causaltwin.simulator.scenarios import advance_scenario_cycle, run_scenario_cycle

TRAP_VARIABLE = "vibration"
OUTCOME = "failure"
MIN_TRUE_BEARING_CHANGE = 0.1  # deg C: smaller true changes are too small to judge direction
MIN_TRUE_RISK_CHANGE = 0.001  # probability
LEVER_CHANGE_PERCENT_RANGE = (10.0, 30.0)
STEPS_BETWEEN_CASES = (200, 600)


def true_cross_edges(include_failure: bool) -> dict[tuple[str, str], int]:
    """True (cause, effect) -> lag, self-lags excluded."""
    return {
        (str(edge["cause"]), str(edge["effect"])): cast(int, edge["lag"])
        for edge in TRUE_EDGES
        if edge["edge_type"] == "cross" and (include_failure or edge["effect"] != OUTCOME)
    }


def graph_recovery_metrics(edges: list[CausalEdge], include_failure: bool) -> dict[str, Any]:
    """Compare discovered cross edges with the true graph."""
    truth = true_cross_edges(include_failure)
    found = {
        (edge.cause, edge.effect): edge.lag
        for edge in edges
        if not edge.is_self_loop and (include_failure or edge.effect != OUTCOME)
    }
    true_positives = [pair for pair in found if pair in truth]
    false_positives = [pair for pair in found if pair not in truth]
    false_negatives = [pair for pair in truth if pair not in found]
    reversals = [pair for pair in false_positives if (pair[1], pair[0]) in false_negatives]
    precision = len(true_positives) / len(found) if found else 0.0
    recall = len(true_positives) / len(truth)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    correct_lags = [pair for pair in true_positives if found[pair] == truth[pair]]
    return {
        "n_true_edges": len(truth),
        "n_found_edges": len(found),
        "true_positives": len(true_positives),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "lag_accuracy": len(correct_lags) / len(true_positives) if true_positives else 0.0,
        "structural_hamming_distance": len(false_positives) + len(false_negatives) - len(reversals),
        "missed_edges": [f"{cause}->{effect} (lag {truth[(cause, effect)]})" for cause, effect in false_negatives],
        "extra_edges": [f"{cause}->{effect} (lag {found[(cause, effect)]})" for cause, effect in false_positives],
        "wrong_lags": [
            f"{cause}->{effect}: found {found[(cause, effect)]}, true {truth[(cause, effect)]}"
            for cause, effect in true_positives
            if found[(cause, effect)] != truth[(cause, effect)]
        ],
    }


def _usable_state(simulator: MachineSimulator) -> bool:
    """True if the machine is running and the last few steps had no stop or tool reset."""
    if not simulator.get_state()["machine_running"]:
        return False
    recent = simulator.get_sensor_dataframe().tail(TAU_MAX + 2)
    return bool((recent["machine_running"] == 1).all() and recent["tool_wear"].diff().dropna().ge(0).all())


def _true_effect(
    simulator: MachineSimulator, lever: str, value: float, horizon: int, seeds: list[int]
) -> dict[str, float]:
    """Paired simulator forks with and without do(lever = value), averaged over seeds."""
    bearing_end, bearing_mean, risk_change = [], [], []
    for seed in seeds:
        baseline = simulator.fork(seed=seed, config_overrides={"failures_enabled": False})
        treated = simulator.fork(seed=seed, config_overrides={"failures_enabled": False})
        treated.intervene(lever, value)
        baseline.run(horizon)
        treated.run(horizon)
        baseline_truth = baseline.get_ground_truth_dataframe()
        treated_truth = treated.get_ground_truth_dataframe()
        difference = treated_truth["true_bearing_temp"].to_numpy() - baseline_truth["true_bearing_temp"].to_numpy()
        bearing_end.append(float(difference[-1]))
        bearing_mean.append(float(difference.mean()))
        risk_change.append(
            failure_probability(treated_truth["true_hazard"]) - failure_probability(baseline_truth["true_hazard"])
        )
    return {
        "bearing_change_end": float(np.mean(bearing_end)),
        "bearing_change_mean": float(np.mean(bearing_mean)),
        "risk_change": float(np.mean(risk_change)),
    }


def intervention_accuracy(
    engine: CausalTwinEngine,
    simulator: MachineSimulator,
    n_cases: int,
    seeds_per_case: int,
    horizon: int,
    rng: np.random.Generator,
) -> dict[str, Any]:
    """Predicted versus true effect of random lever changes at random out-of-sample moments."""
    cases: list[dict[str, Any]] = []
    while len(cases) < n_cases:
        advance_scenario_cycle(
            simulator, int(rng.integers(*STEPS_BETWEEN_CASES)), VALIDATION_SCENARIO_CYCLE, VALIDATION_CHUNK_STEPS
        )
        if not _usable_state(simulator):
            continue
        history = simulator.get_sensor_dataframe().tail(TAU_MAX + 2)
        lever = str(rng.choice(LEVER_NAMES))
        settings = DEFAULT_CONFIG.levers[lever]
        percent = float(rng.uniform(*LEVER_CHANGE_PERCENT_RANGE)) * float(rng.choice([-1.0, 1.0]))
        current = float(history[lever].iloc[-1])
        value = float(np.clip(current * (1.0 + percent / 100.0), settings.minimum, settings.maximum))
        prediction = engine.what_if(
            [Intervention(variable=lever, change_type="set", value=value)], history=history, horizon=horizon
        )
        truth = _true_effect(
            simulator, lever, value, horizon, [int(seed) for seed in rng.integers(0, 2**31 - 1, size=seeds_per_case)]
        )
        cases.append(
            {
                "step": simulator.current_step,
                "lever": lever,
                "from": current,
                "to": value,
                "predicted_bearing_change_end": prediction.forecasts["bearing_temp"].change_at_end,
                "true_bearing_change_end": truth["bearing_change_end"],
                "predicted_bearing_change_mean": prediction.forecasts["bearing_temp"].mean_change,
                "true_bearing_change_mean": truth["bearing_change_mean"],
                "predicted_risk_change": prediction.risk_change,
                "true_risk_change": truth["risk_change"],
                "predicted_baseline_risk": None
                if prediction.baseline_risk is None
                else prediction.baseline_risk.probability,
            }
        )
    frame = pd.DataFrame(cases)
    judged_bearing = frame[frame["true_bearing_change_end"].abs() > MIN_TRUE_BEARING_CHANGE]
    judged_risk = frame[frame["true_risk_change"].abs() > MIN_TRUE_RISK_CHANGE]
    return {
        "n_cases": len(frame),
        "seeds_per_case": seeds_per_case,
        "horizon_steps": horizon,
        "bearing_temp_mae_end": float(
            (frame["predicted_bearing_change_end"] - frame["true_bearing_change_end"]).abs().mean()
        ),
        "bearing_temp_mae_mean": float(
            (frame["predicted_bearing_change_mean"] - frame["true_bearing_change_mean"]).abs().mean()
        ),
        "bearing_temp_mean_abs_true_change": float(frame["true_bearing_change_end"].abs().mean()),
        "bearing_temp_direction_accuracy": float(
            (
                np.sign(judged_bearing["predicted_bearing_change_end"])
                == np.sign(judged_bearing["true_bearing_change_end"])
            ).mean()
        )
        if len(judged_bearing)
        else None,
        "bearing_temp_direction_cases": len(judged_bearing),
        "risk_mae": float((frame["predicted_risk_change"] - frame["true_risk_change"]).abs().mean()),
        "risk_mean_abs_true_change": float(frame["true_risk_change"].abs().mean()),
        "risk_direction_accuracy": float(
            (np.sign(judged_risk["predicted_risk_change"]) == np.sign(judged_risk["true_risk_change"])).mean()
        )
        if len(judged_risk)
        else None,
        "risk_direction_cases": len(judged_risk),
        "cases": cases,
    }


def trap_check(engine: CausalTwinEngine) -> dict[str, Any]:
    """Vibration must not cause failure in the discovered graph, and must be a listed symptom."""
    graph = engine.get_graph()
    failure_causes = sorted(edge.cause for edge in graph.parents_of(OUTCOME))
    symptoms = engine.get_symptoms()
    symptom_names = [symptom.variable for symptom in symptoms]
    screen = engine.failure_screen
    trap_candidate = next((c for c in screen.candidates if c.variable == TRAP_VARIABLE), None) if screen else None
    no_effect = engine.what_if([Intervention(variable=TRAP_VARIABLE, change_type="percent", value=-50.0)])
    return {
        "discovered_failure_causes": failure_causes,
        "vibration_is_discovered_cause": TRAP_VARIABLE in failure_causes,
        "vibration_failure_screen_p_value": None if trap_candidate is None else trap_candidate.p_value,
        "vibration_failure_correlation": engine.failure_correlations.get(TRAP_VARIABLE),
        "symptoms": symptom_names,
        "vibration_is_symptom": TRAP_VARIABLE in symptom_names,
        "predicted_risk_change_when_vibration_halved": no_effect.risk_change,
        "passed": TRAP_VARIABLE not in failure_causes and TRAP_VARIABLE in symptom_names,
    }


def _edge_list(graph: CausalGraph) -> list[dict[str, Any]]:
    """Compact JSON-ready edge list."""
    return [
        {
            "cause": edge.cause,
            "effect": edge.effect,
            "lag": edge.lag,
            "strength": round(edge.strength, 4),
            "p_value": edge.p_value,
            "confidence": edge.confidence,
            "uncertain": edge.uncertain,
        }
        for edge in graph.edges
        if not edge.is_self_loop
    ]


def per_scenario_recovery(window_steps: int) -> dict[str, Any]:
    """Lagged-graph recovery on the last window of each single scenario (no failure edges)."""
    results = {}
    for name in SCENARIOS:
        result = run_scenario(name)
        window = prepare_data(align_events(result.sensors, result.events)).tail(window_steps)
        edges = significant_lagged_edges(fast_mci(window, TAU_MAX), EngineSettings().alpha)
        metrics = graph_recovery_metrics(edges, include_failure=False)
        results[name] = {
            key: metrics[key]
            for key in (
                "precision",
                "recall",
                "f1",
                "lag_accuracy",
                "structural_hamming_distance",
                "missed_edges",
                "extra_edges",
            )
        }
    return results


def run_validation(output_path: Path = VALIDATION_RESULTS_PATH) -> dict[str, Any]:
    """Run the full validation and save the results as JSON."""
    rng = np.random.default_rng(VALIDATION_SEED)
    simulation = run_scenario_cycle(
        VALIDATION_SCENARIO_CYCLE, VALIDATION_CHUNK_STEPS, VALIDATION_TRAINING_STEPS, VALIDATION_SEED
    )
    training_data = align_events(simulation.sensors, simulation.events)
    engine = CausalTwinEngine()
    engine.warm_up()
    fit_start = time.perf_counter()
    engine.fit(training_data)
    fit_seconds = time.perf_counter() - fit_start
    step_timings = {
        entry.step_name: entry.duration_ms
        for entry in engine.get_activity_log()
        if entry.duration_ms is not None
        and entry.step_name
        in (
            "warm_up",
            "pcmci",
            "failure_screen",
            "discovery",
            "bootstrap",
            "fit_model",
            "correlation_graph",
            "fit_engine",
        )
    }
    assert engine.window is not None
    reference = tigramite_mci(engine.window, TAU_MAX, n_jobs=EngineSettings().discovery_n_jobs)
    equivalent = fast_mci(engine.window, TAU_MAX)
    tested = ~np.isnan(reference.p_matrix) & ~np.isnan(equivalent.p_matrix)
    tested[:, :, 0] = False  # lag 0 is never tested in time-series mode
    whatif_ms = [
        engine.what_if([Intervention(variable="coolant_flow", change_type="percent", value=20.0)]).computation_ms
        for _ in range(5)
    ]
    graph = engine.get_graph()
    results: dict[str, Any] = {
        "source": "Simulator ground truth (validation only)",
        "training_data": {
            "scenario_cycle": list(VALIDATION_SCENARIO_CYCLE),
            "chunk_steps": VALIDATION_CHUNK_STEPS,
            "steps": VALIDATION_TRAINING_STEPS,
            "seed": VALIDATION_SEED,
            "failures": int(simulation.sensors["failure"].sum()),
            "discovery_window_steps": engine.settings.window_steps,
        },
        "graph_recovery_with_failure": graph_recovery_metrics(graph.edges, include_failure=True),
        "graph_recovery_without_failure": graph_recovery_metrics(graph.edges, include_failure=False),
        "per_scenario_recovery_without_failure": per_scenario_recovery(engine.settings.window_steps),
        "discovered_edges": _edge_list(graph),
        "graph_notes": graph.notes,
        "model_cards": [card.model_dump() for card in engine.get_model_cards()],
        "numpy_vs_tigramite_max_abs_difference": {
            "partial_correlation": float(np.abs(reference.val_matrix - equivalent.val_matrix)[tested].max()),
            "p_value": float(np.abs(reference.p_matrix - equivalent.p_matrix)[tested].max()),
        },
        "trap_check": trap_check(engine),
        "intervention_accuracy": intervention_accuracy(
            engine,
            simulation.simulator,
            VALIDATION_INTERVENTIONS,
            VALIDATION_SEEDS_PER_INTERVENTION,
            WHATIF_HORIZON,
            rng,
        ),
        "timings_ms": {
            **step_timings,
            "fit_total": fit_seconds * 1000.0,
            "what_if_200_rollouts_median": float(np.median(whatif_ms)),
        },
        "variables": list(MEASURED_VARIABLES),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    return results
