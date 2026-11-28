"""AI4I 2020 check: CausalTwin's tabular mode on a third-party dataset with documented failure rules.

Dataset: S. Matzka, AI4I 2020 Predictive Maintenance Dataset, UCI Machine Learning Repository,
CC BY 4.0, DOI 10.24432/C5HS5C (`data/external/ai4i2020.csv`). It is synthetic, modelled on a real
milling machine, and its documentation states exactly when each failure mode occurs. Those rules
reproduce the dataset's labels and serve as ground truth here:

- heat dissipation (HDF): air-to-process temperature difference below 8.6 K and speed below 1,380 rpm;
- power (PWF): mechanical power (torque x speed in rad/s) below 3,500 W or above 9,000 W;
- overstrain (OSF): tool wear x torque above 11,000 / 12,000 / 13,000 min*Nm for product types L / M / H;
- tool wear (TWF): the tool fails at a random wear between about 200 and 240 minutes; only a tool
  change removes it;
- random (RNF): 0.1% per process, which no action can prevent (such rows are not scored).

What is compared, for every failed row with a preventable failure mode:

- **CausalTwin.** The data goes through the Bring Your Own Data path in tabular mode, with the
  physics features the documentation describes added as columns. Causal discovery and the
  failure model come from the engine. Candidate changes to speed and torque (within the observed
  range) and a tool change are scored with the engine's failure model, after recomputing the
  physics features from their formulas. The cheapest candidate predicted below the target wins.
- **Standard ML.** XGBoost on the raw columns; the top SHAP feature of the row is blamed and
  mapped to an engineer's action (speed or torque changed in whichever direction the model
  prefers, tool wear becomes a tool change, temperatures and product type cannot be acted on).

The documented rules then decide whether each changed row would still fail.
"""

import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import shap
import xgboost
from sklearn.metrics import roc_auc_score

from causaltwin.config import (
    AI4I_CHANGE_STEPS_PCT,
    AI4I_COST_PER_PCT,
    AI4I_DATA_PATH,
    AI4I_HDF_SPEED_RPM,
    AI4I_HDF_TEMPERATURE_DIFFERENCE_K,
    AI4I_OSF_LIMITS,
    AI4I_PWF_POWER_RANGE_W,
    AI4I_TARGET_PROBABILITY,
    AI4I_TOOL_CHANGE_COST,
    AI4I_TWF_MIN_WEAR,
    BASELINE_DEFAULT_MAGNITUDE_PCT,
    BASELINE_LEARNING_RATE,
    BASELINE_MAX_DEPTH,
    BASELINE_N_ESTIMATORS,
    BASELINE_SEED,
    BASELINE_TEST_FRACTION,
)
from causaltwin.engine.twin import CausalTwinEngine, EngineSettings
from causaltwin.utils.timing import timed_step

TYPE_CODES = {"L": 0.0, "M": 1.0, "H": 2.0}
RAW_FEATURES = ["type_code", "air_temp_k", "process_temp_k", "speed_rpm", "torque_nm", "tool_wear_min"]
LEVERS = ("speed_rpm", "torque_nm")
ROLES = {
    "type_code": "environment",
    "air_temp_k": "environment",
    "speed_rpm": "control",
    "torque_nm": "control",
    "tool_wear_min": "control",
    "process_temp_k": "sensor",
    "temperature_difference_k": "sensor",
    "power_w": "sensor",
    "wear_torque": "sensor",
    "failure": "outcome",
}
MODES = ("HDF", "PWF", "OSF", "TWF")


