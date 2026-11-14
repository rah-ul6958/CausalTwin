"""Plain correlation graph, for the UI's "Correlation vs Causation" toggle.

Between continuous variables: lagged Pearson correlation corr(x(t - lag), y(t)) for lags
1..tau_max, keeping the strongest lag of each ordered pair if |r| passes a threshold.
For the failure outcome, which is a rare 0/1 event, each variable is correlated with
"a failure happens within the next N steps" over the failure history.
Correlation edges say nothing about cause and effect; that is the point of the toggle.
"""

import numpy as np

from causaltwin.engine.discovery import build_nodes
from causaltwin.engine.preprocessing import PreparedData
from causaltwin.engine.schemas import CausalEdge, CausalGraph
from causaltwin.utils.timing import timed_step


def _pearson(first: np.ndarray, second: np.ndarray) -> float:
    """Pearson correlation, 0 when either side is constant."""
    first = first - first.mean()
    second = second - second.mean()
    denominator = np.sqrt((first @ first) * (second @ second))
    return float(first @ second / denominator) if denominator > 0 else 0.0


def failure_soon(outcome_values: np.ndarray, horizon: int) -> np.ndarray:
    """1 where at least one failure happens in the next `horizon` rows (current row excluded)."""
    cumulative = np.concatenate([[0.0], np.cumsum(outcome_values)])
    rows = np.arange(outcome_values.size)
    upper = np.minimum(rows + 1 + horizon, outcome_values.size)
    return (cumulative[upper] - cumulative[rows + 1] > 0).astype(float)


def failure_correlations(prepared: PreparedData, horizon: int) -> dict[str, float]:
    """Correlation of each variable with "failure within the next `horizon` steps" (running rows)."""
    if prepared.outcome_values is None or prepared.outcome_values.sum() == 0:
        return {}
    target = failure_soon(prepared.outcome_values, horizon)
    usable = ~prepared.row_mask
    usable[-horizon:] = False  # the future is not fully observed at the end
    return {
        name: _pearson(prepared.values[usable, prepared.index_of(name)], target[usable]) for name in prepared.variables
    }


def correlation_graph(
    window: PreparedData,
    failure_history: PreparedData,
    tau_max: int,
    threshold: float,
    failure_threshold: float,
    failure_horizon: int,
) -> CausalGraph:
    """Build the correlation graph in the same shape as the causal graph."""
    with timed_step("correlation_graph", "Computed correlation graph") as timer:
        edges: list[CausalEdge] = []
        n_rows = window.n_rows
        for cause in window.variables:
            for effect in window.variables:
                if cause == effect:
                    continue
                best_lag, best_r = 0, 0.0
                for lag in range(1, tau_max + 1):
                    rows = np.arange(lag, n_rows)
                    usable = ~window.row_mask[rows] & ~window.row_mask[rows - lag]
                    rows = rows[usable]
                    r = _pearson(
                        window.values[rows - lag, window.index_of(cause)], window.values[rows, window.index_of(effect)]
                    )
                    if abs(r) > abs(best_r):
                        best_lag, best_r = lag, r
                if abs(best_r) >= threshold:
                    edges.append(
                        CausalEdge(
                            cause=cause, effect=effect, lag=best_lag, lags=[best_lag], strength=best_r, method="pearson"
                        )
                    )
        if window.outcome is not None:
            for name, r in failure_correlations(failure_history, failure_horizon).items():
                if abs(r) >= failure_threshold:
                    edges.append(
                        CausalEdge(cause=name, effect=window.outcome, lag=1, lags=[1], strength=r, method="pearson")
                    )
        timer.details.update({"edges": len(edges)})
    return CausalGraph(
        graph_type="correlation",
        nodes=build_nodes(window),
        edges=edges,
        tau_max=tau_max,
        window_steps=window.n_rows,
        notes=[
            f"Lagged Pearson correlation, |r| >= {threshold} between sensors; failure edges use "
            f"correlation with failure within the next {failure_horizon} steps, |r| >= {failure_threshold}.",
            "Correlation is not causation: these edges include common-cause and proxy relationships.",
        ],
    )
