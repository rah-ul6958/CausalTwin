"""REST and WebSocket routes."""

import asyncio
import json
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypeVar

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from causaltwin.api.schemas import (
    ActionResponse,
    ByodResponse,
    EdgeEvidenceResponse,
    FaultRequest,
    GraphResponse,
    HealthResponse,
    ModelCardsResponse,
    Provenance,
    RecommendResponse,
    RootCauseResponse,
    ScenarioRequest,
    SimControlRequest,
    SimStatus,
    WhatIfRequest,
    WhatIfResponse,
)
from causaltwin.api.state import AppState
from causaltwin.config import (
    ALLOW_BENCHMARK_RUN_ENV,
    BENCHMARK_RESULTS_PATH,
    BOOTSTRAP_RESAMPLES,
    BYOD_MAX_COLUMNS,
    BYOD_MAX_ROWS,
    BYOD_MAX_UPLOAD_BYTES,
    BYOD_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_CONFIG,
    FAULT_NAMES,
    HEAVY_REQUEST_TIMEOUT_SECONDS,
    LIVE_SEED,
    MEASURED_VARIABLES,
    OPTIMIZER_TARGET_RISK,
    SIM_STEP_MAX,
    VALIDATION_RESULTS_PATH,
    WHATIF_PERCENT_LIMITS,
)
from causaltwin.engine.schemas import Intervention
from causaltwin.engine.twin import CausalTwinEngine, EngineSettings
from causaltwin.ingestion import data_quality_report, load_csv
from causaltwin.intelligence.benchmark import run_benchmark
from causaltwin.simulator import SCENARIOS
from causaltwin.utils.activity_log import get_activity_logger

router = APIRouter()
ResultType = TypeVar("ResultType")
SCATTER_POINTS = 400
UPLOADED_NOTE = (
    "Uploaded data: no simulator ground truth is available, so these results cannot be validated "
    "against a known true graph."
)
DISCOVERY_METHOD = (
    "PCMCI MCI test (tigramite ParCorr) with the full candidate set as conditions and a joint test "
    "over each cause's lags; failure causes by multivariate logistic regression; edge confidence by "
    "moving-block bootstrap"
)


def _state(request: Request) -> AppState:
    """The application state."""
    state: AppState = request.app.state.causaltwin
    state.requests_served += 1
    return state


def _status(state: AppState) -> SimStatus:
    """Typed live status."""
    return SimStatus(**state.status())


def _provenance(method: str, window: str, milliseconds: float | None = None, **parameters: Any) -> Provenance:
    """Provenance for a simulated-data result."""
    return Provenance(method=method, data_window=window, parameters=parameters, computation_ms=milliseconds)


def _require_simulator(state: AppState) -> None:
    """Fault injection, scenarios and the guided demo exist only with the built-in simulator."""
    source = state.live.source_info
    if not source.has_simulator:
        raise HTTPException(
            status_code=409,
            detail=f"Not available: the live data comes from {source.description}, not the simulator. "
            "With real data, recommendations are advisory: apply them on the machine.",
        )


def _require_running(state: AppState) -> None:
    """Risk-based results need recent normal running data."""
    if not state.live.history_is_usable():
        raise HTTPException(status_code=409, detail="The machine is stopped or just restarted; no risk estimate now.")


async def _run_heavy(
    state: AppState,
    function: Callable[[], ResultType],
    label: str,
    timeout_seconds: float = HEAVY_REQUEST_TIMEOUT_SECONDS,
) -> ResultType:
    """Run a heavy computation in a worker thread, with a concurrency limit and a timeout.

    Too many at once: 503 "busy". Too slow: 504. Python cannot stop a running thread, so a
    timed-out computation still finishes in the background; the client is told so.
    """
    if state.heavy_slots.locked():
        raise HTTPException(
            status_code=503, detail=f"The server is busy with other computations; retry the {label} in a few seconds."
        )
    async with state.heavy_slots:
        try:
            return await asyncio.wait_for(asyncio.to_thread(function), timeout=timeout_seconds)
        except TimeoutError as error:
            raise HTTPException(
                status_code=504,
                detail=f"The {label} took longer than {timeout_seconds:.0f} s, so the server stopped waiting "
                "(it may finish in the background). The server may be overloaded; retry shortly.",
            ) from error


