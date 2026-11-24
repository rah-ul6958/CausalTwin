"""API tests with FastAPI's TestClient: every endpoint, the WebSocket stream, and the scripted demo."""

import io
import json
import time
import warnings
from collections.abc import Iterator

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from causaltwin.api.app import create_app
from causaltwin.intelligence.training import train_models
from causaltwin.simulator import run_scenario

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """One app for the module, live loop paused (tests step it explicitly)."""
    app = create_app(models=train_models(), autostart=False)
    with TestClient(app) as test_client:
        yield test_client


def _step(client: TestClient, n_steps: int) -> dict:
    """Advance the live simulator."""
    response = client.post("/sim/control", json={"action": "step", "value": n_steps})
    assert response.status_code == 200
    return response.json()


def test_health_and_status(client: TestClient) -> None:
    """Health reports a fitted engine and simulated data."""
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["engine_fitted"] and body["simulated"] and not body["live_running"]


def test_graphs_and_edge_evidence(client: TestClient) -> None:
    """Causal graph (with confidence and provenance), correlation graph, and edge evidence."""
    graph = client.get("/graph").json()
    edges = {(e["cause"], e["effect"]) for e in graph["graph"]["edges"]}
    assert ("bearing_temp", "failure") in edges and ("vibration", "failure") not in edges
    assert graph["provenance"]["assumptions"] and all(e["confidence"] is not None for e in graph["graph"]["edges"])
    correlation = client.get("/graph/correlation").json()
    assert correlation["graph"]["graph_type"] == "correlation"
    assert ("vibration", "failure") in {(e["cause"], e["effect"]) for e in correlation["graph"]["edges"]}
    evidence = client.get("/graph/edge/coolant_flow/coolant_temp").json()
    assert evidence["edge"]["lag"] == 1 and len(evidence["points"]) == 400 and evidence["confidence"]
    assert client.get("/graph/edge/vibration/failure").status_code == 404


