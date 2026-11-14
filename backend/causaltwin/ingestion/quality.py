"""Per-column data quality report (used by the UI for uploaded or simulated data)."""

import numpy as np
import pandas as pd
from pydantic import BaseModel

from causaltwin.config import FLATLINE_MIN_RUN


class ColumnQuality(BaseModel):
    """Quality summary for one numeric column."""

    column: str
    minimum: float | None
    maximum: float | None
    mean: float | None
    std: float | None
    missing_count: int
    longest_constant_run: int
    is_binary: bool
    is_flatline: bool


def longest_constant_run(values: pd.Series) -> int:
    """Length of the longest run of identical consecutive non-missing values."""
    clean_values = values.dropna().to_numpy()
    if len(clean_values) == 0:
        return 0
    change_points = np.flatnonzero(np.diff(clean_values) != 0)
    run_boundaries = np.concatenate(([-1], change_points, [len(clean_values) - 1]))
    return int(np.max(np.diff(run_boundaries)))


def data_quality_report(dataframe: pd.DataFrame, columns: list[str] | None = None) -> list[ColumnQuality]:
    """Return min, max, mean, std, missing count and flatline detection per numeric column.

    A column is a flatline when it never changes, or when it stays exactly constant
    for at least FLATLINE_MIN_RUN consecutive samples (a typical stuck-sensor sign).
    Binary columns (two or fewer distinct values, e.g. event indicators) are not
    checked for runs, because long constant runs are normal for them.
    """
    selected_columns = columns or list(dataframe.select_dtypes(include="number").columns)
    report: list[ColumnQuality] = []
    for column in selected_columns:
        values = pd.to_numeric(dataframe[column], errors="coerce")
        has_values = values.notna().any()
        is_binary = values.nunique(dropna=True) <= 2
        run_length = longest_constant_run(values)
        never_changes = has_values and values.nunique(dropna=True) == 1
        is_flatline = bool(never_changes or (not is_binary and run_length >= FLATLINE_MIN_RUN))
        report.append(
            ColumnQuality(
                column=column,
                minimum=float(values.min()) if has_values else None,
                maximum=float(values.max()) if has_values else None,
                mean=float(values.mean()) if has_values else None,
                std=float(values.std()) if values.notna().sum() > 1 else None,
                missing_count=int(values.isna().sum()),
                longest_constant_run=run_length,
                is_binary=bool(is_binary),
                is_flatline=is_flatline,
            )
        )
    return report
