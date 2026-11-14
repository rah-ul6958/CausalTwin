"""Simulated CNC spindle: machine model, scenarios, fault injection, true graph."""

from causaltwin.simulator.machine import MachineSimulator, compute_hazard
from causaltwin.simulator.scenarios import SCENARIOS, SimulationResult, build_simulator, generate_dataset, run_scenario
from causaltwin.simulator.true_graph import TRUE_EDGES, get_true_graph, write_true_graph

__all__ = [
    "MachineSimulator",
    "compute_hazard",
    "SCENARIOS",
    "SimulationResult",
    "build_simulator",
    "generate_dataset",
    "run_scenario",
    "TRUE_EDGES",
    "get_true_graph",
    "write_true_graph",
]
