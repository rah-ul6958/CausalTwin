"""The Correlation Trap benchmark: CausalTwin versus standard ML, judged by the real simulator.

1. Find high-risk situations in all five scenarios (different seeds): the instantaneous
   true hazard passes a cheap screen, then the TRUE risk of failure within the horizon
   (forks of the real simulator, averaged over seeds) must be at least 15%.
2. CausalTwin recommends with the optimizer; the baseline blames its top SHAP feature
   and maps it to an engineer's action (see baseline.py).
3. Both actions are applied to forks of the real simulator from the same starting state
   with the same seeds. Measured: true risk after the action (true hazard), failure rate
   within the horizon, and cost. Success = true risk after <= the 10% target.
Every case is reported, including the ones where CausalTwin does worse.

Three baselines are evaluated on every case (see baseline.py): B1 naive SHAP (the rule in step 2),
B2 actionable SHAP, and B3 predict-then-optimize, the main comparison. All are applied to the same forks
with the same seeds as CausalTwin. Two flags are stored for every method, CausalTwin included:
worse_than_no_action (true risk after > no-action true risk + BENCHMARK_WORSE_MARGIN) and
false_reassurance (the method chose to do nothing while the true no-action risk was above the target).
"""

import json
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

import numpy as np
import pandas as pd

from causaltwin.config import (
    BASELINE_DEFAULT_MAGNITUDE_PCT,
    BASELINE_METHOD_LABELS,
    BENCHMARK_HAZARD_SCREEN,
    BENCHMARK_MAX_STEPS_PER_RUN,
    BENCHMARK_MIN_GAP_STEPS,
    BENCHMARK_MIN_TRUE_RISK,
    BENCHMARK_RESULTS_PATH,
    BENCHMARK_SEED_BASE,
    BENCHMARK_SEEDS_PER_SITUATION,
    BENCHMARK_SITUATIONS_PER_SCENARIO,
    BENCHMARK_WORSE_MARGIN,
    MAIN_BASELINE,
    OPTIMIZER_REQUIRED_CONFIDENCE,
    OPTIMIZER_SELECTION_RULE,
    OPTIMIZER_TARGET_RISK,
    TAU_MAX,
    VALIDATION_RESULTS_PATH,
    WHATIF_HORIZON,
)
from causaltwin.intelligence.baseline import BaselineModel
from causaltwin.intelligence.drift import SimulatorStepper, row_is_usable
from causaltwin.intelligence.optimizer import recommend
from causaltwin.intelligence.schemas import BaselineAction, BenchmarkCase, BenchmarkMethodResult, Recommendation
from causaltwin.intelligence.training import TrainedModels, train_models
from causaltwin.simulator import SCENARIOS, MachineSimulator, build_simulator, failure_probability
from causaltwin.utils.timing import timed_step

ProgressCallback = Callable[[int, int, str], None]
TIE_TOLERANCE = 0.01  # true-risk difference below which the methods tie
SCREEN_RETRY_STEPS = 20
NO_FAILURE = {"failures_enabled": False}
DAMPING_OVERRIDES = {"vibration_process_noise": 0.0, "vibration_sensor_noise": 0.0}


@dataclass
class Situation:
    """A high-risk moment: the simulator at that moment and the measured history."""

    scenario: str
    seed: int
    step: int
    simulator: MachineSimulator
    history: pd.DataFrame
    true_risk: float


def true_outcome(
    simulator: MachineSimulator,
    apply: Callable[[MachineSimulator], None],
    seeds: list[int],
    horizon: int,
    overrides: dict[str, Any] | None = None,
) -> tuple[float, float]:
    """True risk (from the true hazard) and failure rate within the horizon, averaged over seeds."""
    risks, failures = [], []
    for seed in seeds:
        no_failure = simulator.fork(seed=seed, config_overrides={**NO_FAILURE, **(overrides or {})})
        apply(no_failure)
        no_failure.run(horizon)
        risks.append(failure_probability(no_failure.get_ground_truth_dataframe()["true_hazard"]))
        with_failure = simulator.fork(seed=seed, config_overrides=overrides or None)
        apply(with_failure)
        with_failure.run(horizon)
        failures.append(float(with_failure.get_sensor_dataframe()["failure"].sum() > 0))
    return float(np.mean(risks)), float(np.mean(failures))


