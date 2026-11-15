"use client";

/** Intervention Advisor: recommendation, baseline contrast, root cause, full search table, apply and verify. */

import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowRight, ArrowUpDown, ChevronDown, CircleCheck, Lightbulb, RefreshCw, ShieldCheck, Sparkles, TriangleAlert } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Bar, BarChart, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { EmptyState, ErrorState, LoadingOrError, PanelSkeleton } from "@/components/common/states";
import { ConfidenceBar, GroundTruthBadge, HowComputed, InfoTip, SimulatedBadge, TimingChip } from "@/components/transparency/transparency";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { formatNumber, formatPercent, formatPoints, formatSigned, humanize } from "@/lib/format";
import { useLiveStore } from "@/lib/store";
import type { BaselineAction, CandidateResult, Recommendation, RiskEstimate, RootCausePath } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

function RiskBar({ label, risk, target, tone }: { label: string; risk: RiskEstimate; target: number; tone: "before" | "after" }) {
  const scale = (value: number) => `${Math.min(100, Math.sqrt(value) * 100)}%`;
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular text-slate-100">
          {formatPercent(risk.probability)} <span className="text-muted-foreground">({formatPercent(risk.lower)} - {formatPercent(risk.upper)})</span>
        </span>
      </div>
      <div className="relative h-3 rounded-full bg-white/5">
        <div className={cn("absolute inset-y-0 rounded-full opacity-30", tone === "before" ? "bg-risk" : "bg-good")} style={{ left: scale(risk.lower), width: `calc(${scale(risk.upper)} - ${scale(risk.lower)})` }} />
        <div className={cn("absolute inset-y-0.5 w-1 rounded-full", tone === "before" ? "bg-risk" : "bg-good")} style={{ left: scale(risk.probability) }} />
        <div className="absolute inset-y-[-3px] w-px bg-slate-200/60" style={{ left: scale(target) }} title={`Target ${formatPercent(target, 0)}`} />
      </div>
    </div>
  );
}

function actionText(recommendation: Recommendation): string {
  if (recommendation.action_type === "none") return "No change";
  const parts = recommendation.changes.map(
    (change) => `${humanize(change.lever)} ${formatNumber(change.from_value)} -> ${formatNumber(change.to_value)} (${formatSigned(change.change_pct, 0)}%)`,
  );
  if (recommendation.tool_change) parts.push("Change the tool");
  return parts.join(" + ");
}

