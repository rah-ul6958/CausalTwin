"use client";

/** Causal Graph Explorer: correlation vs causation, edge evidence, node equations, version history. */

import { GitCompare, History, Network } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";

import { PageHeader } from "@/components/common/page-header";
import { Panel } from "@/components/common/panel";
import { EmptyState, ErrorState, LoadingOrError, PanelSkeleton } from "@/components/common/states";
import { CausalGraphView, edgeKey, type EdgeState, type GraphMode } from "@/components/graph/causal-graph";
import { ConfidenceBar, HowComputed, DataSourceBadge } from "@/components/transparency/transparency";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api } from "@/lib/api";
import { formatNumber, formatPValue, humanize } from "@/lib/format";
import { useLiveStore } from "@/lib/store";
import type { CausalEdge, CausalGraph, EquationCard, GraphUpdate } from "@/lib/types";
import { useApi } from "@/lib/use-api";

const DIFF_DISPLAY_MS = 6000;
/** A graph update younger than this when the page opens is replayed (for example during the guided demo). */
const REPLAY_RECENT_UPDATE_MS = 60_000;
const REPLAY_DELAY_MS = 400;
/** How often the root-cause panel is refreshed. */
const ROOT_CAUSE_REFRESH_MS = 10_000;

interface SelectedEdge {
  edge: CausalEdge;
  kind: string;
}

