/** The one typed client for the CausalTwin backend API. */

import { API_URL } from "@/lib/config";
import type {
  ActionResponse,
  AppConfig,
  BenchmarkResults,
  ByodResponse,
  ColumnQuality,
  DemoBeatResult,
  DriftResults,
  EdgeEvidence,
  GraphResponse,
  GraphVersion,
  HealthResponse,
  Intervention,
  LiveAlert,
  LiveEvent,
  LogEntry,
  Metrics,
  ModelCardsResponse,
  RecommendResponse,
  RiskHistoryResponse,
  RootCauseResponse,
  SensorRow,
  TrueGraph,
  ValidationResults,
  WhatIfResponse,
} from "@/lib/types";

/** An API error with the HTTP status and the backend's explanation. */
export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(0, `Cannot reach the CausalTwin backend at ${API_URL}. Is it running?`);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep the status text */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export type SimAction = "pause" | "resume" | "speed" | "step" | "reset" | "release";

export const api = {
  health: () => request<HealthResponse>("/health"),
  config: () => request<AppConfig>("/config"),
  graph: () => request<GraphResponse>("/graph"),
  correlationGraph: () => request<GraphResponse>("/graph/correlation"),
  graphHistory: () => request<{ versions: GraphVersion[] }>("/graph/history"),
  edgeEvidence: (cause: string, effect: string) =>
    request<EdgeEvidence>(`/graph/edge/${encodeURIComponent(cause)}/${encodeURIComponent(effect)}`),
  whatIf: (interventions: Intervention[]) => post<WhatIfResponse>("/whatif", { interventions }),
  rootCause: () => request<RootCauseResponse>("/root-cause"),
  recommend: (target?: number) => request<RecommendResponse>(`/recommend${target ? `?target=${target}` : ""}`),
  apply: () => post<ActionResponse>("/apply"),
  injectFault: (fault: string) => post<ActionResponse>("/inject-fault", { fault }),
  clearFault: (fault: string) => post<ActionResponse>("/clear-fault", { fault }),
  scenario: (scenario: string) => post<ActionResponse>("/scenario", { scenario }),
  simControl: (action: SimAction, value?: number) => post<ActionResponse>("/sim/control", { action, value }),
  demoStart: () => post<DemoBeatResult>("/demo/start"),
  demoNext: () => post<DemoBeatResult>("/demo/next"),
  benchmark: () => request<BenchmarkResults>("/benchmark"),
  benchmarkRun: () => post<{ started: boolean; quick: boolean }>("/benchmark/run"),
  validation: () => request<ValidationResults>("/validation"),
  trueGraph: () => request<TrueGraph>("/validation/true-graph"),
  driftResults: () => request<DriftResults>("/drift-results"),
  modelCards: () => request<ModelCardsResponse>("/model-cards"),
  metrics: () => request<Metrics>("/metrics"),
  /** `routine`: include, exclude, or return only the live loop's per-step risk estimates. */
  logs: (limit = 300, routine: "include" | "exclude" | "only" = "include") =>
    request<LogEntry[]>(`/logs?limit=${limit}&routine=${routine}`),
  riskHistory: (limit = 300) => request<RiskHistoryResponse>(`/risk/history?limit=${limit}`),
  events: (limit = 300) => request<{ simulated: boolean; events: LiveEvent[] }>(`/events?limit=${limit}`),
  alerts: (limit = 100) => request<{ alerts: LiveAlert[] }>(`/alerts?limit=${limit}`),
  dataRecent: (n = 300) => request<{ simulated: boolean; rows: SensorRow[] }>(`/data/recent?n=${n}`),
  dataQuality: () => request<{ simulated: boolean; rows: number; columns: ColumnQuality[] }>("/data/quality"),
  exportUrl: () => `${API_URL}/data/export`,
  byodUpload: (file: File, roles: Record<string, string>, timeColumn?: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("roles", JSON.stringify(roles));
    if (timeColumn) form.append("time_column", timeColumn);
    return request<ByodResponse>("/byod/upload", { method: "POST", body: form });
  },
};