def _check_intervention_limits(interventions: list[Intervention]) -> None:
    """Lever settings must stay inside the lever's allowed range; percent changes inside fixed limits."""
    low_percent, high_percent = WHATIF_PERCENT_LIMITS
    for intervention in interventions:
        lever = DEFAULT_CONFIG.levers.get(intervention.variable)
        if intervention.change_type == "set" and lever is not None:
            if not lever.minimum <= intervention.value <= lever.maximum:
                raise HTTPException(
                    status_code=400,
                    detail=f"{intervention.variable} must be between {lever.minimum:g} and {lever.maximum:g}, "
                    f"got {intervention.value:g}",
                )
        if intervention.change_type == "percent" and not low_percent <= intervention.value <= high_percent:
            raise HTTPException(
                status_code=400,
                detail=f"A percent change must be between {low_percent:g}% and {high_percent:g}%, "
                f"got {intervention.value:g}%",
            )


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Service status."""
    state = _state(request)
    return HealthResponse(
        status="ok",
        engine_fitted=state.live.engine.model is not None,
        live_running=state.live.running,
        step=state.live.step_count,
        scenario=state.live.scenario,
        data_source=state.live.source_info.kind,
        simulated=state.live.source_info.simulated_data,
    )


@router.get("/graph", response_model=GraphResponse)
def graph(request: Request) -> GraphResponse:
    """Causal graph with bootstrap confidence."""
    engine = _state(request).live.engine
    current = engine.get_graph()
    return GraphResponse(
        graph=current,
        provenance=Provenance(
            method=DISCOVERY_METHOD,
            data_window=f"most recent {current.window_steps} steps used for discovery",
            parameters={
                "tau_max": engine.settings.tau_max,
                "alpha": engine.settings.alpha,
                "bootstrap_resamples": engine.settings.bootstrap_resamples,
            },
            assumptions=engine.get_assumptions(),
        ),
    )


@router.get("/graph/correlation", response_model=GraphResponse)
def correlation_graph(request: Request) -> GraphResponse:
    """Plain lagged-correlation graph (for the Correlation vs Causation toggle)."""
    engine = _state(request).live.engine
    current = engine.get_correlation_graph()
    return GraphResponse(
        graph=current,
        provenance=Provenance(
            method="Lagged Pearson correlation",
            data_window=f"most recent {current.window_steps} training steps",
            parameters={"threshold": engine.settings.correlation_threshold},
            assumptions=["Correlation is not causation: edges include common causes and proxies."],
        ),
    )


@router.get("/graph/edge/{cause}/{effect}", response_model=EdgeEvidenceResponse)
def edge_evidence(request: Request, cause: str, effect: str) -> EdgeEvidenceResponse:
    """Evidence for one causal edge: statistics and paired data points."""
    engine = _state(request).live.engine
    edge = next((e for e in engine.get_graph().edges if e.cause == cause and e.effect == effect), None)
    if edge is None:
        raise HTTPException(status_code=404, detail=f"No causal edge {cause} -> {effect} in the current graph")
    confidence = next((c for c in engine.get_edge_confidences() if c.cause == cause and c.effect == effect), None)
    source = engine.failure_history if engine.window and effect == engine.window.outcome else engine.window
    assert source is not None
    rows = np.arange(edge.lag, source.n_rows)
    rows = rows[~source.row_mask[rows] & ~source.row_mask[rows - edge.lag]]
    rows = np.random.default_rng(0).choice(rows, size=min(SCATTER_POINTS, rows.size), replace=False)
    x_values = source.values[rows - edge.lag, source.index_of(cause)]
    if effect == source.outcome and source.outcome_values is not None:
        y_values = source.outcome_values[rows]
    else:
        y_values = source.values[rows, source.index_of(effect)]
    return EdgeEvidenceResponse(
        edge=edge,
        confidence=confidence,
        points=[(float(x), float(y)) for x, y in zip(x_values, y_values, strict=True)],
        x_label=f"{cause}(t-{edge.lag})",
        y_label=f"{effect}(t)",
        provenance=_provenance(DISCOVERY_METHOD, f"{rows.size} sampled rows of the discovery data", lag=edge.lag),
    )


@router.post("/whatif", response_model=WhatIfResponse)
async def what_if(request: Request, body: WhatIfRequest) -> WhatIfResponse:
    """Simulate interventions from the live machine's current state."""
    state = _state(request)
    _check_intervention_limits(body.interventions)
    live = state.live

    def compute() -> tuple[Any, Any]:
        with live.lock:
            _require_running(state)
        engine, history = live.snapshot()
        result = engine.what_if(body.interventions, history, body.horizon, body.n_rollouts, None, body.noise_scale)
        return history, result

    try:
        history, result = await _run_heavy(state, compute, "what-if simulation")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return WhatIfResponse(
        result=result,
        provenance=Provenance(
            method="Monte Carlo rollouts of the fitted causal model under do-interventions",
            data_window=f"last {len(history)} live steps as starting state",
            computation_ms=result.computation_ms,
            parameters={"horizon": result.horizon, "n_rollouts": result.n_rollouts},
            assumptions=result.assumptions,
        ),
    )