def find_situations(
    per_scenario: int, seed_base: int, seeds: list[int], horizon: int, progress: ProgressCallback | None = None
) -> list[Situation]:
    """Scan each scenario until enough high-risk moments are found."""
    situations: list[Situation] = []
    for scenario_index, scenario in enumerate(SCENARIOS):
        found = 0
        for attempt in range(3):
            if found >= per_scenario:
                break
            seed = seed_base + 100 * scenario_index + attempt
            simulator = build_simulator(scenario, n_steps=BENCHMARK_MAX_STEPS_PER_RUN, seed=seed)
            stepper = SimulatorStepper(simulator)
            recent: deque[dict[str, Any]] = deque(maxlen=TAU_MAX + 2)
            next_check = 0
            for step in range(BENCHMARK_MAX_STEPS_PER_RUN):
                row, _ = stepper.step()
                recent.append(row)
                if step < next_check or len(recent) < TAU_MAX + 2 or not all(row_is_usable(r) for r in recent):
                    continue
                if simulator.get_true_hazard() < BENCHMARK_HAZARD_SCREEN:
                    continue
                risk, _ = true_outcome(simulator, lambda _: None, seeds[:4], horizon)
                if risk < BENCHMARK_MIN_TRUE_RISK:
                    next_check = step + SCREEN_RETRY_STEPS
                    continue
                situations.append(Situation(scenario, seed, step, simulator.fork(), pd.DataFrame(list(recent)), risk))
                found += 1
                next_check = step + BENCHMARK_MIN_GAP_STEPS
                if progress:
                    progress(len(situations), per_scenario * len(SCENARIOS), f"Found situation in {scenario}")
                if found >= per_scenario:
                    break
    return situations


def _apply_recommendation(recommendation: Recommendation) -> Callable[[MachineSimulator], None]:
    """Apply CausalTwin's recommendation to a simulator fork."""

    def apply(simulator: MachineSimulator) -> None:
        for change in recommendation.changes:
            simulator.intervene(change.lever, change.to_value)
        if recommendation.tool_change:
            simulator.replace_tool()

    return apply


def _apply_baseline(action: BaselineAction) -> Callable[[MachineSimulator], None]:
    """Apply the baseline's action to a simulator fork (damping is a config override)."""

    def apply(simulator: MachineSimulator) -> None:
        if action.kind == "lever" and action.changes:
            for change in action.changes:
                simulator.intervene(change.lever, change.to_value)
        elif action.kind == "lever" and action.lever is not None and action.to_value is not None:
            simulator.intervene(action.lever, action.to_value)
        elif action.kind == "tool_change":
            simulator.replace_tool()

    return apply


ActionKey = tuple[tuple[tuple[str, float], ...], bool, bool]


def _action_key(lever_values: list[tuple[str, float]], tool_change: bool, damping: bool) -> ActionKey:
    """What an action does to the machine, so identical actions are simulated only once per case."""
    return tuple(sorted((lever, round(value, 9)) for lever, value in lever_values)), tool_change, damping


def _baseline_key(action: BaselineAction) -> ActionKey:
    """The action key of a baseline action."""
    if action.kind == "lever" and action.changes:
        levers = [(change.lever, change.to_value) for change in action.changes]
    elif action.kind == "lever" and action.lever is not None and action.to_value is not None:
        levers = [(action.lever, action.to_value)]
    else:
        levers = []
    return _action_key(levers, action.kind == "tool_change", action.kind == "damp_vibration")


class OutcomeFlags(TypedDict):
    """Whether a method did nothing, made things worse, or gave false reassurance in one case."""

    no_action: bool
    worse_than_no_action: bool
    false_reassurance: bool


def outcome_flags(
    true_risk_after: float,
    true_risk_no_action: float,
    chose_no_action: bool,
    target_risk: float = OPTIMIZER_TARGET_RISK,
    worse_margin: float = BENCHMARK_WORSE_MARGIN,
) -> OutcomeFlags:
    """The two warning flags of one method's action in one case.

    worse_than_no_action: the action left the machine riskier than doing nothing (beyond the margin).
    false_reassurance: the method chose to do nothing while the true risk was above the target.
    """
    return {
        "no_action": chose_no_action,
        "worse_than_no_action": true_risk_after > true_risk_no_action + worse_margin,
        "false_reassurance": chose_no_action and true_risk_no_action > target_risk,
    }


def _winner(causaltwin_risk: float, baseline_risk: float) -> Literal["causaltwin", "baseline", "tie"]:
    """Which method left the lower true risk (a difference below TIE_TOLERANCE is a tie)."""
    if abs(causaltwin_risk - baseline_risk) < TIE_TOLERANCE:
        return "tie"
    return "causaltwin" if causaltwin_risk < baseline_risk else "baseline"


