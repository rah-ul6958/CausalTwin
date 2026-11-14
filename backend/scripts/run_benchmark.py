"""Run the Correlation Trap benchmark and the drift evaluation, and print a summary.

Usage (from the backend folder):
    python scripts/run_benchmark.py            # benchmark -> data/benchmark_results.json
    python scripts/run_benchmark.py --drift    # also drift delays and false alarms -> data/drift_results.json
"""

import argparse
import json
import sys
import time
import warnings
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from causaltwin.config import BENCHMARK_RESULTS_PATH, DATA_DIR, FAULT_NAMES  # noqa: E402
from causaltwin.intelligence.benchmark import run_benchmark  # noqa: E402
from causaltwin.intelligence.drift import count_false_alarms, evaluate_fault  # noqa: E402
from causaltwin.intelligence.training import train_models  # noqa: E402

DRIFT_RESULTS_PATH = DATA_DIR / "drift_results.json"
DRIFT_SEEDS = (11, 12, 13)
FALSE_ALARM_STEPS = 20_000


def main() -> None:
    """Run and print."""
    warnings.filterwarnings("ignore")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drift", action="store_true", help="also evaluate drift detection")
    arguments = parser.parse_args()
    start = time.perf_counter()
    models = train_models()
    print(f"Models ready in {time.perf_counter() - start:.1f} s")
    start = time.perf_counter()
    results = run_benchmark(models, progress=lambda done, total, message: print(f"  [{done}/{total}] {message}"))
    print(f"Benchmark: {results['n_cases']} cases in {time.perf_counter() - start:.1f} s -> {BENCHMARK_RESULTS_PATH}")
    print(json.dumps(results["summary"], indent=2))
    for case in results["cases"]:
        ct, bl = case["causaltwin"], case["baseline"]
        print(
            f"  #{case['case_id']:2d} {case['scenario']:16s} risk {case['true_risk_no_action']:.2f} | CT "
            f"{ct['action']:<40s} -> {ct['true_risk_after']:.3f} (cost {ct['cost']:.1f}) | ML blames "
            f"{bl['blamed']:<13s} {bl['action'][:42]:<42s} -> {bl['true_risk_after']:.3f} | {case['winner']}"
        )
    print("baseline accuracy:", results["baseline_accuracy"])
    if arguments.drift:
        drift: dict[str, list[dict[str, Any]]] = {"faults": [], "false_alarms": []}
        for fault in FAULT_NAMES:
            for seed in DRIFT_SEEDS:
                outcome = evaluate_fault(models.engine, fault, seed)
                drift["faults"].append(outcome)
                print(
                    f"  {fault:20s} seed {seed}: detection delay {outcome.get('detection_delay_steps')} steps, "
                    f"rewired after {outcome.get('rewire_delay_steps')} steps, status {outcome.get('status')}"
                )
        for seed in DRIFT_SEEDS:
            outcome = count_false_alarms(models.engine, FALSE_ALARM_STEPS, seed)
            drift["false_alarms"].append(outcome)
            print(
                f"  false alarms, normal run seed {seed}: {outcome['suspected_alarms']} suspected, "
                f"{outcome['wrongly_confirmed_changes']} wrongly confirmed in {FALSE_ALARM_STEPS} steps"
            )
        DRIFT_RESULTS_PATH.write_text(json.dumps(drift, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
