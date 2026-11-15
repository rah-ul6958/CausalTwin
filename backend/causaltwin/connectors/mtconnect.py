"""Read a machine's current values from an MTConnect agent (HTTP + XML, read-only).

MTConnect is an open standard many CNC machines expose: the agent answers GET {url}/current with
an XML document listing every data item's latest value. Nothing is ever written to the machine.

A tag map links model variables to the agent's data items, by `dataItemId` or `name`, for example
{"spindle_speed": "Srpm", "bearing_temp": "spindle_bearing_temp", "machine_running": "execution"}.
Two optional entries have special meaning:
- "machine_running": an event whose value ACTIVE, READY, AVAILABLE or ON means running;
- "failure": a condition item; a FAULT state counts as a failure.
Unmapped or UNAVAILABLE values carry the last known value forward.
"""

import math
import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree
from typing import Any

from causaltwin.config import MEASURED_VARIABLES
from causaltwin.connectors.base import SourceInfo, empty_row

RUNNING_VALUES = {"ACTIVE", "READY", "AVAILABLE", "ON", "TRUE", "1"}
CONDITION_STATES = {"Normal", "Warning", "Fault", "Unavailable"}
DEFAULT_TIMEOUT_SECONDS = 5.0


def _local_name(tag: str) -> str:
    """XML tag without its namespace: "{urn:mtconnect...}RotaryVelocity" -> "RotaryVelocity"."""
    return tag.rsplit("}", 1)[-1]


def parse_mtconnect_current(document: bytes) -> dict[str, str]:
    """Latest value of every data item in an MTConnect Streams document, keyed by id and by name.

    For condition items the value is the state (Normal, Warning, Fault or Unavailable).
    """
    root = ElementTree.fromstring(document)
    values: dict[str, str] = {}
    for element in root.iter():
        item_id = element.attrib.get("dataItemId")
        if item_id is None:
            continue
        tag = _local_name(element.tag)
        value = tag if tag in CONDITION_STATES else (element.text or "").strip()
        values[item_id] = value
        name = element.attrib.get("name")
        if name:
            values[name] = value
    return values


def _number(text: str | None) -> float:
    """A numeric reading, or NaN for missing and UNAVAILABLE values."""
    if text is None:
        return math.nan
    try:
        return float(text)
    except ValueError:
        return math.nan


class MTConnectSource:
    """One row per `step()`, read from the agent's /current endpoint."""

    def __init__(
        self, agent_url: str, tag_map: dict[str, str], timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        """Remember the agent and the mapping; nothing is fetched until the first step."""
        unknown = sorted(set(tag_map) - set(MEASURED_VARIABLES) - {"machine_running", "failure"})
        if unknown:
            raise ValueError(f"Tag map has unknown model variables: {unknown}")
        self.agent_url = agent_url.rstrip("/")
        self.tag_map = tag_map
        self.timeout_seconds = timeout_seconds
        self.current_step = 0
        self._last: dict[str, float] = {}

    def fetch(self) -> dict[str, str]:
        """The agent's current values (raises ConnectionError with a clear message on failure)."""
        url = f"{self.agent_url}/current"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_seconds) as response:  # noqa: S310 - operator-configured URL
                return parse_mtconnect_current(response.read())
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ConnectionError(f"Cannot read the MTConnect agent at {url}: {error}") from error
        except ElementTree.ParseError as error:
            raise ConnectionError(f"The MTConnect agent at {url} returned invalid XML: {error}") from error

    def step(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch the current values and turn them into one row of the model variables."""
        values = self.fetch()
        row = empty_row(self.current_step)
        for name in MEASURED_VARIABLES:
            value = _number(values.get(self.tag_map[name])) if name in self.tag_map else math.nan
            if math.isnan(value):
                value = self._last.get(name, math.nan)
            self._last[name] = value
            row[name] = value
        if "machine_running" in self.tag_map:
            state = values.get(self.tag_map["machine_running"], "")
            row["machine_running"] = int(state.upper() in RUNNING_VALUES)
        if "failure" in self.tag_map:
            row["failure"] = int(values.get(self.tag_map["failure"], "") == "Fault")
        self.current_step += 1
        return row, []

    def describe(self) -> SourceInfo:
        """A real machine through MTConnect: no simulator, no ground truth."""
        return SourceInfo(
            kind="mtconnect",
            description=f"MTConnect agent at {self.agent_url}",
            has_simulator=False,
            simulated_data=False,
        )
