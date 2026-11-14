"""Shared pytest fixtures: every scenario is simulated once per test session."""

import pytest

from causaltwin.config import DEFAULT_N_STEPS
from causaltwin.simulator import SCENARIOS, SimulationResult, run_scenario


@pytest.fixture(scope="session")
def scenario_results() -> dict[str, SimulationResult]:
    """Full-length (20,000 step) run of every scenario with its fixed seed."""
    return {name: run_scenario(name, DEFAULT_N_STEPS) for name in SCENARIOS}
