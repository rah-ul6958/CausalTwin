"""Fit structural equations on the discovered graph.

- Every non-exogenous variable gets a linear equation (Ridge with a small penalty) on its
  discovered causes at their discovered lags plus its own self-lags.
- Failure gets a logistic equation on its discovered causes.
Quality is measured on a held-out final share of the data (chronological split); the
final coefficients are then refitted on all usable rows. Residuals are kept so the
what-if engine can sample realistic noise (including non-Gaussian jumps such as tool
chipping) instead of assuming a normal distribution.
"""

import warnings
from dataclasses import dataclass, field

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import r2_score, roc_auc_score
from statsmodels.discrete.discrete_model import Logit

from causaltwin.engine.preprocessing import PreparedData, lagged_design
from causaltwin.engine.schemas import CausalGraph, EquationCard, EquationTerm
from causaltwin.utils.timing import timed_step


@dataclass
class LinearEquation:
    """target(t) = intercept + sum(coefficient * variable(t - lag)) + noise."""

    target: str
    terms: list[tuple[str, int]]
    coefficients: np.ndarray
    intercept: float
    residuals: np.ndarray
    residual_std: float
    r_squared_holdout: float | None
    n_train: int
    n_holdout: int

    def self_coefficient_sum(self) -> float:
        """Sum of the coefficients on the target's own lags (persistence)."""
        return float(sum(c for (name, _), c in zip(self.terms, self.coefficients, strict=True) if name == self.target))

    def cause_coefficient_sum(self, cause: str) -> float:
        """Sum of the coefficients on one cause over all its lags."""
        return float(sum(c for (name, _), c in zip(self.terms, self.coefficients, strict=True) if name == cause))


@dataclass
class LogisticEquation:
    """logit P(failure(t)) = intercept + sum(coefficient * variable(t - lag))."""

    target: str
    terms: list[tuple[str, int]]
    coefficients: np.ndarray
    intercept: float
    auc_holdout: float | None
    n_train: int
    n_holdout: int
    event_rate: float


@dataclass
class CausalModel:
    """All fitted equations plus what the what-if and root-cause steps need."""

    variables: list[str]
    exogenous: set[str]
    units: dict[str, str | None]
    equations: dict[str, LinearEquation]
    failure: LogisticEquation | None
    baseline: dict[str, float]  # median of each variable over usable rows ("normal level")
    time_structure: bool
    notes: list[str] = field(default_factory=list)

    @property
    def max_lag(self) -> int:
        """Longest lag used by any equation."""
        lags = [lag for equation in self.equations.values() for _, lag in equation.terms]
        if self.failure is not None:
            lags += [lag for _, lag in self.failure.terms]
        return max(lags, default=0)


def _format_equation(target: str, terms: list[tuple[str, int]], coefficients: np.ndarray, intercept: float) -> str:
    """Human-readable equation such as `y(t) = 0.82*y(t-1) + 0.15*x(t-2) + 3.10`."""
    parts: list[str] = []
    for (name, lag), coefficient in zip(terms, coefficients, strict=True):
        time_text = "t" if lag == 0 else f"t-{lag}"
        magnitude = f"{abs(coefficient):.4g}*{name}({time_text})"
        parts.append(magnitude if not parts and coefficient >= 0 else ("+ " if coefficient >= 0 else "- ") + magnitude)
    parts.append(("+ " if intercept >= 0 else "- ") + f"{abs(intercept):.4g}" if parts else f"{intercept:.4g}")
    return f"{target} = " + " ".join(parts)


def _equation_terms(graph: CausalGraph, target: str, time_structure: bool) -> list[tuple[str, int]]:
    """Discovered causes at their discovered lags, plus self-lags (at least lag 1 in time series)."""
    terms: list[tuple[str, int]] = []
    self_lags: list[int] = []
    for edge in graph.parents_of(target):
        if not edge.oriented:
            continue
        if edge.is_self_loop:
            self_lags = edge.lags
        else:
            terms += [(edge.cause, lag) for lag in edge.lags]
    if time_structure:
        terms = [(target, lag) for lag in (self_lags or [1])] + terms
    return terms


def _fit_linear(
    window: PreparedData,
    target: str,
    terms: list[tuple[str, int]],
    penalty: float,
    holdout_fraction: float,
    residual_limit: int,
    rng: np.random.Generator,
) -> LinearEquation | None:
    """Fit one Ridge equation with a chronological holdout."""
    design, target_values, _ = lagged_design(window, target, terms, first_row=0)
    if target_values.size < 20:
        return None
    n_train = int(target_values.size * (1.0 - holdout_fraction))
    r_squared = None
    if terms and target_values.size - n_train >= 5:
        holdout_model = Ridge(alpha=penalty).fit(design[:n_train], target_values[:n_train])
        r_squared = float(r2_score(target_values[n_train:], holdout_model.predict(design[n_train:])))
    if terms:
        model = Ridge(alpha=penalty).fit(design, target_values)
        coefficients, intercept = np.asarray(model.coef_, dtype=float), float(model.intercept_)
        residuals = target_values - model.predict(design)
    else:
        coefficients, intercept = np.zeros(0), float(target_values.mean())
        residuals = target_values - intercept
    if residuals.size > residual_limit:
        residuals = rng.choice(residuals, size=residual_limit, replace=False)
    return LinearEquation(
        target=target,
        terms=terms,
        coefficients=coefficients,
        intercept=intercept,
        residuals=residuals,
        residual_std=float(residuals.std()),
        r_squared_holdout=r_squared,
        n_train=n_train,
        n_holdout=target_values.size - n_train,
    )


