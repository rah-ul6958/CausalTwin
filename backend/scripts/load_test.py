"""Load test: many simultaneous viewers against a running backend; writes data/load_test.json.

Each simulated viewer behaves like a browser tab of the web app: it sends its own session id, keeps
a WebSocket to /ws/stream open, polls /metrics every 3 seconds, and now and then runs a what-if or
asks for a recommendation (the heavy requests). The server gives the first viewers private
simulations (up to its CAUSALTWIN_MAX_SESSIONS limit); the others share one. The test reports latency
percentiles per request type, errors (503 "busy" answers are counted separately: they are the
designed response to overload), WebSocket messages received, how many viewers got a private
simulation, and whether each simulation kept its pace.

    python scripts/load_test.py --viewers 25 --seconds 60 [--url http://localhost:8000] [--shared]

`--shared` sends no session ids, so every viewer shares one simulation (the behaviour before
per-viewer simulations). Start the backend first. The test changes nothing except asking for
recommendations.
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

from causaltwin.config import DATA_DIR, SESSION_HEADER, SESSION_QUERY_PARAMETER  # noqa: E402

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
        # Per viewer: the first and last live status it saw (time, step, run id, private or shared).
        self.first_status: dict[int, dict[str, Any]] = {}
        self.last_status: dict[int, dict[str, Any]] = {}

    def record(self, name: str, seconds: float, status: str) -> None:
        """One request's duration and outcome."""
        self.latencies.setdefault(name, []).append(seconds)
        counts = self.statuses.setdefault(name, {})
        counts[status] = counts.get(status, 0) + 1


async def _timed(
    client: httpx.AsyncClient, results: Results, name: str, method: str, path: str, **kwargs: Any
) -> httpx.Response | None:
    """Send one request, record how long it took and how it ended, and return the response."""
    start = time.perf_counter()
    response: httpx.Response | None = None
    try:
        response = await client.request(method, path, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
        status = str(response.status_code)
    except httpx.HTTPError as error:
        status = type(error).__name__
    results.record(name, time.perf_counter() - start, status)
    return response


def _remember_status(results: Results, viewer: int, response: httpx.Response | None) -> None:
    """Keep the viewer's first and latest live status (to measure each simulation's pace)."""
    if response is None or response.status_code != 200:
        return
    status = response.json()["status"]
    session = status.get("session") or {}
    record = {
        "time": time.perf_counter(),
        "step": status["step"],
        "run_id": status["run_id"],
        "private": bool(session.get("private")),
    }
    results.first_status.setdefault(viewer, record)
    results.last_status[viewer] = record


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


async def _viewer(
    base_url: str,
    ws_url: str,
    results: Results,
    stop: asyncio.Event,
    rng: random.Random,
    viewer: int,
    session_id: str | None,
) -> None:
    """One browser tab: a live stream, status polling, and occasional heavy requests."""
    headers = {} if session_id is None else {SESSION_HEADER: session_id}
    if session_id is not None:
        ws_url = f"{ws_url}?{SESSION_QUERY_PARAMETER}={session_id}"
    listener = asyncio.create_task(_listen(ws_url, results, stop))
    async with httpx.AsyncClient(base_url=base_url, headers=headers) as client:
        await asyncio.sleep(rng.uniform(0, STATUS_POLL_SECONDS))  # tabs are not opened in lockstep
        while not stop.is_set():
            response = await _timed(client, results, "GET /metrics", "GET", "/metrics")
            _remember_status(results, viewer, response)
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


def _pace(results: Results) -> dict[str, Any]:
    """Steps per second of each simulation the viewers watched (private ones and the shared one)."""
    private: list[float] = []
    shared: list[float] = []
    for viewer, first in results.first_status.items():
        last = results.last_status[viewer]
        elapsed = last["time"] - first["time"]
        if elapsed <= 0 or last["run_id"] != first["run_id"] or last["private"] != first["private"]:
            continue  # too short, or the simulation was replaced during the run
        (private if last["private"] else shared).append((last["step"] - first["step"]) / elapsed)
    return {
        "private_viewers": sum(1 for record in results.last_status.values() if record["private"]),
        "shared_viewers": sum(1 for record in results.last_status.values() if not record["private"]),
        "private_steps_per_second": [round(value, 2) for value in sorted(private)],
        "shared_steps_per_second": round(statistics.median(shared), 2) if shared else None,
    }


async def run(base_url: str, viewers: int, seconds: float, shared: bool = False) -> dict[str, Any]:
    """Run the viewers for the given time and summarize."""
    ws_url = base_url.replace("http", "ws", 1).rstrip("/") + "/ws/stream"
    results = Results()
    async with httpx.AsyncClient(base_url=base_url) as client:
        before = (await client.get("/health", timeout=10)).json()
        await client.post("/sim/control", json={"action": "resume"}, timeout=10)  # the shared simulation
        max_sessions = (await client.get("/metrics", timeout=10)).json()["status"]["session"]["max_sessions"]
    stop = asyncio.Event()
    rng = random.Random(LOAD_TEST_SEED)
    tasks = [
        asyncio.create_task(
            _viewer(
                base_url,
                ws_url,
                results,
                stop,
                random.Random(rng.random()),
                viewer,
                None if shared else f"load-viewer-{viewer:04d}-{rng.getrandbits(32):08x}",
            )
        )
        for viewer in range(viewers)
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
        "session_ids": not shared,
        "max_sessions": max_sessions,
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
        "simulations": _pace(results),
    }


def main() -> None:
    """Parse arguments, run, print and save."""
    parser = argparse.ArgumentParser(description="Load test with simulated viewers.")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--viewers", type=int, default=25)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--output", default=str(LOAD_TEST_PATH))
    parser.add_argument("--shared", action="store_true", help="send no session ids: every viewer shares one")
    arguments = parser.parse_args()
    summary = asyncio.run(run(arguments.url, arguments.viewers, arguments.seconds, arguments.shared))
    Path(arguments.output).write_bytes((json.dumps(summary, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
