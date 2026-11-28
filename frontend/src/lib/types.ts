/**
 * TypeScript mirrors of the backend's Pydantic response models (backend/causaltwin/api/schemas.py,
 * engine/schemas.py, intelligence/schemas.py). Every number shown in the UI comes from these.
 */

export type GraphType = "causal" | "correlation";

export interface CausalNode {
  name: string;
  role: string;
  unit: string | null;
}

export interface CausalEdge {
  cause: string;
  effect: string;
  lag: number;
  lags: number[];
  strength: number;
  p_value: number | null;
  method: string;
  is_self_loop: boolean;
  confidence: number | null;
  uncertain: boolean;
  oriented: boolean;
}

export interface CausalGraph {
  graph_type: GraphType;
  nodes: CausalNode[];
  edges: CausalEdge[];
  tau_max: number;
  window_steps: number;
  notes: string[];
}

export interface Provenance {
  method: string;
  data_window: string;
  parameters: Record<string, unknown>;
  computation_ms: number | null;
  assumptions: string[];
  data_source: "simulated" | "uploaded";
  ground_truth_available: boolean;
  ground_truth_used: boolean;
}

export interface GraphResponse {
  graph: CausalGraph;
  provenance: Provenance;
}

export interface EdgeConfidence {
  cause: string;
  effect: string;
  lag: number;
  bootstrap_frequency: number;
  n_resamples: number;
  p_value: number | null;
  strength: number;
  uncertain: boolean;
  explanation: string;
}

export interface EdgeEvidence {
  edge: CausalEdge;
  confidence: EdgeConfidence | null;
  points: [number, number][];
  x_label: string;
  y_label: string;
  provenance: Provenance;
}

export interface EquationTerm {
  variable: string;
  lag: number;
  coefficient: number;
}

export interface EquationCard {
  variable: string;
  kind: "linear" | "logistic";
  terms: EquationTerm[];
  intercept: number;
  equation: string;
  r_squared_holdout: number | null;
  residual_std: number | null;
  auc_holdout: number | null;
  n_train: number;
  n_holdout: number;
  notes: string[];
  /** spline failure model only: failure probability across each cause's range, the others at their mean */
  curves?: { variable: string; values: number[]; probability: number[] }[] | null;
}

export type FailureModelKind = "linear" | "spline";

export interface ModelCardsResponse {
  cards: EquationCard[];
  provenance: Provenance;
}

export interface Intervention {
  variable: string;
  change_type: "set" | "percent" | "reset";
  value: number;
  start_step?: number;
}

export interface RiskEstimate {
  probability: number;
  lower: number;
  upper: number;
}

export interface VariableForecast {
  variable: string;
  median: number[];
  lower: number[];
  upper: number[];
  baseline_median: number[];
  change_at_end: number;
  mean_change: number;
}

export interface WhatIfResult {
  horizon: number;
  n_rollouts: number;
  interventions: Intervention[];
  forecasts: Record<string, VariableForecast>;
  risk: RiskEstimate | null;
  baseline_risk: RiskEstimate | null;
  risk_change: number | null;
  downstream_changes: Record<string, number>;
  computation_ms: number;
  assumptions: string[];
}

export interface WhatIfResponse {
  result: WhatIfResult;
  provenance: Provenance;
}

export interface PathNode {
  variable: string;
  change: number;
  percent_change: number | null;
  unit: string | null;
}

export interface RootCausePath {
  nodes: PathNode[];
  description: string;
  contribution: number;
  share: number;
  explanation: string;
}

export interface Symptom {
  variable: string;
  correlation: number;
  explanation: string;
}

export interface RootCauseResult {
  risk: RiskEstimate | null;
  risk_is_high: boolean;
  paths: RootCausePath[];
  risk_reducing_paths: RootCausePath[];
  symptoms: Symptom[];
  computation_ms: number;
  notes: string[];
}

export interface RootCauseResponse {
  result: RootCauseResult;
  provenance: Provenance;
}

export interface LeverChange {
  lever: string;
  from_value: number;
  to_value: number;
  change_pct: number;
}

export interface CandidateResult {
  rank: number;
  label: string;
  changes: LeverChange[];
  tool_change: boolean;
  risk: number;
  risk_lower: number;
  risk_upper: number;
  risk_reduction: number;
  cost: number;
  meets_target: boolean;
  /** share of the search rollouts whose risk ends at or below the target */
  share_below_target?: number | null;
}

/** The smallest single-lever change that reaches the target under the optimizer's selection rule. */
export interface MinimumEffectiveChange {
  change: LeverChange;
  risk: RiskEstimate;
  share_below_target: number;
  cost: number;
  meets_target: boolean;
}

