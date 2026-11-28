"""What-if engine: Monte Carlo rollouts of the fitted causal model under do-interventions.

Choices (shown to users in the "How was this computed?" drawer):
- Intervened variables are forced to their value from their start step (the do
  operation), which cuts all their incoming causal edges.
- Exogenous variables that are not intervened on (levers nobody touches, ambient
  temperature) follow a random walk built from their own historically observed one-step
  changes, clipped to their observed range ("random_walk"), or stay at their last value
  ("constant", the default). Measured on the Phase 3 benchmark situations, constant is well
  calibrated, while the random walk drifts levers too far (operator adjustments revert to a
  setpoint instead of accumulating) and, clipped at range edges, invents optimism.
- Noise is resampled from each equation's own fitted residuals (empirical distribution),
  so rare jumps such as tool chipping are reproduced.
- The baseline run uses exactly the same noise draws, so the reported change is caused
  by the intervention alone.
- The failure model's coefficients are uncertain (they were estimated from a limited number
  of failures), so every rollout draws its own coefficients from their estimated sampling
  distribution. The risk and its interval include this; uncertainty in the linear
  equations' coefficients is not included.
- Phase 7: with WHATIF_EQUATION_PARAMETER_UNCERTAINTY (the default), every rollout also draws
  each linear equation's intercept and coefficients from their estimated sampling distribution
  (the Ridge covariance stored at fit time), on a random stream of its own, so the noise and the
  failure-model draws are unchanged. The risk and its interval then include this as well.
"""

import time

import numpy as np

from causaltwin.config import (
    WHATIF_EQUATION_PARAMETER_STREAM,
    WHATIF_EQUATION_PARAMETER_UNCERTAINTY,
    WHATIF_FAILURE_PARAMETER_UNCERTAINTY,
    WHATIF_PARAMETER_STREAM,
)
from causaltwin.engine.causal_model import CausalModel
from causaltwin.engine.schemas import Intervention, RiskEstimate, VariableForecast, WhatIfResult
from causaltwin.utils.timing import timed_step

# The batched search uses 32-bit floats: half the memory traffic; risk differs from the 64-bit
# single what-if by well under 0.1 percentage points (checked in the tests).
STATE_DTYPE = np.float32

