"""Causal discovery: lagged graph with PCMCI (tigramite), failure causes with logistic regression.

Method (time-series mode)
-------------------------
PCMCI has two stages: a condition-selection stage (PC1) that prunes candidate parents,
and the MCI test of every link given the selected conditions. On this machine the PC1
stage removed true parents: motor_current is almost a deterministic function of load
and speed, so in some intermediate condition sets it looked redundant and was dropped,
which then produced a false load_pct -> bearing_temp edge (measured in the Phase 2
report). We therefore run tigramite's MCI stage with the FULL candidate set (every
variable at lags 1..tau_max) as conditions for each target, which equals PCMCI with
pc_alpha = 1 and no parents-of-cause conditions (max_conds_px = 0). With a few
thousand samples and about 30 candidates this test is well powered.

Because every link of one target is then tested against the same condition set, all
its partial correlations follow from a single least-squares fit.
`full_condition_mci` computes exactly that with NumPy; a test checks it matches
tigramite to numerical precision. The bootstrap uses it for its 20 resamples.

Edge decision: with all lags in the conditions, a slowly changing cause (for example
ambient temperature) has almost identical neighbouring lags, so its effect is split
between them and no single lag reaches significance. Whether an edge exists is
therefore decided by a joint test of all lags of that cause (an F test, exact for
the same least-squares model); the reported lag is the lag with the strongest
individual MCI result. Self-lags use the individual MCI tests.

Background knowledge: control levers and ambient conditions have no causes inside the
system (exogenous), and only lagged links are allowed (tau_min = 1).

Failure (binary, rare) is handled separately with a multivariate logistic regression.
"""

import warnings
from dataclasses import dataclass, field

import numpy as np
from joblib import Parallel, delayed
from scipy import stats
from statsmodels.discrete.discrete_model import Logit
from tigramite import data_processing
from tigramite.independence_tests.parcorr import ParCorr
from tigramite.pcmci import PCMCI

from causaltwin.config import FAILURE_CANDIDATE_ROLES, FAILURE_LAG_TIE_TOLERANCE
from causaltwin.engine.preprocessing import PreparedData
from causaltwin.engine.schemas import CausalEdge, CausalGraph, CausalNode
from causaltwin.utils.timing import timed_step


class FastParCorr(ParCorr):
    """tigramite ParCorr with the final Pearson correlation computed directly in NumPy.

    Mathematically identical to ParCorr; it only avoids SciPy's `pearsonr`, which builds
    a distribution object on every call and took about 40% of the discovery time.
    """

    def get_dependence_measure(self, array: np.ndarray, xyz: np.ndarray, data_type: object = None) -> float:
        """Partial correlation: Pearson correlation of the X and Y regression residuals."""
        x_residuals = self._get_single_residuals(array, target_var=0)
        y_residuals = self._get_single_residuals(array, target_var=1)
        x_residuals = x_residuals - x_residuals.mean()
        y_residuals = y_residuals - y_residuals.mean()
        denominator = np.sqrt(np.dot(x_residuals, x_residuals) * np.dot(y_residuals, y_residuals))
        return float(np.dot(x_residuals, y_residuals) / denominator) if denominator > 0 else 0.0


@dataclass
class LaggedDiscovery:
    """Raw PCMCI output: partial correlations and p-values, shape (n_vars, n_vars, tau_max + 1)."""

    variables: list[str]
    val_matrix: np.ndarray
    p_matrix: np.ndarray
    joint_p_matrix: np.ndarray  # (n_vars, n_vars): joint test over all lags of the cause
    n_samples: int
    method: str


@dataclass
class FailureCandidate:
    """Result of the failure screen for one variable."""

    variable: str
    lag: int  # lag with the largest |z|
    coefficient: float  # total effect over all lags (sum of lag coefficients) per standard deviation
    z_value: float
    p_value: float  # joint test over all lags of this variable
    significant: bool


@dataclass
class FailureScreen:
    """Multivariate logistic screen of every candidate variable for the failure outcome."""

    fitted: bool
    n_rows: int
    n_events: int
    candidates: list[FailureCandidate] = field(default_factory=list)
    message: str = ""

    def significant_variables(self) -> set[str]:
        """Names of variables whose lag coefficients are jointly significant."""
        return {candidate.variable for candidate in self.candidates if candidate.significant}