def _fit_logistic(
    failure_history: PreparedData, terms: list[tuple[str, int]], holdout_fraction: float
) -> LogisticEquation | None:
    """Fit the failure equation on the failure history (stopped rows excluded)."""
    if failure_history.outcome is None or failure_history.outcome_values is None:
        return None
    first_row = max([lag for _, lag in terms], default=1)
    rows = np.arange(first_row, failure_history.n_rows)
    usable = ~failure_history.row_mask[rows]
    for _, lag in terms:
        usable &= ~failure_history.row_mask[rows - lag]
    rows = rows[usable]
    outcome = failure_history.outcome_values[rows]
    if outcome.sum() == 0:
        return None
    design = np.column_stack(
        [np.ones(rows.size)]
        + [failure_history.values[rows - lag, failure_history.index_of(name)] for name, lag in terms]
    )
    n_train = int(rows.size * (1.0 - holdout_fraction))
    auc = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if terms and outcome[:n_train].sum() > 0 and 0 < outcome[n_train:].sum() < outcome[n_train:].size:
            holdout_fit = Logit(outcome[:n_train], design[:n_train]).fit(disp=0, maxiter=200)
            auc = float(roc_auc_score(outcome[n_train:], holdout_fit.predict(design[n_train:])))
        fit = Logit(outcome, design).fit(disp=0, maxiter=200)
    parameters = np.asarray(fit.params, dtype=float)
    return LogisticEquation(
        target=failure_history.outcome,
        terms=terms,
        coefficients=parameters[1:],
        intercept=float(parameters[0]),
        auc_holdout=auc,
        n_train=n_train,
        n_holdout=rows.size - n_train,
        event_rate=float(outcome.mean()),
    )


def fit_causal_model(
    window: PreparedData,
    failure_history: PreparedData,
    graph: CausalGraph,
    penalty: float,
    holdout_fraction: float,
    residual_limit: int,
    seed: int,
) -> CausalModel:
    """Fit every equation of the causal model."""
    with timed_step("fit_model", "Fitted causal model") as timer:
        rng = np.random.default_rng(seed)
        equations: dict[str, LinearEquation] = {}
        notes = []
        for target in window.endogenous():
            equation = _fit_linear(
                window,
                target,
                _equation_terms(graph, target, window.time_structure),
                penalty,
                holdout_fraction,
                residual_limit,
                rng,
            )
            if equation is None:
                notes.append(f"{target}: not enough usable rows to fit an equation.")
            else:
                equations[target] = equation
        failure = None
        if window.outcome is not None:
            failure_terms = [(edge.cause, edge.lag) for edge in graph.parents_of(window.outcome)]
            failure = _fit_logistic(failure_history, failure_terms, holdout_fraction)
            if failure is not None and not failure_terms:
                notes.append("No failure causes were found; the failure model is the constant event rate.")
        usable = ~window.row_mask
        baseline = {name: float(np.median(window.values[usable, window.index_of(name)])) for name in window.variables}
        timer.details.update(
            {"equations": len(equations), "failure_terms": 0 if failure is None else len(failure.terms)}
        )
    return CausalModel(
        variables=window.variables,
        exogenous=window.exogenous,
        units=window.units,
        equations=equations,
        failure=failure,
        baseline=baseline,
        time_structure=window.time_structure,
        notes=notes,
    )


def model_cards(model: CausalModel) -> list[EquationCard]:
    """UI model cards for every fitted equation."""
    cards = []
    for equation in model.equations.values():
        time_suffix = "(t)"
        cards.append(
            EquationCard(
                variable=equation.target,
                kind="linear",
                terms=[
                    EquationTerm(variable=name, lag=lag, coefficient=float(coefficient))
                    for (name, lag), coefficient in zip(equation.terms, equation.coefficients, strict=True)
                ],
                intercept=equation.intercept,
                equation=_format_equation(
                    equation.target + time_suffix, equation.terms, equation.coefficients, equation.intercept
                ),
                r_squared_holdout=equation.r_squared_holdout,
                residual_std=equation.residual_std,
                n_train=equation.n_train,
                n_holdout=equation.n_holdout,
            )
        )
    if model.failure is not None:
        failure = model.failure
        cards.append(
            EquationCard(
                variable=failure.target,
                kind="logistic",
                terms=[
                    EquationTerm(variable=name, lag=lag, coefficient=float(coefficient))
                    for (name, lag), coefficient in zip(failure.terms, failure.coefficients, strict=True)
                ],
                intercept=failure.intercept,
                equation=_format_equation(
                    f"logit P({failure.target}(t))", failure.terms, failure.coefficients, failure.intercept
                ),
                auc_holdout=failure.auc_holdout,
                n_train=failure.n_train,
                n_holdout=failure.n_holdout,
                notes=[f"Observed failure rate {failure.event_rate:.2%} per step on running rows."],
            )
        )
    return cards