@router.get("/root-cause", response_model=RootCauseResponse)
async def root_cause(request: Request) -> RootCauseResponse:
    """Ranked causal paths for the current risk, plus symptoms."""
    state = _state(request)
    live = state.live

    def compute() -> Any:
        with live.lock:
            _require_running(state)
        engine, history = live.snapshot()
        return engine.root_cause(history)

    result = await _run_heavy(state, compute, "root-cause analysis")
    return RootCauseResponse(
        result=result,
        provenance=Provenance(
            method="Steady-state gains of the fitted equations along causal paths to failure",
            data_window="current live state versus the median of the fitted window",
            computation_ms=result.computation_ms,
            assumptions=[
                "Linearized contributions; accumulators such as tool wear start paths.",
                "Symptoms: correlated with failure soon but no causal path in the discovered graph.",
            ],
        ),
    )


@router.get("/recommend", response_model=RecommendResponse)
async def recommendation(
    request: Request, target: float = Query(OPTIMIZER_TARGET_RISK, gt=0, lt=1)
) -> RecommendResponse:
    """The cheapest intervention that brings predicted risk below the target, with the full search table."""
    state = _state(request)
    live = state.live

    def compute() -> tuple[Any, Any]:
        with live.lock:
            _require_running(state)
        return live.recommend_now(target)

    result, baseline_action = await _run_heavy(state, compute, "recommendation search")
    return RecommendResponse(
        recommendation=result,
        baseline_action=baseline_action,
        provenance=Provenance(
            method="Grid search over single levers (2% steps), lever pairs (5% steps) and a tool change, scored by "
            "the batched what-if engine; top 5 re-checked with independent noise",
            data_window="current live state",
            computation_ms=result.search_ms,
            parameters={"target_risk": target, "n_candidates": result.n_candidates},
            assumptions=[
                "Costs are illustrative units.",
                "Baseline action: XGBoost + SHAP top feature mapped to an "
                "engineer's action (vibration -> damping), shown for contrast.",
            ],
        ),
    )


@router.post("/apply", response_model=ActionResponse)
def apply(request: Request) -> ActionResponse:
    """Apply the latest recommendation to the live simulator."""
    state = _state(request)
    _require_simulator(state)
    try:
        applied = state.live.apply_recommendation()
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ActionResponse(ok=True, message="Recommendation applied", details=applied, status=_status(state))


def _fault_action(request: Request, body: FaultRequest, inject: bool) -> ActionResponse:
    """Inject or clear a fault."""
    state = _state(request)
    _require_simulator(state)
    if body.fault not in FAULT_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown fault. Known: {list(FAULT_NAMES)}")
    state.live.set_fault(body.fault, inject)
    verb = "Injected" if inject else "Cleared"
    get_activity_logger().log("fault", f"{verb} fault {body.fault}")
    return ActionResponse(ok=True, message=f"{verb} {body.fault}", status=_status(state))


