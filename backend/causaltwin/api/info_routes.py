"""Read-only information routes used by the frontend: configuration, histories, ground truth.

Ground-truth routes (`/validation/true-graph`, `/drift-results`) serve validation views only
and label their content as simulator ground truth.
"""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from causaltwin.api.live import GROUND_TRUTH_LABEL
from causaltwin.api.state import AppState
from causaltwin.config import (
    DEFAULT_CONFIG,
    DRIFT_CUSUM_H,
    DRIFT_CUSUM_K,
    DRIFT_REDISCOVERY_WINDOW,
    DRIFT_RESULTS_PATH,
    DRIFT_Z_CLIP,
    FAULT_NAMES,
    LEVER_COSTS,
    LEVER_NAMES,
    LIVE_RISK_ROLLOUTS,
    LIVE_STEPS_PER_SECOND,
    LIVE_TRUTH_EVERY,
    OPTIMIZER_PLANNING_MARGIN,
    OPTIMIZER_SEARCH_ROLLOUTS,
    OPTIMIZER_TARGET_RISK,
    PERFORMANCE_BUDGETS_MS,
    ROOT_CAUSE_RISK_THRESHOLD,
    SECONDS_PER_STEP,
    TOOL_CHANGE_COST,
    VARIABLE_INFO,
    VIBRATION_DAMPING_COST,
)
from causaltwin.intelligence.baseline import SENSOR_ACTION_MAP
from causaltwin.simulator import SCENARIOS, get_true_graph

router = APIRouter()
ILLUSTRATIVE_NOTE = (
    "All physical constants, ranges and costs are illustrative and plausible, not taken from a real machine "
    "datasheet. Costs are in illustrative units, not money."
)


def _state(request: Request) -> AppState:
    """The application state."""
    state: AppState = request.app.state.causaltwin
    return state


@router.get("/config")
def configuration(request: Request) -> dict[str, Any]:
    """Current settings, lever ranges and costs, variable metadata, budgets, and the baseline model card."""
    state = _state(request)
    engine = state.live.engine
    levers = {
        name: {
            "minimum": DEFAULT_CONFIG.levers[name].minimum,
            "maximum": DEFAULT_CONFIG.levers[name].maximum,
            "unit": VARIABLE_INFO[name]["unit"],
            "cost_per_pct": LEVER_COSTS[name].cost_per_pct,
            "max_change_pct": LEVER_COSTS[name].max_change_pct,
            "side_effect_increase": LEVER_COSTS[name].side_effect_increase,
            "side_effect_decrease": LEVER_COSTS[name].side_effect_decrease,
        }
        for name in LEVER_NAMES
    }
    variables = {
        name: {"type": info["type"], "unit": info["unit"], "normal_range": info["normal_range"]}
        for name, info in VARIABLE_INFO.items()
    }
    return {
        "illustrative_note": ILLUSTRATIVE_NOTE,
        "data_source": state.live.source_info.as_dict(),
        "seconds_per_step": SECONDS_PER_STEP,
        "engine": engine.settings.model_dump(),
        "levers": levers,
        "variables": variables,
        "faults": list(FAULT_NAMES),
        "scenarios": [{"name": name, "description": d.description} for name, d in SCENARIOS.items()],
        "budgets_ms": PERFORMANCE_BUDGETS_MS,
        "optimizer": {
            "target_risk": OPTIMIZER_TARGET_RISK,
            "planning_margin": OPTIMIZER_PLANNING_MARGIN,
            "search_rollouts": OPTIMIZER_SEARCH_ROLLOUTS,
            "tool_change_cost": TOOL_CHANGE_COST,
        },
        "drift": {
            "cusum_k": DRIFT_CUSUM_K,
            "cusum_h": DRIFT_CUSUM_H,
            "z_clip": DRIFT_Z_CLIP,
            "rediscovery_window": DRIFT_REDISCOVERY_WINDOW,
        },
        "live": {
            "default_steps_per_second": LIVE_STEPS_PER_SECOND,
            "risk_rollouts": LIVE_RISK_ROLLOUTS,
            "alert_risk_threshold": ROOT_CAUSE_RISK_THRESHOLD,
            "truth_every_steps": LIVE_TRUTH_EVERY,
        },
        "baseline_model": {
            "name": "XGBoost classifier + SHAP TreeExplainer",
            "metrics": state.models.baseline.metrics,
            "features": state.models.baseline.features,
            "action_mapping": [
                {"feature": feature, "action": kind, "lever": lever, "direction": direction}
                for feature, (kind, lever, direction) in SENSOR_ACTION_MAP.items()
            ]
            + [
                {
                    "feature": "a control lever",
                    "action": "lever",
                    "lever": "the same lever",
                    "direction": "whichever the ML model says lowers risk",
                }
            ],
            "damping_cost": VIBRATION_DAMPING_COST,
        },
        "demo_beats": [{"name": beat.name, "title": beat.title} for beat in _demo_beats()],
    }


def _demo_beats() -> list[Any]:
    """The guided demo beats (imported lazily to keep this module light)."""
    from causaltwin.api.live import DEMO_BEATS

    return DEMO_BEATS


@router.get("/events")
def events(request: Request, limit: int = Query(200, ge=1, le=5000)) -> dict[str, Any]:
    """Recent live event log entries (simulated)."""
    live = _state(request).live
    return {"simulated": True, "events": live.events[-limit:]}


@router.get("/alerts")
def alerts(request: Request, limit: int = Query(100, ge=1, le=2000)) -> dict[str, Any]:
    """Recent alerts (risk threshold crossings and drift)."""
    return {"alerts": _state(request).live.alerts[-limit:]}


@router.get("/risk/history")
def risk_history(request: Request, limit: int = Query(300, ge=1, le=2000)) -> dict[str, Any]:
    """Recent risk estimates of the live loop: the twin's estimate and the ground-truth check."""
    live = _state(request).live
    points = list(live.risk_history)[-limit:]
    return {"simulated": True, "run_id": live.run_id, "validation_label": GROUND_TRUTH_LABEL, "points": points}


@router.get("/graph/history")
def graph_history(request: Request) -> dict[str, Any]:
    """Versions of the causal graph over time (a new version after every confirmed drift)."""
    return {"versions": _state(request).live.graph_versions}


@router.get("/drift-results")
def drift_results(request: Request) -> dict[str, Any]:
    """Cached drift-detection evaluation (detection delays per fault, false alarms)."""
    _state(request)
    if not DRIFT_RESULTS_PATH.exists():
        raise HTTPException(status_code=404, detail="No drift results; run scripts/run_benchmark.py --drift")
    return {
        "source": "Simulator ground truth (fault times known because we built the simulator)",
        **json.loads(DRIFT_RESULTS_PATH.read_text()),
    }


@router.get("/validation/true-graph")
def true_graph(request: Request) -> dict[str, Any]:
    """The simulator's true causal graph (ground truth, for validation views only)."""
    _state(request)
    return {"label": "Simulator ground truth (validation only)", **get_true_graph()}
