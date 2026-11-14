"""Tests for the Phase 2 causal engine (discovery, confidence, model, what-if, root cause, tabular mode)."""

import time
import warnings

import numpy as np
import pandas as pd
import pytest
from statsmodels.discrete.discrete_model import Logit

from causaltwin.config import (
    TAU_MAX,
    VALIDATION_CHUNK_STEPS,
    VALIDATION_SCENARIO_CYCLE,
    VALIDATION_SEED,
    VALIDATION_TRAINING_STEPS,
)
from causaltwin.engine import CausalTwinEngine, EngineSettings, Intervention
from causaltwin.engine.discovery import fast_mci, logistic_newton, tigramite_mci
from causaltwin.engine.preprocessing import prepare_data
from causaltwin.engine.validation import graph_recovery_metrics, true_cross_edges
from causaltwin.ingestion import align_events
from causaltwin.simulator import run_scenario
from causaltwin.simulator.scenarios import run_scenario_cycle

warnings.filterwarnings("ignore")


@pytest.fixture(scope="session")
def mixed_data() -> pd.DataFrame:
    """The validation dataset: one continuous run cycling normal and stressed scenarios."""
    result = run_scenario_cycle(
        VALIDATION_SCENARIO_CYCLE, VALIDATION_CHUNK_STEPS, VALIDATION_TRAINING_STEPS, VALIDATION_SEED
    )
    return align_events(result.sensors, result.events)


@pytest.fixture(scope="session")
def fitted_engine(mixed_data: pd.DataFrame) -> CausalTwinEngine:
    """An engine fitted once on the mixed dataset (worker processes warmed up first)."""
    engine = CausalTwinEngine()
    engine.warm_up()
    engine.logger.clear()
    return engine.fit(mixed_data)


def _durations(engine: CausalTwinEngine) -> dict[str, float]:
    """Latest duration in ms of each logged pipeline step."""
    return {entry.step_name: entry.duration_ms for entry in engine.get_activity_log() if entry.duration_ms is not None}


def test_numpy_mci_matches_tigramite(mixed_data: pd.DataFrame) -> None:
    """The NumPy test used in the bootstrap equals tigramite's MCI test to numerical precision."""
    window = prepare_data(mixed_data).tail(1500)
    reference = tigramite_mci(window, TAU_MAX, n_jobs=1)
    equivalent = fast_mci(window, TAU_MAX)
    tested = ~np.isnan(equivalent.p_matrix)
    tested[:, :, 0] = False
    assert tested.sum() == 5 * 10 * TAU_MAX
    np.testing.assert_allclose(reference.val_matrix[tested], equivalent.val_matrix[tested], atol=1e-7)
    np.testing.assert_allclose(reference.p_matrix[tested], equivalent.p_matrix[tested], atol=1e-6)


def test_numpy_logistic_matches_statsmodels() -> None:
    """The NumPy logistic fit used in the bootstrap equals statsmodels."""
    rng = np.random.default_rng(0)
    design = np.column_stack([np.ones(5000), rng.normal(size=(5000, 3))])
    outcome = (rng.random(5000) < 1 / (1 + np.exp(-(design @ np.array([-3.0, 1.0, -0.5, 0.0]))))).astype(float)
    reference = Logit(outcome, design).fit(disp=0)
    coefficients, covariance = logistic_newton(design, outcome)
    np.testing.assert_allclose(coefficients, reference.params, atol=1e-8)
    np.testing.assert_allclose(covariance, reference.cov_params(), atol=1e-8)


def test_discovery_recovers_true_graph(fitted_engine: CausalTwinEngine) -> None:
    """On normal-plus-stressed data: recall >= 0.8 and precision >= 0.8 for non-self edges."""
    graph = fitted_engine.get_graph()
    for include_failure in (True, False):
        metrics = graph_recovery_metrics(graph.edges, include_failure)
        print(f"include_failure={include_failure}: {metrics}")
        assert metrics["recall"] >= 0.8
        assert metrics["precision"] >= 0.8
    assert graph_recovery_metrics(graph.edges, include_failure=False)["lag_accuracy"] >= 0.9


def test_vibration_is_not_a_cause_but_is_a_symptom(fitted_engine: CausalTwinEngine) -> None:
    """The Correlation Trap: vibration is a symptom, bearing_temp and tool_wear are causes."""
    failure_causes = {edge.cause for edge in fitted_engine.get_graph().parents_of("failure")}
    assert failure_causes == {"bearing_temp", "tool_wear"}
    assert "vibration" in [symptom.variable for symptom in fitted_engine.get_symptoms()]
    correlation_failure_edges = {
        edge.cause for edge in fitted_engine.get_correlation_graph().edges if edge.effect == "failure"
    }
    assert "vibration" in correlation_failure_edges  # the correlation view does link it to failure


