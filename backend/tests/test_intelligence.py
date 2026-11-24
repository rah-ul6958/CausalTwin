"""Tests for the Phase 3 intelligence layer: optimizer, baseline, benchmark, drift detection."""

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from causaltwin.config import (
    BASELINE_ACTIONABLE_FEATURES,
    BASELINE_METHOD_LABELS,
    BENCHMARK_WORSE_MARGIN,
    DEFAULT_CONFIG,
    LEVER_COSTS,
    LEVER_NAMES,
    MAIN_BASELINE,
    OPTIMIZER_PLANNING_MARGIN,
    OPTIMIZER_REQUIRED_CONFIDENCE,
    OPTIMIZER_TARGET_RISK,
    TOOL_CHANGE_COST,
    WHATIF_INTERVAL_PERCENTILES,
)
from causaltwin.engine import Intervention
from causaltwin.engine.whatif import batch_failure_risk, run_what_if
from causaltwin.intelligence.benchmark import outcome_flags, run_benchmark
from causaltwin.intelligence.drift import count_false_alarms, evaluate_fault
from causaltwin.intelligence.optimizer import build_candidates, qualifies, recommend
from causaltwin.intelligence.training import TrainedModels, train_models

warnings.filterwarnings("ignore")


@pytest.fixture(scope="session")
def models() -> TrainedModels:
    """Trained engine and baseline (loaded from the cache when it is up to date)."""
    return train_models()


def _state_before_failure(models: TrainedModels, which: int) -> np.ndarray:
    """The history just before one of the training failures."""
    data = models.training_data
    assert models.engine.model is not None
    row = data.index[data["failure"] == 1][which]
    return data.loc[row - 4 : row - 1, models.engine.model.variables].to_numpy(dtype=float)


def _highest_risk_state(models: TrainedModels, candidates: int = 30) -> np.ndarray:
    """Among the states just before the first training failures, the one with the highest predicted risk."""
    model = models.engine.model
    assert model is not None
    states = [_state_before_failure(models, which) for which in range(candidates)]
    risks = [run_what_if(model, state, [], 30, 100, 1, WHATIF_INTERVAL_PERCENTILES).baseline_risk for state in states]
    return states[int(np.argmax([risk.probability if risk else 0.0 for risk in risks]))]


def test_batch_evaluator_matches_single_what_if(models: TrainedModels) -> None:
    """The optimizer's batched evaluator reproduces the single what-if (32-bit tolerance)."""
    model = models.engine.model
    assert model is not None
    history = _state_before_failure(models, 3)
    interventions = [
        Intervention(variable="lube_rate", change_type="set", value=1.4),
        Intervention(variable="tool_wear", change_type="reset", value=0.0),
    ]
    single = run_what_if(model, history, interventions, 30, 100, 5, WHATIF_INTERVAL_PERCENTILES)
    batch = batch_failure_risk(model, history, [[], interventions], 30, 100, 5, WHATIF_INTERVAL_PERCENTILES)
    assert single.risk is not None and single.baseline_risk is not None
    assert batch["risk"][1] == pytest.approx(single.risk.probability, abs=1e-4)
    assert batch["risk"][0] == pytest.approx(single.baseline_risk.probability, abs=1e-4)


def test_optimizer_is_valid_fast_and_never_acts_on_vibration(models: TrainedModels) -> None:
    """A high-risk state gets an in-range recommendation within 1 s; vibration is listed as ineffective."""
    history = _highest_risk_state(models)
    recommend(models.engine, history)  # warm-up (first call pays one-time costs)
    recommendation = recommend(models.engine, history)
    print(recommendation.explanation, f"{recommendation.search_ms:.0f} ms")
    assert recommendation.search_ms < 1000
    assert recommendation.risk_before.probability > OPTIMIZER_TARGET_RISK
    assert recommendation.action_type != "none"
    assert recommendation.risk_after.probability < recommendation.risk_before.probability
    for change in recommendation.changes:
        settings = DEFAULT_CONFIG.levers[change.lever]
        assert settings.minimum <= change.to_value <= settings.maximum
        assert abs(change.change_pct) <= LEVER_COSTS[change.lever].max_change_pct + 1e-6
    assert len(recommendation.candidates) == recommendation.n_candidates > 500  # duplicates removed after clipping
    assert all(change.lever != "vibration" for c in recommendation.candidates for change in c.changes)
    assert "vibration" in [item.variable for item in recommendation.ineffective]
    confidence = recommendation.confidence
    for value in (
        confidence.path_edge_confidence,
        confidence.interval_score,
        confidence.validation_score,
        confidence.combined,
    ):
        assert value is not None and 0.0 <= value <= 1.0
    assert recommendation.time_to_effect_steps is not None and recommendation.causal_paths