export interface IneffectiveLever {
  variable: string;
  best_risk_reduction: number;
  reason: string;
}

export interface RecommendationConfidence {
  path_edge_confidence: number | null;
  interval_score: number | null;
  validation_score: number | null;
  combined: number | null;
  explanation: string;
}

export interface Recommendation {
  action_type: "levers" | "tool_change" | "none";
  changes: LeverChange[];
  tool_change: boolean;
  risk_before: RiskEstimate;
  risk_after: RiskEstimate;
  target_risk: number;
  meets_target: boolean;
  cost: number;
  side_effects: string[];
  time_to_effect_steps: number | null;
  time_to_effect_seconds: number | null;
  causal_paths: string[];
  confidence: RecommendationConfidence;
  explanation: string;
  candidates: CandidateResult[];
  ineffective: IneffectiveLever[];
  n_candidates: number;
  search_ms: number;
  /** one computed sentence in the brief's format */
  headline?: string;
  selection_rule?: string;
  share_below_target?: number | null;
  minimum_effective_change?: MinimumEffectiveChange | null;
  least_effective_lever?: IneffectiveLever | null;
}

/** The three predictive baselines: B1 naive SHAP, B2 actionable SHAP, B3 predict-then-optimize (the main one). */
export type BaselineMethod = "naive_shap" | "actionable_shap" | "counterfactual";

export interface BaselineAction {
  method: BaselineMethod;
  blamed_feature: string;
  /** null for B3, which searches actions instead of blaming a feature */
  blamed_shap: number | null;
  kind: "lever" | "damp_vibration" | "tool_change" | "none";
  lever: string | null;
  from_value: number | null;
  to_value: number | null;
  change_pct: number | null;
  /** every lever change of the action (one for B1 and B2, one or two for B3) */
  changes: LeverChange[];
  cost: number;
  description: string;
  shap_values: Record<string, number>;
  predicted_risk: number;
  /** the baseline model's own predicted risk after its action */
  predicted_risk_after: number | null;
}

export interface RecommendResponse {
  recommendation: Recommendation;
  /** the main baseline (B3) */
  baseline_action: BaselineAction | null;
  baseline_actions: Partial<Record<BaselineMethod, BaselineAction>>;
  main_baseline: BaselineMethod | null;
  baseline_labels: Partial<Record<BaselineMethod, string>>;
  provenance: Provenance;
}

/** Where the live data comes from (GET /health, /metrics status, /config). */
export interface DataSourceInfo {
  kind: "simulator" | "replay" | "mtconnect" | string;
  description: string;
  /** Ground truth, fault injection, scenarios, the demo and Apply exist only with the simulator. */
  has_simulator: boolean;
  /** Whether the data itself is simulated (a replayed sample log still is). */
  simulated: boolean;
}

/** Whether this tab has its own live simulation on the server or shares one, and why. */
export interface SessionStatus {
  private: boolean;
  /** One or two sentences for the viewer. */
  note: string;
  /** Private simulations on the server now, and the server's limit (0: turned off). */
  private_sessions: number;
  max_sessions: number;
}

export interface SimStatus {
  running: boolean;
  /** Increases on every server-side reset of the live simulation. */
  run_id: number;
  step: number;
  steps_per_second: number;
  scenario: string;
  active_faults: string[];
  interventions: Record<string, number>;
  drift_state: string;
  data_source?: DataSourceInfo;
  simulated?: boolean;
  session?: SessionStatus | null;
}

export interface ActionResponse {
  ok: boolean;
  message: string;
  details: Record<string, unknown>;
  status: SimStatus;
}

export interface HealthResponse {
  status: string;
  engine_fitted: boolean;
  live_running: boolean;
  step: number;
  scenario: string;
  simulated: boolean;
}

export interface EdgeChange {
  cause: string;
  effect: string;
  change: "added" | "removed" | "weakened" | "strengthened";
  old_coefficient: number | null;
  new_coefficient: number | null;
  new_interval: [number, number] | null;
  explanation: string;
}

export interface LiveAlert {
  kind: "risk" | "drift" | "error" | string;
  step?: number;
  message?: string;
  risk?: RiskEstimate | null;
  detected_step?: number;
  change_point_step?: number;
  equation?: string;
  cusum_value?: number;
  rediscovery_step?: number | null;
  edge_changes?: EdgeChange[];
  explanation?: string[];
  invalidated_recommendations?: string[];
  status?: "suspected" | "confirmed" | "no_structural_change";
}

export interface LiveEvent {
  step: number;
  event_type: string;
  details: string | Record<string, unknown>;
  timestamp?: string;
}

