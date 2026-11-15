"""Per-viewer live simulations: isolation between viewers, the limit, clean-up, logs and streams."""

import time
import warnings
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from causaltwin.api.app import create_app
from causaltwin.api.runtime import RuntimeSettings
from causaltwin.api.sessions import SessionManager, valid_session_id
from causaltwin.api.state import AppState
from causaltwin.config import SESSION_EVICT_AFTER_SECONDS, SESSION_HEADER, SESSION_IDLE_SECONDS
from causaltwin.intelligence.training import train_models

warnings.filterwarnings("ignore")

VIEWER_A = "viewer-a-0001"
VIEWER_B = "viewer-b-0002"
VIEWER_C = "viewer-c-0003"


def _runtime(max_sessions: int) -> RuntimeSettings:
    """Laptop settings with a given number of private simulations."""
    return RuntimeSettings(
        cors_origins=("http://localhost:3000",),
        cors_origin_regex=None,
        autostart=False,
        discovery_jobs=1,
        allow_benchmark_run=False,
        max_sessions=max_sessions,
    )


def _headers(session_id: str) -> dict[str, str]:
    """The header a browser tab sends with every request."""
    return {SESSION_HEADER: session_id}


def _manager(client: TestClient) -> SessionManager:
    """The running app's session manager."""
    state: AppState = client.app.state.causaltwin  # type: ignore[attr-defined]
    return state.sessions


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """An app with room for two private simulations."""
    app = create_app(models=train_models(), autostart=False, runtime=_runtime(2))
    with TestClient(app) as test_client:
        yield test_client


def _status(client: TestClient, session_id: str | None) -> dict[str, Any]:
    """The live status as one viewer sees it."""
    headers = _headers(session_id) if session_id else {}
    status: dict[str, Any] = client.get("/metrics", headers=headers).json()["status"]
    return status


def test_session_ids_are_checked() -> None:
    """Only ids of 8 to 64 letters, digits, - and _ are accepted; anything else means the shared simulation."""
    assert valid_session_id(VIEWER_A) == VIEWER_A
    assert valid_session_id("3f2b9c1e-8d4a-4f6b-9a2e-1c7d5e8f0a3b") is not None  # a browser UUID
    for bad in (None, "", "short", "has space in it", "x" * 65, "semi;colon-id", "shared"):
        assert valid_session_id(bad) is None


def test_viewers_do_not_affect_each_other(client: TestClient) -> None:
    """A fault, steps and a reset in one viewer's simulation leave the others unchanged."""
    status_a, status_b = _status(client, VIEWER_A), _status(client, VIEWER_B)
    assert status_a["session"]["private"] and status_b["session"]["private"]
    shared_before = _status(client, None)
    assert shared_before["session"]["private"] is False

    response = client.post("/inject-fault", json={"fault": "lube_leak"}, headers=_headers(VIEWER_A))
    assert response.status_code == 200 and response.json()["status"]["active_faults"] == ["lube_leak"]
    client.post("/sim/control", json={"action": "step", "value": 7}, headers=_headers(VIEWER_A))
    assert _status(client, VIEWER_B)["active_faults"] == []
    assert _status(client, None)["active_faults"] == []
    assert _status(client, VIEWER_B)["step"] == status_b["step"]
    assert _status(client, None)["step"] == shared_before["step"]
    assert _status(client, VIEWER_A)["step"] == status_a["step"] + 7

    reset_b = client.post("/sim/control", json={"action": "reset"}, headers=_headers(VIEWER_B)).json()["status"]
    assert reset_b["run_id"] != status_b["run_id"]
    assert _status(client, VIEWER_A)["run_id"] == status_a["run_id"]
    assert _status(client, VIEWER_A)["active_faults"] == ["lube_leak"]
    # Every run id is unique, so a viewer always notices a new run.
    run_ids = {_status(client, viewer)["run_id"] for viewer in (VIEWER_A, VIEWER_B, None)}
    assert len(run_ids) == 3
    client.post("/clear-fault", json={"fault": "lube_leak"}, headers=_headers(VIEWER_A))


def test_each_viewer_has_its_own_demo(client: TestClient) -> None:
    """Starting the guided demo in one viewer's simulation does not reset another's."""
    step_b = _status(client, VIEWER_B)["step"]
    started = client.post("/demo/start", headers=_headers(VIEWER_A))
    assert started.status_code == 200 and started.json()["position"] == 0
    assert _status(client, VIEWER_B)["step"] == step_b
    # The other viewer has no demo running, so "next" asks it to start one first.
    assert client.post("/demo/next", headers=_headers(VIEWER_B)).status_code == 409


