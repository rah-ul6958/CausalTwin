"""Tests for event alignment, CSV loading and the data quality report."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from causaltwin.config import EVENT_TYPES, FLATLINE_MIN_RUN, STEPS_SINCE_MAINTENANCE_COLUMN
from causaltwin.ingestion import align_events, data_quality_report, load_csv
from causaltwin.simulator import SimulationResult


def test_align_events_adds_correct_indicator_columns(scenario_results: dict[str, SimulationResult]) -> None:
    """Each event type becomes a 0/1 column marking exactly the rows where it happened."""
    result = scenario_results["overload"]
    aligned = align_events(result.sensors, result.events)
    assert len(aligned) == len(result.sensors)
    for event_type in EVENT_TYPES:
        column = f"event_{event_type}"
        assert column in aligned.columns
        expected_rows = set(result.events.loc[result.events["event_type"] == event_type, "step"])
        assert set(aligned.index[aligned[column] == 1]) == expected_rows, event_type
    # The failure indicator from the event log agrees with the failure column.
    assert (aligned["event_failure"] == aligned["failure"]).all()


def test_steps_since_maintenance_resets(scenario_results: dict[str, SimulationResult]) -> None:
    """steps_since_maintenance is 0 on maintenance rows and grows by 1 otherwise."""
    result = scenario_results["overload"]
    aligned = align_events(result.sensors, result.events)
    counter = aligned[STEPS_SINCE_MAINTENANCE_COLUMN].to_numpy()
    maintenance_rows = aligned["event_maintenance"].to_numpy() == 1
    assert maintenance_rows.sum() > 0
    assert (counter[maintenance_rows] == 0).all()
    assert counter[0] == 0
    increments = np.diff(counter)
    assert ((increments == 1) | maintenance_rows[1:]).all()


def test_align_events_with_empty_log() -> None:
    """An empty event log gives all-zero indicators and a plain step counter."""
    sensors = pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=5, freq="10s"), "value": range(5)})
    events = pd.DataFrame(columns=["timestamp", "event_type", "details"])
    aligned = align_events(sensors, events)
    assert aligned["event_failure"].sum() == 0
    assert aligned[STEPS_SINCE_MAINTENANCE_COLUMN].tolist() == [0, 1, 2, 3, 4]


def _write_messy_csv(path: Path) -> None:
    """Write a small CSV with shuffled rows, gaps, a bad value and extra columns."""
    timestamps = pd.date_range("2026-03-01 08:00", periods=10, freq="30s")
    data = pd.DataFrame(
        {
            "timestamp": timestamps.astype(str),
            "speed": [1000, 1010, None, 1030, 1040, 1050, 1060, 1070, 1080, 1090],
            "temp": [None, 40.1, 40.2, 40.3, "bad", 40.5, None, None, 40.8, 40.9],
            "broke": [0, 0, 0, 0, 0, 0, 0, 0, 1, 0],
            "operator_name": ["a"] * 10,
            "unused": range(10),
        }
    )
    data.sample(frac=1.0, random_state=0).to_csv(path, index=False)


def test_load_csv_cleans_and_reports(tmp_path: Path) -> None:
    """Rows are sorted, gaps are forward filled and counted, and the report is correct."""
    csv_path = tmp_path / "factory.csv"
    _write_messy_csv(csv_path)
    roles = {"speed": "control", "temp": "sensor", "broke": "outcome", "operator_name": "ignore"}
    data, report = load_csv(csv_path, roles)

    assert report.time_column == "timestamp"
    assert report.was_sorted_by_time
    assert data["timestamp"].is_monotonic_increasing
    assert report.sampling_interval_seconds == pytest.approx(30.0)
    assert report.unassigned_columns == ["unused"]
    assert list(data.columns) == ["timestamp", "speed", "temp", "broke"]
    assert report.missing_counts == {"speed": 1, "temp": 4, "broke": 0}
    assert report.non_numeric_values["temp"] == 1
    # The first temp value has nothing before it, so the first row is dropped.
    assert report.dropped_leading_rows == 1
    assert report.filled_counts == {"speed": 1, "temp": 3, "broke": 0}
    assert report.n_rows == 9 and report.n_columns == 3
    assert not data[["speed", "temp", "broke"]].isna().any().any()
    assert data["temp"].tolist()[5] == pytest.approx(40.5)  # forward-filled gap


def test_load_csv_rejects_bad_roles_and_columns(tmp_path: Path) -> None:
    """Unknown roles and missing columns raise clear errors."""
    csv_path = tmp_path / "factory.csv"
    _write_messy_csv(csv_path)
    with pytest.raises(ValueError, match="Invalid roles"):
        load_csv(csv_path, {"speed": "lever"})
    with pytest.raises(ValueError, match="not found"):
        load_csv(csv_path, {"pressure": "sensor"})
    with pytest.raises(ValueError, match="No usable columns"):
        load_csv(csv_path, {"speed": "ignore"})


def test_data_quality_report_detects_flatlines() -> None:
    """A stuck sensor is flagged; a varying sensor and a 0/1 column are not."""
    random_generator = np.random.default_rng(0)
    n_rows = 200
    stuck = random_generator.normal(size=n_rows)
    stuck[50 : 50 + FLATLINE_MIN_RUN + 5] = 3.0
    data = pd.DataFrame(
        {
            "healthy": random_generator.normal(size=n_rows),
            "stuck": stuck,
            "constant": np.full(n_rows, 7.0),
            "event_flag": (np.arange(n_rows) == 100).astype(int),
            "gappy": np.where(np.arange(n_rows) % 10 == 0, np.nan, 1.0 + np.arange(n_rows)),
        }
    )
    report = {item.column: item for item in data_quality_report(data)}
    assert not report["healthy"].is_flatline
    assert report["stuck"].is_flatline and report["stuck"].longest_constant_run == FLATLINE_MIN_RUN + 5
    assert report["constant"].is_flatline
    assert not report["event_flag"].is_flatline and report["event_flag"].is_binary
    assert report["gappy"].missing_count == 20
    assert report["healthy"].minimum < report["healthy"].mean < report["healthy"].maximum
