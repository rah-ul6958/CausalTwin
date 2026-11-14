# Phase 3 Report - Intelligence Layer and API

All data is **simulated**. Benchmark and drift results are judged by **simulator ground truth**
(forks of the real simulator), which the twin itself never sees. Costs are **illustrative
units**, not money. Every number below comes from `scripts/run_benchmark.py --drift`
(`data/benchmark_results.json`, `data/drift_results.json`), `scripts/run_validation.py`
(`data/validation_results.json`), or the test suite.

## What was built

| Module | Responsibility |
|---|---|
| `intelligence/optimizer.py` | Cost-aware recommender. Searches about 870 candidates: single levers -30% to +30% in 2% steps, lever pairs in 5% steps, and a tool change. Returns the full ranked table, ineffective levers, confidence components, time-to-effect and a plain-English explanation |
| `engine/whatif.py` (extended) | Batched evaluator that scores every candidate in one vectorized pass (shared noise, variable-major 32-bit arrays), plus the new `reset` intervention type (set once, then let dynamics run, as for a tool change) |
| `intelligence/baseline.py` | XGBoost (depth 3, 200 trees) predicting failure within 30 steps, SHAP TreeExplainer, and the documented mapping from "top SHAP feature" to an engineer's action |
| `intelligence/benchmark.py` | The Correlation Trap benchmark: 30 high-risk situations, both methods applied in the real simulator, every case reported, with progress callbacks |
| `intelligence/drift.py` | CUSUM drift monitor on one-step-ahead errors, then collection of 600 post-change steps, re-discovery, graph diff with bootstrap intervals, rewiring and refitting, and invalidated recommendations. Also the evaluation helpers |
| `intelligence/training.py` | Trains the engine and baseline once and caches them on disk (`data/model_cache.pkl`, keyed by a fingerprint of the relevant source files) |
| `api/live.py` | Live loop (asyncio, default 5 steps/s, pause/resume/speed) and the 8-beat guided demo |
| `api/hub.py` | Thread-safe WebSocket hub: bounded queue per client, batched log messages |
| `api/routes.py`, `api/schemas.py`, `api/app.py` | FastAPI app. Every request and response is a Pydantic model; every computed result carries a `provenance` block; CORS comes from `CAUSALTWIN_CORS_ORIGINS` |

Simulator and engine additions for this phase:
- `MachineSimulator.replace_tool()`.
- `CausalTwinEngine.update_structure()`, which refits only the changed equations.
- Pickle support for the engine.
- An optional "random walk" model for exogenous variables in what-if (not the default; see below).

## How to run

```bash
cd backend
python scripts/run_benchmark.py --drift            # benchmark + drift evaluation (about 2-3 min)
uvicorn causaltwin.api.app:app --port 8000         # API; docs at http://localhost:8000/docs
CAUSALTWIN_AUTOSTART=0 uvicorn causaltwin.api.app:app   # start with the live loop paused
python -m pytest -q                                # 66 tests, about 95 s
```

The first start trains the models, which takes about 25 s, and caches them. Later starts load
the cache in about 0.2 s and spend about 3 s starting the discovery worker processes.

## Test results

`python -m pytest -q`: **66 passed** in about 93 s (Phase 1: 31, Phase 2: 15, Phase 3: 20).
`ruff check` and `mypy` (51 files) are clean.

Phase 3 tests:
- The batched evaluator equals the single what-if.
- The optimizer stays in range, under 1 s, never acts on vibration, lists vibration as
  ineffective, and returns "no action" when risk is low.
- The baseline has AUC > 0.8 and still blames vibration at high-risk states.
- The benchmark runs end to end.
- Drift fires for each fault with the correct structural diagnosis.
- No structural change is wrongly confirmed in 20,000 normal steps.
- A tool-change what-if lowers risk.
- API tests cover every endpoint (including error cases), the WebSocket stream, BYOD in both
  modes, a background benchmark with WebSocket progress, and the full scripted demo through
  the API alone.