export interface LogEntry {
  timestamp: string;
  step_name: string;
  duration_ms: number | null;
  message: string;
  details: Record<string, unknown>;
  level: string;
  /** High-frequency entry (the live loop's per-step risk estimate), kept in its own buffer. */
  routine: boolean;
}

export interface SensorRow {
  step: number;
  failure: number;
  machine_running: number;
  [variable: string]: number;
}

export interface SensorFrame {
  run_id: number;
  step: number;
  timestamp: string;
  values: Record<string, number>;
  machine_running: number;
  failure: number;
  active_faults: string[];
  interventions: Record<string, number>;
  simulated: boolean;
}

export interface RiskUpdate {
  run_id: number;
  step: number;
  twin_risk: RiskEstimate | null;
  machine_running: boolean;
  horizon_steps: number;
  root_cause: RootCauseResult | null;
  validation: {
    label: string;
    true_hazard_per_step: number;
    /** The latest ground-truth value; it is refreshed only every few steps (see true_risk_step). */
    true_risk_within_horizon: number | null;
    /** Step at which true_risk_within_horizon was computed. */
    true_risk_step: number | null;
    /** Whether that ground truth is inside the twin's 90% interval at the same step (null before the first check). */
    inside_twin_interval?: boolean | null;
    /** This session's running count of such checks, and how many were inside. */
    interval_checks?: number;
    interval_hits?: number;
  };
}

/** One past risk estimate of the live loop (GET /risk/history). */
export interface RiskHistoryPoint {
  step: number;
  twin_risk: RiskEstimate | null;
  true_risk_within_horizon: number | null;
  true_risk_step: number | null;
}

export interface RiskHistoryResponse {
  simulated: boolean;
  run_id: number;
  validation_label: string;
  points: RiskHistoryPoint[];
}

export interface GraphUpdate {
  reason: string;
  graph: CausalGraph;
  edge_changes: EdgeChange[];
}

export interface GraphVersion {
  version: number;
  step: number;
  reason: string;
  n_edges: number;
  edge_changes: EdgeChange[];
}

export interface DemoBeatResult {
  finished: boolean;
  position: number;
  total: number;
  beat: string | null;
  title?: string;
  narration?: string;
  step?: number;
  twin_risk?: RiskEstimate | null;
  validation?: {
    label: string;
    true_risk_within_horizon: number | null;
    inside_twin_interval?: boolean | null;
    interval_checks?: number;
    interval_hits?: number;
  };
  recommendation?: Recommendation;
  baseline_action?: BaselineAction | null;
  baseline_actions?: Partial<Record<BaselineMethod, BaselineAction>>;
  alert?: LiveAlert | null;
  invalidated_recommendations?: string[];
}

export interface BenchmarkProgress {
  done: number;
  total: number;
  message: string;
}

export interface LeverConfig {
  minimum: number;
  maximum: number;
  unit: string;
  cost_per_pct: number;
  max_change_pct: number;
  side_effect_increase: string;
  side_effect_decrease: string;
}

export interface AppConfig {
  illustrative_note: string;
  seconds_per_step: number;
  engine: Record<string, number | string>;
  levers: Record<string, LeverConfig>;
  variables: Record<string, { type: string; unit: string; normal_range: [number, number] | null }>;
  faults: string[];
  scenarios: { name: string; description: string }[];
  budgets_ms: Record<string, number>;
  optimizer: { target_risk: number; [key: string]: number | string };
  drift: Record<string, number>;
  live: Record<string, number>;
  baseline_model: {
    name: string;
    metrics: Record<string, number | string>;
    features: string[];
    action_mapping: { feature: string; action: string; lever: string | null; direction: number | string }[];
    damping_cost: number;
    methods?: Partial<Record<BaselineMethod, string>>;
    main_method?: BaselineMethod;
  };
  demo_beats: { name: string; title: string }[];
}

export interface MethodResult {
  action: string;
  blamed: string;
  cost: number;
  true_risk_after: number;
  risk_reduction: number;
  failure_rate_after: number;
  success: boolean;
  /** the method chose to do nothing */
  no_action: boolean;
  /** true risk after the action exceeds the no-action true risk by more than the margin */
  worse_than_no_action: boolean;
  /** the method chose to do nothing while the true risk was above the target */
  false_reassurance: boolean;
  predicted_risk_after?: number | null;
}

export type Winner = "causaltwin" | "baseline" | "tie";

