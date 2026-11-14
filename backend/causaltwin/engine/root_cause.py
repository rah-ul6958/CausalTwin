"""Root-cause tracer: which causal paths explain the current failure risk, and which signals are mere symptoms.

Method
------
Every variable's deviation from its normal level (median of the fitted window) is split
into the part explained by its causes and its own unexplained part, using steady-state
gains of the fitted equations: gain(parent -> child) = (sum of the parent's
coefficients) / (1 - sum of the child's self-lag coefficients). A path
root -> ... -> direct cause -> failure contributes

    root's own unexplained deviation * product of gains along the path * failure coefficient

to the failure log-odds (the risk score). Summed over all paths this reproduces the
linearized log-odds deviation. Accumulators (self-lag sum >= INTEGRATOR_THRESHOLD, for
example tool wear) have no steady state, so they start paths instead of passing them on.

Symptoms are variables correlated with "failure soon" that have no causal path to failure
in the discovered graph; changing them is not expected to change the risk.
"""

import time

import numpy as np

from causaltwin.engine.causal_model import CausalModel
from causaltwin.engine.schemas import (
    CausalGraph,
    PathNode,
    RiskEstimate,
    RootCausePath,
    RootCauseResult,
    Symptom,
)
from causaltwin.utils.timing import timed_step


def _gains(model: CausalModel, integrator_threshold: float) -> tuple[dict[str, dict[str, float]], set[str]]:
    """Steady-state gains child -> {parent: gain}, and the set of accumulator variables."""
    gains: dict[str, dict[str, float]] = {}
    integrators = set()
    for name, equation in model.equations.items():
        persistence = equation.self_coefficient_sum()
        if persistence >= integrator_threshold:
            integrators.add(name)
            continue
        parents = {cause for cause, _ in equation.terms if cause != name}
        gains[name] = {parent: equation.cause_coefficient_sum(parent) / (1.0 - persistence) for parent in parents}
    return gains, integrators


def ancestors_of(graph: CausalGraph, target: str) -> set[str]:
    """Every variable with a directed causal path to `target`."""
    found: set[str] = set()
    frontier = [target]
    while frontier:
        node = frontier.pop()
        for edge in graph.parents_of(node):
            if edge.is_self_loop or not edge.oriented or edge.cause in found:
                continue
            found.add(edge.cause)
            frontier.append(edge.cause)
    return found


def _change_text(node: PathNode, is_root: bool, root_kind: str) -> str:
    """Readable change, for example 'down 35%' or '+6.0 deg C'."""
    unit = f" {node.unit}" if node.unit else ""
    if is_root and root_kind == "exogenous" and node.percent_change is not None:
        direction = "up" if node.percent_change >= 0 else "down"
        return f"{direction} {abs(node.percent_change):.0f}%"
    text = f"{node.change:+.3g}{unit}"
    if is_root and root_kind == "unexplained":
        return f"{text} not explained by its causes"
    if is_root and root_kind == "accumulated":
        return f"{text} accumulated"
    return text