function RecommendationCard({ recommendation, provenanceNode, onApply, applying }: {
  recommendation: Recommendation;
  provenanceNode: React.ReactNode;
  onApply: () => void;
  applying: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const confidence = recommendation.confidence;
  return (
    <Panel index={0} demo="recommendation" testId="recommendation-card" title="CausalTwin recommendation" subtitle="Cheapest evaluated change that brings predicted risk below the target" icon={<Lightbulb className="size-4" />} actions={<>{provenanceNode}<TimingChip ms={recommendation.search_ms} label="search" /></>}>
      <div className="space-y-4 text-sm">
        <div className="rounded-lg border border-causal/30 bg-causal/5 p-3">
          <p className="font-heading text-lg text-cyan-100">{actionText(recommendation)}</p>
          <p className="mt-1 text-xs leading-relaxed text-slate-300">{recommendation.explanation}</p>
        </div>
        <div className="space-y-3">
          <RiskBar label="Predicted risk before" risk={recommendation.risk_before} target={recommendation.target_risk} tone="before" />
          <RiskBar label="Predicted risk after" risk={recommendation.risk_after} target={recommendation.target_risk} tone="after" />
        </div>
        <dl className="grid grid-cols-3 gap-2 text-xs">
          <div className="rounded-lg border border-line p-2">
            <dt className="text-muted-foreground">Cost</dt>
            <dd className="tabular text-base text-slate-100">{formatNumber(recommendation.cost, 1)}</dd>
            <dd className="text-[10px] text-muted-foreground">illustrative units</dd>
          </div>
          <div className="rounded-lg border border-line p-2">
            <dt className="text-muted-foreground">Time to effect</dt>
            <dd className="tabular text-base text-slate-100">{recommendation.time_to_effect_seconds === null ? "-" : `${recommendation.time_to_effect_seconds} s`}</dd>
            <dd className="text-[10px] text-muted-foreground">from the causal lags</dd>
          </div>
          <div className="rounded-lg border border-line p-2">
            <dt className="text-muted-foreground">Meets target</dt>
            <dd className={cn("text-base", recommendation.meets_target ? "text-good" : "text-warn")}>{recommendation.meets_target ? "Yes" : "No"}</dd>
            <dd className="text-[10px] text-muted-foreground">target {formatPercent(recommendation.target_risk, 0)}</dd>
          </div>
        </dl>
        {recommendation.side_effects.length > 0 && (
          <p className="text-xs text-amber-200">Side effects: {recommendation.side_effects.join("; ")}</p>
        )}
        <div>
          <button type="button" className="flex w-full items-center justify-between gap-2" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
            <ConfidenceBar className="flex-1" label="Confidence" value={confidence.combined} />
            <ChevronDown className={cn("size-4 text-muted-foreground transition-transform", expanded && "rotate-180")} aria-hidden />
          </button>
          {expanded && (
            <div className="mt-2 space-y-1.5 rounded-lg border border-line p-3">
              <ConfidenceBar label="Path edges" value={confidence.path_edge_confidence} explanation="Lowest bootstrap confidence among the edges on the causal path." />
              <ConfidenceBar label="Interval" value={confidence.interval_score} explanation="1 means a tight predicted risk interval; 0 means the interval is as wide as the risk itself." />
              <ConfidenceBar label="Validation" value={confidence.validation_score} explanation="Held-out accuracy (R squared, or AUC rescaled) of the equations on the path." />
              <p className="pt-1 text-[11px] leading-relaxed text-muted-foreground">{confidence.explanation}</p>
            </div>
          )}
        </div>
        {recommendation.causal_paths.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Causal path: <span className="font-mono text-cyan-200">{recommendation.causal_paths.join("; ")}</span>
          </p>
        )}
        <Button onClick={onApply} disabled={applying || recommendation.action_type === "none"} className="w-full">
          <Sparkles aria-hidden /> {applying ? "Applying..." : "Apply to the live machine"}
        </Button>
      </div>
    </Panel>
  );
}

function BaselineCard({ action }: { action: BaselineAction | null }) {
  const data = useMemo(
    () =>
      Object.entries(action?.shap_values ?? {})
        .map(([name, value]) => ({ name: humanize(name), value }))
        .sort((first, second) => Math.abs(second.value) - Math.abs(first.value))
        .slice(0, 6),
    [action],
  );
  return (
    <Panel index={1} title="Standard ML baseline for the same moment" subtitle="XGBoost + SHAP: fix the top feature" icon={<TriangleAlert className="size-4 text-warn" />}
      actions={<InfoTip>The baseline blames the feature with the largest positive SHAP value and maps it to an engineer&apos;s action: a lever is moved in the direction the ML model says lowers risk; vibration becomes &quot;damp vibration&quot; (remove its noise), a hot bearing or coolant becomes more coolant, motor current becomes less load, tool wear becomes a tool change, ambient temperature means no action. The mapping is a modelling choice, documented in the backend.</InfoTip>}>
      {!action ? (
        <EmptyState title="No baseline action" />
      ) : (
        <div className="space-y-3 text-sm">
          <div className="rounded-lg border border-warn/30 bg-warn/5 p-3">
            <p className="text-xs text-muted-foreground">Blames</p>
            <p className="font-heading text-lg text-amber-100">{humanize(action.blamed_feature)}</p>
            <p className="mt-1 text-xs text-slate-300">{action.description}</p>
          </div>
          <p className="text-xs text-muted-foreground">
            Baseline&apos;s own predicted failure probability (next 30 steps): <span className="tabular text-slate-200">{formatPercent(action.predicted_risk)}</span> - cost{" "}
            <span className="tabular text-slate-200">{formatNumber(action.cost, 1)}</span>
          </p>
          <div className="h-44">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 20 }}>
                <XAxis type="number" tick={{ fontSize: 9, fill: "#64748b" }} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 10, fill: "#cbd5e1" }} width={90} />
                <Tooltip contentStyle={{ background: "#0b1120", border: "1px solid rgb(148 163 184 / 0.2)", fontSize: 11 }} formatter={(value) => [formatNumber(Number(value), 3), "SHAP (log-odds)"]} />
                <ReferenceLine x={0} stroke="#475569" />
                <Bar dataKey="value" fill="#fbbf24" isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </Panel>
  );
}