export interface BenchmarkCase {
  case_id: number;
  scenario: string;
  seed: number;
  step: number;
  true_risk_no_action: number;
  failure_rate_no_action: number;
  causaltwin: MethodResult;
  baselines: Record<BaselineMethod, MethodResult>;
  /** head to head against the main baseline (B3) */
  winner: Winner;
  winner_by_baseline: Record<BaselineMethod, Winner>;
}

export interface MethodSummary {
  success_rate: number;
  n_success: number;
  mean_risk_reduction: number;
  mean_true_risk_after: number;
  mean_failure_rate_after: number;
  mean_cost: number;
  blamed: Record<string, number>;
  n_no_action: number;
  n_worse_than_no_action: number;
  cases_worse_than_no_action: number[];
  n_false_reassurance: number;
  cases_false_reassurance: number[];
}

export interface GraphRecoveryMetrics {
  n_true_edges: number;
  n_found_edges: number;
  true_positives: number;
  precision: number;
  recall: number;
  f1: number;
  lag_accuracy: number;
  structural_hamming_distance: number;
  missed_edges: string[];
  extra_edges: string[];
  wrong_lags?: string[];
}

/** How the benchmark was run (GET /benchmark, "settings"). */
export interface BenchmarkSettings {
  situations_per_scenario: number;
  seeds_per_situation: number;
  horizon_steps: number;
  target_risk: number;
  min_true_risk: number;
  success: string;
}

export interface BenchmarkResults {
  source: string;
  settings: BenchmarkSettings;
  n_cases: number;
  summary: {
    causaltwin: MethodSummary;
    baselines: Record<BaselineMethod, MethodSummary>;
    main_baseline: BaselineMethod;
    baseline_labels: Record<BaselineMethod, string>;
    mean_true_risk_no_action: number;
    mean_failure_rate_no_action: number;
    /** head to head against the main baseline (B3) */
    wins: Record<string, number>;
    cases_where_causaltwin_is_worse: number[];
    wins_by_baseline: Record<BaselineMethod, Record<string, number>>;
    cases_where_causaltwin_is_worse_by_baseline: Record<BaselineMethod, number[]>;
  };
  per_scenario: Record<
    string,
    { n: number; causaltwin_success: number | null; baseline_success: Record<BaselineMethod, number | null> }
  >;
  cases: BenchmarkCase[];
  baseline_accuracy: Record<string, number | string>;
  graph_recovery: {
    graph_recovery_with_failure: GraphRecoveryMetrics | null;
    graph_recovery_without_failure: GraphRecoveryMetrics | null;
  };
  timing_seconds: Record<string, number>;
  running?: boolean;
  provenance?: Provenance;
}

export interface FaultRun {
  fault: string;
  seed: number;
  fault_step: number;
  false_alarms_before_fault: number;
  detected_step: number | null;
  confirmed_step: number | null;
  equation?: string;
  rediscovery_attempts?: number;
  status?: string;
  explanation?: string[];
  detection_delay_steps?: number;
  rewire_delay_steps?: number;
}

export interface DriftResults {
  source: string;
  faults: FaultRun[];
  false_alarms: {
    scenario: string;
    seed: number;
    n_steps: number;
    suspected_alarms: number;
    wrongly_confirmed_changes: number;
  }[];
}

export interface ValidationResults {
  source: string;
  training_data: Record<string, unknown>;
  graph_recovery_with_failure: GraphRecoveryMetrics;
  graph_recovery_without_failure: GraphRecoveryMetrics;
  per_scenario_recovery_without_failure: Record<string, Partial<GraphRecoveryMetrics>>;
  trap_check: Record<string, unknown> & { passed: boolean };
  intervention_accuracy: Record<string, unknown>;
  timings_ms: Record<string, number>;
}

export interface TrueGraph {
  label: string;
  source: string;
  edges: { cause: string; effect: string; lag: number; sign: string; edge_type: string; explanation: string }[];
  notes: string[];
}

export interface Metrics {
  latest_step_durations_ms: Record<string, number>;
  counts: Record<string, number | Record<string, number>>;
  status: SimStatus;
  benchmark: { running: boolean; progress: Partial<BenchmarkProgress> };
  training: Record<string, number>;
}

export interface ColumnQuality {
  column: string;
  minimum: number | null;
  maximum: number | null;
  mean: number | null;
  std: number | null;
  missing_count: number;
  longest_constant_run: number;
  is_binary: boolean;
  is_flatline: boolean;
}

export interface ByodResponse {
  validation_report: Record<string, unknown>;
  quality: ColumnQuality[];
  graph: CausalGraph | null;
  model_cards: EquationCard[];
  mode: "time_series" | "tabular";
  notes: string[];
  provenance: Provenance;
}

export interface StreamMessage {
  type: string;
  time: number;
  data: unknown;
}
