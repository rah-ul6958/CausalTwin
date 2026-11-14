"""Bring Your Own Data: load a user CSV, validate it, and apply column roles."""

from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from causaltwin.config import COLUMN_ROLES, TIME_COLUMN_CANDIDATES
from causaltwin.utils.timing import timed_step


class ValidationReport(BaseModel):
    """What happened while loading a CSV, shown to the user before analysis."""

    n_rows: int
    n_columns: int
    time_column: str | None
    sampling_interval_seconds: float | None
    was_sorted_by_time: bool  # True if rows had to be re-ordered
    column_roles: dict[str, str]
    unassigned_columns: list[str]  # present in the file but not given a role (ignored)
    missing_counts: dict[str, int]  # missing values per used column BEFORE filling
    filled_counts: dict[str, int]  # values filled by forward fill
    dropped_leading_rows: int  # rows dropped because nothing earlier existed to fill from
    non_numeric_values: dict[str, int]  # values that could not be read as numbers
    warnings: list[str] = Field(default_factory=list)


def _find_time_column(dataframe: pd.DataFrame, time_column: str | None) -> str | None:
    """Use the given time column, or auto-detect a common name."""
    if time_column is not None:
        if time_column not in dataframe.columns:
            raise ValueError(f"Time column '{time_column}' not found in CSV")
        return time_column
    for column in dataframe.columns:
        if column.strip().lower() in TIME_COLUMN_CANDIDATES:
            return column
    return None


def _detect_sampling_interval(time_values: pd.Series) -> float | None:
    """Median gap between consecutive samples, in seconds (or in index units for numbers)."""
    if len(time_values) < 2:
        return None
    if pd.api.types.is_datetime64_any_dtype(time_values):
        gaps = time_values.diff().dropna().dt.total_seconds()
    else:
        gaps = pd.to_numeric(time_values, errors="coerce").diff().dropna()
    return float(gaps.median()) if len(gaps) > 0 else None


def load_csv(
    path: str | Path, column_roles: dict[str, str], time_column: str | None = None
) -> tuple[pd.DataFrame, ValidationReport]:
    """Load a CSV, apply column roles, clean it, and return (data, validation report).

    Steps: check roles, parse the time column and sort by it, convert used columns
    to numbers, forward fill missing values (and report how many), and drop leading
    rows that still have gaps. Columns with role "ignore" or without a role are dropped.
    """
    with timed_step("load_csv", f"Loaded CSV {Path(path).name}") as timer:
        raw_data = pd.read_csv(path)
        warnings: list[str] = []

        invalid_roles = {column: role for column, role in column_roles.items() if role not in COLUMN_ROLES}
        if invalid_roles:
            raise ValueError(f"Invalid roles {invalid_roles}. Allowed roles: {', '.join(COLUMN_ROLES)}")
        missing_columns = [column for column in column_roles if column not in raw_data.columns]
        if missing_columns:
            raise ValueError(f"Columns not found in CSV: {missing_columns}")

        detected_time_column = _find_time_column(raw_data, time_column)
        used_columns = [column for column, role in column_roles.items() if role != "ignore" and column != detected_time_column]
        if not used_columns:
            raise ValueError("No usable columns: every column is ignored")
        unassigned_columns = [
            column for column in raw_data.columns if column not in column_roles and column != detected_time_column
        ]

        was_sorted = False
        if detected_time_column is not None:
            parsed_time = pd.to_datetime(raw_data[detected_time_column], errors="coerce")
            if parsed_time.notna().mean() > 0.9:
                raw_data[detected_time_column] = parsed_time
            else:
                warnings.append(f"Time column '{detected_time_column}' is not a date; treated as a number")
            if not raw_data[detected_time_column].is_monotonic_increasing:
                raw_data = raw_data.sort_values(detected_time_column, kind="stable")
                was_sorted = True
        else:
            warnings.append("No time column found; row order is used as time")
        raw_data = raw_data.reset_index(drop=True)

        cleaned = pd.DataFrame(index=raw_data.index)
        if detected_time_column is not None:
            cleaned[detected_time_column] = raw_data[detected_time_column]
        non_numeric_values: dict[str, int] = {}
        for column in used_columns:
            numeric_values = pd.to_numeric(raw_data[column], errors="coerce")
            non_numeric_values[column] = int((numeric_values.isna() & raw_data[column].notna()).sum())
            cleaned[column] = numeric_values
        for column, count in non_numeric_values.items():
            if count > 0:
                warnings.append(f"{count} non-numeric values in '{column}' treated as missing")

        missing_counts = {column: int(cleaned[column].isna().sum()) for column in used_columns}
        filled = cleaned[used_columns].ffill()
        filled_counts = {
            column: int(missing_counts[column] - filled[column].isna().sum()) for column in used_columns
        }
        cleaned[used_columns] = filled
        complete_rows = cleaned[used_columns].notna().all(axis=1)
        first_complete = int(complete_rows.to_numpy().argmax()) if complete_rows.any() else len(cleaned)
        cleaned = cleaned.iloc[first_complete:].reset_index(drop=True)
        if first_complete > 0:
            warnings.append(f"Dropped {first_complete} leading rows with values that could not be filled")

        for column in used_columns:
            if column_roles[column] == "outcome" and not cleaned[column].dropna().isin([0, 1]).all():
                warnings.append(f"Outcome column '{column}' is not 0/1")

        report = ValidationReport(
            n_rows=len(cleaned),
            n_columns=len(used_columns),
            time_column=detected_time_column,
            sampling_interval_seconds=(
                _detect_sampling_interval(cleaned[detected_time_column]) if detected_time_column else None
            ),
            was_sorted_by_time=was_sorted,
            column_roles={column: column_roles[column] for column in used_columns},
            unassigned_columns=unassigned_columns,
            missing_counts=missing_counts,
            filled_counts=filled_counts,
            dropped_leading_rows=first_complete,
            non_numeric_values=non_numeric_values,
            warnings=warnings,
        )
        timer.details.update({"rows": report.n_rows, "columns": report.n_columns, "filled": sum(filled_counts.values())})
    return cleaned, report
