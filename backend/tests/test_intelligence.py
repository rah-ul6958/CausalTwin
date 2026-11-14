"""Tests for the Phase 3 intelligence layer: optimizer, baseline, benchmark, drift detection."""

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from causaltwin.config import (
    DEFAULT_CONFIG,
    LEVER_COSTS,
    OPTIMIZER_TARGET_RISK,
    WHATIF_INTERVAL_PERCENTILES,
)
from causaltwin.engine import Intervention
from causaltwin.engine.whatif import batch_failure_risk, run_what_if
from causaltwin.intelligence.benchmark import run_benchmark
from causaltwin.intelligence.drift import count_false_alarms, evaluate_fault
from causaltwin.intelligence.optimizer import recommend
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
    for case in saved["cases"]:
        assert 0.0 <= case["causaltwin"]["true_risk_after"] <= 1.0
        assert case["baseline"]["blamed"] and case["causaltwin"]["blamed"]
    assert set(saved["summary"]) >= {"causaltwin", "baseline", "wins", "cases_where_causaltwin_is_worse"}
    assert saved["baseline_accuracy"]["auc"] > 0.8


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
