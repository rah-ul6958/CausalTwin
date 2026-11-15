"use client";

/** Control Room: live sensors, the twin's risk gauge, events, alerts, and simulation controls. */

import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  Bell,
  ChevronDown,
  CircleDot,
  Cog,
  Gauge,
  GitBranch,
  History,
  Pause,
  Play,
  Radio,
  RotateCcw,
  SkipForward,
  SlidersHorizontal,
  TriangleAlert,
  Zap,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Area, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { RiskGauge } from "@/components/charts/risk-gauge";
import { SensorChart, type SeriesPoint } from "@/components/charts/sensor-chart";
import { riskLevel, SpindleTwin, type RiskLevel } from "@/components/charts/spindle-twin";
import { CountUp } from "@/components/common/count-up";
import { PageHeader, variableColor } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { EmptyState, ErrorState, LoadingOrError, PanelSkeleton } from "@/components/common/states";
import { DataSourceBadge, GroundTruthBadge, InfoTip } from "@/components/transparency/transparency";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { api, type SimAction } from "@/lib/api";
import { LIVE_BUFFER_POINTS } from "@/lib/config";
import type { AppConfig } from "@/lib/types";
import { formatNumber, formatPercent, humanize } from "@/lib/format";
import { useDataSource } from "@/lib/data-source";
import { useLiveStore, type RiskPoint } from "@/lib/store";
import type { LiveAlert, LiveEvent, RiskEstimate } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

/** Settings the risk panels describe, all read from GET /config. */
interface RiskSettings {
  target: number;
  horizonSteps: number;
  secondsPerStep: number;
  rollouts: number;
}

/** The twin's current risk estimate: the last streamed update, or the last point of the risk history. */
function useCurrentRisk(): { known: boolean; risk: RiskEstimate | null; truth: number | null } {
  const latestRisk = useLiveStore((state) => state.latestRisk);
  const lastPoint = useLiveStore((state) => (state.riskPoints.length ? state.riskPoints[state.riskPoints.length - 1] : null));
  // Ground truth is computed only every few steps; take the most recent computed value.
  const lastTruth = useLiveStore((state) => {
    for (let index = state.riskPoints.length - 1; index >= 0; index -= 1) {
      const truth = state.riskPoints[index].truth;
      if (truth !== null) return truth;
    }
    return null;
  });
  if (latestRisk) return { known: true, risk: latestRisk.twin_risk, truth: latestRisk.validation.true_risk_within_horizon };
  if (!lastPoint) return { known: false, risk: null, truth: null };
  const risk = lastPoint.twin !== null && lastPoint.lower !== null && lastPoint.upper !== null
    ? { probability: lastPoint.twin, lower: lastPoint.lower, upper: lastPoint.upper }
    : null;
  return { known: true, risk, truth: lastTruth };
}

const LEVEL_STYLE: Record<RiskLevel, { text: string; className: string }> = {
  low: { text: "Low risk", className: "border-good/40 bg-good/10 text-emerald-200" },
  elevated: { text: "Elevated risk", className: "border-warn/40 bg-warn/10 text-amber-200" },
  critical: { text: "Critical: above target", className: "border-risk/50 bg-risk/10 text-rose-200" },
  unknown: { text: "No estimate", className: "border-line text-slate-300" },
};

/** Low, elevated or critical, relative to the optimizer's target risk. */
function RiskLevelChip({ level }: { level: RiskLevel }) {
  const style = LEVEL_STYLE[level];
  return (
    <span className={cn("mt-2 inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium", style.className)}>
      <span className={cn("size-1.5 rounded-full bg-current", level === "critical" && "status-light")} aria-hidden />
      {style.text}
    </span>
  );
}

/** One tile of the machine status strip. */
function StatusTile({ icon: Icon, label, value, tone = "text-slate-100", pulse }: { icon: typeof Activity; label: string; value: string; tone?: string; pulse?: string }) {
  return (
    <div className="flex min-w-0 items-center gap-3 rounded-xl border border-line bg-white/[0.02] px-3 py-2.5">
      <span className="relative flex size-8 shrink-0 items-center justify-center rounded-lg border border-line bg-[#060a13]">
        <Icon className={cn("size-4", tone)} aria-hidden />
        {pulse && <span className={cn("status-light absolute -top-0.5 -right-0.5 size-2", pulse)} aria-hidden />}
      </span>
      <div className="min-w-0">
        <p className="hud-label truncate">{label}</p>
        <p className={cn("tabular truncate text-sm", tone)}>{value}</p>
      </div>
    </div>
  );
}

