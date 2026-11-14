"""Merge a factory-style event log into a sensor time series."""

import numpy as np
import pandas as pd

from causaltwin.config import EVENT_INDICATOR_PREFIX, EVENT_TYPES, STEPS_SINCE_MAINTENANCE_COLUMN


def _event_row_positions(sensor_dataframe: pd.DataFrame, event_dataframe: pd.DataFrame) -> np.ndarray:
    """For every event, find the sensor row it belongs to.

    An event belongs to the last sensor row whose timestamp is at or before the
    event time. Events before the first sensor row map to row 0.
    """
    sensor_times = pd.to_datetime(sensor_dataframe["timestamp"]).to_numpy()
    event_times = pd.to_datetime(event_dataframe["timestamp"]).to_numpy()
    positions = np.searchsorted(sensor_times, event_times, side="right") - 1
    return np.clip(positions, 0, len(sensor_dataframe) - 1)


def align_events(sensor_dataframe: pd.DataFrame, event_dataframe: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of the sensor data with event indicator columns added.

    Adds one column `event_<type>` (0/1) per known event type, plus any extra types
    found in the log, and a `steps_since_maintenance` column (steps since the most
    recent maintenance event, or since the start of the data if none happened yet).
    Both inputs need a `timestamp` column; the sensor data must be sorted by time.
    """
    aligned = sensor_dataframe.reset_index(drop=True).copy()
    n_rows = len(aligned)
    event_types = list(EVENT_TYPES)
    if len(event_dataframe) > 0:
        event_types += sorted(set(event_dataframe["event_type"]) - set(EVENT_TYPES))
        positions = _event_row_positions(aligned, event_dataframe)
    else:
        positions = np.array([], dtype=int)

    event_type_values = event_dataframe["event_type"].to_numpy() if len(event_dataframe) > 0 else np.array([])
    maintenance_rows = np.array([], dtype=int)
    for event_type in event_types:
        indicator = np.zeros(n_rows, dtype=int)
        rows_for_type = positions[event_type_values == event_type]
        indicator[rows_for_type] = 1
        aligned[f"{EVENT_INDICATOR_PREFIX}{event_type}"] = indicator
        if event_type == "maintenance":
            maintenance_rows = np.unique(rows_for_type)

    aligned[STEPS_SINCE_MAINTENANCE_COLUMN] = _steps_since(maintenance_rows, n_rows)
    return aligned


def _steps_since(event_rows: np.ndarray, n_rows: int) -> np.ndarray:
    """Count rows since the latest event row (0 on the event row itself)."""
    row_numbers = np.arange(n_rows)
    last_event_row = np.zeros(n_rows, dtype=int)  # before any event: count from the start
    if len(event_rows) > 0:
        marker = np.full(n_rows, -1)
        marker[event_rows] = event_rows
        running_last = np.maximum.accumulate(marker)
        last_event_row = np.where(running_last >= 0, running_last, 0)
    return row_numbers - last_event_row
