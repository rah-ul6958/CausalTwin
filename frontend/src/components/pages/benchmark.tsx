"use client";

/** Benchmark: the Correlation Trap judged by the real simulator, plus graph recovery and drift results. */

import { motion, useReducedMotion } from "framer-motion";
import { Play, ShieldAlert, Trophy } from "lucide-react";
import { useEffect, useMemo } from "react";
import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { CountUp } from "@/components/common/count-up";
import { PageHeader } from "@/components/common/page-header";
import { Takeaway } from "@/components/common/takeaway";
import { Panel } from "@/components/common/panel";
import { EmptyState, ErrorState, LoadingOrError, PanelSkeleton } from "@/components/common/states";
import { CausalGraphView, edgeKey } from "@/components/graph/causal-graph";
import { GroundTruthBadge, HowComputed, SimulatedBadge } from "@/components/transparency/transparency";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { api } from "@/lib/api";
import { formatNumber, formatPercent, humanize } from "@/lib/format";
import { useLiveStore } from "@/lib/store";
import type {
  BaselineMethod,
  BenchmarkResults,
  CausalEdge,
  CausalGraph,
  GraphRecoveryMetrics,
  MethodResult,
  MethodSummary,
  TrueGraph,
} from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

/** Chart colors per method: CausalTwin cyan, the baselines from amber (strongest) to grey. */
const METHOD_COLORS: Record<BaselineMethod | "causaltwin", string> = {
  causaltwin: "#22d3ee",
  counterfactual: "#fbbf24",
  actionable_shap: "#fb923c",
  naive_shap: "#94a3b8",
};

/** Baseline methods, the main (strongest) one first, then the others from strongest to weakest. */
function baselineOrder(results: BenchmarkResults): BaselineMethod[] {
  const main = results.summary.main_baseline;
  const others = (Object.keys(results.summary.baseline_labels) as BaselineMethod[]).reverse().filter((method) => method !== main);
  return [main, ...others];
}

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
        <div>
          <dt className="text-xs text-muted-foreground">Made things worse than doing nothing</dt>
          <dd className={cn("tabular text-lg", summary.n_worse_than_no_action ? "text-rose-200" : "text-slate-100")}>{summary.n_worse_than_no_action}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Said no action while risk was high</dt>
          <dd className={cn("tabular text-lg", summary.n_false_reassurance ? "text-rose-200" : "text-slate-100")}>{summary.n_false_reassurance}</dd>
        </div>
      </dl>
    </Panel>
  );
}