def test_optimizer_recommends_nothing_when_risk_is_low(models: TrainedModels) -> None:
    """In calm normal operation the optimizer says no action is needed."""
    data = models.training_data
    assert models.engine.model is not None
    calm = data[(data["bearing_temp"] < 50) & (data["tool_wear"] < 20) & (data["machine_running"] == 1)].index[100]
    history = data.loc[calm - 3 : calm, models.engine.model.variables].to_numpy(dtype=float)
    recommendation = recommend(models.engine, history)
    assert recommendation.action_type == "none" and recommendation.cost == 0.0
    assert recommendation.explanation.startswith("No action needed")
    assert recommendation.headline.startswith("No change is needed: failure risk is predicted at")


def test_selection_rules() -> None:
    """Confidence rule: share of rollouts at or below the target; margin rule: mean below target * margin."""
    target = OPTIMIZER_TARGET_RISK
    assert qualifies(0.09, OPTIMIZER_REQUIRED_CONFIDENCE, target, "confidence")
    assert not qualifies(0.02, OPTIMIZER_REQUIRED_CONFIDENCE - 0.01, target, "confidence")
    assert qualifies(target * OPTIMIZER_PLANNING_MARGIN, 0.0, target, "margin")
    assert not qualifies(target * OPTIMIZER_PLANNING_MARGIN + 0.001, 1.0, target, "margin")


def test_recommendation_headline_minimum_change_and_least_effective_lever(models: TrainedModels) -> None:
    """The brief's sentence, the smallest qualifying single-lever change, and the lever that helps least."""
    history = _highest_risk_state(models)
    recommendation = recommend(models.engine, history)
    headline = recommendation.headline
    print(headline)
    assert headline.split(" ")[0] in {"Increasing", "Reducing", "Changing"}
    assert "is predicted to cut failure risk from" in headline and "within 5 minutes" in headline
    assert f"from {recommendation.risk_before.probability:.0%}" in headline
    rule = recommendation.selection_rule
    singles = [c for c in recommendation.candidates if len(c.changes) == 1 and not c.tool_change]
    qualifying = [
        c for c in singles if qualifies(c.risk, c.share_below_target or 0.0, recommendation.target_risk, rule)
    ]
    minimum = recommendation.minimum_effective_change
    if qualifying:
        assert minimum is not None
        smallest = min(abs(c.changes[0].change_pct) for c in qualifying)
        assert abs(minimum.change.change_pct) == pytest.approx(smallest)
    else:
        assert minimum is None
    least = recommendation.least_effective_lever
    assert least is not None
    best_by_lever: dict[str, float] = {}
    for candidate in singles:
        lever = candidate.changes[0].lever
        best_by_lever[lever] = max(best_by_lever.get(lever, -1.0), candidate.risk_reduction)
    assert least.variable == min(best_by_lever, key=lambda lever: best_by_lever[lever])
    assert least.best_risk_reduction == pytest.approx(best_by_lever[least.variable])


def test_optimizer_runs_with_either_selection_rule(models: TrainedModels) -> None:
    """Both rules work; under the confidence rule the chosen option's rollouts mostly end below the target."""
    history = _highest_risk_state(models)
    by_confidence = recommend(models.engine, history, selection_rule="confidence")
    by_margin = recommend(models.engine, history, selection_rule="margin")
    assert by_confidence.selection_rule == "confidence" and by_margin.selection_rule == "margin"
    assert by_confidence.share_below_target is not None
    if by_confidence.meets_target:
        assert by_confidence.share_below_target >= OPTIMIZER_REQUIRED_CONFIDENCE
    with pytest.raises(ValueError, match="selection rule"):
        recommend(models.engine, history, selection_rule="cheapest")


def test_baseline_is_competent_but_falls_into_the_trap(models: TrainedModels) -> None:
    """The XGBoost baseline predicts well (AUC), yet often blames vibration at high-risk states."""
    metrics = models.baseline.metrics
    print(metrics)
    assert metrics["auc"] > 0.8 and metrics["training_seconds"] < 10
    data = models.training_data
    rows = data.index[data["failure"] == 1][:40]
    blamed = [models.baseline.recommend(data.loc[row - 1], 20.0).blamed_feature for row in rows]
    print({name: blamed.count(name) for name in set(blamed)})
    assert blamed.count("vibration") >= 5


