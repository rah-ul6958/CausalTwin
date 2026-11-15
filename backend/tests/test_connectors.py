"""Live data sources: MTConnect parsing, a fake MTConnect agent end to end, log replay, and the API
running on a non-simulator source (no ground truth, advisory recommendations)."""

import threading
import warnings
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest
from fastapi.testclient import TestClient

from causaltwin.api.app import create_app
from causaltwin.api.runtime import RuntimeSettings
from causaltwin.config import DATA_DIR, MEASURED_VARIABLES
from causaltwin.connectors import MTConnectSource, ReplaySource, parse_mtconnect_current
from causaltwin.intelligence.drift import SimulatorStepper
from causaltwin.intelligence.training import train_models
from causaltwin.simulator import build_simulator

warnings.filterwarnings("ignore")

TAG_MAP = {name: f"{name}_id" for name in MEASURED_VARIABLES} | {"machine_running": "execution", "failure": "cond"}
SAMPLE_LOG = DATA_DIR / "samples" / "normal_sensors.csv"


def _streams_document(values: dict[str, float], running: bool = True, fault: bool = False) -> bytes:
    """An MTConnect Streams document like an agent's /current answer."""
    samples = "".join(
        f'<Temperature dataItemId="{name}_id" name="{name}" sequence="{i}">{value}</Temperature>'
        for i, (name, value) in enumerate(values.items())
    )
    condition = '<Fault dataItemId="cond" type="SYSTEM"/>' if fault else '<Normal dataItemId="cond" type="SYSTEM"/>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<MTConnectStreams xmlns="urn:mtconnect.org:MTConnectStreams:1.3"><Header instanceId="1" version="1.3"/>'
        '<Streams><DeviceStream name="Spindle" uuid="test"><ComponentStream component="Rotary" componentId="c1">'
        f"<Samples>{samples}</Samples>"
        f'<Events><Execution dataItemId="execution">{"ACTIVE" if running else "STOPPED"}</Execution></Events>'
        f"<Condition>{condition}</Condition>"
        "</ComponentStream></DeviceStream></Streams></MTConnectStreams>"
    ).encode()


@pytest.fixture(scope="module")
def fake_agent() -> Iterator[str]:
    """A local HTTP server answering /current with the simulator's latest readings."""
    stepper = SimulatorStepper(build_simulator("normal", n_steps=5000, seed=3))
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
            with lock:
                row, _ = stepper.step()
            body = _streams_document({name: row[name] for name in MEASURED_VARIABLES})
            self.send_response(200 if self.path.endswith("/current") else 404)
            self.send_header("Content-Type", "application/xml")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """Keep test output quiet."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_parse_mtconnect_document() -> None:
    """Values are found by data item id and by name; UNAVAILABLE stays text; conditions give their state."""
    document = _streams_document({"bearing_temp": 61.5, "spindle_speed": 3000.0}, fault=True)
    document = document.replace(b">3000.0<", b">UNAVAILABLE<")
    values = parse_mtconnect_current(document)
    assert values["bearing_temp_id"] == "61.5" and values["bearing_temp"] == "61.5"
    assert values["spindle_speed_id"] == "UNAVAILABLE"
    assert values["cond"] == "Fault" and values["execution"] == "ACTIVE"


def test_mtconnect_source_reads_the_agent(fake_agent: str) -> None:
    """Rows carry every mapped variable; a stopped agent or unknown tags give clear errors."""
    source = MTConnectSource(fake_agent, TAG_MAP)
    rows = [source.step()[0] for _ in range(3)]
    assert [row["step"] for row in rows] == [0, 1, 2]
    assert all(np.isfinite(rows[-1][name]) for name in MEASURED_VARIABLES)
    assert rows[-1]["machine_running"] == 1 and rows[-1]["failure"] == 0
    info = source.describe()
    assert info.kind == "mtconnect" and not info.has_simulator and not info.simulated_data
    with pytest.raises(ValueError, match="unknown model variables"):
        MTConnectSource(fake_agent, {"warp_drive": "x"})
    with pytest.raises(ConnectionError, match="Cannot read the MTConnect agent"):
        MTConnectSource("http://127.0.0.1:9", TAG_MAP, timeout_seconds=0.5).step()


def test_replay_source_streams_a_log() -> None:
    """Replay returns the file's rows in order and starts again at the end."""
    source = ReplaySource(SAMPLE_LOG)
    first, _ = source.step()
    assert first["bearing_temp"] == pytest.approx(float(source.frame.iloc[0]["bearing_temp"]))
    for _ in range(len(source.frame) - 1):
        source.step()
    wrapped, _ = source.step()
    assert wrapped["bearing_temp"] == pytest.approx(first["bearing_temp"]) and wrapped["step"] == len(source.frame)
    assert source.describe().simulated_data  # the samples are simulated, and labelled so


def test_api_on_a_real_machine_source(fake_agent: str) -> None:
    """With MTConnect: live risk without ground truth, and simulator-only actions refused clearly."""
    runtime = RuntimeSettings(
        cors_origins=("http://localhost:3000",),
        cors_origin_regex=None,
        autostart=False,
        discovery_jobs=1,
        allow_benchmark_run=False,
        data_source="mtconnect",
        mtconnect_url=fake_agent,
        tag_map=TAG_MAP,
    )
    app = create_app(models=train_models(), autostart=False, runtime=runtime)
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["data_source"] == "mtconnect" and health["simulated"] is False
        client.post("/sim/control", json={"action": "step", "value": 8})
        status = client.get("/metrics").json()["status"]
        assert status["data_source"]["kind"] == "mtconnect" and status["active_faults"] == []
        assert status["simulated"] is False
        points = client.get("/risk/history").json()["points"]
        assert points and points[-1]["twin_risk"] is not None and points[-1]["true_risk_within_horizon"] is None
        what_if = client.post("/whatif", json={"interventions": [{"variable": "coolant_flow", "value": 25.0}]})
        assert what_if.status_code == 200
        for method, path, body in (
            ("post", "/apply", None),
            ("post", "/inject-fault", {"fault": "lube_leak"}),
            ("post", "/scenario", {"scenario": "overload"}),
            ("post", "/demo/start", None),
        ):
            response = getattr(client, method)(path, json=body)
            assert response.status_code == 409 and "advisory" in response.json()["detail"], path
