"""Why CausalTwin's AI4I actions do not remove some failures: a per-failure-mode diagnosis.

    python scripts/diagnose_ai4i.py

For each failure mode it reports how the fitted failure model rates the failed rows when nothing is
done, which actions CausalTwin chose, and which documented failure rules still fire after them. It
also lists the failure screen's verdict on every candidate cause. Uses the same fit and the same
action search as scripts/run_ai4i.py. Results -> data/ai4i_diagnosis.json.
"""

import json
import sys
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from causaltwin.config import (  # noqa: E402
    AI4I_HDF_SPEED_RPM,
    AI4I_PWF_POWER_RANGE_W,
    AI4I_TARGET_PROBABILITY,
    DATA_DIR,
)
from causaltwin.intelligence.ai4i import (  # noqa: E402
    LEVERS,
    MODES,
    _scored_rows,
    apply_action,
    candidate_actions,
    causaltwin_action,
    failure_probability,
    fit_causaltwin,
    load_ai4i,
    rule_failures,
)

OUTPUT_PATH = DATA_DIR / "ai4i_diagnosis.json"


def _action_kinds(action_text: str) -> list[str]:
    """ "speed +20%, tool change" -> ["speed up", "tool change"]."""
    kinds = []
    for part in action_text.split(", "):
        if part.startswith("speed"):
            kinds.append("speed up" if "+" in part else "speed down")
        elif part.startswith("torque"):
            kinds.append("torque up" if "+" in part else "torque down")
        elif part:
            kinds.append(part)
    return kinds


def main() -> None:
    """Fit, act on every scored row, and summarise per failure mode."""
    warnings.filterwarnings("ignore")
    data = load_ai4i()
    engine = fit_causaltwin(data)
    screen = engine.failure_screen
    model = engine._require_fitted()
    ranges = {lever: (float(data[lever].min()), float(data[lever].max())) for lever in LEVERS}
    actions = candidate_actions()
    low_power, high_power = AI4I_PWF_POWER_RANGE_W

    healthy = data[data["failure"] == 0]
    healthy_risk = [failure_probability(engine, row) for _, row in healthy.sample(2000, random_state=0).iterrows()]

    rows: list[dict[str, Any]] = []
    for _, row in _scored_rows(data).iterrows():
        action, predicted_after = causaltwin_action(engine, row, actions, ranges)
        after = apply_action(row, action)
        modes = [mode for mode in MODES if row[mode]]
        if "PWF" in modes:
            modes.append("PWF low power" if row["power_w"] < low_power else "PWF high power")
        rows.append(
            {
                "modes": modes,
                "predicted_no_action": failure_probability(engine, row),
                "predicted_after": predicted_after,
                "action": action.describe(),
                "kinds": _action_kinds(action.describe()),
                "failures_after": rule_failures(after, bool(row["TWF"])),
                "speed_after": float(after["speed_rpm"]),
                "power_before": float(row["power_w"]),
                "power_after": float(after["power_w"]),
            }
        )

    per_mode: dict[str, Any] = {}
    for mode in (*MODES, "PWF low power", "PWF high power"):
        group = [row for row in rows if mode in row["modes"]]
        if not group:
            continue
        predicted = np.array([row["predicted_no_action"] for row in group])
        summary: dict[str, Any] = {
            "n": len(group),
            "success": sum(not row["failures_after"] for row in group),
            "predicted_no_action_median": float(np.median(predicted)),
            "share_predicted_below_target_with_no_action": float(np.mean(predicted <= AI4I_TARGET_PROBABILITY)),
            "actions": dict(Counter(kind for row in group for kind in (row["kinds"] or ["no action"])).most_common()),
            "failures_after": dict(Counter(mode for row in group for mode in row["failures_after"]).most_common()),
        }
        if mode == "HDF":
            summary["share_speed_raised_to_limit"] = float(
                np.mean([row["speed_after"] >= AI4I_HDF_SPEED_RPM for row in group])
            )
        if mode.startswith("PWF"):
            summary["share_power_moved_into_window"] = float(
                np.mean([low_power <= row["power_after"] <= high_power for row in group])
            )
            summary["share_power_raised"] = float(np.mean([row["power_after"] > row["power_before"] for row in group]))
        if mode == "TWF":
            summary["share_tool_change"] = float(np.mean(["tool change" in row["kinds"] for row in group]))
        per_mode[mode] = summary

    output = {
        "failure_screen": [
            {
                "variable": candidate.variable,
                "coefficient_per_sd": candidate.coefficient,
                "p_value": candidate.p_value,
                "significant": candidate.significant,
            }
            for candidate in (screen.candidates if screen is not None else [])
        ],
        "failure_model_terms": [] if model.failure is None else [name for name, _ in model.failure.terms],
        "failure_model_coefficients": [] if model.failure is None else model.failure.coefficients.tolist(),
        "healthy_rows_predicted_median": float(np.median(healthy_risk)),
        "target_probability": AI4I_TARGET_PROBABILITY,
        "per_mode": per_mode,
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print("Failure screen (every candidate cause):")
    for candidate in output["failure_screen"]:
        print(
            f"  {candidate['variable']:26s} coefficient per sd {candidate['coefficient_per_sd']:+8.3f}  "
            f"p = {candidate['p_value']:.3g}  {'cause' if candidate['significant'] else '-'}"
        )
    print("Failure model terms:", output["failure_model_terms"], output["failure_model_coefficients"])
    print(f"Healthy rows, median predicted probability: {output['healthy_rows_predicted_median']:.4f}")
    for mode, summary in per_mode.items():
        print(f"{mode}: {json.dumps(summary)}")
    print(f"-> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
