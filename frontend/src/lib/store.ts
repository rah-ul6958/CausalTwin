/**
 * Live state shared by all pages (Zustand). WebSocket messages are queued and applied in
 * batches (see STORE_FLUSH_MS) so charts redraw a few times per second, not per message.
 */

import { create } from "zustand";

import { LIVE_BUFFER_POINTS, MAX_ALERTS, MAX_EVENTS, MAX_LOGS, MAX_ROUTINE_LOGS } from "@/lib/config";
import type {
  BenchmarkProgress,
  DemoBeatResult,
  GraphUpdate,
  LiveAlert,
  LiveEvent,
  LogEntry,
  RiskHistoryPoint,
  RiskUpdate,
  SensorFrame,
  SensorRow,
  SimStatus,
  StreamMessage,
} from "@/lib/types";

export type ConnectionState = "connecting" | "open" | "closed";

export interface LivePoint {
  step: number;
  values: Record<string, number>;
  running: boolean;
}

export interface RiskPoint {
  step: number;
  twin: number | null;
  lower: number | null;
  upper: number | null;
  /** Ground truth only at steps where it was actually computed (null elsewhere, not carried forward). */
  truth: number | null;
}

/** Snapshot from the REST API used to fill the store when the socket (re)connects. */
export interface SeedData {
  rows: SensorRow[];
  riskHistory: RiskHistoryPoint[];
  events: LiveEvent[];
  alerts: LiveAlert[];
  logs: LogEntry[];
  routineLogs: LogEntry[];
}

/** Seeded points first, then any streamed points that are newer than the seed. */
function mergeByStep<T extends { step: number }>(seeded: T[], streamed: T[]): T[] {
  const lastSeeded = seeded.length ? seeded[seeded.length - 1].step : -Infinity;
  return [...seeded, ...streamed.filter((point) => point.step > lastSeeded)];
}

interface LiveStore {
  connection: ConnectionState;
  status: SimStatus | null;
  points: LivePoint[];
  latest: SensorFrame | null;
  riskPoints: RiskPoint[];
  latestRisk: RiskUpdate | null;
  events: LiveEvent[];
  alerts: LiveAlert[];
  logs: LogEntry[];
  /** The live loop's per-step "no intervention" risk estimates (see isRoutineRiskEstimate). */
  routineLogs: LogEntry[];
  graphRevision: number;
  /** The server's live run id (from frames and risk updates); null before the first message. */
  serverRunId: number | null;
  /** Increases when the server restarts the simulation (reset or demo start); pages reload on it. */
  runId: number;
  lastGraphUpdate: GraphUpdate | null;
  /** Browser time (ms) when lastGraphUpdate arrived, so a page opened shortly after can replay it. */
  lastGraphUpdateAt: number | null;
  demo: DemoBeatResult | null;
  benchmarkProgress: BenchmarkProgress | null;
  benchmarkDoneAt: number | null;
  setConnection: (state: ConnectionState) => void;
  setStatus: (status: SimStatus) => void;
  setDemo: (demo: DemoBeatResult | null) => void;
  seed: (data: SeedData) => void;
  applyMessages: (messages: StreamMessage[]) => void;
}

/** True for the risk estimate the live loop logs every step (marked routine by the server). */
export function isRoutineRiskEstimate(entry: LogEntry): boolean {
  return entry.routine === true;
}

/** Two log lists merged in time order (ISO timestamps sort as text). */
export function mergeLogs(first: LogEntry[], second: LogEntry[]): LogEntry[] {
  return [...first, ...second].sort((a, b) => (a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0));
}

/** The last `limit` items of a list. */
function tail<T>(items: T[], limit: number): T[] {
  return items.length > limit ? items.slice(items.length - limit) : items;
}

/** A REST sensor row as a chart point (numeric sensor columns only). */
function rowToPoint(row: SensorRow): LivePoint {
  const values: Record<string, number> = {};
  for (const [key, value] of Object.entries(row)) {
    if (typeof value === "number" && !key.startsWith("event_") && key !== "step") values[key] = value;
  }
  return { step: row.step, values, running: row.machine_running === 1 };
}

/** A REST risk history entry as a chart point. */
function historyToRiskPoint(point: RiskHistoryPoint): RiskPoint {
  return {
    step: point.step,
    twin: point.twin_risk?.probability ?? null,
    lower: point.twin_risk?.lower ?? null,
    upper: point.twin_risk?.upper ?? null,
    truth: point.true_risk_step === point.step ? point.true_risk_within_horizon : null,
  };
}

