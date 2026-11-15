"""Pydantic models produced by the intelligence layer (and served by the API)."""

from typing import Literal

from pydantic import BaseModel, Field

from causaltwin.engine.schemas import RiskEstimate


class LeverChange(BaseModel):
    """One lever moved from its current value to a new value."""

    lever: str
    from_value: float
    to_value: float
    change_pct: float


class CandidateResult(BaseModel):
    """One evaluated option of the optimizer's search."""

    rank: int
    label: str
    changes: list[LeverChange]
    tool_change: bool
    risk: float
    risk_lower: float
    risk_upper: float
    risk_reduction: float
    cost: float
    meets_target: bool


class IneffectiveLever(BaseModel):
    """A lever or variable whose change barely moves the predicted risk."""

    variable: str
    best_risk_reduction: float
    reason: str


class RecommendationConfidence(BaseModel):
    """Confidence of a recommendation, with each component shown separately (all 0..1)."""

    path_edge_confidence: float | None  # lowest bootstrap confidence of the edges on the causal path
    interval_score: float | None  # 1 = tight predicted risk interval, 0 = interval as wide as the risk
    validation_score: float | None  # held-out accuracy of the equations on the path
    combined: float | None  # geometric mean of the three components
    explanation: str


class Recommendation(BaseModel):
    """The optimizer's answer."""

    action_type: Literal["levers", "tool_change", "none"]
    changes: list[LeverChange]
    tool_change: bool
    risk_before: RiskEstimate
    risk_after: RiskEstimate
    target_risk: float
    meets_target: bool
    cost: float
    side_effects: list[str]
    time_to_effect_steps: int | None
    time_to_effect_seconds: float | None
    causal_paths: list[str]
    confidence: RecommendationConfidence
    explanation: str
    candidates: list[CandidateResult]
    ineffective: list[IneffectiveLever]
    n_candidates: int
    search_ms: float


class BaselineAction(BaseModel):
    """What the standard-ML baseline would do, after mapping its top feature to an action."""

    blamed_feature: str
    blamed_shap: float
    kind: Literal["lever", "damp_vibration", "tool_change", "none"]
    lever: str | None = None
    from_value: float | None = None
    to_value: float | None = None
    change_pct: float | None = None
    cost: float
    description: str
    shap_values: dict[str, float]
    predicted_risk: float  # the baseline model's own failure probability for the current state


class EdgeChange(BaseModel):
    """One difference between the old and the newly discovered graph."""

    cause: str
    effect: str
    change: Literal["added", "removed", "weakened", "strengthened"]
    old_coefficient: float | None
    new_coefficient: float | None
    new_interval: tuple[float, float] | None
    explanation: str


class DriftAlert(BaseModel):
    """A detected structural change and what it means."""

    detected_step: int
    change_point_step: int
    equation: str
    cusum_value: float
    rediscovery_step: int | None = None
    window_steps: int | None = None
    edge_changes: list[EdgeChange] = Field(default_factory=list)
    explanation: list[str] = Field(default_factory=list)
    invalidated_recommendations: list[str] = Field(default_factory=list)
    status: Literal["suspected", "confirmed", "no_structural_change"]


class BenchmarkMethodResult(BaseModel):
    """One method's action in one benchmark situation and its true outcome."""

    action: str
    blamed: str
    cost: float
    true_risk_after: float
    risk_reduction: float
    failure_rate_after: float
    success: bool
    # CausalTwin only: what the twin predicted for its own action (calibration check).
    predicted_risk_after: float | None = None
    predicted_risk_after_interval: tuple[float, float] | None = None


class BenchmarkCase(BaseModel):
    """One high-risk benchmark situation."""

    case_id: int
    scenario: str
    seed: int
    step: int
    true_risk_no_action: float
    failure_rate_no_action: float
    # The twin's own estimate of the no-action risk and its 90% interval (calibration check).
    twin_risk_no_action: float | None = None
    twin_risk_no_action_interval: tuple[float, float] | None = None
    causaltwin: BenchmarkMethodResult
    baseline: BenchmarkMethodResult
    winner: Literal["causaltwin", "baseline", "tie"]
