"""Pydantic data models produced by the causal engine (and later served by the API)."""

from typing import Literal

from pydantic import BaseModel, Field

GraphType = Literal["causal", "correlation"]


class CausalNode(BaseModel):
    """One variable in a graph."""

    name: str
    role: str  # control, environment, sensor, outcome
    unit: str | None = None


class CausalEdge(BaseModel):
    """A directed, lagged link from cause to effect.

    `lag` is the strongest lag; `lags` lists every significant lag. For causal edges,
    `strength` is the partial correlation (PCMCI) or the standardized logistic
    coefficient (failure edges); for correlation edges it is the Pearson correlation.
    """

    cause: str
    effect: str
    lag: int
    lags: list[int] = Field(default_factory=list)
    strength: float
    p_value: float | None = None
    method: str  # pcmci, pcmciplus, logistic, pearson
    is_self_loop: bool = False
    confidence: float | None = None  # bootstrap frequency, 0..1
    uncertain: bool = False  # confidence below the minimum: show dashed
    oriented: bool = True  # False for edges whose direction could not be decided (tabular mode)


class CausalGraph(BaseModel):
    """A graph of variables and edges, causal or correlational."""

    graph_type: GraphType
    nodes: list[CausalNode]
    edges: list[CausalEdge]
    tau_max: int
    window_steps: int
    notes: list[str] = Field(default_factory=list)

    def cross_edges(self) -> list[CausalEdge]:
        """Edges between two different variables (self-lags excluded)."""
        return [edge for edge in self.edges if not edge.is_self_loop]

    def parents_of(self, variable: str) -> list[CausalEdge]:
        """Incoming edges of a variable (including its self-lag edge)."""
        return [edge for edge in self.edges if edge.effect == variable]


class EdgeConfidence(BaseModel):
    """How sure we are about one edge, with the evidence behind it."""

    cause: str
    effect: str
    lag: int
    bootstrap_frequency: float
    n_resamples: int
    p_value: float | None
    strength: float
    uncertain: bool
    explanation: str


class EquationTerm(BaseModel):
    """One term `coefficient * variable(t - lag)` of a fitted equation."""

    variable: str
    lag: int
    coefficient: float


class EquationCard(BaseModel):
    """A fitted structural equation, as shown on a UI model card."""

    variable: str
    kind: Literal["linear", "logistic"]
    terms: list[EquationTerm]
    intercept: float
    equation: str
    r_squared_holdout: float | None = None  # linear equations
    residual_std: float | None = None
    auc_holdout: float | None = None  # logistic equation
    n_train: int
    n_holdout: int
    notes: list[str] = Field(default_factory=list)


class Intervention(BaseModel):
    """A do-intervention used by the what-if engine."""

    variable: str
    change_type: Literal["set", "percent"] = "set"
    value: float  # target value ("set") or percent change ("percent", e.g. -20)
    start_step: int = 0  # steps into the horizon when the intervention starts


class VariableForecast(BaseModel):
    """Forecast of one variable over the horizon, with and without the interventions."""

    variable: str
    median: list[float]
    lower: list[float]
    upper: list[float]
    baseline_median: list[float]
    change_at_end: float  # median paired difference at the last step
    mean_change: float  # median paired difference averaged over the horizon


class RiskEstimate(BaseModel):
    """Probability of at least one failure within the horizon, with an interval."""

    probability: float
    lower: float
    upper: float


class WhatIfResult(BaseModel):
    """Everything the what-if engine returns."""

    horizon: int
    n_rollouts: int
    interventions: list[Intervention]
    forecasts: dict[str, VariableForecast]
    risk: RiskEstimate | None
    baseline_risk: RiskEstimate | None
    risk_change: float | None
    downstream_changes: dict[str, float]  # mean change over the horizon per variable
    computation_ms: float
    assumptions: list[str]


class PathNode(BaseModel):
    """One variable on a root-cause path and the change attributed to this path."""

    variable: str
    change: float
    percent_change: float | None
    unit: str | None


class RootCausePath(BaseModel):
    """A ranked causal path ending in failure risk."""

    nodes: list[PathNode]
    description: str
    contribution: float  # contribution to the failure logit (risk score units)
    share: float  # share of the total risk-increasing contribution
    explanation: str


class Symptom(BaseModel):
    """A variable correlated with failure that has no causal path to it."""

    variable: str
    correlation: float
    explanation: str


class RootCauseResult(BaseModel):
    """Root-cause analysis for the current state."""

    risk: RiskEstimate | None
    risk_is_high: bool
    paths: list[RootCausePath]
    risk_reducing_paths: list[RootCausePath]
    symptoms: list[Symptom]
    computation_ms: float
    notes: list[str] = Field(default_factory=list)
