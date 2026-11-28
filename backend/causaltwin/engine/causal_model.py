"""Fit structural equations on the discovered graph.

- Every non-exogenous variable gets a linear equation (Ridge with a small penalty) on its
  discovered causes at their discovered lags plus its own self-lags.
- Failure gets a logistic equation on its discovered causes.
Quality is measured on a held-out final share of the data (chronological split); the
final coefficients are then refitted on all usable rows. Residuals are kept so the
what-if engine can sample realistic noise (including non-Gaussian jumps such as tool
chipping) instead of assuming a normal distribution.
"""

import itertools
import warnings
from dataclasses import dataclass, field

import numpy as np
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import r2_score, roc_auc_score
from sklearn.preprocessing import SplineTransformer
from statsmodels.discrete.discrete_model import Logit

from causaltwin.config import (
    FAILURE_MODEL_KIND,
    FAILURE_MODEL_KINDS,
    FAILURE_SPLINE_C,
    FAILURE_SPLINE_DEGREE,
    FAILURE_SPLINE_GRID_POINTS,
    FAILURE_SPLINE_INTERACTIONS,
    FAILURE_SPLINE_KNOTS,
)
from causaltwin.engine.preprocessing import PreparedData, lagged_design
from causaltwin.engine.robust import robust_outliers
from causaltwin.engine.schemas import CausalGraph, EquationCard, EquationTerm, FailureCurve
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
    n_jumps: int = 0  # rare unexplained jumps left out of the fit (kept in `residuals`)
    # Approximate covariance of (intercept, coefficients...) of the fit; None if it could not be estimated.
    covariance: np.ndarray | None = None

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
    # Covariance of (intercept, coefficients...) from the fit; None if it could not be estimated.
    covariance: np.ndarray | None = None
    kind: str = "linear"

    # The failure-model interface shared with SplineFailureModel (used by what-if, optimizer, root cause).
    def point_parameters(self) -> np.ndarray:
        """(intercept, coefficients...) as one vector."""
        return np.concatenate([[self.intercept], self.coefficients])

    def logit(self, columns: list[np.ndarray], parameters: np.ndarray) -> np.ndarray:
        """Log-odds for rows of cause values (one array per term) with per-row parameters (rows, 1 + terms)."""
        logit = parameters[:, 0].copy()
        for position, values in enumerate(columns):
            logit += parameters[:, 1 + position] * values
        return logit

    def local_coefficients(self, current: np.ndarray, reference: np.ndarray) -> np.ndarray:
        """Change in log-odds per unit of each cause; for a linear model simply the coefficients."""
        return np.asarray(self.coefficients, dtype=float)


