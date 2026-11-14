"""The TRUE causal graph of the simulator (ground truth, known only because we built it).

Each edge lists cause, effect, lag (in steps), sign, edge type ("cross" between two
variables or "self" for inertia/persistence), and a one-line physical explanation.
These edges mirror the equations in `machine.py` exactly.
"""

import json
from pathlib import Path

from causaltwin.config import TRUE_GRAPH_PATH

TRUE_EDGES: list[dict[str, object]] = [
    {"cause": "load_pct", "effect": "motor_current", "lag": 1, "sign": "+", "edge_type": "cross",
     "explanation": "Higher cutting load needs more torque, so the motor draws more current."},
    {"cause": "spindle_speed", "effect": "motor_current", "lag": 1, "sign": "+", "edge_type": "cross",
     "explanation": "Spinning faster takes more electrical power."},
    {"cause": "coolant_flow", "effect": "coolant_temp", "lag": 1, "sign": "-", "edge_type": "cross",
     "explanation": "More coolant flow carries more heat away, so coolant stays cooler."},
    {"cause": "ambient_temp", "effect": "coolant_temp", "lag": 1, "sign": "+", "edge_type": "cross",
     "explanation": "Coolant cools toward room temperature, so a hotter room means hotter coolant."},
    {"cause": "motor_current", "effect": "bearing_temp", "lag": 2, "sign": "+", "edge_type": "cross",
     "explanation": "Electrical and mechanical losses from high current heat the spindle bearing."},
    {"cause": "spindle_speed", "effect": "bearing_temp", "lag": 1, "sign": "+", "edge_type": "cross",
     "explanation": "Faster rotation means more friction heat in the bearing."},
    {"cause": "coolant_temp", "effect": "bearing_temp", "lag": 2, "sign": "+", "edge_type": "cross",
     "explanation": "Warmer coolant removes less heat from the bearing housing."},
    {"cause": "lube_rate", "effect": "bearing_temp", "lag": 2, "sign": "-", "edge_type": "cross",
     "explanation": "More lubrication reduces friction, so the bearing runs cooler."},
    {"cause": "load_pct", "effect": "vibration", "lag": 1, "sign": "+", "edge_type": "cross",
     "explanation": "Heavier cuts excite stronger cutting forces and vibration."},
    {"cause": "spindle_speed", "effect": "vibration", "lag": 1, "sign": "+", "edge_type": "cross",
     "explanation": "Higher speed raises vibration energy (imbalance grows with speed)."},
    {"cause": "tool_wear", "effect": "vibration", "lag": 1, "sign": "+", "edge_type": "cross",
     "explanation": "A worn, dull tool cuts unevenly and vibrates more."},
    {"cause": "load_pct", "effect": "tool_wear", "lag": 1, "sign": "+", "edge_type": "cross",
     "explanation": "Heavier cuts wear the tool edge faster (wear increment per step)."},
    {"cause": "spindle_speed", "effect": "tool_wear", "lag": 1, "sign": "+", "edge_type": "cross",
     "explanation": "Higher cutting speed wears the tool faster (wear increment per step)."},
    {"cause": "bearing_temp", "effect": "failure", "lag": 1, "sign": "+", "edge_type": "cross",
     "explanation": "An overheated bearing loses lubrication film and seizes."},
    {"cause": "tool_wear", "effect": "failure", "lag": 1, "sign": "+", "edge_type": "cross",
     "explanation": "A badly worn tool can break and stop the machine."},
    # Self-lags (inertia and persistence).
    {"cause": "motor_current", "effect": "motor_current", "lag": 1, "sign": "+", "edge_type": "self",
     "explanation": "Motor current responds with electrical and mechanical inertia."},
    {"cause": "coolant_temp", "effect": "coolant_temp", "lag": 1, "sign": "+", "edge_type": "self",
     "explanation": "Coolant has thermal mass, so its temperature changes gradually."},
    {"cause": "bearing_temp", "effect": "bearing_temp", "lag": 1, "sign": "+", "edge_type": "self",
     "explanation": "The bearing has thermal mass, so its temperature changes gradually."},
    {"cause": "vibration", "effect": "vibration", "lag": 1, "sign": "+", "edge_type": "self",
     "explanation": "Vibration RMS is averaged over time, so it carries over between steps."},
    {"cause": "tool_wear", "effect": "tool_wear", "lag": 1, "sign": "+", "edge_type": "self",
     "explanation": "Wear accumulates: this step's wear is last step's wear plus an increment."},
    {"cause": "ambient_temp", "effect": "ambient_temp", "lag": 1, "sign": "+", "edge_type": "self",
     "explanation": "Room temperature drifts slowly (random walk plus daily cycle)."},
]

GRAPH_NOTES: list[str] = [
    "vibration has NO causal effect on failure (the Correlation Trap).",
    "Control levers are also autocorrelated through operator setpoint persistence; this is "
    "operator behavior, not physics, so lever self-lags are not listed as edges.",
    "Faults change edge strengths: coolant_filter_clog weakens coolant_flow -> coolant_temp to about 20%, "
    "lube_leak removes lube_rate -> bearing_temp, bearing_damage strengthens motor_current -> bearing_temp.",
    "All coefficients are illustrative, not from a real machine datasheet.",
]


def get_true_graph() -> dict[str, object]:
    """Return the true graph as a JSON-ready dictionary."""
    return {"source": "Simulator ground truth", "edges": TRUE_EDGES, "notes": GRAPH_NOTES}


def write_true_graph(path: Path = TRUE_GRAPH_PATH) -> Path:
    """Write the true graph to a JSON file and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(get_true_graph(), indent=2), encoding="utf-8")
    return path