def _target_link_assumptions(n_variables: int, target_index: int, tau_max: int) -> dict[int, dict]:
    """Link assumptions where only one target has (lagged) candidate parents."""
    assumptions: dict[int, dict] = {index: {} for index in range(n_variables)}
    assumptions[target_index] = {
        (cause_index, -lag): "-?>" for cause_index in range(n_variables) for lag in range(1, tau_max + 1)
    }
    return assumptions


def _tigramite_target_mci(
    values: np.ndarray, row_mask: np.ndarray, variables: list[str], target_index: int, tau_max: int
) -> tuple[int, np.ndarray, np.ndarray]:
    """Run tigramite's MCI stage for one target with all candidates as conditions."""
    warnings.filterwarnings("ignore")
    assumptions = _target_link_assumptions(len(variables), target_index, tau_max)
    parents = {index: list(links) for index, links in assumptions.items()}
    mask = np.repeat(row_mask[:, None], len(variables), axis=1)
    frame = data_processing.DataFrame(values, var_names=variables, mask=mask)
    pcmci = PCMCI(dataframe=frame, cond_ind_test=FastParCorr(mask_type="xyz"), verbosity=0)
    results = pcmci.run_mci(
        tau_min=1, tau_max=tau_max, parents=parents, link_assumptions=assumptions, max_conds_px=0, alpha_level=1.0
    )
    return target_index, results["val_matrix"][:, target_index, :], results["p_matrix"][:, target_index, :]


def warm_up_workers(n_jobs: int) -> None:
    """Start the worker processes once, so later discoveries do not pay the start-up cost.

    On Windows, starting worker processes and importing tigramite in them takes a few
    seconds; joblib reuses the same workers afterwards.
    """
    if n_jobs > 1:
        Parallel(n_jobs=n_jobs)(delayed(_import_tigramite)() for _ in range(n_jobs))


def _import_tigramite() -> bool:
    """Import the heavy modules inside a worker process."""
    import tigramite.pcmci  # noqa: F401

    return True


def tigramite_mci(prepared: PreparedData, tau_max: int, n_jobs: int) -> LaggedDiscovery:
    """Lagged discovery with tigramite, one parallel job per endogenous target."""
    n_variables = len(prepared.variables)
    targets = [prepared.index_of(name) for name in prepared.endogenous()]
    arguments = [(prepared.values, prepared.row_mask, prepared.variables, target, tau_max) for target in targets]
    if n_jobs > 1:
        outputs = Parallel(n_jobs=min(n_jobs, len(targets)))(
            delayed(_tigramite_target_mci)(*job_arguments) for job_arguments in arguments
        )
    else:
        outputs = [_tigramite_target_mci(*job_arguments) for job_arguments in arguments]
    val_matrix = np.zeros((n_variables, n_variables, tau_max + 1))
    p_matrix = np.full((n_variables, n_variables, tau_max + 1), np.nan)
    for target_index, target_values, target_p_values in outputs:
        val_matrix[:, target_index, :] = target_values
        p_matrix[:, target_index, :] = target_p_values
    _, _, joint_p_matrix, n_samples = full_condition_mci(prepared.values, prepared.row_mask, targets, tau_max)
    return LaggedDiscovery(prepared.variables, val_matrix, p_matrix, joint_p_matrix, n_samples, "pcmci")


def _sample_rows(row_mask: np.ndarray, tau_max: int) -> np.ndarray:
    """Rows usable as samples: like tigramite, start at 2 * tau_max; no masked row in the lag window."""
    rows = np.arange(2 * tau_max, row_mask.size)
    usable = ~row_mask[rows]
    for lag in range(1, tau_max + 1):
        usable &= ~row_mask[rows - lag]
    return rows[usable]


