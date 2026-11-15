"""Measure every pipeline step against the performance budgets and write data/timings.json.

Run from backend/ on a quiet machine (stop the API server first):
    python scripts/measure_timings.py                 # about 2 minutes, includes the full benchmark
    python scripts/measure_timings.py --skip-benchmark

Each operation runs several times; the median is compared with its budget from config.py
(PERFORMANCE_BUDGETS_MS, taken from CLAUDE.md section 4) and the slowest run is reported too.
The README's timing table is generated from the JSON file (scripts/update_readme_results.py).
"""

import argparse
import json
import os
import platform
import statistics
import sys
import time
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from causaltwin.api.hub import StreamHub  # noqa: E402
from causaltwin.api.live import DemoScript, LiveLoop  # noqa: E402
from causaltwin.config import DATA_DIR, PERFORMANCE_BUDGETS_MS  # noqa: E402
from causaltwin.engine.twin import CausalTwinEngine  # noqa: E402
from causaltwin.intelligence.baseline import BaselineModel  # noqa: E402
from causaltwin.intelligence.benchmark import run_benchmark  # noqa: E402
from causaltwin.intelligence.training import TrainedModels, train_models  # noqa: E402
from causaltwin.simulator import run_scenario  # noqa: E402
from causaltwin.utils.activity_log import get_activity_logger  # noqa: E402

TIMINGS_PATH = DATA_DIR / "timings.json"
SIMULATION_STEPS = 20_000
SIMULATION_SEED = 99
REPEATS = {"simulate": 3, "discovery": 3, "what_if": 20, "recommend": 5, "baseline_fit": 2}


def _time_runs(function: Callable[[], Any], repeats: int) -> list[float]:
    """Wall-clock milliseconds of each run."""
    durations = []
    for _ in range(repeats):
        start = time.perf_counter()
        function()
        durations.append((time.perf_counter() - start) * 1000)
    return durations


def _entry(key: str, operation: str, durations: list[float], note: str = "") -> dict[str, Any]:
    """One row of the timings table, judged by the median against the budget."""
    median = statistics.median(durations)
    budget = PERFORMANCE_BUDGETS_MS[key]
    print(f"{operation}: median {median:.0f} ms, max {max(durations):.0f} ms, budget {budget:.0f} ms")
    return {
        "key": key,
        "operation": operation,
        "runs": len(durations),
        "median_ms": round(median, 1),
        "max_ms": round(max(durations), 1),
        "budget_ms": budget,
        "within_budget": median <= budget,
        "note": note,
    }


def _logged_durations(step_name: str, since: int) -> list[float]:
    """Durations of one pipeline step logged after position `since` in the activity log."""
    entries = get_activity_logger().entries("exclude")[since:]
    return [entry.duration_ms for entry in entries if entry.step_name == step_name and entry.duration_ms is not None]


def _measure_engine(models: TrainedModels) -> tuple[dict[str, Any], dict[str, Any]]:
    """Discovery and model fitting, as logged by the engine itself (warm worker processes)."""
    engine = CausalTwinEngine()
    engine.warm_up()
    since = len(get_activity_logger().entries("exclude"))
    for _ in range(REPEATS["discovery"]):
        engine.fit(models.training_data)
    discovery = _entry(
        "discovery",
        "Causal discovery on one 3,000-step window",
        _logged_durations("discovery", since),
        "PCMCI on the window plus the failure-cause screen; warm worker processes",
    )
    fit = _entry(
        "fit_model", "Fit the causal model", _logged_durations("fit_model", since), "Ridge and logistic equations"
    )
    return discovery, fit


def _high_risk_loop(models: TrainedModels) -> LiveLoop:
    """A live loop in the demo's high-risk state (normal operation, then the hot, aggressive job)."""
    live = LiveLoop(models, StreamHub())
    demo = DemoScript(live)
    demo.start()
    demo.next()
    return live


def main() -> None:
    """Measure, print and save."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--skip-benchmark", action="store_true", help="do not time the full benchmark")
    arguments = parser.parse_args()
    warnings.filterwarnings("ignore")
    models = train_models()
    operations = [
        _entry(
            "simulate",
            "Simulate 20,000 steps",
            _time_runs(lambda: run_scenario("normal", SIMULATION_STEPS, SIMULATION_SEED), REPEATS["simulate"]),
            "normal scenario, sensors, events and ground truth",
        )
    ]
    operations.extend(_measure_engine(models))
    live = _high_risk_loop(models)
    history = live.history()
    operations.append(
        _entry(
            "what_if",
            "One what-if simulation (200 rollouts)",
            _time_runs(lambda: live.engine.what_if([], history=history), REPEATS["what_if"]),
            "30-step horizon, with the paired no-intervention baseline",
        )
    )
    operations.append(
        _entry(
            "recommend",
            "Recommendation search",
            _time_runs(live.compute_recommendation, REPEATS["recommend"]),
            "about 870 candidates plus the top-5 re-check, at the demo's high-risk state",
        )
    )
    operations.append(
        _entry(
            "baseline_fit",
            "Baseline XGBoost training",
            _time_runs(lambda: BaselineModel().fit(models.training_data), REPEATS["baseline_fit"]),
            "evaluation fit plus final fit, 60,000 rows",
        )
    )
    if not arguments.skip_benchmark:
        operations.append(
            _entry(
                "benchmark",
                "Full benchmark (30 cases)",
                _time_runs(lambda: run_benchmark(models, output_path=None), 1),
                "cached models; results are cached afterwards",
            )
        )
    results = {
        "source": "scripts/measure_timings.py",
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": {
            "system": platform.system(),
            "processor": platform.processor() or platform.machine(),
            "logical_cpus": os.cpu_count(),
            "python": platform.python_version(),
        },
        "note": "Laptop timings vary up to about 3x with CPU throttling (see the Phase 2 report).",
        "operations": operations,
    }
    TIMINGS_PATH.write_bytes((json.dumps(results, indent=2) + "\n").encode("utf-8"))  # LF line endings on every OS
    print(f"Wrote {TIMINGS_PATH}")


if __name__ == "__main__":
    main()