@router.post("/inject-fault", response_model=ActionResponse)
def inject_fault(request: Request, body: FaultRequest) -> ActionResponse:
    """Inject a structural fault into the live simulator."""
    return _fault_action(request, body, True)


@router.post("/clear-fault", response_model=ActionResponse)
def clear_fault(request: Request, body: FaultRequest) -> ActionResponse:
    """Clear a structural fault."""
    return _fault_action(request, body, False)


@router.post("/scenario", response_model=ActionResponse)
def scenario(request: Request, body: ScenarioRequest) -> ActionResponse:
    """Switch the live scenario."""
    state = _state(request)
    _require_simulator(state)
    if body.scenario not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"Unknown scenario. Known: {list(SCENARIOS)}")
    state.live.switch_scenario(body.scenario)
    return ActionResponse(ok=True, message=f"Switched to {body.scenario}", status=_status(state))


@router.post("/sim/control", response_model=ActionResponse)
async def sim_control(request: Request, body: SimControlRequest) -> ActionResponse:
    """Pause, resume, set speed, step, reset, or release all interventions."""
    state = _state(request)
    live = state.live
    if body.action == "pause":
        live.running = False
    elif body.action == "resume":
        live.running = True
    elif body.action == "speed":
        if body.value is None or not 0.1 <= body.value <= 200:
            raise HTTPException(status_code=400, detail="speed needs a value between 0.1 and 200 steps per second")
        live.steps_per_second = body.value
    elif body.action == "step":
        n_steps = int(body.value or 1)
        if not 1 <= n_steps <= SIM_STEP_MAX:
            raise HTTPException(status_code=400, detail=f"step needs 1 to {SIM_STEP_MAX} steps")
        await _run_heavy(state, lambda: live.advance(n_steps, fast=n_steps > 10), f"{n_steps}-step run")
    elif body.action == "reset":
        live.running = False
        await asyncio.to_thread(live.reset, LIVE_SEED, "normal")
    elif body.action == "release":
        _require_simulator(state)
        live.release_all()
    return ActionResponse(ok=True, message=f"{body.action} done", status=_status(state))


@router.post("/demo/start")
async def demo_start(request: Request) -> dict[str, Any]:
    """Start the guided demo (fixed seed) and play the first beat."""
    state = _state(request)
    _require_simulator(state)
    if state.demo_lock.locked():
        raise HTTPException(status_code=409, detail="A demo beat is still running; wait for it to finish.")
    async with state.demo_lock:
        state.live.running = False
        return await asyncio.to_thread(state.demo.start)


@router.post("/demo/next")
async def demo_next(request: Request) -> dict[str, Any]:
    """Play the next demo beat."""
    state = _state(request)
    _require_simulator(state)
    if state.demo.position < 0:
        raise HTTPException(status_code=409, detail="Start the demo first with /demo/start")
    if state.demo_lock.locked():
        raise HTTPException(status_code=409, detail="A demo beat is still running; wait for it to finish.")
    async with state.demo_lock:
        return await asyncio.to_thread(state.demo.next)


@router.get("/benchmark")
def benchmark(request: Request) -> dict[str, Any]:
    """Cached Correlation Trap benchmark results."""
    state = _state(request)
    if not BENCHMARK_RESULTS_PATH.exists():
        raise HTTPException(status_code=404, detail="No cached benchmark; run POST /benchmark/run")
    results = json.loads(BENCHMARK_RESULTS_PATH.read_text())
    return {
        **results,
        "running": state.benchmark_running,
        "provenance": _provenance(
            "CausalTwin optimizer vs XGBoost + SHAP baseline, both applied in forks of the real simulator",
            f"{results.get('n_cases')} high-risk situations",
            ground_truth="simulator",
        ).model_dump()
        | {"ground_truth_used": True},
    }


