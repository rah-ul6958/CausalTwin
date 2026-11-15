"""Standard-ML baseline for the Correlation Trap: XGBoost + SHAP, then "fix the top feature".

The model predicts "failure within the next 30 steps" from the current sensor values. It
is a competent model (accuracy metrics are reported); its weakness is not accuracy but
that SHAP explains the model's prediction, not the machine's causes.

Recommendation rule ("reduce the top SHAP feature by the same magnitude CausalTwin
recommends"): the blamed feature is the one with the largest positive SHAP value (it
pushes predicted risk up the most). The engineer's action depends on that feature:

| Blamed feature | Action an engineer would take (this mapping is a modelling choice) |
|---|---|
| a control lever | move it by the magnitude, in whichever direction the ML model says lowers risk |
| vibration | "damp vibration": remove the vibration signal's noise component (damping fixture) |
| bearing_temp, coolant_temp | raise coolant_flow by the magnitude |
| motor_current | lower load_pct by the magnitude |
| tool_wear | change the tool |
| ambient_temp (not controllable) | no action |
"""

import time
from typing import Any

import numpy as np
import pandas as pd
import shap
import xgboost
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

from causaltwin.config import (
    BASELINE_HORIZON,
    BASELINE_LEARNING_RATE,
    BASELINE_MAX_DEPTH,
    BASELINE_N_ESTIMATORS,
    BASELINE_SEED,
    BASELINE_TEST_FRACTION,
    DEFAULT_CONFIG,
    LEVER_COSTS,
    LEVER_NAMES,
    MEASURED_VARIABLES,
    TOOL_CHANGE_COST,
    VIBRATION_DAMPING_COST,
)
from causaltwin.engine.correlation import failure_soon
from causaltwin.intelligence.schemas import BaselineAction
from causaltwin.utils.formatting import format_value
from causaltwin.utils.timing import timed_step

SENSOR_ACTION_MAP: dict[str, tuple[str, str | None, float]] = {
    # feature: (kind, lever, direction of the lever change)
    "vibration": ("damp_vibration", None, 0.0),
    "bearing_temp": ("lever", "coolant_flow", +1.0),
    "coolant_temp": ("lever", "coolant_flow", +1.0),
    "motor_current": ("lever", "load_pct", -1.0),
    "tool_wear": ("tool_change", None, 0.0),
    "ambient_temp": ("none", None, 0.0),
}


def _classifier() -> xgboost.XGBClassifier:
    """Shallow gradient-boosted trees."""
    return xgboost.XGBClassifier(
        n_estimators=BASELINE_N_ESTIMATORS,
        max_depth=BASELINE_MAX_DEPTH,
        learning_rate=BASELINE_LEARNING_RATE,
        random_state=BASELINE_SEED,
        n_jobs=4,
        eval_metric="logloss",
    )