def full_condition_mci(
    values: np.ndarray, row_mask: np.ndarray, endogenous_indices: list[int], tau_max: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Exact NumPy equivalent of `tigramite_mci` (same samples, same test), plus joint tests.

    For each target, one OLS of target(t) on every variable at lags 1..tau_max gives
    each coefficient's t statistic; the partial correlation is t / sqrt(t^2 + df) and
    the p-value is the two-sided Student-t p-value with df = n - 2 - (number of
    conditions). The joint test of all lags of one cause is the F test of that
    coefficient block, computed from the same fit (Wald form, exact for OLS).
    """
    n_variables = values.shape[1]
    rows = _sample_rows(row_mask, tau_max)
    candidates = [(cause, lag) for cause in range(n_variables) for lag in range(1, tau_max + 1)]
    design = np.column_stack([values[rows - lag, cause] for cause, lag in candidates])
    design = np.column_stack([np.ones(rows.size), design])
    gram_inverse = np.linalg.pinv(design.T @ design)
    degrees_of_freedom = rows.size - 2 - (len(candidates) - 1)
    residual_degrees_of_freedom = rows.size - design.shape[1]
    val_matrix = np.zeros((n_variables, n_variables, tau_max + 1))
    p_matrix = np.full((n_variables, n_variables, tau_max + 1), np.nan)  # NaN = not tested
    joint_p_matrix = np.full((n_variables, n_variables), np.nan)
    for target in endogenous_indices:
        target_values = values[rows, target]
        coefficients = gram_inverse @ design.T @ target_values
        residuals = target_values - design @ coefficients
        residual_variance = residuals @ residuals / degrees_of_freedom
        standard_errors = np.sqrt(np.maximum(np.diag(gram_inverse)[1:] * residual_variance, 1e-300))
        t_values = coefficients[1:] / standard_errors
        partial_correlations = t_values / np.sqrt(t_values**2 + degrees_of_freedom)
        p_values = 2.0 * stats.t.sf(np.abs(t_values), degrees_of_freedom)
        for position, (cause, lag) in enumerate(candidates):
            val_matrix[cause, target, lag] = partial_correlations[position]
            p_matrix[cause, target, lag] = p_values[position]
        mean_squared_error = residuals @ residuals / residual_degrees_of_freedom
        for cause in range(n_variables):
            block = [1 + position for position, (variable, _) in enumerate(candidates) if variable == cause]
            block_coefficients = coefficients[block]
            block_inverse = np.linalg.pinv(gram_inverse[np.ix_(block, block)])
            f_statistic = float(block_coefficients @ block_inverse @ block_coefficients) / (
                len(block) * mean_squared_error
            )
            joint_p_matrix[cause, target] = float(stats.f.sf(f_statistic, len(block), residual_degrees_of_freedom))
    return val_matrix, p_matrix, joint_p_matrix, int(rows.size)


def fast_mci(prepared: PreparedData, tau_max: int) -> LaggedDiscovery:
    """Lagged discovery with the NumPy equivalent of the tigramite test."""
    endogenous = [prepared.index_of(name) for name in prepared.endogenous()]
    val_matrix, p_matrix, joint_p_matrix, n_samples = full_condition_mci(
        prepared.values, prepared.row_mask, endogenous, tau_max
    )
    return LaggedDiscovery(prepared.variables, val_matrix, p_matrix, joint_p_matrix, n_samples, "pcmci")


def significant_lagged_edges(discovery: LaggedDiscovery, alpha: float) -> list[CausalEdge]:
    """One edge per (cause, effect) pair.

    Cross edges: kept when the joint test over all lags of the cause is significant;
    `lag` is the lag with the smallest individual MCI p-value and `lags` lists the
    individually significant lags (at least the strongest one). Self-lags: kept when
    an individual lag is significant. `p_value` is the joint p-value for cross edges.
    """
    edges = []
    variables = discovery.variables
    n_variables = len(variables)
    tau_max = discovery.p_matrix.shape[2] - 1
    for cause in range(n_variables):
        for effect in range(n_variables):
            lag_p_values = discovery.p_matrix[cause, effect, 1:]
            if np.isnan(lag_p_values).all():
                continue  # not tested: the effect is exogenous
            individually_significant = [lag for lag in range(1, tau_max + 1) if lag_p_values[lag - 1] < alpha]
            strongest = int(np.argmin(lag_p_values)) + 1
            is_self_loop = cause == effect
            if is_self_loop:
                if not individually_significant:
                    continue
                edge_p_value = float(lag_p_values[strongest - 1])
            else:
                edge_p_value = float(discovery.joint_p_matrix[cause, effect])
                if edge_p_value >= alpha:
                    continue
            edges.append(
                CausalEdge(
                    cause=variables[cause],
                    effect=variables[effect],
                    lag=strongest,
                    lags=individually_significant or [strongest],
                    strength=float(discovery.val_matrix[cause, effect, strongest]),
                    p_value=edge_p_value,
                    method=discovery.method,
                    is_self_loop=is_self_loop,
                )
            )
    return edges


def failure_screen(
    prepared: PreparedData,
    lags: list[int],
    alpha: float,
    min_events: int,
    candidate_roles: tuple[str, ...] = FAILURE_CANDIDATE_ROLES,
    solver: str = "statsmodels",
) -> FailureScreen:
    """Multivariate logistic regression of failure(t) on every candidate variable at every lag.

    Each variable's lags are tested jointly (Wald chi-square), because neighbouring lags
    of a slowly changing variable are almost collinear and split one effect between
    them. Rows where the machine is stopped, or with a masked lag, are excluded.
    `solver` is "statsmodels" (reference) or "numpy" (the same maximum-likelihood fit,
    used inside the bootstrap; verified equal in the tests).
    """
    if prepared.outcome is None or prepared.outcome_values is None:
        return FailureScreen(False, 0, 0, message="No outcome column.")
    candidate_names = [name for name in prepared.variables if prepared.roles[name] in candidate_roles]
    if not candidate_names:
        return FailureScreen(False, 0, 0, message=f"No variables with roles {candidate_roles} to screen.")
    first_row = max(lags)
    rows = np.arange(first_row, prepared.n_rows)
    usable = ~prepared.row_mask[rows]
    for lag in set(lags):
        usable &= ~prepared.row_mask[rows - lag]
    rows = rows[usable]
    outcome = prepared.outcome_values[rows]
    n_events = int(outcome.sum())
    if n_events < min_events:
        return FailureScreen(
            False, rows.size, n_events, message=f"Only {n_events} failure events (need {min_events}); not fitted."
        )
    sorted_lags = sorted(lags)
    blocks = []
    for name in candidate_names:
        lagged = np.column_stack([prepared.values[rows - lag, prepared.index_of(name)] for lag in sorted_lags])
        blocks.append(_level_and_differences(lagged))
    design = np.column_stack(blocks)
    center = design.mean(axis=0)
    spread = design.std(axis=0)
    spread[spread == 0] = 1.0
    standardized = np.column_stack([np.ones(rows.size), (design - center) / spread])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            if solver == "numpy":
                parameters, parameter_covariance = logistic_newton(standardized, outcome)
            else:
                model = Logit(outcome, standardized).fit(disp=0, maxiter=100)
                parameters, parameter_covariance = np.asarray(model.params), np.asarray(model.cov_params())
        except Exception as error:  # singular or separated data: report, do not guess
            return FailureScreen(False, rows.size, n_events, message=f"Logistic fit failed: {error}")
    # Back to coefficients per raw unit of each reparametrized column.
    gamma = parameters[1:] / spread
    gamma_covariance = parameter_covariance[1:, 1:] / np.outer(spread, spread)
    back_transform = _lag_back_transform(len(sorted_lags))
    candidates = []
    for position, name in enumerate(candidate_names):
        block = slice(position * len(sorted_lags), (position + 1) * len(sorted_lags))
        block_gamma = gamma[block]
        block_covariance = gamma_covariance[block, block]
        wald = float(block_gamma @ np.linalg.pinv(block_covariance) @ block_gamma)
        p_value = float(stats.chi2.sf(wald, df=len(sorted_lags)))
        lag_coefficients = back_transform @ block_gamma
        lag_covariance = back_transform @ block_covariance @ back_transform.T
        lag_z = lag_coefficients / np.sqrt(np.maximum(np.diag(lag_covariance), 1e-300))
        strongest = int(np.argmax(np.abs(lag_z)))
        variable_spread = float(prepared.values[rows, prepared.index_of(name)].std()) or 1.0
        # Individual lag coefficients of a slowly changing variable are ill-determined (they
        # can be large with opposite signs); their sum, the effect of a sustained change, is stable.
        total_effect = float(lag_coefficients.sum())
        candidates.append(
            FailureCandidate(
                variable=name,
                lag=sorted_lags[strongest],
                coefficient=total_effect * variable_spread,
                z_value=float(lag_z[strongest]),
                p_value=p_value,
                significant=p_value < alpha,
            )
        )
    return FailureScreen(True, rows.size, n_events, candidates)


def logistic_newton(
    design: np.ndarray, outcome: np.ndarray, max_iterations: int = 50, tolerance: float = 1e-8
) -> tuple[np.ndarray, np.ndarray]:
    """Maximum-likelihood logistic regression by Newton's method (same estimate as statsmodels Logit).

    Returns the coefficients and their covariance (inverse of the observed information).
    """
    coefficients = np.zeros(design.shape[1])
    event_rate = float(np.clip(outcome.mean(), 1e-6, 1 - 1e-6))
    coefficients[0] = np.log(event_rate / (1.0 - event_rate))  # start the intercept at the base rate
    for _ in range(max_iterations):
        probability = 1.0 / (1.0 + np.exp(-np.clip(design @ coefficients, -50, 50)))
        weights = probability * (1.0 - probability)
        information = (design * weights[:, None]).T @ design
        step = np.linalg.solve(information, design.T @ (outcome - probability))
        coefficients += step
        if np.max(np.abs(step)) < tolerance:
            break
    probability = 1.0 / (1.0 + np.exp(-np.clip(design @ coefficients, -50, 50)))
    information = (design * (probability * (1.0 - probability))[:, None]).T @ design
    return coefficients, np.linalg.inv(information)


def _level_and_differences(lagged: np.ndarray) -> np.ndarray:
    """Reparametrize lags (x(t-1), x(t-2), ...) as (x(t-1), x(t-1)-x(t-2), x(t-2)-x(t-3), ...).

    Same model, same likelihood, same joint test per variable, but well conditioned:
    neighbouring lags of a slowly changing variable are almost identical columns.
    """
    columns = [lagged[:, 0]] + [lagged[:, index - 1] - lagged[:, index] for index in range(1, lagged.shape[1])]
    return np.column_stack(columns)


def _lag_back_transform(n_lags: int) -> np.ndarray:
    """Matrix A with lag coefficients = A @ (level and difference coefficients)."""
    transform = np.zeros((n_lags, n_lags))
    transform[0, 0] = 1.0
    for difference in range(1, n_lags):
        transform[difference - 1, difference] += 1.0  # +x(t-difference)
        transform[difference, difference] -= 1.0  # -x(t-difference-1)
    return transform


def _log_likelihood(prepared: PreparedData, rows: np.ndarray, terms: list[tuple[str, int]]) -> float:
    """Log-likelihood of a logistic model of the outcome on the given lagged terms."""
    assert prepared.outcome_values is not None
    columns = [prepared.values[rows - lag, prepared.index_of(name)] for name, lag in terms]
    design = np.column_stack([np.ones(rows.size), *columns])
    spread = design[:, 1:].std(axis=0)
    spread[spread == 0] = 1.0
    design[:, 1:] = (design[:, 1:] - design[:, 1:].mean(axis=0)) / spread
    outcome = prepared.outcome_values[rows]
    coefficients, _ = logistic_newton(design, outcome)
    logits = np.clip(design @ coefficients, -50, 50)
    return float(np.sum(outcome * logits - np.log1p(np.exp(logits))))


def refine_failure_lags(prepared: PreparedData, screen: FailureScreen, lags: list[int]) -> None:
    """Choose each significant cause's lag by likelihood in a model of only the significant causes.

    In the full screen, neighbouring lags of a slowly changing variable are nearly
    collinear, so the strongest individual z is a poor guide to the true lag. Here each
    cause's lag is chosen (one cause at a time, others held at their current lag) by the
    likelihood of the small model. If a shorter lag is within FAILURE_LAG_TIE_TOLERANCE
    log-likelihood units of the best one, the data cannot tell them apart (a
    likelihood-ratio test would not reject it), and the shortest such lag is used.
    """
    causes = [candidate for candidate in screen.candidates if candidate.significant]
    if not causes:
        return
    rows = np.arange(max(lags), prepared.n_rows)
    usable = ~prepared.row_mask[rows]
    for lag in lags:
        usable &= ~prepared.row_mask[rows - lag]
    rows = rows[usable]
    for candidate in causes:
        others = [(other.variable, other.lag) for other in causes if other is not candidate]
        likelihoods = {lag: _log_likelihood(prepared, rows, [*others, (candidate.variable, lag)]) for lag in lags}
        best = max(likelihoods.values())
        candidate.lag = min(lag for lag, value in likelihoods.items() if value >= best - FAILURE_LAG_TIE_TOLERANCE)


def failure_edges(screen: FailureScreen, outcome: str) -> list[CausalEdge]:
    """Edges into the outcome for significant, risk-increasing-or-decreasing candidates."""
    return [
        CausalEdge(
            cause=candidate.variable,
            effect=outcome,
            lag=candidate.lag,
            lags=[candidate.lag],
            strength=candidate.coefficient,
            p_value=candidate.p_value,
            method="logistic",
        )
        for candidate in screen.candidates
        if candidate.significant
    ]


def contemporaneous_discovery(prepared: PreparedData, alpha: float) -> list[CausalEdge]:
    """Tabular mode: PCMCIplus at tau_max = 0 with role-based background knowledge.

    Rows are treated as independent samples. Exogenous variables can only be causes.
    Edges whose direction cannot be decided are returned with `oriented=False`.
    """
    usable_rows = ~prepared.row_mask
    values = prepared.values[usable_rows]
    variables = prepared.variables
    exogenous = {prepared.index_of(name) for name in prepared.exogenous}
    assumptions: dict[int, dict] = {index: {} for index in range(len(variables))}
    for first in range(len(variables)):
        for second in range(len(variables)):
            if first == second or (first in exogenous and second in exogenous):
                continue
            if first in exogenous:  # first can cause second, never the reverse
                assumptions[second][(first, 0)] = "-?>"
                assumptions[first][(second, 0)] = "<?-"
            elif second not in exogenous:
                assumptions[second][(first, 0)] = "o?o"
    frame = data_processing.DataFrame(values, var_names=variables)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pcmci = PCMCI(dataframe=frame, cond_ind_test=FastParCorr(), verbosity=0)
        results = pcmci.run_pcmciplus(tau_min=0, tau_max=0, pc_alpha=alpha, link_assumptions=assumptions)
    graph = results["graph"]
    edges = []
    for first in range(len(variables)):
        for second in range(len(variables)):
            link = graph[first, second, 0]
            if link == "-->" or (link in ("o-o", "x-x") and first < second):
                edges.append(
                    CausalEdge(
                        cause=variables[first],
                        effect=variables[second],
                        lag=0,
                        lags=[0],
                        strength=float(results["val_matrix"][first, second, 0]),
                        p_value=float(results["p_matrix"][first, second, 0]),
                        method="pcmciplus",
                        oriented=link == "-->",
                    )
                )
    return edges


def build_nodes(prepared: PreparedData) -> list[CausalNode]:
    """Graph nodes for every continuous variable and the outcome."""
    names = [*prepared.variables, *([prepared.outcome] if prepared.outcome else [])]
    return [CausalNode(name=name, role=prepared.roles[name], unit=prepared.units.get(name)) for name in names]


def discover(
    window: PreparedData,
    failure_history: PreparedData,
    tau_max: int,
    alpha: float,
    min_failure_events: int,
    n_jobs: int,
    backend: str = "tigramite",
) -> tuple[CausalGraph, FailureScreen]:
    """Full discovery: lagged (or contemporaneous) graph plus failure causes.

    `backend` is "tigramite" (the reference implementation, used for the reported graph)
    or "numpy" (its exact equivalent, used inside the bootstrap).
    """
    with timed_step("discovery", "Discovered causal graph") as timer:
        notes = []
        with timed_step("pcmci", f"Lagged graph discovery on {window.n_rows} rows ({backend})") as pcmci_timer:
            if window.time_structure:
                if backend == "tigramite":
                    lagged = tigramite_mci(window, tau_max, n_jobs)
                else:
                    lagged = fast_mci(window, tau_max)
                edges = significant_lagged_edges(lagged, alpha)
                screen_lags = list(range(1, tau_max + 1))
                notes.append(
                    "PCMCI MCI test (tigramite ParCorr) with the full candidate set as conditions; "
                    "assumes no hidden confounders, linear effects, and lags up to tau_max."
                )
            else:
                edges = contemporaneous_discovery(window, alpha)
                screen_lags = [0]
                notes.append("Tabular mode: PCMCIplus at tau_max = 0; rows are treated as independent samples.")
            pcmci_timer.details.update({"edges": len(edges)})
        with timed_step("failure_screen", f"Failure-cause screen on {failure_history.n_rows} rows") as screen_timer:
            screen = failure_screen(failure_history, screen_lags, alpha, min_failure_events)
            if screen.fitted and len(screen_lags) > 1:
                refine_failure_lags(failure_history, screen, screen_lags)
            screen_timer.details.update({"events": screen.n_events, "causes": sorted(screen.significant_variables())})
        if window.outcome is not None:
            edges += failure_edges(screen, window.outcome)
            if not screen.fitted:
                notes.append(f"Failure model: {screen.message}")
        graph = CausalGraph(
            graph_type="causal",
            nodes=build_nodes(window),
            edges=edges,
            tau_max=tau_max if window.time_structure else 0,
            window_steps=window.n_rows,
            notes=notes,
        )
        timer.details.update({"edges": len(edges), "failure_events": screen.n_events, "rows": window.n_rows})
    return graph, screen
