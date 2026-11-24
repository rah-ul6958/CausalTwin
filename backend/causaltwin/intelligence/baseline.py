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

Three baselines are compared with CausalTwin in the benchmark, all using the same trained model:

- B1, naive SHAP (`recommend`): the rule above. Kept so nothing is hidden, but it is easy to call a
  straw man: it often blames vibration (a symptom) or ambient temperature (not controllable).
- B2, actionable SHAP (`recommend_actionable`): the top SHAP feature among the things an engineer can
  act on (the four levers, or tool wear, acted on with a tool change), with the same lever rule.
- B3, predict-then-optimize (`recommend_counterfactual`): the main comparison. It searches exactly the
  candidate actions CausalTwin's optimizer searches (`build_candidates`: same grid, same costs), scores
  each by the XGBoost model's predicted risk, and picks the cheapest one predicted to reach the same
  target with the same planning margin.

Why B3 is the strongest fair predictive baseline: it removes every handicap that is not about
causality. It does not depend on SHAP or on a hand-made feature-to-action mapping, it never blames a
symptom or an uncontrollable variable, it may move any lever in either direction, pair levers or change
the tool, and it optimizes cost against the same target. The one thing it cannot do is know what a
change causes: a candidate changes only that lever's value in the feature row (a tool change sets
tool_wear to 0), while bearing temperature, vibration and the other sensors keep their current values.
The model therefore answers "how risky is a machine that looks like this?", not "what happens if I make
this change?". Wherever lowering risk works through a mediator (more coolant lowers coolant and bearing
temperature, which lowers risk), the model holds the mediator fixed and sees little or no benefit.
"""

import time
from typing import Any, Literal

import numpy as np
import pandas as pd
import shap
import xgboost
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

from causaltwin.config import (
    BASELINE_ACTIONABLE_FEATURES,
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
    OPTIMIZER_PLANNING_MARGIN,
    OPTIMIZER_TARGET_RISK,
    TOOL_CHANGE_COST,
    VIBRATION_DAMPING_COST,
)
from causaltwin.engine.correlation import failure_soon
from causaltwin.intelligence.optimizer import TOOL_STATE, _ranking_key, build_candidates
from causaltwin.intelligence.schemas import BaselineAction, LeverChange
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

    def recommend(
        self,
        row: pd.Series,
        magnitude_pct: float,
        contributions: dict[str, float] | None = None,
        target_risk: float = OPTIMIZER_TARGET_RISK,
        low_risk_rule: bool = True,
    ) -> BaselineAction:
        """B1, naive SHAP: blame the top SHAP feature and map it to the engineer's action.

        `contributions` may pass SHAP values already computed for this row (they are the same for B1 and B2).
        With `low_risk_rule`, no action when the model's own predicted risk is at or below `target_risk`.
        """
        contributions = contributions if contributions is not None else self.shap_values(row)
        return self._blame_and_act(
            row, contributions, contributions, magnitude_pct, "naive_shap", target_risk, low_risk_rule
        )

    def recommend_actionable(
        self,
        row: pd.Series,
        magnitude_pct: float,
        contributions: dict[str, float] | None = None,
        target_risk: float = OPTIMIZER_TARGET_RISK,
        low_risk_rule: bool = True,
    ) -> BaselineAction:
        """B2, actionable SHAP: blame the top SHAP feature among the levers and tool wear, then act on it."""
        contributions = contributions if contributions is not None else self.shap_values(row)
        actionable = {name: contributions[name] for name in BASELINE_ACTIONABLE_FEATURES if name in contributions}
        return self._blame_and_act(
            row, actionable, contributions, magnitude_pct, "actionable_shap", target_risk, low_risk_rule
        )

    def _blame_and_act(
        self,
        row: pd.Series,
        eligible: dict[str, float],
        contributions: dict[str, float],
        magnitude_pct: float,
        method: Literal["naive_shap", "actionable_shap"],
        target_risk: float = OPTIMIZER_TARGET_RISK,
        low_risk_rule: bool = True,
    ) -> BaselineAction:
        """Blame the eligible feature with the largest positive SHAP value and map it to an action.

        When the model's own predicted risk is already at or below the target, there is nothing to fix:
        no action (the same rule as B3 and as CausalTwin's optimizer).
        """
        blamed, blamed_value = max(eligible.items(), key=lambda item: item[1])
        risk = self.predict_risk(row)
        if low_risk_rule and risk <= target_risk:
            return BaselineAction(
                method=method,
                blamed_feature="none",
                blamed_shap=None,
                kind="none",
                cost=0.0,
                description=low_risk_description(risk, target_risk),
                shap_values=contributions,
                predicted_risk=risk,
                predicted_risk_after=risk,
            )
        if blamed_value <= 0:
            return BaselineAction(
                method=method,
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
                action = {
                    "kind": "tool_change",
                    "cost": TOOL_CHANGE_COST,
                    "description": "Change the tool.",
                    "predicted_risk_after": self._predicted_risk_with(row, {}, tool_change=True),
                }
            else:
                action = {"kind": "none", "cost": 0.0, "description": f"{blamed} cannot be controlled; no action."}
        return BaselineAction(
            method=method,
            blamed_feature=blamed,
            blamed_shap=blamed_value,
            shap_values=contributions,
            predicted_risk=risk,
            **action,
        )

    def recommend_counterfactual(
        self,
        row: pd.Series,
        target_risk: float = OPTIMIZER_TARGET_RISK,
        contributions: dict[str, float] | None = None,
    ) -> BaselineAction:
        """B3, predict-then-optimize: CausalTwin's candidate actions, scored by the XGBoost model.

        Same search space and costs as the optimizer (`build_candidates`), same target and planning
        margin, same ranking rule (cheapest option predicted below target * margin). Each candidate
        changes only the lever values in the feature row (a tool change sets tool_wear to 0); every
        other feature keeps its current value. No action is chosen when the predicted risk is already
        at or below the target, or when no candidate lowers the predicted risk.
        """
        classifier = self._require_classifier()
        current = {lever: float(row[lever]) for lever in LEVER_NAMES}
        candidates = build_candidates(current, list(LEVER_NAMES))
        feature_index = {name: position for position, name in enumerate(self.features)}
        current_features = row[self.features].to_numpy(dtype=float)
        candidate_features = np.repeat(current_features[None, :], len(candidates), axis=0)
        for position, candidate in enumerate(candidates):
            for change in candidate.changes:
                candidate_features[position, feature_index[change.lever]] = change.to_value
            if candidate.tool_change:
                candidate_features[position, feature_index[TOOL_STATE]] = 0.0
        predicted_risks = classifier.predict_proba(candidate_features)[:, 1]
        risk_before = float(predicted_risks[0])  # candidate 0 is "no action"
        planning_target = target_risk * OPTIMIZER_PLANNING_MARGIN
        chosen = min(
            range(1, len(candidates)),
            key=lambda i: _ranking_key(float(predicted_risks[i]), candidates[i].cost, planning_target),
        )
        risk_after = float(predicted_risks[chosen])
        shap_values = contributions if contributions is not None else {}
        if risk_before <= target_risk or risk_after >= risk_before:
            description = (
                low_risk_description(risk_before, target_risk)
                if risk_before <= target_risk
                else f"No action: no evaluated change lowers XGBoost's predicted risk ({risk_before:.1%})."
            )
            return BaselineAction(
                method="counterfactual",
                blamed_shap=None,
                shap_values=shap_values,
                blamed_feature="none",
                kind="none",
                cost=0.0,
                description=description,
                predicted_risk=risk_before,
                predicted_risk_after=risk_before,
            )
        candidate = candidates[chosen]
        parts = [
            f"{change.lever} from {format_value(change.from_value)} to {format_value(change.to_value)} "
            f"({change.change_pct:+.0f}%)"
            for change in candidate.changes
        ]
        action_text = "Change the tool" if candidate.tool_change else f"Change {'; '.join(parts)}"
        first = candidate.changes[0] if candidate.changes else None
        return BaselineAction(
            method="counterfactual",
            blamed_shap=None,
            shap_values=shap_values,
            blamed_feature=TOOL_STATE if candidate.tool_change else " + ".join(c.lever for c in candidate.changes),
            kind="tool_change" if candidate.tool_change else "lever",
            lever=None if first is None else first.lever,
            from_value=None if first is None else first.from_value,
            to_value=None if first is None else first.to_value,
            change_pct=None if first is None else first.change_pct,
            changes=list(candidate.changes),
            cost=candidate.cost,
            description=(
                f"{action_text}. XGBoost predicts failure risk drops from "
                f"{risk_before:.1%} to {risk_after:.1%} (cost {candidate.cost:.1f})."
            ),
            predicted_risk=risk_before,
            predicted_risk_after=risk_after,
        )

    def recommend_all(
        self,
        row: pd.Series,
        magnitude_pct: float,
        target_risk: float = OPTIMIZER_TARGET_RISK,
        shap_low_risk_rule: bool = True,
    ) -> dict[str, BaselineAction]:
        """All three baselines for one state (SHAP values computed once), keyed by method name.

        `shap_low_risk_rule`: B1 and B2 do nothing when the model's own risk is at or below the target
        (on in the API and UI). The benchmark turns it off, so B1 and B2 always act as defined in Phase 7
        task A1, which is the more favourable choice for them there. B3 always has this rule by design.
        """
        contributions = self.shap_values(row)
        return {
            "naive_shap": self.recommend(row, magnitude_pct, contributions, target_risk, shap_low_risk_rule),
            "actionable_shap": self.recommend_actionable(
                row, magnitude_pct, contributions, target_risk, shap_low_risk_rule
            ),
            "counterfactual": self.recommend_counterfactual(row, target_risk, contributions),
        }

    def _predicted_risk_with(self, row: pd.Series, levers: dict[str, float], tool_change: bool) -> float:
        """The model's predicted risk with some lever values replaced (and tool_wear set to 0 for a tool change)."""
        changed = row.copy()
        for lever, value in levers.items():
            changed[lever] = value
        if tool_change:
            changed[TOOL_STATE] = 0.0
        return self.predict_risk(changed)

    def _lever_action(self, row: pd.Series, lever: str, percent: float) -> tuple[dict[str, Any], float]:
        """A lever change and the baseline model's predicted risk after it."""
        settings = DEFAULT_CONFIG.levers[lever]
        current = float(row[lever])
        target = float(np.clip(current * (1.0 + percent / 100.0), settings.minimum, settings.maximum))
        changed = row.copy()
        changed[lever] = target
        actual_pct = 100.0 * (target - current) / current if current else 0.0
        risk_after = self.predict_risk(changed)
        action = {
            "kind": "lever",
            "lever": lever,
            "from_value": current,
            "to_value": target,
            "change_pct": actual_pct,
            "changes": [LeverChange(lever=lever, from_value=current, to_value=target, change_pct=actual_pct)],
            "cost": LEVER_COSTS[lever].cost_per_pct * abs(actual_pct),
            "description": (
                f"Change {lever} from {format_value(current)} to {format_value(target)} ({actual_pct:+.0f}%)."
            ),
            "predicted_risk_after": risk_after,
        }
        return action, risk_after


def low_risk_description(predicted_risk: float, target_risk: float) -> str:
    """The shared wording when a baseline's own model predicts a risk at or below the target."""
    return (
        f"No action (its model predicts low risk): XGBoost predicts {predicted_risk:.1%} failure risk, "
        f"at or below the {target_risk:.0%} target."
    )


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
