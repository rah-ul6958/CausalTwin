"""The data-source interface shared by the simulator, log replay and machine connectors."""

from dataclasses import dataclass
from typing import Any, Protocol

from causaltwin.config import EVENT_INDICATOR_PREFIX, EVENT_TYPES, MEASURED_VARIABLES


@dataclass(frozen=True)
class SourceInfo:
    """What the live data is, shown in the UI and in /health.

    `has_simulator` decides whether simulator-only features exist (ground truth, fault injection,
    scenarios, the guided demo, applying a recommendation from software). `simulated_data` decides
    whether the data must carry the "Simulated" label.
    """

    kind: str  # "simulator", "replay" or "mtconnect"
    description: str
    has_simulator: bool
    simulated_data: bool

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly form."""
        return {
            "kind": self.kind,
            "description": self.description,
            "has_simulator": self.has_simulator,
            "simulated": self.simulated_data,
        }


class DataSource(Protocol):
    """Anything the live loop can read rows from."""

    @property
    def current_step(self) -> int:
        """Index of the next row."""
        ...

    def step(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return the next row (model variables, failure, machine_running, event flags) and its events."""
        ...

    def describe(self) -> SourceInfo:
        """What this source is."""
        ...


def empty_row(step: int) -> dict[str, Any]:
    """A row with every field the live loop expects; values not provided stay NaN or 0."""
    row: dict[str, Any] = {"step": step, **{name: float("nan") for name in MEASURED_VARIABLES}}
    row["failure"] = 0
    row["machine_running"] = 1
    for event_type in EVENT_TYPES:
        row[f"{EVENT_INDICATOR_PREFIX}{event_type}"] = 0
    return row
