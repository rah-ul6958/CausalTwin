# Phase 1 Report - Foundation and Digital Machine

All data in this phase is **simulated**. All physical constants are **illustrative**
(physically plausible, not taken from any real machine datasheet). Every number in this
report was produced by code in the repository; the commands are listed next to each table.

## What was built

- **Project skeleton** following CLAUDE.md section 6: `backend/` (Python 3.11 venv, pinned
  `requirements.txt`, dev tools in `requirements-dev.txt`), `frontend/` placeholder, `docs/`
  (phase prompts in `docs/prompts/`), GPL-3.0 `LICENSE`, `.gitignore`, README skeleton,
  placeholder `docker-compose.yml`.
- **`causaltwin/config.py`**: single source of truth for every constant.
- **`causaltwin/utils/`**: structured activity logger (`ActivityLogEntry`: timestamp, step
  name, duration in ms, message, details dict, with subscribers for later live streaming) and
  the `timed_step` context manager. Simulation, event alignment, CSV loading and the quality
  report all log their duration.
- **`causaltwin/simulator/`**:
  - `machine.py`: `MachineSimulator(config, seed, profile)` with `step()`, `run()`,
    `intervene()` (do-operator, optionally scheduled, bounds-checked), `release()`,
    `inject_fault()`, `clear_fault()`, `get_true_hazard()` and `get_state()`, plus:
    - `fork(seed, config_overrides, keep_history)`: an independent copy that continues
      from the current state. Without a seed it repeats the original future exactly, for
      paired runs with and without an intervention. With a seed it gives a fresh future,
      for averaging over seeds. Config overrides allow, for example, removing vibration
      noise or disabling failures. Later phases need this to apply recommendations "in the
      real simulator from the same starting state with the same seeds".
    - `set_profile()`: switch scenario mid-run.
    - `failure_probability(hazards)`: the ground-truth probability of at least one failure
      over a horizon.
  - `operator.py`: random setpoint changes (about every 120 steps per lever) plus AR(1)
    small adjustments. New setpoints stay 2 adjustment-spreads inside the range, so values
    are rarely clipped at the range edges.
  - `scenarios.py`: five seeded scenarios, `generate_dataset()`, `run_scenario()` (also
    returns ground truth), and `switch_scenario()`.
  - `true_graph.py`: the true graph, written to `backend/data/true_graph.json`.
  - `random_stream.py`: block-drawn seeded random numbers. The simulator uses a **fixed
    number of random draws per step**, whatever the interventions, faults or config. Two
    runs with the same seed therefore differ only because of the intervention ("common
    random numbers").
- **`causaltwin/ingestion/`**: `align_events` (indicator columns plus
  `steps_since_maintenance`), `load_csv` (roles, sorting, numeric parsing, forward fill with
  counts, validation report), and `data_quality_report` (min, max, mean, std, missing count,
  flatline detection).
- **Scripts** (run from `backend/`):
  - `generate_samples.py`: the true graph, plus sensor and event CSVs per scenario.
  - `trap_report.py`: the trap evidence tables.
  - `identifiability_check.py`: the PCMCI pre-check.

## The machine model in brief

One step is 10 seconds. All effects are lagged by 1 to 2 steps, as in the true graph.
- **Motor current, coolant temperature, bearing temperature and vibration** follow
  first-order dynamics: each moves part of the way toward a target every step, plus process
  noise.
- **Tool wear** accumulates a load- and speed-dependent increment each step, plus occasional
  **micro-chipping**: an 8-minute wear jump with probability 1% per running step.
- **Failure hazard** is `sigmoid(0.25*(bearing_temp - 80) + 0.07*(tool_wear - 120) + 2.0)`.
  A failure stops the machine for 12 steps, then maintenance replaces the tool.
- **Sensor noise** is added to every measured sensor reading. Levers are logged commands,
  so they are recorded exactly.
- **Faults:**
  - `coolant_filter_clog`: coolant flow keeps 20% of its cooling effect.
  - `lube_leak`: lube no longer affects the bearing.
  - `bearing_damage`: 1.6 times the heating per amp of motor current.
- **Scenarios:**
  - `normal`: comfortable lever ranges (load 40-85%, speed 1500-4000 rpm, coolant 12-30 L/min,
    lube 0.7-1.5).
  - `overload`: load and speed floors rise to 90% and 3600 rpm.
  - `cooling_failure`: the coolant ceiling drops to 10 L/min, then the filter clogs halfway.
  - `heat_wave`: up to +14 deg C ambient.
  - `tool_wear`: tools are replaced only at 400 to 600 minutes of wear.

## Refinement pass: what changed after checking identifiability, and why

The first version passed its own tests, but checks against what Phases 2 and 3 require
exposed real problems. Each fix below was verified by measurement.

| Problem found | Evidence | Fix |
|---|---|---|
| Sensor noise created spurious lagged edges | Regressing bearing_temp on its true parents plus extra lags: extra lags were significant with measured data (for example bearing_temp lag 2, t = 5.8) but not with true states | Sensor noise kept small relative to process noise (for example bearing 0.05 vs 0.4). Afterwards no extra lag has \|t\| > 1.5 |
| Tool-wear edges were invisible | Wear sensor noise (2 min) was 12 times larger than the per-step increment (0.17 min) | Wear is read from a load-weighted cutting-time counter, so noise is 0.005 min |
| tool_wear -> vibration was undetectable | A deterministic accumulator has no innovations once its own past is conditioned on | Tool micro-chipping (random wear jumps) |
| tool_wear -> failure not significant | Pooled logistic z = 1.5 | Wear term strengthened (0.07 per min, threshold 120). Pooled p = 4e-9 |
| Weak trap for a 30-step-ahead model | XGBoost ranked vibration 8th of 10 | Vibration made a stronger summary of speed, load and wear. It now has the highest single-variable AUC (table below) |
| Too many failures in normal operation | 17 per 20,000 steps | Normal operator avoids lever extremes. Now 5 per 20,000 steps |
| Lever values often clipped at range edges | 2-5% of steps at an edge, constant runs up to 22 steps | Setpoints drawn with an edge margin |
| Missing capabilities for later phases | Phase 2 and 3 need "same starting state, several seeds" and mid-run scenario switching | `fork()`, `switch_scenario()`, `failure_probability()`, bounds-checked `intervene()` |

## How to run

```bash
cd backend
python3.11 -m venv .venv
.venv/Scripts/activate              # Windows (use .venv/bin/activate on Linux/macOS)
pip install -r requirements-dev.txt # runtime plus ruff and mypy

python -m pytest -q -s                      # all tests, with measured values printed
python -m ruff check . && python -m mypy    # lint and type check
python scripts/generate_samples.py          # true_graph.json and sample CSVs
python scripts/trap_report.py               # trap evidence tables (about 10 s)
python scripts/identifiability_check.py     # PCMCI pre-check (about 25 s)
```

## Test results

`python -m pytest -q`: **31 passed in about 12 s**. `ruff check`: all checks passed.
`mypy`: no issues in 24 files.

| Area | What is checked |
|---|---|
| Determinism | Same seed gives identical sensors and events; a different seed differs |
| Performance | 20,000 steps in 0.29-0.31 s (budget 2 s) |
| Bounds | All variables inside physical bounds; levers inside their range while running; normal scenario stays in its comfortable ranges; 85%+ of normal running steps have bearing_temp in 40-65 deg C |
| Intervention lags | do(coolant_flow = 30): coolant_temp changes at exactly +1 step and bearing_temp at exactly +3 steps, with a sustained drop of more than 1 deg C |
| Trap exists | corr(vibration, true hazard) > 0.1 and above corr(tool_wear, true hazard) in every scenario; pooled corr(vibration, failure) above tool_wear, lube_rate and coolant_flow |
| Trap is not causal | Hazard signature is `(bearing_temp, tool_wear, config)` and an AST check finds no vibration reference. do(vibration = 25) leaves hazard, failures and bearing temperature bit-identical. Removing all vibration noise (fork with config override) leaves the hazard bit-identical. do(bearing_temp = 85) raises the hazard more than 10x |
| Trap is identifiable | Pooled multivariate logistic regression: bearing_temp p = 3e-54, tool_wear p = 4e-9, vibration p = 0.33 |
| Faults | Clog: flow coefficient ratio after/before = 0.20 (target 0.2). Lube leak: lube coefficient -2.22 to 0.002. Bearing damage: current coefficient 0.143 to 0.225 (1.6x) |
| Failures and maintenance | Counts per scenario (below); every failure followed by downtime, maintenance and a wear reset |
| Fork and switching | An unseeded fork repeats the original future exactly; a seeded fork differs; the original is untouched. Paired forks show more cooling lowers true 60-step risk. Switching to overload mid-run keeps the state and starts the creep at the switch |
| Chipping, validation | About 0.8% of steps have a chip; out-of-bounds do() raises; release() cancels scheduled interventions |
| Ingestion | Indicator columns match the event log exactly; steps_since_maintenance resets; CSV sorting, fill counts, dropped leading rows, non-numeric values, role errors; flatline detection |

## Measured timings and failure counts (20,000 steps, scenario seeds)

From `scripts/trap_report.py`:

| Scenario | Simulation time (s) | Failures | Tool changes | Events |
|---|---|---|---|---|
| normal | 0.28 | 5 | 48 | 745 |
| overload | 0.29 | 28 | 63 | 748 |
| cooling_failure | 0.28 | 25 | 41 | 812 |
| heat_wave | 0.27 | 35 | 37 | 779 |
| tool_wear | 0.29 | 48 | 0 | 767 |

Other timings: 100 forks in 4.7 ms; one fork plus 200 steps in 2.7 ms.

Normal-operation ranges while running (min / median / max):

| Variable | Min | Median | Max |
|---|---|---|---|
| ambient_temp | 22.9 | 30.3 | 36.0 |
| motor_current | 2.9 | 29.0 | 37.6 |
| coolant_temp | 22.7 | 32.7 | 41.1 |
| bearing_temp | 30.2 | 51.1 | 69.8 |
| vibration | 0.5 | 4.3 | 6.5 |
| tool_wear | 0.0 | 38.1 | 97.0 |

## The Correlation Trap

Vibration has **no** causal effect on failure. It responds to speed, load and tool wear,
the same conditions that drive bearing heat and tool failure, so it summarizes risk well.

**Correlation with the true hazard** per scenario (running steps), and with the failure event
pooled over all scenarios:

| Variable | Causal role | normal | overload | cooling_failure | heat_wave | tool_wear | pooled corr(failure) |
|---|---|---|---|---|---|---|---|
| vibration | NOT a cause | +0.409 | +0.414 | +0.432 | +0.337 | +0.416 | +0.050 |
| bearing_temp | direct cause | +0.349 | +0.392 | +0.435 | +0.444 | +0.210 | +0.043 |
| tool_wear | direct cause | +0.330 | +0.371 | +0.258 | +0.272 | +0.366 | +0.046 |
| coolant_temp | upstream cause | +0.176 | +0.237 | +0.318 | +0.306 | +0.061 | +0.030 |
| spindle_speed | upstream cause | +0.261 | +0.229 | +0.306 | +0.200 | +0.201 | +0.026 |
| motor_current | upstream cause | +0.243 | +0.221 | +0.277 | +0.189 | +0.174 | +0.025 |
| ambient_temp | upstream cause | +0.144 | +0.203 | +0.246 | +0.282 | +0.021 | +0.025 |
| lube_rate | upstream cause | -0.157 | -0.122 | -0.095 | -0.247 | -0.061 | -0.020 |
| coolant_flow | upstream cause | -0.080 | -0.110 | -0.118 | -0.128 | -0.087 | -0.016 |
| load_pct | upstream cause | +0.055 | +0.133 | +0.092 | +0.062 | +0.050 | +0.012 |

Vibration is the variable most correlated with the true hazard in normal, overload and
tool_wear operation, and the most correlated with the failure event when pooled.

**Single-variable AUC** for "failure within the next 30 steps", pooled. This is how a naive
analyst would rank the signals:

| Variable | Causal role | AUC |
|---|---|---|
| vibration | NOT a cause | 0.817 |
| bearing_temp | direct cause | 0.777 |
| tool_wear | direct cause | 0.776 |
| coolant_temp | upstream cause | 0.710 |
| ambient_temp | upstream cause | 0.683 |
| spindle_speed | upstream cause | 0.663 |
| motor_current | upstream cause | 0.661 |
| lube_rate | upstream cause | 0.635 |
| coolant_flow | upstream cause | 0.625 |
| load_pct | upstream cause | 0.590 |

**Naive XGBoost + SHAP**, same target, all ten variables, trained and explained in 1.2 s.
This is an early look; the proper baseline is built in Phase 3.

| Variable | Causal role | Mean \|SHAP\| | Top feature share at high-risk states (top 1% true hazard) |
|---|---|---|---|
| tool_wear | direct cause | 1.286 | 47% |
| vibration | NOT a cause | 0.720 | 14% |
| bearing_temp | direct cause | 0.625 | 22% |
| coolant_temp | upstream cause | 0.571 | 16% |
| (others) | upstream causes | 0.39 or less | 0% |

Honest reading of the SHAP result: the tree model ranks vibration **second** globally, ahead
of the direct cause bearing_temp, and blames it first in 14% of high-risk states. It does not
rank vibration first. During tuning, SHAP rankings between vibration and tool_wear changed a
lot with small parameter changes: vibration's mean |SHAP| ranged from 0.30 to 0.98 across the
settings tried. The two are strongly collinear proxies, and a tree model splits credit between
them almost arbitrarily. Phase 3 should report per-case results of what the baseline blames,
not assume it always blames vibration.

**The trap is identifiable by a causal method.** A pooled multivariate logistic regression
(test suite) finds bearing_temp (p = 3e-54) and tool_wear (p = 4e-9), and rejects vibration
(p = 0.33). Across 18 extra runs (3 scenarios x 6 seeds) during tuning, vibration's |z| never
exceeded 2.4, so it was never significant at alpha = 0.01.

## Identifiability pre-check (PCMCI)

`scripts/identifiability_check.py` runs PCMCI with ParCorr (tau_max = 3, alpha = 0.01) on the
last 3,000 steps of each scenario. Stopped-machine rows and tool-reset rows are masked. Edges
are scored as (cause, effect) pairs against the 13 true cross edges among continuous
variables. Failure edges and self-lags are excluded.

With `pc_alpha = 0.2` (a lenient condition-selection stage, as tigramite recommends):

| Scenario | Recall | Precision | Correct lag | Time (s) | Missed | Extra |
|---|---|---|---|---|---|---|
| normal | 0.92 | 0.86 | 12/12 | 1.0 | load_pct->tool_wear | load_pct->bearing_temp (lag 3), coolant_temp->vibration |
| overload | 0.85 | 0.79 | 10/11 | 4.2 | load_pct->tool_wear, spindle_speed->tool_wear | load_pct->bearing_temp (lag 3), coolant_flow->bearing_temp (lag 3), tool_wear->bearing_temp |
| cooling_failure | 0.85 | 0.92 | 11/11 | 6.3 | load_pct->tool_wear, spindle_speed->tool_wear | load_pct->bearing_temp (lag 3) |
| heat_wave | 0.92 | 0.92 | 11/12 | 5.5 | load_pct->tool_wear | load_pct->bearing_temp (lag 3) |
| tool_wear | 0.85 | 1.00 | 10/11 | 6.0 | load_pct->tool_wear, spindle_speed->tool_wear | - |

With `pc_alpha = 0.01`, overload precision drops to 0.73 and 8 of 11 lags are correct.
The other scenarios are similar to the table above.

## Notes for Phase 2 (measured, not assumed)

- **Mask regime breaks.** Rows where `machine_running == 0` and the tool reset rows (from
  `event_tool_change` and `event_maintenance`) should be masked before discovery.
- **Use tigramite's `mask`, not `missing_flag`.** In tigramite 5.2.7.0, `missing_flag` with
  `remove_missing_upto_maxlag=True` crashed with an IndexError on these windows. Using `mask`
  with `ParCorr(mask_type="xyz")` works.
- **Condition selection matters.** The persistent false positive `load_pct -> bearing_temp`
  at lag 3 does not exist in the data. Regressing bearing_temp on its true parents plus that
  lag gives t = -0.1. It is a conditioning-set artifact of PCMCI that the Phase 2 engine needs
  to handle.
- **load_pct -> tool_wear is hard in 3,000 steps.** The load-driven part of the tiny per-step
  wear increment is small next to chipping. This is the known trade-off of the chipping fix.
  Without chipping, tool_wear -> vibration is missed instead.
- **The failure model needs more than one 3,000-step window.** A single window holds only 1 to
  14 failures. A pooled history works well (p-values above). With about 40 events, a free
  logistic fit can give a collinear neighbor such as coolant_temp a z of about 3. A check with
  the true coefficients fixed shows coolant_temp is then not significant (z = 1.4 and 0.5), so
  this is small-sample collinearity, not a simulator bug. Bootstrap stability (Phase 2 step 3)
  is the right guard.
- **PCMCI time** with masking is 1 to 6 s per 3,000-step window on this laptop, above the 3 s
  budget. Phase 2 must reduce it (shorter window, fewer variables per test, or caching).

## Known limitations

- All constants are illustrative. Time constants (bearing about 50 s) are faster than a real
  spindle, to keep runs short.
- Sensor noise is deliberately small relative to process noise, to avoid spurious lagged edges.
  Real sensors can be noisier; this simulator represents a well-instrumented machine.
- Tool wear is read as a near-exact load-weighted counter. Shops that do not track wear would
  see a harder problem.
- The XGBoost trap is real but not dominant: vibration ranks second, not first, and the
  vibration versus tool_wear attribution is unstable (see above).
- Normal operation still has 5 failures per 20,000 steps (about one per 11 hours of running),
  against 25 to 48 in stressed scenarios.
- Downtime rows (load and speed forced to 0) and tool resets are regime breaks, not causal
  dynamics; later phases must mask them.
- At standstill, vibration keeps a small floor that scales with tool wear (the model is linear).
- During downtime, a stopped machine overrides interventions on load and speed.
- Several setpoint changes in the same step collapse into one indicator value of 1 in
  `align_events`.
- Failure counts depend on the seed; the numbers above use the fixed scenario seeds in `config.py`.
- Sample CSVs are 10,000 steps per scenario to keep the repository small (use `--steps 20000`
  for full length). The normal sample has only 1 failure in 10,000 steps.