@dataclass
class Action:
    """A change to one row: lever multipliers and an optional tool change."""

    speed_factor: float = 1.0
    torque_factor: float = 1.0
    tool_change: bool = False

    def describe(self) -> str:
        """Readable description, for example "speed -20%, tool change"."""
        parts = []
        if self.speed_factor != 1.0:
            parts.append(f"speed {100 * (self.speed_factor - 1):+.0f}%")
        if self.torque_factor != 1.0:
            parts.append(f"torque {100 * (self.torque_factor - 1):+.0f}%")
        if self.tool_change:
            parts.append("tool change")
        return ", ".join(parts) or "no action"

    def cost(self) -> float:
        """Illustrative cost: percent changed times the cost per percent, plus a tool change."""
        change = abs(self.speed_factor - 1) + abs(self.torque_factor - 1)
        return 100 * change * AI4I_COST_PER_PCT + (AI4I_TOOL_CHANGE_COST if self.tool_change else 0.0)


# ---------------------------------------------------------------------------
# Data and the documented rules
# ---------------------------------------------------------------------------
def load_ai4i(path: Path = AI4I_DATA_PATH) -> pd.DataFrame:
    """Read the CSV with short column names, a numeric product type and the failure-mode flags."""
    raw = pd.read_csv(path, encoding="utf-8-sig")
    data = pd.DataFrame(
        {
            "type": raw["Type"],
            "type_code": raw["Type"].map(TYPE_CODES).astype(float),
            "air_temp_k": raw["Air temperature [K]"].astype(float),
            "process_temp_k": raw["Process temperature [K]"].astype(float),
            "speed_rpm": raw["Rotational speed [rpm]"].astype(float),
            "torque_nm": raw["Torque [Nm]"].astype(float),
            "tool_wear_min": raw["Tool wear [min]"].astype(float),
            "failure": raw["Machine failure"].astype(int),
        }
    )
    for mode in (*MODES, "RNF"):
        data[mode] = raw[mode].astype(int)
    return add_physics_features(data)


def add_physics_features(data: pd.DataFrame) -> pd.DataFrame:
    """The physics features named in the dataset documentation, computed from their formulas."""
    data = data.copy()
    data["temperature_difference_k"] = data["process_temp_k"] - data["air_temp_k"]
    data["power_w"] = data["torque_nm"] * data["speed_rpm"] * 2 * np.pi / 60
    data["wear_torque"] = data["tool_wear_min"] * data["torque_nm"]
    return data


def rule_failures(row: pd.Series, twf_present: bool) -> list[str]:
    """Failure modes the documented rules predict for a (possibly changed) row.

    TWF cannot be predicted from one row (the wear at which a tool fails is random); it stays
    if the original row had it and the action did not change the tool.
    """
    modes = []
    if row["temperature_difference_k"] < AI4I_HDF_TEMPERATURE_DIFFERENCE_K and row["speed_rpm"] < AI4I_HDF_SPEED_RPM:
        modes.append("HDF")
    low, high = AI4I_PWF_POWER_RANGE_W
    if not low <= row["power_w"] <= high:
        modes.append("PWF")
    if row["wear_torque"] > AI4I_OSF_LIMITS[str(row["type"])]:
        modes.append("OSF")
    if twf_present and row["tool_wear_min"] >= AI4I_TWF_MIN_WEAR:
        modes.append("TWF")
    return modes


def apply_action(row: pd.Series, action: Action) -> pd.Series:
    """The row after the action, with the physics features recomputed."""
    changed = row.copy()
    changed["speed_rpm"] = row["speed_rpm"] * action.speed_factor
    changed["torque_nm"] = row["torque_nm"] * action.torque_factor
    if action.tool_change:
        changed["tool_wear_min"] = 0.0
    changed["temperature_difference_k"] = changed["process_temp_k"] - changed["air_temp_k"]
    changed["power_w"] = changed["torque_nm"] * changed["speed_rpm"] * 2 * np.pi / 60
    changed["wear_torque"] = changed["tool_wear_min"] * changed["torque_nm"]
    return changed