def test_whatif_root_cause_and_model_cards(client: TestClient) -> None:
    """What-if, root cause and model cards return results with provenance."""
    _step(client, 5)
    response = client.post(
        "/whatif", json={"interventions": [{"variable": "coolant_flow", "change_type": "percent", "value": 20}]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["forecasts"]["coolant_temp"]["change_at_end"] < 0 and body["provenance"]["computation_ms"]
    assert client.post("/whatif", json={"interventions": [{"variable": "nope", "value": 1}]}).status_code == 400
    root = client.get("/root-cause").json()
    assert "symptoms" in root["result"] and root["provenance"]["method"]
    cards = client.get("/model-cards").json()["cards"]
    assert {card["variable"] for card in cards} >= {"bearing_temp", "failure"}


def test_recommend_and_apply(client: TestClient) -> None:
    """Recommendation with the full candidate table, then apply it to the live simulator."""
    client.post("/sim/control", json={"action": "reset"})
    for lever, value in {"load_pct": 98.0, "spindle_speed": 4400.0, "coolant_flow": 10.0, "lube_rate": 0.6}.items():
        client.app.state.causaltwin.live.simulator.intervene(lever, value)  # type: ignore[attr-defined]
    _step(client, 200)
    body = client.get("/recommend").json()
    recommendation = body["recommendation"]
    assert recommendation["n_candidates"] == len(recommendation["candidates"]) > 100
    assert recommendation["search_ms"] < 2000 and body["baseline_action"]["blamed_feature"]
    assert all(change["lever"] != "vibration" for change in recommendation["changes"])
    # All three baselines for the same moment; the single baseline_action is the main one (B3).
    assert set(body["baseline_actions"]) == {"naive_shap", "actionable_shap", "counterfactual"}
    assert body["main_baseline"] == "counterfactual"
    assert body["baseline_action"] == body["baseline_actions"]["counterfactual"]
    assert body["baseline_labels"]["counterfactual"].startswith("B3")
    applied = client.post("/apply").json()
    assert applied["ok"] and applied["details"]["action_type"] == recommendation["action_type"]


def test_faults_scenarios_and_controls(client: TestClient) -> None:
    """Fault injection, scenario switch, and every simulation control action."""
    assert client.post("/inject-fault", json={"fault": "lube_leak"}).json()["status"]["active_faults"] == ["lube_leak"]
    assert client.post("/clear-fault", json={"fault": "lube_leak"}).json()["status"]["active_faults"] == []
    assert client.post("/inject-fault", json={"fault": "unknown"}).status_code == 400
    assert client.post("/scenario", json={"scenario": "heat_wave"}).json()["status"]["scenario"] == "heat_wave"
    assert client.post("/scenario", json={"scenario": "moon"}).status_code == 400
    assert client.post("/sim/control", json={"action": "resume"}).json()["status"]["running"]
    assert not client.post("/sim/control", json={"action": "pause"}).json()["status"]["running"]
    assert client.post("/sim/control", json={"action": "speed", "value": 20}).json()["status"]["steps_per_second"] == 20
    assert client.post("/sim/control", json={"action": "speed", "value": 0}).status_code == 400
    assert client.post("/sim/control", json={"action": "release"}).json()["status"]["interventions"] == {}
    assert client.post("/sim/control", json={"action": "reset"}).json()["status"]["scenario"] == "normal"


def test_data_logs_metrics_and_validation(client: TestClient) -> None:
    """Recent data, CSV export, data quality, logs, metrics and cached results."""
    _step(client, 30)
    recent = client.get("/data/recent", params={"n": 25}).json()
    assert recent["simulated"] and len(recent["rows"]) == 25
    export = client.get("/data/export")
    assert export.headers["content-type"].startswith("text/csv") and "bearing_temp" in export.text.splitlines()[0]
    quality = client.get("/data/quality").json()
    assert {column["column"] for column in quality["columns"]} >= {"vibration", "tool_wear"}
    logs = client.get("/logs", params={"limit": 20}).json()
    assert logs and {"step_name", "duration_ms", "message"} <= set(logs[-1])
    metrics = client.get("/metrics").json()
    assert metrics["counts"]["steps"] > 0 and "what_if" in metrics["latest_step_durations_ms"]
    validation = client.get("/validation").json()
    assert validation["trap_check"]["passed"]
    benchmark = client.get("/benchmark")
    assert benchmark.status_code in (200, 404)
    if benchmark.status_code == 200:
        assert benchmark.json()["provenance"]["ground_truth_used"]


def test_websocket_stream(client: TestClient) -> None:
    """The stream sends hello, then sensor frames, risk updates (with a separate ground-truth field) and logs."""
    with client.websocket_connect("/ws/stream") as websocket:
        assert websocket.receive_json()["type"] == "hello"
        _step(client, 3)
        seen: dict[str, dict] = {}
        for _ in range(60):
            message = websocket.receive_json()
            seen.setdefault(message["type"], message)
            if {"sensor_frame", "risk_update", "log"} <= set(seen):
                break
    assert {"sensor_frame", "risk_update", "log"} <= set(seen)
    assert seen["sensor_frame"]["data"]["simulated"] is True
    validation = seen["risk_update"]["data"]["validation"]
    assert "ground truth" in validation["label"].lower() and "true_hazard_per_step" in validation


def test_byod_time_series_and_tabular(client: TestClient) -> None:
    """Uploaded CSVs are analysed; responses say no ground truth is available."""
    sensors = run_scenario("overload", n_steps=6000).sensors
    roles = {
        "ambient_temp": "environment",
        "load_pct": "control",
        "spindle_speed": "control",
        "coolant_flow": "control",
        "lube_rate": "control",
        "motor_current": "sensor",
        "coolant_temp": "sensor",
        "bearing_temp": "sensor",
        "vibration": "sensor",
        "tool_wear": "sensor",
        "failure": "outcome",
        "step": "ignore",
        "machine_running": "ignore",
    }
    upload = sensors.to_csv(index=False).encode()
    response = client.post(
        "/byod/upload",
        files={"file": ("factory.csv", io.BytesIO(upload), "text/csv")},
        data={"roles": json.dumps(roles)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "time_series" and body["graph"] is not None
    assert body["provenance"]["data_source"] == "uploaded" and body["provenance"]["ground_truth_available"] is False
    assert any("no simulator ground truth" in note for note in body["notes"])

    rng = np.random.default_rng(1)
    setting = rng.normal(size=2000)
    pressure = 1.5 * setting + rng.normal(size=2000)
    tabular = pd.DataFrame({"setting": setting, "pressure": pressure, "noise": rng.normal(size=2000)})
    response = client.post(
        "/byod/upload",
        files={"file": ("rows.csv", io.BytesIO(tabular.to_csv(index=False).encode()), "text/csv")},
        data={"roles": json.dumps({"setting": "control", "pressure": "sensor", "noise": "sensor"})},
    )
    body = response.json()
    assert response.status_code == 200 and body["mode"] == "tabular"
    assert ("setting", "pressure") in {(e["cause"], e["effect"]) for e in body["graph"]["edges"]}
    assert (
        client.post(
            "/byod/upload", files={"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")}, data={"roles": "not json"}
        ).status_code
        == 400
    )


def test_benchmark_run_in_background_with_progress(client: TestClient) -> None:
    """A quick benchmark run reports progress over the WebSocket and finishes."""
    with client.websocket_connect("/ws/stream") as websocket:
        websocket.receive_json()
        assert client.post("/benchmark/run", params={"situations_per_scenario": 1}).json()["started"]
        assert client.post("/benchmark/run", params={"situations_per_scenario": 1}).status_code == 409
        types = set()
        deadline = time.time() + 180
        while time.time() < deadline:
            message = websocket.receive_json()
            types.add(message["type"])
            if message["type"] == "benchmark_done":
                assert message["data"]["n_cases"] >= 4 and message["data"]["cached"] is False
                break
    assert {"benchmark_progress", "benchmark_done"} <= types


def test_scripted_demo_through_the_api(client: TestClient) -> None:
    """The guided demo plays all eight beats through the API alone."""
    assert client.post("/demo/next").status_code in (200, 409)
    first = client.post("/demo/start").json()
    beats = [first]
    while not beats[-1]["finished"]:
        response = client.post("/demo/next")
        assert response.status_code == 200
        beats.append(response.json())
    names = [beat["beat"] for beat in beats]
    print([(beat["beat"], beat["narration"][:120]) for beat in beats])
    assert names == [
        "normal_operation",
        "risk_rises",
        "recommendation",
        "apply",
        "risk_drops",
        "fault_injected",
        "drift_alert",
        "new_recommendation",
    ]
    by_name = {beat["beat"]: beat for beat in beats}
    assert by_name["risk_rises"]["twin_risk"]["probability"] > 0.1
    assert by_name["risk_drops"]["twin_risk"]["probability"] < by_name["risk_rises"]["twin_risk"]["probability"]
    assert by_name["drift_alert"]["alert"]["status"] == "confirmed"
    assert "coolant_flow" in by_name["drift_alert"]["narration"]
    assert by_name["new_recommendation"]["recommendation"]["action_type"] != "none"
    assert client.post("/demo/next").json()["finished"]


def test_information_endpoints(client: TestClient) -> None:
    """Configuration, histories and labelled ground truth for the frontend."""
    config = client.get("/config").json()
    assert set(config["levers"]) == {"load_pct", "spindle_speed", "coolant_flow", "lube_rate"}
    assert config["levers"]["coolant_flow"]["cost_per_pct"] > 0 and "illustrative" in config["illustrative_note"]
    assert config["budgets_ms"]["recommend"] == 1000 and config["baseline_model"]["metrics"]["auc"] > 0.8
    assert len(config["demo_beats"]) == 8 and config["engine"]["tau_max"] == 3
    assert 0 < config["live"]["alert_risk_threshold"] < 1
    assert "events" in client.get("/events").json() and "alerts" in client.get("/alerts").json()
    versions = client.get("/graph/history").json()["versions"]
    assert versions[0]["version"] == 1
    client.post("/sim/control", json={"action": "step", "value": 5})
    risk_history = client.get("/risk/history", params={"limit": 3}).json()
    assert risk_history["simulated"] and "ground truth" in risk_history["validation_label"].lower()
    assert 1 <= len(risk_history["points"]) <= 3 and "twin_risk" in risk_history["points"][-1]
    # Ground truth is refreshed only every few steps; each point says when it was computed.
    last_point = risk_history["points"][-1]
    assert last_point["true_risk_step"] is None or last_point["true_risk_step"] <= last_point["step"]
    # A reset starts a new run, so clients can drop the old run's series.
    run_before = risk_history["run_id"]
    status = client.post("/sim/control", json={"action": "reset"}).json()["status"]
    assert status["run_id"] == run_before + 1
    assert client.get("/risk/history").json()["points"] == []
    true_graph = client.get("/validation/true-graph").json()
    assert "ground truth" in true_graph["label"].lower() and len(true_graph["edges"]) > 10
    drift = client.get("/drift-results")
    assert drift.status_code in (200, 404)
    logs = client.get("/logs", params={"limit": 500}).json()
    assert all(entry["level"] in ("info", "warning", "error") for entry in logs)
    # Per-step risk estimates are routine entries, filtered on request.
    routine = client.get("/logs", params={"limit": 50, "routine": "only"}).json()
    assert routine and all(entry["routine"] and entry["step_name"] == "what_if" for entry in routine)
    main = client.get("/logs", params={"limit": 500, "routine": "exclude"}).json()
    assert main and not any(entry["routine"] for entry in main)