function EdgeDrawer({ selected, onClose }: { selected: SelectedEdge | null; onClose: () => void }) {
  const causal = selected?.kind === "causal";
  const evidence = useApi(
    () => (selected && causal ? api.edgeEvidence(selected.edge.cause, selected.edge.effect) : Promise.resolve(null)),
    selected ? `${selected.kind}:${edgeKey(selected.edge.cause, selected.edge.effect)}` : "none",
  );
  const data = evidence.data;
  const points = useMemo(() => (data?.points ?? []).map(([x, y]) => ({ x, y })), [data]);
  return (
    <Sheet open={selected !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-[480px] max-w-[94vw] overflow-y-auto border-line bg-[#080c16]/95 backdrop-blur-xl sm:max-w-lg">
        {selected && (
          <>
            <SheetHeader>
              <SheetTitle className="font-heading">
                {humanize(selected.edge.cause)} {causal ? "->" : "<->"} {humanize(selected.edge.effect)}
              </SheetTitle>
              <SheetDescription>{causal ? "Causal edge evidence" : "Correlation only (not a causal claim)"}</SheetDescription>
            </SheetHeader>
            <div className="space-y-5 px-4 pb-6 text-sm">
              {!causal ? (
                <p className="leading-relaxed text-slate-300">
                  Lagged Pearson correlation r = <span className="tabular">{formatNumber(selected.edge.strength, 3)}</span> at lag{" "}
                  {selected.edge.lag}. Correlation can come from a common cause or a proxy, so it says nothing about what
                  happens if you change one of them. Switch to the causal view to see which links survive conditioning.
                </p>
              ) : evidence.error ? (
                <ErrorState message={evidence.error} onRetry={evidence.reload} />
              ) : !data ? (
                <PanelSkeleton rows={5} />
              ) : (
                <>
                  <dl className="grid grid-cols-3 gap-3">
                    {[
                      ["Lag", `t-${data.edge.lag}`],
                      [data.edge.method === "logistic" ? "Std. effect" : "Partial corr.", formatNumber(data.edge.strength, 3)],
                      ["p-value", formatPValue(data.edge.p_value)],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-lg border border-line bg-white/[0.03] p-2">
                        <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</dt>
                        <dd className="tabular text-base text-slate-100">{value}</dd>
                      </div>
                    ))}
                  </dl>
                  <ConfidenceBar label="Bootstrap confidence" value={data.confidence?.bootstrap_frequency} explanation={data.confidence?.explanation} />
                  {data.confidence && <p className="text-xs leading-relaxed text-slate-300">{data.confidence.explanation}</p>}
                  <div>
                    <p className="mb-1 text-xs text-muted-foreground">
                      {data.x_label} versus {data.y_label} ({points.length} sampled rows)
                    </p>
                    <div className="h-56 rounded-lg border border-line bg-white/[0.02] p-2">
                      <ResponsiveContainer width="100%" height="100%">
                        <ScatterChart margin={{ top: 4, right: 8, bottom: 4, left: -8 }}>
                          <CartesianGrid stroke="rgb(148 163 184 / 0.08)" />
                          <XAxis type="number" dataKey="x" name={data.x_label} tick={{ fontSize: 9, fill: "#64748b" }} domain={["auto", "auto"]} />
                          <YAxis type="number" dataKey="y" name={data.y_label} tick={{ fontSize: 9, fill: "#64748b" }} domain={["auto", "auto"]} />
                          <Tooltip contentStyle={{ background: "#0b1120", border: "1px solid rgb(148 163 184 / 0.2)", fontSize: 11 }} />
                          <Scatter data={points} fill="#22d3ee" fillOpacity={0.45} isAnimationActive={false} />
                        </ScatterChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    How was this computed? <HowComputed provenance={data.provenance} />
                  </div>
                </>
              )}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

function NodeDrawer({ name, graph, cards, onClose }: { name: string | null; graph: CausalGraph; cards: EquationCard[]; onClose: () => void }) {
  const card = cards.find((item) => item.variable === name);
  const causes = graph.edges.filter((edge) => edge.effect === name && !edge.is_self_loop);
  const effects = graph.edges.filter((edge) => edge.cause === name && !edge.is_self_loop);
  const node = graph.nodes.find((item) => item.name === name);
  return (
    <Sheet open={name !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-[480px] max-w-[94vw] overflow-y-auto border-line bg-[#080c16]/95 backdrop-blur-xl sm:max-w-lg">
        {name && (
          <>
            <SheetHeader>
              <SheetTitle className="font-heading">{humanize(name)}</SheetTitle>
              <SheetDescription>
                {humanize(node?.role ?? "")} {node?.unit ? `(${node.unit})` : ""}
              </SheetDescription>
            </SheetHeader>
            <div className="space-y-5 px-4 pb-6 text-sm">
              {card ? (
                <section>
                  <h3 className="mb-1 text-xs uppercase tracking-wider text-muted-foreground">Fitted equation</h3>
                  <p className="rounded-lg border border-line bg-white/[0.03] p-3 font-mono text-xs leading-relaxed text-cyan-100">{card.equation}</p>
                  <dl className="mt-2 grid grid-cols-3 gap-2 text-xs">
                    <div>
                      <dt className="text-muted-foreground">{card.kind === "logistic" ? "Held-out AUC" : "Held-out R squared"}</dt>
                      <dd className="tabular text-slate-100">{formatNumber(card.kind === "logistic" ? card.auc_holdout : card.r_squared_holdout, 3)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Residual std</dt>
                      <dd className="tabular text-slate-100">{formatNumber(card.residual_std, 3)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Train / holdout</dt>
                      <dd className="tabular text-slate-100">
                        {card.n_train} / {card.n_holdout}
                      </dd>
                    </div>
                  </dl>
                </section>
              ) : (
                <p className="text-slate-300">
                  {node?.role === "control" || node?.role === "environment"
                    ? "Exogenous variable: it has no causes inside the system, so there is no equation for it."
                    : "No fitted equation for this variable."}
                </p>
              )}
              <section>
                <h3 className="mb-1 text-xs uppercase tracking-wider text-muted-foreground">Causes ({causes.length})</h3>
                <ul className="space-y-1">
                  {causes.map((edge) => (
                    <li key={edge.cause} className="flex justify-between text-xs">
                      <span>{humanize(edge.cause)}</span>
                      <span className="tabular text-muted-foreground">t-{edge.lag}, confidence {formatNumber(edge.confidence, 2)}</span>
                    </li>
                  ))}
                  {causes.length === 0 && <li className="text-xs text-muted-foreground">None discovered</li>}
                </ul>
              </section>
              <section>
                <h3 className="mb-1 text-xs uppercase tracking-wider text-muted-foreground">Effects ({effects.length})</h3>
                <ul className="space-y-1">
                  {effects.map((edge) => (
                    <li key={edge.effect} className="flex justify-between text-xs">
                      <span>{humanize(edge.effect)}</span>
                      <span className="tabular text-muted-foreground">t-{edge.lag}</span>
                    </li>
                  ))}
                  {effects.length === 0 && <li className="text-xs text-muted-foreground">None discovered</li>}
                </ul>
              </section>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

interface DriftDiff {
  states: Record<string, EdgeState>;
  fading: CausalEdge[];
}

export function GraphExplorer() {
  // A restart of the simulation (reset or demo start) reloads the graph of the new run.
  const runId = useLiveStore((state) => state.runId);
  const causal = useApi(() => api.graph(), `graph-${runId}`);
  const correlation = useApi(() => api.correlationGraph(), "correlation");
  const history = useApi(() => api.graphHistory(), `history-${runId}`, 5000);
  const cards = useApi(() => api.modelCards(), "cards");
  const rootCause = useApi(() => api.rootCause().catch(() => null), "root-cause", ROOT_CAUSE_REFRESH_MS);
  const [mode, setMode] = useState<GraphMode>("causal");
  const [selectedEdge, setSelectedEdge] = useState<SelectedEdge | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  // The graph pushed by the last drift update, with the run it belongs to.
  const [liveGraph, setLiveGraph] = useState<{ runId: number; graph: CausalGraph } | null>(null);
  const [diff, setDiff] = useState<DriftDiff | null>(null);

  // A drift-driven graph update arrives over the WebSocket: animate the difference. An update that
  // arrived shortly before this page opened (the guided demo navigates here after the drift) is replayed.
  useEffect(() => {
    let clearTimer: ReturnType<typeof setTimeout> | undefined;
    const play = (update: GraphUpdate, runId: number) => {
      const states: Record<string, EdgeState> = {};
      const fading: CausalEdge[] = [];
      for (const change of update.edge_changes) {
        const key = edgeKey(change.cause, change.effect);
        if (change.change === "removed") {
          fading.push({
            cause: change.cause, effect: change.effect, lag: 1, lags: [1], strength: change.old_coefficient ?? 0.2,
            p_value: null, method: "pcmci", is_self_loop: false, confidence: null, uncertain: false, oriented: true,
          });
        } else {
          states[key] = change.change === "added" ? "added" : "changed";
        }
      }
      setLiveGraph({ runId, graph: update.graph });
      setDiff({ states, fading });
      if (clearTimer) clearTimeout(clearTimer);
      clearTimer = setTimeout(() => setDiff(null), DIFF_DISPLAY_MS);
    };
    const unsubscribe = useLiveStore.subscribe((state, previous) => {
      if (state.graphRevision === previous.graphRevision || !state.lastGraphUpdate) return;
      play(state.lastGraphUpdate, state.runId);
    });
    const { lastGraphUpdate: recentUpdate, lastGraphUpdateAt: receivedAt, runId: currentRunId } = useLiveStore.getState();
    const isRecent = receivedAt !== null && Date.now() - receivedAt < REPLAY_RECENT_UPDATE_MS;
    // Short delay so the page has faded in before the edges animate.
    const replayTimer = recentUpdate && isRecent ? setTimeout(() => play(recentUpdate, currentRunId), REPLAY_DELAY_MS) : undefined;
    return () => {
      unsubscribe();
      if (clearTimer) clearTimeout(clearTimer);
      if (replayTimer) clearTimeout(replayTimer);
    };
  }, []);

  const graph = (liveGraph?.runId === runId ? liveGraph.graph : null) ?? causal.data?.graph ?? null;
  const topPath = rootCause.data?.result.paths[0];
  const pathNodes = useMemo(() => new Set(topPath ? [...topPath.nodes.map((node) => node.variable), "failure"] : []), [topPath]);
  const pathEdges = useMemo(() => {
    const keys = new Set<string>();
    if (topPath) {
      const names = [...topPath.nodes.map((node) => node.variable), "failure"];
      for (let index = 0; index < names.length - 1; index += 1) keys.add(edgeKey(names[index], names[index + 1]));
    }
    return keys;
  }, [topPath]);
  const symptoms = useMemo(() => new Set(rootCause.data?.result.symptoms.map((symptom) => symptom.variable) ?? []), [rootCause.data]);

  return (
    <div>
      <PageHeader eyebrow="Module 02 / Causal intelligence network" title="Causal Graph Explorer" description="Click an edge for its evidence, or a node for its fitted equation. Toggle correlation and causation to see the trap.">
        <DataSourceBadge />
        <HowComputed provenance={causal.data?.provenance} title="Discovery of this causal graph" />
      </PageHeader>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_340px]">
        <Panel
          index={0}
          demo="graph-canvas"
          testId="graph-canvas"
          title="Graph"
          subtitle={
            mode === "correlation"
              ? "Correlation view: everything that moves together (amber, undirected)"
              : mode === "overlay"
                ? "Overlay: correlations (amber) beneath the causal links (cyan)"
                : "Causal view: links that survive conditioning on everything else"
          }
          icon={<Network className="size-4" />}
          actions={
            <ToggleGroup type="single" value={mode} onValueChange={(value) => value && setMode(value as GraphMode)} variant="outline" size="sm" aria-label="Graph view">
              <ToggleGroupItem value="correlation">Correlation</ToggleGroupItem>
              <ToggleGroupItem value="causal">Causal</ToggleGroupItem>
              <ToggleGroupItem value="overlay">Overlay</ToggleGroupItem>
            </ToggleGroup>
          }
          bodyClassName="p-0"
        >
          <div className="h-[620px]">
            {causal.error ? (
              <div className="p-4">
                <ErrorState message={causal.error} onRetry={causal.reload} />
              </div>
            ) : !graph ? (
              <div className="p-4">
                <PanelSkeleton rows={8} />
              </div>
            ) : (
              <CausalGraphView
                graph={graph}
                correlation={correlation.data?.graph}
                mode={mode}
                pathEdges={pathEdges}
                pathNodes={pathNodes}
                symptoms={symptoms}
                edgeStates={diff?.states}
                fadingEdges={diff?.fading}
                onEdgeClick={(edge, kind) => setSelectedEdge({ edge, kind })}
                onNodeClick={(name) => setSelectedNode(name)}
              />
            )}
          </div>
          <div className="flex flex-wrap gap-4 border-t border-line px-4 py-2 text-[11px] text-muted-foreground">
            <span>Thickness = strength</span>
            <span>Solid = confident, dashed = uncertain (bootstrap below 0.6)</span>
            <span className="text-rose-300">Red glow = top root-cause path now</span>
            <span className="text-amber-300">Amber tag = symptom (correlated, no causal path)</span>
          </div>
        </Panel>
        <div className="flex flex-col gap-4">
          <Panel index={1} title="Root cause now" subtitle="Top path explaining the current risk" icon={<GitCompare className="size-4" />}>
            {!rootCause.data ? (
              <EmptyState title="No root-cause analysis right now" hint={`It needs recent running data; it refreshes every ${ROOT_CAUSE_REFRESH_MS / 1000} s.`} />
            ) : (
              <div className="space-y-2 text-xs">
                <p className="leading-relaxed text-slate-200">{topPath?.description ?? "No risk-increasing path."}</p>
                <div>
                  <p className="mb-1 text-muted-foreground">Symptoms, not causes</p>
                  {rootCause.data.result.symptoms.length === 0 ? (
                    <p className="text-muted-foreground">None</p>
                  ) : (
                    rootCause.data.result.symptoms.map((symptom) => (
                      <p key={symptom.variable} className="text-amber-200">
                        {humanize(symptom.variable)} (r = {formatNumber(symptom.correlation, 2)})
                      </p>
                    ))
                  )}
                </div>
              </div>
            )}
          </Panel>
          <Panel index={2} title="Graph version history" subtitle="A new version after every confirmed drift" icon={<History className="size-4" />}>
            {!history.data ? (
              <LoadingOrError state={history} rows={2} />
            ) : (
              <ol className="space-y-3">
                {[...history.data.versions].reverse().map((version) => (
                  <li key={version.version} className="border-l-2 border-causal/40 pl-3 text-xs">
                    <p className="font-medium text-slate-100">
                      Version {version.version} <span className="tabular text-muted-foreground">- step {version.step}, {version.n_edges} edges</span>
                    </p>
                    <p className="leading-relaxed text-slate-400">{version.reason}</p>
                    {version.edge_changes.map((change) => (
                      <p key={`${change.cause}-${change.effect}`} className="text-amber-200">
                        {humanize(change.change)}: {humanize(change.cause)} -&gt; {humanize(change.effect)}
                      </p>
                    ))}
                  </li>
                ))}
              </ol>
            )}
          </Panel>
          {graph && graph.notes.length > 0 && (
            <Panel index={3} title="Notes from discovery">
              <ul className="list-disc space-y-1 pl-4 text-xs text-slate-400">
                {graph.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      </div>
      <EdgeDrawer selected={selectedEdge} onClose={() => setSelectedEdge(null)} />
      {graph && <NodeDrawer name={selectedNode} graph={graph} cards={cards.data?.cards ?? []} onClose={() => setSelectedNode(null)} />}
    </div>
  );
}
