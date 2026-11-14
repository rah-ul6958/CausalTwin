"""Validate the causal engine against simulator ground truth and save the results.

Usage (from the backend folder):  python scripts/run_validation.py
Writes backend/data/validation_results.json and prints a summary.
"""

import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from causaltwin.config import VALIDATION_RESULTS_PATH  # noqa: E402
from causaltwin.engine.validation import run_validation  # noqa: E402


def main() -> None:
    """Run the validation and print the headline numbers."""
    warnings.filterwarnings("ignore")
    start_time = time.perf_counter()
    results = run_validation()
    print(f"Validation finished in {time.perf_counter() - start_time:.1f} s -> {VALIDATION_RESULTS_PATH}")
    for label in ("graph_recovery_with_failure", "graph_recovery_without_failure"):
        metrics = results[label]
        print(
            f"{label}: precision={metrics['precision']:.2f} recall={metrics['recall']:.2f} f1={metrics['f1']:.2f} "
            f"lag_accuracy={metrics['lag_accuracy']:.2f} SHD={metrics['structural_hamming_distance']} "
            f"missed={metrics['missed_edges']} extra={metrics['extra_edges']} wrong_lags={metrics['wrong_lags']}"
        )
    for name, metrics in results["per_scenario_recovery_without_failure"].items():
        print(
            f"  {name:16s} precision={metrics['precision']:.2f} recall={metrics['recall']:.2f} "
            f"lag_accuracy={metrics['lag_accuracy']:.2f} missed={metrics['missed_edges']} "
            f"extra={metrics['extra_edges']}"
        )
    accuracy = {key: value for key, value in results["intervention_accuracy"].items() if key != "cases"}
    print("intervention_accuracy:", accuracy)
    print("trap_check:", results["trap_check"])
    print("numpy_vs_tigramite:", results["numpy_vs_tigramite_max_abs_difference"])
    print("timings_ms:", {key: round(value, 1) for key, value in results["timings_ms"].items()})


if __name__ == "__main__":
    main()