@router.post("/benchmark/run")
async def benchmark_run(
    request: Request, situations_per_scenario: int | None = Query(None, ge=1, le=10)
) -> dict[str, Any]:
    """Run the benchmark in the background; progress arrives as "benchmark_progress" messages.

    A smaller `situations_per_scenario` gives a quick run that is not written to the cache.
    """
    state = _state(request)
    if not state.runtime.allow_benchmark_run:
        raise HTTPException(
            status_code=403,
            detail=f"Re-running the benchmark is disabled on this server ({ALLOW_BENCHMARK_RUN_ENV}=0) to keep the "
            "public demo responsive. The cached results are shown; run it locally to reproduce them.",
        )
    if state.benchmark_running:
        raise HTTPException(status_code=409, detail="A benchmark is already running")
    state.benchmark_running = True
    quick = situations_per_scenario is not None

    def progress(done: int, total: int, message: str) -> None:
        state.benchmark_progress = {"done": done, "total": total, "message": message}
        state.hub.broadcast("benchmark_progress", state.benchmark_progress)

    async def job() -> None:
        try:
            kwargs: dict[str, Any] = {"progress": progress}
            if quick:
                kwargs.update(per_scenario=situations_per_scenario, seeds_per_situation=2, output_path=None)
            results = await asyncio.to_thread(run_benchmark, state.models, **kwargs)
            state.last_benchmark_run = results
            state.hub.broadcast(
                "benchmark_done", {"n_cases": results["n_cases"], "summary": results["summary"], "cached": not quick}
            )
        finally:
            state.benchmark_running = False

    asyncio.create_task(job())
    return {"started": True, "quick": quick}


@router.get("/validation")
def validation(request: Request) -> dict[str, Any]:
    """Phase 2 validation results against simulator ground truth."""
    _state(request)
    if not VALIDATION_RESULTS_PATH.exists():
        raise HTTPException(status_code=404, detail="No validation results; run scripts/run_validation.py")
    return json.loads(VALIDATION_RESULTS_PATH.read_text())


@router.get("/model-cards", response_model=ModelCardsResponse)
def model_cards(request: Request) -> ModelCardsResponse:
    """Fitted equations with held-out accuracy."""
    engine = _state(request).live.engine
    return ModelCardsResponse(
        cards=engine.get_model_cards(),
        provenance=Provenance(
            method="Ridge regression per variable on its discovered causes; logistic regression for failure",
            data_window="discovery window (linear) and failure history (logistic); R squared / AUC on the final 20%",
            parameters={"ridge_penalty": engine.settings.ridge_penalty},
            assumptions=engine.get_assumptions(),
        ),
    )


@router.get("/metrics")
def metrics(request: Request) -> dict[str, Any]:
    """Pipeline timings, counts, and system status."""
    state = _state(request)
    timings: dict[str, float] = {}
    for entry in get_activity_logger().entries():
        if entry.duration_ms is not None:
            timings[entry.step_name] = entry.duration_ms
    live = state.live
    return {
        "latest_step_durations_ms": timings,
        "counts": {
            "steps": live.step_count,
            "events": len(live.events),
            "alerts": len(live.alerts),
            "drift_alerts": sum(1 for a in live.alerts if a.get("kind") == "drift"),
            "recommendations_applied": len(live.applied),
            "requests_served": state.requests_served,
            "websocket_clients": len(state.hub.clients),
            "messages_sent": state.hub.sent_counts,
        },
        "status": state.status(),
        "benchmark": {"running": state.benchmark_running, "progress": state.benchmark_progress},
        "training": {"seconds": state.models.training_seconds, **state.models.details},
    }


@router.get("/logs")
def logs(
    request: Request,
    limit: int = Query(100, ge=1, le=5000),
    routine: Literal["include", "exclude", "only"] = "include",
) -> list[dict[str, Any]]:
    """Most recent activity log entries.

    `routine` controls the live loop's per-step risk estimates: "include" (default),
    "exclude" them, or return "only" them.
    """
    _state(request)
    return [entry.model_dump() for entry in get_activity_logger().entries(routine)[-limit:]]


@router.get("/data/recent")
def data_recent(request: Request, n: int = Query(200, ge=1, le=5000)) -> dict[str, Any]:
    """The most recent live rows (simulated)."""
    rows = _state(request).live.buffer.dataframe(n)
    return {"simulated": True, "rows": json.loads(rows.to_json(orient="records"))}