def test_raising_coolant_flow_lowers_bearing_temp_and_risk(fitted_engine: CausalTwinEngine) -> None:
    """What-if: more coolant lowers predicted bearing temperature and failure risk."""
    result = fitted_engine.what_if([Intervention(variable="coolant_flow", change_type="set", value=30.0)])
    assert result.forecasts["bearing_temp"].change_at_end < -0.5
    assert result.forecasts["coolant_temp"].change_at_end < -0.5
    assert result.risk_change is not None and result.risk_change < 0
    assert result.risk is not None and 0.0 <= result.risk.lower <= result.risk.probability <= result.risk.upper <= 1.0


def test_vibration_changes_do_not_affect_predicted_risk(fitted_engine: CausalTwinEngine) -> None:
    """What-if: removing vibration noise or forcing vibration leaves predicted risk unchanged."""
    baseline = fitted_engine.what_if([])
    without_noise = fitted_engine.what_if([], noise_scale={"vibration": 0.0})
    forced = fitted_engine.what_if([Intervention(variable="vibration", change_type="percent", value=-50.0)])
    assert baseline.risk is not None and without_noise.risk is not None and forced.risk_change is not None
    assert without_noise.risk.probability == pytest.approx(baseline.risk.probability, abs=1e-12)
    assert forced.risk_change == pytest.approx(0.0, abs=1e-12)
    assert forced.forecasts["vibration"].change_at_end < 0


def test_timings_within_budget(fitted_engine: CausalTwinEngine) -> None:
    """Discovery < 3 s for a 3,000-step window, model fit < 1 s, what-if (200 rollouts) < 200 ms."""
    durations = _durations(fitted_engine)
    print({name: round(value, 1) for name, value in durations.items()})
    assert durations["discovery"] < 3000
    assert durations["fit_model"] < 1000
    start = time.perf_counter()
    result = fitted_engine.what_if([Intervention(variable="lube_rate", change_type="percent", value=10.0)])
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"what-if {elapsed_ms:.1f} ms")
    assert result.n_rollouts == 200 and elapsed_ms < 200


def test_bootstrap_confidence_is_valid_and_favours_true_edges(fitted_engine: CausalTwinEngine) -> None:
    """Confidences lie in [0, 1]; true edges score higher on average than false edges."""
    bootstrap = fitted_engine.bootstrap
    assert bootstrap is not None and bootstrap.n_resamples == EngineSettings().bootstrap_resamples
    assert all(0.0 <= value <= 1.0 for value in bootstrap.pair_frequencies.values())
    truth = set(true_cross_edges(include_failure=True))
    cross_pairs = {pair: value for pair, value in bootstrap.pair_frequencies.items() if pair[0] != pair[1]}
    true_scores = [cross_pairs.get(pair, 0.0) for pair in truth]
    false_scores = [value for pair, value in cross_pairs.items() if pair not in truth]
    print(f"mean true {np.mean(true_scores):.2f}, mean false {np.mean(false_scores) if false_scores else 0:.2f}")
    assert np.mean(true_scores) > (np.mean(false_scores) if false_scores else 0.0)
    for confidence in fitted_engine.get_edge_confidences():
        assert confidence.explanation.startswith("Found in")
        assert confidence.uncertain == (confidence.bootstrap_frequency < EngineSettings().bootstrap_min_confidence)


def test_model_cards_are_readable_and_accurate(fitted_engine: CausalTwinEngine) -> None:
    """Each equation has a readable string and good held-out accuracy."""
    cards = {card.variable: card for card in fitted_engine.get_model_cards()}
    assert set(cards) == {"motor_current", "coolant_temp", "bearing_temp", "vibration", "tool_wear", "failure"}
    assert cards["bearing_temp"].equation.startswith("bearing_temp(t) = ")
    assert "motor_current(t-2)" in cards["bearing_temp"].equation
    for name in ("motor_current", "coolant_temp", "bearing_temp", "vibration", "tool_wear"):
        r_squared = cards[name].r_squared_holdout
        assert r_squared is not None and r_squared > 0.8
    failure_auc = cards["failure"].auc_holdout
    assert cards["failure"].kind == "logistic" and failure_auc is not None and failure_auc > 0.8


