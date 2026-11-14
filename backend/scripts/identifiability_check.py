"""Pre-check that the simulator's true graph is identifiable from its measured data.

Usage (from the backend folder):  python scripts/identifiability_check.py [--pc-alpha 0.2]

This is a Phase 1 check of the SIMULATOR, not the Phase 2 engine. It runs PCMCI with
ParCorr (tau_max = 3, final alpha = 0.01) on the last 3,000 steps of each scenario and
scores the discovered cross edges against the true graph (failure edges and self-lags
excluded). Stopped-machine rows and tool-reset rows are masked, because they are regime
breaks, not causal dynamics. Takes roughly 6 seconds per window.
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from tigramite import data_processing
from tigramite.independence_tests.parcorr import ParCorr
from tigramite.pcmci import PCMCI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from causaltwin.config import ENVIRONMENT_NAMES, LEVER_NAMES, MEASURED_VARIABLES  # noqa: E402
from causaltwin.simulator import SCENARIOS, TRUE_EDGES, run_scenario  # noqa: E402

WINDOW_STEPS = 3000
TAU_MAX = 3
FINAL_ALPHA = 0.01
RESET_EVENTS = ("tool_change", "maintenance")
TRUE_CROSS_EDGES = {
    (edge["cause"], edge["effect"]): edge["lag"]
    for edge in TRUE_EDGES
    if edge["edge_type"] == "cross" and edge["effect"] != "failure"
}


def build_mask(sensors: pd.DataFrame, events: pd.DataFrame) -> np.ndarray:
    """True where a value must be ignored: stopped machine, or tool wear reset rows."""
    variables = list(MEASURED_VARIABLES)
    mask = np.zeros((len(sensors), len(variables)), dtype=bool)
    mask[sensors["machine_running"].to_numpy() == 0, :] = True
    reset_steps = events.loc[events["event_type"].isin(RESET_EVENTS), "step"].to_numpy()
    reset_rows = np.isin(sensors["step"].to_numpy(), reset_steps)
    for variable in ("tool_wear", "vibration"):
        mask[reset_rows, variables.index(variable)] = True
    return mask


def discover_edges(values: np.ndarray, mask: np.ndarray, pc_alpha: float) -> dict[tuple[str, str], tuple[int, float]]:
    """Run PCMCI and return the strongest significant lag per (cause, effect) pair."""
    variables = list(MEASURED_VARIABLES)
    exogenous = set(LEVER_NAMES) | set(ENVIRONMENT_NAMES)
    link_assumptions = {
        effect_index: (
            {}
            if effect in exogenous
            else {(cause_index, -lag): "-?>" for cause_index in range(len(variables)) for lag in range(1, TAU_MAX + 1)}
        )
        for effect_index, effect in enumerate(variables)
    }
    frame = data_processing.DataFrame(values, var_names=variables, mask=mask)
    results = PCMCI(dataframe=frame, cond_ind_test=ParCorr(mask_type="xyz"), verbosity=0).run_pcmci(
        tau_min=1, tau_max=TAU_MAX, pc_alpha=pc_alpha, alpha_level=FINAL_ALPHA, link_assumptions=link_assumptions
    )
    found: dict[tuple[str, str], tuple[int, float]] = {}
    for cause_index, cause in enumerate(variables):
        for effect_index, effect in enumerate(variables):
            if cause_index == effect_index:
                continue
            for lag in range(1, TAU_MAX + 1):
                if results["p_matrix"][cause_index, effect_index, lag] < FINAL_ALPHA:
                    strength = float(results["val_matrix"][cause_index, effect_index, lag])
                    if (cause, effect) not in found or abs(strength) > abs(found[(cause, effect)][1]):
                        found[(cause, effect)] = (lag, strength)
    return found


def main() -> None:
    """Score PCMCI recovery on the last window of every scenario."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pc-alpha", type=float, default=0.2, help="alpha of the PC condition-selection stage")
    arguments = parser.parse_args()
    print(
        f"PCMCI pre-check: window={WINDOW_STEPS}, tau_max={TAU_MAX}, "
        f"pc_alpha={arguments.pc_alpha}, alpha={FINAL_ALPHA}\n"
    )
    print("| Scenario | Recall | Precision | Correct lag | Time (s) | Missed edges | Extra edges |")
    print("|---|---|---|---|---|---|---|")
    for scenario_name in SCENARIOS:
        result = run_scenario(scenario_name)
        values = result.sensors[list(MEASURED_VARIABLES)].to_numpy()[-WINDOW_STEPS:]
        mask = build_mask(result.sensors, result.events)[-WINDOW_STEPS:]
        start_time = time.perf_counter()
        found = discover_edges(values, mask, arguments.pc_alpha)
        elapsed_seconds = time.perf_counter() - start_time
        true_positives = [pair for pair in found if pair in TRUE_CROSS_EDGES]
        missed = [f"{cause}->{effect}" for cause, effect in TRUE_CROSS_EDGES if (cause, effect) not in found]
        extra = [
            f"{cause}->{effect} (lag {found[(cause, effect)][0]})"
            for cause, effect in found
            if (cause, effect) not in TRUE_CROSS_EDGES
        ]
        correct_lags = sum(found[pair][0] == TRUE_CROSS_EDGES[pair] for pair in true_positives)
        print(
            f"| {scenario_name} | {len(true_positives) / len(TRUE_CROSS_EDGES):.2f} | "
            f"{len(true_positives) / max(1, len(found)):.2f} | {correct_lags}/{len(true_positives)} | "
            f"{elapsed_seconds:.1f} | {', '.join(missed) or '-'} | {', '.join(extra) or '-'} |"
        )


if __name__ == "__main__":
    main()