/** Machine status at a glance; every value comes from the live stream or the status poll. */
function MachineStatusStrip({ target }: { target: number | undefined }) {
  const latest = useLiveStore((state) => state.latest);
  const status = useLiveStore((state) => state.status);
  const connection = useLiveStore((state) => state.connection);
  const { risk } = useCurrentRisk();
  const level = riskLevel(risk?.probability, target);
  const running = latest ? latest.machine_running === 1 : null;
  const failed = latest?.failure === 1;
  const faults = status?.active_faults ?? [];
  const interventions = Object.keys(status?.interventions ?? {}).length;
  const drift = status?.drift_state ?? "unknown";
  return (
    <section aria-label="Machine status" className="col-span-full grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
      <StatusTile
        icon={Cog}
        label="Machine"
        value={failed ? "Failure" : running === null ? "Waiting" : running ? "Running" : "Stopped"}
        tone={failed ? "text-rose-300" : running ? "text-emerald-200" : "text-amber-200"}
        pulse={running && !failed ? "bg-good" : failed ? "bg-risk" : undefined}
      />
      <StatusTile
        icon={Radio}
        label="Stream"
        value={connection === "open" ? "Connected" : connection === "connecting" ? "Connecting" : "Reconnecting"}
        tone={connection === "open" ? "text-cyan-100" : "text-amber-200"}
      />
      <StatusTile icon={CircleDot} label="Simulation step" value={status ? status.step.toLocaleString() : "-"} />
      <StatusTile
        icon={Gauge}
        label="Twin risk"
        value={risk ? `${formatPercent(risk.probability)} - ${LEVEL_STYLE[level].text.split(":")[0].replace(" risk", "")}` : "No estimate"}
        tone={level === "critical" ? "text-rose-300" : level === "elevated" ? "text-amber-200" : level === "low" ? "text-emerald-200" : "text-slate-300"}
        pulse={level === "critical" ? "bg-risk" : undefined}
      />
      <StatusTile
        icon={GitBranch}
        label="Drift monitor"
        value={humanize(drift)}
        tone={drift === "monitoring" ? "text-slate-100" : "text-amber-200"}
        pulse={drift !== "monitoring" && drift !== "unknown" ? "bg-warn" : undefined}
      />
      <StatusTile
        icon={faults.length ? TriangleAlert : SlidersHorizontal}
        label={faults.length ? "Active fault" : "Interventions"}
        value={faults.length ? faults.map(humanize).join(", ") : interventions ? `${interventions} lever(s) held` : "None"}
        tone={faults.length ? "text-amber-200" : "text-slate-100"}
        pulse={faults.length ? "bg-warn" : undefined}
      />
    </section>
  );
}

/** The live spindle visualization with a legend of what drives it. */
function SpindlePanel({ config, target }: { config: AppConfig; target: number }) {
  const { risk } = useCurrentRisk();
  return (
    <Panel
      index={0}
      title="Live spindle"
      subtitle="Every element follows the streamed readings"
      icon={<Cog className="size-4" />}
      actions={
        <InfoTip label="What the spindle visual shows">
          A drawing of the live readings, not a 3D model. The rotor turns faster with spindle speed (slowed down for readability)
          and stops when the machine stops; the bearing ring turns amber, then red, as bearing temperature nears and passes the top
          of its normal range; the rotor shakes only while vibration is above its normal range; the coolant channel flows with
          coolant flow; the orange arc fills with tool wear. The core takes the colour of the twin&apos;s risk level, amber during an
          injected fault and red at a failure. Normal ranges are illustrative.
        </InfoTip>
      }
    >
      <SpindleTwin config={config} riskLevel={riskLevel(risk?.probability, target)} className="mx-auto max-w-[400px]" />
      <ul className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <li className="flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-causal" aria-hidden /> Bearing ring: temperature
        </li>
        <li className="flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-teal-400" aria-hidden /> Channel: coolant flow
        </li>
        <li className="flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-orange-500" aria-hidden /> Arc: tool wear
        </li>
        <li className="flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-good" aria-hidden /> Core: twin risk level
        </li>
      </ul>
    </Panel>
  );
}

