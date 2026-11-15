"""A stand-in MTConnect agent for testing the connector without a machine. TEST TOOL ONLY.

It answers GET /current with an MTConnect Streams document whose values come from the CausalTwin
SIMULATOR (one simulator step per request). It exists to exercise the MTConnect path end to end;
the data it serves is simulated, even though the backend will show it as an MTConnect source.

    python scripts/fake_mtconnect_agent.py --port 5000

Then start the backend with (bash):
    CAUSALTWIN_DATA_SOURCE=mtconnect CAUSALTWIN_MTCONNECT_URL=http://127.0.0.1:5000 \\
    CAUSALTWIN_TAG_MAP="$(python scripts/fake_mtconnect_agent.py --print-tag-map)" \\
    uvicorn causaltwin.api.app:app --port 8000
"""

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from causaltwin.config import MEASURED_VARIABLES  # noqa: E402
from causaltwin.intelligence.drift import SimulatorStepper  # noqa: E402
from causaltwin.simulator import build_simulator  # noqa: E402

TAG_MAP = {name: f"{name}_id" for name in MEASURED_VARIABLES} | {"machine_running": "execution"}
AGENT_SEED = 21


def streams_document(row: dict[str, float]) -> bytes:
    """One MTConnect Streams document with every model variable as a sample."""
    samples = "".join(
        f'<Sample dataItemId="{name}_id" name="{name}" sequence="{index}">{row[name]:.4f}</Sample>'
        for index, name in enumerate(MEASURED_VARIABLES)
    )
    execution = "ACTIVE" if row["machine_running"] == 1 else "STOPPED"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<MTConnectStreams xmlns="urn:mtconnect.org:MTConnectStreams:1.3">'
        '<Header instanceId="1" version="1.3" sender="causaltwin-test-agent"/>'
        '<Streams><DeviceStream name="TestSpindle" uuid="causaltwin-test">'
        f'<ComponentStream component="Rotary" componentId="c1"><Samples>{samples}</Samples>'
        f'<Events><Execution dataItemId="execution">{execution}</Execution></Events></ComponentStream>'
        "</DeviceStream></Streams></MTConnectStreams>"
    ).encode()


def main() -> None:
    """Serve until interrupted, or print the matching tag map."""
    parser = argparse.ArgumentParser(description="Stand-in MTConnect agent backed by the simulator (test tool).")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--print-tag-map", action="store_true", help="print the CAUSALTWIN_TAG_MAP JSON and exit")
    arguments = parser.parse_args()
    if arguments.print_tag_map:
        print(json.dumps(TAG_MAP))
        return
    stepper = SimulatorStepper(build_simulator("normal", n_steps=200_000, seed=AGENT_SEED))
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        """Answers /current; anything else is 404."""

        def do_GET(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
            if not self.path.rstrip("/").endswith("/current"):
                self.send_error(404)
                return
            with lock:
                row, _ = stepper.step()
            body = streams_document(row)
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", arguments.port), Handler)
    print(f"Test MTConnect agent (simulated data) on http://127.0.0.1:{arguments.port}/current")
    server.serve_forever()


if __name__ == "__main__":
    main()
