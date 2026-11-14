"""Cost-aware intervention optimizer: the smallest, cheapest change that prevents failure.

Search: every single lever from -30% to +30% in 2% steps, every pair of levers on a 5%
grid, and a tool change (a maintenance action on the tool_wear state). All candidates are
scored at once with the batched what-if evaluator (100 rollouts, common random numbers);
the top 5 are re-evaluated with 200 rollouts and independent noise. The goal is the
lowest-cost candidate whose predicted risk is below the target; if none reaches it, the
one with the largest risk reduction is returned, and the explanation says so.

Planning margin: the search ranks against target * OPTIMIZER_PLANNING_MARGIN. Choosing the
cheapest of about 900 noisy estimates that pass a threshold favours estimates that came
out too low (the winner's curse); without the margin, 40% of benchmark recommendations
predicted to meet the 10% target ended at 10.7-15% true risk. Costs are illustrative units.
"""

import heapq
import itertools
import math
import time

import numpy as np

from causaltwin.config import (
    DEFAULT_CONFIG,
    INEFFECTIVE_RISK_REDUCTION,
    LEVER_COSTS,
    LEVER_NAMES,
    OPTIMIZER_FINAL_ROLLOUTS,
    OPTIMIZER_FINAL_SEED,
    OPTIMIZER_PAIR_STEP_PCT,
    OPTIMIZER_PLANNING_MARGIN,
    OPTIMIZER_SEARCH_ROLLOUTS,
    OPTIMIZER_SEED,
    OPTIMIZER_SINGLE_STEP_PCT,
    OPTIMIZER_TARGET_RISK,
    OPTIMIZER_TOP_K,
    SECONDS_PER_STEP,
    TOOL_CHANGE_COST,
    TOOL_CHANGE_SIDE_EFFECT,
    WHATIF_INTERVAL_PERCENTILES,
)
from causaltwin.engine.root_cause import ancestors_of
from causaltwin.engine.schemas import CausalGraph, Intervention, RiskEstimate
from causaltwin.engine.twin import CausalTwinEngine
from causaltwin.engine.whatif import batch_failure_risk
from causaltwin.intelligence.schemas import (
    CandidateResult,
    IneffectiveLever,
    LeverChange,
    Recommendation,
    RecommendationConfidence,
)
from causaltwin.utils.timing import timed_step

TOOL_STATE = "tool_wear"
OUTCOME = "failure"
SYMPTOM_TEST_PCT = -30.0  # how much a symptom is reduced when testing whether acting on it helps


class _Candidate:
    """An option before evaluation."""

    def __init__(self, label: str, changes: list[LeverChange], tool_change: bool) -> None:
        """Store the option and derive its interventions and cost."""
        self.label = label
        self.changes = changes
        self.tool_change = tool_change
        self.interventions = [Intervention(variable=c.lever, change_type="set", value=c.to_value) for c in changes]
        if tool_change:
            self.interventions.append(Intervention(variable=TOOL_STATE, change_type="reset", value=0.0))
        self.cost = sum(LEVER_COSTS[c.lever].cost_per_pct * abs(c.change_pct) for c in changes)
        self.cost += TOOL_CHANGE_COST if tool_change else 0.0


def _percent_grid(step_pct: float, max_pct: float) -> list[float]:
    """Non-zero percent changes from -max to +max."""
    count = int(round(max_pct / step_pct))
    return [step_pct * k for k in range(-count, count + 1) if k != 0]


def _lever_change(lever: str, current: float, percent: float) -> LeverChange | None:
    """A lever change clipped to the lever's allowed range (None if it changes nothing)."""
    settings = DEFAULT_CONFIG.levers[lever]
    target = min(settings.maximum, max(settings.minimum, current * (1.0 + percent / 100.0)))
    if current == 0 or abs(target - current) / abs(current) < 0.005:
        return None
    return LeverChange(
        lever=lever, from_value=current, to_value=target, change_pct=100.0 * (target - current) / current
    )


