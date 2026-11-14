"""Save one sample CSV per scenario (sensors and events) and write the true graph.

Usage (from the backend folder):
    python scripts/generate_samples.py            # 10,000 steps per scenario
    python scripts/generate_samples.py --steps 20000

All output is SIMULATED data from the CausalTwin spindle simulator.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from causaltwin.config import SAMPLES_DIR  # noqa: E402
from causaltwin.simulator import SCENARIOS, run_scenario, write_true_graph  # noqa: E402

SAMPLE_STEPS = 10_000
CSV_FLOAT_FORMAT = "%.4f"


def main() -> None:
    """Generate every scenario and write sensors/events CSV files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=SAMPLE_STEPS, help="steps per scenario")
    arguments = parser.parse_args()

    graph_path = write_true_graph()
    print(f"Wrote {graph_path}")
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    for scenario_name in SCENARIOS:
        result = run_scenario(scenario_name, arguments.steps)
        sensors_path = SAMPLES_DIR / f"{scenario_name}_sensors.csv"
        events_path = SAMPLES_DIR / f"{scenario_name}_events.csv"
        result.sensors.to_csv(sensors_path, index=False, float_format=CSV_FLOAT_FORMAT)
        result.events.to_csv(events_path, index=False)
        failure_count = int(result.sensors["failure"].sum())
        print(f"{scenario_name:16s} {arguments.steps} steps, {failure_count} failures, {len(result.events)} events")


if __name__ == "__main__":
    main()
