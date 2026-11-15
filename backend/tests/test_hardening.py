"""Hardening tests: input limits, readable errors, CORS on errors, busy and timeout answers,
a live loop that survives failures, and a random sequence of API calls while the loop runs."""

import asyncio
import json
import random
import time
import warnings
from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from causaltwin.api import routes
from causaltwin.api.app import create_app
from causaltwin.api.runtime import RuntimeSettings
from causaltwin.api.state import AppState
from causaltwin.config import BYOD_MAX_COLUMNS, BYOD_MAX_ROWS, BYOD_MAX_UPLOAD_BYTES, FAULT_NAMES
from causaltwin.intelligence.training import train_models
from causaltwin.simulator import SCENARIOS
from causaltwin.utils.activity_log import get_activity_logger

warnings.filterwarnings("ignore")

VERCEL_ORIGIN = "https://causaltwin-preview.vercel.app"
RUNTIME = RuntimeSettings(
    cors_origins=("http://localhost:3000",),
    cors_origin_regex=r"https://.*\.vercel\.app",
    autostart=False,
    discovery_jobs=1,
    allow_benchmark_run=False,
)


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """An app configured like a public deployment; server errors come back as responses."""
    app = create_app(models=train_models(), autostart=False, runtime=RUNTIME)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.post("/sim/control", json={"action": "step", "value": 5})
        yield test_client


def _state(client: TestClient) -> AppState:
    """The running app's state."""
    state: AppState = client.app.state.causaltwin  # type: ignore[attr-defined]
    return state