# ---------------------------------------------------------------------------
# CausalTwin
# ---------------------------------------------------------------------------
def fit_causaltwin(data: pd.DataFrame) -> CausalTwinEngine:
    """Discovery and fitting on all rows through the tabular (Bring Your Own Data) path."""
    engine = CausalTwinEngine(
        EngineSettings(discovery_backend="numpy", window_steps=len(data), failure_history_steps=len(data))
    )
    return engine.fit(data[list(ROLES)], ROLES, time_structure=False)


def failure_probability(engine: CausalTwinEngine, row: pd.Series) -> float:
    """The engine's fitted failure model evaluated on one row (tabular mode: same-row causes)."""
    failure = engine._require_fitted().failure
    if failure is None:
        return float("nan")
    causes = [np.array([float(row[name])]) for name, _ in failure.terms]
    logit = float(failure.logit(causes, failure.point_parameters()[None, :])[0])
    return float(1.0 / (1.0 + np.exp(-np.clip(logit, -50, 50))))


def candidate_actions() -> list[Action]:
    """Single-lever and two-lever changes, each with and without a tool change."""
    factors = [1 + step / 100 for step in AI4I_CHANGE_STEPS_PCT]
    actions = [Action()]
    actions += [Action(speed_factor=factor) for factor in factors]
    actions += [Action(torque_factor=factor) for factor in factors]
    actions += [Action(speed_factor=s, torque_factor=t) for s in factors for t in factors]
    return actions + [Action(a.speed_factor, a.torque_factor, tool_change=True) for a in actions]


def _candidate_columns(row: pd.Series, actions: list[Action]) -> dict[str, np.ndarray]:
    """Every candidate's changed row as columns (the same formulas as `apply_action`, vectorized)."""
    speed = row["speed_rpm"] * np.array([action.speed_factor for action in actions])
    torque = row["torque_nm"] * np.array([action.torque_factor for action in actions])
    wear = np.where([action.tool_change for action in actions], 0.0, row["tool_wear_min"])
    columns = {name: np.full(len(actions), float(row[name])) for name in RAW_FEATURES}
    columns.update({"speed_rpm": speed, "torque_nm": torque, "tool_wear_min": wear})
    columns["temperature_difference_k"] = np.full(len(actions), float(row["process_temp_k"] - row["air_temp_k"]))
    columns["power_w"] = torque * speed * 2 * np.pi / 60
    columns["wear_torque"] = wear * torque
    return columns


def causaltwin_action(
    engine: CausalTwinEngine, row: pd.Series, actions: list[Action], ranges: dict[str, tuple[float, float]]
) -> tuple[Action, float]:
    """Cheapest action predicted below the target; otherwise the action with the lowest prediction.

    Candidates that would move a lever outside the range seen in the data are skipped.
    """
    failure = engine._require_fitted().failure
    if failure is None:
        return Action(), float("nan")
    columns = _candidate_columns(row, actions)
    parameters = np.tile(failure.point_parameters(), (len(actions), 1))
    logit = failure.logit([columns[name] for name, _ in failure.terms], parameters)  # linear or spline
    probability = 1.0 / (1.0 + np.exp(-np.clip(logit, -50, 50)))
    inside = np.ones(len(actions), dtype=bool)
    for lever in LEVERS:
        inside &= (columns[lever] >= ranges[lever][0]) & (columns[lever] <= ranges[lever][1])
    costs = np.array([action.cost() for action in actions])
    meeting = np.flatnonzero(inside & (probability <= AI4I_TARGET_PROBABILITY))
    if meeting.size:
        chosen = meeting[np.lexsort((probability[meeting], costs[meeting]))[0]]
    else:
        candidates = np.flatnonzero(inside)
        chosen = candidates[np.lexsort((costs[candidates], probability[candidates]))[0]]
    return actions[int(chosen)], float(probability[chosen])