def _interpolate_basis(grid: np.ndarray, basis: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Basis functions tabulated on `grid` (G, n_basis), linearly interpolated at `values` (rows,).

    Values outside the grid take the value at the nearest end (constant extrapolation).
    """
    position = (np.asarray(values, dtype=float) - grid[0]) / (grid[-1] - grid[0]) * (grid.size - 1)
    position = np.clip(position, 0.0, grid.size - 1.0)
    lower = np.minimum(position.astype(int), grid.size - 2)
    weight = (position - lower)[:, None]
    return basis[lower] * (1.0 - weight) + basis[lower + 1] * weight


@dataclass
class SplineFailureModel:
    """logit P(failure(t)) = intercept + sum of one smooth curve per cause (+ optional pairwise products).

    Each curve is a cubic B-spline in that cause (knots at quantiles of the data), so the model can
    express thresholds and windows a straight line cannot, such as "risky below 3,500 W and above
    9,000 W". It uses only the discovered causes, like the linear model, and is fitted on the data
    alone. Each basis function is tabulated on a fine grid over the cause's training range and
    interpolated, which keeps evaluation fast inside the what-if engine and the optimizer; outside
    the training range a curve stays at its end value.
    """

    target: str
    terms: list[tuple[str, int]]
    coefficients: np.ndarray  # one per design column: the spline bases of every cause, then the products
    intercept: float
    auc_holdout: float | None
    n_train: int
    n_holdout: int
    event_rate: float
    grids: list[np.ndarray]  # per cause: evaluation grid over its training range
    bases: list[np.ndarray]  # per cause: basis values on the grid (G, n_basis)
    interactions: list[tuple[int, int]]  # pairs of cause positions whose standardised product is a column
    means: np.ndarray  # per cause, for the standardised products
    scales: np.ndarray
    n_knots: int
    covariance: np.ndarray | None = None
    kind: str = "spline"

    def point_parameters(self) -> np.ndarray:
        """(intercept, coefficients...) as one vector."""
        return np.concatenate([[self.intercept], self.coefficients])

    def design(self, columns: list[np.ndarray]) -> np.ndarray:
        """Design matrix (rows, coefficients) for rows of cause values, one array per term."""
        parts = [
            _interpolate_basis(grid, basis, values)
            for values, grid, basis in zip(columns, self.grids, self.bases, strict=True)
        ]
        for first, second in self.interactions:
            first_z = (np.asarray(columns[first], dtype=float) - self.means[first]) / self.scales[first]
            second_z = (np.asarray(columns[second], dtype=float) - self.means[second]) / self.scales[second]
            parts.append((first_z * second_z)[:, None])
        return np.hstack(parts)

    def logit(self, columns: list[np.ndarray], parameters: np.ndarray) -> np.ndarray:
        """Log-odds for rows of cause values (one array per term) with per-row parameters (rows, 1 + coefficients)."""
        design = self.design(columns)
        return parameters[:, 0] + np.einsum("ij,ij->i", design, parameters[:, 1:])

    def local_coefficients(self, current: np.ndarray, reference: np.ndarray) -> np.ndarray:
        """Change in log-odds per unit of each cause between `reference` and `current` (a secant slope).

        Where the two values (almost) coincide, the slope at `current` is used instead.
        """
        point = np.repeat(self.point_parameters()[None, :], 2, axis=0)
        slopes = np.zeros(len(self.terms))
        for position in range(len(self.terms)):
            width = float(self.grids[position][-1] - self.grids[position][0]) or 1.0
            start = np.array(current, dtype=float)
            step = float(current[position] - reference[position])
            if abs(step) < 1e-6 * width:
                step = width / 100.0  # no deviation: the slope at the current value
            else:
                start[position] = reference[position]  # the secant from the reference to the current value
            end = start.copy()
            end[position] += step
            logits = self.logit([np.array([start[k], end[k]]) for k in range(len(self.terms))], point)
            slopes[position] = float((logits[1] - logits[0]) / step)
        return slopes

    def curve(self, position: int, n_points: int = 60) -> tuple[np.ndarray, np.ndarray]:
        """How the log-odds depend on one cause across its range, the others held at their means."""
        values = np.linspace(self.grids[position][0], self.grids[position][-1], n_points)
        columns = [values if k == position else np.full(n_points, self.means[k]) for k in range(len(self.terms))]
        return values, self.logit(columns, np.repeat(self.point_parameters()[None, :], n_points, axis=0))


FailureModel = LogisticEquation | SplineFailureModel


@dataclass
class CausalModel:
    """All fitted equations plus what the what-if and root-cause steps need."""

    variables: list[str]
    exogenous: set[str]
    units: dict[str, str | None]
    equations: dict[str, LinearEquation]
    failure: FailureModel | None
    baseline: dict[str, float]  # median of each variable over usable rows ("normal level")
    time_structure: bool
    notes: list[str] = field(default_factory=list)
    # Observed one-step changes and ranges of each exogenous variable: the what-if engine lets
    # non-intervened exogenous variables follow a random walk built from these changes.
    exogenous_steps: dict[str, np.ndarray] = field(default_factory=dict)
    exogenous_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

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


def ridge_covariance(design: np.ndarray, residuals: np.ndarray, penalty: float) -> np.ndarray | None:
    """Approximate covariance of (intercept, coefficients...) of a Ridge fit with an intercept.

    scikit-learn's Ridge centres the columns and does not penalise the intercept. With the centred
    design Xc (n rows, p columns), A = Xc'Xc + penalty * I and the residual variance
    s2 = RSS / (n - p - 1), the standard approximation is
        Var(coefficients)               = s2 * A^-1 (Xc'Xc) A^-1
    and, because intercept = mean(y) - mean(x)' coefficients,
        Var(intercept)                  = s2 / n + mean(x)' Var(coefficients) mean(x)
        Cov(intercept, coefficients)    = -Var(coefficients) mean(x).
    It treats the rows as independent; with autocorrelated residuals the true sampling spread is
    larger, so this is, if anything, too narrow.
    """
    n_rows, n_terms = design.shape
    if n_terms == 0 or n_rows <= n_terms + 1:
        return None
    column_means = design.mean(axis=0)
    centred = design - column_means
    gram = centred.T @ centred
    inverse = np.linalg.inv(gram + penalty * np.eye(n_terms))
    residual_variance = float(residuals @ residuals) / (n_rows - n_terms - 1)
    coefficient_covariance = residual_variance * inverse @ gram @ inverse
    covariance = np.empty((n_terms + 1, n_terms + 1))
    covariance[1:, 1:] = coefficient_covariance
    covariance[0, 1:] = covariance[1:, 0] = -(coefficient_covariance @ column_means)
    covariance[0, 0] = residual_variance / n_rows + column_means @ coefficient_covariance @ column_means
    covariance = (covariance + covariance.T) / 2.0  # exact symmetry for the multivariate normal draw
    return covariance if np.all(np.isfinite(covariance)) else None


def _fit_linear(
    window: PreparedData,
    target: str,
    terms: list[tuple[str, int]],
    penalty: float,
    holdout_fraction: float,
    residual_limit: int,
    rng: np.random.Generator,
) -> LinearEquation | None:
    """Fit one Ridge equation with a chronological holdout.

    Rare jumps that the causes cannot explain (see engine/robust.py) are left out of the fit,
    so they do not distort the coefficients, but their residuals stay in the noise pool used by
    the what-if engine, so simulated futures still contain them at the observed rate. The
    held-out R squared is measured on every holdout row, jumps included.
    """
    design, target_values, _ = lagged_design(window, target, terms, first_row=0)
    if target_values.size < 20:
        return None
    n_train = int(target_values.size * (1.0 - holdout_fraction))
    keep = np.ones(target_values.size, dtype=bool)
    if terms:
        first_pass = Ridge(alpha=penalty).fit(design, target_values)
        keep = ~robust_outliers(target_values - first_pass.predict(design))
    r_squared = None
    if terms and target_values.size - n_train >= 5:
        train_keep = keep[:n_train]
        holdout_model = Ridge(alpha=penalty).fit(design[:n_train][train_keep], target_values[:n_train][train_keep])
        r_squared = float(r2_score(target_values[n_train:], holdout_model.predict(design[n_train:])))
    covariance = None
    if terms:
        model = Ridge(alpha=penalty).fit(design[keep], target_values[keep])
        coefficients, intercept = np.asarray(model.coef_, dtype=float), float(model.intercept_)
        residuals = target_values - model.predict(design)  # every row: jumps remain part of the noise
        covariance = ridge_covariance(design[keep], residuals[keep], penalty)
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
        n_jumps=int((~keep).sum()),
        covariance=covariance,
    )


def _failure_rows(failure_history: PreparedData, terms: list[tuple[str, int]]) -> np.ndarray | None:
    """Rows usable for the failure fit: running, with every lagged cause available (None: no outcome)."""
    if failure_history.outcome is None or failure_history.outcome_values is None:
        return None
    first_row = max([lag for _, lag in terms], default=1)
    rows = np.arange(first_row, failure_history.n_rows)
    usable = ~failure_history.row_mask[rows]
    for _, lag in terms:
        usable &= ~failure_history.row_mask[rows - lag]
    return rows[usable]


def _fit_logistic(
    failure_history: PreparedData, terms: list[tuple[str, int]], holdout_fraction: float
) -> LogisticEquation | None:
    """Fit the failure equation on the failure history (stopped rows excluded)."""
    rows = _failure_rows(failure_history, terms)
    if rows is None or failure_history.outcome is None or failure_history.outcome_values is None:
        return None
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
    covariance = np.asarray(fit.cov_params(), dtype=float)
    return LogisticEquation(
        target=failure_history.outcome,
        terms=terms,
        coefficients=parameters[1:],
        intercept=float(parameters[0]),
        auc_holdout=auc,
        n_train=n_train,
        n_holdout=rows.size - n_train,
        event_rate=float(outcome.mean()),
        covariance=covariance if np.all(np.isfinite(covariance)) else None,
    )


def _spline_bases(values: np.ndarray, n_knots: int, grid_points: int) -> tuple[np.ndarray, np.ndarray]:
    """A grid over the values' range and cubic B-spline basis values on it (G, n_basis).

    Knots sit at quantiles of the values (uniform spacing if the quantiles coincide). The basis that
    would duplicate the intercept is dropped.
    """
    grid = np.linspace(float(values.min()), float(values.max()), grid_points)
    if grid[-1] == grid[0]:
        grid = grid[0] + np.linspace(-0.5, 0.5, grid_points)
    for knots in ("quantile", "uniform"):
        try:
            transformer = SplineTransformer(
                n_knots=n_knots,
                degree=FAILURE_SPLINE_DEGREE,
                knots=knots,
                extrapolation="constant",
                include_bias=False,
            ).fit(values[:, None])
            return grid, transformer.transform(grid[:, None])
        except ValueError:
            continue
    raise ValueError("Could not place spline knots")


def _fit_spline_logistic(
    failure_history: PreparedData,
    terms: list[tuple[str, int]],
    holdout_fraction: float,
    interactions: bool,
) -> SplineFailureModel | None:
    """Fit a logistic model on spline features of the discovered failure causes (stopped rows excluded).

    L2-penalised (FAILURE_SPLINE_C), the intercept unpenalised. The covariance of the parameters is the
    inverse of the penalised Fisher information, X'WX + I / C (W = p(1 - p) at the fit), the usual
    approximation for a penalised logistic fit; the what-if engine draws each rollout's parameters from it.
    """
    rows = _failure_rows(failure_history, terms)
    if not terms or rows is None or failure_history.outcome is None or failure_history.outcome_values is None:
        return None
    outcome = failure_history.outcome_values[rows]
    if outcome.sum() == 0:
        return None
    columns = [failure_history.values[rows - lag, failure_history.index_of(name)] for name, lag in terms]
    grids, bases = zip(
        *(_spline_bases(values, FAILURE_SPLINE_KNOTS, FAILURE_SPLINE_GRID_POINTS) for values in columns),
        strict=True,
    )
    model = SplineFailureModel(
        target=failure_history.outcome,
        terms=terms,
        coefficients=np.zeros(0),
        intercept=0.0,
        auc_holdout=None,
        n_train=0,
        n_holdout=0,
        event_rate=float(outcome.mean()),
        grids=list(grids),
        bases=list(bases),
        interactions=list(itertools.combinations(range(len(terms)), 2)) if interactions else [],
        means=np.array([values.mean() for values in columns]),
        scales=np.array([values.std() or 1.0 for values in columns]),
        n_knots=FAILURE_SPLINE_KNOTS,
    )
    design = model.design(columns)
    n_train = int(rows.size * (1.0 - holdout_fraction))

    def classifier() -> LogisticRegression:
        return LogisticRegression(C=FAILURE_SPLINE_C, max_iter=2000)

    auc = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if outcome[:n_train].sum() > 0 and 0 < outcome[n_train:].sum() < outcome[n_train:].size:
            holdout_fit = classifier().fit(design[:n_train], outcome[:n_train])
            auc = float(roc_auc_score(outcome[n_train:], holdout_fit.predict_proba(design[n_train:])[:, 1]))
        fit = classifier().fit(design, outcome)
    model.coefficients = np.asarray(fit.coef_[0], dtype=float)
    model.intercept = float(fit.intercept_[0])
    model.auc_holdout, model.n_train, model.n_holdout = auc, n_train, rows.size - n_train
    probability = fit.predict_proba(design)[:, 1]
    with_intercept = np.column_stack([np.ones(rows.size), design])
    information = with_intercept.T @ (with_intercept * (probability * (1.0 - probability))[:, None])
    information += np.diag([0.0] + [1.0 / FAILURE_SPLINE_C] * design.shape[1])
    try:
        covariance = np.linalg.inv(information)
        model.covariance = (covariance + covariance.T) / 2.0 if np.all(np.isfinite(covariance)) else None
    except np.linalg.LinAlgError:
        model.covariance = None
    return model


def fit_failure_model(
    failure_history: PreparedData,
    terms: list[tuple[str, int]],
    holdout_fraction: float,
    kind: str = FAILURE_MODEL_KIND,
    interactions: bool = FAILURE_SPLINE_INTERACTIONS,
) -> FailureModel | None:
    """The failure model on the discovered causes: "linear" (logistic) or "spline" (logistic on splines).

    Without discovered causes there is nothing to bend, so the linear model (the constant event rate)
    is used whatever the kind.
    """
    if kind not in FAILURE_MODEL_KINDS:
        raise ValueError(f"Unknown failure model {kind!r}; use one of {FAILURE_MODEL_KINDS}")
    if kind == "spline" and terms:
        return _fit_spline_logistic(failure_history, terms, holdout_fraction, interactions)
    return _fit_logistic(failure_history, terms, holdout_fraction)


def _exogenous_dynamics(
    window: PreparedData, limit: int, rng: np.random.Generator
) -> tuple[dict[str, np.ndarray], dict[str, tuple[float, float]]]:
    """One-step changes (consecutive usable rows) and observed range of each exogenous variable."""
    steps: dict[str, np.ndarray] = {}
    ranges: dict[str, tuple[float, float]] = {}
    if not window.time_structure:
        return steps, ranges
    both_usable = ~window.row_mask[1:] & ~window.row_mask[:-1]
    usable = ~window.row_mask
    for name in sorted(window.exogenous):
        column = window.values[:, window.index_of(name)]
        changes = np.diff(column)[both_usable]
        if changes.size > limit:
            changes = rng.choice(changes, size=limit, replace=False)
        steps[name] = changes if changes.size else np.zeros(1)
        ranges[name] = (float(column[usable].min()), float(column[usable].max()))
    return steps, ranges


def fit_causal_model(
    window: PreparedData,
    failure_history: PreparedData,
    graph: CausalGraph,
    penalty: float,
    holdout_fraction: float,
    residual_limit: int,
    seed: int,
    failure_model_kind: str = FAILURE_MODEL_KIND,
    failure_interactions: bool = FAILURE_SPLINE_INTERACTIONS,
) -> CausalModel:
    """Fit every equation of the causal model (the failure model of the given kind)."""
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
            failure = fit_failure_model(
                failure_history, failure_terms, holdout_fraction, failure_model_kind, failure_interactions
            )
            if failure is not None and not failure_terms:
                notes.append("No failure causes were found; the failure model is the constant event rate.")
        usable = ~window.row_mask
        baseline = {name: float(np.median(window.values[usable, window.index_of(name)])) for name in window.variables}
        exogenous_steps, exogenous_ranges = _exogenous_dynamics(window, residual_limit, rng)
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
        exogenous_steps=exogenous_steps,
        exogenous_ranges=exogenous_ranges,
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
                notes=(
                    [
                        f"{equation.n_jumps} rare jumps that the causes cannot explain (for example tool "
                        "chipping) were left out of the fit; they remain in the simulated noise."
                    ]
                    if equation.n_jumps
                    else []
                ),
            )
        )
    if isinstance(model.failure, SplineFailureModel):
        spline = model.failure
        # A spline has no single coefficient per cause: the card shows each curve's average slope
        # (log-odds per unit) between the 25th and 75th percentile of its grid, and explains the form.
        low = np.array([float(np.percentile(grid, 25)) for grid in spline.grids])
        high = np.array([float(np.percentile(grid, 75)) for grid in spline.grids])
        slopes = spline.local_coefficients(high, low)
        causes = " + ".join(f"s({name}(t{'' if lag == 0 else f'-{lag}'}))" for name, lag in spline.terms)
        products = " + products of each pair of causes" if spline.interactions else ""
        cards.append(
            EquationCard(
                variable=spline.target,
                kind="logistic",
                terms=[
                    EquationTerm(variable=name, lag=lag, coefficient=float(slope))
                    for (name, lag), slope in zip(spline.terms, slopes, strict=True)
                ],
                intercept=spline.intercept,
                equation=f"logit P({spline.target}(t)) = {causes}{products} + {spline.intercept:.3g}",
                auc_holdout=spline.auc_holdout,
                n_train=spline.n_train,
                n_holdout=spline.n_holdout,
                notes=[
                    f"Observed failure rate {spline.event_rate:.2%} per step on running rows.",
                    f"Spline failure model: s(x) is a cubic B-spline with {spline.n_knots} knots, fitted on the data; "
                    "the coefficients shown are each curve's average slope over the middle half of its range.",
                ],
                curves=[
                    FailureCurve(
                        variable=name,
                        values=values.tolist(),
                        probability=(1.0 / (1.0 + np.exp(-np.clip(logit, -50, 50)))).tolist(),
                    )
                    for position, (name, _) in enumerate(spline.terms)
                    for values, logit in [spline.curve(position)]
                ],
            )
        )
    elif model.failure is not None:
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


def refit_linear_equations(
    window: PreparedData,
    graph: CausalGraph,
    previous: CausalModel,
    penalty: float,
    holdout_fraction: float,
    residual_limit: int,
    seed: int,
    targets: set[str] | None = None,
) -> CausalModel:
    """Refit the linear equations of `targets` (default: all) on a recent window and a new graph.

    Equations that did not change keep their long-window estimates. A cause that was held
    constant in the window (for example a lever under a do-intervention) carries no
    information about its effect, so its previous coefficient is kept as a fixed offset.
    The failure equation and the normal baseline are kept from the previous model: the
    failure mechanism is fitted on a long history that a short post-change window
    cannot replace.
    """
    with timed_step("refit_model", f"Refitted linear equations on {window.n_rows} recent rows") as timer:
        rng = np.random.default_rng(seed)
        equations: dict[str, LinearEquation] = dict(previous.equations)
        for target in window.endogenous():
            if targets is not None and target not in targets:
                continue
            equation = _fit_with_constant_causes(
                window,
                target,
                _equation_terms(graph, target, window.time_structure),
                previous.equations.get(target),
                penalty,
                holdout_fraction,
                residual_limit,
                rng,
            )
            if equation is not None:
                equations[target] = equation
        timer.details.update({"refitted": sorted(targets) if targets is not None else "all"})
    return CausalModel(
        variables=previous.variables,
        exogenous=previous.exogenous,
        units=previous.units,
        equations=equations,
        failure=previous.failure,
        baseline=previous.baseline,
        time_structure=previous.time_structure,
        exogenous_steps=previous.exogenous_steps,
        exogenous_ranges=previous.exogenous_ranges,
        notes=[*previous.notes, "Linear equations refitted after a detected structural change."],
    )


CONSTANT_STD = 1e-9  # a column with less spread than this in the window is treated as held constant


def _fit_with_constant_causes(
    window: PreparedData,
    target: str,
    terms: list[tuple[str, int]],
    previous: LinearEquation | None,
    penalty: float,
    holdout_fraction: float,
    residual_limit: int,
    rng: np.random.Generator,
) -> LinearEquation | None:
    """Like `_fit_linear`, but causes held constant keep their previous coefficient as an offset."""
    design, values, _ = lagged_design(window, target, terms, first_row=0)
    if values.size < 20:
        return None
    constant = [index for index, _ in enumerate(terms) if design[:, index].std() < CONSTANT_STD]
    if not constant:
        return _fit_linear(window, target, terms, penalty, holdout_fraction, residual_limit, rng)
    previous_coefficients = {} if previous is None else dict(zip(previous.terms, previous.coefficients, strict=True))
    fixed = {index: float(previous_coefficients.get(terms[index], 0.0)) for index in constant}
    free = [index for index in range(len(terms)) if index not in fixed]
    adjusted = values - sum(coefficient * design[:, index] for index, coefficient in fixed.items())
    if free:
        model = Ridge(alpha=penalty).fit(design[:, free], adjusted)
        free_coefficients, intercept = np.asarray(model.coef_, dtype=float), float(model.intercept_)
    else:
        free_coefficients, intercept = np.zeros(0), float(adjusted.mean())
    coefficients = np.zeros(len(terms))
    coefficients[free] = free_coefficients
    for index, coefficient in fixed.items():
        coefficients[index] = coefficient
    residuals = values - (design @ coefficients + intercept)
    # Covariance of the free coefficients and the intercept; the fixed offsets are treated as known.
    covariance = None
    free_covariance = ridge_covariance(design[:, free], residuals, penalty) if free else None
    if free_covariance is not None:
        positions = [0] + [1 + index for index in free]  # intercept, then the free terms
        covariance = np.zeros((len(terms) + 1, len(terms) + 1))
        covariance[np.ix_(positions, positions)] = free_covariance
    if residuals.size > residual_limit:
        residuals = rng.choice(residuals, size=residual_limit, replace=False)
    return LinearEquation(
        target=target,
        terms=terms,
        coefficients=coefficients,
        intercept=intercept,
        residuals=residuals,
        residual_std=float(residuals.std()),
        r_squared_holdout=previous.r_squared_holdout if previous else None,
        n_train=values.size,
        n_holdout=0,
        covariance=covariance,
    )