ASSUMPTIONS = [
    "Intervened variables are forced to their value (do operation); their incoming edges are cut.",
    "Exogenous variables that are not intervened on stay at their last observed value.",
    "Noise is resampled from each equation's fitted residuals; the baseline uses the same draws.",
    "Linear equations and a logistic failure model; no hidden confounders assumed.",
    (
        "Risk and interval include process noise and the coefficient uncertainty of the failure model and of "
        "the linear equations (standard approximation, rows treated as independent)."
        if WHATIF_EQUATION_PARAMETER_UNCERTAINTY
        else "Risk and interval include process noise and the failure model's coefficient uncertainty, not the "
        "uncertainty of the linear equations' coefficients."
    ),
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


Forcing = dict[int, tuple[float, int, bool]]  # variable index -> (value, start step, is_reset)


def _is_forced(forcing: tuple[float, int, bool] | None, step: int, is_exogenous: bool) -> bool:
    """Whether a forced value applies at this step ("reset" only at its start, except for exogenous)."""
    if forcing is None:
        return False
    _, start, is_reset = forcing
    if is_reset and not is_exogenous:
        return step == start
    return step >= start


def _forcing(model: CausalModel, interventions: list[Intervention], last_row: np.ndarray) -> Forcing:
    """Translate interventions into forced values per variable index."""
    forced: Forcing = {}
    for intervention in interventions:
        if intervention.variable not in model.variables:
            raise ValueError(f"Cannot intervene on unknown variable '{intervention.variable}'")
        position = model.variables.index(intervention.variable)
        forced[position] = (
            _target_value(intervention, float(last_row[position])),
            intervention.start_step,
            intervention.change_type == "reset",
        )
    return forced


def _draw_noise(
    model: CausalModel, rng: np.random.Generator, n_rollouts: int, horizon: int, exogenous_mode: str
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Residual indices per equation, then (random-walk mode) step indices per exogenous variable."""
    noise_draws = {
        name: rng.integers(0, equation.residuals.size, size=(n_rollouts, horizon))
        for name, equation in model.equations.items()
    }
    exogenous_draws: dict[str, np.ndarray] = {}
    if exogenous_mode == "random_walk":
        exogenous_draws = {
            name: rng.integers(0, steps.size, size=(n_rollouts, horizon))
            for name, steps in sorted(model.exogenous_steps.items())
        }
    return noise_draws, exogenous_draws


def _failure_parameters(model: CausalModel, seed: int, n_rollouts: int) -> np.ndarray | None:
    """Failure-model (intercept, coefficients...) for each rollout, shape (rollouts, 1 + terms).

    Drawn from a normal distribution around the fitted values with the fit's covariance,
    from a random stream of its own, so the process-noise draws stay exactly the same.
    Without a usable covariance every rollout uses the fitted values.
    """
    failure = model.failure
    if failure is None:
        return None
    point = np.concatenate([[failure.intercept], failure.coefficients])
    covariance = failure.covariance
    if not WHATIF_FAILURE_PARAMETER_UNCERTAINTY or covariance is None or covariance.shape != (point.size, point.size):
        return np.tile(point, (n_rollouts, 1))
    rng = np.random.default_rng([seed, WHATIF_PARAMETER_STREAM])
    return rng.multivariate_normal(point, covariance, size=n_rollouts, method="eigh")


def _equation_parameters(model: CausalModel, seed: int, n_rollouts: int) -> dict[str, np.ndarray]:
    """Linear-equation (intercept, coefficients...) for each rollout, per equation: (rollouts, 1 + terms).

    Drawn around the fitted values with each fit's covariance, from a random stream of its own
    (separate from the noise and the failure-model streams), so those draws stay exactly the same.
    Equations without a usable covariance, or every equation when the switch is off, are left out
    and use their fitted values.
    """
    if not WHATIF_EQUATION_PARAMETER_UNCERTAINTY:
        return {}
    rng = np.random.default_rng([seed, WHATIF_EQUATION_PARAMETER_STREAM])
    parameters = {}
    for name in sorted(model.equations):
        equation = model.equations[name]
        point = np.concatenate([[equation.intercept], equation.coefficients])
        covariance = getattr(equation, "covariance", None)  # models pickled before Phase 7 have none
        if covariance is None or covariance.shape != (point.size, point.size):
            continue
        parameters[name] = rng.multivariate_normal(point, covariance, size=n_rollouts, method="eigh")
    return parameters


def _simulate(
    model: CausalModel,
    history: np.ndarray,
    forced: Forcing,
    horizon: int,
    noise_draws: dict[str, np.ndarray],
    noise_scale: dict[str, float],
    exogenous_draws: dict[str, np.ndarray] | None = None,
    failure_parameters: np.ndarray | None = None,
    equation_parameters: dict[str, np.ndarray] | None = None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Roll the model forward. Returns paths (rollouts, history + horizon, variables) and hazards.

    `failure_parameters` (rollouts, 1 + terms) gives each rollout its own failure-model
    intercept and coefficients; without it the fitted values are used.
    `equation_parameters` does the same for the linear equations it contains (by target name).
    """
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
        for name, draws in (exogenous_draws or {}).items():
            position = index[name]
            low, high = model.exogenous_ranges[name]
            walk = paths[:, row - 1, position] + model.exogenous_steps[name][draws[:, step]]
            paths[:, row, position] = np.clip(walk, low, high)
        for position, forcing in forced.items():
            if model.variables[position] in model.exogenous and _is_forced(forcing, step, True):
                paths[:, row, position] = forcing[0]
        for name in order:
            equation = model.equations[name]
            position = index[name]
            endogenous_forcing = forced.get(position)
            if endogenous_forcing is not None and _is_forced(endogenous_forcing, step, False):
                paths[:, row, position] = endogenous_forcing[0]
                continue
            drawn = (equation_parameters or {}).get(name)
            if drawn is None:
                value = np.full(n_rollouts, equation.intercept)
                for (cause, lag), coefficient in zip(equation.terms, equation.coefficients, strict=True):
                    value += coefficient * paths[:, row - lag, index[cause]]
            else:
                value = drawn[:, 0].copy()
                for term_position, (cause, lag) in enumerate(equation.terms):
                    value += drawn[:, 1 + term_position] * paths[:, row - lag, index[cause]]
            value += noise_scale.get(name, 1.0) * equation.residuals[noise_draws[name][:, step]]
            paths[:, row, position] = value
        if hazards is not None and model.failure is not None:
            parameters = (
                failure_parameters
                if failure_parameters is not None
                else np.tile(model.failure.point_parameters(), (n_rollouts, 1))
            )
            # The failure model's own logit (linear or spline), through the shared interface.
            causes = [paths[:, row - lag, index[cause]] for cause, lag in model.failure.terms]
            logit = model.failure.logit(causes, parameters)
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
    exogenous_mode: str = "constant",
) -> WhatIfResult:
    """Simulate the interventions and a no-intervention baseline with the same noise.

    `history` holds the most recent rows (oldest first) of `model.variables`; at least
    `model.max_lag` rows (one row in tabular mode).
    """
    # With no interventions this is a plain risk estimate; the live loop runs one every step,
    # so it is logged as a routine entry that does not flood the main activity log.
    message = (
        f"Simulated {len(interventions)} intervention(s)"
        if interventions
        else "Estimated failure risk with no intervention"
    )
    details = {"n_interventions": len(interventions)}
    with timed_step("what_if", message, details=details, routine=not interventions) as timer:
        start_time = time.perf_counter()
        needed_rows = max(model.max_lag, 1)
        if history.shape[0] < needed_rows:
            raise ValueError(f"History needs at least {needed_rows} rows, got {history.shape[0]}")
        history = history[-needed_rows:]
        if not model.time_structure:
            horizon = 1
        rng = np.random.default_rng(seed)
        noise_draws, exogenous_draws = _draw_noise(model, rng, n_rollouts, horizon, exogenous_mode)
        forced = _forcing(model, interventions, history[-1])
        scale = noise_scale or {}
        parameters = _failure_parameters(model, seed, n_rollouts)
        equation_parameters = _equation_parameters(model, seed, n_rollouts)
        paths, hazards = _simulate(
            model, history, forced, horizon, noise_draws, scale, exogenous_draws, parameters, equation_parameters
        )
        baseline_paths, baseline_hazards = _simulate(
            model, history, {}, horizon, noise_draws, scale, exogenous_draws, parameters, equation_parameters
        )

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


def batch_failure_risk(
    model: CausalModel,
    history: np.ndarray,
    candidates: list[list[Intervention]],
    horizon: int,
    n_rollouts: int,
    seed: int,
    percentiles: tuple[float, float],
    exogenous_mode: str = "constant",
) -> dict[str, np.ndarray]:
    """Evaluate many intervention sets at once (used by the optimizer's search).

    Every candidate uses the same noise draws (common random numbers), drawn exactly as
    in `run_what_if` with the same seed, so a candidate here gives the same rollouts as
    `run_what_if` would. Only a rolling buffer of the last `max_lag` rows is kept, which
    keeps memory small for about 1,000 candidates x 100 rollouts.

    Returns per-candidate arrays: "risk", "risk_lower", "risk_upper" (shape C) and
    "end_values" (shape C x rollouts x variables, the state at the end of the horizon).
    Also "per_rollout_risk" (shape C x rollouts): each rollout's probability of a failure
    within the horizon, used by the optimizer's confidence rule.
    """
    if model.failure is None:
        raise ValueError("The model has no failure equation, so risk cannot be evaluated")
    needed_rows = max(model.max_lag, 1)
    history = history[-needed_rows:]
    n_candidates, n_variables = len(candidates), history.shape[1]
    n_rows = n_candidates * n_rollouts
    index = {name: position for position, name in enumerate(model.variables)}
    rng = np.random.default_rng(seed)
    noise_draws, exogenous_draws = _draw_noise(model, rng, n_rollouts, horizon, exogenous_mode)
    parameters = _failure_parameters(model, seed, n_rollouts)
    assert parameters is not None
    tiled_parameters = np.tile(parameters, (n_candidates, 1))  # same draws for every candidate
    # Per-rollout linear-equation parameters, one row array per (intercept, coefficient), same for every candidate.
    tiled_equation_parameters = {
        name: [np.tile(drawn[:, column], n_candidates).astype(STATE_DTYPE) for column in range(drawn.shape[1])]
        for name, drawn in _equation_parameters(model, seed, n_rollouts).items()
    }
    walk_steps = {
        name: model.exogenous_steps[name][draws].astype(STATE_DTYPE) for name, draws in exogenous_draws.items()
    }  # (rollouts, horizon), shared by every candidate
    forced_value = np.full((n_candidates, n_variables), np.nan)
    forced_start = np.zeros((n_candidates, n_variables), dtype=int)
    forced_reset = np.zeros((n_candidates, n_variables), dtype=bool)
    for candidate_index, interventions in enumerate(candidates):
        for position, (forced_level, start, is_reset) in _forcing(model, interventions, history[-1]).items():
            forced_value[candidate_index, position] = forced_level
            forced_start[candidate_index, position] = start
            forced_reset[candidate_index, position] = is_reset
    exogenous = {index[name] for name in model.exogenous}
    # Per forced variable: rows (candidate x rollout) and value, expanded once.
    forcings = []
    for position in range(n_variables):
        candidate_mask = ~np.isnan(forced_value[:, position])
        if candidate_mask.any():
            forcings.append(
                (
                    position,
                    np.repeat(candidate_mask, n_rollouts),
                    np.repeat(np.nan_to_num(forced_value[:, position]), n_rollouts),
                    np.repeat(forced_start[:, position], n_rollouts),
                    np.repeat(forced_reset[:, position], n_rollouts) & (position not in exogenous),
                )
            )

    def apply_forcing(state: np.ndarray, position: int, step: int) -> None:
        """Overwrite forced rows of one variable (state is variables x rows)."""
        for forced_position, mask, values, starts, resets in forcings:
            if forced_position != position:
                continue
            active = mask & (starts <= step) & (~resets | (starts == step))
            state[position, active] = values[active]

    # Variable-major layout (variables x rows): each variable is one contiguous array.
    history = history.astype(STATE_DTYPE)
    recent = [np.broadcast_to(row[:, None], (n_variables, n_rows)) for row in history]  # oldest first
    survival = np.ones(n_rows)
    order = _evaluation_order(model)
    noise_values = {
        name: equation.residuals[noise_draws[name]].astype(STATE_DTYPE) for name, equation in model.equations.items()
    }  # (rollouts, horizon), shared by every candidate
    for step in range(horizon):
        current = np.empty((n_variables, n_rows), dtype=STATE_DTYPE)
        current[:] = history[-1][:, None]
        for name, steps in walk_steps.items():
            low, high = model.exogenous_ranges[name]
            current[index[name]] = np.clip(recent[-1][index[name]] + np.tile(steps[:, step], n_candidates), low, high)
        for position in exogenous:
            apply_forcing(current, position, step)
        for name in order:
            equation = model.equations[name]
            position = index[name]
            drawn_columns = tiled_equation_parameters.get(name)
            if drawn_columns is None:
                value = np.full(n_rows, equation.intercept, dtype=STATE_DTYPE)
                for (cause, lag), coefficient in zip(equation.terms, equation.coefficients, strict=True):
                    source = current if lag == 0 else recent[-lag]
                    value += STATE_DTYPE(coefficient) * source[index[cause]]
            else:
                value = drawn_columns[0].copy()
                for term_position, (cause, lag) in enumerate(equation.terms):
                    source = current if lag == 0 else recent[-lag]
                    value += drawn_columns[1 + term_position] * source[index[cause]]
            value += np.tile(noise_values[name][:, step], n_candidates)
            current[position] = value
            apply_forcing(current, position, step)
        causes = [(current if lag == 0 else recent[-lag])[index[cause]] for cause, lag in model.failure.terms]
        logit = model.failure.logit(causes, tiled_parameters)  # linear or spline, through the shared interface
        survival *= 1.0 - 1.0 / (1.0 + np.exp(-np.clip(logit, -50, 50)))
        recent.append(current)
        if len(recent) > needed_rows:
            recent.pop(0)
    per_rollout_risk = (1.0 - survival).reshape(n_candidates, n_rollouts)
    lower, upper = np.percentile(per_rollout_risk, percentiles, axis=1)
    return {
        "risk": per_rollout_risk.mean(axis=1),
        "risk_lower": lower,
        "risk_upper": upper,
        "end_values": recent[-1].T.reshape(n_candidates, n_rollouts, n_variables),
        "per_rollout_risk": per_rollout_risk,
    }
