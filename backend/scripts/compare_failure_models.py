"""Run the simulator benchmark with each failure model on the same discovered causes and compare them.

    python scripts/compare_failure_models.py

Variants: the linear (logistic) failure model, the spline failure model, and the spline model with
pairwise products. Discovery, the graph and the linear equations are the same trained engine; only the
failure model is refitted (CausalTwinEngine.with_failure_model). Each variant runs the same 30
benchmark situations with the same seeds. Results -> data/failure_model_comparison.json. The benchmark
results file itself is not touched.
"""

import dataclasses
import json
import statistics
import sys
import time
import warnings
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from causaltwin.config import DATA_DIR, FAILURE_MODEL_KIND  # noqa: E402
from causaltwin.engine.twin import CausalTwinEngine  # noqa: E402
from causaltwin.intelligence.benchmark import run_benchmark  # noqa: E402
from causaltwin.intelligence.optimizer import recommend  # noqa: E402
from causaltwin.intelligence.training import TrainedModels, train_models  # noqa: E402

OUTPUT_PATH = DATA_DIR / "failure_model_comparison.json"
TIMING_STATES = 10  # states just before training failures, for the recommendation search time


def _variants(models: TrainedModels) -> dict[str, CausalTwinEngine]:
    """The trained engine with each failure model."""
    return {
        "linear": models.engine.with_failure_model("linear"),
        "spline": models.engine.with_failure_model("spline"),
        "spline_products": models.engine.with_failure_model("spline", interactions=True),
    }


def _recommend_milliseconds(engine: CausalTwinEngine, models: TrainedModels) -> dict[str, float]:
    """Median and slowest recommendation search time on states before training failures."""
    model = engine._require_fitted()
    data = models.training_data
    rows = data.index[data["failure"] == 1][:TIMING_STATES]
    recommend(engine, data.loc[rows[0] - 4 : rows[0] - 1, model.variables].to_numpy(dtype=float))  # warm-up
    times = [
        recommend(engine, data.loc[row - 4 : row - 1, model.variables].to_numpy(dtype=float)).search_ms for row in rows
    ]
    return {"median": statistics.median(times), "max": max(times)}


def main() -> None:
    """Run the benchmark once per failure model, print a comparison and save it."""
    warnings.filterwarnings("ignore")
    models = train_models()
    output: dict[str, Any] = {"default_failure_model": FAILURE_MODEL_KIND, "variants": {}}
    for name, engine in _variants(models).items():
        start = time.perf_counter()
        results = run_benchmark(dataclasses.replace(models, engine=engine), output_path=None)
        summary = results["summary"]["causaltwin"]
        failure = engine._require_fitted().failure
        output["variants"][name] = {
            "n_cases": results["n_cases"],
            "n_success": summary["n_success"],
            "success_rate": summary["success_rate"],
            "mean_true_risk_after": summary["mean_true_risk_after"],
            "mean_failure_rate_after": summary["mean_failure_rate_after"],
            "mean_cost": summary["mean_cost"],
            "n_worse_than_no_action": summary["n_worse_than_no_action"],
            "n_false_reassurance": summary["n_false_reassurance"],
            "missed_cases": [case["case_id"] for case in results["cases"] if not case["causaltwin"]["success"]],
            "calibration": {key: results["calibration"].get(key) for key in ("no_action", "after_action")},
            "failure_auc_holdout": None if failure is None else failure.auc_holdout,
            "recommend_ms": _recommend_milliseconds(engine, models),
            "seconds": time.perf_counter() - start,
        }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    for name, row in output["variants"].items():
        no_action, after = row["calibration"]["no_action"], row["calibration"]["after_action"]
        print(
            f"{name:16s} success {row['n_success']}/{row['n_cases']}, mean true risk after "
            f"{row['mean_true_risk_after']:.3f}, mean cost {row['mean_cost']:.2f}, missed {row['missed_cases']}, "
            f"AUC {row['failure_auc_holdout']:.3f}, no-action MAE {no_action['mean_absolute_error']:.4f} "
            f"coverage {no_action['interval_coverage']:.2f}, after-action MAE {after['mean_absolute_error']:.4f} "
            f"coverage {after['interval_coverage']:.2f}, recommend {row['recommend_ms']['median']:.0f} ms median"
        )
    print(f"-> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
