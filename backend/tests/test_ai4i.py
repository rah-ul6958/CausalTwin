"""AI4I 2020 check: the documented rules are a valid oracle, and the pieces of the comparison work."""

import numpy as np
import pandas as pd
import pytest

from causaltwin.config import AI4I_DATA_PATH, AI4I_TARGET_PROBABILITY
from causaltwin.intelligence.ai4i import (
    Action,
    apply_action,
    candidate_actions,
    causaltwin_action,
    fit_causaltwin,
    load_ai4i,
    rule_failures,
)

pytestmark = pytest.mark.skipif(not AI4I_DATA_PATH.exists(), reason="AI4I dataset not present")


@pytest.fixture(scope="module")
def data() -> pd.DataFrame:
    """The full dataset with physics features."""
    return load_ai4i()


def test_documented_rules_reproduce_the_labels(data: pd.DataFrame) -> None:
    """HDF, PWF and OSF rules agree with the dataset's labels on every row (the oracle is exact)."""
    predicted = [rule_failures(row, twf_present=False) for _, row in data.iterrows()]
    for mode in ("HDF", "PWF", "OSF"):
        rule = np.array([mode in modes for modes in predicted])
        assert (rule == (data[mode].to_numpy() == 1)).all(), mode


def test_actions_recompute_the_physics(data: pd.DataFrame) -> None:
    """Changing speed or torque changes power; a tool change resets wear and wear x torque."""
    row = data.iloc[0]
    changed = apply_action(row, Action(speed_factor=1.2, torque_factor=0.5, tool_change=True))
    assert changed["power_w"] == pytest.approx(row["power_w"] * 1.2 * 0.5)
    assert changed["tool_wear_min"] == 0.0 and changed["wear_torque"] == 0.0
    assert Action(speed_factor=0.8, tool_change=True).describe() == "speed -20%, tool change"


def test_discovery_finds_the_documented_mechanism(data: pd.DataFrame) -> None:
    """Failure is caused by the three physics features; the search respects the target and ranges."""
    sample = data.sample(4000, random_state=0).sort_index()
    engine = fit_causaltwin(sample)
    causes = {edge.cause for edge in engine.get_graph().edges if edge.effect == "failure"}
    assert {"power_w", "wear_torque", "temperature_difference_k"} <= causes
    ranges = {lever: (float(sample[lever].min()), float(sample[lever].max())) for lever in ("speed_rpm", "torque_nm")}
    failed = sample[(sample["OSF"] == 1)].iloc[0]
    action, probability = causaltwin_action(engine, failed, candidate_actions(), ranges)
    changed = apply_action(failed, action)
    assert ranges["torque_nm"][0] <= changed["torque_nm"] <= ranges["torque_nm"][1]
    assert probability <= AI4I_TARGET_PROBABILITY or action.describe() != "no action"


def test_spline_failure_model_learns_the_power_window(data: pd.DataFrame) -> None:
    """On the same discovered causes, the spline model sees that power is risky at both ends of its range.

    The documented power rule is a window (failure below 3,500 W or above 9,000 W); a straight line in
    power cannot express it, a spline can. Discovery and the graph are shared with the linear engine.
    """
    from causaltwin.engine.causal_model import LogisticEquation, SplineFailureModel

    engine = fit_causaltwin(data)
    spline_engine = engine.with_failure_model("spline")
    linear, spline = engine.model.failure, spline_engine.model.failure  # type: ignore[union-attr]
    assert isinstance(linear, LogisticEquation) and isinstance(spline, SplineFailureModel)
    assert spline_engine.get_graph() is engine.get_graph() and spline.terms == linear.terms
    position = [name for name, _ in spline.terms].index("power_w")
    values, logit = spline.curve(position)
    lowest = int(np.argmin(logit))
    assert 0 < lowest < len(values) - 1  # the safest power lies inside the range, not at an end
    assert logit[0] > logit[lowest] + 1.0 and logit[-1] > logit[lowest] + 1.0
    assert spline.auc_holdout is not None and linear.auc_holdout is not None
    assert spline.auc_holdout > linear.auc_holdout
    card = next(card for card in spline_engine.get_model_cards() if card.kind == "logistic")
    assert card.curves is not None and {curve.variable for curve in card.curves} == {name for name, _ in spline.terms}
    assert all(0.0 <= value <= 1.0 for curve in card.curves for value in curve.probability)
