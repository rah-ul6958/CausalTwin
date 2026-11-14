"""CausalTwinEngine: the single entry point the API uses.

fit(dataframe) runs discovery, bootstrap confidence, model fitting and the correlation
graph. After fitting: get_graph(), get_correlation_graph(), get_edge_confidences(),
what_if(...), root_cause(...), get_model_cards(), get_symptoms(), get_activity_log().

Works on simulated data and on uploaded data (Bring Your Own Data). If the data has no
time structure (no step or timestamp column), it runs in tabular mode: PCMCIplus at
tau_max = 0, no lags, one-step what-if propagation. See TABULAR_ASSUMPTIONS.
"""

import numpy as np
import pandas as pd
from pydantic import BaseModel

from causaltwin.config import (
    BOOTSTRAP_BLOCK_LENGTH,
    BOOTSTRAP_MIN_CONFIDENCE,
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    CORRELATION_THRESHOLD,
    DISCOVERY_ALPHA,
    DISCOVERY_N_JOBS,
    DISCOVERY_WINDOW_STEPS,
    FAILURE_CORRELATION_HORIZON,
    FAILURE_CORRELATION_THRESHOLD,
    FAILURE_HISTORY_MAX_STEPS,
    FAILURE_MIN_EVENTS,
    HOLDOUT_FRACTION,
    INTEGRATOR_THRESHOLD,
    RESIDUAL_SAMPLE_LIMIT,
    RIDGE_PENALTY,
    ROOT_CAUSE_MAX_DEPTH,
    ROOT_CAUSE_MAX_PATHS,
    ROOT_CAUSE_RISK_THRESHOLD,
    SYMPTOM_THRESHOLD,
    TAU_MAX,
    WHATIF_HORIZON,
    WHATIF_INTERVAL_PERCENTILES,
    WHATIF_ROLLOUTS,
    WHATIF_SEED,
)
from causaltwin.engine.causal_model import CausalModel, fit_causal_model, model_cards
from causaltwin.engine.confidence import BootstrapResult, bootstrap_confidence
from causaltwin.engine.correlation import correlation_graph, failure_correlations
from causaltwin.engine.discovery import FailureScreen, discover, warm_up_workers
from causaltwin.engine.preprocessing import PreparedData, prepare_data
from causaltwin.engine.root_cause import trace_root_causes
from causaltwin.engine.schemas import (
    CausalGraph,
    EdgeConfidence,
    EquationCard,
    Intervention,
    RootCauseResult,
    Symptom,
    WhatIfResult,
)
from causaltwin.engine.whatif import run_what_if
from causaltwin.utils.activity_log import ActivityLogEntry, ActivityLogger, get_activity_logger
from causaltwin.utils.timing import timed_step

TIME_SERIES_ASSUMPTIONS = [
    "Causal sufficiency: no hidden variable drives two measured variables (PCMCI assumption).",
    "Effects are linear and act with a lag of 1 to tau_max steps; nothing causes the past.",
    "Control levers and environment variables have no causes inside the system.",
    "The causal structure is stable within the analysis window (drift is checked separately).",
    "Rows where the machine is stopped or a tool is reset are excluded as regime breaks.",
]
TABULAR_ASSUMPTIONS = [
    "Tabular mode: rows are treated as independent samples with no time order.",
    "Causal sufficiency, linear effects, and an acyclic structure among same-row variables.",
    "Control levers and environment variables have no causes inside the system.",
    "Some edge directions cannot be decided from data alone; such edges are shown as undirected "
    "and are not used for what-if simulation.",
    "What-if results are one-step propagations, not forecasts over time.",
]