def build_candidates(current: dict[str, float], levers: list[str]) -> list[_Candidate]:
    """No action, single-lever grid, lever-pair grid, and a tool change."""
    candidates = [_Candidate("no action", [], False)]
    seen: set[tuple[tuple[str, float], ...]] = set()
    for lever in levers:
        max_pct = LEVER_COSTS[lever].max_change_pct
        for percent in _percent_grid(OPTIMIZER_SINGLE_STEP_PCT, max_pct):
            change = _lever_change(lever, current[lever], percent)
            if change is None or ((lever, round(change.to_value, 6)),) in seen:
                continue
            seen.add(((lever, round(change.to_value, 6)),))
            candidates.append(_Candidate(f"{lever} {change.change_pct:+.0f}%", [change], False))
    for first, second in itertools.combinations(levers, 2):
        for first_pct in _percent_grid(OPTIMIZER_PAIR_STEP_PCT, LEVER_COSTS[first].max_change_pct):
            for second_pct in _percent_grid(OPTIMIZER_PAIR_STEP_PCT, LEVER_COSTS[second].max_change_pct):
                first_change = _lever_change(first, current[first], first_pct)
                second_change = _lever_change(second, current[second], second_pct)
                if first_change is None or second_change is None:
                    continue
                key = ((first, round(first_change.to_value, 6)), (second, round(second_change.to_value, 6)))
                if key in seen:
                    continue
                seen.add(key)
                label = f"{first} {first_change.change_pct:+.0f}%, {second} {second_change.change_pct:+.0f}%"
                candidates.append(_Candidate(label, [first_change, second_change], False))
    candidates.append(_Candidate("tool change", [], True))
    return candidates


def _ranking_key(risk: float, cost: float, target: float) -> tuple[int, float, float]:
    """Options that reach the target first (cheapest first), then the rest by risk."""
    if risk <= target:
        return (0, cost, risk)
    return (1, risk, cost)


def shortest_lag_path(graph: CausalGraph, source: str, target: str = OUTCOME) -> tuple[list[str], int] | None:
    """Directed causal path from source to target with the smallest total lag (Dijkstra)."""
    edges = [edge for edge in graph.edges if not edge.is_self_loop and edge.oriented]
    queue: list[tuple[int, str, list[str]]] = [(0, source, [source])]
    best: dict[str, int] = {}
    while queue:
        total, node, path = heapq.heappop(queue)
        if node == target:
            return path, total
        if node in best and best[node] <= total:
            continue
        best[node] = total
        for edge in edges:
            if edge.cause == node and edge.effect not in path:
                heapq.heappush(queue, (total + edge.lag, edge.effect, [*path, edge.effect]))
    return None


def _confidence(
    engine: CausalTwinEngine, paths: list[list[str]], risk_before: RiskEstimate, risk_after: RiskEstimate
) -> RecommendationConfidence:
    """Combine path edge confidence, interval tightness, and equation validation accuracy."""
    graph = engine.get_graph()
    edge_confidence = {(edge.cause, edge.effect): edge.confidence for edge in graph.edges}
    path_values = [edge_confidence.get((path[i], path[i + 1])) or 0.0 for path in paths for i in range(len(path) - 1)]
    path_score = min(path_values) if path_values else None
    width = risk_after.upper - risk_after.lower
    interval_score = float(np.clip(1.0 - width / max(risk_before.probability, 0.01), 0.0, 1.0))
    cards = {card.variable: card for card in engine.get_model_cards()}
    validation_values = []
    for path in paths:
        for node in path[1:]:
            card = cards.get(node)
            if card is None:
                continue
            if card.kind == "logistic" and card.auc_holdout is not None:
                validation_values.append(float(np.clip((card.auc_holdout - 0.5) * 2.0, 0.0, 1.0)))
            elif card.r_squared_holdout is not None:
                validation_values.append(float(np.clip(card.r_squared_holdout, 0.0, 1.0)))
    validation_score = float(np.mean(validation_values)) if validation_values else None
    components = [value for value in (path_score, interval_score, validation_score) if value is not None]
    combined = float(np.prod(components) ** (1.0 / len(components))) if components else None
    explanation = (
        f"Path edges found in at least {path_score:.0%} of bootstrap resamples; "
        if path_score is not None
        else "No causal path to failure; "
    ) + (
        f"predicted risk interval {risk_after.lower:.1%}-{risk_after.upper:.1%} (interval score {interval_score:.2f}); "
        + (
            f"held-out accuracy of the equations on the path {validation_score:.2f}."
            if validation_score is not None
            else "no equation accuracy available."
        )
    )
    return RecommendationConfidence(
        path_edge_confidence=path_score,
        interval_score=interval_score,
        validation_score=validation_score,
        combined=combined,
        explanation=explanation,
    )


def _side_effects(changes: list[LeverChange], tool_change: bool) -> list[str]:
    """Plain-language side effects of the chosen action."""
    effects = []
    for change in changes:
        costs = LEVER_COSTS[change.lever]
        template = costs.side_effect_increase if change.change_pct > 0 else costs.side_effect_decrease
        effects.append(f"{change.lever}: " + template.format(pct=abs(change.change_pct)))
    if tool_change:
        effects.append(TOOL_CHANGE_SIDE_EFFECT)
    return effects


