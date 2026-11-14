# Phase 1 Report - Foundation and Digital Machine

All data in this phase is **simulated**. All physical constants are **illustrative**
(physically plausible, not taken from any real machine datasheet).

## What was built

- **Project skeleton** following CLAUDE.md section 6: `backend/` (Python 3.11 venv,
  pinned `requirements.txt`), `frontend/` placeholder, `docs/`, GPL-3.0 `LICENSE`,
  `.gitignore`, README skeleton, placeholder `docker-compose.yml`.
- **`causaltwin/config.py`**: single source of truth for every constant (variables,
  units, ranges, time step, coefficients, hazard parameters, fault strengths, seeds).
- **`causaltwin/utils/`**: structured activity logger (`ActivityLogEntry`: timestamp,
  step name, duration in ms, message, details dict; supports subscribers for later
  live streaming) and a `timed_step` context manager.
- **`causaltwin/simulator/`**:
  - `machine.py` - `MachineSimulator(config, seed, profile)` with `step()`, `run()`,
    `intervene()` (do-operator, optionally scheduled), `release()`, `inject_fault()`,
    `clear_fault()`, `get_true_hazard()`, `get_state()`, plus sensor, event and
    ground-truth tables. First-order thermal dynamics, current proportional to load,
    vibration from speed/load/wear, accumulating tool wear, sigmoid hazard, downtime
    after failure followed by maintenance (tool replaced).
  - `operator.py` - operator behavior: random setpoint changes (every ~120 steps per
    lever on average) plus AR(1) small adjustments, so levers vary enough for discovery.
  - `scenarios.py` - five seeded scenarios and `generate_dataset(scenario_name, n_steps, seed)`.
  - `true_graph.py` - the true causal graph, written to `backend/data/true_graph.json`.
  - `random_stream.py` - block-drawn seeded random numbers. The simulator draws the
    **same number of random values every step** regardless of interventions
    ("common random numbers"), so an intervened run and a baseline run with the same
    seed differ only because of the intervention. Later phases can use this for exact
    ground-truth checks of recommendations.
- **`causaltwin/ingestion/`**: `align_events` (event indicator columns plus
  `steps_since_maintenance`), `load_csv` (roles, sorting, numeric parsing, forward fill
  with counts, validation report with detected sampling interval), and
  `data_quality_report` (min, max, mean, std, missing count, flatline detection).
- **Scripts**: `scripts/generate_samples.py` (true graph and one sensors/events CSV per
  scenario into `backend/data/samples/`, 10,000 steps each by default) and
  `scripts/trap_report.py` (reproduces the tables in this report).

### Design decisions worth knowing

- **Measurement model.** Each internal variable has process noise in its dynamics and
  separate Gaussian sensor noise on its reading. The physics uses the true state; the
  sensor table holds the noisy readings. Control levers are logged commands and are
  recorded exactly.
- **Machine stop.** During the 12 steps after a failure, `load_pct` and
  `spindle_speed` are 0 and `machine_running` is 0. Coolant and lubrication keep
  running. The hazard is 0 while the machine is stopped.
- **Ground truth is separate.** `true_hazard` and the true internal states live only in
  the ground-truth table (`run_scenario(...).ground_truth`), never in the sensor data.
- **Self-lags in the true graph.** In addition to the self-lags the brief lists, the
  graph includes `tool_wear` (it accumulates) and `ambient_temp` (slow drift), because
  the equations contain them. Lever autocorrelation comes from operator setpoint
  persistence. It is noted in the graph file but not listed as an edge.

## How to run

```bash
cd backend
# Python 3.11 virtual environment
python3.11 -m venv .venv
.venv/Scripts/activate        # Windows (use .venv/bin/activate on Linux/macOS)
pip install -r requirements.txt

python -m pytest -q -s               # all tests, with measured values printed
python scripts/generate_samples.py   # true_graph.json + sample CSVs
python scripts/trap_report.py        # tables in this report
```

## Test results

`python -m pytest -q -s` gives **23 passed in 2.96 s**.

| Area | What is checked |
|---|---|
| Determinism | Same seed gives identical sensors and events; a different seed gives different data |
| Performance | 20,000 steps in 0.25 s (budget 2 s) |
| Bounds | All variables inside physical bounds; levers inside their normal range while running; about 87% of normal-operation steps have bearing_temp in 40-65 deg C |
| Intervention lags | do(coolant_flow=30): coolant_temp changes at exactly +1 step, bearing_temp at exactly +3 steps (1 + 2), with a sustained drop of more than 1 deg C |
| Trap exists | corr(vibration, true hazard) > 0.1 and above corr(tool_wear, true hazard) in every scenario |
| Trap is not causal | Hazard signature is `(bearing_temp, tool_wear, config)`, and an AST check finds no reference to vibration. do(vibration=25) leaves hazard, failures and bearing temperature **bit-identical**, while do(bearing_temp=85) raises the hazard more than 10x |
| Faults | Clog: flow coefficient ratio after/before = **0.21** (target about 0.2). Lube leak: lube coefficient -2.28 to -0.02. Bearing damage: current coefficient 0.143 to 0.226 (about 1.6x) |
| Failures | Normal is rare; every stressed scenario has at least 20 per 20,000 steps; every failure is followed by downtime, maintenance and a tool-wear reset |
| Ingestion | Indicator columns match the event log exactly; steps_since_maintenance resets; CSV sorting, fill counts, dropped leading rows, non-numeric values, role errors; flatline detection |

