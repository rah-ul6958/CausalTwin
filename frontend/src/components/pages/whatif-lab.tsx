"use client";

/** What-If Lab: move the levers, see the ripple through the causal graph and the predicted effect. */

import { motion, useReducedMotion } from "framer-motion";
import { ArrowDownRight, ArrowUpRight, FlaskConical, RotateCcw, Timer } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { ForecastChart } from "@/components/charts/forecast-chart";
import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { EmptyState, ErrorState, LoadingOrError, PanelSkeleton } from "@/components/common/states";
import { CausalGraphView } from "@/components/graph/causal-graph";
import { HowComputed, DataSourceBadge, TimingChip } from "@/components/transparency/transparency";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { api } from "@/lib/api";
import { WHATIF_DEBOUNCE_MS } from "@/lib/config";
import { formatNumber, formatPercent, formatPoints, formatSigned, humanize, shortUnit } from "@/lib/format";
import { lagDistances } from "@/lib/graph-layout";
import { useLiveStore } from "@/lib/store";
import type { CausalGraph, Intervention, LeverConfig, RiskEstimate, WhatIfResponse } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

const RIPPLE_MS_PER_LAG = 220;
const NEGLIGIBLE_RISK_CHANGE = 0.002;

function LeverSlider({ name, config, current, value, onChange }: {
  name: string;
  config: LeverConfig;
  current: number | undefined;
  value: number;
  onChange: (value: number) => void;
}) {
  const percent = current ? (100 * (value - current)) / current : 0;
  const cost = Math.abs(percent) * config.cost_per_pct;
  const step = (config.maximum - config.minimum) / 200;
  const moved = Math.abs(percent) >= 0.5;
  return (
    <div
      className={cn(
        "rounded-xl border p-3 transition-[border-color,box-shadow,background] duration-300",
        moved ? "border-whatif/50 bg-whatif/[0.06] shadow-[0_0_24px_-10px_var(--whatif)]" : "border-line bg-white/[0.015]",
      )}
    >
      <div className="mb-2 flex items-baseline justify-between">
        <label htmlFor={`lever-${name}`} className="text-sm text-slate-100">
          {humanize(name)}
        </label>
        <span className="tabular text-sm text-violet-200" title={config.unit}>
          {formatNumber(value)} <span className="text-[10px] text-muted-foreground">{shortUnit(config.unit)}</span>
        </span>
      </div>
      <Slider
        id={`lever-${name}`}
        aria-label={`${humanize(name)} (${config.unit})`}
        min={config.minimum}
        max={config.maximum}
        step={step}
        value={[value]}
        onValueChange={(values) => onChange(values[0])}
      />
      <div className="tabular mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
        <span>
          now {formatNumber(current)} ({formatSigned(percent, 0)}%)
        </span>
        <span className="text-right">cost {formatNumber(cost, 1)}</span>
        <span>
          range {formatNumber(config.minimum)} - {formatNumber(config.maximum)}
        </span>
        <span className="text-right">{config.cost_per_pct} per %</span>
      </div>
    </div>
  );
}

/**
 * Before-and-after failure risk as two bars on one scale, each with its 90% Monte Carlo interval
 * drawn as a band (the uncertainty of the rollouts).
 */
function RiskComparison({ before, after }: { before: RiskEstimate | null | undefined; after: RiskEstimate | null | undefined }) {
  const reduceMotion = useReducedMotion();
  const top = Math.max(0.05, before?.upper ?? 0, after?.upper ?? 0) * 1.1;
  const rows = [
    { label: "Without change", risk: before, bar: "bg-slate-400/70", band: "bg-slate-300/20" },
    { label: "With change", risk: after, bar: "bg-gradient-to-r from-violet-500 to-fuchsia-400", band: "bg-whatif/25" },
  ];
  return (
    <div className="space-y-3">
      {rows.map((row) => (
        <div key={row.label}>
          <div className="mb-1 flex items-baseline justify-between text-xs">
            <span className="text-muted-foreground">{row.label}</span>
            <span className="tabular text-slate-100">{formatPercent(row.risk?.probability)}</span>
          </div>
          <div className="relative h-2.5 overflow-hidden rounded-full bg-white/[0.05]">
            {row.risk && (
              <span
                className={cn("absolute inset-y-0 rounded-full", row.band)}
                style={{ left: `${(row.risk.lower / top) * 100}%`, width: `${Math.max(0.5, ((row.risk.upper - row.risk.lower) / top) * 100)}%` }}
                aria-hidden
              />
            )}
            <motion.span
              className={cn("absolute inset-y-0 left-0 rounded-full", row.bar)}
              initial={false}
              animate={{ width: `${((row.risk?.probability ?? 0) / top) * 100}%` }}
              transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 140, damping: 22 }}
              aria-hidden
            />
          </div>
        </div>
      ))}
      <p className="text-[10px] text-muted-foreground">Bands: 90% interval across the Monte Carlo rollouts.</p>
    </div>
  );
}

