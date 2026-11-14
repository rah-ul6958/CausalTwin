"""Print the Correlation Trap evidence and simulator timings used in the phase report.

Usage (from the backend folder):  python scripts/trap_report.py

All numbers come from the five 20,000-step scenarios with their fixed seeds, using
running steps only. "true_hazard" is simulator ground truth (validation only).
Sections:
1. timings and event counts per scenario
2. correlation of each variable with the true hazard, and with failure (pooled)
3. single-variable AUC for "failure within the next 30 steps" (how a naive analyst ranks signals)
4. a naive XGBoost + SHAP model on the same target (global and local attributions)
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import shap
import xgboost
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from causaltwin.config import DEFAULT_N_STEPS, MEASURED_VARIABLES  # noqa: E402
from causaltwin.simulator import SCENARIOS, run_scenario  # noqa: E402

PREDICTION_HORIZON = 30  # steps ahead for the "failure within horizon" target
XGBOOST_SEED = 0
SHAP_SAMPLE_SIZE = 5000
HIGH_RISK_QUANTILE = 0.99  # "high-risk states" = top 1% of true hazard
HIGH_RISK_SAMPLE_SIZE = 500
DIRECT_CAUSES = {"bearing_temp", "tool_wear"}


def causal_role(variable: str) -> str:
    """Describe a variable's true causal role with respect to failure."""
    if variable in DIRECT_CAUSES:
        return "direct cause"
    if variable == "vibration":
        return "NOT a cause"
    return "upstream cause"


def failure_within_horizon(failure: pd.Series, horizon: int) -> pd.Series:
    """1 if a failure happens in the next `horizon` steps (not counting the current one)."""
    future_max = failure[::-1].rolling(horizon, min_periods=1).max()[::-1]
    return future_max.shift(-1)


def print_markdown_table(table: pd.DataFrame, first_column: str) -> None:
    """Print a dataframe as a Markdown table with signed 3-decimal numbers."""
    print(f"| {first_column} | " + " | ".join(str(column) for column in table.columns) + " |")
    print("|---|" + "---|" * len(table.columns))
    for index, row in table.iterrows():
        cells = [value if isinstance(value, str) else f"{value:+.3f}" for value in row]
        print(f"| {index} | " + " | ".join(cells) + " |")


def main() -> None:
    """Simulate every scenario and print all trap evidence tables."""
    variables = list(MEASURED_VARIABLES)
    running_frames = []
    hazard_correlations = {}
    print("## Timings and counts\n")
    print("| Scenario | Simulation time (s) | Failures | Tool changes | Events |")
    print("|---|---|---|---|---|")
    for scenario_name in SCENARIOS:
        start_time = time.perf_counter()
        result = run_scenario(scenario_name, DEFAULT_N_STEPS)
        elapsed_seconds = time.perf_counter() - start_time
        sensors = result.sensors.assign(true_hazard=result.ground_truth["true_hazard"])
        sensors["failure_soon"] = failure_within_horizon(sensors["failure"], PREDICTION_HORIZON)
        running = sensors[(sensors["machine_running"] == 1) & sensors["failure_soon"].notna()]
        running_frames.append(running)
        hazard_correlations[scenario_name] = running[variables].corrwith(running["true_hazard"])
        tool_changes = int((result.events["event_type"] == "tool_change").sum())
        print(
            f"| {scenario_name} | {elapsed_seconds:.2f} | {int(sensors['failure'].sum())} "
            f"| {tool_changes} | {len(result.events)} |"
        )

    pooled = pd.concat(running_frames, ignore_index=True)
    correlation_table = pd.DataFrame(hazard_correlations)
    correlation_table["pooled corr(failure)"] = pooled[variables].corrwith(pooled["failure"])
    correlation_table.insert(0, "Causal role", [causal_role(variable) for variable in correlation_table.index])
    print("\n## Correlation with true hazard (per scenario) and with failure (pooled)\n")
    print_markdown_table(correlation_table, "Variable")

    target = pooled["failure_soon"].astype(int)
    auc_rows = {}
    for variable in variables:
        auc = roc_auc_score(target, pooled[variable])
        auc_rows[variable] = {"Causal role": causal_role(variable), "AUC": max(auc, 1.0 - auc)}
    auc_table = pd.DataFrame(auc_rows).T.sort_values("AUC", ascending=False)
    print(f"\n## Single-variable AUC for failure within {PREDICTION_HORIZON} steps (pooled, sign-free)\n")
    print_markdown_table(auc_table, "Variable")

    start_time = time.perf_counter()
    classifier = xgboost.XGBClassifier(
        n_estimators=150, max_depth=3, learning_rate=0.1, random_state=XGBOOST_SEED, n_jobs=4
    )
    classifier.fit(pooled[variables], target)
    explainer = shap.TreeExplainer(classifier)
    sample = pooled[variables].sample(SHAP_SAMPLE_SIZE, random_state=XGBOOST_SEED)
    global_importance = pd.Series(np.abs(explainer.shap_values(sample)).mean(axis=0), index=variables)
    high_risk = pooled[pooled["true_hazard"] > pooled["true_hazard"].quantile(HIGH_RISK_QUANTILE)]
    high_risk_sample = high_risk[variables].sample(
        min(HIGH_RISK_SAMPLE_SIZE, len(high_risk)), random_state=XGBOOST_SEED
    )
    top_feature = np.array(variables)[np.abs(explainer.shap_values(high_risk_sample)).argmax(axis=1)]
    top_share = pd.Series(top_feature).value_counts(normalize=True)
    elapsed_seconds = time.perf_counter() - start_time

    shap_table = pd.DataFrame(
        {
            "Causal role": [causal_role(variable) for variable in variables],
            "mean abs SHAP": global_importance.to_numpy(),
            "top feature share at high-risk states": [float(top_share.get(variable, 0.0)) for variable in variables],
        },
        index=variables,
    ).sort_values("mean abs SHAP", ascending=False)
    print(
        f"\n## Naive XGBoost + SHAP, failure within {PREDICTION_HORIZON} steps "
        f"(trained and explained in {elapsed_seconds:.1f} s)\n"
    )
    print_markdown_table(shap_table, "Variable")


if __name__ == "__main__":
    main()
