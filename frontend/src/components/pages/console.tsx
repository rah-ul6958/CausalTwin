"use client";

/** System Console: pipeline tracker, live activity log, model cards, configuration and system metrics. */

import { Cpu, Pause, Play, Settings, Terminal, Workflow } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { EmptyState, ErrorState, LoadingOrError, PanelSkeleton } from "@/components/common/states";
import { DataSourceBadge } from "@/components/transparency/transparency";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";
import { formatMs, formatNumber, formatPercent, formatTime, humanize } from "@/lib/format";
import { MAX_ROUTINE_LOGS } from "@/lib/config";
import { mergeLogs, useLiveStore } from "@/lib/store";
import type { AppConfig, LogEntry, Metrics } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

const STAGES = [
  { name: "Ingest", steps: ["align_events", "load_csv"], budget: null },
  { name: "Discover", steps: ["discovery", "rediscovery"], budget: "discovery" },
  { name: "Fit", steps: ["fit_model", "refit_model"], budget: "fit_model" },
  { name: "Simulate", steps: ["what_if"], budget: "what_if" },
  { name: "Optimize", steps: ["recommend"], budget: "recommend" },
  { name: "Drift check", steps: ["rediscovery", "alert_drift"], budget: null },
] as const;

/** One card per pipeline stage: last duration against its budget, from this session or the validation run. */
function PipelineTracker({ logs, metrics, config, validationTimings }: {
  logs: LogEntry[];
  metrics: Metrics | undefined;
  config: AppConfig | undefined;
  validationTimings: Record<string, number> | undefined;
}) {
  return (
    <Panel index={0} title="Pipeline" subtitle="Last run and duration against the performance budget" icon={<Workflow className="size-4" />} className="col-span-full">
      <ol className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {STAGES.map((stage) => {
          const last = [...logs].reverse().find((entry) => (stage.steps as readonly string[]).includes(entry.step_name));
          const sessionDuration = last?.duration_ms ?? stage.steps.map((step) => metrics?.latest_step_durations_ms[step]).find((value) => value !== undefined) ?? null;
          // The server loads fitted models from its cache, so discovery and fitting may not have run
          // in this session; then the timing measured by the validation run is shown, labelled as such.
          const validationDuration = stage.steps.map((step) => validationTimings?.[step]).find((value) => value !== undefined) ?? null;
          const fromValidation = sessionDuration === null && validationDuration !== null;
          const duration = sessionDuration ?? validationDuration;
          const budget = stage.budget ? config?.budgets_ms[stage.budget] : undefined;
          const over = budget !== undefined && duration !== null && duration > budget;
          const driftState = stage.name === "Drift check" ? metrics?.status.drift_state : undefined;
          return (
            <li key={stage.name} className={cn("rounded-lg border p-3", over ? "border-warn/50 bg-warn/5" : "border-line")}>
              <div className="flex items-center justify-between">
                <span className="font-heading text-sm text-slate-100">{stage.name}</span>
                <span className={cn("size-2 rounded-full", duration !== null || driftState ? (over ? "bg-warn" : "bg-good") : "bg-slate-600")} aria-hidden />
              </div>
              <p className="tabular mt-2 text-lg text-slate-100">{formatMs(duration)}</p>
              <p className="text-[11px] text-muted-foreground">{budget !== undefined ? `budget ${formatMs(budget)}` : driftState ? `state: ${driftState}` : "no budget"}</p>
              <p className="text-[11px] text-muted-foreground">
                {last ? `last run ${formatTime(last.timestamp)}` : fromValidation ? "from the validation run (models loaded from cache)" : "not run in this session"}
              </p>
              {over && <p className="mt-1 text-[11px] text-warn">Over budget on {fromValidation ? "the validation run" : "this run"}</p>}
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}

/** The live activity log with filters; per-step risk estimates are hidden unless switched on. */
function LogStream() {
  const mainLogs = useLiveStore((state) => state.logs);
  const routineLogs = useLiveStore((state) => state.routineLogs);
  const [showRoutine, setShowRoutine] = useState(false);
  const logs = useMemo(() => (showRoutine ? mergeLogs(mainLogs, routineLogs) : mainLogs), [showRoutine, mainLogs, routineLogs]);
  const [step, setStep] = useState("all");
  const [level, setLevel] = useState("all");
  const [search, setSearch] = useState("");
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState<LogEntry[] | null>(null);
  const container = useRef<HTMLDivElement>(null);
  const source = paused && frozen ? frozen : logs;
  const steps = useMemo(() => [...new Set(logs.map((entry) => entry.step_name))].sort(), [logs]);
  const shown = useMemo(
    () =>
      source
        .filter((entry) => (step === "all" || entry.step_name === step) && (level === "all" || entry.level === level))
        .filter((entry) => !search || entry.message.toLowerCase().includes(search.toLowerCase()))
        .slice(-400),
    [source, step, level, search],
  );
  useEffect(() => {
    if (!paused && container.current) container.current.scrollTop = container.current.scrollHeight;
  }, [shown, paused]);
  return (
    <Panel
      index={1}
      title="Activity log"
      subtitle="Every pipeline step with its duration, streamed live"
      icon={<Terminal className="size-4" />}
      className="col-span-full xl:col-span-2"
      actions={
        <Button size="xs" variant="outline" onClick={() => { setFrozen(paused ? null : logs); setPaused((value) => !value); }} aria-pressed={paused}>
          {paused ? <Play aria-hidden /> : <Pause aria-hidden />} {paused ? "Resume auto-scroll" : "Pause"}
        </Button>
      }
    >
      <div className="mb-3 flex flex-wrap gap-2">
        <Select value={step} onValueChange={setStep}>
          <SelectTrigger className="w-44" aria-label="Filter by step">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All steps</SelectItem>
            {steps.map((name) => (
              <SelectItem key={name} value={name}>
                {name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={level} onValueChange={setLevel}>
          <SelectTrigger className="w-32" aria-label="Filter by level">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All levels</SelectItem>
            <SelectItem value="info">Info</SelectItem>
            <SelectItem value="warning">Warning</SelectItem>
            <SelectItem value="error">Error</SelectItem>
          </SelectContent>
        </Select>
        <Input placeholder="Search messages" value={search} onChange={(event) => setSearch(event.target.value)} className="w-56" aria-label="Search log messages" />
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Switch id="show-routine" checked={showRoutine} onCheckedChange={setShowRoutine} />
          <Label htmlFor="show-routine" className="text-xs font-normal text-muted-foreground">
            Per-step risk estimates (last {MAX_ROUTINE_LOGS})
          </Label>
        </div>
      </div>
      <div ref={container} className="h-80 overflow-y-auto rounded-lg border border-line bg-black/40 p-2 font-mono text-[11px] leading-5" role="log" aria-live="off" aria-label="Activity log entries" tabIndex={0}>
        {shown.length === 0 ? (
          <EmptyState title="No log entries match" />
        ) : (
          shown.map((entry, index) => (
            <div key={`${entry.timestamp}-${index}`} className={cn("flex gap-2", entry.level === "warning" && "text-amber-200", entry.level === "error" && "text-rose-300")}>
              <span className="shrink-0 text-slate-500">{formatTime(entry.timestamp)}</span>
              <span className="w-36 shrink-0 truncate text-cyan-300">{entry.step_name}</span>
              <span className="w-16 shrink-0 text-right text-slate-400">{entry.duration_ms !== null ? formatMs(entry.duration_ms) : ""}</span>
              <span className="truncate text-slate-200">{entry.message}</span>
            </div>
          ))
        )}
      </div>
    </Panel>
  );
}

function SystemMetrics({ metrics, request }: { metrics: Metrics | undefined; request: { error?: string; reload: () => void } }) {
  const counts = metrics?.counts ?? {};
  const numeric = Object.entries(counts).filter((entry): entry is [string, number] => typeof entry[1] === "number");
  const messages = counts.messages_sent;
  return (
    <Panel index={2} title="System metrics" subtitle="Refreshed every 2 s" icon={<Cpu className="size-4" />}>
      {!metrics ? (
        <LoadingOrError state={request} rows={5} />
      ) : (
        <div className="space-y-3 text-xs">
          <dl className="grid grid-cols-2 gap-2">
            {numeric.map(([name, value]) => (
              <div key={name} className="rounded-lg border border-line p-2">
                <dt className="text-muted-foreground">{humanize(name)}</dt>
                <dd className="tabular text-base text-slate-100">{value.toLocaleString()}</dd>
              </div>
            ))}
          </dl>
          {messages && typeof messages === "object" && (
            <p className="text-muted-foreground">
              WebSocket messages: {Object.entries(messages).map(([type, count]) => `${type} ${count}`).join(", ")}
            </p>
          )}
          <p className="text-muted-foreground">
            Models trained in {formatNumber(metrics.training.seconds, 1)} s on {metrics.training.rows?.toLocaleString()} rows with {metrics.training.failures} failures.
          </p>
        </div>
      )}
    </Panel>
  );
}

function ModelCards({ config }: { config: AppConfig | undefined }) {
  const cards = useApi(() => api.modelCards(), "cards");
  return (
    <Panel index={3} title="Model cards" subtitle="Every fitted equation, plus the baseline model" className="col-span-full">
      {cards.error ? (
        <ErrorState message={cards.error} onRetry={cards.reload} />
      ) : !cards.data ? (
        <PanelSkeleton rows={4} />
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-3">
          {cards.data.cards.map((card) => (
            <article key={card.variable} className="rounded-lg border border-line p-3">
              <div className="flex items-center justify-between">
                <h3 className="font-heading text-sm text-slate-100">{humanize(card.variable)}</h3>
                <span className="rounded bg-white/5 px-1.5 text-[10px] uppercase text-muted-foreground">{card.kind}</span>
              </div>
              <p className="mt-2 break-words font-mono text-[11px] leading-relaxed text-cyan-100">{card.equation}</p>
              <p className="tabular mt-2 text-[11px] text-muted-foreground">
                {card.kind === "logistic" ? `held-out AUC ${formatNumber(card.auc_holdout, 3)}` : `held-out R squared ${formatNumber(card.r_squared_holdout, 3)}, residual std ${formatNumber(card.residual_std, 3)}`}{" "}
                - {card.n_train.toLocaleString()} train / {card.n_holdout.toLocaleString()} holdout rows
              </p>
            </article>
          ))}
          {config && (
            <article className="rounded-lg border border-warn/30 p-3">
              <div className="flex items-center justify-between">
                <h3 className="font-heading text-sm text-amber-100">{config.baseline_model.name}</h3>
                <span className="rounded bg-warn/10 px-1.5 text-[10px] uppercase text-warn">baseline</span>
              </div>
              <p className="tabular mt-2 text-[11px] text-slate-300">
                AUC {formatNumber(Number(config.baseline_model.metrics.auc), 3)}, precision {formatNumber(Number(config.baseline_model.metrics.precision), 3)}, recall{" "}
                {formatNumber(Number(config.baseline_model.metrics.recall), 3)} on the chronological final{" "}
                {formatPercent(Number(config.baseline_model.metrics.n_test) / (Number(config.baseline_model.metrics.n_train) + Number(config.baseline_model.metrics.n_test)), 0)}{" "}
                of the data
              </p>
              <p className="mt-1 text-[11px] text-muted-foreground">Target: {String(config.baseline_model.metrics.target)}. Features: {config.baseline_model.features.join(", ")}.</p>
              <p className="mt-1 text-[11px] text-muted-foreground">
                Action mapping: {config.baseline_model.action_mapping.map((item) => `${humanize(item.feature)} -> ${item.lever ? `${item.action} ${item.lever}` : item.action}`).join("; ")}
              </p>
            </article>
          )}
        </div>
      )}
    </Panel>
  );
}

function ConfigViewer({ config, request }: { config: AppConfig | undefined; request: { error?: string; reload: () => void } }) {
  const sections: [string, Record<string, unknown>][] = config
    ? [
        ["Engine", config.engine],
        ["Optimizer", config.optimizer],
        ["Drift detection", config.drift],
        ["Live loop", config.live],
        ["Budgets (ms)", config.budgets_ms],
      ]
    : [];
  return (
    <Panel index={4} title="Configuration" subtitle="Current settings (single source of truth: backend config.py)" icon={<Settings className="size-4" />} className="col-span-full">
      {!config ? (
        <LoadingOrError state={request} rows={6} />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
          {sections.map(([title, values]) => (
            <section key={title}>
              <h3 className="mb-2 text-xs uppercase tracking-wider text-muted-foreground">{title}</h3>
              <dl className="space-y-1 font-mono text-[11px]">
                {Object.entries(values).map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-2">
                    <dt className="truncate text-slate-400">{key}</dt>
                    <dd className="text-right text-slate-100">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      )}
      {config && <p className="mt-3 text-[11px] text-muted-foreground">{config.illustrative_note}</p>}
    </Panel>
  );
}

export function SystemConsole() {
  const config = useApi(() => api.config(), "config");
  const metrics = useApi(() => api.metrics(), "metrics", 2000);
  const validation = useApi(() => api.validation(), "validation");
  const mainLogs = useLiveStore((state) => state.logs);
  const routineLogs = useLiveStore((state) => state.routineLogs);
  const logs = useMemo(() => mergeLogs(mainLogs, routineLogs), [mainLogs, routineLogs]);
  return (
    <div>
      <PageHeader title="System Console" description="Every process, its timing against the budget, and every model, in the open.">
        <DataSourceBadge />
      </PageHeader>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <PipelineTracker logs={logs} metrics={metrics.data} config={config.data} validationTimings={validation.data?.timings_ms} />
        <LogStream />
        <SystemMetrics metrics={metrics.data} request={metrics} />
        <ModelCards config={config.data} />
        <ConfigViewer config={config.data} request={config} />
      </div>
    </div>
  );
}