def test_benchmark_runs_end_to_end(models: TrainedModels, tmp_path: Path) -> None:
    """A small benchmark finds situations, evaluates both methods in the simulator, and writes results."""
    output = tmp_path / "benchmark.json"
    progress: list[tuple[int, int]] = []
    results = run_benchmark(
        models,
        progress=lambda done, total, _: progress.append((done, total)),
        output_path=output,
        per_scenario=1,
        seeds_per_situation=2,
    )
    saved = json.loads(output.read_text())
    assert saved["n_cases"] == results["n_cases"] >= 4 and progress
    methods = set(BASELINE_METHOD_LABELS)
    for case in saved["cases"]:
        assert 0.0 <= case["causaltwin"]["true_risk_after"] <= 1.0
        assert set(case["baselines"]) == methods and case["causaltwin"]["blamed"]
        assert case["winner"] == case["winner_by_baseline"][MAIN_BASELINE]
        for result in [case["causaltwin"], *case["baselines"].values()]:
            assert result["blamed"]
            # The stored flags are exactly what outcome_flags gives for the stored risks.
            expected = outcome_flags(result["true_risk_after"], case["true_risk_no_action"], result["no_action"])
            assert {name: result[name] for name in expected} == expected
    summary = saved["summary"]
    assert set(summary) >= {"causaltwin", "baselines", "wins", "cases_where_causaltwin_is_worse", "main_baseline"}
    assert set(summary["baselines"]) == methods and summary["main_baseline"] == MAIN_BASELINE
    for method in ["causaltwin", *methods]:
        method_summary = summary["causaltwin"] if method == "causaltwin" else summary["baselines"][method]
        results_of_method = [
            case["causaltwin"] if method == "causaltwin" else case["baselines"][method] for case in saved["cases"]
        ]
        assert method_summary["n_worse_than_no_action"] == sum(r["worse_than_no_action"] for r in results_of_method)
        assert method_summary["n_false_reassurance"] == sum(r["false_reassurance"] for r in results_of_method)
    assert saved["baseline_accuracy"]["auc"] > 0.8


def test_outcome_flags() -> None:
    """Worse than doing nothing (beyond the margin) and false reassurance (no action above the target)."""
    target, margin = OPTIMIZER_TARGET_RISK, BENCHMARK_WORSE_MARGIN
    made_worse = outcome_flags(0.30, 0.25, chose_no_action=False)
    assert made_worse == {"no_action": False, "worse_than_no_action": True, "false_reassurance": False}
    within_margin = outcome_flags(0.25 + margin / 2, 0.25, chose_no_action=False)
    assert not within_margin["worse_than_no_action"]
    reassured = outcome_flags(0.25, 0.25, chose_no_action=True)
    assert reassured == {"no_action": True, "worse_than_no_action": False, "false_reassurance": True}
    rightly_idle = outcome_flags(target / 2, target / 2, chose_no_action=True)
    assert not rightly_idle["false_reassurance"] and not rightly_idle["worse_than_no_action"]
    helped = outcome_flags(0.02, 0.25, chose_no_action=False)
    assert not any(helped.values())


def _rows_before_failures(models: TrainedModels, count: int = 40) -> list[pd.Series]:
    """Measured rows one step before the first training failures (high-risk states)."""
    data = models.training_data
    return [data.loc[row - 1] for row in data.index[data["failure"] == 1][:count]]


def test_actionable_baseline_blames_only_what_an_engineer_can_change(models: TrainedModels) -> None:
    """B2 never blames a symptom or the weather: only a lever (moved) or tool wear (tool change)."""
    actions = [models.baseline.recommend_actionable(row, 20.0) for row in _rows_before_failures(models)]
    for action in actions:
        assert action.method == "actionable_shap"
        assert action.blamed_feature in (*BASELINE_ACTIONABLE_FEATURES, "none")
        assert action.kind != "damp_vibration"
        if action.blamed_feature == "tool_wear":
            assert action.kind == "tool_change" and action.cost == TOOL_CHANGE_COST
        elif action.kind == "lever":
            assert action.lever == action.blamed_feature and len(action.changes) == 1
            assert action.changes[0].to_value == action.to_value
    # Where B1 blames vibration, B2 picks something actionable instead.
    naive = [models.baseline.recommend(row, 20.0) for row in _rows_before_failures(models)]
    assert any(n.blamed_feature == "vibration" for n in naive)
    assert not any(a.blamed_feature == "vibration" for a in actions)


