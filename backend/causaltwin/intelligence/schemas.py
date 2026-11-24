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
    # Share of the search rollouts whose risk ends at or below the target (the confidence rule's measure).
    share_below_target: float | None = None


class MinimumEffectiveChange(BaseModel):
    """The smallest single-lever change that reaches the target under the optimizer's selection rule."""

    change: LeverChange
    risk: RiskEstimate  # re-checked with the finalists' independent rollouts
    share_below_target: float
    cost: float
    meets_target: bool  # still qualifies after the re-check


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
    # One computed sentence in the brief's format: the action, its predicted effect, and the lever that helps least.
    headline: str = ""
    selection_rule: str = "margin"
    # Share of the re-check rollouts whose risk after the chosen action is at or below the target.
    share_below_target: float | None = None
    minimum_effective_change: MinimumEffectiveChange | None = None
    least_effective_lever: IneffectiveLever | None = None


BaselineMethod = Literal["naive_shap", "actionable_shap", "counterfactual"]


class BaselineAction(BaseModel):
    """What a standard-ML baseline would do (B1 and B2: map the top SHAP feature to an action; B3: search)."""

    method: BaselineMethod = "naive_shap"
    blamed_feature: str
    blamed_shap: float | None  # None for B3, which searches actions instead of blaming a feature
    kind: Literal["lever", "damp_vibration", "tool_change", "none"]
    lever: str | None = None
    from_value: float | None = None
    to_value: float | None = None
    change_pct: float | None = None
    # Every lever change of the action (one for B1 and B2, one or two for B3).
    changes: list[LeverChange] = Field(default_factory=list)
    cost: float
    description: str
    shap_values: dict[str, float]
    predicted_risk: float  # the baseline model's own failure probability for the current state
    predicted_risk_after: float | None = None  # the baseline model's own probability after its action


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
    # The method chose to do nothing.
    no_action: bool = False
    # True risk after the action exceeds the no-action true risk by more than BENCHMARK_WORSE_MARGIN.
    worse_than_no_action: bool = False
    # The method chose to do nothing while the true no-action risk was above the target.
    false_reassurance: bool = False
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
    # One result per baseline method, keyed by "naive_shap" (B1), "actionable_shap" (B2), "counterfactual" (B3).
    baselines: dict[str, BenchmarkMethodResult]
    # Head to head against the main baseline (B3).
    winner: Literal["causaltwin", "baseline", "tie"]
    # Head to head against every baseline method.
    winner_by_baseline: dict[str, Literal["causaltwin", "baseline", "tie"]] = Field(default_factory=dict)
