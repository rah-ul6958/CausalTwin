"""Request and response models of the API.

Every response that contains a computed result carries a `provenance` block (method, data
window, parameters, computation time, assumptions), which the UI shows in its
"How was this computed?" drawers.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from causaltwin.config import (
    WHATIF_MAX_HORIZON,
    WHATIF_MAX_INTERVENTIONS,
    WHATIF_MAX_NOISE_SCALE,
    WHATIF_MAX_ROLLOUTS,
    WHATIF_MIN_ROLLOUTS,
)
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
    data_source: str = "simulator"
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
    """Body of POST /whatif (limits keep one request from exhausting the server)."""

    interventions: list[Intervention] = Field(max_length=WHATIF_MAX_INTERVENTIONS)
    horizon: int | None = Field(default=None, ge=1, le=WHATIF_MAX_HORIZON)
    n_rollouts: int | None = Field(default=None, ge=WHATIF_MIN_ROLLOUTS, le=WHATIF_MAX_ROLLOUTS)
    noise_scale: dict[str, Annotated[float, Field(ge=0, le=WHATIF_MAX_NOISE_SCALE, allow_inf_nan=False)]] | None = (
        Field(default=None, max_length=20)
    )


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

    fault: str = Field(max_length=64)


class ScenarioRequest(BaseModel):
    """Body of POST /scenario."""

    scenario: str = Field(max_length=64)


class SimControlRequest(BaseModel):
    """Body of POST /sim/control."""

    action: Literal["pause", "resume", "speed", "step", "reset", "release"]
    value: float | None = Field(default=None, allow_inf_nan=False)


class SessionStatus(BaseModel):
    """Whether the viewer has a private live simulation or shares one, and why."""

    private: bool
    note: str  # one or two sentences for the viewer
    private_sessions: int  # private simulations on the server now
    max_sessions: int  # the server's limit (0: private simulations are off)


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
    data_source: dict[str, Any] = Field(default_factory=dict)  # kind, description, has_simulator, simulated
    simulated: bool = True
    session: SessionStatus | None = None


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
