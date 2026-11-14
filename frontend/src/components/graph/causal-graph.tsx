"use client";

/**
 * Interactive causal graph (React Flow) with a fixed, role-based layout.
 * - Edge thickness = strength, solid = confident, dashed = uncertain, label = lag ("t-2").
 * - Particles flow along causal edges; edges on the current root-cause path glow red.
 * - Symptom nodes (correlated, not causal) get an amber tag.
 * - Drift diffs: added edges draw in, changed edges flash, removed edges fade out.
 * - Modes: causal, correlation, or overlay. Both edge sets are always rendered and crossfade.
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
import { memo, useMemo } from "react";

import { Sparkline } from "@/components/charts/sparkline";
import { formatNumber, formatPercent, humanize } from "@/lib/format";
import { layoutGraph } from "@/lib/graph-layout";
import { useLiveStore } from "@/lib/store";
import type { CausalEdge, CausalGraph } from "@/lib/types";
import { cn } from "@/lib/utils";

export type GraphMode = "causal" | "correlation" | "overlay";
export type EdgeState = "added" | "removed" | "changed";

export const edgeKey = (cause: string, effect: string) => `${cause}->${effect}`;

const ROLE_STYLE: Record<string, { label: string; accent: string }> = {
  control: { label: "Control lever", accent: "border-causal/50" },
  environment: { label: "Environment", accent: "border-slate-400/40" },
  sensor: { label: "Sensor", accent: "border-white/15" },
  outcome: { label: "Outcome", accent: "border-risk/60" },
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

interface CausalEdgeData extends Record<string, unknown> {
  edge: CausalEdge;
  kind: "causal" | "correlation" | "truth" | "missing";
  width: number;
  visible: boolean;
  onPath: boolean;
  state: EdgeState | null;
}
type CausalEdgeType = Edge<CausalEdgeData, "causal">;

function LiveValue({ name, unit }: { name: string; unit: string | null }) {
  const points = useLiveStore((state) => state.points);
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
  return (
    <div
      key={data.pulseKey}
      className={cn(
        "rounded-xl border bg-[#0b1222]/90 px-3 py-2 shadow-lg backdrop-blur",
        style.accent,
        data.compact ? "w-[132px]" : "w-[164px]",
        data.onPath && "border-risk shadow-[0_0_22px_-2px_rgb(244_63_94/0.7)]",
        data.pulseKey > 0 && "node-pulse",
      )}
    >
      <Handle type="target" position={Position.Left} className="!size-1.5 !border-0 !bg-slate-500" />
      <div className="flex items-center justify-between gap-1">
        <span className="truncate text-xs font-medium text-slate-100">{humanize(data.name)}</span>
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
  if (!data) return null;
  const color = data.onPath ? "#f43f5e" : EDGE_COLOR[data.kind];
  const dashed = data.edge.uncertain || data.kind === "correlation" || data.kind === "missing";
  const opacity = data.visible ? (data.kind === "correlation" ? 0.7 : 0.95) : 0;
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
          strokeWidth: data.width,
          strokeDasharray: dashed ? "5 5" : undefined,
          opacity,
          filter: data.onPath ? "drop-shadow(0 0 6px rgb(244 63 94 / 0.8))" : undefined,
        }}
      />
      {data.visible && data.kind === "causal" && (
        <>
          {[0, 0.5].map((offset) => (
            <circle key={offset} r={2.2} fill={color} className="edge-particle">
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
        minZoom={0.3}
      >
        {!compact && <Controls showInteractive={false} className="!border-line !bg-[#0b1222] [&_button]:!border-line [&_button]:!bg-[#0b1222] [&_button]:!fill-slate-300" />}
      </ReactFlow>
    </div>
  );
}
