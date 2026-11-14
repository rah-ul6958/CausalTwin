"""What-if engine: Monte Carlo rollouts of the fitted causal model under do-interventions.

Choices (shown to users in the "How was this computed?" drawer):
- Intervened variables are forced to their value from their start step (the do
  operation), which cuts all their incoming causal edges.
- Exogenous variables that are not intervened on stay at their last observed value
  (constant). A trend could be extrapolated, but over a 5-minute horizon a constant is
  safer and does not invent drift.
- Noise is resampled from each equation's own fitted residuals (empirical distribution),
  so rare jumps such as tool chipping are reproduced.
- The baseline run uses exactly the same noise draws, so the reported change is caused
  by the intervention alone.
- Parameter uncertainty is not included: the interval reflects process noise only.
"""

import time

import numpy as np

from causaltwin.engine.causal_model import CausalModel
from causaltwin.engine.schemas import Intervention, RiskEstimate, VariableForecast, WhatIfResult
from causaltwin.utils.timing import timed_step

ASSUMPTIONS = [
    "Intervened variables are forced to their value (do operation); their incoming edges are cut.",
    "Exogenous variables that are not intervened on stay at their last observed value.",
    "Noise is resampled from each equation's fitted residuals; the baseline uses the same draws.",
    "Linear equations and a logistic failure model; no hidden confounders assumed.",
    "Interval reflects process noise only, not uncertainty in the fitted coefficients.",
    "No tool changes or maintenance are simulated inside the horizon.",
]


def _evaluation_order(model: CausalModel) -> list[str]:
    """Endogenous variables in an order where same-step (lag 0) causes come first."""
    remaining = list(model.equations)
    ordered: list[str] = []
    while remaining:
        progress = False
        for name in list(remaining):
            same_step_causes = {
                cause for cause, lag in model.equations[name].terms if lag == 0 and cause in model.equations
            }
            if same_step_causes <= set(ordered):
                ordered.append(name)
                remaining.remove(name)
                progress = True
        if not progress:  # a same-step cycle cannot be simulated; fall back to listing order
            ordered += remaining
            break
    return ordered


def _target_value(intervention: Intervention, last_value: float) -> float:
    """The forced value of an intervention."""
    if intervention.change_type == "percent":
        return last_value * (1.0 + intervention.value / 100.0)
    return intervention.value


def _simulate(
    model: CausalModel,
    history: np.ndarray,
    forced: dict[int, tuple[float, int]],
    horizon: int,
    noise_draws: dict[str, np.ndarray],
    noise_scale: dict[str, float],
) -> tuple[np.ndarray, np.ndarray | None]:
    """Roll the model forward. Returns paths (rollouts, history + horizon, variables) and hazards."""
    n_rollouts = next(iter(noise_draws.values())).shape[0] if noise_draws else 1
    n_history, n_variables = history.shape
    paths = np.empty((n_rollouts, n_history + horizon, n_variables))
    paths[:, :n_history, :] = history
    index = {name: position for position, name in enumerate(model.variables)}
    order = _evaluation_order(model)
    hazards = np.empty((n_rollouts, horizon)) if model.failure is not None else None
    for step in range(horizon):
        row = n_history + step
        paths[:, row, :] = history[-1]  # exogenous default: last observed value
        for position, (forced_level, start) in forced.items():
            if step >= start and model.variables[position] in model.exogenous:
                paths[:, row, position] = forced_level
        for name in order:
            equation = model.equations[name]
            position = index[name]
            forced_value = forced.get(position)
            if forced_value is not None and step >= forced_value[1]:
                paths[:, row, position] = forced_value[0]
                continue
            value = np.full(n_rollouts, equation.intercept)
            for (cause, lag), coefficient in zip(equation.terms, equation.coefficients, strict=True):
                value += coefficient * paths[:, row - lag, index[cause]]
            value += noise_scale.get(name, 1.0) * equation.residuals[noise_draws[name][:, step]]
            paths[:, row, position] = value
        if hazards is not None and model.failure is not None:
            logit = np.full(n_rollouts, model.failure.intercept)
            for (cause, lag), coefficient in zip(model.failure.terms, model.failure.coefficients, strict=True):
                logit += coefficient * paths[:, row - lag, index[cause]]
            hazards[:, step] = 1.0 / (1.0 + np.exp(-np.clip(logit, -50, 50)))
    return paths, hazards