export const useLiveStore = create<LiveStore>((set) => ({
  connection: "connecting",
  status: null,
  points: [],
  latest: null,
  riskPoints: [],
  latestRisk: null,
  serverRunId: null,
  runId: 0,
  events: [],
  alerts: [],
  logs: [],
  routineLogs: [],
  graphRevision: 0,
  lastGraphUpdate: null,
  lastGraphUpdateAt: null,
  demo: null,
  benchmarkProgress: null,
  benchmarkDoneAt: null,
  setConnection: (connection) => set({ connection }),
  // The status counts the steps taken; a live frame carries the index of its row, one less. When a
  // status is exactly one step ahead of the latest frame of the same run, both describe the same
  // moment, so the frame's number is kept (otherwise a paused counter would jump by one at the next poll).
  setStatus: (status) =>
    set((state) => {
      const frame = state.latest;
      const sameMoment = frame !== null && frame.run_id === status.run_id && status.step - frame.step === 1;
      return { status: sameMoment ? { ...status, step: frame.step } : status };
    }),
  setDemo: (demo) => set({ demo }),
  seed: ({ rows, riskHistory, events, alerts, logs, routineLogs }) =>
    set((state) => ({
      points: tail(mergeByStep(rows.map(rowToPoint), state.points), LIVE_BUFFER_POINTS),
      riskPoints: tail(mergeByStep(riskHistory.map(historyToRiskPoint), state.riskPoints), LIVE_BUFFER_POINTS),
      events: tail(events, MAX_EVENTS),
      alerts: tail(alerts, MAX_ALERTS),
      logs: tail(logs, MAX_LOGS),
      routineLogs: tail(routineLogs, MAX_ROUTINE_LOGS),
    })),
  applyMessages: (messages) =>
    set((state) => {
      let { points, riskPoints, events, alerts, logs, routineLogs, latest, latestRisk, status, graphRevision, runId, serverRunId } = state;
      let { lastGraphUpdate, lastGraphUpdateAt, demo, benchmarkProgress, benchmarkDoneAt } = state;
      for (const message of messages) {
        // A new run id means the server restarted the simulation: drop the old run's series
        // so charts do not mix two runs (LiveConnection then re-seeds from the REST API).
        if (message.type === "sensor_frame" || message.type === "risk_update") {
          const messageRunId = (message.data as { run_id: number }).run_id;
          if (serverRunId !== null && messageRunId !== serverRunId) {
            points = [];
            riskPoints = [];
            events = [];
            alerts = [];
            latestRisk = null;
            lastGraphUpdate = null;
            lastGraphUpdateAt = null;
            runId += 1;
          }
          serverRunId = messageRunId;
        }
        switch (message.type) {
          case "hello":
            status = message.data as SimStatus;
            break;
          case "sensor_frame": {
            const frame = message.data as SensorFrame;
            latest = frame;
            const last = points[points.length - 1];
            if (!last || frame.step > last.step) {
              points = [...points, { step: frame.step, values: frame.values, running: frame.machine_running === 1 }];
            }
            if (status) status = { ...status, step: frame.step, active_faults: frame.active_faults, interventions: frame.interventions };
            break;
          }
          case "risk_update": {
            const update = message.data as RiskUpdate;
            latestRisk = update;
            riskPoints = [
              ...riskPoints,
              {
                step: update.step,
                twin: update.twin_risk?.probability ?? null,
                lower: update.twin_risk?.lower ?? null,
                upper: update.twin_risk?.upper ?? null,
                truth: update.validation.true_risk_step === update.step ? update.validation.true_risk_within_horizon : null,
              },
            ];
            break;
          }
          case "event":
            events = [...events, message.data as LiveEvent];
            break;
          case "alert":
            alerts = [...alerts, message.data as LiveAlert];
            break;
          case "log": {
            const entries = message.data as LogEntry[];
            logs = [...logs, ...entries.filter((entry) => !isRoutineRiskEstimate(entry))];
            routineLogs = [...routineLogs, ...entries.filter(isRoutineRiskEstimate)];
            break;
          }
          case "graph_update":
            lastGraphUpdate = message.data as GraphUpdate;
            lastGraphUpdateAt = Date.now();
            graphRevision += 1;
            break;
          case "demo":
            demo = message.data as DemoBeatResult;
            break;
          case "benchmark_progress":
            benchmarkProgress = message.data as BenchmarkProgress;
            break;
          case "benchmark_done":
            benchmarkDoneAt = message.time;
            benchmarkProgress = null;
            break;
          default:
            break;
        }
      }
      return {
        points: tail(points, LIVE_BUFFER_POINTS),
        riskPoints: tail(riskPoints, LIVE_BUFFER_POINTS),
        events: tail(events, MAX_EVENTS),
        alerts: tail(alerts, MAX_ALERTS),
        logs: tail(logs, MAX_LOGS),
        routineLogs: tail(routineLogs, MAX_ROUTINE_LOGS),
        latest,
        latestRisk,
        status,
        graphRevision,
        runId,
        serverRunId,
        lastGraphUpdate,
        lastGraphUpdateAt,
        demo,
        benchmarkProgress,
        benchmarkDoneAt,
      };
    }),
}));
