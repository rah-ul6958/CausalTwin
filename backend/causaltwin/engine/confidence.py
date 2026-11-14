"""Edge confidence by moving-block bootstrap.

Each resample glues together random blocks of consecutive steps (default 50), so short
range time dependence inside a block is preserved. The first row of every block is
masked, so no lag window crosses a join. Discovery is rerun on every resample, and an
edge's confidence is the fraction of resamples in which it appears again.

The lagged graph is rediscovered with the exact NumPy equivalent of the tigramite test,
and the failure model with a NumPy maximum-likelihood fit equal to statsmodels (both
verified in the tests). That keeps 20 resamples within a few seconds without worker
processes, whose start-up cost on Windows would exceed the computation itself.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from causaltwin.config import BOOTSTRAP_THREADS
from causaltwin.engine.discovery import (
    contemporaneous_discovery,
    failure_screen,
    fast_mci,
    significant_lagged_edges,
)
from causaltwin.engine.preprocessing import PreparedData
from causaltwin.engine.schemas import CausalEdge, CausalGraph, EdgeConfidence
from causaltwin.utils.timing import timed_step

HIGH_CONFIDENCE = 0.9


@dataclass
class BootstrapResult:
    """Bootstrap frequencies for every pair seen at least once, and per-edge breakdowns."""

    n_resamples: int
    pair_frequencies: dict[tuple[str, str], float]
    confidences: list[EdgeConfidence]
    elapsed_seconds: float


def block_resample(prepared: PreparedData, block_length: int, rng: np.random.Generator) -> PreparedData:
    """One moving-block bootstrap resample of the same length as the input."""
    n_rows = prepared.n_rows
    block_length = min(block_length, n_rows)
    n_blocks = int(np.ceil(n_rows / block_length))
    starts = rng.integers(0, n_rows - block_length + 1, size=n_blocks)
    index = (starts[:, None] + np.arange(block_length)).ravel()[:n_rows]
    row_mask = prepared.row_mask[index].copy()
    if block_length > 1:  # time series: every lag window that crosses a join contains this row
        row_mask[::block_length] = True
    return PreparedData(
        variables=prepared.variables,
        roles=prepared.roles,
        units=prepared.units,
        exogenous=prepared.exogenous,
        outcome=prepared.outcome,
        values=prepared.values[index],
        row_mask=row_mask,
        outcome_values=None if prepared.outcome_values is None else prepared.outcome_values[index],
        time_structure=prepared.time_structure,
    )


def _failure_resample_causes(
    failure_history: PreparedData, block_length: int, seed: int, lags: list[int], alpha: float, min_events: int
) -> set[str]:
    """Significant failure causes on one resample (runs in a worker process)."""
    resample = block_resample(failure_history, block_length, np.random.default_rng(seed))
    return failure_screen(resample, lags, alpha, min_events, solver="numpy").significant_variables()


def _explanation(frequency: float, n_resamples: int, edge: CausalEdge) -> str:
    """Plain-English sentence explaining an edge's confidence."""
    found = int(round(frequency * n_resamples))
    p_text = "p < 0.001" if edge.p_value is not None and edge.p_value < 0.001 else f"p = {edge.p_value:.3f}"
    measure = "standardized logistic coefficient" if edge.method == "logistic" else "partial correlation"
    if frequency >= HIGH_CONFIDENCE:
        level = "High confidence: the edge comes back almost every time the data is resampled."
    elif frequency >= 0.6:
        level = "Moderate confidence: the edge usually comes back when the data is resampled."
    else:
        level = "Uncertain: the edge often disappears when the data is resampled, so treat it with caution."
    return (
        f"Found in {found} of {n_resamples} bootstrap resamples ({frequency:.0%}); "
        f"{measure} {edge.strength:+.2f}, {p_text}. {level}"
    )


def bootstrap_confidence(
    window: PreparedData,
    failure_history: PreparedData,
    graph: CausalGraph,
    tau_max: int,
    alpha: float,
    min_failure_events: int,
    n_resamples: int,
    block_length: int,
    min_confidence: float,
    seed: int,
) -> BootstrapResult:
    """Rerun discovery on block-bootstrap resamples and count how often each edge returns."""
    with timed_step("bootstrap", f"Bootstrapped edge confidence ({n_resamples} resamples)") as timer:
        start_time = time.perf_counter()
        rng = np.random.default_rng(seed)
        counts: dict[tuple[str, str], int] = {}
        for _ in range(n_resamples):
            if window.time_structure:
                resample = block_resample(window, block_length, rng)
                resample_edges = significant_lagged_edges(fast_mci(resample, tau_max), alpha)
            else:  # tabular rows are independent: plain row resampling, contemporaneous discovery
                resample = block_resample(window, 1, rng)
                resample_edges = contemporaneous_discovery(resample, alpha)
            for edge in resample_edges:
                key = (edge.cause, edge.effect)
                counts[key] = counts.get(key, 0) + 1
        if window.outcome is not None:
            lags = list(range(1, tau_max + 1)) if window.time_structure else [0]
            if not window.time_structure:
                block_length = 1
            seeds = rng.integers(0, 2**31 - 1, size=n_resamples).tolist()
            # Threads (not processes): NumPy releases the GIL for large array work, and each
            # resample has its own seed, so results are identical to a sequential run.
            with ThreadPoolExecutor(BOOTSTRAP_THREADS) as pool:
                cause_sets = list(
                    pool.map(
                        lambda seed_value: _failure_resample_causes(
                            failure_history, block_length, seed_value, lags, alpha, min_failure_events
                        ),
                        seeds,
                    )
                )
            for causes in cause_sets:
                for cause in causes:
                    key = (cause, window.outcome)
                    counts[key] = counts.get(key, 0) + 1
        frequencies = {key: count / n_resamples for key, count in counts.items()}
        confidences = []
        for edge in graph.edges:
            frequency = frequencies.get((edge.cause, edge.effect), 0.0)
            confidences.append(
                EdgeConfidence(
                    cause=edge.cause,
                    effect=edge.effect,
                    lag=edge.lag,
                    bootstrap_frequency=frequency,
                    n_resamples=n_resamples,
                    p_value=edge.p_value,
                    strength=edge.strength,
                    uncertain=frequency < min_confidence,
                    explanation=_explanation(frequency, n_resamples, edge),
                )
            )
        elapsed = time.perf_counter() - start_time
        timer.details.update({"resamples": n_resamples, "pairs_seen": len(frequencies)})
    return BootstrapResult(n_resamples, frequencies, confidences, elapsed)
