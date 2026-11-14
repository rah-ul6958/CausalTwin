"""Print the Correlation Trap table and simulator timings used in the phase report.

Usage (from the backend folder):  python scripts/trap_report.py
Correlations are computed on running steps of each 20,000-step scenario.
"true_hazard" is simulator ground truth (validation only).
"""

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from causaltwin.config import DEFAULT_N_STEPS  # noqa: E402
from causaltwin.simulator import SCENARIOS, run_scenario  # noqa: E402

REPORT_VARIABLES = [
    "vibration",
    "bearing_temp",
    "tool_wear",
    "motor_current",
    "coolant_temp",
    "load_pct",
    "spindle_speed",
    "coolant_flow",
    "lube_rate",
    "ambient_temp",
]
TRUE_DIRECT_CAUSES = {"bearing_temp", "tool_wear"}


def main() -> None:
    """Simulate every scenario and print timing, failure counts and correlation tables."""
    pooled_frames = []
    hazard_rows = {}
    print("| Scenario | Simulation time (s) | Failures | Tool changes | Events |")
    print("|---|---|---|---|---|")
    for scenario_name in SCENARIOS:
        start_time = time.perf_counter()
        result = run_scenario(scenario_name, DEFAULT_N_STEPS)
        elapsed_seconds = time.perf_counter() - start_time
        sensors = result.sensors.assign(true_hazard=result.ground_truth["true_hazard"])
        running = sensors[sensors["machine_running"] == 1]
        pooled_frames.append(running)
        hazard_rows[scenario_name] = running[REPORT_VARIABLES].corrwith(running["true_hazard"])
        tool_changes = int((result.events["event_type"] == "tool_change").sum())
        print(
            f"| {scenario_name} | {elapsed_seconds:.2f} | {int(sensors['failure'].sum())} | {tool_changes} | {len(result.events)} |"
        )

    hazard_table = pd.DataFrame(hazard_rows)
    pooled = pd.concat(pooled_frames)
    hazard_table["pooled corr(failure)"] = pooled[REPORT_VARIABLES].corrwith(pooled["failure"])
    print("\nCorrelation with true hazard per scenario, and with the failure event (pooled):\n")
    print("| Variable | Causal role | " + " | ".join(hazard_table.columns) + " |")
    print("|---|---|" + "---|" * len(hazard_table.columns))
    for variable, row in hazard_table.iterrows():
        if variable in TRUE_DIRECT_CAUSES:
            role = "direct cause"
        elif variable == "vibration":
            role = "NOT a cause"
        else:
            role = "upstream cause" if variable != "ambient_temp" else "upstream (environment)"
        print(f"| {variable} | {role} | " + " | ".join(f"{value:+.3f}" for value in row) + " |")


if __name__ == "__main__":
    main()