def _risk(hazards: np.ndarray, percentiles: tuple[float, float]) -> RiskEstimate:
    """Probability of at least one failure within the horizon, mean and interval over rollouts."""
    per_rollout = 1.0 - np.prod(1.0 - hazards, axis=1)
    lower, upper = np.percentile(per_rollout, percentiles)
    return RiskEstimate(probability=float(per_rollout.mean()), lower=float(lower), upper=float(upper))


def run_what_if(
    model: CausalModel,
    history: np.ndarray,
    interventions: list[Intervention],
    horizon: int,
    n_rollouts: int,
    seed: int,
    percentiles: tuple[float, float],
    noise_scale: dict[str, float] | None = None,
) -> WhatIfResult:
    """Simulate the interventions and a no-intervention baseline with the same noise.

    `history` holds the most recent rows (oldest first) of `model.variables`; at least
    `model.max_lag` rows (one row in tabular mode).
    """
    with timed_step("what_if", f"Simulated {len(interventions)} intervention(s)") as timer:
        start_time = time.perf_counter()
        needed_rows = max(model.max_lag, 1)
        if history.shape[0] < needed_rows:
            raise ValueError(f"History needs at least {needed_rows} rows, got {history.shape[0]}")
        history = history[-needed_rows:]
        if not model.time_structure:
            horizon = 1
        rng = np.random.default_rng(seed)
        noise_draws = {
            name: rng.integers(0, equation.residuals.size, size=(n_rollouts, horizon))
            for name, equation in model.equations.items()
        }
        forced: dict[int, tuple[float, int]] = {}
        for intervention in interventions:
            if intervention.variable not in model.variables:
                raise ValueError(f"Cannot intervene on unknown variable '{intervention.variable}'")
            position = model.variables.index(intervention.variable)
            forced[position] = (_target_value(intervention, float(history[-1, position])), intervention.start_step)
        scale = noise_scale or {}
        paths, hazards = _simulate(model, history, forced, horizon, noise_draws, scale)
        baseline_paths, baseline_hazards = _simulate(model, history, {}, horizon, noise_draws, scale)

        future = paths[:, needed_rows:, :]
        baseline_future = baseline_paths[:, needed_rows:, :]
        paired_difference = future - baseline_future
        forecasts = {}
        downstream_changes = {}
        for position, name in enumerate(model.variables):
            lower, upper = np.percentile(future[:, :, position], percentiles, axis=0)
            mean_change = float(np.median(paired_difference[:, :, position].mean(axis=1)))
            forecasts[name] = VariableForecast(
                variable=name,
                median=np.median(future[:, :, position], axis=0).tolist(),
                lower=lower.tolist(),
                upper=upper.tolist(),
                baseline_median=np.median(baseline_future[:, :, position], axis=0).tolist(),
                change_at_end=float(np.median(paired_difference[:, -1, position])),
                mean_change=mean_change,
            )
            downstream_changes[name] = mean_change
        risk = _risk(hazards, percentiles) if hazards is not None else None
        baseline_risk = _risk(baseline_hazards, percentiles) if baseline_hazards is not None else None
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        timer.details.update({"horizon": horizon, "rollouts": n_rollouts})
    return WhatIfResult(
        horizon=horizon,
        n_rollouts=n_rollouts,
        interventions=interventions,
        forecasts=forecasts,
        risk=risk,
        baseline_risk=baseline_risk,
        risk_change=None if risk is None or baseline_risk is None else risk.probability - baseline_risk.probability,
        downstream_changes=downstream_changes,
        computation_ms=elapsed_ms,
        assumptions=ASSUMPTIONS if model.time_structure else [*ASSUMPTIONS[:4], "Tabular mode: one-step propagation."],
    )