def test_validation_errors_are_readable(client: TestClient) -> None:
    """Out-of-range inputs get a 422 whose detail names the field."""
    response = client.post("/whatif", json={"interventions": [], "n_rollouts": 100_000})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str) and "n_rollouts" in response.json()["detail"]
    too_many = [{"variable": "coolant_flow", "value": 20.0}] * 9
    assert client.post("/whatif", json={"interventions": too_many}).status_code == 422
    not_a_number = '{"interventions": [{"variable": "coolant_flow", "value": NaN}]}'
    response = client.post("/whatif", content=not_a_number, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert client.post("/sim/control", json={"action": "explode"}).status_code == 422
    assert client.post("/scenario", json={"scenario": "x" * 100}).status_code == 422


def test_lever_ranges_and_percent_limits(client: TestClient) -> None:
    """A lever cannot be set outside its allowed range; percent changes are bounded."""
    response = client.post("/whatif", json={"interventions": [{"variable": "coolant_flow", "value": 500.0}]})
    assert response.status_code == 400 and "coolant_flow must be between" in response.json()["detail"]
    percent = {"variable": "load_pct", "change_type": "percent", "value": 1000.0}
    response = client.post("/whatif", json={"interventions": [percent]})
    assert response.status_code == 400 and "percent change" in response.json()["detail"]
    unknown = client.post("/whatif", json={"interventions": [{"variable": "warp_drive", "value": 1.0}]})
    assert unknown.status_code == 400 and "unknown variable" in unknown.json()["detail"]


def test_benchmark_run_can_be_disabled(client: TestClient) -> None:
    """Public deployments can switch off benchmark re-runs; the reason is explained."""
    response = client.post("/benchmark/run")
    assert response.status_code == 403 and "CAUSALTWIN_ALLOW_BENCHMARK_RUN" in response.json()["detail"]
    assert client.get("/benchmark").status_code == 200  # cached results still served


def test_upload_limits(client: TestClient) -> None:
    """Too many columns, rows or bytes are refused with a clear message."""
    roles = json.dumps({f"column_{index}": "sensor" for index in range(BYOD_MAX_COLUMNS + 1)})
    small = b"a,b\n1,2\n"
    response = client.post("/byod/upload", files={"file": ("data.csv", small, "text/csv")}, data={"roles": roles})
    assert response.status_code == 400 and f"{BYOD_MAX_COLUMNS} columns" in response.json()["detail"]
    many_rows = b"a,b\n" + b"1,2\n" * (BYOD_MAX_ROWS + 10)
    roles = json.dumps({"a": "control", "b": "sensor"})
    response = client.post("/byod/upload", files={"file": ("data.csv", many_rows, "text/csv")}, data={"roles": roles})
    assert response.status_code == 413 and "rows" in response.json()["detail"]
    too_big = b"a,b\n" + b"9" * (BYOD_MAX_UPLOAD_BYTES + 200_000)
    response = client.post("/byod/upload", files={"file": ("data.csv", too_big, "text/csv")}, data={"roles": roles})
    assert response.status_code == 413 and "MB" in response.json()["detail"]


def test_unexpected_errors_are_json_with_cors(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bug inside a route gives a JSON 500 the browser can read, and it is logged as an error."""
    engine = _state(client).live.engine

    def broken() -> None:
        raise RuntimeError("simulated bug")

    monkeypatch.setattr(engine, "get_model_cards", broken)
    response = client.get("/model-cards", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 500
    assert "Internal error" in response.json()["detail"] and response.json()["error"] == "RuntimeError"
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    errors = [entry for entry in get_activity_logger().entries("exclude") if entry.step_name == "api_error"]
    assert errors and errors[-1].level == "error"


def test_cors_origin_regex_allows_vercel_previews(client: TestClient) -> None:
    """Preview deployments on a matching domain are allowed; other origins are not."""
    allowed = client.get("/health", headers={"Origin": VERCEL_ORIGIN})
    assert allowed.headers.get("access-control-allow-origin") == VERCEL_ORIGIN
    other = client.get("/health", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in other.headers


def test_busy_and_timeout_answers() -> None:
    """Heavy work beyond the concurrency limit gets 503; work beyond the timeout gets 504."""

    class FakeState:
        heavy_slots: asyncio.Semaphore

    async def scenario() -> tuple[int, int]:
        state = FakeState()
        state.heavy_slots = asyncio.Semaphore(0)
        with pytest.raises(HTTPException) as busy:
            await routes._run_heavy(state, lambda: 1, "test")  # type: ignore[arg-type]
        state.heavy_slots = asyncio.Semaphore(1)
        with pytest.raises(HTTPException) as slow:
            await routes._run_heavy(state, lambda: time.sleep(0.5), "test", timeout_seconds=0.05)  # type: ignore[arg-type]
        return busy.value.status_code, slow.value.status_code

    assert asyncio.run(scenario()) == (503, 504)


def test_live_loop_pauses_and_reports_instead_of_dying(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing step pauses the loop with an error alert; after the fix, Resume continues."""
    live = _state(client).live
    original_step = live.stepper.step

    def failing_step() -> None:
        raise RuntimeError("sensor bus timeout")

    monkeypatch.setattr(live.stepper, "step", failing_step)
    errors_before = live.loop_errors
    # A simulation steps only while someone watches its live stream.
    with client.websocket_connect("/ws/stream"):
        client.post("/sim/control", json={"action": "resume"})
        deadline = time.time() + 5
        while live.running and time.time() < deadline:
            time.sleep(0.05)
        assert not live.running and live.loop_errors == errors_before + 1
        assert live.alerts[-1]["kind"] == "error" and "sensor bus timeout" in live.alerts[-1]["message"]
        monkeypatch.setattr(live.stepper, "step", original_step)
        step_before = client.get("/health").json()["step"]
        client.post("/sim/control", json={"action": "speed", "value": 50})
        client.post("/sim/control", json={"action": "resume"})
        time.sleep(0.6)
        client.post("/sim/control", json={"action": "pause"})
    assert client.get("/health").json()["step"] > step_before


def test_unwatched_shared_simulation_does_not_step(client: TestClient) -> None:
    """With private simulations on, the shared one is "running" but idle until someone watches it."""
    client.post("/sim/control", json={"action": "speed", "value": 50})
    client.post("/sim/control", json={"action": "resume"})
    step_before = client.get("/health").json()["step"]
    time.sleep(0.5)
    assert client.get("/health").json()["step"] == step_before
    assert client.get("/health").json()["live_running"] is True
    client.post("/sim/control", json={"action": "pause"})


def test_random_api_calls_while_the_loop_runs(client: TestClient) -> None:
    """Seeded random actions (faults, scenarios, steps, advice, resets) never produce a server error."""
    rng = random.Random(5)
    live = _state(client).live
    errors_before = live.loop_errors
    client.post("/sim/control", json={"action": "reset"})
    client.post("/sim/control", json={"action": "speed", "value": 60})
    client.post("/sim/control", json={"action": "resume"})
    actions = [
        lambda: client.post("/inject-fault", json={"fault": rng.choice(FAULT_NAMES)}),
        lambda: client.post("/clear-fault", json={"fault": rng.choice(FAULT_NAMES)}),
        lambda: client.post("/scenario", json={"scenario": rng.choice(list(SCENARIOS))}),
        lambda: client.post("/sim/control", json={"action": "step", "value": rng.randint(1, 40)}),
        lambda: client.post("/whatif", json={"interventions": [{"variable": "coolant_flow", "value": 25.0}]}),
        lambda: client.get("/root-cause"),
        lambda: client.get("/recommend"),
        lambda: client.post("/apply"),
        lambda: client.post("/sim/control", json={"action": "release"}),
        lambda: client.post("/sim/control", json={"action": rng.choice(["pause", "resume"])}),
        lambda: client.get("/graph"),
        lambda: client.get("/risk/history"),
    ]
    statuses = []
    with client.websocket_connect("/ws/stream"):  # watched, so the loop really runs meanwhile
        for index in range(45):
            if index in (15, 30):
                response = client.post("/sim/control", json={"action": "reset"})
                client.post("/sim/control", json={"action": "resume"})
            else:
                response = rng.choice(actions)()
            statuses.append(response.status_code)
            time.sleep(0.02)
        assert all(status < 500 for status in statuses), statuses
        assert live.loop_errors == errors_before
        client.post("/sim/control", json={"action": "resume"})
        step_before = client.get("/health").json()["step"]
        time.sleep(0.5)
        client.post("/sim/control", json={"action": "pause"})
    assert client.get("/health").json()["step"] > step_before