function RiskPanel({ settings }: { settings: RiskSettings }) {
  const { known, risk, truth } = useCurrentRisk();
  const source = useDataSource();
  const target = settings.target;
  const horizonMinutes = (settings.horizonSteps * settings.secondsPerStep) / 60;
  return (
    <Panel
      index={1}
      demo="risk-gauge"
      testId="risk-panel"
      title={`Failure risk within ${formatNumber(horizonMinutes, 0)} minutes`}
      subtitle="Twin estimate, inferred from data"
      icon={<Gauge className="size-4" />}
      actions={
        <InfoTip label="How the risk is computed">
          The twin rolls its fitted causal model forward {settings.horizonSteps} steps ({formatNumber(horizonMinutes, 0)} minutes) from the
          latest live data with {settings.rollouts} Monte Carlo rollouts and reports the mean probability of at least one failure,
          with a 90% interval across rollouts. Each rollout includes process noise and its own draw of the failure model&apos;s coefficients (they were estimated from a limited number of failures); uncertainty in the other equations&apos; coefficients is not included.
        </InfoTip>
      }
    >
      {!known ? (
        <PanelSkeleton rows={3} />
      ) : (
        <div className="flex flex-col items-center">
          <RiskGauge risk={risk} target={target} />
          <div className="-mt-6 text-center">
            {risk ? (
              <CountUp value={risk.probability} format={(value) => formatPercent(value)} className="tabular text-4xl font-semibold text-slate-50" />
            ) : (
              <span className="text-sm text-warn">Machine stopped or restarting - no estimate</span>
            )}
            {risk && (
              <p className="tabular mt-1 text-xs text-muted-foreground">
                90% interval {formatPercent(risk.lower)} - {formatPercent(risk.upper)} - target {formatPercent(target, 0)}
              </p>
            )}
            {risk && <RiskLevelChip level={riskLevel(risk.probability, target)} />}
          </div>
          {source.has_simulator ? (
            <>
              <div className="mt-4 flex w-full items-center justify-between rounded-lg border border-good/20 bg-good/5 px-3 py-2">
                <GroundTruthBadge />
                <span className="tabular text-sm text-emerald-200">{formatPercent(truth)}</span>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                Shown only to check the twin; it comes from forks of the simulator, which the twin never sees.
              </p>
            </>
          ) : (
            <p className="mt-4 text-[11px] leading-relaxed text-muted-foreground">
              No ground truth exists for real data: compare the estimate with the machine&apos;s own maintenance records.
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}

/** Round axis maxima for the risk chart (probabilities); each is split into five equal steps. */
const RISK_AXIS_MAXIMA = [0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1];

/** A risk axis that always shows the target line and uses even, readable ticks. */
function riskAxis(points: RiskPoint[], target: number): { maximum: number; ticks: number[] } {
  let highest = target * 1.2;
  for (const point of points) highest = Math.max(highest, point.twin ?? 0, point.upper ?? 0, point.truth ?? 0);
  const maximum = RISK_AXIS_MAXIMA.find((value) => value >= highest) ?? 1;
  return { maximum, ticks: Array.from({ length: 6 }, (_, index) => (maximum * index) / 5) };
}

function RiskHistory({ target }: { target: number }) {
  const riskPoints = useLiveStore((state) => state.riskPoints);
  const data = useMemo(
    () =>
      riskPoints.map((point) => ({
        step: point.step,
        twin: point.twin,
        band: point.lower !== null && point.upper !== null ? [point.lower, point.upper] : null,
        truth: point.truth,
      })),
    [riskPoints],
  );
  const axis = useMemo(() => riskAxis(riskPoints, target), [riskPoints, target]);
  const source = useDataSource();
  return (
    <Panel
      index={3}
      demo="risk-chart"
      className="lg:col-span-2"
      title="Risk over time"
      subtitle={source.has_simulator ? "Twin estimate with interval, and the simulator ground truth" : "Twin estimate with interval"}
      icon={<Activity className="size-4" />}
    >
      {data.length < 2 ? (
        <EmptyState title="Waiting for live risk updates" hint="Resume the simulation if it is paused." />
      ) : (
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -10 }}>
              <XAxis dataKey="step" tick={{ fontSize: 10, fill: "#64748b" }} />
              <YAxis
                tick={{ fontSize: 10, fill: "#64748b" }}
                ticks={axis.ticks}
                domain={[0, axis.maximum]}
                allowDataOverflow
                tickFormatter={(value) => formatPercent(Number(value), Number.isInteger(Math.round(Number(value) * 1000) / 10) ? 0 : 1)}
              />
              <Tooltip
                contentStyle={{ background: "#0b1120", border: "1px solid rgb(148 163 184 / 0.2)", fontSize: 11 }}
                labelFormatter={(step) => `step ${step}`}
                formatter={(value, name) =>
                  Array.isArray(value)
                    ? [`${formatPercent(Number(value[0]))} - ${formatPercent(Number(value[1]))}`, "Twin 90% interval"]
                    : [formatPercent(Number(value)), name === "truth" ? "Simulator ground truth" : "Twin estimate"]
                }
              />
              <defs>
                <linearGradient id="risk-band" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.28} />
                  <stop offset="100%" stopColor="#22d3ee" stopOpacity={0.04} />
                </linearGradient>
              </defs>
              <ReferenceLine
                y={target}
                stroke="#f43f5e"
                strokeDasharray="4 4"
                strokeOpacity={0.55}
                label={{ value: "target", position: "insideTopRight", fill: "#fda4af", fontSize: 10 }}
              />
              <Area dataKey="band" stroke="none" fill="url(#risk-band)" isAnimationActive={false} />
              <Line dataKey="twin" stroke="#22d3ee" dot={false} strokeWidth={2.2} isAnimationActive={false} connectNulls={false} style={{ filter: "drop-shadow(0 0 4px rgb(34 211 238 / 0.6))" }} />
              <Line dataKey="truth" stroke="#34d399" strokeDasharray="4 3" dot={false} strokeWidth={1.4} isAnimationActive={false} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}

function Controls() {
  const config = useApi(() => api.config(), "config");
  const status = useLiveStore((state) => state.status);
  const setStatus = useLiveStore((state) => state.setStatus);
  const [speed, setSpeed] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (action: () => Promise<{ status: typeof status }>) => {
    setError(null);
    try {
      const response = await action();
      if (response.status) setStatus(response.status);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };
  const control = (action: SimAction, value?: number) => run(() => api.simControl(action, value));
  const source = useDataSource();
  const shownSpeed = speed ?? status?.steps_per_second ?? 5;

  return (
    <Panel
      index={2}
      title={source.has_simulator ? "Simulation controls" : "Live data controls"}
      subtitle={source.description}
      icon={<Zap className="size-4" />}
      actions={<DataSourceBadge />}
    >
      {!config.data || !status ? (
        config.error ? <ErrorState message={config.error} onRetry={config.reload} /> : <PanelSkeleton rows={4} />
      ) : (
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => void control(status.running ? "pause" : "resume")} aria-label={status.running ? "Pause" : "Resume"}>
              {status.running ? <Pause aria-hidden /> : <Play aria-hidden />} {status.running ? "Pause" : "Resume"}
            </Button>
            {source.has_simulator && (
              <Button size="sm" variant="outline" onClick={() => void control("step", 50)}>
                <SkipForward aria-hidden /> Step 50
              </Button>
            )}
            <Button size="sm" variant="outline" onClick={() => void control("reset")}>
              <RotateCcw aria-hidden /> Reset
            </Button>
          </div>
          {source.has_simulator && (
          <div>
            <label className="mb-2 flex justify-between text-xs text-muted-foreground" htmlFor="speed-slider">
              <span>Speed</span>
              <span className="tabular text-slate-200">{shownSpeed} steps/s</span>
            </label>
            <Slider
              id="speed-slider"
              aria-label="Simulation speed in steps per second"
              min={1}
              max={50}
              step={1}
              value={[shownSpeed]}
              onValueChange={(values) => setSpeed(values[0])}
              onValueCommit={(values) => void control("speed", values[0])}
            />
          </div>
          )}
          {!source.has_simulator && (
            <p className="text-xs leading-relaxed text-muted-foreground">
              Scenarios and fault injection exist only in the simulator. With real data, CausalTwin watches and
              advises; changes are made on the machine.
            </p>
          )}
          {source.has_simulator && (
          <div>
            <p className="mb-1 text-xs text-muted-foreground">Scenario</p>
            <Select value={status.scenario} onValueChange={(scenario) => void run(() => api.scenario(scenario))}>
              <SelectTrigger className="w-full" aria-label="Scenario">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {config.data.scenarios.map((scenario) => (
                  <SelectItem key={scenario.name} value={scenario.name}>
                    {humanize(scenario.name)} - {scenario.description}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          )}
          {source.has_simulator && (
          <div className="flex flex-wrap items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="outline" className="border-warn/40 text-amber-200">
                  Inject fault <ChevronDown aria-hidden />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuLabel>Changes the causal structure</DropdownMenuLabel>
                {config.data.faults.map((fault) => (
                  <DropdownMenuItem key={fault} onSelect={() => void run(() => api.injectFault(fault))}>
                    {humanize(fault)}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
            {status.active_faults.map((fault) => (
              <Button key={fault} size="xs" variant="outline" onClick={() => void run(() => api.clearFault(fault))}>
                Clear {humanize(fault)}
              </Button>
            ))}
            {Object.keys(status.interventions).length > 0 && (
              <Button size="xs" variant="outline" onClick={() => void control("release")}>
                Release {Object.keys(status.interventions).length} intervention(s)
              </Button>
            )}
          </div>
          )}
          {error && <p className="text-xs text-risk">{error}</p>}
        </div>
      )}
    </Panel>
  );
}

function SensorGrid() {
  const points = useLiveStore((state) => state.points);
  const config = useApi(() => api.config(), "config");
  const variables = useMemo(
    () => Object.entries(config.data?.variables ?? {}).filter(([, info]) => info.type !== "outcome"),
    [config.data],
  );
  const series = useMemo(() => {
    const result: Record<string, SeriesPoint[]> = {};
    for (const [name] of variables) result[name] = points.map((point) => ({ step: point.step, value: point.values[name] ?? null }));
    return result;
  }, [points, variables]);
  return (
    <Panel
      index={5}
      title="Live sensors"
      subtitle={`Rolling buffer of the last ${LIVE_BUFFER_POINTS} live readings${config.data ? ` (1 step = ${config.data.seconds_per_step} s of machine time)` : ""}`}
      icon={<Activity className="size-4" />}
      className="col-span-full"
    >
      {config.error ? (
        <ErrorState message={config.error} onRetry={config.reload} />
      ) : variables.length === 0 || points.length === 0 ? (
        <PanelSkeleton rows={3} />
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          {variables.map(([name, info]) => (
            <SensorChart key={name} name={name} unit={info.unit} data={series[name]} color={variableColor(name)} latest={points[points.length - 1]?.values[name] ?? null} />
          ))}
        </div>
      )}
    </Panel>
  );
}

const EVENT_STYLE: Record<string, string> = {
  failure: "text-risk border-risk/40",
  maintenance: "text-good border-good/40",
  tool_change: "text-emerald-200 border-good/30",
  fault_injected: "text-warn border-warn/40",
  fault_cleared: "text-cyan-200 border-causal/40",
  setpoint_change: "text-slate-300 border-line",
  shift_change: "text-slate-400 border-line",
  recommendation_applied: "text-violet-200 border-whatif/40",
};

function eventDetails(event: LiveEvent): string {
  if (typeof event.details !== "string") return JSON.stringify(event.details);
  try {
    const parsed = JSON.parse(event.details) as Record<string, unknown>;
    return Object.entries(parsed)
      .map(([key, value]) => `${humanize(key)}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`)
      .join(", ");
  } catch {
    return event.details;
  }
}

function EventTimeline() {
  const events = useLiveStore((state) => state.events);
  const [showSetpoints, setShowSetpoints] = useState(false);
  const shown = useMemo(
    () => events.filter((event) => showSetpoints || event.event_type !== "setpoint_change").slice(-80).reverse(),
    [events, showSetpoints],
  );
  return (
    <Panel
      index={6}
      title="Event timeline"
      className="col-span-full"
      subtitle="Failures, maintenance, faults, setpoint changes"
      icon={<History className="size-4" />}
      actions={
        <Button size="xs" variant="ghost" onClick={() => setShowSetpoints((value) => !value)} aria-pressed={showSetpoints}>
          {showSetpoints ? "Hide" : "Show"} setpoint changes
        </Button>
      }
    >
      {shown.length === 0 ? (
        <EmptyState title="No events yet" />
      ) : (
        <ol className="max-h-72 space-y-1.5 overflow-y-auto pr-1" tabIndex={0} aria-label="Events, newest first">
          {shown.map((event, index) => (
            <li key={`${event.step}-${event.event_type}-${index}`} className="flex items-start gap-2 text-xs">
              <span className="tabular w-14 shrink-0 text-right text-muted-foreground">{event.step}</span>
              <span className={cn("shrink-0 rounded border px-1.5 py-px text-[10px] uppercase tracking-wide", EVENT_STYLE[event.event_type] ?? "border-line text-slate-300")}>
                {humanize(event.event_type)}
              </span>
              <span className="truncate text-slate-400" title={eventDetails(event)}>
                {eventDetails(event)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

function alertText(alert: LiveAlert): string {
  if (alert.message) return alert.message;
  return (alert.explanation ?? []).join(" ");
}

function AlertFeed() {
  const alerts = useLiveStore((state) => state.alerts);
  const config = useApi(() => api.config(), "config");
  const threshold = config.data?.live.alert_risk_threshold;
  const shown = useMemo(() => alerts.slice(-30).reverse(), [alerts]);
  return (
    <Panel index={4} demo="alert-feed" title="Alerts" subtitle="Risk threshold crossings, causal drift and system errors" icon={<Bell className="size-4" />}>
      {shown.length === 0 ? (
        <EmptyState
          title="No alerts"
          hint={`Alerts appear when predicted risk crosses ${threshold === undefined ? "the alert threshold" : formatPercent(threshold, 0)} or the causal structure changes.`}
        />
      ) : (
        <ul className="max-h-72 space-y-2 overflow-y-auto pr-1" tabIndex={0} aria-label="Alerts, newest first">
          <AnimatePresence initial={false}>
            {shown.map((alert, index) => {
              const drift = alert.kind === "drift";
              const failure = alert.kind === "error";
              const step = alert.step ?? alert.rediscovery_step ?? alert.detected_step;
              return (
                <motion.li
                  key={`${alert.kind}-${step}-${alert.status ?? ""}-${index}`}
                  initial={{ opacity: 0, x: 24 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.25 }}
                  className={cn(
                    "rounded-lg border p-2.5 text-xs",
                    index === 0 && "glow-pulse",
                    drift ? "border-warn/40 bg-warn/5" : "border-risk/40 bg-risk/5",
                  )}
                >
                  <div className="mb-1 flex items-center justify-between">
                    <span className={cn("font-semibold uppercase tracking-wide", drift ? "text-warn" : "text-risk")}>
                      {drift ? `Drift ${alert.status ?? ""}` : failure ? "System error" : "Risk"}
                    </span>
                    <span className="tabular text-muted-foreground">step {step}</span>
                  </div>
                  <p className="leading-relaxed text-slate-300">{alertText(alert)}</p>
                  {alert.invalidated_recommendations && alert.invalidated_recommendations.length > 0 && (
                    <p className="mt-1 text-amber-200">{alert.invalidated_recommendations.join(" ")}</p>
                  )}
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
    </Panel>
  );
}

export function ControlRoom() {
  const config = useApi(() => api.config(), "config");
  const source = useDataSource();
  const settings: RiskSettings | null = config.data
    ? {
        target: config.data.optimizer.target_risk,
        horizonSteps: Number(config.data.engine.whatif_horizon),
        secondsPerStep: config.data.seconds_per_step,
        rollouts: config.data.live.risk_rollouts,
      }
    : null;
  return (
    <div>
      <PageHeader
        eyebrow="Module 01 / Live operations"
        title="Control Room"
        description={`${source.has_simulator ? "The live simulated spindle" : source.description}: the twin's failure-risk estimate, events and alerts.`}
      >
        <DataSourceBadge />
      </PageHeader>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <MachineStatusStrip target={settings?.target} />
        {settings && config.data ? (
          <>
            <SpindlePanel config={config.data} target={settings.target} />
            <RiskPanel settings={settings} />
          </>
        ) : (
          <>
            <Panel index={0} title="Live spindle"><LoadingOrError state={config} rows={4} /></Panel>
            <Panel index={1} title="Failure risk">{config.error ? <ErrorState message={config.error} onRetry={config.reload} /> : <PanelSkeleton rows={3} />}</Panel>
          </>
        )}
        <Controls />
        {settings ? (
          <RiskHistory target={settings.target} />
        ) : (
          <Panel index={3} title="Risk over time" className="lg:col-span-2"><LoadingOrError state={config} rows={4} /></Panel>
        )}
        <AlertFeed />
        <SensorGrid />
        <EventTimeline />
      </div>
    </div>
  );
}