@router.get("/data/export")
def data_export(request: Request) -> Response:
    """CSV download of the live buffer (simulated data)."""
    frame = _state(request).live.buffer.dataframe()
    return Response(
        content=frame.to_csv(index=False),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=causaltwin_simulated_live_data.csv"},
    )


@router.get("/data/quality")
def data_quality(request: Request) -> dict[str, Any]:
    """Data quality report of the live buffer."""
    frame = _state(request).live.buffer.dataframe()
    report = data_quality_report(frame, list(MEASURED_VARIABLES))
    return {"simulated": True, "rows": len(frame), "columns": [item.model_dump() for item in report]}


@router.post("/byod/upload", response_model=ByodResponse)
async def byod_upload(
    request: Request, file: UploadFile = File(...), roles: str = Form(...), time_column: str | None = Form(None)
) -> ByodResponse:
    """Bring Your Own Data: run discovery and fitting on an uploaded CSV."""
    state = _state(request)
    try:
        column_roles = json.loads(roles)
        assert isinstance(column_roles, dict)
    except (json.JSONDecodeError, AssertionError) as error:
        raise HTTPException(status_code=400, detail='roles must be a JSON object such as {"temp": "sensor"}') from error
    if len(column_roles) > BYOD_MAX_COLUMNS:
        raise HTTPException(status_code=400, detail=f"At most {BYOD_MAX_COLUMNS} columns can be analysed at once.")
    content = await file.read(BYOD_MAX_UPLOAD_BYTES + 1)
    if len(content) > BYOD_MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413, detail=f"The file is larger than {BYOD_MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )
    if content.count(b"\n") > BYOD_MAX_ROWS + 1:
        raise HTTPException(
            status_code=413,
            detail=f"The file has more than {BYOD_MAX_ROWS:,} rows; upload a shorter or down-sampled extract.",
        )
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "upload.csv"
        path.write_bytes(content)
        try:
            data, report = load_csv(path, column_roles, time_column)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    start = time.perf_counter()
    if report.time_column and report.time_column != "timestamp":
        data = data.rename(columns={report.time_column: "timestamp"})
    time_series = report.time_column is not None
    used_roles = {column: role for column, role in report.column_roles.items()}
    quality = [item.model_dump() for item in data_quality_report(data, [c for c in used_roles if c in data])]
    engine = CausalTwinEngine(EngineSettings(discovery_backend="numpy", bootstrap_resamples=BOOTSTRAP_RESAMPLES))
    notes = [UPLOADED_NOTE]
    graph, cards = None, []
    try:
        await _run_heavy(
            state,
            lambda: engine.fit(data, used_roles, time_series),
            "analysis of the uploaded data",
            BYOD_REQUEST_TIMEOUT_SECONDS,
        )
        graph, cards = engine.get_graph(), engine.get_model_cards()
        state.byod_engine = engine
        notes += graph.notes
    except (ValueError, RuntimeError, np.linalg.LinAlgError) as error:
        notes.append(f"Causal analysis could not run on this data: {error}")
    elapsed = (time.perf_counter() - start) * 1000
    return ByodResponse(
        validation_report=report.model_dump(),
        quality=quality,
        graph=graph,
        model_cards=cards,
        mode="time_series" if time_series else "tabular",
        notes=notes,
        provenance=Provenance(
            method=DISCOVERY_METHOD if time_series else "PCMCIplus at tau_max = 0 (tabular mode)",
            data_window=f"{report.n_rows} uploaded rows",
            computation_ms=elapsed,
            assumptions=engine.get_assumptions() if engine.model is not None else [],
            data_source="uploaded",
            ground_truth_available=False,
        ),
    )


@router.websocket("/ws/stream")
async def stream(websocket: WebSocket) -> None:
    """Live message stream: sensor_frame, risk_update, event, alert, log, graph_update (and demo/benchmark)."""
    state: AppState = websocket.app.state.causaltwin
    await websocket.accept()
    queue = state.hub.register()
    try:
        await websocket.send_json({"type": "hello", "time": time.time(), "data": state.status()})
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        state.hub.unregister(queue)
