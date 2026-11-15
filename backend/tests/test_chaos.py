"""Concurrency chaos test: the background loop runs fast while several threads inject faults,
switch scenarios, step, reset, ask for advice, apply it and play demo beats, all at once.

Before the live loop had a lock, these actions could interleave with a running step. The
test passes when no action fails unexpectedly, the background loop never trips, and the
machine keeps stepping afterwards.
"""

import asyncio
import random
import threading
import time
import warnings
from collections.abc import Callable

from causaltwin.api.hub import StreamHub
from causaltwin.api.live import DemoScript, LiveLoop
from causaltwin.config import FAULT_NAMES
from causaltwin.engine.schemas import Intervention
from causaltwin.intelligence.training import train_models
from causaltwin.simulator import SCENARIOS

warnings.filterwarnings("ignore")

N_THREADS = 4
ACTIONS_PER_THREAD = 40
# Messages that are correct refusals, not failures (for example no recommendation to apply yet).
EXPECTED_REFUSALS = ("No recommendation to apply", "no risk estimate")


def _start_background_loop(live: LiveLoop) -> tuple[Callable[[], None], threading.Thread]:
    """Run live.run_forever in its own event loop thread, as the API server does.

    Returns a function that stops the loop cleanly, and the thread.
    """
    event_loop = asyncio.new_event_loop()
    live.hub.bind(event_loop)
    task = event_loop.create_task(live.run_forever())

    def run() -> None:
        asyncio.set_event_loop(event_loop)
        try:
            event_loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        finally:
            event_loop.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    def stop() -> None:
        event_loop.call_soon_threadsafe(task.cancel)

    return stop, thread


def _actions(live: LiveLoop, demo: DemoScript, rng: random.Random) -> list[Callable[[], object]]:
    """Every kind of state change the API can trigger."""

    def what_if() -> object:
        with live.lock:
            if not live.history_is_usable():
                return None
            return live.engine.what_if([Intervention(variable="coolant_flow", value=25.0)], live.history())

    def recommend_and_apply() -> object:
        with live.lock:
            if not live.history_is_usable():
                return None
            live.compute_recommendation()
            return live.apply_recommendation()

    def demo_beat() -> object:
        return demo.start() if demo.position < 0 or demo.position >= 2 else demo.next()

    return [
        lambda: live.set_fault(rng.choice(FAULT_NAMES), True),
        lambda: live.set_fault(rng.choice(FAULT_NAMES), False),
        lambda: live.switch_scenario(rng.choice(list(SCENARIOS))),
        lambda: live.advance(rng.randint(1, 25), fast=True),
        lambda: live.release_all(),
        what_if,
        recommend_and_apply,
        lambda: setattr(live, "running", rng.random() < 0.8),
        lambda: live.reset(rng.randint(1, 50), rng.choice(list(SCENARIOS))) if rng.random() < 0.3 else None,
        demo_beat if rng.random() < 0.3 else what_if,
    ]


def test_concurrent_actions_never_break_the_live_loop() -> None:
    """Random concurrent actions from several threads: no unexpected errors, loop still steps."""
    models = train_models()
    models.engine.settings.discovery_n_jobs = 1
    live = LiveLoop(models, StreamHub())
    demo = DemoScript(live)
    live.steps_per_second = 100
    live.running = True
    stop_loop, loop_thread = _start_background_loop(live)
    failures: list[str] = []
    completed: list[int] = []

    def worker(seed: int) -> None:
        rng = random.Random(seed)
        actions = _actions(live, demo, rng)
        for _ in range(ACTIONS_PER_THREAD):
            action = rng.choice(actions)
            try:
                action()
            except ValueError as error:
                if not any(message in str(error) for message in EXPECTED_REFUSALS):
                    failures.append(f"{type(error).__name__}: {error}")
            except Exception as error:  # noqa: BLE001 - every other failure is a finding
                failures.append(f"{type(error).__name__}: {error}")
            completed.append(seed)
            time.sleep(rng.uniform(0.0, 0.03))

    threads = [threading.Thread(target=worker, args=(seed,)) for seed in range(N_THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads), "a worker thread hung (possible deadlock)"

    live.running = True
    step_before = live.step_count
    time.sleep(0.6)
    step_after = live.step_count
    stop_loop()
    loop_thread.join(timeout=10)

    assert len(completed) == N_THREADS * ACTIONS_PER_THREAD
    assert failures == []
    assert live.loop_errors == 0
    assert step_after > step_before, "the background loop stopped stepping"