# ---------------------------------------------------------------------------
# Standard ML baseline
# ---------------------------------------------------------------------------
class Ai4iBaseline:
    """XGBoost on the raw columns plus SHAP: blame the top feature, act on it."""

    def __init__(self, data: pd.DataFrame) -> None:
        """Train with the main baseline's settings; accuracy on a chronological holdout."""
        features, labels = data[RAW_FEATURES].to_numpy(float), data["failure"].to_numpy(int)
        split = int(len(labels) * (1 - BASELINE_TEST_FRACTION))
        evaluation = self._classifier().fit(features[:split], labels[:split])
        self.auc = float(roc_auc_score(labels[split:], evaluation.predict_proba(features[split:])[:, 1]))
        self.model = self._classifier().fit(features, labels)
        self.explainer = shap.TreeExplainer(self.model)

    @staticmethod
    def _classifier() -> xgboost.XGBClassifier:
        """The same tree settings as the main benchmark's baseline."""
        return xgboost.XGBClassifier(
            n_estimators=BASELINE_N_ESTIMATORS,
            max_depth=BASELINE_MAX_DEPTH,
            learning_rate=BASELINE_LEARNING_RATE,
            random_state=BASELINE_SEED,
            n_jobs=4,
            eval_metric="logloss",
        )

    def risk(self, row: pd.Series) -> float:
        """The model's own failure probability for one row."""
        return float(self.model.predict_proba(row[RAW_FEATURES].to_numpy(float)[None, :])[0, 1])

    def action(self, row: pd.Series, magnitude_pct: float) -> tuple[str, Action]:
        """Blame the feature with the largest positive SHAP value and map it to an action."""
        contributions = self.explainer.shap_values(row[RAW_FEATURES].to_numpy(float)[None, :])[0]
        blamed = RAW_FEATURES[int(np.argmax(contributions))]
        factor = magnitude_pct / 100
        if blamed == "speed_rpm":
            options = [Action(speed_factor=1 + factor), Action(speed_factor=1 - factor)]
            return blamed, min(options, key=lambda action: self.risk(apply_action(row, action)))
        if blamed == "torque_nm":
            options = [Action(torque_factor=1 + factor), Action(torque_factor=1 - factor)]
            return blamed, min(options, key=lambda action: self.risk(apply_action(row, action)))
        if blamed == "tool_wear_min":
            return blamed, Action(tool_change=True)
        return blamed, Action()  # temperatures and product type cannot be changed by the operator


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def _scored_rows(data: pd.DataFrame) -> pd.DataFrame:
    """Failed rows with at least one preventable failure mode (random failures are not scored)."""
    preventable = data[list(MODES)].sum(axis=1) > 0
    return data[(data["failure"] == 1) & preventable]


def _mode_summary(cases: list[dict[str, Any]], method: str) -> dict[str, Any]:
    """Success rate overall and per original failure mode."""
    summary: dict[str, Any] = {"success_rate": float(np.mean([case[method]["success"] for case in cases]))}
    for mode in MODES:
        with_mode = [case for case in cases if mode in case["modes"]]
        summary[mode] = {
            "n": len(with_mode),
            "success_rate": float(np.mean([case[method]["success"] for case in with_mode])) if with_mode else None,
        }
    summary["mean_cost"] = float(np.mean([case[method]["cost"] for case in cases]))
    return summary


def _failure_card(engine: CausalTwinEngine) -> dict[str, Any] | None:
    """The failure model's card (equation, held-out AUC, notes) as a plain dict."""
    card = next((card for card in engine.get_model_cards() if card.kind == "logistic"), None)
    return None if card is None else card.model_dump()