class EngineSettings(BaseModel):
    """All tunable engine settings (defaults from config.py)."""

    window_steps: int = DISCOVERY_WINDOW_STEPS
    tau_max: int = TAU_MAX
    alpha: float = DISCOVERY_ALPHA
    discovery_n_jobs: int = DISCOVERY_N_JOBS
    discovery_backend: str = "tigramite"
    failure_history_steps: int = FAILURE_HISTORY_MAX_STEPS
    failure_min_events: int = FAILURE_MIN_EVENTS
    correlation_threshold: float = CORRELATION_THRESHOLD
    failure_correlation_threshold: float = FAILURE_CORRELATION_THRESHOLD
    failure_correlation_horizon: int = FAILURE_CORRELATION_HORIZON
    bootstrap_resamples: int = BOOTSTRAP_RESAMPLES
    bootstrap_block_length: int = BOOTSTRAP_BLOCK_LENGTH
    bootstrap_min_confidence: float = BOOTSTRAP_MIN_CONFIDENCE
    bootstrap_seed: int = BOOTSTRAP_SEED
    ridge_penalty: float = RIDGE_PENALTY
    holdout_fraction: float = HOLDOUT_FRACTION
    residual_sample_limit: int = RESIDUAL_SAMPLE_LIMIT
    whatif_horizon: int = WHATIF_HORIZON
    whatif_rollouts: int = WHATIF_ROLLOUTS
    whatif_seed: int = WHATIF_SEED
    risk_threshold: float = ROOT_CAUSE_RISK_THRESHOLD
    max_paths: int = ROOT_CAUSE_MAX_PATHS
    max_path_depth: int = ROOT_CAUSE_MAX_DEPTH
    integrator_threshold: float = INTEGRATOR_THRESHOLD
    symptom_threshold: float = SYMPTOM_THRESHOLD


