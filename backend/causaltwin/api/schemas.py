"""Request and response models of the API.

Every response that contains a computed result carries a `provenance` block (method, data
window, parameters, computation time, assumptions), which the UI shows in its
"How was this computed?" drawers.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from causaltwin.engine.schemas import (
    CausalEdge,
    CausalGraph,
    EdgeConfidence,
    EquationCard,
    Intervention,
    RootCauseResult,
    WhatIfResult,
)
from causaltwin.intelligence.schemas import BaselineAction, Recommendation


class Provenance(BaseModel):
    """How a result was computed."""

    method: str
    data_window: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    computation_ms: float | None = None
    assumptions: list[str] = Field(default_factory=list)
    data_source: Literal["simulated", "uploaded"] = "simulated"
    ground_truth_available: bool = True
    ground_truth_used: bool = False


class HealthResponse(BaseModel):
    """Service status."""

    status: str
    engine_fitted: bool
    live_running: bool
    step: int
    scenario: str
    simulated: bool = True


class GraphResponse(BaseModel):
    """A causal or correlation graph."""

    graph: CausalGraph
    provenance: Provenance


class EdgeEvidenceResponse(BaseModel):
    """Evidence for one edge, with paired data points for a scatter plot."""

    edge: CausalEdge
    confidence: EdgeConfidence | None
    points: list[tuple[float, float]]
    x_label: str
    y_label: str
    provenance: Provenance


class WhatIfRequest(BaseModel):
    """Body of POST /whatif."""

    interventions: list[Intervention]
    horizon: int | None = None
    n_rollouts: int | None = None
    noise_scale: dict[str, float] | None = None


class WhatIfResponse(BaseModel):
    """What-if result."""

    result: WhatIfResult
    provenance: Provenance


class RootCauseResponse(BaseModel):
    """Root-cause result."""

    result: RootCauseResult
    provenance: Provenance


class RecommendResponse(BaseModel):
    """Recommendation, its full candidate table, and the baseline's action for contrast."""

    recommendation: Recommendation
    baseline_action: BaselineAction | None
    provenance: Provenance


class FaultRequest(BaseModel):
    """Body of POST /inject-fault and /clear-fault."""

    fault: str


class ScenarioRequest(BaseModel):
    """Body of POST /scenario."""

    scenario: str


class SimControlRequest(BaseModel):
    """Body of POST /sim/control."""

    action: Literal["pause", "resume", "speed", "step", "reset", "release"]
    value: float | None = None


class SimStatus(BaseModel):
    """Live simulator status."""

    running: bool
    run_id: int  # increases on every reset of the live simulation
    step: int
    steps_per_second: float
    scenario: str
    active_faults: list[str]
    interventions: dict[str, float]
    drift_state: str
    simulated: bool = True


class ModelCardsResponse(BaseModel):
    """Fitted equations."""

    cards: list[EquationCard]
    provenance: Provenance


class ActionResponse(BaseModel):
    """Result of a state-changing action."""

    ok: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    status: SimStatus


class ByodResponse(BaseModel):
    """Result of a Bring Your Own Data upload."""

    validation_report: dict[str, Any]
    quality: list[dict[str, Any]]
    graph: CausalGraph | None
    model_cards: list[EquationCard]
    mode: Literal["time_series", "tabular"]
    notes: list[str]
    provenance: Provenance