def _describe(recommendation: Recommendation) -> str:
    """Short text of CausalTwin's action."""
    parts = [f"{c.lever} {c.change_pct:+.0f}%" for c in recommendation.changes]
    if recommendation.tool_change:
        parts.append("tool change")
    return ", ".join(parts) or "no action"


def evaluate_situation(
    case_id: int,
    situation: Situation,
    models: TrainedModels,
    seeds: list[int],
    horizon: int,
    selection_rule: str | None = None,
    baseline_low_risk_rule: bool = False,
) -> BenchmarkCase:
    """Both methods' actions on one situation, judged in the real simulator."""
    engine, baseline = models.engine, models.baseline
    assert engine.model is not None
    history = situation.history[engine.model.variables].to_numpy(dtype=float)
    recommendation = recommend(engine, history, horizon=horizon, selection_rule=selection_rule)
    root_cause = engine.root_cause(situation.history[engine.model.variables])
    blamed_ct = " -> ".join(node.variable for node in root_cause.paths[0].nodes) if root_cause.paths else "none"
    magnitude = max((abs(c.change_pct) for c in recommendation.changes), default=BASELINE_DEFAULT_MAGNITUDE_PCT)
    actions = baseline.recommend_all(
        situation.history.iloc[-1], magnitude, OPTIMIZER_TARGET_RISK, shap_low_risk_rule=baseline_low_risk_rule
    )

    # True outcomes, simulated once per distinct action (forks with the same seeds are deterministic).
    outcomes: dict[ActionKey, tuple[float, float]] = {}
    no_action_key = _action_key([], False, False)
    outcomes[no_action_key] = true_outcome(situation.simulator, lambda _: None, seeds, horizon)
    no_action_risk, no_action_failures = outcomes[no_action_key]
    ct_key = _action_key([(c.lever, c.to_value) for c in recommendation.changes], recommendation.tool_change, False)
    if ct_key not in outcomes:
        outcomes[ct_key] = true_outcome(situation.simulator, _apply_recommendation(recommendation), seeds, horizon)
    ct_risk, ct_failures = outcomes[ct_key]
    baseline_results: dict[str, BenchmarkMethodResult] = {}
    for method, action in actions.items():
        key = _baseline_key(action)
        if key not in outcomes:
            outcomes[key] = true_outcome(
                situation.simulator,
                _apply_baseline(action),
                seeds,
                horizon,
                DAMPING_OVERRIDES if action.kind == "damp_vibration" else None,
            )
        risk, failures = outcomes[key]
        baseline_results[method] = BenchmarkMethodResult(
            action=action.description,
            blamed=action.blamed_feature,
            cost=action.cost,
            true_risk_after=risk,
            risk_reduction=no_action_risk - risk,
            failure_rate_after=failures,
            success=risk <= OPTIMIZER_TARGET_RISK,
            predicted_risk_after=action.predicted_risk_after,
            **outcome_flags(risk, no_action_risk, action.kind == "none"),
        )
    winner_by_baseline = {
        method: _winner(ct_risk, result.true_risk_after) for method, result in baseline_results.items()
    }
    return BenchmarkCase(
        case_id=case_id,
        scenario=situation.scenario,
        seed=situation.seed,
        step=situation.step,
        true_risk_no_action=no_action_risk,
        failure_rate_no_action=no_action_failures,
        twin_risk_no_action=recommendation.risk_before.probability,
        twin_risk_no_action_interval=(recommendation.risk_before.lower, recommendation.risk_before.upper),
        causaltwin=BenchmarkMethodResult(
            action=_describe(recommendation),
            blamed=blamed_ct,
            cost=recommendation.cost,
            true_risk_after=ct_risk,
            risk_reduction=no_action_risk - ct_risk,
            failure_rate_after=ct_failures,
            success=ct_risk <= OPTIMIZER_TARGET_RISK,
            predicted_risk_after=recommendation.risk_after.probability,
            predicted_risk_after_interval=(recommendation.risk_after.lower, recommendation.risk_after.upper),
            **outcome_flags(ct_risk, no_action_risk, recommendation.action_type == "none"),
        ),
        baselines=baseline_results,
        winner=winner_by_baseline[MAIN_BASELINE],
        winner_by_baseline=winner_by_baseline,
    )


