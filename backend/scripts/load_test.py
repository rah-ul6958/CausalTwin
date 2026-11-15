"""Load test: many simultaneous viewers against a running backend; writes data/load_test.json.

Each simulated viewer behaves like the web app: it keeps a WebSocket to /ws/stream open, polls
/metrics every 3 seconds, and now and then runs a what-if or asks for a recommendation (the heavy
requests). The test reports latency percentiles per request type, errors (503 "busy" answers are
counted separately: they are the designed response to overload), WebSocket messages received, and
whether the live simulation kept its pace.

    python scripts/load_test.py --viewers 25 --seconds 60 [--url http://localhost:8000]

Start the backend first. The test changes nothing except asking for recommendations.
"""

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from causaltwin.config import DATA_DIR  # noqa: E402

LOAD_TEST_PATH = DATA_DIR / "load_test.json"
STATUS_POLL_SECONDS = 3.0
HEAVY_REQUEST_PROBABILITY = 0.15  # per poll cycle and viewer
REQUEST_TIMEOUT_SECONDS = 40.0
LOAD_TEST_SEED = 17


class Results:
    """Latencies, status codes and message counts collected by all viewers."""

    def __init__(self) -> None:
        """Start empty."""
        self.latencies: dict[str, list[float]] = {}
        self.statuses: dict[str, dict[str, int]] = {}
        self.websocket_messages = 0
        self.websocket_failures = 0

    def record(self, name: str, seconds: float, status: str) -> None:
        """One request's duration and outcome."""
        self.latencies.setdefault(name, []).append(seconds)
        counts = self.statuses.setdefault(name, {})
        counts[status] = counts.get(status, 0) + 1


async def _timed(client: httpx.AsyncClient, results: Results, name: str, method: str, path: str, **kwargs: Any) -> None:
    """Send one request and record how long it took and how it ended."""
    start = time.perf_counter()
    try:
        response = await client.request(method, path, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
        status = str(response.status_code)
    except httpx.HTTPError as error:
        status = type(error).__name__
    results.record(name, time.perf_counter() - start, status)


async def _listen(url: str, results: Results, stop: asyncio.Event) -> None:
    """Hold a WebSocket open and count the messages that arrive."""
    try:
        async with websockets.connect(url, max_size=None) as socket:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(socket.recv(), timeout=1.0)
                    results.websocket_messages += 1
                except TimeoutError:
                    continue
    except (OSError, websockets.WebSocketException):
        results.websocket_failures += 1


async def _viewer(base_url: str, ws_url: str, results: Results, stop: asyncio.Event, rng: random.Random) -> None:
    """One browser tab: a live stream, status polling, and occasional heavy requests."""
    listener = asyncio.create_task(_listen(ws_url, results, stop))
    async with httpx.AsyncClient(base_url=base_url) as client:
        await asyncio.sleep(rng.uniform(0, STATUS_POLL_SECONDS))  # tabs are not opened in lockstep
        while not stop.is_set():
            await _timed(client, results, "GET /metrics", "GET", "/metrics")
            if rng.random() < HEAVY_REQUEST_PROBABILITY:
                if rng.random() < 0.5:
                    body = {"interventions": [{"variable": "coolant_flow", "value": rng.uniform(15, 28)}]}
                    await _timed(client, results, "POST /whatif", "POST", "/whatif", json=body)
                else:
                    await _timed(client, results, "GET /recommend", "GET", "/recommend")
            await asyncio.sleep(STATUS_POLL_SECONDS)
    await listener


def _percentile(values: list[float], share: float) -> float:
    """Simple percentile of a list of seconds, in milliseconds."""
    ordered = sorted(values)
    return 1000 * ordered[min(len(ordered) - 1, int(share * len(ordered)))]


async def run(base_url: str, viewers: int, seconds: float) -> dict[str, Any]:
    """Run the viewers for the given time and summarize."""
    ws_url = base_url.replace("http", "ws", 1).rstrip("/") + "/ws/stream"
    results = Results()
    async with httpx.AsyncClient(base_url=base_url) as client:
        before = (await client.get("/health", timeout=10)).json()
        await client.post("/sim/control", json={"action": "resume"}, timeout=10)
    stop = asyncio.Event()
    rng = random.Random(LOAD_TEST_SEED)
    tasks = [
        asyncio.create_task(_viewer(base_url, ws_url, results, stop, random.Random(rng.random())))
        for _ in range(viewers)
    ]
    started = time.perf_counter()
    await asyncio.sleep(seconds)
    stop.set()
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - started
    async with httpx.AsyncClient(base_url=base_url) as client:
        after = (await client.get("/health", timeout=10)).json()
        speed = (await client.get("/metrics", timeout=10)).json()["status"]["steps_per_second"]
    requests = {
        name: {
            "count": len(values),
            "median_ms": round(1000 * statistics.median(values), 1),
            "p95_ms": round(_percentile(values, 0.95), 1),
            "max_ms": round(1000 * max(values), 1),
            "statuses": results.statuses[name],
        }
        for name, values in sorted(results.latencies.items())
    }
    steps = after["step"] - before["step"]
    return {
        "source": "scripts/load_test.py",
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_url": base_url,
        "viewers": viewers,
        "seconds": round(elapsed, 1),
        "requests": requests,
        "websocket": {
            "messages": results.websocket_messages,
            "messages_per_viewer_per_second": round(results.websocket_messages / max(viewers * elapsed, 1e-9), 1),
            "failed_connections": results.websocket_failures,
        },
        "live_loop": {
            "steps": steps,
            "steps_per_second": round(steps / elapsed, 2),
            "target_steps_per_second": speed,
        },
    }


def main() -> None:
    """Parse arguments, run, print and save."""
    parser = argparse.ArgumentParser(description="Load test with simulated viewers.")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--viewers", type=int, default=25)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--output", default=str(LOAD_TEST_PATH))
    arguments = parser.parse_args()
    summary = asyncio.run(run(arguments.url, arguments.viewers, arguments.seconds))
    Path(arguments.output).write_bytes((json.dumps(summary, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
