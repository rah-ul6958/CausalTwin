# Phase 2 Report - Causal Engine

All data is **simulated**. "Simulator ground truth" (the true graph, true hazard and
interventions in the real simulator) is used only in validation, never inside the engine.
Every number below comes from `python scripts/run_validation.py` (saved to
`backend/data/validation_results.json`) or from the test suite.

## What was built (`backend/causaltwin/engine/`)

| Module | Responsibility |
|---|---|
| `preprocessing.py` | Variable roles, and a row mask for regime breaks: stopped machine, tool reset or maintenance rows, jumps in the step counter, missing values |
| `discovery.py` | Lagged causal graph with tigramite (PCMCI's MCI test with ParCorr), an exact NumPy equivalent, the failure screen (multivariate logistic regression), and tabular mode (PCMCIplus at tau_max = 0) |
| `correlation.py` | Lagged Pearson correlation graph, in the same shape as the causal graph, for the "Correlation vs Causation" toggle |
| `confidence.py` | Moving-block bootstrap (block 50, 20 resamples): edge frequency plus a plain-English explanation per edge |
| `causal_model.py` | Ridge equation per non-exogenous variable and a logistic failure equation, each with held-out quality, residuals for Monte Carlo noise, and readable equation strings (model cards) |
| `whatif.py` | Vectorized Monte Carlo rollouts (200) under do-interventions, against a baseline with identical noise draws |
| `root_cause.py` | Ranked causal paths that explain the current failure risk, plus "symptoms" (correlated with failure, no causal path) |
| `validation.py` | Graph recovery metrics, intervention accuracy against the real simulator, and the trap check |
| `twin.py` | `CausalTwinEngine`: `fit`, `get_graph`, `get_correlation_graph`, `get_edge_confidences`, `what_if`, `root_cause`, `get_symptoms`, `get_model_cards`, `get_assumptions`, `get_activity_log`, `warm_up` |
| `schemas.py` | Pydantic models for everything the engine returns |

All settings are in `config.py`, in the "Causal engine (Phase 2)" section. Every pipeline step
writes an activity-log entry with its duration: `pcmci`, `failure_screen`, `discovery`,
`bootstrap`, `fit_model`, `correlation_graph`, `what_if`, `root_cause`, `fit_engine` and
`warm_up`. Phase 1 additions used here are `run_scenario_cycle()` and
`advance_scenario_cycle()`: one continuous simulator run that switches scenario every
1,500 steps.

## Design decisions, each backed by a measurement

### 1. MCI with the full candidate set as conditions (instead of PCMCI's PC1 stage)

Standard PCMCI first prunes candidate parents (PC1), then tests every link given the kept
conditions (MCI). On this machine, PC1 **removed a true parent**. It dropped
`motor_current(t-2)` from bearing_temp's conditions once `motor_current(t-1)` was in the
condition set: the partial correlation fell to 0.008, p = 0.68. Motor current is almost a
deterministic function of load and speed, so in intermediate condition sets it looks
redundant. The MCI test then produced a false `load_pct -> bearing_temp` edge (partial
correlation 0.48) in most windows. A plain least-squares check confirmed this is the
proxy effect, not a simulator bug.

Fix: run tigramite's `run_mci` with **every variable at lags 1-3 as conditions** for each
target. This equals PCMCI with `pc_alpha = 1` and `max_conds_px = 0`. With about 3,000
samples and 30 candidates the test is well powered, and the false edge disappears in every
window.

### 2. Joint test over a cause's lags decides whether an edge exists

With all lags conditioned on, a slowly changing cause has nearly identical neighbouring lags.
Ambient temperature is the clearest case. Its effect is split between the lags, so no single
lag is significant: `ambient_temp -> coolant_temp` was missed and recall was 0.77. The engine
therefore decides "edge or no edge" with an **F test of all lags of that cause jointly**,
computed exactly from the same least-squares model. The reported lag is the lag with the
strongest individual MCI result. Self-lags use individual tests.

Measured on the last 3,000 steps of each scenario: recall rose from 0.77-0.92 to 0.85
everywhere, with **precision 1.00 in every window**.

### 3. Speed: exact equivalents, verified in the tests

- `FastParCorr`: tigramite's ParCorr with the final Pearson correlation computed in NumPy.
  It gives identical results and is about 30% faster; SciPy's `pearsonr` built a
  distribution object on every call.
- With a shared condition set, every partial correlation of one target follows from a
  single least-squares fit. `full_condition_mci` does exactly that. Against tigramite on
  the validation window, the maximum differences are **1.95e-9** (partial correlation) and
  **6.3e-8** (p-value). The 20 bootstrap resamples use it.
- `logistic_newton` (NumPy Newton method) equals statsmodels `Logit` to about 1e-14 and is
  used inside the bootstrap. The main failure screen uses statsmodels.
- Tigramite runs in **5 worker processes**, one per target, with results identical to the
  sequential run. `engine.warm_up()` starts the workers once (about 3-4 s on Windows), as a
  long-lived server would at start-up.

### 4. The failure model

The brief asks for a multivariate logistic regression of failure on candidate sensor
variables at lags 1 to tau_max. Three issues came up:
- **Singular fits.** Neighbouring lags of slow variables (tool wear changes about 0.17 per
  step on a range of about 100) are nearly identical columns. Each variable's lags are
  reparametrized as level plus successive differences. This is the same model and the same
  likelihood, but well conditioned. Each variable is tested jointly over its lags (Wald
  chi-square). Strength is the total effect over the lags, per standard deviation.
- **Power.** With 10 variables x 3 lags and 28-48 events, nothing was significant. Only
  variables with the **sensor** role are screened, which halves the parameters. The
  assumption is that levers and environment act on failure through measured sensors.
- **History length.** A 3,000-step window holds 1 to 14 failures. The failure model uses up
  to 100,000 steps of history (`FAILURE_HISTORY_MAX_STEPS`). With 30,000 training steps
  (43 failures), `bearing_temp -> failure` was found in only 50% of bootstrap resamples and
  was correctly dropped as unstable. With **60,000 steps (89 failures)**, both true causes
  return in 100% of resamples.

Failure-edge lags are **not identifiable** here. The log-likelihoods for lags 1, 2 and 3
differed by at most 1.8 (lag 3 marginally best), which is below the 1.92 a likelihood-ratio
test needs at the 95% level. When lags cannot be told apart, the engine uses the shortest
one (`FAILURE_LAG_TIE_TOLERANCE`). This tie-break is why the failure-edge lags below are
correct; without it, they would read 3.

## How to run

```bash
cd backend
python -m pytest -q                  # 46 tests (Phase 1 + Phase 2), about 25 s
python scripts/run_validation.py     # full validation, about 20-45 s; writes data/validation_results.json
```

```python
from causaltwin.engine import CausalTwinEngine, Intervention
engine = CausalTwinEngine()
engine.warm_up()
engine.fit(aligned_dataframe)           # output of ingestion.align_events(sensors, events)
engine.what_if([Intervention(variable="coolant_flow", change_type="percent", value=20)])
engine.root_cause()
```

## Test results

`python -m pytest -q`: **46 passed** (31 Phase 1, 15 Phase 2). `ruff check`: all checks
passed. `mypy`: no issues in 36 files.

Phase 2 tests:
- NumPy MCI equals tigramite; NumPy logistic regression equals statsmodels.
- Graph recovery reaches recall >= 0.8 and precision >= 0.8, with and without failure edges.
- Vibration is not a failure cause, is a symptom, and does appear in the correlation graph.
- Raising coolant lowers predicted coolant and bearing temperature and the failure risk.
- Removing vibration noise, or halving vibration, changes predicted risk by exactly 0.
- Timing budgets hold.
- Bootstrap frequencies lie in [0, 1], and true edges average 0.89 against 0.10 for false pairs.
- Model cards are readable and accurate.
- Root cause works on high-risk states.
- Too few failures are reported rather than guessed.
- An unfitted engine raises a clear error.
- Tabular mode works on synthetic independent rows.
- SHD counts a reversed edge once.
- Regime breaks are masked.

## Graph recovery

Training data: one continuous run of 60,000 steps cycling heat_wave, tool_wear, normal and
overload every 1,500 steps (seed 2026, 89 failures). PCMCI uses the last 3,000 steps, which
cover normal and overload operation. Self-lags are ignored.

| Metric | With failure edges | Without failure edges |
|---|---|---|
| True edges | 15 | 13 |
| Precision | **1.00** | **1.00** |
| Recall | **0.87** | **0.85** |
| F1 | 0.93 | 0.92 |
| Lag accuracy (among correct edges) | 1.00 | 1.00 |
| Structural Hamming distance | 2 | 2 |

**Missed in every run:** `load_pct -> tool_wear` and `spindle_speed -> tool_wear`. This is the
known Phase 1 trade-off. The load- and speed-driven part of the tiny per-step wear increment
is small next to random tool chipping, which in turn is what makes
`tool_wear -> vibration` detectable. **No extra edges.** Every discovered edge has bootstrap
confidence 1.00.

Per scenario (last 3,000 steps of each 20,000-step scenario, no failure edges): precision
**1.00**, recall **0.85** and lag accuracy **1.00** in all five scenarios, with the same two
edges missed. In `cooling_failure` the window lies after the filter clog, where coolant flow
keeps only 20% of its effect, and the edge is still found.

## Intervention accuracy (out of sample, against the real simulator)

Setup: after training, the simulator continues. At 20 random moments with the machine
running, a random lever is changed by 10-30% (up or down, clipped to its range). The what-if
engine predicts the effect over 30 steps. The ground truth is paired forks of the real
simulator, with and without the change, averaged over 8 seeds, failures disabled, true
hazard used.

| Metric | Value |
|---|---|
| Bearing temperature, mean absolute error at horizon end | **1.26 deg C** (true changes average 2.93 deg C) |
| Bearing temperature, mean absolute error averaged over the horizon | 0.64 deg C |
| Bearing temperature, direction correct | **18 of 19** cases with a true change > 0.1 deg C (95%) |
| Failure risk change, mean absolute error | **0.0059** (true changes average 0.0156) |
| Failure risk change, direction correct | **9 of 9** cases with a true change > 0.001 (100%) |

All 20 cases (risk = probability of at least one failure within 30 steps):

| Lever | From -> to | Predicted dBearing | True dBearing | Predicted dRisk | True dRisk |
|---|---|---|---|---|---|
| load_pct | 98.2 -> 75.8 | -3.51 | -2.28 | -0.0418 | -0.0680 |
| load_pct | 56.9 -> 43.8 | -2.06 | -2.07 | -0.0113 | -0.0111 |
| load_pct | 69.0 -> 87.3 | +2.87 | +2.63 | +0.0031 | +0.0070 |
| coolant_flow | 14.2 -> 11.5 | +1.18 | +1.62 | +0.0494 | +0.0677 |
| spindle_speed | 3507 -> 4163 | +4.41 | +5.22 | +0.0388 | +0.0753 |
| coolant_flow | 15.7 -> 13.1 | +1.19 | +2.03 | +0.0539 | +0.0685 |
| spindle_speed | 2565 -> 2983 | +2.81 | +1.41 | +0.0031 | +0.0008 |
| coolant_flow | 20.5 -> 17.6 | +1.26 | +1.57 | +0.0003 | +0.0006 |
| coolant_flow | 18.3 -> 14.7 | +1.59 | +2.56 | +0.0001 | +0.0001 |
| lube_rate | 1.40 -> 1.50 | -1.16 | -2.47 | -0.0001 | -0.0004 |
| spindle_speed | 3083 -> 2554 | -3.56 | -4.48 | -0.0014 | -0.0025 |
| lube_rate | 0.72 -> 0.94 | -2.38 | -1.27 | -0.0006 | -0.0007 |
| load_pct | 93.6 -> 79.0 | -2.29 | -1.91 | -0.0003 | -0.0008 |
| coolant_flow | 13.5 -> 16.2 | -1.20 | +0.10 | -0.0003 | -0.0000 |
| spindle_speed | 3163 -> 3977 | +5.48 | +6.96 | +0.0150 | +0.0045 |
| load_pct | 85.2 -> 100.0 | +2.33 | +2.43 | +0.0005 | +0.0007 |
| spindle_speed | 2149 -> 1673 | -3.20 | -5.37 | -0.0000 | -0.0001 |
| **spindle_speed** | **1864 -> 2300** | **+2.93** | **-2.91** | +0.0002 | -0.0004 |
| spindle_speed | 3908 -> 3113 | -5.34 | -4.32 | -0.0003 | -0.0004 |
| spindle_speed | 2004 -> 1756 | -1.67 | -5.04 | -0.0015 | -0.0031 |

**The failure case, explained.** Raising spindle speed from 1864 to 2300 rpm must raise
bearing temperature physically, and the twin predicts +2.9 deg C, but the simulator shows
-2.9 deg C. The intervention holds speed at 2300 for the whole horizon. In the paired
baseline runs, the simulated operator raised the speed setpoint further on their own during
those 30 steps. Compared with that baseline, "holding 2300" means lower speed. The what-if
engine assumes that levers nobody intervenes on stay constant, and that assumption breaks
when the operator moves them. The `coolant_flow 13.5 -> 16.2` case, where the true change was
only +0.10, is the same effect. Typical errors are 1 to 2 deg C, mostly an underestimate of
how far bearing temperature moves within 30 steps.

## Trap check

| Check | Result |
|---|---|
| Discovered failure causes | `bearing_temp`, `tool_wear` (the true causes) |
| Vibration discovered as a cause? | **No.** Failure screen p = 0.93 |
| Vibration correlation with "failure within 30 steps" | +0.247, the highest of all variables |
| Listed symptoms | `vibration` (the only symptom) |
| Predicted risk change when vibration is halved | **0.0** |
| Passed | **Yes** |

The correlation graph does link vibration to failure (r = +0.25), while the causal graph does
not. That is the "Correlation vs Causation" contrast for the UI.

## Fitted equations (model cards) against the simulator truth

| Equation (held-out R squared or AUC) | Truth (from `machine.py`) |
|---|---|
| `motor_current(t) = 0.493*mc(t-1) + 0.112*load(t-1) + 0.00204*speed(t-1) + 2.00` (R2 0.93) | 0.5*mc + 0.5*(0.22*load + 0.004*speed + 4) = 0.5, 0.11, 0.002, 2.0 |
| `coolant_temp(t) = 0.708*ct(t-1) + 0.284*ambient(t-1) - 0.134*flow(t-1) + 3.74` (R2 0.92) | 0.7, 0.3, -0.135, 3.6 |
| `bearing_temp(t) = 0.798*bt(t-1) + 0.00078*speed(t-1) - 2.28*lube(t-2) + 0.144*mc(t-2) + 0.198*ct(t-2) + 0.14` (R2 0.99) | 0.8, 0.0008, -2.2, 0.14, 0.2, 0 |
| `vibration(t) = 0.395*vib(t-1) + 0.0120*load(t-1) + 0.00045*speed(t-1) + 0.0122*wear(t-1) + 0.12` (R2 0.99) | 0.4, 0.012, 0.00045, 0.012, 0.12 |
| `tool_wear(t) = 1.000*wear(t-1) + 0.256` (R2 0.999) | wear + load- and speed-dependent increment + chipping |
| `logit P(failure(t)) = 0.255*bt(t-1) + 0.0785*wear(t-1) - 27.45` (AUC 0.96) | 0.25*bt(t-1) + 0.07*wear(t-1) - 26.4 |

## Timings against the budget

From the final validation run, on an Intel i5-13500H laptop on the Balanced power plan:

| Step | Measured | Budget |
|---|---|---|
| Discovery on a 3,000-step window (PCMCI 0.48 s + failure screen on 60,000 rows 0.61 s) | **1.09 s** | < 3 s |
| Fit the causal model | **0.20 s** | < 1 s |
| One what-if (200 rollouts, with paired baseline) | **14 ms** (median) | < 200 ms |
| Bootstrap, 20 resamples (graph and failure model) | 3.0 s | none set ("reasonable") |
| Whole `fit()` | 4.4 s | none set |
| Worker warm-up (once per process) | 3.1 s | none set |

**Measurement caveat.** This laptop throttles sustained single-threaded work. An identical
pure-Python loop slowed from 0.20 s to 0.44 s per round after about 3 seconds of load, so
timings vary up to about 3x between runs. In a throttled run with a cold worker pool,
discovery took 5.6 s and the bootstrap 7-14 s. With warm workers, discovery measured
1.0-2.3 s across runs, inside the budget.

## Assumptions (shown in the "How was this computed?" drawer, from `get_assumptions()`)

- **Causal sufficiency:** no hidden variable drives two measured variables (PCMCI assumption).
- Effects are linear and act with a lag of 1 to 3 steps; nothing causes the past.
- Control levers and environment variables have no causes inside the system.
- The causal structure is stable within the analysis window (drift detection comes in Phase 3).
- Rows where the machine is stopped or a tool is reset are excluded as regime breaks.
- **What-if:**
  - Levers that are not intervened on stay at their last observed value.
  - Noise is resampled from each equation's own residuals, with the same draws for the baseline.
  - The interval reflects process noise only, not coefficient uncertainty.
  - No tool changes are simulated inside the horizon.
- **Root cause:**
  - "Normal level" is the median of the fitted window.
  - Contributions use steady-state gains, so they are a linearization.
  - Accumulators such as tool wear start paths rather than pass them on.
- **Tabular mode:**
  - Rows are treated as independent samples.
  - Edges whose direction cannot be decided are shown as undirected and not used for what-if.
  - What-if results are one-step propagations.

## Known limitations

- **Linear model, no hidden confounders, lags of at most 3 steps.** Real machines can violate
  all three.
- **The discovery method deviates from standard PCMCI**, and deliberately so: full-condition
  MCI plus a joint lag test, for the measured reasons above. It is still tigramite's MCI test
  for every lagged link; edge existence uses the joint test.
- **Two true edges are always missed** (`load_pct` and `spindle_speed` -> `tool_wear`). As a
  result the wear equation has no causes: it predicts a constant increment, so what-if cannot
  show that slower cutting slows wear.
- **The failure model needs about 90 failure events.** With fewer, causes are dropped as
  unstable or the model is not fitted, and the engine says so instead of guessing. Only
  variables with the sensor role are screened as direct failure causes.
- **Failure-edge lags are not identifiable from these data.** The shortest of the
  statistically equivalent lags is reported.
- **The constant-lever assumption in what-if** causes the visible failure case above. Phase 3
  could model operator behaviour or show the assumption more prominently.
- **Risk intervals show process noise only**, so they are narrower than the true uncertainty.
- **Tabular mode is tested only on synthetic data.** The Phase 3 AI4I check would be its
  first real-world-like test.
- **Timings vary up to about 3x** on this laptop because of CPU throttling (see the caveat).