def recommend(
    engine: CausalTwinEngine,
    history: np.ndarray,
    target_risk: float = OPTIMIZER_TARGET_RISK,
    horizon: int | None = None,
    levers: list[str] | None = None,
) -> Recommendation:
    """Search all candidate interventions and return the recommendation with the full table."""
    model = engine._require_fitted()
    if model.failure is None:
        raise ValueError("No failure model is available, so no risk-based recommendation can be made")
    horizon = horizon or engine.settings.whatif_horizon
    lever_names = [lever for lever in (levers or list(LEVER_NAMES)) if lever in model.variables]
    with timed_step("recommend", "Searched candidate interventions") as timer:
        start_time = time.perf_counter()
        current = {name: float(history[-1, model.variables.index(name)]) for name in model.variables}
        candidates = build_candidates(current, lever_names)
        causal_ancestors = ancestors_of(engine.get_graph(), OUTCOME)
        symptoms = [
            name
            for name, correlation in engine.failure_correlations.items()
            if abs(correlation) >= engine.settings.symptom_threshold and name not in causal_ancestors
        ]
        symptom_candidates = [
            [Intervention(variable=name, change_type="percent", value=SYMPTOM_TEST_PCT)] for name in symptoms
        ]
        evaluation = batch_failure_risk(
            model,
            history,
            [c.interventions for c in candidates] + symptom_candidates,
            horizon,
            OPTIMIZER_SEARCH_ROLLOUTS,
            OPTIMIZER_SEED,
            WHATIF_INTERVAL_PERCENTILES,
            engine.settings.whatif_exogenous_mode,
        )
        risks = evaluation["risk"]
        baseline_risk = float(risks[0])
        planning_target = target_risk * OPTIMIZER_PLANNING_MARGIN
        order = sorted(
            range(len(candidates)), key=lambda i: _ranking_key(float(risks[i]), candidates[i].cost, planning_target)
        )

        # Re-evaluate the top candidates with more rollouts and INDEPENDENT noise, and choose among them.
        finalists = [i for i in order if i != 0][:OPTIMIZER_TOP_K]
        final = batch_failure_risk(
            model,
            history,
            [[]] + [candidates[i].interventions for i in finalists],
            horizon,
            OPTIMIZER_FINAL_ROLLOUTS,
            OPTIMIZER_FINAL_SEED,
            WHATIF_INTERVAL_PERCENTILES,
            engine.settings.whatif_exogenous_mode,
        )
        final_risk = {
            i: RiskEstimate(
                probability=float(final["risk"][k + 1]),
                lower=float(final["risk_lower"][k + 1]),
                upper=float(final["risk_upper"][k + 1]),
            )
            for k, i in enumerate(finalists)
        }
        chosen = min(
            finalists, key=lambda i: _ranking_key(final_risk[i].probability, candidates[i].cost, planning_target)
        )
        risk_before = RiskEstimate(
            probability=float(final["risk"][0]),
            lower=float(final["risk_lower"][0]),
            upper=float(final["risk_upper"][0]),
        )
        risk_after = final_risk[chosen]
        action_needed = risk_before.probability > target_risk
        meets_target = risk_after.probability <= target_risk
        if action_needed and risk_after.probability >= risk_before.probability:
            action_needed = False  # nothing helps; do not recommend a costly change that does nothing
        chosen_candidate = candidates[chosen] if action_needed else candidates[0]

        table = [
            CandidateResult(
                rank=rank + 1,
                label=candidates[i].label,
                changes=candidates[i].changes,
                tool_change=candidates[i].tool_change,
                risk=float(risks[i]),
                risk_lower=float(evaluation["risk_lower"][i]),
                risk_upper=float(evaluation["risk_upper"][i]),
                risk_reduction=baseline_risk - float(risks[i]),
                cost=candidates[i].cost,
                meets_target=bool(risks[i] <= target_risk),
            )
            for rank, i in enumerate(order)
        ]
        ineffective = _ineffective(candidates, risks, baseline_risk, lever_names, symptoms, risks[len(candidates) :])

        sources = [c.lever for c in chosen_candidate.changes] + ([TOOL_STATE] if chosen_candidate.tool_change else [])
        found_paths = [p for p in (shortest_lag_path(engine.get_graph(), source) for source in sources) if p]
        paths = [path for path, _ in found_paths]
        time_to_effect = min((lag for _, lag in found_paths), default=None)
        if not action_needed:
            risk_after = risk_before
            meets_target = risk_before.probability <= target_risk
        confidence = _confidence(engine, paths, risk_before, risk_after)
        explanation = _explanation(
            chosen_candidate, risk_before, risk_after, target_risk, meets_target, action_needed, paths, time_to_effect
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        timer.details.update({"candidates": len(candidates), "chosen": chosen_candidate.label})
    return Recommendation(
        action_type="none" if not action_needed else ("tool_change" if chosen_candidate.tool_change else "levers"),
        changes=chosen_candidate.changes,
        tool_change=chosen_candidate.tool_change,
        risk_before=risk_before,
        risk_after=risk_after,
        target_risk=target_risk,
        meets_target=meets_target,
        cost=chosen_candidate.cost,
        side_effects=_side_effects(chosen_candidate.changes, chosen_candidate.tool_change),
        time_to_effect_steps=time_to_effect,
        time_to_effect_seconds=None if time_to_effect is None else time_to_effect * SECONDS_PER_STEP,
        causal_paths=[" -> ".join(path) for path in paths],
        confidence=confidence,
        explanation=explanation,
        candidates=table,
        ineffective=ineffective,
        n_candidates=len(candidates),
        search_ms=elapsed_ms,
    )


def _ineffective(
    candidates: list[_Candidate],
    risks: np.ndarray,
    baseline_risk: float,
    levers: list[str],
    symptoms: list[str],
    symptom_risks: np.ndarray,
) -> list[IneffectiveLever]:
    """Levers whose best single change barely helps, and symptoms (acting on them does nothing)."""
    results = []
    for lever in levers:
        reductions = [
            baseline_risk - risks[i]
            for i, c in enumerate(candidates)
            if len(c.changes) == 1 and c.changes[0].lever == lever and not c.tool_change
        ]
        best = max(reductions, default=0.0)
        if best < INEFFECTIVE_RISK_REDUCTION:
            results.append(
                IneffectiveLever(
                    variable=lever,
                    best_risk_reduction=float(best),
                    reason=f"The best single change of {lever} lowers predicted risk by only {best:.1%}.",
                )
            )
    for name, risk in zip(symptoms, symptom_risks, strict=True):
        change = baseline_risk - float(risk)
        results.append(
            IneffectiveLever(
                variable=name,
                best_risk_reduction=change,
                reason=(
                    f"{name} has no causal path to failure: reducing it by {abs(SYMPTOM_TEST_PCT):.0f}% changes "
                    f"predicted risk by {change:+.1%}. It is a symptom, not a lever."
                ),
            )
        )
    return results


def _explanation(
    candidate: _Candidate,
    risk_before: RiskEstimate,
    risk_after: RiskEstimate,
    target: float,
    meets_target: bool,
    action_needed: bool,
    paths: list[list[str]],
    time_to_effect: int | None,
) -> str:
    """Plain-English summary of the recommendation."""
    before = f"{risk_before.probability:.1%} ({risk_before.lower:.1%}-{risk_before.upper:.1%})"
    if not action_needed:
        if risk_before.probability <= target:
            return (
                f"No action needed: predicted failure risk within the horizon is {before}, "
                f"below the {target:.0%} target."
            )
        return (
            f"Predicted failure risk is {before}, above the {target:.0%} target, but no evaluated change "
            f"lowers it. Consider maintenance; no lever change is recommended."
        )
    parts = [f"{c.lever} from {c.from_value:.3g} to {c.to_value:.3g} ({c.change_pct:+.0f}%)" for c in candidate.changes]
    if candidate.tool_change:
        parts.append("change the tool")
    action = "; ".join(parts)
    after = f"{risk_after.probability:.1%} ({risk_after.lower:.1%}-{risk_after.upper:.1%})"
    goal = (
        f"below the {target:.0%} target; it is the cheapest evaluated option that reaches it"
        if meets_target
        else f"still above the {target:.0%} target: no evaluated option reaches it, so this is the largest reduction"
    )
    route = (
        f" Effect starts after about {time_to_effect} steps ({time_to_effect * SECONDS_PER_STEP:.0f} s) via "
        f"{'; '.join(' -> '.join(p) for p in paths)}."
        if time_to_effect is not None and not math.isnan(time_to_effect)
        else ""
    )
    return (
        f"Recommended: {action}. Predicted failure risk within the horizon drops from {before} to {after}, "
        f"{goal}. Cost {candidate.cost:.1f} units (illustrative).{route}"
    )
