# Phase 6 Report - Closing the Gaps Left After Phase 5

This phase closed the gaps listed in `docs/FINAL_AUDIT.md` at the end of Phase 5, except those that need
a real machine or a person (deployment, video, slides, sourced statistics). All machine data is still
**simulated** unless stated; AI4I 2020 is a third-party synthetic dataset. Numbers below were measured
during this phase on the development laptop; the README's result tables are generated from the result
files.

Nothing was deployed or pushed.

## 1. The two always-missed causal links (load and speed affecting tool wear)

**Cause, measured.** Per step, tool wear grows by about 0.04-0.4 minutes depending on load and speed,
plus an 8-minute chip in about 1% of steps. In the discovery window the chips make up almost all of the
variance, so the joint test gave p = 0.35 (load) and p = 0.56 (speed). With the 37 chip rows (of 2,921)
left out of that one test, both became overwhelmingly significant (p < 1e-200) and no false cause
appeared.

**Fix** (`engine/robust.py`, `discovery.py`, `causal_model.py`):

- **Testing:** a target's rows whose residual (from a regression on all candidates) lies beyond 6 robust
  standard deviations (1.4826 x MAD) are left out of that target's tests, but only if such rows are rare
  (at most 5%).
- **Fitting:** they are also left out of the equation fit, but their residuals stay in the noise pool,
  so simulated futures still contain chips at the observed rate.
- **Which targets are affected:** jumps in raw changes were not used. Setpoint changes make every
  variable jump legitimately, and those rows are the most informative. On residuals, only tool wear has
  such jumps: 0.89% of rows, none for any other variable.
- **Reporting:** the model card notes how many jumps were left out; the assumption is listed in the
  "How was this computed?" drawer.

**Result.** Graph recovery: **15 of 15 true links, no false links, SHD 0, in all five scenarios** (was
13 of 15). The new wear equation matches the simulator's physics:

| Effect on wear per step | Fitted | True (at the reference point) |
|---|---|---|
| Load | 0.00259 per % | 0.00238 per % |
| Speed | 0.000076 per rpm | 0.000072 per rpm |

Both new links have bootstrap confidence 1.00. The NumPy and tigramite MCI paths still agree, to within
1e-7 in the partial correlations and 1e-5 in the p-values. The test tolerances were widened by that
much, because the near-exact wear fit is where rounding differs most.

## 2. Risk under-prediction in extreme states

**Diagnosis at the demo's hot afternoon.** The true risk, from 60 simulator futures, was 53.4%; the twin
said 34.9%.

| Diagnostic | Risk | What it isolates |
|---|---|---|
| Twin's failure model applied to the true temperature and wear paths | 41.1% | the failure-model error |
| True hazard applied to the twin's forecast paths | 44.3% | the forecast error |

So the failure model is the larger part of the error, and the bearing-temperature forecast (about
0.6 deg C low) the smaller.

**Cause: estimation noise, not bias.** The same logistic model was fitted on independent histories, at
85 deg C bearing temperature and 20 minutes of wear:

| Training history | Failures | Fitted hazard | True hazard |
|---|---|---|---|
| 60,000 steps, seed 2026 (the twin's) | 89 | 0.0149 | 0.0230 |
| 60,000 steps, seed 7 | 114 | 0.0398 | 0.0230 |
| 60,000 steps, seed 11 | 91 | 0.0154 | 0.0230 |
| 200,000 steps, seed 2026 | 322 | 0.0195 | 0.0230 |

The error goes both ways and shrinks with more failures.

**What changed:**

- **Coefficient uncertainty.** Every risk estimate (what-if, live risk, optimizer) now draws the failure
  model's coefficients per rollout from their estimated distribution, on a separate random stream so the
  noise draws are unchanged. The single and batched evaluators stay identical.
- **Calibration measurement.** The benchmark now records the twin's predicted risk next to the true risk
  in every case:

| Twin prediction | Mean predicted | Mean true | Mean absolute error | Truth inside the 90% interval | Correlation |
|---|---|---|---|---|---|
| No action | 30.8% | 30.4% | 4.1 points | 87% | 0.96 |
| After its own action | 3.5% | 3.8% | 1.1 points | 43% | 0.97 |

The twin is calibrated on average. Its intervals after an action are too narrow; this is reported, not
hidden.

**Tried and rejected: a longer training history (120,000 steps).**

| Measure | 60,000 steps | 120,000 steps |
|---|---|---|
| Benchmark successes | 28 of 30 | 30 of 30 |
| Calibration: mean absolute error | 4.1 points | 4.8 points (over-prediction) |
| Calibration: truth inside the interval | 87% | 70% |
| Fault-free runs: wrongly confirmed drift changes | 0 | 4 in 40,000 steps |
| Mean cost | 6.6 | 7.5 |

The setting was reverted on that evidence.

## 3. Drift confirmation

At 120,000 steps, and in one run during development, re-discovery in a short window found spurious new
links in equations that had not drifted. The confirmation step now only confirms changes in the equation
whose prediction errors raised the alarm. All nine real fault detections in the drift results changed
exactly the alarmed equation, so none is lost. Fault-free runs: 0 wrongly confirmed changes in 60,000
steps.

## 4. Demo beats 4 and 5

With the new wear links the first recommendation is cheaper (slow the spindle 25%, add 25% lubrication;
cost 14.5, was 45). Over 90 steps, risk crept back up as the fresh tool wore at 96% load:

| Steps after applying | Twin | Truth |
|---|---|---|
| 30 | 6.0% | 8.6% |
| 90 | 18.7% | 23.6% |

The recommendation promises the risk for the next 30 steps, so beat 4 now runs 10 steps and beat 5 runs
to exactly 30 steps after the change. Its narration compares with the prediction: "5 minutes after the
change, predicted failure risk is 6.0% (predicted when recommending: 7.8%)"; the ground truth (8.6%) is
shown separately. The demo script says plainly that risk creeps up later and the live alert would catch
it. Five consecutive demo runs from the UI gave identical narration.

## 5. AI4I 2020 (the Phase 3 stretch goal)

**Data and rules.** The dataset (UCI, CC BY 4.0, DOI 10.24432/C5HS5C; in `backend/data/external/` with
attribution) is synthetic data modelled on a real milling machine. Its documented rules reproduce the
heat-dissipation, power and overstrain labels on **all 10,000 rows**, so they serve as ground truth.

**Method.** `intelligence/ai4i.py` and `scripts/run_ai4i.py` (6.6 s):

- tabular discovery through the Bring Your Own Data path, with the documented physics features
  (temperature difference, mechanical power, wear x torque);
- for each failed row, the cheapest change of speed, torque (within the observed range) and tool,
  scored by CausalTwin's failure model;
- XGBoost + SHAP on the raw columns, with the same action mapping as the main benchmark;
- both judged by the rules.

**Discovery: exactly the documented mechanism.** Failure is caused by temperature difference, power and
wear x torque. Air temperature drives process temperature; speed and torque drive power; torque and
wear drive wear x torque.

**Recommendations: worse than the baseline.**

| Failure mode (rows) | CausalTwin | XGBoost + SHAP |
|---|---|---|
| All 330 scored | 37% | 65% |
| Heat dissipation (115) | 0% | 22% |
| Power (95) | 47% | 77% |
| Overstrain (98) | 85% | 82% |
| Tool wear (46) | 9% | 93% |

The reason is the linear failure model. Power must stay inside a window, and heat dissipation needs a
speed above a threshold; a linear model cannot express either, and XGBoost's trees can. Screening
control columns as direct failure causes was tried and changed nothing. This is reported as a loss, and
a non-linear failure model is the documented next step.

## 6. Live machine connector

**Code** (`causaltwin/connectors/`): one `DataSource` interface (the simulator stepper already fits it)
with two new sources:

- `MTConnectSource`: polls an agent's `/current` once per step, maps data items to model variables
  (`CAUSALTWIN_TAG_MAP`), and handles UNAVAILABLE values, execution state and FAULT conditions;
- `ReplaySource`: streams a recorded CSV.

**Behaviour with a non-simulator source:**

- `/health` and the status report the source;
- the UI shows a "Live data" badge instead of "Simulated" and drops ground truth;
- scenarios, fault injection, the speed and step controls and the guided demo are hidden or disabled,
  with an explanation;
- Apply becomes an advisory note; the API answers 409 with a clear message.

A row with a missing variable is not used for risk or drift.

**Tests and evidence:**

- `tests/test_connectors.py`: parsing, a fake agent, replay, and the full API on an MTConnect source.
- `scripts/fake_mtconnect_agent.py`: a stand-in agent serving simulator values, labelled as a test tool.
  It was used to check the UI in this mode by eye.
- **Not tested against a real machine** (none available).

## 7. Accessibility, browsers and narrow screens

**Audit.** axe-core (WCAG 2.1 A/AA) on all eight pages (`tests/accessibility.spec.ts`) found:

- invalid ARIA structure in the two virtualized tables;
- scrollable regions that keyboard users could not reach (seven lists);
- a `<dl>` with a stray `<p>`.

**Result.** All fixed: **no violations on any page**, colour contrast included.

**Also checked:**

- no horizontal scrolling at 1024 and 768 px;
- the page smoke tests pass in **Firefox and WebKit** as well as Chromium.

## 8. Load test (`scripts/load_test.py`)

Each simulated viewer keeps a WebSocket open, polls status every 3 s, and sometimes runs a what-if or
a recommendation.

**First run, 25 viewers.** Nothing failed, but the live simulation slowed from 5 to 2.4 steps per second:
heavy requests held the live lock for their whole computation.

**Fix.** Heavy requests now take a snapshot under the lock and compute outside it. This is safe because a
drift rewiring replaces the engine's model and graph objects rather than editing them. The SHAP
explainer is used one call at a time.

**After the fix:**

| 25 viewers, 60 s | Before | After |
|---|---|---|
| Live simulation | 2.37 steps/s | 3.11 steps/s |
| What-if, 95th percentile | 703 ms | 284 ms |
| Recommendation, median | 0.77 s | 1.27 s (now shares the CPU instead of blocking) |
| Live messages per viewer per second | 6.1 | 8.4 |
| Failed requests or connections | 0 | 0 |

**50 viewers:**

- every status poll answered (95th percentile 288 ms);
- no failed live connection;
- 40% of heavy requests got the designed "busy, retry";
- 2.7 steps per second.

## 9. Performance

**Baseline training.** The F1 threshold search called scikit-learn 200 times. Computing all 200 F1 scores
at once from sorted probabilities gives the same threshold (checked on the real data and 30 random
cases) 380 times faster. Baseline training fell from 6.4 s to 1.4-1.8 s.

**Demo cost of the richer model, measured against the Phase 5 code** (same machine load, one discovery
worker, in-process):

| | Phase 5 code | Final code |
|---|---|---|
| Full demo | 11.7-12.9 s | 13.0-13.4 s |

Profiling showed two sources of the extra time:

- the extra terms of the wear equation and the per-rollout coefficient draws;
- a per-target least-squares fit in the jump check, now one fit for all targets.

**Final timings** (`data/timings.json`, measured with other applications open):

| Operation | Median | Slowest | Budget |
|---|---|---|---|
| Simulate 20,000 steps | 1.51 s | 1.91 s | 2 s |
| Discovery on one window | 2.34 s | 3.07 s | 3 s |
| Fit the causal model | 0.42 s | 0.45 s | 1 s |
| One what-if | 39 ms | 50 ms | 200 ms |
| Recommendation search | 0.49 s | 0.56 s | 1 s |
| Baseline training | 1.81 s | 1.83 s | 10 s |
| Full benchmark | 57.0 s | 57.0 s | 60 s |

All medians are within budget. One discovery run exceeded 3 s, and the benchmark is close to its
budget; this laptop's timings vary by up to 3x (Phase 2 report).

## Test results

| Check | Result |
|---|---|
| Backend `pytest -q` | **88 passed** (new: robust discovery covered by existing graph tests, 4 connector tests, 3 AI4I tests) |
| `ruff check`, `ruff format --check`, `mypy causaltwin tests` | Clean (62 files) |
| Frontend `npm run lint`, `npx tsc --noEmit`, `npm run build` | Pass |
| Playwright on the Docker stack (`docker compose up`) | **41 passed**: Chromium (resilience, smoke, demo story, accessibility, 1024/768 px), Firefox and WebKit (smoke) |
| Guided demo 5 times in a row (UI) | Identical narration in every run |
| `update_readme_results.py --check` | All generated blocks up to date |

## Known limitations (after this phase)

- **Real machines:** no validation on a real machine. The MTConnect connector is tested against a
  stand-in agent only, and a real machine needs the twin trained on its own history first.
- **Linear failure model:** threshold-type failures are advised poorly (AI4I).
- **Risk estimates:** the twin can under-predict at the edge of its training data, and intervals after an
  action are too narrow (calibration table).
- **Load:** heavy load slows the live simulation; there is one shared simulation per server, and
  many-machine operation is a design only.
- **Accessibility:** automated checks only, no screen-reader user testing; phone widths not tested.
- **Not done by request or needing a person:** deployment, video, slides and sourced statistics.
