"use client";

/** Control Room: live sensors, the twin's risk gauge, events, alerts, and simulation controls. */

import { AnimatePresence, motion } from "framer-motion";
import { Activity, Bell, ChevronDown, Gauge, History, Pause, Play, RotateCcw, SkipForward, Zap } from "lucide-react";
import { useMemo, useState } from "react";
import { Area, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { RiskGauge } from "@/components/charts/risk-gauge";
import { SensorChart, type SeriesPoint } from "@/components/charts/sensor-chart";
import { CountUp } from "@/components/common/count-up";
import { PageHeader, variableColor } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { EmptyState, ErrorState, PanelSkeleton } from "@/components/common/states";
import { GroundTruthBadge, InfoTip, SimulatedBadge } from "@/components/transparency/transparency";
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
import { formatNumber, formatPercent, humanize } from "@/lib/format";
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

function RiskPanel({ settings }: { settings: RiskSettings }) {
  const { known, risk, truth } = useCurrentRisk();
  const target = settings.target;
  const horizonMinutes = (settings.horizonSteps * settings.secondsPerStep) / 60;
  return (
    <Panel
      index={0}
      demo="risk-gauge"
      testId="risk-panel"
      title={`Failure risk within ${formatNumber(horizonMinutes, 0)} minutes`}
      subtitle="Twin estimate, inferred from data"
      icon={<Gauge className="size-4" />}
      actions={
        <InfoTip label="How the risk is computed">
          The twin rolls its fitted causal model forward {settings.horizonSteps} steps ({formatNumber(horizonMinutes, 0)} minutes) from the
          latest live data with {settings.rollouts} Monte Carlo rollouts and reports the mean probability of at least one failure,
          with a 90% interval across rollouts. Only process noise is included, not coefficient uncertainty.
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
          </div>
          <div className="mt-4 flex w-full items-center justify-between rounded-lg border border-good/20 bg-good/5 px-3 py-2">
            <GroundTruthBadge />
            <span className="tabular text-sm text-emerald-200">{formatPercent(truth)}</span>
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Shown only to check the twin; it comes from forks of the simulator, which the twin never sees.
          </p>
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
  return (
    <Panel index={1} demo="risk-chart" title="Risk over time" subtitle="Twin estimate with interval, and the simulator ground truth" icon={<Activity className="size-4" />}>
      {data.length < 2 ? (
        <EmptyState title="Waiting for live risk updates" hint="Resume the simulation if it is paused." />
      ) : (
        <div className="h-60">
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
              <ReferenceLine y={target} stroke="#e2e8f0" strokeDasharray="3 3" strokeOpacity={0.4} />
              <Area dataKey="band" stroke="none" fill="#22d3ee" fillOpacity={0.12} isAnimationActive={false} />
              <Line dataKey="twin" stroke="#22d3ee" dot={false} strokeWidth={2} isAnimationActive={false} connectNulls={false} />
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
  const shownSpeed = speed ?? status?.steps_per_second ?? 5;

  return (
    <Panel index={2} title="Simulation controls" subtitle="Live simulated spindle" icon={<Zap className="size-4" />} actions={<SimulatedBadge />}>
      {!config.data || !status ? (
        config.error ? <ErrorState message={config.error} onRetry={config.reload} /> : <PanelSkeleton rows={4} />
      ) : (
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => void control(status.running ? "pause" : "resume")} aria-label={status.running ? "Pause" : "Resume"}>
              {status.running ? <Pause aria-hidden /> : <Play aria-hidden />} {status.running ? "Pause" : "Resume"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => void control("step", 50)}>
              <SkipForward aria-hidden /> Step 50
            </Button>
            <Button size="sm" variant="outline" onClick={() => void control("reset")}>
              <RotateCcw aria-hidden /> Reset
            </Button>
          </div>
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
      index={3}
      title="Live sensors"
      subtitle={`Rolling buffer of the last ${LIVE_BUFFER_POINTS} live readings${config.data ? ` (1 step = ${config.data.seconds_per_step} s of machine time)` : ""}`}
      icon={<Activity className="size-4" />}
      className="col-span-full"
    >
      {variables.length === 0 || points.length === 0 ? (
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
      index={4}
      title="Event timeline"
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
        <ol className="max-h-72 space-y-1.5 overflow-y-auto pr-1">
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
    <Panel index={5} demo="alert-feed" title="Alerts" subtitle="Risk threshold crossings and causal drift" icon={<Bell className="size-4" />}>
      {shown.length === 0 ? (
        <EmptyState
          title="No alerts"
          hint={`Alerts appear when predicted risk crosses ${threshold === undefined ? "the alert threshold" : formatPercent(threshold, 0)} or the causal structure changes.`}
        />
      ) : (
        <ul className="max-h-72 space-y-2 overflow-y-auto pr-1">
          <AnimatePresence initial={false}>
            {shown.map((alert, index) => {
              const drift = alert.kind === "drift";
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
                      {drift ? `Drift ${alert.status ?? ""}` : "Risk"}
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
      <PageHeader title="Control Room" description="The live simulated spindle, the twin's failure-risk estimate, events and alerts.">
        <SimulatedBadge />
      </PageHeader>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(300px,360px)_1fr_minmax(300px,360px)]">
        {settings ? (
          <>
            <RiskPanel settings={settings} />
            <RiskHistory target={settings.target} />
          </>
        ) : (
          <>
            <Panel index={0} title="Failure risk">{config.error ? <ErrorState message={config.error} onRetry={config.reload} /> : <PanelSkeleton rows={3} />}</Panel>
            <Panel index={1} title="Risk over time"><PanelSkeleton rows={4} /></Panel>
          </>
        )}
        <Controls />
        <SensorGrid />
        <div className="col-span-full grid grid-cols-1 gap-4 lg:grid-cols-2">
          <EventTimeline />
          <AlertFeed />
        </div>
      </div>
    </div>
  );
}
