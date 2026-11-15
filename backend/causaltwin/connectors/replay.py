"""Replay a recorded sensor log (CSV) as if it were a live machine.

Useful to demonstrate the real-data path, to test a twin on a factory's recorded history before
connecting it, and as an offline fallback. Columns are matched by name to the model variables,
optionally through a mapping {model variable: CSV column}. Missing values carry the last known
value forward. At the end of the file the replay starts again from the top (or stops).
"""

import math
from pathlib import Path
from typing import Any

import pandas as pd

from causaltwin.config import MEASURED_VARIABLES
from causaltwin.connectors.base import SourceInfo, empty_row


class ReplaySource:
    """Rows of a CSV, one per step."""

    def __init__(
        self, path: Path, mapping: dict[str, str] | None = None, simulated_data: bool = True, loop: bool = True
    ) -> None:
        """Load the file. `simulated_data` says whether the log itself is simulated (the samples are)."""
        self.path = Path(path)
        self.mapping = mapping or {}
        self.simulated_data = simulated_data
        self.loop = loop
        self.frame = pd.read_csv(self.path)
        missing = [name for name in MEASURED_VARIABLES if self.mapping.get(name, name) not in self.frame.columns]
        if len(missing) == len(MEASURED_VARIABLES):
            raise ValueError(f"{self.path.name} has none of the model variables; give a column mapping.")
        self.current_step = 0
        self._position = 0
        self._last: dict[str, float] = {}

    def step(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """The next row of the file (with forward fill), plus no events."""
        if self._position >= len(self.frame):
            if not self.loop:
                raise StopIteration(f"End of {self.path.name}")
            self._position = 0
        record = self.frame.iloc[self._position]
        self._position += 1
        row = empty_row(self.current_step)
        for name in MEASURED_VARIABLES:
            column = self.mapping.get(name, name)
            value = float(record[column]) if column in record.index else math.nan
            if math.isnan(value):
                value = self._last.get(name, math.nan)
            self._last[name] = value
            row[name] = value
        for flag in ("failure", "machine_running"):
            column = self.mapping.get(flag, flag)
            if column in record.index and not pd.isna(record[column]):
                row[flag] = int(record[column])
        self.current_step += 1
        return row, []

    def describe(self) -> SourceInfo:
        """A recorded log; simulated or not as declared."""
        label = "simulated" if self.simulated_data else "recorded"
        return SourceInfo(
            kind="replay",
            description=f"Replay of the {label} log {self.path.name}",
            has_simulator=False,
            simulated_data=self.simulated_data,
        )