def trace_root_causes(
    model: CausalModel,
    graph: CausalGraph,
    current: np.ndarray,
    risk: RiskEstimate | None,
    failure_correlations: dict[str, float],
    risk_threshold: float,
    max_paths: int,
    max_depth: int,
    integrator_threshold: float,
    symptom_threshold: float,
) -> RootCauseResult:
    """Rank causal paths to failure for the current state and list symptoms."""
    with timed_step("root_cause", "Traced root causes") as timer:
        start_time = time.perf_counter()
        notes: list[str] = []
        deviation = {name: float(current[i]) - model.baseline[name] for i, name in enumerate(model.variables)}
        gains, integrators = _gains(model, integrator_threshold)

        def own_part(name: str) -> tuple[float, str]:
            """A node's deviation not explained by its causes, and how to describe it."""
            if name in model.exogenous:
                return deviation[name], "exogenous"
            if name in integrators or name not in gains:
                return deviation[name], "accumulated"
            explained = sum(gain * deviation[parent] for parent, gain in gains[name].items())
            return deviation[name] - explained, "unexplained"

        raw_paths: list[tuple[list[str], float, str]] = []
        if model.failure is None or not model.failure.terms:
            notes.append("No failure causes in the model, so no causal paths can be traced.")
        else:
            failure_coefficients: dict[str, float] = {}
            for (cause, _), coefficient in zip(model.failure.terms, model.failure.coefficients, strict=True):
                failure_coefficients[cause] = failure_coefficients.get(cause, 0.0) + float(coefficient)

            def walk(path: list[str], gain_product: float) -> None:
                """Record the path starting at path[0], then extend it upstream."""
                head = path[0]
                own, kind = own_part(head)
                raw_paths.append((path, own * gain_product, kind))
                if len(path) >= max_depth or head not in gains:
                    return
                for parent, gain in gains[head].items():
                    if parent not in path:
                        walk([parent, *path], gain_product * gain)

            for cause, coefficient in failure_coefficients.items():
                walk([cause], coefficient)

        def build_path(path: list[str], contribution: float, kind: str, total: float) -> RootCausePath:
            """Turn a raw path into the API object with attributed changes per node."""
            own, _ = own_part(path[0])
            nodes = []
            attributed = own
            for position, name in enumerate(path):
                if position > 0:
                    attributed *= gains[name][path[position - 1]]
                baseline = model.baseline[name]
                percent = 100.0 * attributed / abs(baseline) if baseline != 0 else None
                nodes.append(
                    PathNode(variable=name, change=attributed, percent_change=percent, unit=model.units.get(name))
                )
            parts = [f"{node.variable} ({_change_text(node, index == 0, kind)})" for index, node in enumerate(nodes)]
            description = " -> ".join(parts) + " -> failure risk"
            share = contribution / total if total > 0 else 0.0
            via = f" through {', '.join(path[1:])}" if len(path) > 1 else ""
            direction = "raises" if contribution >= 0 else "lowers"
            explanation = (
                f"{path[0]} is {_change_text(nodes[0], True, kind)} relative to its normal level; "
                f"{via.strip() + ', ' if via else ''}this {direction} the failure risk score by "
                f"{abs(contribution):.2f} log-odds ({abs(share):.0%} of the total increase)."
            )
            return RootCausePath(
                nodes=nodes, description=description, contribution=contribution, share=share, explanation=explanation
            )

        total_increase = sum(value for _, value, _ in raw_paths if value > 0)
        increasing = sorted((p for p in raw_paths if p[1] > 0), key=lambda p: -p[1])[:max_paths]
        decreasing = sorted((p for p in raw_paths if p[1] < 0), key=lambda p: p[1])[:max_paths]
        paths = [build_path(path, value, kind, total_increase) for path, value, kind in increasing]
        reducing = [build_path(path, value, kind, total_increase) for path, value, kind in decreasing]

        causal_ancestors = ancestors_of(graph, model.failure.target) if model.failure is not None else set()
        symptoms = [
            Symptom(
                variable=name,
                correlation=correlation,
                explanation=(
                    f"{name} is correlated with failure soon (r = {correlation:+.2f}) but has no causal path to "
                    f"failure in the discovered graph, so changing it is not expected to change the risk."
                ),
            )
            for name, correlation in sorted(failure_correlations.items(), key=lambda item: -abs(item[1]))
            if abs(correlation) >= symptom_threshold and name not in causal_ancestors
        ]
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        timer.details.update({"paths": len(paths), "symptoms": len(symptoms)})
    return RootCauseResult(
        risk=risk,
        risk_is_high=risk is not None and risk.probability >= risk_threshold,
        paths=paths,
        risk_reducing_paths=reducing,
        symptoms=symptoms,
        computation_ms=elapsed_ms,
        notes=notes,
    )
