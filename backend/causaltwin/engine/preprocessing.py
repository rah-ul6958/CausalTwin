"""Turn an aligned dataframe into arrays the engine can use, with a regime-break mask.

Rows that are not normal causal dynamics are masked (ignored as samples and as lags):
- the machine is stopped (`machine_running == 0`)
- a regime-break event happened (tool change, maintenance), for example a tool reset
- the time series jumps (for example two separately recorded runs joined together)
- any used value is missing
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from causaltwin.config import (
    EVENT_INDICATOR_PREFIX,
    MACHINE_RUNNING_COLUMN,
    REGIME_BREAK_EVENTS,
    VARIABLE_INFO,
)

CONTINUOUS_ROLES = ("control", "environment", "sensor")
EXOGENOUS_ROLES = ("control", "environment")


@dataclass
class PreparedData:
    """Arrays and metadata for one dataset."""

    variables: list[str]  # continuous variables, in column order of `values`
    roles: dict[str, str]
    units: dict[str, str | None]
    exogenous: set[str]
    outcome: str | None
    values: np.ndarray  # shape (n_rows, n_variables)
    row_mask: np.ndarray  # True = row is not usable (regime break or missing value)
    outcome_values: np.ndarray | None  # 0/1 per row
    time_structure: bool

    @property
    def n_rows(self) -> int:
        """Number of rows."""
        return int(self.values.shape[0])

    def index_of(self, variable: str) -> int:
        """Column index of a variable."""
        return self.variables.index(variable)

    def endogenous(self) -> list[str]:
        """Continuous variables that can have causes inside the system."""
        return [variable for variable in self.variables if variable not in self.exogenous]

    def tail(self, n_rows: int) -> "PreparedData":
        """The most recent `n_rows` rows as a new PreparedData."""
        start = max(0, self.n_rows - n_rows)
        return PreparedData(
            variables=self.variables,
            roles=self.roles,
            units=self.units,
            exogenous=self.exogenous,
            outcome=self.outcome,
            values=self.values[start:],
            row_mask=self.row_mask[start:],
            outcome_values=None if self.outcome_values is None else self.outcome_values[start:],
            time_structure=self.time_structure,
        )


def default_roles(dataframe: pd.DataFrame) -> dict[str, str]:
    """Roles for the simulator's known variables that are present in the dataframe."""
    return {name: str(info["type"]) for name, info in VARIABLE_INFO.items() if name in dataframe.columns}


def detect_time_structure(dataframe: pd.DataFrame) -> bool:
    """A dataset is a time series if it has a step or timestamp column."""
    return "step" in dataframe.columns or "timestamp" in dataframe.columns


def regime_break_rows(dataframe: pd.DataFrame) -> np.ndarray:
    """Rows that are not normal dynamics: stopped machine, reset events, series jumps."""
    breaks = np.zeros(len(dataframe), dtype=bool)
    if MACHINE_RUNNING_COLUMN in dataframe.columns:
        breaks |= dataframe[MACHINE_RUNNING_COLUMN].to_numpy() == 0
    for event_type in REGIME_BREAK_EVENTS:
        column = f"{EVENT_INDICATOR_PREFIX}{event_type}"
        if column in dataframe.columns:
            breaks |= dataframe[column].to_numpy() == 1
    if "step" in dataframe.columns and len(dataframe) > 1:
        # Masking the first row after a jump excludes every lag window that crosses it.
        jumps = np.diff(dataframe["step"].to_numpy()) != 1
        breaks[1:] |= jumps
    return breaks


def prepare_data(
    dataframe: pd.DataFrame,
    roles: dict[str, str] | None = None,
    time_structure: bool | None = None,
) -> PreparedData:
    """Select variables by role, build the row mask, and return arrays."""
    roles = dict(roles) if roles is not None else default_roles(dataframe)
    missing = [name for name in roles if name not in dataframe.columns]
    if missing:
        raise ValueError(f"Columns not found: {missing}")
    variables = [name for name, role in roles.items() if role in CONTINUOUS_ROLES]
    if len(variables) < 2:
        raise ValueError("At least two continuous variables are needed for causal discovery")
    outcomes = [name for name, role in roles.items() if role == "outcome"]
    outcome = outcomes[0] if outcomes else None

    values = dataframe[variables].to_numpy(dtype=float)
    row_mask = regime_break_rows(dataframe) | np.isnan(values).any(axis=1)
    values = np.where(np.isnan(values), 0.0, values)  # masked anyway; keeps the arithmetic finite
    outcome_values = None
    if outcome is not None:
        outcome_values = dataframe[outcome].fillna(0).to_numpy(dtype=float)

    units: dict[str, str | None] = {}
    for name in [*variables, *outcomes]:
        info = VARIABLE_INFO.get(name)
        units[name] = str(info["unit"]) if info is not None else None
    return PreparedData(
        variables=variables,
        roles={name: roles[name] for name in [*variables, *outcomes]},
        units=units,
        exogenous={name for name in variables if roles[name] in EXOGENOUS_ROLES},
        outcome=outcome,
        values=values,
        row_mask=row_mask,
        outcome_values=outcome_values,
        time_structure=detect_time_structure(dataframe) if time_structure is None else time_structure,
    )


def lagged_design(
    prepared: PreparedData,
    target: str,
    predictors: list[tuple[str, int]],
    first_row: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build (X, y, rows) for regressing target(t) on predictor(t - lag).

    Only rows t >= first_row are used, and a row is dropped if the target row or any
    lagged predictor row is masked.
    """
    max_lag = max([lag for _, lag in predictors], default=0)
    first_row = max(first_row, max_lag)
    rows = np.arange(first_row, prepared.n_rows)
    usable = ~prepared.row_mask[rows]
    for _, lag in predictors:
        usable &= ~prepared.row_mask[rows - lag]
    rows = rows[usable]
    target_values = prepared.values[rows, prepared.index_of(target)]
    design = (
        np.column_stack([prepared.values[rows - lag, prepared.index_of(name)] for name, lag in predictors])
        if predictors
        else np.empty((len(rows), 0))
    )
    return design, target_values, rows
