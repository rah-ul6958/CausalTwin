"use client";

/**
 * Interactive causal graph (React Flow) with a fixed, role-based layout.
 * - Edge thickness = strength, solid = confident, dashed = uncertain, label = lag ("t-2").
 * - Particles flow along causal edges; edges on the current root-cause path glow red.
 * - Symptom nodes (correlated, not causal) get an amber tag.
 * - Drift diffs: added edges draw in, changed edges flash, removed edges fade out.
 * - Modes: causal, correlation, or overlay. Both edge sets are always rendered and crossfade.
 * - Hovering a node highlights its direct causes and effects and dims the rest. The hover state
 *   travels through React context, not through the node and edge data: changing that data on
 *   hover makes React Flow rebuild its elements and swallow the following click.
 */

import {
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  getBezierPath,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { createContext, memo, useContext, useMemo, useState } from "react";

import { Sparkline } from "@/components/charts/sparkline";
import { formatNumber, formatPercent, humanize } from "@/lib/format";
import { layoutGraph } from "@/lib/graph-layout";
import { useLiveStore } from "@/lib/store";
import { useThrottledLive } from "@/lib/use-throttled-live";
import type { CausalEdge, CausalGraph } from "@/lib/types";
import { cn } from "@/lib/utils";

export type GraphMode = "causal" | "correlation" | "overlay";
export type EdgeState = "added" | "removed" | "changed";

export const edgeKey = (cause: string, effect: string) => `${cause}->${effect}`;

const ROLE_STYLE: Record<string, { label: string; accent: string; dot: string; glow: string }> = {
  control: { label: "Control lever", accent: "border-causal/50", dot: "bg-causal", glow: "shadow-[0_0_20px_-8px_var(--causal)]" },
  environment: { label: "Environment", accent: "border-slate-400/40", dot: "bg-slate-400", glow: "" },
  sensor: { label: "Sensor", accent: "border-sky-300/25", dot: "bg-sky-300", glow: "" },
  outcome: { label: "Outcome", accent: "border-risk/70", dot: "bg-risk", glow: "shadow-[0_0_30px_-6px_rgb(244_63_94/0.65)]" },
};

interface VariableNodeData extends Record<string, unknown> {
  name: string;
  role: string;
  unit: string | null;
  symptom: boolean;
  onPath: boolean;
  pulseKey: number;
  compact: boolean;
  live: boolean;
}
type VariableNodeType = Node<VariableNodeData, "variable">;

/** The hovered node and the nodes linked to it by a causal edge (null when nothing is hovered). */
interface HoverFocus {
  hovered: string;
  linked: Set<string>;
}
const HoverContext = createContext<HoverFocus | null>(null);

interface CausalEdgeData extends Record<string, unknown> {
  edge: CausalEdge;
  kind: "causal" | "correlation" | "truth" | "missing";
  width: number;
  visible: boolean;
  onPath: boolean;
  state: EdgeState | null;
}
type CausalEdgeType = Edge<CausalEdgeData, "causal">;

/** Graph sparklines update at most once a second (eleven of them on the page). */
const SPARKLINE_REFRESH_MS = 1000;
const selectPoints = (state: ReturnType<typeof useLiveStore.getState>) => state.points;

function LiveValue({ name, unit }: { name: string; unit: string | null }) {
  const points = useThrottledLive(selectPoints, SPARKLINE_REFRESH_MS);
  const values = useMemo(
    () => points.slice(-60).map((point) => point.values[name]).filter((value): value is number => typeof value === "number"),
    [points, name],
  );
  const latest = values[values.length - 1];
  return (
    <>
      <div className="tabular text-sm text-slate-100">
        {formatNumber(latest)} <span className="text-[10px] text-muted-foreground">{unit}</span>
      </div>
      <Sparkline values={values} />
    </>
  );
}

function FailureValue() {
  const latestRisk = useLiveStore((state) => state.latestRisk);
  return (
    <div className="tabular text-sm text-rose-200">
      {latestRisk?.twin_risk ? `${formatPercent(latestRisk.twin_risk.probability)} risk` : "no estimate"}
    </div>
  );
}

function VariableNodeBase({ data }: NodeProps<VariableNodeType>) {
  const style = ROLE_STYLE[data.role] ?? ROLE_STYLE.sensor;
  const focus = useContext(HoverContext);
  const dimmed = focus !== null && !focus.linked.has(data.name);
  return (
    <div
      key={data.pulseKey}
      className={cn(
        "rounded-xl border bg-gradient-to-b from-[#0e1729]/95 to-[#070c17]/95 px-3 py-2 shadow-lg backdrop-blur transition-[opacity,box-shadow,border-color] duration-200",
        style.accent,
        style.glow,
        data.compact ? "w-[132px]" : "w-[164px]",
        data.onPath && "border-risk shadow-[0_0_26px_-2px_rgb(244_63_94/0.75)]",
        data.pulseKey > 0 && "node-pulse",
        dimmed && "opacity-30",
      )}
    >
      <Handle type="target" position={Position.Left} className="!size-1.5 !border-0 !bg-slate-500" />
      <div className="flex items-center justify-between gap-1">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className={cn("size-1.5 shrink-0 rounded-full", style.dot)} aria-hidden />
          <span className="truncate text-xs font-medium text-slate-100">{humanize(data.name)}</span>
        </span>
        {data.symptom && (
          <span className="rounded bg-warn/15 px-1 text-[9px] font-semibold uppercase tracking-wide text-warn">symptom</span>
        )}
      </div>
      {!data.compact && <div className="text-[9px] uppercase tracking-wider text-muted-foreground">{style.label}</div>}
      {data.live && !data.compact && (data.role === "outcome" ? <FailureValue /> : <LiveValue name={data.name} unit={data.unit} />)}
      <Handle type="source" position={Position.Right} className="!size-1.5 !border-0 !bg-slate-500" />
    </div>
  );
}
const VariableNode = memo(VariableNodeBase);

const EDGE_COLOR: Record<CausalEdgeData["kind"], string> = {
  causal: "#22d3ee",
  correlation: "#fbbf24",
  truth: "#34d399",
  missing: "#f43f5e",
};

function CausalEdgeBase(props: EdgeProps<CausalEdgeType>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd } = props;
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const hover = useContext(HoverContext);
  if (!data) return null;
  const focus =
    hover === null || data.kind === "correlation"
      ? null
      : data.edge.cause === hover.hovered || data.edge.effect === hover.hovered
        ? "highlight"
        : "dim";
  const color = data.onPath ? "#f43f5e" : EDGE_COLOR[data.kind];
  const dashed = data.edge.uncertain || data.kind === "correlation" || data.kind === "missing";
  const baseOpacity = data.visible ? (data.kind === "correlation" ? 0.7 : 0.95) : 0;
  const opacity = focus === "dim" ? baseOpacity * 0.15 : baseOpacity;
  // Glow only where it carries meaning (root-cause path, hovered links); filters on every edge are costly.
  const glow = data.onPath ? "drop-shadow(0 0 5px rgb(244 63 94 / 0.8))" : focus === "highlight" ? `drop-shadow(0 0 5px ${color})` : undefined;
  const stateClass = data.state ? `edge-${data.state}` : "";
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={data.kind === "correlation" ? undefined : markerEnd}
        className={cn("edge-path", stateClass)}
        style={{
          stroke: color,
          strokeDasharray: dashed ? "5 5" : undefined,
          strokeWidth: focus === "highlight" ? data.width + 1 : data.width,
          opacity,
          filter: glow,
        }}
      />
      {data.visible && data.kind === "causal" && focus !== "dim" && (
        <>
          {[0].map((offset) => (
            <circle key={offset} r={focus === "highlight" ? 3 : 2.2} fill={color} className="edge-particle">
              <animateMotion dur={`${1.4 + data.edge.lag * 0.5}s`} begin={`${offset * (1.4 + data.edge.lag * 0.5)}s`} repeatCount="indefinite" path={path} />
            </circle>
          ))}
        </>
      )}
      {data.visible && data.kind !== "correlation" && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-none absolute rounded bg-[#05070d]/80 px-1 font-mono text-[9px] text-slate-400"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            t-{data.edge.lag}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
const CausalEdgeView = memo(CausalEdgeBase);

const NODE_TYPES = { variable: VariableNode };
const EDGE_TYPES = { causal: CausalEdgeView };

export interface CausalGraphViewProps {
  graph: CausalGraph;
  correlation?: CausalGraph | null;
  mode?: GraphMode;
  pathEdges?: Set<string>;
  pathNodes?: Set<string>;
  symptoms?: Set<string>;
  edgeStates?: Record<string, EdgeState>;
  /** Edges that are no longer in the graph but are shown fading out. */
  fadingEdges?: CausalEdge[];
  /** Edges drawn in red as "missing" (benchmark comparison). */
  missingEdges?: CausalEdge[];
  pulse?: Record<string, number>;
  live?: boolean;
  compact?: boolean;
  edgeKind?: "causal" | "truth";
  className?: string;
  onEdgeClick?: (edge: CausalEdge, kind: CausalEdgeData["kind"]) => void;
  onNodeClick?: (name: string) => void;
}

function widths(edges: CausalEdge[]): Map<CausalEdge, number> {
  const maxByMethod = new Map<string, number>();
  for (const edge of edges) maxByMethod.set(edge.method, Math.max(maxByMethod.get(edge.method) ?? 0, Math.abs(edge.strength)));
  return new Map(edges.map((edge) => [edge, 1.2 + 4 * (Math.abs(edge.strength) / (maxByMethod.get(edge.method) || 1))]));
}

/** Correlation edges are associations: keep one undirected edge per pair (the strongest). */
function undirected(edges: CausalEdge[]): CausalEdge[] {
  const best = new Map<string, CausalEdge>();
  for (const edge of edges) {
    const key = [edge.cause, edge.effect].sort().join("|");
    const known = best.get(key);
    if (!known || Math.abs(edge.strength) > Math.abs(known.strength)) best.set(key, edge);
  }
  return [...best.values()];
}

export function CausalGraphView({
  graph,
  correlation,
  mode = "causal",
  pathEdges,
  pathNodes,
  symptoms,
  edgeStates,
  fadingEdges = [],
  missingEdges = [],
  pulse,
  live = true,
  compact = false,
  edgeKind = "causal",
  className,
  onEdgeClick,
  onNodeClick,
}: CausalGraphViewProps) {
  const [hovered, setHovered] = useState<string | null>(null);
  // The hovered node and the nodes directly linked to it by a causal edge.
  const focus = useMemo<HoverFocus | null>(() => {
    if (!hovered || mode === "correlation") return null;
    const linked = new Set<string>([hovered]);
    for (const edge of graph.edges) {
      if (edge.is_self_loop) continue;
      if (edge.cause === hovered) linked.add(edge.effect);
      if (edge.effect === hovered) linked.add(edge.cause);
    }
    return { hovered, linked };
  }, [hovered, graph, mode]);

  const nodes = useMemo<VariableNodeType[]>(() => {
    const positions = layoutGraph(graph);
    return graph.nodes.map((node) => ({
      id: node.name,
      type: "variable",
      position: positions.get(node.name) ?? { x: 0, y: 0 },
      draggable: false,
      data: {
        name: node.name,
        role: node.role,
        unit: node.unit,
        symptom: symptoms?.has(node.name) ?? false,
        onPath: pathNodes?.has(node.name) ?? false,
        pulseKey: pulse?.[node.name] ?? 0,
        compact,
        live,
      },
    }));
  }, [graph, symptoms, pathNodes, pulse, compact, live]);

  const edges = useMemo<CausalEdgeType[]>(() => {
    const causalEdges = graph.edges.filter((edge) => !edge.is_self_loop);
    const correlationEdges = correlation ? undirected(correlation.edges) : [];
    const widthOf = widths([...causalEdges, ...fadingEdges]);
    const correlationWidth = widths(correlationEdges);
    const make = (edge: CausalEdge, kind: CausalEdgeData["kind"], visible: boolean, width: number, state: EdgeState | null): CausalEdgeType => {
      const key = edgeKey(edge.cause, edge.effect);
      const onPath = kind === "causal" && (pathEdges?.has(key) ?? false);
      const color = onPath ? "#f43f5e" : EDGE_COLOR[kind];
      return {
        id: `${kind}:${key}`,
        source: edge.cause,
        target: edge.effect,
        type: "causal",
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
        zIndex: kind === "correlation" ? 0 : 1,
        data: { edge, kind, width, visible, onPath, state },
      };
    };
    const showCausal = mode !== "correlation";
    const showCorrelation = mode !== "causal";
    return [
      ...correlationEdges.map((edge) => make(edge, "correlation", showCorrelation, correlationWidth.get(edge) ?? 1.5, null)),
      ...causalEdges.map((edge) =>
        make(edge, edgeKind, showCausal, widthOf.get(edge) ?? 2, edgeStates?.[edgeKey(edge.cause, edge.effect)] ?? null),
      ),
      ...fadingEdges.map((edge) => make(edge, "causal", showCausal, widthOf.get(edge) ?? 2, "removed")),
      ...missingEdges.map((edge) => make(edge, "missing", true, 1.5, null)),
    ];
  }, [graph, correlation, mode, pathEdges, edgeStates, fadingEdges, missingEdges, edgeKind]);

  return (
    <HoverContext.Provider value={focus}>
    <div className={cn("h-full w-full", className)}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        nodesConnectable={false}
        nodesDraggable={false}
        elementsSelectable={Boolean(onEdgeClick || onNodeClick)}
        zoomOnScroll={false}
        panOnScroll={false}
        proOptions={{ hideAttribution: true }}
        onEdgeClick={(_, edge) => {
          const data = edge.data as CausalEdgeData | undefined;
          if (data?.visible && onEdgeClick) onEdgeClick(data.edge, data.kind);
        }}
        onNodeClick={(_, node) => onNodeClick?.(node.id)}
        onNodeMouseEnter={compact ? undefined : (_, node) => setHovered(node.id)}
        onNodeMouseLeave={compact ? undefined : () => setHovered(null)}
        minZoom={0.3}
      >
        {!compact && <Controls showInteractive={false} className="!border-line !bg-[#0b1222] [&_button]:!border-line [&_button]:!bg-[#0b1222] [&_button]:!fill-slate-300" />}
      </ReactFlow>
    </div>
    </HoverContext.Provider>
  );
}