## Benchmark: the Correlation Trap

30 high-risk situations, 6 per scenario on different seeds. Each has a true risk of failure
within 5 minutes of at least 15%, measured in the real simulator. Each method's action is
applied to forks of the real simulator from the same starting state with the same 8 seeds.
Success means the true risk after the action is at or below the 10% target. The full run takes
**40-54 s** with cached models (budget 60 s). Including first-time training it would take about
75 s, which exceeds the budget.

| Method | Success rate | Mean true risk after (before: 30.4%) | Mean risk reduction | Failures within 5 min (8 futures x 30 cases) | Mean cost |
|---|---|---|---|---|---|
| **CausalTwin** | **93% (28/30)** | **3.7%** | 0.267 | **0%** | 6.6 |
| Standard ML (XGBoost + SHAP) | 3% (1/30) | 30.1% | 0.003 | 24% | 2.5 |
| No action | - | 30.4% | - | 23% | 0 |

Wins: CausalTwin 29, tie 1, baseline 0.

What each method blamed (top root-cause path for CausalTwin; top SHAP feature for the baseline):

| CausalTwin | Count | Baseline | Count |
|---|---|---|---|
| tool_wear | 23 | **vibration** | **22** |
| ambient_temp -> coolant_temp -> bearing_temp | 7 | ambient_temp (no action possible) | 6 |
| | | lube_rate | 1 |
| | | tool_wear | 1 |

**The trap in numbers.** The baseline is a competent predictor: held-out AUC **0.91**, with
precision 0.30 and recall 0.32 at its best-F1 threshold, against a positive rate of 4.3%,
trained in about 7 s. Yet in 22 of 30 high-risk states it blames vibration. "Damping
vibration" leaves the true risk exactly where it was (for example 0.200 -> 0.200, 0.460 ->
0.460), because vibration has no causal path to failure. CausalTwin lists vibration as an
ineffective lever and acts on the actual causes: a tool change in 19 cases, and
coolant/lube/speed changes in 11.

**Where CausalTwin falls short.**
- **Two misses:** case 28 ended at a true risk of 12.3% and case 29 at 11.2%, just above
  target, both with lever changes. They still beat the baseline.
- **It spends more:** 6.6 cost units on average against the baseline's 2.5, partly because a
  tool change (8 units) is often the cheapest action that reliably reaches the target.
- **The one tie:** case 13, where the baseline blamed tool wear and changed the tool, exactly
  as CausalTwin did.

**How the optimizer got here, measured honestly.** The first version predicted every chosen
action would meet the 10% target, yet only **60%** did in the simulator. Misses landed at 10.7%
to 15%. Two causes and fixes:
1. **The winner's curse.** Choosing the cheapest of about 900 noisy 100-rollout estimates that
   pass a threshold favours estimates that came out too low. The top 5 are now re-checked with
   **independent** noise (200 rollouts).
2. **A planning margin.** The search ranks against 80% of the target, while success is still
   judged at 10%.

I also tried letting non-intervened levers follow a random walk of their historical one-step
changes. It made things **worse** (success 27%). A calibration check on the 30 situations
showed why: with constant levers the predicted no-action risk is well calibrated (mean 0.297
against a true 0.304, correlation 0.95), while the random walk is biased low (0.184). Operator
adjustments revert to a setpoint rather than accumulate, and clipping at range edges only lets
the walk drift toward safer values. So "constant" remains the default, and the walk is a
documented option.

Optimizer timing: one recommendation search over 761-871 candidates takes **0.57-0.69 s**
(budget 1 s), logged as `recommend`.

## Causal drift detection

Each equation's one-step-ahead error is standardized, clipped at 3 (so a single tool chip
cannot raise an alarm), and run through a two-sided CUSUM (k = 0.5, h = 10). An alarm starts
collection of 600 post-change steps. Re-discovery then compares coefficients against their
bootstrap intervals and requires at least a 30% change. Faults were injected after 3,000
normal steps, using 3 seeds per fault.