def _calibration_pair(
    predicted: list[float], intervals: list[tuple[float, float]], true: list[float]
) -> dict[str, Any]:
    """How well predicted risks match the true risks: bias, error, interval coverage, correlation."""
    predicted_array, true_array = np.array(predicted), np.array(true)
    covered = [lower <= value <= upper for (lower, upper), value in zip(intervals, true, strict=True)]
    correlation = float(np.corrcoef(predicted_array, true_array)[0, 1]) if len(true) > 2 else None
    return {
        "n_cases": len(true),
        "mean_predicted": float(predicted_array.mean()),
        "mean_true": float(true_array.mean()),
        "mean_error": float((predicted_array - true_array).mean()),
        "mean_absolute_error": float(np.abs(predicted_array - true_array).mean()),
        "interval_coverage": float(np.mean(covered)),
        # Where the misses fall: true risk below the interval's lower end, or above its upper end.
        "n_below_interval": sum(value < lower for (lower, _), value in zip(intervals, true, strict=True)),
        "n_above_interval": sum(value > upper for (_, upper), value in zip(intervals, true, strict=True)),
        "correlation": correlation,
    }


def calibration_summary(cases: list[BenchmarkCase]) -> dict[str, Any]:
    """The twin's predicted risks against the simulator's true risks, before and after its action.

    Interval coverage is the share of cases whose true risk falls inside the twin's 90% interval.
    """
    usable = [case for case in cases if case.twin_risk_no_action is not None]
    if not usable:
        return {}
    acted = [case for case in usable if case.causaltwin.predicted_risk_after is not None]
    return {
        "n_cases": len(usable),
        "no_action": _calibration_pair(
            [case.twin_risk_no_action or 0.0 for case in usable],
            [case.twin_risk_no_action_interval or (0.0, 1.0) for case in usable],
            [case.true_risk_no_action for case in usable],
        ),
        "after_action": _calibration_pair(
            [case.causaltwin.predicted_risk_after or 0.0 for case in acted],
            [case.causaltwin.predicted_risk_after_interval or (0.0, 1.0) for case in acted],
            [case.causaltwin.true_risk_after for case in acted],
        )
        if acted
        else {},
        # The same check split by the kind of action CausalTwin took.
        "after_action_by_kind": {
            kind: _calibration_pair(
                [case.causaltwin.predicted_risk_after or 0.0 for case in group],
                [case.causaltwin.predicted_risk_after_interval or (0.0, 1.0) for case in group],
                [case.causaltwin.true_risk_after for case in group],
            )
            for kind, group in (
                ("tool_change", [case for case in acted if "tool change" in case.causaltwin.action]),
                ("levers", [case for case in acted if "tool change" not in case.causaltwin.action]),
            )
            if group
        },
    }


def _method_result(case: BenchmarkCase, method: str) -> BenchmarkMethodResult:
    """One method's result in one case ("causaltwin" or a baseline method name)."""
    return case.causaltwin if method == "causaltwin" else case.baselines[method]


def _summary(cases: list[BenchmarkCase], method: str) -> dict[str, Any]:
    """Aggregate one method's results ("causaltwin" or a baseline method name)."""
    results = [_method_result(case, method) for case in cases]
    worse = [case.case_id for case, r in zip(cases, results, strict=True) if r.worse_than_no_action]
    reassured = [case.case_id for case, r in zip(cases, results, strict=True) if r.false_reassurance]
    return {
        "success_rate": float(np.mean([r.success for r in results])),
        "n_success": int(sum(r.success for r in results)),
        "mean_risk_reduction": float(np.mean([r.risk_reduction for r in results])),
        "mean_true_risk_after": float(np.mean([r.true_risk_after for r in results])),
        "mean_failure_rate_after": float(np.mean([r.failure_rate_after for r in results])),
        "mean_cost": float(np.mean([r.cost for r in results])),
        "blamed": dict(Counter(r.blamed for r in results)),
        "n_no_action": int(sum(r.no_action for r in results)),
        "n_worse_than_no_action": len(worse),
        "cases_worse_than_no_action": worse,
        "n_false_reassurance": len(reassured),
        "cases_false_reassurance": reassured,
    }


def _scenario_summary(cases: list[BenchmarkCase]) -> dict[str, Any]:
    """Success rates of every method within one scenario (None when the scenario has no cases)."""
    if not cases:
        return {"n": 0, "causaltwin_success": None, "baseline_success": dict.fromkeys(BASELINE_METHOD_LABELS)}
    return {
        "n": len(cases),
        "causaltwin_success": float(np.mean([c.causaltwin.success for c in cases])),
        "baseline_success": {
            method: float(np.mean([c.baselines[method].success for c in cases])) for method in BASELINE_METHOD_LABELS
        },
    }


