/**
 * Fixed, readable layout for causal graphs: control levers and environment on the left,
 * internal sensors in the middle (one column per causal depth), the failure outcome on the right.
 * Works for any uploaded dataset, not only the simulator.
 */

import type { CausalEdge, CausalGraph, CausalNode } from "@/lib/types";

export interface NodePosition {
  x: number;
  y: number;
}

const COLUMN_GAP = 270;
const ROW_GAP = 118;

function isExogenous(node: CausalNode): boolean {
  return node.role === "control" || node.role === "environment";
}

/** Longest chain of sensor-to-sensor causes leading into each sensor (cycles are cut). */
function sensorDepths(nodes: CausalNode[], edges: CausalEdge[]): Map<string, number> {
  const sensors = new Set(nodes.filter((node) => node.role === "sensor").map((node) => node.name));
  const parents = new Map<string, string[]>();
  for (const edge of edges) {
    if (edge.is_self_loop || !sensors.has(edge.cause) || !sensors.has(edge.effect)) continue;
    parents.set(edge.effect, [...(parents.get(edge.effect) ?? []), edge.cause]);
  }
  const depths = new Map<string, number>();
  const visit = (name: string, trail: Set<string>): number => {
    const known = depths.get(name);
    if (known !== undefined) return known;
    let depth = 0;
    for (const parent of parents.get(name) ?? []) {
      if (!trail.has(parent)) depth = Math.max(depth, visit(parent, new Set([...trail, name])) + 1);
    }
    depths.set(name, depth);
    return depth;
  };
  for (const name of sensors) visit(name, new Set());
  return depths;
}

export function layoutGraph(graph: Pick<CausalGraph, "nodes" | "edges">): Map<string, NodePosition> {
  const depths = sensorDepths(graph.nodes, graph.edges);
  const maxDepth = Math.max(0, ...depths.values());
  const columns = new Map<number, CausalNode[]>();
  for (const node of graph.nodes) {
    let column: number;
    if (isExogenous(node)) column = 0;
    else if (node.role === "outcome") column = maxDepth + 2;
    else column = 1 + (depths.get(node.name) ?? 0);
    columns.set(column, [...(columns.get(column) ?? []), node]);
  }
  const positions = new Map<string, NodePosition>();
  const tallest = Math.max(...[...columns.values()].map((members) => members.length));
  for (const [column, members] of columns) {
    // Controls first, environment last in the left column, so environment sits at the bottom.
    const ordered = [...members].sort((first, second) => {
      if (first.role !== second.role) return first.role === "environment" ? 1 : second.role === "environment" ? -1 : 0;
      return 0;
    });
    const offset = ((tallest - ordered.length) * ROW_GAP) / 2;
    ordered.forEach((node, index) => {
      positions.set(node.name, { x: column * COLUMN_GAP, y: offset + index * ROW_GAP });
    });
  }
  return positions;
}

/** Smallest total lag from a source to every reachable node (used for ripples and time to effect). */
export function lagDistances(edges: CausalEdge[], source: string): Map<string, number> {
  const distances = new Map<string, number>([[source, 0]]);
  const queue: string[] = [source];
  while (queue.length > 0) {
    const node = queue.shift() as string;
    const base = distances.get(node) ?? 0;
    for (const edge of edges) {
      if (edge.is_self_loop || edge.cause !== node) continue;
      const candidate = base + edge.lag;
      const known = distances.get(edge.effect);
      if (known === undefined || candidate < known) {
        distances.set(edge.effect, candidate);
        queue.push(edge.effect);
      }
    }
  }
  return distances;
}