| Fault | Detection delay (steps; 1 step = 10 s) | Rewired after (steps) | Diagnosis (seed 11) |
|---|---|---|---|
| coolant_filter_clog | 2, 4, 3 | 602, 604, 603 | "coolant_flow no longer strongly controls coolant_temp: its effect fell from -0.134 to -0.033 (75% weaker)" (truth: 20% of normal) |
| lube_leak | 3, 3, 3 | 603, 603, 603 | "lube_rate no longer affects bearing_temp (effect -2.28 -> +0.212)" |
| bearing_damage | 3, 3, 3 | 603, **1210**, 603 | "motor_current now drives bearing_temp more strongly: +0.144 -> +0.245 (70% stronger)" (truth: 1.6x) |

The rewiring delay is the 600-step collection window plus 2 to 4 steps. For bearing damage,
seed 12, the first re-discovery found no significant change, so the model was kept and
monitoring resumed. The second cycle confirmed the change, at 1,210 steps.

**False alarms** in fault-free normal runs of 20,000 steps (about 55 hours each):

| Seed | Suspected alarms | Wrongly confirmed structural changes |
|---|---|---|
| 11 | 1 | 0 |
| 12 | 2 | 0 |
| 13 | 2 | 0 |

That is roughly one CUSUM alarm per 12,000 steps (about 33 hours of operation). In every case
re-discovery correctly concluded "no structural change" and kept the model.

After a confirmed change, only the changed equations are refitted. Recommendations whose
causal path used a changed edge are listed as invalid.

## Live loop, demo, and validation channel

- **Each live step:**
  - Append data and stream it.
  - Estimate the twin's risk (50 rollouts).
  - Run the drift check.
  - Refresh the root cause every 50 steps.
  - Compute ground-truth risk every 10 steps.
  - Raise alerts when risk crosses 10% and when drift is suspected or confirmed.
- **Ground truth** travels in `risk_update.data.validation`, labelled "Simulator ground truth
  (validation only; not used by the twin)".
- **The guided demo** (`/demo/start`, `/demo/next`; fixed seed 777) pauses after each beat.
  One run, through the API:

| Beat | What happened |
|---|---|
| 1 normal operation | Predicted risk 0.0% |
| 2 risk rises | Hot afternoon (40 deg C) and an aggressive job with a fresh tool: twin risk 37.9% (ground truth 50.5%) |
| 3 recommendation | Reduce load_pct 96 -> 67 and spindle_speed 4300 -> 3010: predicted 36.9% -> 7.9%. Standard ML blames bearing_temp and suggests +20% coolant |
| 4 apply / 5 risk drops | Twin risk 2.4%, then 5.0% (ground truth 2.9%, then 6.0%) |
| 6 fault injected | The job ends, then the coolant filter clogs; within a few steps, a CUSUM alarm on the coolant_temp equation (CUSUM 10.6 > 10) |
| 7 drift alert | "coolant_flow no longer affects coolant_temp (effect -0.134 -> -0.018)"; graph rewired |
| 8 new recommendation | Aggressive job again on the changed machine: spindle_speed -20%, lube_rate +20% (coolant is no longer used) |

The demo is honest but scripted. In this simulator, high thermal risk at normal ambient
temperature only comes with a worn tool. So the "risk rises" beat uses a hot afternoon
(ambient forced to 40 deg C) and a fresh tool. Otherwise the cheapest fix would always be a
tool change. With default operator behaviour the optimizer often picks tool changes, as the
benchmark shows.

## API

