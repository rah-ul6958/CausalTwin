"""Run the benchmark with each optimizer selection rule and compare them (about half a minute).

    python scripts/compare_selection_rules.py

"confidence": an option qualifies if at least OPTIMIZER_REQUIRED_CONFIDENCE of its rollouts end at or
below the target. "margin": its mean predicted risk is at or below target * OPTIMIZER_PLANNING_MARGIN.
Both runs use the same situations and seeds. Results -> data/selection_rule_comparison.json. The
benchmark results file itself is not touched.
"""

import json
import sys
import warnings
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from causaltwin.config import (  # noqa: E402
    DATA_DIR,
    OPTIMIZER_REQUIRED_CONFIDENCE,
    OPTIMIZER_SELECTION_RULE,
    OPTIMIZER_SELECTION_RULES,
)
from causaltwin.intelligence.benchmark import run_benchmark  # noqa: E402
from causaltwin.intelligence.training import train_models  # noqa: E402

OUTPUT_PATH = DATA_DIR / "selection_rule_comparison.json"


def _rule_summary(results: dict[str, Any]) -> dict[str, Any]:
    """The numbers the comparison needs from one benchmark run."""
    summary = results["summary"]["causaltwin"]
    return {
        "n_cases": results["n_cases"],
        "n_success": summary["n_success"],
        "success_rate": summary["success_rate"],
        "mean_cost": summary["mean_cost"],
        "mean_true_risk_after": summary["mean_true_risk_after"],
        "mean_failure_rate_after": summary["mean_failure_rate_after"],
        "n_worse_than_no_action": summary["n_worse_than_no_action"],
        "calibration_after_action": results["calibration"].get("after_action", {}),
        "missed_cases": [case["case_id"] for case in results["cases"] if not case["causaltwin"]["success"]],
        "actions": {case["case_id"]: case["causaltwin"]["action"] for case in results["cases"]},
        "true_risk_after": {case["case_id"]: case["causaltwin"]["true_risk_after"] for case in results["cases"]},
        "costs": {case["case_id"]: case["causaltwin"]["cost"] for case in results["cases"]},
        "seconds": results["timing_seconds"]["total"],
    }


def main() -> None:
    """Run both rules, print a comparison, and save it."""
    warnings.filterwarnings("ignore")
    models = train_models()
    rules = {
        rule: _rule_summary(run_benchmark(models, output_path=None, selection_rule=rule))
        for rule in OPTIMIZER_SELECTION_RULES
    }
    first, second = OPTIMIZER_SELECTION_RULES
    changed = [
        {
            "case_id": case_id,
            **{f"{rule}_action": rules[rule]["actions"][case_id] for rule in rules},
            **{f"{rule}_true_risk_after": rules[rule]["true_risk_after"][case_id] for rule in rules},
            **{f"{rule}_cost": rules[rule]["costs"][case_id] for rule in rules},
        }
        for case_id in rules[first]["actions"]
        if rules[first]["actions"][case_id] != rules[second]["actions"][case_id]
    ]
    output = {
        "active_rule": OPTIMIZER_SELECTION_RULE,
        "required_confidence": OPTIMIZER_REQUIRED_CONFIDENCE,
        "rules": {
            rule: {key: value for key, value in row.items() if key not in ("actions", "true_risk_after", "costs")}
            for rule, row in rules.items()
        },
        "cases_with_different_actions": changed,
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    for rule, row in output["rules"].items():
        calibration = row["calibration_after_action"]
        error = calibration.get("mean_absolute_error", float("nan"))
        coverage = calibration.get("interval_coverage", float("nan"))
        print(
            f"{rule:10s} success {row['n_success']}/{row['n_cases']}, mean cost {row['mean_cost']:.2f}, mean true risk "
            f"after {row['mean_true_risk_after']:.3f}, missed {row['missed_cases']}, after-action MAE "
            f"{error:.4f}, coverage {coverage:.2f}"
        )
    for case in changed:
        outcomes = [
            f"{rule}: {case[f'{rule}_action']} -> {case[f'{rule}_true_risk_after']:.3f} "
            f"(cost {case[f'{rule}_cost']:.1f})"
            for rule in rules
        ]
        print(f"  case {case['case_id']}: " + " | ".join(outcomes))
    print(f"-> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