def test_logs_are_per_viewer(client: TestClient) -> None:
    """A viewer's console shows its own actions and global entries, not other viewers' actions."""
    client.post("/inject-fault", json={"fault": "bearing_damage"}, headers=_headers(VIEWER_A))
    client.post("/clear-fault", json={"fault": "bearing_damage"}, headers=_headers(VIEWER_A))

    def messages(session_id: str | None) -> list[str]:
        headers = _headers(session_id) if session_id else {}
        return [entry["message"] for entry in client.get("/logs", params={"limit": 5000}, headers=headers).json()]

    assert "Injected fault bearing_damage" in messages(VIEWER_A)
    assert "Injected fault bearing_damage" not in messages(VIEWER_B)
    assert "Injected fault bearing_damage" not in messages(None)


def test_stream_carries_only_its_own_session(client: TestClient) -> None:
    """A WebSocket with ?session= gets that simulation's messages, and only a watched one steps."""
    step_b = _status(client, VIEWER_B)["step"]
    with client.websocket_connect(f"/ws/stream?session={VIEWER_A}") as socket:
        hello = socket.receive_json()
        assert hello["type"] == "hello" and hello["data"]["session"]["private"] is True
        client.post("/sim/control", json={"action": "resume"}, headers=_headers(VIEWER_A))
        client.post("/sim/control", json={"action": "resume"}, headers=_headers(VIEWER_B))
        frames: list[dict[str, Any]] = []
        deadline = time.time() + 20
        while len(frames) < 3 and time.time() < deadline:
            message = socket.receive_json()
            if message["type"] == "sensor_frame":
                frames.append(message["data"])
        assert len(frames) == 3 and all(frame["run_id"] == hello["data"]["run_id"] for frame in frames)
        # B is "running" but nobody watches it, so it does not step (no CPU for an unwatched tab).
        assert _status(client, VIEWER_B)["step"] == step_b
        metrics = client.get("/metrics", headers=_headers(VIEWER_A)).json()["counts"]
        assert metrics["websocket_clients"] == 1
    client.post("/sim/control", json={"action": "pause"}, headers=_headers(VIEWER_A))


def test_full_server_shares_then_frees_slots() -> None:
    """With every slot taken and watched, a new viewer shares; unwatched slots are taken over or cleaned up."""
    app = create_app(models=train_models(), autostart=False, runtime=_runtime(1))
    with TestClient(app) as client:
        manager = _manager(client)
        assert _status(client, VIEWER_A)["session"]["private"] is True
        # A was active just now, so it keeps its slot and B joins the shared simulation.
        crowded = _status(client, VIEWER_B)["session"]
        assert crowded["private"] is False and "in use" in crowded["note"]
        client.post("/inject-fault", json={"fault": "lube_leak"}, headers=_headers(VIEWER_B))
        assert _status(client, None)["active_faults"] == ["lube_leak"]  # B really uses the shared one
        assert _status(client, VIEWER_A)["active_faults"] == []
        client.post("/clear-fault", json={"fault": "lube_leak"}, headers=_headers(VIEWER_B))

        # Nobody has watched A for a while: a new viewer C takes over its slot.
        session_a = manager.find(VIEWER_A)
        assert session_a is not None
        session_a.last_seen -= SESSION_EVICT_AFTER_SECONDS + 1
        assert _status(client, VIEWER_C)["session"]["private"] is True
        assert manager.find(VIEWER_A) is None and manager.private_count == 1

        # The janitor deletes simulations nobody has watched for SESSION_IDLE_SECONDS.
        assert manager.remove_idle(now=time.monotonic() + SESSION_IDLE_SECONDS + 1) == 1
        assert manager.private_count == 0
        # With the slot free again, B (no open stream) gets a private simulation of its own.
        assert _status(client, VIEWER_B)["session"]["private"] is True


def test_private_simulations_can_be_turned_off() -> None:
    """max_sessions = 0: every viewer shares one simulation, and the status explains why."""
    app = create_app(models=train_models(), autostart=False, runtime=_runtime(0))
    with TestClient(app) as client:
        session = _status(client, VIEWER_A)["session"]
        assert session["private"] is False and "turned off" in session["note"]
        assert _manager(client).private_count == 0
