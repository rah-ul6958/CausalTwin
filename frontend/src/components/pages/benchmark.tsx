"use client";

/** Benchmark: the Correlation Trap judged by the real simulator, plus graph recovery and drift results. */

import { Play, Trophy } from "lucide-react";
import { useEffect, useMemo } from "react";
import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { CountUp } from "@/components/common/count-up";
import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { EmptyState, ErrorState, PanelSkeleton } from "@/components/common/states";
import { CausalGraphView, edgeKey } from "@/components/graph/causal-graph";
import { GroundTruthBadge, HowComputed, SimulatedBadge } from "@/components/transparency/transparency";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { api } from "@/lib/api";
import { formatNumber, formatPercent, humanize } from "@/lib/format";
import { useLiveStore } from "@/lib/store";
import type { BenchmarkResults, CausalEdge, CausalGraph, GraphRecoveryMetrics, MethodSummary, TrueGraph } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

/** One method's benchmark results; `horizonSteps` is the benchmark's look-ahead window. */
function SummaryCard({ title, summary, tone, index, horizonSteps }: {
  title: string;
  summary: MethodSummary;
  tone: "causal" | "warn";
  index: number;
  horizonSteps: number;
}) {
  const percent = (value: number) => formatPercent(value, 0);
  return (
    <Panel index={index} title={title} className={tone === "causal" ? "border-causal/30" : "border-warn/30"}>
      <dl className="grid grid-cols-2 gap-3">
        <div>
          <dt className="text-xs text-muted-foreground">Success rate</dt>
          <dd className={cn("tabular text-3xl", tone === "causal" ? "text-cyan-100" : "text-amber-100")}>
            <CountUp value={summary.success_rate} format={percent} />
          </dd>
          <dd className="text-[10px] text-muted-foreground">true risk after action at or below 10%</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Mean true risk after</dt>
          <dd className="tabular text-3xl text-slate-100">
            <CountUp value={summary.mean_true_risk_after} format={(value) => formatPercent(value, 1)} />
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Mean risk reduction</dt>
          <dd className="tabular text-lg text-slate-100">{formatNumber(summary.mean_risk_reduction * 100, 1)} pts</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Mean cost</dt>
          <dd className="tabular text-lg text-slate-100">{formatNumber(summary.mean_cost, 1)}</dd>
          <dd className="text-[10px] text-muted-foreground">illustrative units</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-xs text-muted-foreground">Failure rate within {horizonSteps} steps (simulated futures)</dt>
          <dd className="tabular text-lg text-slate-100">{formatPercent(summary.mean_failure_rate_after, 1)}</dd>
        </div>
      </dl>
    </Panel>
  );
}