def run_ai4i_check(path: Path = AI4I_DATA_PATH) -> dict[str, Any]:
    """Fit both methods, act on every scored failed row, judge with the documented rules."""
    with timed_step("ai4i_check", "AI4I 2020 check (tabular mode, documented failure rules)") as timer:
        start = time.perf_counter()
        data = load_ai4i(path)
        engine = fit_causaltwin(data)
        # The same discovery and causes, with the non-linear failure model (Phase 7, task B1).
        spline_engines = {
            "causaltwin_spline": engine.with_failure_model("spline"),
            "causaltwin_spline_products": engine.with_failure_model("spline", interactions=True),
        }
        baseline = Ai4iBaseline(data)
        ranges = {lever: (float(data[lever].min()), float(data[lever].max())) for lever in LEVERS}
        actions = candidate_actions()
        cases: list[dict[str, Any]] = []
        for _, row in _scored_rows(data).iterrows():
            twf = bool(row["TWF"])
            modes = [mode for mode in MODES if row[mode]]
            ct_action, ct_probability = causaltwin_action(engine, row, actions, ranges)
            magnitude = 100 * max(abs(ct_action.speed_factor - 1), abs(ct_action.torque_factor - 1))
            blamed, bl_action = baseline.action(row, magnitude or BASELINE_DEFAULT_MAGNITUDE_PCT)
            ct_after = rule_failures(apply_action(row, ct_action), twf)
            bl_after = rule_failures(apply_action(row, bl_action), twf)
            spline_results = {}
            for name, spline_engine in spline_engines.items():
                spline_action, spline_probability = causaltwin_action(spline_engine, row, actions, ranges)
                spline_after = rule_failures(apply_action(row, spline_action), twf)
                spline_results[name] = {
                    "action": spline_action.describe(),
                    "cost": spline_action.cost(),
                    "predicted_probability": spline_probability,
                    "failures_after": spline_after,
                    "success": not spline_after,
                }
            cases.append(
                {
                    "row": int(row.name) + 1,
                    "modes": modes,
                    **spline_results,
                    "causaltwin": {
                        "action": ct_action.describe(),
                        "cost": ct_action.cost(),
                        "predicted_probability": ct_probability,
                        "failures_after": ct_after,
                        "success": not ct_after,
                    },
                    "baseline": {
                        "action": bl_action.describe(),
                        "blamed": blamed,
                        "cost": bl_action.cost(),
                        "failures_after": bl_after,
                        "success": not bl_after,
                    },
                }
            )
        graph = engine.get_graph()
        failure_card = next((card for card in engine.get_model_cards() if card.kind == "logistic"), None)
        seconds = time.perf_counter() - start
        results: dict[str, Any] = {
            "source": "AI4I 2020 Predictive Maintenance Dataset (UCI, CC BY 4.0, DOI 10.24432/C5HS5C); "
            "synthetic data modelled on a real milling machine. Judged by the dataset's documented failure rules.",
            "settings": {
                "target_probability": AI4I_TARGET_PROBABILITY,
                "change_steps_pct": list(AI4I_CHANGE_STEPS_PCT),
                "tool_change_cost": AI4I_TOOL_CHANGE_COST,
                "cost_per_pct": AI4I_COST_PER_PCT,
            },
            "n_rows": len(data),
            "n_failures": int(data["failure"].sum()),
            "n_scored": len(cases),
            "n_random_only": int(((data["failure"] == 1) & (data[list(MODES)].sum(axis=1) == 0)).sum()),
            "discovered_failure_causes": sorted({edge.cause for edge in graph.edges if edge.effect == "failure"}),
            "discovered_edges": [
                {"cause": edge.cause, "effect": edge.effect, "confidence": edge.confidence, "oriented": edge.oriented}
                for edge in graph.edges
            ],
            "failure_model": None if failure_card is None else failure_card.model_dump(),
            "spline_failure_models": {
                name: _failure_card(spline_engine) for name, spline_engine in spline_engines.items()
            },
            "baseline_auc": baseline.auc,
            "baseline_blamed": dict(Counter(case["baseline"]["blamed"] for case in cases).most_common()),
            "summary": {
                "causaltwin": _mode_summary(cases, "causaltwin"),
                **{name: _mode_summary(cases, name) for name in spline_engines},
                "baseline": _mode_summary(cases, "baseline"),
            },
            "cases": cases,
            "seconds": seconds,
        }
        timer.details.update({"scored": len(cases), "seconds": round(seconds, 1)})
    return results