/** Every method side by side: success, risk, cost and the two warning counts. */
function MethodComparisonTable({ results }: { results: BenchmarkResults }) {
  const order = baselineOrder(results);
  const total = results.n_cases;
  const columns = [
    { key: "causaltwin", label: "CausalTwin", summary: results.summary.causaltwin },
    ...order.map((method) => ({ key: method, label: results.summary.baseline_labels[method], summary: results.summary.baselines[method] })),
  ];
  const rows: { name: string; render: (summary: MethodSummary) => string; warn?: boolean }[] = [
    { name: `Reached the ${formatPercent(results.settings.target_risk, 0)} target`, render: (s) => `${s.n_success} / ${total}` },
    { name: "Mean true risk after", render: (s) => formatPercent(s.mean_true_risk_after, 1) },
    { name: "Mean risk reduction", render: (s) => `${formatNumber(s.mean_risk_reduction * 100, 1)} pts` },
    { name: `Failure rate within ${results.settings.horizon_steps} steps`, render: (s) => formatPercent(s.mean_failure_rate_after, 1) },
    { name: "Mean cost (illustrative)", render: (s) => formatNumber(s.mean_cost, 1) },
    { name: "Made things worse than doing nothing", render: (s) => String(s.n_worse_than_no_action), warn: true },
    { name: "Said no action while the true risk was above the target", render: (s) => String(s.n_false_reassurance), warn: true },
  ];
  return (
    <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Method comparison table, scrollable">
      <table className="w-full text-xs">
        <thead className="text-left text-[10px] uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="p-2">Metric</th>
            {columns.map((column) => (
              <th key={column.key} className="p-2">
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden className="size-2 rounded-full" style={{ background: METHOD_COLORS[column.key as BaselineMethod | "causaltwin"] }} />
                  {column.label}
                  {column.key === results.summary.main_baseline && <span className="rounded bg-warn/15 px-1 text-[9px] normal-case text-amber-200">main</span>}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.name} className="border-t border-line/60">
              <td className="p-2 text-slate-300">{row.name}</td>
              {columns.map((column) => {
                const text = row.render(column.summary);
                return (
                  <td key={column.key} className={cn("tabular p-2", column.key === "causaltwin" ? "text-cyan-100" : "text-slate-100", row.warn && text !== "0" && "text-rose-200")}>
                    {text}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The cases where a predictive baseline made things worse than doing nothing, or said "no action" at high risk. */
function WarningCallout({ results }: { results: BenchmarkResults }) {
  const order = baselineOrder(results);
  const target = formatPercent(results.settings.target_risk, 0);
  const riskOf = (caseId: number) => results.cases.find((item) => item.case_id === caseId)?.true_risk_no_action;
  const ct = results.summary.causaltwin;
  const worseTotal = order.reduce((sum, method) => sum + results.summary.baselines[method].n_worse_than_no_action, 0);
  const reassuranceTotal = order.reduce((sum, method) => sum + results.summary.baselines[method].n_false_reassurance, 0);
  const caseList = (ids: number[]) =>
    ids.map((id) => `#${id}${riskOf(id) !== undefined ? ` (${formatPercent(riskOf(id) ?? 0, 0)})` : ""}`).join(", ");
  return (
    <section aria-label="Where predictive baselines did harm" className="mb-4 rounded-2xl border border-risk/40 bg-risk/[0.07] p-4">
      <p className="flex items-center gap-2 text-sm font-medium text-rose-100">
        <ShieldAlert aria-hidden className="size-4" />
        Predictive baselines made things worse than doing nothing {worseTotal} time(s) and said &quot;no action&quot; at high risk {reassuranceTotal} time(s).
        CausalTwin: {ct.n_worse_than_no_action} and {ct.n_false_reassurance}.
      </p>
      <div className="mt-3 grid grid-cols-1 gap-3 text-xs md:grid-cols-3">
        {order.map((method) => {
          const summary = results.summary.baselines[method];
          return (
            <div key={method} className="rounded-lg border border-line bg-black/20 p-3">
              <p className="mb-1 font-medium text-slate-100">{results.summary.baseline_labels[method]}</p>
              <p className="text-slate-300">
                <span className="text-rose-200">Worse than doing nothing:</span>{" "}
                {summary.cases_worse_than_no_action.length ? caseList(summary.cases_worse_than_no_action) : "none"}
              </p>
              <p className="mt-1 text-slate-300">
                <span className="text-rose-200">No action while true risk was above {target}:</span>{" "}
                {summary.cases_false_reassurance.length ? caseList(summary.cases_false_reassurance) : "none"}
              </p>
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">
        Case numbers, with the true risk if nothing is done. &quot;Worse&quot; means the true risk after the action is more than 1 point above doing nothing.
      </p>
    </section>
  );
}

function BlamedChart({ results }: { results: BenchmarkResults }) {
  const data = useMemo(() => {
    const naive = results.summary.baselines.naive_shap.blamed;
    const actionable = results.summary.baselines.actionable_shap.blamed;
    const names = new Set([...Object.keys(results.summary.causaltwin.blamed), ...Object.keys(naive), ...Object.keys(actionable)]);
    return [...names].map((name) => ({
      name: name.split(" -> ").map(humanize).join(" > "),
      CausalTwin: results.summary.causaltwin.blamed[name] ?? 0,
      [results.summary.baseline_labels.naive_shap]: naive[name] ?? 0,
      [results.summary.baseline_labels.actionable_shap]: actionable[name] ?? 0,
    }));
  }, [results]);
  return (
    <div className="h-72">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 40 }}>
          <XAxis type="number" allowDecimals={false} tick={{ fontSize: 9, fill: "#64748b" }} />
          <YAxis type="category" dataKey="name" width={170} tick={{ fontSize: 10, fill: "#cbd5e1" }} />
          <Tooltip contentStyle={{ background: "#0b1120", border: "1px solid rgb(148 163 184 / 0.2)", fontSize: 11 }} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="CausalTwin" fill={METHOD_COLORS.causaltwin} isAnimationActive={false} />
          <Bar dataKey={results.summary.baseline_labels.actionable_shap} fill={METHOD_COLORS.actionable_shap} isAnimationActive={false} />
          <Bar dataKey={results.summary.baseline_labels.naive_shap} fill={METHOD_COLORS.naive_shap} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function ComparisonChart({ results }: { results: BenchmarkResults }) {
  const order = baselineOrder(results);
  const labels = results.summary.baseline_labels;
  const row = (metric: string, pick: (summary: MethodSummary) => number, noAction: number) => ({
    metric,
    CausalTwin: pick(results.summary.causaltwin),
    ...Object.fromEntries(order.map((method) => [labels[method], pick(results.summary.baselines[method])])),
    "No action": noAction,
  });
  const data = [
    row("Success rate", (s) => s.success_rate, 0),
    row("True risk after", (s) => s.mean_true_risk_after, results.summary.mean_true_risk_no_action),
    row("Failure rate", (s) => s.mean_failure_rate_after, results.summary.mean_failure_rate_no_action),
  ];
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
          <XAxis dataKey="metric" tick={{ fontSize: 10, fill: "#cbd5e1" }} />
          <YAxis tick={{ fontSize: 9, fill: "#64748b" }} tickFormatter={(value) => formatPercent(Number(value), 0)} />
          <Tooltip contentStyle={{ background: "#0b1120", border: "1px solid rgb(148 163 184 / 0.2)", fontSize: 11 }} formatter={(value) => formatPercent(Number(value), 1)} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="CausalTwin" fill={METHOD_COLORS.causaltwin} isAnimationActive={false} />
          {order.map((method) => (
            <Bar key={method} dataKey={labels[method]} fill={METHOD_COLORS[method]} isAnimationActive={false} />
          ))}
          <Bar dataKey="No action" fill="#475569" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** One baseline's action and outcome in one case, with its warning flags. */
function BaselineCell({ result }: { result: MethodResult }) {
  return (
    <td className="p-2 align-top">
      <span className={cn("tabular block", result.success ? "text-good" : "text-warn")}>{formatPercent(result.true_risk_after)}</span>
      <span className="block max-w-[220px] text-[11px] text-muted-foreground">{result.action}</span>
      {result.worse_than_no_action && <span className="mt-0.5 inline-block rounded bg-risk/20 px-1 text-[10px] text-rose-200">worse than nothing</span>}
      {result.false_reassurance && <span className="mt-0.5 inline-block rounded bg-risk/20 px-1 text-[10px] text-rose-200">no action at high risk</span>}
    </td>
  );
}

function CaseTable({ results }: { results: BenchmarkResults }) {
  const order = baselineOrder(results);
  return (
    <div className="max-h-[520px] overflow-auto" tabIndex={0} role="region" aria-label="Every benchmark case, scrollable">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-[#0b1222] text-left text-[10px] uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="p-2">#</th>
            <th className="p-2">Scenario</th>
            <th className="p-2">True risk, no action</th>
            <th className="p-2">CausalTwin: risk after / action</th>
            {order.map((method) => (
              <th key={method} className="p-2">{results.summary.baseline_labels[method]}: risk after / action</th>
            ))}
            <th className="p-2">Winner vs {results.summary.baseline_labels[results.summary.main_baseline].split(":")[0]}</th>
          </tr>
        </thead>
        <tbody>
          {results.cases.map((item) => (
            <tr key={item.case_id} className={cn("border-t border-line/60", item.winner === "baseline" && "bg-risk/10", !item.causaltwin.success && "text-amber-100")}>
              <td className="tabular p-2 align-top text-muted-foreground">{item.case_id}</td>
              <td className="p-2 align-top">{humanize(item.scenario)}</td>
              <td className="tabular p-2 align-top">{formatPercent(item.true_risk_no_action)}</td>
              <td className="p-2 align-top">
                <span className={cn("tabular block", item.causaltwin.success ? "text-good" : "text-warn")}>{formatPercent(item.causaltwin.true_risk_after)}</span>
                <span className="block text-[11px] text-slate-300">{item.causaltwin.action}</span>
              </td>
              {order.map((method) => (
                <BaselineCell key={method} result={item.baselines[method]} />
              ))}
              <td className="p-2 align-top">
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

/** Head-to-head result: both success rates, animated, with the losses stated next to them. */
function VerdictStrip({ results }: { results: BenchmarkResults }) {
  const reduceMotion = useReducedMotion();
  const cases = results.n_cases;
  const target = formatPercent(results.settings.target_risk, 0);
  const order = baselineOrder(results);
  const baselineBars = ["from-amber-500 to-amber-300", "from-orange-500 to-orange-300", "from-slate-500 to-slate-400"];
  const rows = [
    { name: "CausalTwin", summary: results.summary.causaltwin, bar: "from-cyan-400 to-sky-400", text: "text-cyan-100" },
    ...order.map((method, index) => ({
      name: results.summary.baseline_labels[method],
      summary: results.summary.baselines[method],
      bar: baselineBars[index] ?? baselineBars[baselineBars.length - 1],
      text: index === 0 ? "text-amber-100" : "text-slate-200",
    })),
  ];
  const missed = cases - results.summary.causaltwin.n_success;
  const worse = results.summary.cases_where_causaltwin_is_worse.length;
  const mainLabel = results.summary.baseline_labels[results.summary.main_baseline];
  return (
    <section aria-label="Head-to-head result" className="glass mb-4 grid grid-cols-1 gap-6 p-5 lg:grid-cols-[1.4fr_1fr]">
      <div className="space-y-4">
        <p className="hud-label">Cases brought to {target} true risk or less, judged by the simulator</p>
        {rows.map((row, index) => (
          <div key={row.name}>
            <div className="mb-1.5 flex items-baseline justify-between">
              <span className="text-sm text-slate-200">{row.name}</span>
              <span className={cn("tabular text-2xl font-medium", row.text)}>
                <CountUp value={row.summary.success_rate} format={(value) => formatPercent(value, 0)} durationMs={1200} />
                <span className="ml-2 text-xs text-muted-foreground">{row.summary.n_success} / {cases}</span>
              </span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-white/[0.05]">
              <motion.div
                className={cn("h-full rounded-full bg-gradient-to-r", row.bar)}
                initial={reduceMotion ? false : { width: 0 }}
                whileInView={{ width: `${row.summary.success_rate * 100}%` }}
                viewport={{ once: true }}
                transition={{ duration: 1.1, delay: index * 0.15, ease: [0.2, 0.8, 0.2, 1] }}
                style={reduceMotion ? { width: `${row.summary.success_rate * 100}%` } : undefined}
              />
            </div>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-3 self-center">
        <div className="rounded-xl border border-warn/30 bg-warn/[0.06] p-3">
          <p className="hud-label">CausalTwin missed</p>
          <p className="tabular mt-1 text-2xl text-amber-100">{missed}</p>
          <p className="text-[11px] text-muted-foreground">case(s) above the target after its action</p>
        </div>
        <div className="rounded-xl border border-line bg-white/[0.02] p-3">
          <p className="hud-label">{mainLabel.split(":")[0]} did better</p>
          <p className="tabular mt-1 text-2xl text-slate-100">{worse}</p>
          <p className="text-[11px] text-muted-foreground">case(s) with a lower true risk, red rows in the table below</p>
        </div>
      </div>
    </section>
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
  const main = data?.summary.main_baseline;
  const mainSummary = data && main ? data.summary.baselines[main] : undefined;
  const mainLabel = data && main ? data.summary.baseline_labels[main] : "";
  return (
    <div>
      <PageHeader eyebrow="Module 05 / Scientific benchmark" title="Benchmark: the Correlation Trap" description="CausalTwin and three predictive baselines act on the same risky moments, and the simulator judges which action really prevented failure. Every case is shown, losses included.">
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
      ) : !data || !mainSummary ? (
        <PanelSkeleton rows={10} />
      ) : (
        <>
        <Takeaway tone="good">
          CausalTwin prevented failure in {data.summary.causaltwin.n_success} of {data.n_cases} risky moments. The strongest predictive
          baseline ({mainLabel}: the same candidate actions, scored by XGBoost) managed {mainSummary.n_success}; it said &quot;no
          action&quot; in {mainSummary.n_false_reassurance} case(s) where the true risk was above the target and made things worse than
          doing nothing in {mainSummary.n_worse_than_no_action}. The naive SHAP rule mostly blamed vibration (
          {data.summary.baselines.naive_shap.blamed.vibration ?? 0} cases), which does not cause failure.
        </Takeaway>
        <VerdictStrip results={data} />
        <WarningCallout results={data} />
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <Panel index={0} title="Method comparison" subtitle={`All four methods on the same ${data.n_cases} cases, the same forks and the same seeds`} className="col-span-full">
            <MethodComparisonTable results={data} />
          </Panel>
          <SummaryCard index={1} title={`CausalTwin (${data.n_cases} cases)`} summary={data.summary.causaltwin} tone="causal" horizonSteps={data.settings.horizon_steps} />
          <SummaryCard index={2} title={`Strongest predictive baseline (${mainLabel})`} summary={mainSummary} tone="warn" horizonSteps={data.settings.horizon_steps} />
          <Panel index={3} title="Outcome comparison" subtitle={`No action: true risk ${formatPercent(data.summary.mean_true_risk_no_action)} on average`} icon={<Trophy className="size-4" />}>
            <ComparisonChart results={data} />
            <p className="text-xs text-muted-foreground">
              Against {mainLabel}: {Object.entries(data.summary.wins).map(([name, count]) => `${humanize(name)} ${count}`).join(", ")}. Cases where CausalTwin is worse:{" "}
              {data.summary.cases_where_causaltwin_is_worse.length === 0 ? "none" : data.summary.cases_where_causaltwin_is_worse.join(", ")}.
            </p>
          </Panel>
          <Panel index={4} title="What each method blamed" subtitle="CausalTwin: top root-cause path. B1: top SHAP feature. B2: top SHAP feature among levers and tool wear.">
            <BlamedChart results={data} />
          </Panel>
          <Panel index={5} title="Every case" subtitle={`Green: reached the target. Amber row: CausalTwin missed the target. Red row: ${mainLabel.split(":")[0]} left a lower true risk.`} className="col-span-full">
            <CaseTable results={data} />
          </Panel>
          <Panel index={6} expert title="Graph recovery (Phase 2 validation)" subtitle="Discovered graph versus the simulator's true graph" className="col-span-full">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <MetricsGrid metrics={data.graph_recovery.graph_recovery_with_failure} label="Including the two failure edges" />
              <MetricsGrid metrics={data.graph_recovery.graph_recovery_without_failure} label="Between sensors and levers only" />
            </div>
            <div className="mt-4">
              {truth.data && discovered.data ? (
                <GraphComparison truth={truth.data} discovered={discovered.data.graph} />
              ) : (
                <LoadingOrError state={truth.error ? truth : discovered} rows={4} />
              )}
            </div>
          </Panel>
          <Panel index={7} title="Drift detection" subtitle={driftSubtitle}>
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
          <Panel index={8} title="Is the baseline fair?" subtitle="Held-out accuracy of the XGBoost failure predictor that all three baselines use">
            {!baseline ? (
              <LoadingOrError state={config} rows={3} />
            ) : (
              <div className="space-y-2 text-xs">
                <dl className="grid grid-cols-2 gap-2">
                  {(["auc", "precision", "recall", "f1"] as const).map((name) => (
                    <div key={name} className="rounded-lg border border-line p-2">
                      <dt className="uppercase tracking-wider text-muted-foreground">{name}</dt>
                      <dd className="tabular text-xl text-slate-100">{formatNumber(Number(baseline[name]), 3)}</dd>
                    </div>
                  ))}
                </dl>
                <p className="text-muted-foreground">
                  Target: {String(baseline.target)}; positive rate in the test period {formatPercent(Number(baseline.positive_rate_test))}; trained in{" "}
                  {formatNumber(Number(baseline.training_seconds), 1)} s.
                </p>
              </div>
            )}
          </Panel>
          <Panel index={9} expert title="Methodology" className="col-span-full">
            <div className="grid grid-cols-1 gap-4 text-xs leading-relaxed text-slate-300 lg:grid-cols-3">
              <p>
                <span className="font-semibold text-slate-100">Situations.</span> High-risk moments are found in all{" "}
                {Object.keys(data.per_scenario).length} scenarios on different seeds: the simulator&apos;s true hazard passes a screen, then the
                true probability of failure within {data.settings.horizon_steps} steps
                {secondsPerStep ? ` (${formatNumber((data.settings.horizon_steps * secondsPerStep) / 60, 0)} minutes)` : ""}, measured on forks
                of the real simulator, must be at least {formatPercent(data.settings.min_true_risk, 0)}.
              </p>
              <p>
                <span className="font-semibold text-slate-100">Actions.</span> CausalTwin runs its optimizer. B3, the main baseline, scores the
                same candidate actions (same grid, costs, target and planning margin) with the XGBoost failure predictor, which can only change a
                lever&apos;s value in its input row. B2 blames the top SHAP feature among the levers and tool wear. B1 blames the top SHAP
                feature and maps it to an engineer&apos;s action (for example vibration becomes damping, which removes vibration noise). All
                actions are applied to forks of the real simulator from the same state with the same {data.settings.seeds_per_situation} seeds.
              </p>
              <p>
                <span className="font-semibold text-slate-100">Scoring.</span> Success means the true risk after the action is at or below
                the {formatPercent(data.settings.target_risk, 0)} target. &quot;Worse than doing nothing&quot; means the true risk after
                the action is more than 1 point above the no-action risk. Failure rates come from futures with failures enabled. Costs
                are illustrative units. The benchmark took {formatNumber(data.timing_seconds.total, 1)} s.
              </p>
            </div>
          </Panel>
        </div>
        </>
      )}
    </div>
  );
}