function BlamedChart({ results }: { results: BenchmarkResults }) {
  const data = useMemo(() => {
    const names = new Set([...Object.keys(results.summary.causaltwin.blamed), ...Object.keys(results.summary.baseline.blamed)]);
    return [...names].map((name) => ({
      name: name.split(" -> ").map(humanize).join(" > "),
      CausalTwin: results.summary.causaltwin.blamed[name] ?? 0,
      Baseline: results.summary.baseline.blamed[name] ?? 0,
    }));
  }, [results]);
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 40 }}>
          <XAxis type="number" allowDecimals={false} tick={{ fontSize: 9, fill: "#64748b" }} />
          <YAxis type="category" dataKey="name" width={170} tick={{ fontSize: 10, fill: "#cbd5e1" }} />
          <Tooltip contentStyle={{ background: "#0b1120", border: "1px solid rgb(148 163 184 / 0.2)", fontSize: 11 }} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="CausalTwin" fill="#22d3ee" isAnimationActive={false} />
          <Bar dataKey="Baseline" fill="#fbbf24" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function ComparisonChart({ results }: { results: BenchmarkResults }) {
  const data = [
    { metric: "Success rate", CausalTwin: results.summary.causaltwin.success_rate, Baseline: results.summary.baseline.success_rate, "No action": 0 },
    {
      metric: "True risk after",
      CausalTwin: results.summary.causaltwin.mean_true_risk_after,
      Baseline: results.summary.baseline.mean_true_risk_after,
      "No action": results.summary.mean_true_risk_no_action,
    },
    {
      metric: "Failure rate",
      CausalTwin: results.summary.causaltwin.mean_failure_rate_after,
      Baseline: results.summary.baseline.mean_failure_rate_after,
      "No action": results.summary.mean_failure_rate_no_action,
    },
  ];
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
          <XAxis dataKey="metric" tick={{ fontSize: 10, fill: "#cbd5e1" }} />
          <YAxis tick={{ fontSize: 9, fill: "#64748b" }} tickFormatter={(value) => formatPercent(Number(value), 0)} />
          <Tooltip contentStyle={{ background: "#0b1120", border: "1px solid rgb(148 163 184 / 0.2)", fontSize: 11 }} formatter={(value) => formatPercent(Number(value), 1)} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="CausalTwin" fill="#22d3ee" isAnimationActive={false} />
          <Bar dataKey="Baseline" fill="#fbbf24" isAnimationActive={false} />
          <Bar dataKey="No action" fill="#64748b" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function CaseTable({ results }: { results: BenchmarkResults }) {
  return (
    <div className="max-h-[420px] overflow-auto">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-[#0b1222] text-left text-[10px] uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="p-2">#</th>
            <th className="p-2">Scenario</th>
            <th className="p-2">True risk, no action</th>
            <th className="p-2">CausalTwin action</th>
            <th className="p-2">Risk after</th>
            <th className="p-2">Baseline blamed / action</th>
            <th className="p-2">Risk after</th>
            <th className="p-2">Winner</th>
          </tr>
        </thead>
        <tbody>
          {results.cases.map((item) => (
            <tr key={item.case_id} className={cn("border-t border-line/60", item.winner === "baseline" && "bg-risk/10", !item.causaltwin.success && "text-amber-100")}>
              <td className="tabular p-2 text-muted-foreground">{item.case_id}</td>
              <td className="p-2">{humanize(item.scenario)}</td>
              <td className="tabular p-2">{formatPercent(item.true_risk_no_action)}</td>
              <td className="p-2">{item.causaltwin.action}</td>
              <td className={cn("tabular p-2", item.causaltwin.success ? "text-good" : "text-warn")}>{formatPercent(item.causaltwin.true_risk_after)}</td>
              <td className="p-2">
                <span className="text-amber-200">{humanize(item.baseline.blamed)}</span>: <span className="text-muted-foreground">{item.baseline.action}</span>
              </td>
              <td className={cn("tabular p-2", item.baseline.success ? "text-good" : "text-warn")}>{formatPercent(item.baseline.true_risk_after)}</td>
              <td className="p-2">
                <span className={cn("rounded px-1.5 py-px text-[10px] uppercase", item.winner === "causaltwin" ? "bg-causal/15 text-cyan-200" : item.winner === "baseline" ? "bg-risk/20 text-rose-200" : "bg-white/10 text-slate-300")}>
                  {item.winner}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetricsGrid({ metrics, label }: { metrics: GraphRecoveryMetrics | null | undefined; label: string }) {
  if (!metrics) return null;
  const items: [string, string][] = [
    ["Precision", formatNumber(metrics.precision, 2)],
    ["Recall", formatNumber(metrics.recall, 2)],
    ["F1", formatNumber(metrics.f1, 2)],
    ["Lag accuracy", formatNumber(metrics.lag_accuracy, 2)],
    ["SHD", String(metrics.structural_hamming_distance)],
    ["Edges found", `${metrics.true_positives}/${metrics.n_true_edges}`],
  ];
  return (
    <div>
      <p className="mb-2 text-xs text-muted-foreground">{label}</p>
      <dl className="grid grid-cols-3 gap-2">
        {items.map(([name, value]) => (
          <div key={name} className="rounded-lg border border-line p-2">
            <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{name}</dt>
            <dd className="tabular text-base text-slate-100">{value}</dd>
          </div>
        ))}
      </dl>
      {metrics.missed_edges.length > 0 && <p className="mt-2 text-xs text-amber-200">Missed: {metrics.missed_edges.join(", ")}</p>}
      {metrics.extra_edges.length > 0 && <p className="mt-1 text-xs text-rose-200">Extra: {metrics.extra_edges.join(", ")}</p>}
    </div>
  );
}

function truthAsGraph(truth: TrueGraph, discovered: CausalGraph): CausalGraph {
  const edges: CausalEdge[] = truth.edges
    .filter((edge) => edge.edge_type === "cross")
    .map((edge) => ({
      cause: edge.cause, effect: edge.effect, lag: edge.lag, lags: [edge.lag], strength: 1, p_value: null, method: "truth",
      is_self_loop: false, confidence: null, uncertain: false, oriented: true,
    }));
  return { ...discovered, edges, notes: [] };
}

function GraphComparison({ truth, discovered }: { truth: TrueGraph; discovered: CausalGraph }) {
  const trueGraph = useMemo(() => truthAsGraph(truth, discovered), [truth, discovered]);
  const missing = useMemo(() => {
    const found = new Set(discovered.edges.map((edge) => edgeKey(edge.cause, edge.effect)));
    return trueGraph.edges.filter((edge) => !found.has(edgeKey(edge.cause, edge.effect)));
  }, [trueGraph, discovered]);
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <div>
        <p className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
          True graph <GroundTruthBadge />
        </p>
        <div className="h-72 rounded-lg border border-line">
          <CausalGraphView graph={trueGraph} compact live={false} edgeKind="truth" />
        </div>
      </div>
      <div>
        <p className="mb-1 text-xs text-muted-foreground">Discovered from data (missing true edges in red, dashed)</p>
        <div className="h-72 rounded-lg border border-line">
          <CausalGraphView graph={discovered} compact live={false} missingEdges={missing} />
        </div>
      </div>
    </div>
  );
}

export function Benchmark() {
  const results = useApi(() => api.benchmark(), "benchmark");
  const drift = useApi(() => api.driftResults(), "drift");
  const truth = useApi(() => api.trueGraph(), "truth");
  const discovered = useApi(() => api.graph(), "graph");
  const config = useApi(() => api.config(), "config");
  const progress = useLiveStore((state) => state.benchmarkProgress);
  const reload = results.reload;

  useEffect(
    () =>
      useLiveStore.subscribe((state, previous) => {
        if (state.benchmarkDoneAt !== previous.benchmarkDoneAt) reload();
      }),
    [reload],
  );

  const data = results.data;
  const baseline = data?.baseline_accuracy ?? config.data?.baseline_model.metrics;
  const secondsPerStep = config.data?.seconds_per_step;
  // Steps at which the drift runs injected their faults (read from the results, not assumed).
  const faultSteps = [...new Set((drift.data?.faults ?? []).map((run) => run.fault_step))];
  const driftSubtitle = faultSteps.length
    ? `Faults injected at step ${faultSteps.map((step) => step.toLocaleString("en-US")).join(", ")}${secondsPerStep ? ` (1 step = ${secondsPerStep} s)` : ""}`
    : "Faults injected into otherwise normal runs";
  return (
    <div>
      <PageHeader title="Benchmark: the Correlation Trap" description="Both methods act on the same high-risk moments; the real simulator judges the outcome. Every case is shown, including losses.">
        <SimulatedBadge />
        <GroundTruthBadge />
        <HowComputed provenance={data?.provenance} title="How the benchmark was run" />
        <Button size="sm" onClick={() => void api.benchmarkRun().catch(() => undefined)} disabled={Boolean(progress) || data?.running}>
          <Play aria-hidden /> Re-run benchmark
        </Button>
      </PageHeader>
      {progress && (
        <div className="glass mb-4 p-4" role="status" aria-live="polite">
          <p className="mb-2 text-xs text-slate-300">
            {progress.message} ({progress.done}/{progress.total})
          </p>
          <Progress value={(100 * progress.done) / Math.max(1, progress.total)} />
        </div>
      )}
      {results.error ? (
        <ErrorState message={results.error} onRetry={results.reload} />
      ) : !data ? (
        <PanelSkeleton rows={10} />
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <SummaryCard index={0} title={`CausalTwin (${data.n_cases} cases)`} summary={data.summary.causaltwin} tone="causal" horizonSteps={data.settings.horizon_steps} />
          <SummaryCard index={1} title="Standard ML baseline (XGBoost + SHAP)" summary={data.summary.baseline} tone="warn" horizonSteps={data.settings.horizon_steps} />
          <Panel index={2} title="Outcome comparison" subtitle={`No action: true risk ${formatPercent(data.summary.mean_true_risk_no_action)} on average`} icon={<Trophy className="size-4" />}>
            <ComparisonChart results={data} />
            <p className="text-xs text-muted-foreground">
              Wins: {Object.entries(data.summary.wins).map(([name, count]) => `${humanize(name)} ${count}`).join(", ")}. Cases where CausalTwin is worse:{" "}
              {data.summary.cases_where_causaltwin_is_worse.length === 0 ? "none" : data.summary.cases_where_causaltwin_is_worse.join(", ")}.
            </p>
          </Panel>
          <Panel index={3} title="What each method blamed" subtitle="CausalTwin: top root-cause path. Baseline: top SHAP feature.">
            <BlamedChart results={data} />
          </Panel>
          <Panel index={4} title="Every case" subtitle="Amber: CausalTwin missed the target. Red row: the baseline did better." className="col-span-full">
            <CaseTable results={data} />
          </Panel>
          <Panel index={5} title="Graph recovery (Phase 2 validation)" subtitle="Discovered graph versus the simulator's true graph" className="col-span-full">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <MetricsGrid metrics={data.graph_recovery.graph_recovery_with_failure} label="Including the two failure edges" />
              <MetricsGrid metrics={data.graph_recovery.graph_recovery_without_failure} label="Between sensors and levers only" />
            </div>
            <div className="mt-4">
              {truth.data && discovered.data ? <GraphComparison truth={truth.data} discovered={discovered.data.graph} /> : <PanelSkeleton rows={4} />}
            </div>
          </Panel>
          <Panel index={6} title="Drift detection" subtitle={driftSubtitle}>
            {drift.error ? (
              <EmptyState title="No drift results cached" hint={drift.error} />
            ) : !drift.data ? (
              <PanelSkeleton rows={4} />
            ) : (
              <div className="space-y-3">
                <table className="w-full text-xs">
                  <thead className="text-left text-[10px] uppercase tracking-wider text-muted-foreground">
                    <tr>
                      <th className="p-1.5">Fault</th>
                      <th className="p-1.5">Seed</th>
                      <th className="p-1.5">Detection delay</th>
                      <th className="p-1.5">Rewired after</th>
                      <th className="p-1.5">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {drift.data.faults.map((run) => (
                      <tr key={`${run.fault}-${run.seed}`} className="border-t border-line/60">
                        <td className="p-1.5">{humanize(run.fault)}</td>
                        <td className="tabular p-1.5">{run.seed}</td>
                        <td className="tabular p-1.5">{run.detection_delay_steps ?? "-"} steps</td>
                        <td className="tabular p-1.5">
                          {run.rewire_delay_steps ?? "-"} steps{run.rediscovery_attempts && run.rediscovery_attempts > 1 ? ` (${run.rediscovery_attempts} attempts)` : ""}
                        </td>
                        <td className={cn("p-1.5", run.status === "confirmed" ? "text-good" : "text-warn")}>{run.status ?? "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="text-xs text-muted-foreground">
                  False alarms in fault-free runs:{" "}
                  {drift.data.false_alarms
                    .map((run) => `${run.suspected_alarms} suspected, ${run.wrongly_confirmed_changes} wrongly confirmed in ${run.n_steps.toLocaleString()} steps`)
                    .join("; ")}
                  .
                </p>
              </div>
            )}
          </Panel>
          <Panel index={7} title="Is the baseline fair?" subtitle="Held-out accuracy of the XGBoost failure predictor">
            {!baseline ? (
              <PanelSkeleton rows={3} />
            ) : (
              <dl className="grid grid-cols-2 gap-2 text-xs">
                {(["auc", "precision", "recall", "f1"] as const).map((name) => (
                  <div key={name} className="rounded-lg border border-line p-2">
                    <dt className="uppercase tracking-wider text-muted-foreground">{name}</dt>
                    <dd className="tabular text-xl text-slate-100">{formatNumber(Number(baseline[name]), 3)}</dd>
                  </div>
                ))}
                <p className="col-span-2 text-muted-foreground">
                  Target: {String(baseline.target)}; positive rate in the test period {formatPercent(Number(baseline.positive_rate_test))}; trained in{" "}
                  {formatNumber(Number(baseline.training_seconds), 1)} s.
                </p>
              </dl>
            )}
          </Panel>
          <Panel index={8} title="Methodology" className="col-span-full">
            <div className="grid grid-cols-1 gap-4 text-xs leading-relaxed text-slate-300 lg:grid-cols-3">
              <p>
                <span className="font-semibold text-slate-100">Situations.</span> High-risk moments are found in all{" "}
                {Object.keys(data.per_scenario).length} scenarios on different seeds: the simulator&apos;s true hazard passes a screen, then the
                true probability of failure within {data.settings.horizon_steps} steps
                {secondsPerStep ? ` (${formatNumber((data.settings.horizon_steps * secondsPerStep) / 60, 0)} minutes)` : ""}, measured on forks
                of the real simulator, must be at least {formatPercent(data.settings.min_true_risk, 0)}.
              </p>
              <p>
                <span className="font-semibold text-slate-100">Actions.</span> CausalTwin runs its optimizer. The baseline blames its top
                SHAP feature and maps it to an engineer&apos;s action (for example vibration becomes damping, which removes vibration
                noise). Both actions are applied to forks of the real simulator from the same state with the same{" "}
                {data.settings.seeds_per_situation} seeds.
              </p>
              <p>
                <span className="font-semibold text-slate-100">Scoring.</span> Success means the true risk after the action is at or below
                the {formatPercent(data.settings.target_risk, 0)} target. Failure rates come from futures with failures enabled. Costs
                are illustrative units. The benchmark took {formatNumber(data.timing_seconds.total, 1)} s.
              </p>
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}