def test_root_cause_on_a_high_risk_state(fitted_engine: CausalTwinEngine, mixed_data: pd.DataFrame) -> None:
    """Just before a failure, risk is high, paths end in a direct cause, and vibration is only a symptom."""
    failure_rows = mixed_data.index[mixed_data["failure"] == 1]
    variables = fitted_engine.model.variables if fitted_engine.model else []
    best = None
    for row in failure_rows:
        history = mixed_data.loc[row - 4 : row - 1, variables]
        result = fitted_engine.root_cause(history)
        if best is None or (result.risk and best.risk and result.risk.probability > best.risk.probability):
            best = result
    assert best is not None and best.risk_is_high and best.paths
    direct_causes = {"bearing_temp", "tool_wear"}
    for path in best.paths:
        assert path.nodes[-1].variable in direct_causes
        assert all(node.variable != "vibration" for node in path.nodes)
        assert path.description.endswith("-> failure risk") and 0 < path.share <= 1
    assert sum(path.share for path in best.paths) <= 1.0 + 1e-9
    assert "vibration" in [symptom.variable for symptom in best.symptoms]
    print([path.description for path in best.paths])


def test_engine_reports_too_few_failures() -> None:
    """With almost no failures, the failure model is not fitted and the graph says why."""
    result = run_scenario("normal", n_steps=4000)
    engine = CausalTwinEngine(EngineSettings(discovery_backend="numpy", bootstrap_resamples=4))
    engine.fit(align_events(result.sensors, result.events))
    graph = engine.get_graph()
    assert not graph.parents_of("failure")
    assert any("failure events" in note for note in graph.notes)


def test_unfitted_engine_raises() -> None:
    """Asking for results before fitting fails clearly."""
    with pytest.raises(RuntimeError, match="not fitted"):
        CausalTwinEngine().get_graph()


def test_tabular_mode_on_independent_rows() -> None:
    """Bring Your Own Data without a time column: PCMCIplus at lag 0 with role knowledge."""
    rng = np.random.default_rng(3)
    n_rows = 4000
    setting = rng.normal(size=n_rows)
    environment = rng.normal(size=n_rows)
    pressure = 2.0 * setting + rng.normal(size=n_rows)
    temperature = -1.5 * pressure + 0.8 * environment + rng.normal(size=n_rows)
    unrelated = rng.normal(size=n_rows)
    broke = (rng.random(n_rows) < 1 / (1 + np.exp(-(0.6 * temperature - 4.0)))).astype(int)
    data = pd.DataFrame(
        {
            "setting": setting,
            "environment": environment,
            "pressure": pressure,
            "temperature": temperature,
            "unrelated": unrelated,
            "broke": broke,
        }
    )
    roles = {
        "setting": "control",
        "environment": "environment",
        "pressure": "sensor",
        "temperature": "sensor",
        "unrelated": "sensor",
        "broke": "outcome",
    }
    engine = CausalTwinEngine(EngineSettings(bootstrap_resamples=5))
    engine.fit(data, roles=roles)
    graph = engine.get_graph()
    directed = {(edge.cause, edge.effect) for edge in graph.edges if edge.oriented}
    assert {("setting", "pressure"), ("pressure", "temperature"), ("environment", "temperature")} <= directed
    assert {edge.cause for edge in graph.parents_of("broke")} == {"temperature"}
    assert graph.tau_max == 0
    result = engine.what_if([Intervention(variable="setting", change_type="set", value=2.0)], history=data.tail(1))
    assert result.horizon == 1 and result.forecasts["temperature"].change_at_end < 0
    assert any("Tabular mode" in assumption for assumption in engine.get_assumptions())


def test_graph_recovery_metrics_counts_reversals_once() -> None:
    """SHD counts a reversed edge once, not as one missing plus one extra."""
    from causaltwin.engine.schemas import CausalEdge

    truth = true_cross_edges(include_failure=False)
    edges = [CausalEdge(cause=c, effect=e, lag=lag, strength=1.0, method="test") for (c, e), lag in truth.items()]
    edges[0] = CausalEdge(cause=edges[0].effect, effect=edges[0].cause, lag=1, strength=1.0, method="test")
    metrics = graph_recovery_metrics(edges, include_failure=False)
    assert metrics["structural_hamming_distance"] == 1
    assert metrics["recall"] == pytest.approx((len(truth) - 1) / len(truth))


def test_preprocessing_masks_regime_breaks(mixed_data: pd.DataFrame) -> None:
    """Stopped-machine rows, tool resets and series jumps are masked."""
    prepared = prepare_data(mixed_data)
    stopped = mixed_data["machine_running"].to_numpy() == 0
    resets = (mixed_data["event_tool_change"] == 1).to_numpy()
    assert prepared.row_mask[stopped].all() and prepared.row_mask[resets].all()
    joined = pd.concat([mixed_data.iloc[:100], mixed_data.iloc[500:600]], ignore_index=True)
    assert prepare_data(joined).row_mask[100]