def run_benchmark(
    models: TrainedModels | None = None,
    progress: ProgressCallback | None = None,
    output_path: Path | None = BENCHMARK_RESULTS_PATH,
    per_scenario: int = BENCHMARK_SITUATIONS_PER_SCENARIO,
    seeds_per_situation: int = BENCHMARK_SEEDS_PER_SITUATION,
    horizon: int = WHATIF_HORIZON,
    selection_rule: str | None = None,
    baseline_low_risk_rule: bool = False,
) -> dict[str, Any]:
    """Run the whole benchmark, report every case, and cache the results as JSON."""
    with timed_step("benchmark", "Ran the Correlation Trap benchmark") as timer:
        start_time = time.perf_counter()
        models = models or train_models()
        seeds = [BENCHMARK_SEED_BASE + 1000 + k for k in range(seeds_per_situation)]
        situations = find_situations(per_scenario, BENCHMARK_SEED_BASE, seeds, horizon, progress)
        search_seconds = time.perf_counter() - start_time
        cases = []
        for case_id, situation in enumerate(situations):
            cases.append(
                evaluate_situation(case_id, situation, models, seeds, horizon, selection_rule, baseline_low_risk_rule)
            )
            if progress:
                progress(case_id + 1, len(situations), f"Evaluated case {case_id + 1} ({situation.scenario})")
        validation = json.loads(VALIDATION_RESULTS_PATH.read_text()) if VALIDATION_RESULTS_PATH.exists() else {}
        results: dict[str, Any] = {
            "source": "Simulator ground truth (benchmark judged by the real simulator)",
            "settings": {
                "situations_per_scenario": per_scenario,
                "seeds_per_situation": seeds_per_situation,
                "horizon_steps": horizon,
                "target_risk": OPTIMIZER_TARGET_RISK,
                "selection_rule": selection_rule or OPTIMIZER_SELECTION_RULE,
                "required_confidence": OPTIMIZER_REQUIRED_CONFIDENCE,
                # B1 and B2 always act in the benchmark (see BaselineModel.recommend_all).
                "baseline_low_risk_rule": baseline_low_risk_rule,
                "min_true_risk": BENCHMARK_MIN_TRUE_RISK,
                "success": "true risk after the action <= target",
            },
            "n_cases": len(cases),
            "summary": {
                "causaltwin": _summary(cases, "causaltwin") if cases else {},
                "baselines": {method: _summary(cases, method) if cases else {} for method in BASELINE_METHOD_LABELS},
                "main_baseline": MAIN_BASELINE,
                "baseline_labels": dict(BASELINE_METHOD_LABELS),
                "mean_true_risk_no_action": float(np.mean([c.true_risk_no_action for c in cases])) if cases else None,
                "mean_failure_rate_no_action": float(np.mean([c.failure_rate_no_action for c in cases]))
                if cases
                else None,
                # Head to head against the main baseline (B3), then against every baseline.
                "wins": dict(Counter(case.winner for case in cases)),
                "cases_where_causaltwin_is_worse": [c.case_id for c in cases if c.winner == "baseline"],
                "wins_by_baseline": {
                    method: dict(Counter(case.winner_by_baseline[method] for case in cases))
                    for method in BASELINE_METHOD_LABELS
                },
                "cases_where_causaltwin_is_worse_by_baseline": {
                    method: [c.case_id for c in cases if c.winner_by_baseline[method] == "baseline"]
                    for method in BASELINE_METHOD_LABELS
                },
            },
            "per_scenario": {
                scenario: _scenario_summary([c for c in cases if c.scenario == scenario]) for scenario in SCENARIOS
            },
            "cases": [case.model_dump() for case in cases],
            "calibration": calibration_summary(cases),
            "baseline_accuracy": models.baseline.metrics,
            "graph_recovery": {
                key: validation.get(key) for key in ("graph_recovery_with_failure", "graph_recovery_without_failure")
            },
            "timing_seconds": {"situation_search": search_seconds, "total": time.perf_counter() - start_time},
        }
        timer.details.update({"cases": len(cases), "seconds": round(time.perf_counter() - start_time, 1)})
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


__all__ = ["BaselineModel", "run_benchmark", "find_situations", "evaluate_situation", "true_outcome"]