function PathChain({ path }: { path: RootCausePath }) {
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {path.nodes.map((node, index) => (
          <span key={`${node.variable}-${index}`} className="flex items-center gap-1.5">
            <span className="rounded-md border border-risk/30 bg-risk/10 px-2 py-1 text-xs text-rose-100">
              {humanize(node.variable)} <span className="tabular text-rose-300">{formatSigned(node.change, 2)} {node.unit}</span>
            </span>
            <ArrowRight className="size-3.5 text-risk" aria-hidden />
          </span>
        ))}
        <span className="rounded-md border border-risk/50 bg-risk/20 px-2 py-1 text-xs font-medium text-rose-100">failure risk</span>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <div className="h-1.5 flex-1 rounded-full bg-white/5">
          <div className="h-full rounded-full bg-risk" style={{ width: `${Math.max(2, path.share * 100)}%` }} />
        </div>
        <span className="tabular w-12 text-right text-xs text-slate-300">{formatPercent(path.share, 0)}</span>
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{path.explanation}</p>
    </div>
  );
}

type SortKey = "rank" | "risk" | "cost" | "risk_reduction";

function CandidateTable({ candidates }: { candidates: CandidateResult[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [ascending, setAscending] = useState(true);
  const sorted = useMemo(() => {
    const list = [...candidates];
    list.sort((first, second) => (ascending ? 1 : -1) * (first[sortKey] - second[sortKey]));
    return list;
  }, [candidates, sortKey, ascending]);
  const container = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line react-hooks/incompatible-library -- the virtualizer is used as documented by TanStack
  const virtualizer = useVirtualizer({ count: sorted.length, getScrollElement: () => container.current, estimateSize: () => 34, overscan: 12 });
  const header = (key: SortKey, label: string) => (
    <button
      type="button"
      className="flex items-center gap-1 hover:text-slate-100"
      onClick={() => {
        if (key === sortKey) setAscending((value) => !value);
        else {
          setSortKey(key);
          setAscending(key === "rank" || key === "risk" || key === "cost");
        }
      }}
      aria-label={`Sort by ${label}`}
    >
      {label} <ArrowUpDown className="size-3" aria-hidden />
    </button>
  );
  const columns = "grid grid-cols-[56px_1fr_120px_130px_90px_70px] items-center gap-2 px-3";
  return (
    <Panel index={5} title={`Every evaluated option (${candidates.length})`} subtitle="The optimizer's full search, ranked: options that reach the target first, cheapest first" className="col-span-full">
      <div className={cn(columns, "border-b border-line pb-2 text-[11px] uppercase tracking-wider text-muted-foreground")} role="row">
        {header("rank", "Rank")}
        <span>Option</span>
        {header("risk", "Predicted risk")}
        <span>90% interval</span>
        {header("risk_reduction", "Reduction")}
        {header("cost", "Cost")}
      </div>
      <div ref={container} className="h-80 overflow-y-auto" role="table" aria-label="Evaluated candidate interventions">
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {virtualizer.getVirtualItems().map((item) => {
            const candidate = sorted[item.index];
            return (
              <div
                key={candidate.label}
                role="row"
                className={cn(columns, "absolute inset-x-0 border-b border-line/50 text-xs", candidate.meets_target && "text-emerald-100")}
                style={{ height: item.size, transform: `translateY(${item.start}px)` }}
              >
                <span className="tabular text-muted-foreground">{candidate.rank}</span>
                <span className="truncate">{candidate.label}</span>
                <span className="tabular">{formatPercent(candidate.risk)}</span>
                <span className="tabular text-muted-foreground">
                  {formatPercent(candidate.risk_lower)} - {formatPercent(candidate.risk_upper)}
                </span>
                <span className="tabular">{formatPoints(candidate.risk_reduction)}</span>
                <span className="tabular">{formatNumber(candidate.cost, 1)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </Panel>
  );
}

function VerificationPanel({ recommendation, horizon, truthEvery }: {
  recommendation: Recommendation | undefined;
  horizon: number | null;
  truthEvery: number | null;
}) {
  const events = useLiveStore((state) => state.events);
  const riskPoints = useLiveStore((state) => state.riskPoints);
  const applied = useMemo(() => [...events].reverse().find((event) => event.event_type === "recommendation_applied"), [events]);
  const since = useMemo(() => (applied ? riskPoints.filter((point) => point.step >= applied.step) : []), [applied, riskPoints]);
  const latest = since[since.length - 1];
  // Ground truth is computed only every few steps, so show its most recent actual value.
  const latestTruth = useMemo(() => [...since].reverse().find((point) => point.truth !== null), [since]);
  return (
    <Panel index={4} demo="verification" title="Verification: predicted versus observed" subtitle="After applying, watch the risk on the live machine" icon={<ShieldCheck className="size-4" />}>
      {!applied ? (
        <EmptyState title="Nothing applied yet" hint="Apply a recommendation, then watch the observed risk here. The live simulation must be running." />
      ) : (
        <div className="space-y-3 text-sm">
          <dl className="grid grid-cols-3 gap-2 text-xs">
            <div className="rounded-lg border border-line p-2">
              <dt className="text-muted-foreground">Predicted after</dt>
              <dd className="tabular text-base text-slate-100">{formatPercent(recommendation?.risk_after.probability)}</dd>
            </div>
            <div className="rounded-lg border border-line p-2">
              <dt className="text-muted-foreground">Twin estimate now</dt>
              <dd className="tabular text-base text-cyan-100">{formatPercent(latest?.twin)}</dd>
            </div>
            <div className="rounded-lg border border-good/30 bg-good/5 p-2">
              <dt className="text-emerald-300">Ground truth now</dt>
              <dd className="tabular text-base text-emerald-100">{formatPercent(latestTruth?.truth)}</dd>
              {latestTruth && <dd className="tabular text-[10px] text-muted-foreground">at step {latestTruth.step}</dd>}
            </div>
          </dl>
          <p className="text-xs text-muted-foreground">
            Applied at step <span className="tabular text-slate-200">{applied.step}</span>;{" "}
            {latest ? `${latest.step - applied.step} steps observed${horizon ? ` (prediction horizon ${horizon} steps)` : ""}.` : "waiting for live updates."}
          </p>
          <div className="h-40">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={since} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
                <XAxis dataKey="step" tick={{ fontSize: 9, fill: "#64748b" }} />
                <YAxis tick={{ fontSize: 9, fill: "#64748b" }} tickFormatter={(value) => formatPercent(Number(value), 0)} />
                <Tooltip contentStyle={{ background: "#0b1120", border: "1px solid rgb(148 163 184 / 0.2)", fontSize: 11 }} formatter={(value, name) => [formatPercent(Number(value)), name === "truth" ? "Simulator ground truth" : "Twin estimate"]} />
                {recommendation && <ReferenceLine y={recommendation.risk_after.probability} stroke="#a78bfa" strokeDasharray="4 3" label={{ value: "predicted", fill: "#a78bfa", fontSize: 9, position: "insideTopRight" }} />}
                <Line dataKey="twin" stroke="#22d3ee" dot={false} isAnimationActive={false} />
                <Line dataKey="truth" stroke="#34d399" strokeDasharray="4 3" dot={{ r: 2.5, fill: "#34d399", strokeWidth: 0 }} isAnimationActive={false} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <GroundTruthBadge />
            <span className="text-[11px] text-muted-foreground">
              Dots are computed ground-truth values{truthEvery ? ` (every ${truthEvery} steps while running live)` : ""}; the dashed line between them is
              interpolation.
            </span>
          </div>
        </div>
      )}
    </Panel>
  );
}

export function Advisor() {
  // A restart of the simulation (reset or demo start) recomputes the advice for the new run.
  const runId = useLiveStore((state) => state.runId);
  const recommendation = useApi(() => api.recommend(), `recommend-${runId}`);
  const rootCause = useApi(() => api.rootCause(), `root-cause-${runId}`);
  const config = useApi(() => api.config(), "config");
  const horizon = Number(config.data?.engine.whatif_horizon ?? 0) || null;
  const setStatus = useLiveStore((state) => state.setStatus);
  const [applying, setApplying] = useState(false);
  const [applyMessage, setApplyMessage] = useState<string | null>(null);

  const apply = async () => {
    setApplying(true);
    setApplyMessage(null);
    try {
      const response = await api.apply();
      setStatus(response.status);
      setApplyMessage(response.status.running ? "Applied. Watch the verification panel." : "Applied. The simulation is paused: resume it to see the effect.");
    } catch (caught) {
      setApplyMessage(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setApplying(false);
    }
  };

  const data = recommendation.data;
  return (
    <div>
      <PageHeader title="Intervention Advisor" description="Which lever actually prevents failure, how much to change it, what it costs, and how sure we are.">
        <SimulatedBadge />
        <Button size="sm" variant="outline" onClick={() => { recommendation.reload(); rootCause.reload(); }}>
          <RefreshCw aria-hidden /> Recompute for the current state
        </Button>
      </PageHeader>
      {applyMessage && (
        <p className="mb-3 flex items-center gap-2 text-sm text-emerald-200" role="status">
          <CircleCheck className="size-4" aria-hidden /> {applyMessage}
        </p>
      )}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {recommendation.error ? (
          <Panel title="CausalTwin recommendation" demo="recommendation">
            <ErrorState message={recommendation.error} onRetry={recommendation.reload} />
          </Panel>
        ) : !data ? (
          <Panel title="CausalTwin recommendation" demo="recommendation">
            <PanelSkeleton rows={8} />
          </Panel>
        ) : (
          <RecommendationCard
            recommendation={data.recommendation}
            provenanceNode={<HowComputed provenance={data.provenance} title="How the recommendation was searched" />}
            onApply={() => void apply()}
            applying={applying}
          />
        )}
        {data ? <BaselineCard action={data.baseline_action} /> : <Panel title="Standard ML baseline"><LoadingOrError state={recommendation} rows={6} /></Panel>}
        <Panel index={2} title="Root cause" subtitle="Ranked causal paths that explain the current risk" actions={<HowComputed provenance={rootCause.data?.provenance} />}>
          {rootCause.error ? (
            <ErrorState message={rootCause.error} onRetry={rootCause.reload} />
          ) : !rootCause.data ? (
            <PanelSkeleton rows={5} />
          ) : rootCause.data.result.paths.length === 0 ? (
            <EmptyState title="No risk-increasing causal path right now" />
          ) : (
            <div className="space-y-2">
              {rootCause.data.result.paths.map((path) => (
                <PathChain key={path.description} path={path} />
              ))}
            </div>
          )}
        </Panel>
        <Panel index={3} title="Symptoms, not causes, and ineffective levers" subtitle="Acting on these does not change the risk">
          <div className="space-y-3 text-xs">
            {(rootCause.data?.result.symptoms ?? []).map((symptom) => (
              <div key={symptom.variable} className="rounded-lg border border-warn/30 bg-warn/5 p-2.5">
                <p className="font-medium text-amber-100">
                  {humanize(symptom.variable)} <span className="rounded bg-warn/20 px-1 text-[9px] uppercase">symptom</span>
                </p>
                <p className="mt-0.5 leading-relaxed text-slate-300">{symptom.explanation}</p>
              </div>
            ))}
            {(data?.recommendation.ineffective ?? []).map((item) => (
              <div key={item.variable} className="rounded-lg border border-line p-2.5">
                <p className="font-medium text-slate-200">{humanize(item.variable)}</p>
                <p className="mt-0.5 leading-relaxed text-muted-foreground">{item.reason}</p>
              </div>
            ))}
          </div>
        </Panel>
        <VerificationPanel recommendation={data?.recommendation} horizon={horizon} truthEvery={config.data?.live.truth_every_steps ?? null} />
        {data && <CandidateTable candidates={data.recommendation.candidates} />}
      </div>
    </div>
  );
}