def test_counterfactual_baseline_searches_the_optimizers_candidates(models: TrainedModels) -> None:
    """B3 picks the cheapest of CausalTwin's own candidates that XGBoost predicts below target * margin.

    Each candidate changes only the lever values in the feature row (a tool change sets tool_wear to 0),
    so the predicted risk after B3's action is the model's risk for that edited row.
    """
    baseline = models.baseline
    planning_target = OPTIMIZER_TARGET_RISK * OPTIMIZER_PLANNING_MARGIN
    acted = 0
    for row in _rows_before_failures(models):
        action = baseline.recommend_counterfactual(row)
        assert action.method == "counterfactual" and action.blamed_shap is None
        candidates = build_candidates({lever: float(row[lever]) for lever in LEVER_NAMES}, list(LEVER_NAMES))
        predicted = []
        for candidate in candidates:
            edited = row.copy()
            for change in candidate.changes:
                edited[change.lever] = change.to_value
            if candidate.tool_change:
                edited["tool_wear"] = 0.0
            predicted.append(baseline.predict_risk(edited))
        assert action.predicted_risk == pytest.approx(predicted[0], abs=1e-6)
        if action.kind == "none":
            assert predicted[0] <= OPTIMIZER_TARGET_RISK or min(predicted[1:]) >= predicted[0]
            continue
        acted += 1
        reaching = [i for i in range(1, len(candidates)) if predicted[i] <= planning_target]
        if reaching:
            assert action.cost == pytest.approx(min(candidates[i].cost for i in reaching))
            assert action.predicted_risk_after is not None and action.predicted_risk_after <= planning_target
        # The chosen action is one of the optimizer's candidates, with its cost.
        matching = [
            c
            for c in candidates
            if c.tool_change == (action.kind == "tool_change")
            and [(x.lever, x.to_value) for x in c.changes] == [(x.lever, x.to_value) for x in action.changes]
        ]
        assert matching and action.cost == pytest.approx(matching[0].cost)
        index = candidates.index(matching[0])
        assert action.predicted_risk_after == pytest.approx(predicted[index], abs=1e-6)
    assert acted >= 5


def test_counterfactual_baseline_does_nothing_when_predicted_risk_is_low(models: TrainedModels) -> None:
    """At a calm state the XGBoost risk is below the target, so B3 recommends no action."""
    data = models.training_data
    calm = data[(data["machine_running"] == 1) & (data["tool_wear"] < data["tool_wear"].quantile(0.2))]
    row = calm.iloc[len(calm) // 2]
    action = models.baseline.recommend_counterfactual(row)
    assert action.predicted_risk <= OPTIMIZER_TARGET_RISK
    assert action.kind == "none" and action.cost == 0.0 and action.description.startswith("No action")


def test_recommend_all_returns_every_baseline(models: TrainedModels) -> None:
    """recommend_all gives B1, B2 and B3 for one state, sharing one set of SHAP values."""
    row = _rows_before_failures(models, 1)[0]
    actions = models.baseline.recommend_all(row, 20.0)
    assert set(actions) == set(BASELINE_METHOD_LABELS)
    assert {action.method for action in actions.values()} == set(BASELINE_METHOD_LABELS)
    assert actions["naive_shap"].shap_values == actions["actionable_shap"].shap_values
    assert actions["naive_shap"] == models.baseline.recommend(row, 20.0)


@pytest.mark.parametrize(
    ("fault", "cause", "effect", "kinds"),
    [
        ("coolant_filter_clog", "coolant_flow", "coolant_temp", {"weakened", "removed"}),
        ("lube_leak", "lube_rate", "bearing_temp", {"weakened", "removed"}),
        ("bearing_damage", "motor_current", "bearing_temp", {"strengthened"}),
    ],
)
def test_drift_detects_each_fault(models: TrainedModels, fault: str, cause: str, effect: str, kinds: set[str]) -> None:
    """Each fault raises an alarm quickly and re-discovery finds the intended structural change."""
    result = evaluate_fault(models.engine, fault, seed=11)
    print({key: result.get(key) for key in ("detection_delay_steps", "rewire_delay_steps", "status", "explanation")})
    assert result["false_alarms_before_fault"] == 0
    assert result["detection_delay_steps"] is not None and result["detection_delay_steps"] <= 50
    assert result["status"] == "confirmed"
    changes = {(c["cause"], c["effect"], c["change"]) for c in result["edge_changes"]}
    assert any((cause, effect, kind) in changes for kind in kinds)


def test_no_wrong_structural_change_in_a_long_normal_run(models: TrainedModels) -> None:
    """20,000 fault-free steps: alarms are rare and none is confirmed as a structural change."""
    result = count_false_alarms(models.engine, n_steps=20_000, seed=12)
    print(result)
    assert result["wrongly_confirmed_changes"] == 0
    assert result["suspected_alarms"] <= 3


def test_tool_change_lowers_predicted_risk_when_worn(models: TrainedModels) -> None:
    """A "reset" intervention on tool_wear (tool change) lowers risk when the tool is worn."""
    data = models.training_data
    assert models.engine.model is not None
    worn = data[(data["tool_wear"] > 120) & (data["machine_running"] == 1)].index[10]
    history = pd.DataFrame(data.loc[worn - 3 : worn, models.engine.model.variables])
    result = models.engine.what_if([Intervention(variable="tool_wear", change_type="reset", value=0.0)], history)
    assert result.risk_change is not None and result.risk_change < 0
    assert result.forecasts["tool_wear"].median[0] == pytest.approx(0.0)
    assert result.forecasts["tool_wear"].median[-1] > 0.0  # wear accumulates again after the reset