All 24 REST endpoints from the brief, plus the WebSocket, are documented at `/docs`.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Status (`simulated: true`) |
| `GET /graph`, `GET /graph/correlation` | Causal graph with bootstrap confidence; correlation graph |
| `GET /graph/edge/{cause}/{effect}` | Lag, strength, p-value, bootstrap frequency, 400 paired points |
| `POST /whatif` | Interventions from the live state (409 while the machine is stopped) |
| `GET /root-cause` | Ranked causal paths and symptoms |
| `GET /recommend?target=0.1` | Recommendation, full candidate table, ineffective levers, and the baseline's action for contrast |
| `POST /apply` | Apply the latest recommendation to the live simulator |
| `POST /inject-fault`, `POST /clear-fault` | Structural faults |
| `POST /scenario` | Switch scenario without resetting the machine |
| `POST /sim/control` | `pause`, `resume`, `speed`, `step` (n), `reset`, `release` (all interventions) |
| `POST /demo/start`, `POST /demo/next` | Guided demo |
| `GET /benchmark`, `POST /benchmark/run` | Cached results; background run (`?situations_per_scenario=` gives an uncached quick run) with `benchmark_progress` and `benchmark_done` messages |
| `GET /validation`, `GET /model-cards`, `GET /metrics`, `GET /logs` | Proof, equations, timings and counts, activity log |
| `GET /data/recent`, `GET /data/export`, `GET /data/quality` | Live buffer (simulated), CSV download, quality report |
| `POST /byod/upload` | CSV + roles JSON (+ optional time column). Time-series or tabular analysis; `ground_truth_available: false` and a note saying no ground truth exists |
| `WS /ws/stream` | `hello`, `sensor_frame` (at most 10/s), `risk_update` (with a separate `validation` field), `event`, `alert`, `log` (batched every 0.5 s), `graph_update`, plus `demo`, `benchmark_progress` and `benchmark_done` |

## Timings against the budget

| Operation | Measured | Budget |
|---|---|---|
| Recommendation search (about 870 candidates, plus top-5 re-check) | 0.57-0.69 s | < 1 s |
| Baseline XGBoost training (evaluation fit + final fit) | 7.2-7.6 s | < 10 s |
| Full benchmark (30 cases, cached models) | 40-54 s | < 60 s |
| What-if, 200 rollouts | 14-31 ms | < 200 ms |
| Discovery on 3,000 steps (warm workers) | 1.0-2.3 s | < 3 s |
| Live risk estimate per step (50 rollouts) | 25 ms (median of 20) | - (a step lasts 200 ms at 5 steps/s) |
| Drift check per step | 0.12 ms | - |
| Re-discovery on 600 steps (with bootstrap) | 1.3 s | - |

Timings vary up to about 3x on this laptop because of CPU throttling (see the Phase 2 report).

## Stretch goal (AI4I 2020)

**Not attempted.** As the brief requires, it is left out rather than shipped half done.

## Known limitations

- **The demo's thermal scenario needs a hot ambient.** In this simulator, extreme lever
  settings alone keep the bearing near 80 deg C, so most real high-risk states are wear-driven
  and the optimizer often recommends a tool change.
- **The baseline's action mapping is a modelling choice:** vibration -> damping, hot bearing
  or coolant -> more coolant, current -> less load, wear -> tool change, ambient -> none. A
  different mapping would change the baseline's numbers. With the obvious "hot bearing ->
  more coolant" response, the baseline does well whenever SHAP blames bearing_temp; its losses
  come from blaming vibration or ambient temperature.
- **The planning margin trades cost for reliability** (mean cost 6.6 instead of 5.3), and two
  of 30 cases still miss the target.
- **Risk is under-predicted in some states.** For example, the demo's hot afternoon: twin
  37.9% against a true 50.5%. The ambient of 40 deg C is at the edge of the training data.
- **Rediscovery can only judge causes that vary.** It needs 600 post-change steps, and causes
  held constant by a do-intervention cannot be assessed, so they keep their old coefficient
  and are not reported. One of nine fault runs needed a second detection cycle.
- **The live loop uses one engine copy and one simulator per server process.** It is not
  designed for many concurrent users. Recommendations and what-if computations run in worker
  threads on a snapshot of the latest data.
- **Benchmark costs and cost-per-percent values are illustrative.** The benchmark covers this
  simulator only.