## Measured timings and failure counts (20,000 steps, scenario seeds)

| Scenario | Simulation time (s) | Failures | Tool changes | Events |
|---|---|---|---|---|
| normal | 0.24 | 5 | 39 | 731 |
| overload | 0.27 | 27 | 53 | 765 |
| cooling_failure | 0.27 | 25 | 32 | 809 |
| heat_wave | 0.25 | 37 | 32 | 801 |
| tool_wear | 0.25 | 25 | 0 | 722 |

## The Correlation Trap

The table shows Pearson correlation with the **true hazard** (simulator ground truth) on
running steps of each scenario, and with the binary **failure** event, pooled over all
scenarios.

| Variable | Causal role | normal | overload | cooling_failure | heat_wave | tool_wear | pooled corr(failure) |
|---|---|---|---|---|---|---|---|
| vibration | NOT a cause | +0.236 | +0.138 | +0.220 | +0.119 | +0.183 | +0.027 |
| bearing_temp | direct cause | +0.333 | +0.484 | +0.436 | +0.395 | +0.264 | +0.063 |
| tool_wear | direct cause | +0.103 | -0.017 | -0.007 | -0.079 | +0.095 | +0.004 |
| motor_current | upstream cause | +0.228 | +0.219 | +0.293 | +0.192 | +0.205 | +0.033 |
| coolant_temp | upstream cause | +0.122 | +0.313 | +0.271 | +0.283 | +0.108 | +0.043 |
| load_pct | upstream cause | +0.128 | +0.141 | +0.109 | +0.088 | +0.106 | +0.017 |
| spindle_speed | upstream cause | +0.180 | +0.190 | +0.259 | +0.193 | +0.193 | +0.031 |
| coolant_flow | upstream cause | -0.008 | -0.137 | -0.117 | -0.057 | -0.073 | -0.019 |
| lube_rate | upstream cause | -0.160 | -0.237 | -0.094 | -0.068 | -0.074 | -0.019 |
| ambient_temp | upstream (environment) | +0.136 | +0.263 | +0.199 | +0.277 | +0.085 | +0.037 |

Vibration has no causal effect on failure, but in normal operation it is the
second-strongest correlate of the true hazard. In every scenario it correlates more
strongly than the direct cause `tool_wear`, and it beats `load_pct`, `coolant_flow` and
`lube_rate` in most scenarios.

An exploratory check outside the test suite (to be done properly in Phase 3): an XGBoost
failure classifier with SHAP, trained on all scenarios, ranked mean |SHAP| as
bearing_temp 2.43, **vibration 0.54**, lube_rate 0.36, spindle_speed 0.33, and
tool_wear 0.32. The naive model puts vibration second, ahead of the true cause tool_wear.

## Known limitations

- All constants are illustrative. The time constants (bearing about 50 s, coolant about
  30 s) are faster than a real spindle, which keeps runs short.
- Sensor noise sits on top of a latent state, so the measured series is not exactly a
  lagged linear model. With long windows, PCMCI may find small extra lags (for example
  a self-lag at lag 2). Phase 2 should compare against the true graph with this in mind.
- `tool_wear` correlates weakly, sometimes negatively, with the hazard in stressed
  scenarios, even though it is a direct cause. Tool changes and post-failure resets cut
  off the high-wear region (a selection effect). This is realistic, but it means
  naive correlation also hides a true cause.
- In the trap, a naive model does **not** rank vibration first; bearing_temp is measured
  directly and ranks first. The trap is "vibration looks more important than real
  causes such as tool_wear", not "vibration is the top predictor".
- Downtime rows (load and speed forced to 0) are a different regime. Later phases should
  filter on `machine_running == 1` before discovery.
- At standstill, vibration keeps a small floor that scales with tool wear (the model is linear).
- Wear is in "equivalent cutting minutes": it accelerates with load and speed, so it is
  not wall-clock time.
- Several setpoint changes in the same step collapse into one indicator value of 1 in
  `align_events`.
- Failure counts depend on the seed. The counts above are for the fixed scenario seeds
  in `config.py`.
- Sample CSVs are 10,000 steps per scenario to keep the repository small. Use
  `--steps 20000` for full length.