class CausalTwinEngine:
    """Discover, fit, simulate and explain, on one dataset."""

    def __init__(self, settings: EngineSettings | None = None, logger: ActivityLogger | None = None) -> None:
        """Create an unfitted engine."""
        self.settings = settings or EngineSettings()
        self.logger = logger or get_activity_logger()
        self.window: PreparedData | None = None
        self.failure_history: PreparedData | None = None
        self.raw_graph: CausalGraph | None = None  # before bootstrap filtering of failure edges
        self.graph: CausalGraph | None = None
        self.correlation: CausalGraph | None = None
        self.failure_screen: FailureScreen | None = None
        self.bootstrap: BootstrapResult | None = None
        self.model: CausalModel | None = None
        self.failure_correlations: dict[str, float] = {}
        self.recent_history: pd.DataFrame | None = None

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------
    def warm_up(self) -> None:
        """Start discovery worker processes ahead of time (a long-lived server calls this once)."""
        if self.settings.discovery_backend == "tigramite":
            with timed_step("warm_up", "Started discovery worker processes", logger=self.logger):
                warm_up_workers(self.settings.discovery_n_jobs)

    def fit(
        self,
        dataframe: pd.DataFrame,
        roles: dict[str, str] | None = None,
        time_structure: bool | None = None,
    ) -> "CausalTwinEngine":
        """Run the whole pipeline on an aligned dataframe (see ingestion.align_events)."""
        settings = self.settings
        with timed_step("fit_engine", "Fitted CausalTwin engine", logger=self.logger) as timer:
            full = prepare_data(dataframe, roles, time_structure)
            window = full.tail(settings.window_steps)
            failure_history = full.tail(settings.failure_history_steps)
            raw_graph, screen = discover(
                window,
                failure_history,
                settings.tau_max,
                settings.alpha,
                settings.failure_min_events,
                settings.discovery_n_jobs,
                settings.discovery_backend,
            )
            bootstrap = bootstrap_confidence(
                window,
                failure_history,
                raw_graph,
                settings.tau_max,
                settings.alpha,
                settings.failure_min_events,
                settings.bootstrap_resamples,
                settings.bootstrap_block_length,
                settings.bootstrap_min_confidence,
                settings.bootstrap_seed,
            )
            graph = self._apply_confidence(raw_graph, bootstrap, window.outcome)
            model = fit_causal_model(
                window,
                failure_history,
                graph,
                settings.ridge_penalty,
                settings.holdout_fraction,
                settings.residual_sample_limit,
                settings.bootstrap_seed,
            )
            self.correlation = correlation_graph(
                window,
                failure_history,
                settings.tau_max,
                settings.correlation_threshold,
                settings.failure_correlation_threshold,
                settings.failure_correlation_horizon,
            )
            self.failure_correlations = failure_correlations(failure_history, settings.failure_correlation_horizon)
            self.window, self.failure_history = window, failure_history
            self.raw_graph, self.graph, self.failure_screen = raw_graph, graph, screen
            self.bootstrap, self.model = bootstrap, model
            self.recent_history = dataframe[window.variables].tail(max(model.max_lag, 1))
            timer.details.update({"rows": full.n_rows, "edges": len(graph.edges)})
        return self

    def _apply_confidence(self, graph: CausalGraph, bootstrap: BootstrapResult, outcome: str | None) -> CausalGraph:
        """Attach bootstrap confidence to every edge.

        Failure edges are kept only if significant AND stable (frequency >= minimum);
        other edges are kept and marked uncertain when below the minimum.
        """
        minimum = self.settings.bootstrap_min_confidence
        edges = []
        dropped = []
        for edge in graph.edges:
            frequency = bootstrap.pair_frequencies.get((edge.cause, edge.effect), 0.0)
            updated = edge.model_copy(update={"confidence": frequency, "uncertain": frequency < minimum})
            if outcome is not None and edge.effect == outcome and frequency < minimum:
                dropped.append(f"{edge.cause} ({frequency:.0%})")
                continue
            edges.append(updated)
        notes = list(graph.notes)
        if dropped:
            notes.append(f"Failure causes dropped as unstable under bootstrap: {', '.join(dropped)}.")
        return graph.model_copy(update={"edges": edges, "notes": notes})

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------
    def _require_fitted(self) -> CausalModel:
        """Return the fitted model or raise a clear error."""
        if self.model is None:
            raise RuntimeError("The engine is not fitted yet; call fit(dataframe) first.")
        return self.model

    def get_graph(self) -> CausalGraph:
        """The causal graph with bootstrap confidence on every edge."""
        self._require_fitted()
        assert self.graph is not None
        return self.graph

    def get_correlation_graph(self) -> CausalGraph:
        """The plain correlation graph in the same shape."""
        self._require_fitted()
        assert self.correlation is not None
        return self.correlation

    def get_edge_confidences(self) -> list[EdgeConfidence]:
        """Confidence breakdown for every discovered edge (before filtering)."""
        self._require_fitted()
        assert self.bootstrap is not None
        return self.bootstrap.confidences

    def get_model_cards(self) -> list[EquationCard]:
        """Fitted equations as UI model cards."""
        return model_cards(self._require_fitted())

    def get_assumptions(self) -> list[str]:
        """Assumptions behind the current results, for the "How was this computed?" drawer."""
        model = self._require_fitted()
        return TIME_SERIES_ASSUMPTIONS if model.time_structure else TABULAR_ASSUMPTIONS

    def get_activity_log(self) -> list[ActivityLogEntry]:
        """Activity log entries (durations of every pipeline step)."""
        return self.logger.entries()

    def _history_array(self, history: pd.DataFrame | None) -> np.ndarray:
        """Recent rows of the model variables, oldest first."""
        model = self._require_fitted()
        source = history if history is not None else self.recent_history
        assert source is not None
        return source[model.variables].to_numpy(dtype=float)

    def what_if(
        self,
        interventions: list[Intervention],
        history: pd.DataFrame | None = None,
        horizon: int | None = None,
        n_rollouts: int | None = None,
        seed: int | None = None,
        noise_scale: dict[str, float] | None = None,
    ) -> WhatIfResult:
        """Simulate interventions from the given (or the latest fitted) state."""
        model = self._require_fitted()
        settings = self.settings
        return run_what_if(
            model,
            self._history_array(history),
            interventions,
            horizon or settings.whatif_horizon,
            n_rollouts or settings.whatif_rollouts,
            settings.whatif_seed if seed is None else seed,
            WHATIF_INTERVAL_PERCENTILES,
            noise_scale,
        )

    def get_symptoms(self) -> list[Symptom]:
        """Variables correlated with failure but with no causal path to it."""
        return self.root_cause().symptoms

    def root_cause(self, history: pd.DataFrame | None = None) -> RootCauseResult:
        """Rank causal paths that explain the current failure risk, and list symptoms."""
        model = self._require_fitted()
        assert self.graph is not None
        settings = self.settings
        history_values = self._history_array(history)
        risk = self.what_if([], history=history).baseline_risk if model.failure is not None else None
        return trace_root_causes(
            model,
            self.graph,
            history_values[-1],
            risk,
            self.failure_correlations,
            settings.risk_threshold,
            settings.max_paths,
            settings.max_path_depth,
            settings.integrator_threshold,
            settings.symptom_threshold,
        )