class BaselineModel:
    """XGBoost failure predictor with SHAP explanations and the engineer's action mapping."""

    def __init__(self) -> None:
        """Create an untrained baseline."""
        self.features = list(MEASURED_VARIABLES)
        self.classifier: xgboost.XGBClassifier | None = None
        self.metrics: dict[str, Any] = {}
        self._explainer: shap.TreeExplainer | None = None

    def __getstate__(self) -> dict[str, Any]:
        """Pickle support: the SHAP explainer is rebuilt on demand."""
        state = dict(self.__dict__)
        state["_explainer"] = None
        return state

    def fit(self, data: pd.DataFrame, horizon: int = BASELINE_HORIZON) -> "BaselineModel":
        """Train on running rows; accuracy is measured on the chronological final share."""
        with timed_step("baseline_fit", "Trained XGBoost baseline") as timer:
            start_time = time.perf_counter()
            target = failure_soon(data["failure"].to_numpy(dtype=float), horizon)
            running = data["machine_running"].to_numpy() == 1
            usable = running.copy()
            usable[-horizon:] = False
            features = data.loc[usable, self.features].to_numpy(dtype=float)
            labels = target[usable].astype(int)
            split = int(len(labels) * (1.0 - BASELINE_TEST_FRACTION))
            evaluation_model = _classifier().fit(features[:split], labels[:split])
            train_probability = evaluation_model.predict_proba(features[:split])[:, 1]
            threshold = _best_f1_threshold(labels[:split], train_probability)
            test_probability = evaluation_model.predict_proba(features[split:])[:, 1]
            predictions = (test_probability >= threshold).astype(int)
            self.classifier = _classifier().fit(features, labels)
            self.metrics = {
                "target": f"failure within the next {horizon} steps",
                "n_train": split,
                "n_test": len(labels) - split,
                "positive_rate_test": float(labels[split:].mean()),
                "auc": float(roc_auc_score(labels[split:], test_probability)),
                "threshold": threshold,
                "precision": float(precision_score(labels[split:], predictions, zero_division=0)),
                "recall": float(recall_score(labels[split:], predictions, zero_division=0)),
                "f1": float(f1_score(labels[split:], predictions, zero_division=0)),
                "training_seconds": time.perf_counter() - start_time,
            }
            timer.details.update(
                {key: round(value, 3) for key, value in self.metrics.items() if isinstance(value, float)}
            )
        return self

    def _require_classifier(self) -> xgboost.XGBClassifier:
        """The trained classifier or a clear error."""
        if self.classifier is None:
            raise RuntimeError("The baseline is not trained; call fit(data) first.")
        return self.classifier

    def predict_risk(self, row: pd.Series) -> float:
        """The baseline's own probability of failure within the horizon."""
        return float(self._require_classifier().predict_proba(row[self.features].to_numpy(float)[None, :])[0, 1])

    def shap_values(self, row: pd.Series) -> dict[str, float]:
        """SHAP values (log-odds contributions) of every feature for one state."""
        if self._explainer is None:
            self._explainer = shap.TreeExplainer(self._require_classifier())
        values = np.asarray(self._explainer.shap_values(row[self.features].to_numpy(float)[None, :]))[0]
        return {name: float(value) for name, value in zip(self.features, values, strict=True)}

    def recommend(self, row: pd.Series, magnitude_pct: float) -> BaselineAction:
        """Blame the top SHAP feature and map it to the engineer's action."""
        contributions = self.shap_values(row)
        blamed, blamed_value = max(contributions.items(), key=lambda item: item[1])
        risk = self.predict_risk(row)
        if blamed_value <= 0:
            return BaselineAction(
                blamed_feature="none",
                blamed_shap=blamed_value,
                kind="none",
                cost=0.0,
                description="No feature pushes predicted risk up; no action.",
                shap_values=contributions,
                predicted_risk=risk,
            )
        if blamed in LEVER_NAMES:
            down = self._lever_action(row, blamed, -magnitude_pct)
            up = self._lever_action(row, blamed, +magnitude_pct)
            action = min((down, up), key=lambda option: option[1])[0]
        else:
            kind, lever, direction = SENSOR_ACTION_MAP[blamed]
            if kind == "lever" and lever is not None:
                action = self._lever_action(row, lever, direction * magnitude_pct)[0]
            elif kind == "damp_vibration":
                action = {
                    "kind": "damp_vibration",
                    "cost": VIBRATION_DAMPING_COST,
                    "description": "Damp vibration (remove the vibration signal's noise component).",
                }
            elif kind == "tool_change":
                action = {"kind": "tool_change", "cost": TOOL_CHANGE_COST, "description": "Change the tool."}
            else:
                action = {"kind": "none", "cost": 0.0, "description": f"{blamed} cannot be controlled; no action."}
        return BaselineAction(
            blamed_feature=blamed, blamed_shap=blamed_value, shap_values=contributions, predicted_risk=risk, **action
        )

    def _lever_action(self, row: pd.Series, lever: str, percent: float) -> tuple[dict[str, Any], float]:
        """A lever change and the baseline model's predicted risk after it."""
        settings = DEFAULT_CONFIG.levers[lever]
        current = float(row[lever])
        target = float(np.clip(current * (1.0 + percent / 100.0), settings.minimum, settings.maximum))
        changed = row.copy()
        changed[lever] = target
        actual_pct = 100.0 * (target - current) / current if current else 0.0
        action = {
            "kind": "lever",
            "lever": lever,
            "from_value": current,
            "to_value": target,
            "change_pct": actual_pct,
            "cost": LEVER_COSTS[lever].cost_per_pct * abs(actual_pct),
            "description": (
                f"Change {lever} from {format_value(current)} to {format_value(target)} ({actual_pct:+.0f}%)."
            ),
        }
        return action, self.predict_risk(changed)


def _best_f1_threshold(labels: np.ndarray, probabilities: np.ndarray) -> float:
    """Decision threshold with the best F1 on the training data (fair to the baseline).

    The F1 score of every candidate threshold is computed at once from the sorted
    probabilities: the same candidates and the same F1 = 2TP / (2TP + FP + FN) as calling
    scikit-learn's f1_score per threshold, but fast on large data.
    """
    candidates = np.unique(np.quantile(probabilities, np.linspace(0.5, 0.999, 200)))
    order = np.argsort(probabilities, kind="stable")
    sorted_probabilities = probabilities[order]
    positives_below = np.concatenate([[0], np.cumsum(labels[order])])  # positives among the k lowest
    total_positives = int(positives_below[-1])
    first_predicted = np.searchsorted(sorted_probabilities, candidates, side="left")  # first p >= threshold
    predicted_positive = probabilities.size - first_predicted
    true_positive = total_positives - positives_below[first_predicted]
    false_positive = predicted_positive - true_positive
    false_negative = total_positives - true_positive
    denominator = 2 * true_positive + false_positive + false_negative
    scores = np.divide(2.0 * true_positive, denominator, out=np.zeros(candidates.size), where=denominator > 0)
    return float(candidates[int(np.argmax(scores))])