function timeToEffect(graph: CausalGraph | undefined, levers: string[]): number | null {
  if (!graph) return null;
  const lags = levers.map((lever) => lagDistances(graph.edges, lever).get("failure")).filter((lag): lag is number => lag !== undefined);
  return lags.length ? Math.min(...lags) : null;
}

export function WhatIfLab() {
  const config = useApi(() => api.config(), "config");
  const graph = useApi(() => api.graph(), "graph");
  const latest = useLiveStore((state) => state.latest);
  const points = useLiveStore((state) => state.points);
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const [result, setResult] = useState<WhatIfResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [pulse, setPulse] = useState<Record<string, number>>({});
  const pulseCounter = useRef(0);

  const currentValues = useMemo(() => latest?.values ?? points[points.length - 1]?.values ?? {}, [latest, points]);
  const levers = Object.entries(config.data?.levers ?? {});
  const changed = useMemo(
    () =>
      Object.entries(overrides).filter(([name, value]) => {
        const current = currentValues[name];
        return current !== undefined && Math.abs(value - current) / Math.abs(current || 1) > 0.005;
      }),
    [overrides, currentValues],
  );
  const changedKey = changed.map(([name, value]) => `${name}=${value.toFixed(4)}`).join("|");

  useEffect(() => {
    if (!changedKey) return;
    const interventions: Intervention[] = changedKey.split("|").map((part) => {
      const [variable, value] = part.split("=");
      return { variable, change_type: "set", value: Number(value) };
    });
    const timers: ReturnType<typeof setTimeout>[] = [];
    const debounce = setTimeout(async () => {
      setPending(true);
      try {
        const response = await api.whatIf(interventions);
        setResult(response);
        setError(null);
        // Ripple: pulse affected nodes in order of their causal lag from the changed levers.
        if (graph.data) {
          const distances = new Map<string, number>();
          for (const intervention of interventions) {
            for (const [node, lag] of lagDistances(graph.data.graph.edges, intervention.variable)) {
              distances.set(node, Math.min(lag, distances.get(node) ?? Infinity));
            }
          }
          for (const [node, lag] of distances) {
            timers.push(
              setTimeout(() => {
                pulseCounter.current += 1;
                const token = pulseCounter.current;
                setPulse((previous) => ({ ...previous, [node]: token }));
              }, lag * RIPPLE_MS_PER_LAG),
            );
          }
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setPending(false);
      }
    }, WHATIF_DEBOUNCE_MS);
    return () => {
      clearTimeout(debounce);
      timers.forEach(clearTimeout);
    };
  }, [changedKey, graph.data]);

  const what = changed.length > 0 ? result?.result : undefined;
  const affected = useMemo(() => {
    if (!what) return [];
    return Object.values(what.forecasts)
      .filter((forecast) => !(forecast.variable in (config.data?.levers ?? {})))
      .filter((forecast) => Math.abs(forecast.change_at_end) > 1e-3)
      .sort((first, second) => Math.abs(second.change_at_end) - Math.abs(first.change_at_end));
  }, [what, config.data]);
  const negligible = what && Math.abs(what.risk_change ?? 0) < NEGLIGIBLE_RISK_CHANGE;
  const ttf = timeToEffect(graph.data?.graph, changed.map(([name]) => name));
  const secondsPerStep = config.data?.seconds_per_step ?? null;
  const horizonSteps = Number(config.data?.engine.whatif_horizon ?? 0) || null;
  const rollouts = Number(config.data?.engine.whatif_rollouts ?? 0) || null;
  const horizonText = horizonSteps && secondsPerStep ? `${formatNumber((horizonSteps * secondsPerStep) / 60, 0)} min` : "the horizon";

  return (
    <div>
      <PageHeader eyebrow="Module 03 / Simulation environment" title="What-If Lab" description={`Move a lever and the twin simulates the next ${horizonText} with and without the change (${rollouts ?? "-"} Monte Carlo rollouts, same noise for both).`}>
        <DataSourceBadge />
        <HowComputed provenance={result?.provenance} title="How this what-if was simulated" />
        <Button size="sm" variant="outline" onClick={() => { setOverrides({}); setResult(null); }}>
          <RotateCcw aria-hidden /> Reset levers
        </Button>
      </PageHeader>
      <div className="mb-4 h-5" aria-live="polite">
        {pending ? (
          <div className="flex items-center gap-3">
            <span className="hud-label shrink-0 !text-violet-300">Evaluating new simulation: {rollouts ?? "-"} Monte Carlo rollouts</span>
            <div className="scan-bar flex-1" aria-hidden />
          </div>
        ) : what ? (
          <span className="hud-label !text-slate-400">Experiment ready: {changed.length} lever(s) changed, {rollouts ?? "-"} rollouts per arm</span>
        ) : null}
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[380px_1fr]">
        <Panel index={0} title="Control levers" subtitle="Allowed range and illustrative cost per % change" icon={<FlaskConical className="size-4 text-whatif" />}>
          {config.error ? (
            <ErrorState message={config.error} onRetry={config.reload} />
          ) : levers.length === 0 ? (
            <PanelSkeleton rows={6} />
          ) : (
            <div className="space-y-3">
              {levers.map(([name, lever]) => (
                <LeverSlider
                  key={name}
                  name={name}
                  config={lever}
                  current={currentValues[name]}
                  value={overrides[name] ?? currentValues[name] ?? lever.minimum}
                  onChange={(value) => setOverrides((previous) => ({ ...previous, [name]: value }))}
                />
              ))}
              <p className="text-[11px] text-muted-foreground">{config.data?.illustrative_note}</p>
            </div>
          )}
        </Panel>
        <div className={cn("flex min-w-0 flex-col gap-4 transition-opacity duration-300", pending && "opacity-70")}>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Panel index={1} title="Failure risk" subtitle={`Twin estimate, next ${horizonText}`} actions={what && <TimingChip ms={what.computation_ms} />}>
              {!what ? (
                <EmptyState title={pending ? "Simulating..." : "Move a lever to start"} />
              ) : (
                <div className="tabular space-y-3">
                  <div className="flex items-end justify-between gap-2">
                    <p className="text-3xl text-violet-100">{formatPercent(what.risk?.probability)}</p>
                    <p
                      className={cn(
                        "flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
                        (what.risk_change ?? 0) < 0 ? "border-good/40 bg-good/10 text-good" : "border-risk/40 bg-risk/10 text-risk",
                      )}
                    >
                      {(what.risk_change ?? 0) < 0 ? <ArrowDownRight className="size-3" aria-hidden /> : <ArrowUpRight className="size-3" aria-hidden />}
                      {formatPoints(what.risk_change, 2, "percentage points")}
                    </p>
                  </div>
                  <RiskComparison before={what.baseline_risk} after={what.risk} />
                </div>
              )}
            </Panel>
            <Panel index={2} title="Time to effect" icon={<Timer className="size-4" />}>
              {changed.length === 0 ? (
                <EmptyState title="-" />
              ) : ttf === null ? (
                <p className="text-sm text-warn">No causal path from the changed lever(s) to failure in the discovered graph.</p>
              ) : (
                <div className="tabular">
                  <p className="text-3xl text-slate-100">{secondsPerStep === null ? "-" : `${ttf * secondsPerStep} s`}</p>
                  <p className="text-xs text-muted-foreground">{ttf} steps: the shortest sum of discovered lags from the lever to failure</p>
                </div>
              )}
            </Panel>
            <Panel index={3} title="Verdict">
              {!what ? (
                <EmptyState title="-" />
              ) : negligible ? (
                <p className="text-sm leading-relaxed text-warn">
                  Negligible effect: this change moves predicted failure risk by less than {NEGLIGIBLE_RISK_CHANGE * 100} percentage points.
                </p>
              ) : (
                <p className="text-sm leading-relaxed text-slate-200">
                  {(what.risk_change ?? 0) < 0 ? "Lowers" : "Raises"} predicted risk by {formatPercent(Math.abs(what.risk_change ?? 0), 2)};{" "}
                  {affected.length} downstream variable(s) change.
                </p>
              )}
              {error && <p className="mt-2 text-xs text-risk">{error}</p>}
            </Panel>
          </div>
          <Panel index={4} title="Ripple through the causal graph" subtitle="Affected nodes pulse in order of their causal lag" bodyClassName="p-0">
            <div className="h-[340px]">
              {graph.data ? (
                <CausalGraphView graph={graph.data.graph} pulse={pulse} compact />
              ) : (
                <div className="p-4">
                  <LoadingOrError state={graph} rows={5} />
                </div>
              )}
            </div>
          </Panel>
          <Panel index={5} title="Before and after" subtitle="Median with change (violet), without change (grey dashed), 90% band">
            {!what || secondsPerStep === null ? (
              <EmptyState title="No simulation yet" />
            ) : affected.length === 0 ? (
              <EmptyState title="No downstream variable changes noticeably" />
            ) : (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
                {affected.map((forecast) => (
                  <ForecastChart key={forecast.variable} forecast={forecast} unit={config.data?.variables[forecast.variable]?.unit ?? null} secondsPerStep={secondsPerStep} />
                ))}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
