"use client";

/** Data: raw live data and events, data quality, CSV export, and Bring Your Own Data. */

import { useVirtualizer } from "@tanstack/react-virtual";
import { Database, Download, RefreshCw, TriangleAlert, Upload } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { EmptyState, ErrorState, PanelSkeleton } from "@/components/common/states";
import { CausalGraphView } from "@/components/graph/causal-graph";
import { HowComputed, DataSourceBadge } from "@/components/transparency/transparency";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";
import { formatNumber, humanize } from "@/lib/format";
import type { ByodResponse, ColumnQuality, SensorRow } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useDataSource } from "@/lib/data-source";
import { useSessionId } from "@/lib/use-session";
import { cn } from "@/lib/utils";

const ROLES = ["control", "environment", "sensor", "outcome", "ignore"] as const;
type Role = (typeof ROLES)[number];

/** Virtualized table of the newest rows (newest first); `label` names the data for screen readers. */
function RawTable({ rows, label }: { rows: SensorRow[]; label: string }) {
  const columns = useMemo(() => (rows[0] ? Object.keys(rows[0]).filter((key) => !key.startsWith("event_")) : []), [rows]);
  const container = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line react-hooks/incompatible-library -- the virtualizer is used as documented by TanStack
  const virtualizer = useVirtualizer({ count: rows.length, getScrollElement: () => container.current, estimateSize: () => 28, overscan: 20 });
  const template = { gridTemplateColumns: `repeat(${columns.length}, minmax(92px, 1fr))` };
  return (
    // Focusable so keyboard users can scroll it; the table structure follows ARIA (rowgroups, rows, cells).
    <div
      ref={container}
      className="h-96 overflow-auto rounded-lg border border-line"
      role="table"
      tabIndex={0}
      aria-label={label}
      aria-rowcount={rows.length + 1}
    >
      <div role="rowgroup" className="sticky top-0 z-10 min-w-max">
        <div className="grid min-w-max gap-2 border-b border-line bg-[#0b1222] px-3 py-2 text-[10px] uppercase tracking-wider text-muted-foreground" style={template} role="row" aria-rowindex={1}>
          {columns.map((column) => (
            <span key={column} role="columnheader" className="truncate">
              {column}
            </span>
          ))}
        </div>
      </div>
      <div role="rowgroup" style={{ height: virtualizer.getTotalSize(), position: "relative" }} className="min-w-max">
        {virtualizer.getVirtualItems().map((item) => {
          const row = rows[rows.length - 1 - item.index];
          return (
            <div key={item.key} role="row" aria-rowindex={item.index + 2} className={cn("tabular absolute inset-x-0 grid gap-2 px-3 text-[11px]", row.failure === 1 && "bg-risk/15", row.machine_running === 0 && "text-slate-500")}
              style={{ ...template, height: item.size, transform: `translateY(${item.start}px)`, alignItems: "center" }}>
              {columns.map((column) => (
                <span key={column} role="cell" className="truncate">
                  {formatNumber(row[column], 3)}
                </span>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function QualityTable({ columns }: { columns: ColumnQuality[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-left text-[10px] uppercase tracking-wider text-muted-foreground">
          <tr>
            {["Column", "Min", "Max", "Mean", "Std", "Missing", "Longest constant run", "Flatline"].map((name) => (
              <th key={name} className="p-1.5">
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular">
          {columns.map((column) => (
            <tr key={column.column} className="border-t border-line/60">
              <td className="p-1.5 font-sans">{humanize(column.column)}</td>
              <td className="p-1.5">{formatNumber(column.minimum)}</td>
              <td className="p-1.5">{formatNumber(column.maximum)}</td>
              <td className="p-1.5">{formatNumber(column.mean)}</td>
              <td className="p-1.5">{formatNumber(column.std)}</td>
              <td className="p-1.5">{column.missing_count}</td>
              <td className="p-1.5">{column.longest_constant_run}</td>
              <td className={cn("p-1.5", column.is_flatline ? "text-warn" : "text-muted-foreground")}>{column.is_flatline ? "yes" : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function guessRole(column: string): Role {
  const name = column.toLowerCase();
  if (/(fail|broke|fault|outcome)/.test(name)) return "outcome";
  if (/(^id$|_id$|index)/.test(name)) return "ignore";
  return "sensor";
}

function ByodPanel() {
  const [file, setFile] = useState<File | null>(null);
  const [columns, setColumns] = useState<string[]>([]);
  const [roles, setRoles] = useState<Record<string, Role>>({});
  const [timeColumn, setTimeColumn] = useState<string>("none");
  const [result, setResult] = useState<ByodResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const pick = async (selected: File | undefined) => {
    setResult(null);
    setError(null);
    if (!selected) return;
    const header = (await selected.slice(0, 64000).text()).split(/\r?\n/)[0] ?? "";
    const names = header.split(",").map((name) => name.trim().replace(/^"|"$/g, "")).filter(Boolean);
    const time = names.find((name) => /^(timestamp|time|datetime|date)$/i.test(name));
    setFile(selected);
    setColumns(names);
    setTimeColumn(time ?? "none");
    setRoles(Object.fromEntries(names.filter((name) => name !== time).map((name) => [name, guessRole(name)])));
  };

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const assigned = Object.fromEntries(Object.entries(roles).filter(([name]) => name !== timeColumn));
      setResult(await api.byodUpload(file, assigned, timeColumn === "none" ? undefined : timeColumn));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel index={4} title="Bring Your Own Data" subtitle="Upload a CSV from your machine, assign column roles, run causal discovery" icon={<Upload className="size-4" />} className="col-span-full">
      <div className="mb-4 flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/5 p-3 text-xs text-amber-100">
        <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
        No ground truth exists for uploaded data: the discovered graph and equations cannot be checked against a known true graph,
        as they can for the simulator. Treat the results as hypotheses to confirm with an engineer.
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[420px_1fr]">
        <div className="space-y-3">
          <label className="block text-xs text-muted-foreground" htmlFor="byod-file">
            CSV file (first row = column names)
          </label>
          <input
            id="byod-file"
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => void pick(event.target.files?.[0])}
            className="block w-full text-xs text-slate-300 file:mr-3 file:rounded-md file:border file:border-line file:bg-white/5 file:px-3 file:py-1.5 file:text-slate-100"
          />
          {columns.length > 0 && (
            <>
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-muted-foreground">Time column (none = tabular rows)</span>
                <Select value={timeColumn} onValueChange={setTimeColumn}>
                  <SelectTrigger className="w-44" aria-label="Time column">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">None (tabular)</SelectItem>
                    {columns.map((name) => (
                      <SelectItem key={name} value={name}>
                        {name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="max-h-80 space-y-1.5 overflow-y-auto pr-1" tabIndex={0} role="region" aria-label="Uploaded data validation details">
                {columns
                  .filter((name) => name !== timeColumn)
                  .map((name) => (
                    <div key={name} className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-xs text-slate-200">{name}</span>
                      <Select value={roles[name] ?? "sensor"} onValueChange={(role) => setRoles((previous) => ({ ...previous, [name]: role as Role }))}>
                        <SelectTrigger className="w-36" aria-label={`Role of ${name}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {ROLES.map((role) => (
                            <SelectItem key={role} value={role}>
                              {humanize(role)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  ))}
              </div>
              <p className="text-[11px] text-muted-foreground">
                Control = levers you can set; environment = conditions you cannot control; sensor = measured internal variables; outcome = 0/1 failure.
              </p>
              <Button onClick={() => void upload()} disabled={busy} className="w-full">
                <Upload aria-hidden /> {busy ? "Analysing..." : "Run causal analysis"}
              </Button>
            </>
          )}
          {error && <ErrorState message={error} />}
        </div>
        <div className="min-w-0 space-y-4">
          {!result ? (
            <EmptyState title="No upload analysed yet" hint="The result shows the validation report, data quality, the discovered graph and the fitted equations." />
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="rounded-full border border-causal/40 px-2 py-0.5 text-cyan-200">{result.mode === "tabular" ? "Tabular mode" : "Time-series mode"}</span>
                <span className="text-muted-foreground">
                  {String(result.validation_report.n_rows)} rows, {String(result.validation_report.n_columns)} columns used
                </span>
                <HowComputed provenance={result.provenance} title="How the uploaded data was analysed" />
              </div>
              <ul className="list-disc space-y-1 pl-4 text-xs text-slate-400">
                {result.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
                {((result.validation_report.warnings as string[] | undefined) ?? []).map((warning) => (
                  <li key={warning} className="text-amber-200">
                    {warning}
                  </li>
                ))}
              </ul>
              {result.graph && (
                <div className="h-80 rounded-lg border border-line">
                  <CausalGraphView graph={result.graph} live={false} />
                </div>
              )}
              <QualityTable columns={result.quality} />
              <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
                {result.model_cards.map((card) => (
                  <p key={card.variable} className="rounded-lg border border-line p-2 font-mono text-[11px] text-cyan-100">
                    {card.equation}
                    <span className="mt-1 block font-sans text-muted-foreground">
                      {card.kind === "logistic" ? `AUC ${formatNumber(card.auc_holdout, 3)}` : `held-out R squared ${formatNumber(card.r_squared_holdout, 3)}`}
                    </span>
                  </p>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </Panel>
  );
}

export function DataPage() {
  const recent = useApi(() => api.dataRecent(5000), "recent");
  const events = useApi(() => api.events(1000), "events");
  const quality = useApi(() => api.dataQuality(), "quality");
  const source = useDataSource();
  // The download must carry this tab's session id, which is known only in the browser.
  const exportHref = useSessionId() ? api.exportUrl() : undefined;
  return (
    <div>
      <PageHeader eyebrow="Module 07 / Data explorer" title="Data" description="The raw live data behind every number, its quality, and your own data.">
        <DataSourceBadge />
        <Button size="sm" variant="outline" asChild>
          <a href={exportHref} download>
            <Download aria-hidden /> Export CSV
          </a>
        </Button>
        <Button size="sm" variant="outline" onClick={() => { recent.reload(); events.reload(); quality.reload(); }}>
          <RefreshCw aria-hidden /> Refresh
        </Button>
      </PageHeader>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_420px]">
        <Panel index={0} title={`Raw sensor rows (${recent.data?.rows.length ?? 0}, newest first)`} subtitle={`${source.simulated ? "Simulated" : source.description}. Red rows: failure. Grey rows: machine stopped.`} icon={<Database className="size-4" />}>
          {recent.error ? <ErrorState message={recent.error} onRetry={recent.reload} /> : !recent.data ? <PanelSkeleton rows={8} /> : <RawTable rows={recent.data.rows} label={`Recent ${source.simulated ? "simulated " : ""}sensor rows`} />}
        </Panel>
        <Panel index={1} title="Event log" subtitle="Newest first">
          {events.error ? (
            <ErrorState message={events.error} onRetry={events.reload} />
          ) : !events.data ? (
            <PanelSkeleton rows={8} />
          ) : (
            <ol className="h-96 space-y-1 overflow-y-auto pr-1 text-xs" tabIndex={0} aria-label="Event log, newest first">
              {[...events.data.events].reverse().map((event, index) => (
                <li key={`${event.step}-${index}`} className="flex gap-2">
                  <span className="tabular w-14 shrink-0 text-right text-muted-foreground">{event.step}</span>
                  <span className="w-32 shrink-0 text-slate-200">{humanize(event.event_type)}</span>
                  <span className="truncate text-slate-500">{typeof event.details === "string" ? event.details : JSON.stringify(event.details)}</span>
                </li>
              ))}
            </ol>
          )}
        </Panel>
        <Panel index={2} title="Data quality report" subtitle={`Live buffer, ${quality.data?.rows ?? 0} rows`} className="col-span-full">
          {quality.error ? <ErrorState message={quality.error} onRetry={quality.reload} /> : !quality.data ? <PanelSkeleton rows={6} /> : <QualityTable columns={quality.data.columns} />}
        </Panel>
        <ByodPanel />
      </div>
    </div>
  );
}
