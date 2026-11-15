# Judge Q&A

Likely questions with prepared, honest answers. Numbers come from the generated blocks, which are
refreshed from the result files. Where the answer is "we have not done that", say so.

## How do you know the graph is correct?

On the simulator we can check it, because we wrote the true graph ourselves. Discovered graph against
the true graph:

<!-- results:graph:start -->
| Metric | With the failure edges | Between sensors and levers only |
|---|---|---|
| True edges | 15 | 13 |
| Edges found (correct) | 15 (15) | 13 (13) |
| Precision | 1.00 | 1.00 |
| Recall | 1.00 | 1.00 |
| F1 | 1.00 | 1.00 |
| Lag accuracy (correct edges) | 1.00 | 1.00 |
| Structural Hamming distance | 0 | 0 |

Missed: none. Extra (false) edges: none.
Trap check: discovered failure causes bearing_temp, tool_wear; vibration is not a discovered cause (failure-screen p = 0.93) although its correlation with failure is +0.25; predicted risk change when vibration is halved: 0.000.
<!-- results:graph:end -->

Every discovered link also carries a bootstrap confidence: how often it reappears when causal discovery
is re-run on 20 resampled versions of the data. The Graph Explorer shows the evidence for each link:
the lag, strength, p-value, confidence and a scatter plot.

On real data there is no answer key. We show confidence and assumptions, and recommend confirming a
recommendation with a small, safe trial change before relying on it.

## What if there are hidden confounders?

That is the main assumption of the method. PCMCI assumes causal sufficiency: every common cause of two
measured variables is itself measured. An unmeasured common cause can create a false link or hide a
true one. The assumption is stated in every "How was this computed?" drawer.

What limits the damage today:

- control levers and environment variables are treated as having no causes inside the system;
- drift detection notices when the learned model stops predicting well;
- a recommendation is a hypothesis that a small trial can confirm.

Not implemented: latent-variable discovery, for example LPCMCI in the same `tigramite` library. That
would be the next step for real plants.

## Why linear models?

- **Speed:** every step stays within the performance budget on a laptop CPU (timings table in the README).
- **Readability:** every equation is shown on a model card, so an engineer can check each coefficient.
- **Fit:** they describe this simulator well; the held-out accuracy is in the table below.

The cost is that real machines have non-linear effects and thresholds that a linear model only
approximates. We measured that cost on the AI4I 2020 dataset, whose failures follow threshold rules:
discovery found the right causes, but the linear failure model gave poor advice there (README, "Third-party
data"). A non-linear failure model is the next step; `tigramite` also has non-linear independence tests we
did not use, because they are much slower.

<!-- results:intervention:start -->
20 random lever changes, 8 seeds each, 30-step horizon, judged against the real simulator:

| Prediction | Result |
|---|---|
| Bearing temperature: direction of change correct | 18 / 19 |
| Bearing temperature: mean absolute error at the horizon end | 1.26 deg C (true changes average 2.93 deg C) |
| Failure risk: direction of change correct | 9 / 9 |
| Failure risk: mean absolute error | 0.57 points (true changes average 1.56 points) |

| Fitted equation | Held-out accuracy |
|---|---|
| motor_current (linear) | R2 0.926 |
| coolant_temp (linear) | R2 0.922 |
| bearing_temp (linear) | R2 0.993 |
| vibration (linear) | R2 0.987 |
| tool_wear (linear) | R2 0.999 |
| failure (logistic) | AUC 0.961 |
<!-- results:intervention:end -->

## How is confidence computed?

Nothing is invented; each value comes from a computation:

- **Edge confidence:** the share of 20 moving-block bootstrap resamples in which causal discovery finds
  the edge again. Below 0.6 it is drawn dashed as uncertain.
- **Risk intervals:** 200 Monte Carlo rollouts of the causal model; the interval runs from the 5th to the
  95th percentile. Each rollout has its own process noise and its own draw of the failure model's
  coefficients (they come from a limited number of failures). Uncertainty in the linear equations'
  coefficients is not included. How often the truth falls inside the interval is measured in the
  benchmark's calibration table: well before an action, too rarely after one. We say this in the UI.
- **Recommendation confidence:** the geometric mean of three parts, each shown in the expandable breakdown:
  - the lowest bootstrap confidence of the edges on its causal path;
  - how tight the predicted risk interval is;
  - the held-out accuracy of the equations on that path.

## Does it work on real data?

Partly, and we say exactly which part:

- **Built:** the Bring Your Own Data page runs causal discovery and fits equations on any uploaded CSV.
- **Built and tested, without a real machine:** a live MTConnect connector and a log-replay source. The
  MTConnect path is tested end to end against a stand-in agent that serves simulator values. With a real
  source the app shows "Live data", drops the ground truth and makes recommendations advisory.
- **Third-party data:** on the AI4I 2020 dataset (synthetic, modelled on a real milling machine, with
  published failure rules), discovery found exactly the documented failure mechanism. The advice was
  weaker than XGBoost + SHAP there, because those failures follow thresholds a linear model cannot express.
  We report that result as it is.
- **Not validated:** nothing has been checked against a real machine, because we have none.

Real data adds requirements:

- the levers must have varied in the history;
- there must be at least 15 failure events, or the failure model refuses to fit;
- the assumptions above need checking with the machine's engineers.

## How does it scale?

Measured today, with one backend process on a laptop: 25 and 50 simulated viewers at once (live stream
plus requests, each with its own session id) got every status answer and no failed live connection;
heavy requests beyond two at a time got a clear "busy, retry". The first four viewers got private
simulations and the rest shared one; under that load the five simulations shared the CPU and each ran
at about 1.4 steps per second instead of 5 (README, "Many simultaneous viewers"). The container runs
within 512 MB.

Many machines is a design, not something built yet:

- **One twin per machine.** A twin is a handful of equations, and a risk estimate takes milliseconds.
- **Discovery only when needed.** Full causal discovery runs only at commissioning and after a drift
  alert.
- **Incremental refits.** Only the changed equations are refitted; rewiring already works this way.
- **Horizontal scaling.** Twins live in worker processes keyed by machine, with a message bus in front.

Each browser tab gets its own private live simulation, so one judge's Reset, fault or guided demo does
not change what another judge sees. The number of private simulations is limited (4 by default, 2 on
Render's free plan), because each watched simulation needs CPU; later viewers share one simulation, and
the top bar says "Shared" instead of "Private". Other limits keep the server responsive: two heavy
computations at a time, 30-second timeouts, and upload size limits.

## What happens when the machine changes?

Drift detection:

1. The twin compares each new reading with its own one-step prediction. A CUSUM test flags a sustained
   change in the errors.
2. It collects 600 fresh steps and re-runs causal discovery.
3. It compares the old and new graphs with confidence intervals on the changed effects.
4. It rewires the graph live, refits only the affected equations, and marks earlier recommendations as
   outdated.

Results on injected faults:

<!-- results:drift:start -->
Each fault is injected at step 3,000 of an otherwise normal run (1 step = 10 s).

| Fault | Seed | Detected after | Graph rewired after | Status |
|---|---|---|---|---|
| coolant_filter_clog | 11 | 2 steps (20 s) | 602 steps | confirmed |
| coolant_filter_clog | 12 | 4 steps (40 s) | 604 steps | confirmed |
| coolant_filter_clog | 13 | 3 steps (30 s) | 603 steps | confirmed |
| lube_leak | 11 | 3 steps (30 s) | 603 steps | confirmed |
| lube_leak | 12 | 3 steps (30 s) | 603 steps | confirmed |
| lube_leak | 13 | 3 steps (30 s) | 603 steps | confirmed |
| bearing_damage | 11 | 3 steps (30 s) | 603 steps | confirmed |
| bearing_damage | 12 | 3 steps (30 s) | 1210 steps | confirmed |
| bearing_damage | 13 | 3 steps (30 s) | 603 steps | confirmed |

Fault-free runs: 60,000 steps, 5 suspected alarms, 0 wrongly confirmed structural changes. Rewiring waits for 600 post-change steps of data before re-discovery.
<!-- results:drift:end -->

## What are the limitations?

- **Simulated machine.** The physics constants and costs are illustrative.
- **No hidden confounders assumed.** See the answer above.
- **Linear effects with short lags** (1 to 3 steps). Threshold-type failures (AI4I) are modelled poorly.
- **What-if holds untouched levers constant.** If an operator moves them during the horizon, predictions
  can be wrong.
- **Risk estimates are right on average but not everywhere.** Across the benchmark the mean prediction
  matches the truth closely, but in extreme states, such as the demo's hot afternoon, the twin
  under-predicts. The cause is measured: the failure model rests on about 90 failures. The demo shows
  the twin and the ground truth side by side, so this is visible.
- **Intervals after an action are too narrow** (calibration table).
- **The benchmark has 30 cases on one simulator.** CausalTwin misses the target in 2 of them; the
  benchmark table lists them.

## Is the baseline a fair comparison?

We made it as fair as we could:

- **Accuracy:** XGBoost is a strong failure predictor (its held-out accuracy is on the Benchmark page,
  "Is the baseline fair?").
- **Same data:** it sees the same sensors as CausalTwin.
- **Documented mapping:** SHAP's top feature becomes the engineer's usual action, for example vibration
  becomes damping, a hot bearing becomes more coolant, a worn tool becomes a tool change.
- **Same judge:** both actions run in forks of the real simulator from the same state with the same
  random seeds.

When SHAP happens to blame a real cause, the baseline can do well. Its losses come from blaming
vibration or the uncontrollable ambient temperature. A different action mapping would change its
numbers, and we say so.

## Didn't you build the simulator to make yourselves look good?

We built it to contain a realistic trap: a symptom that predicts failure but does not cause it. Three
things guard against fooling ourselves:

- **Losses are reported.** The benchmark lists every case, including the ones CausalTwin loses.
- **Identifiability was checked first.** Before building the engine, we checked that the true structure
  can be recovered from data at all (Phase 1 report).
- **The baseline is strong.** It predicts failures well; the test is whether its advice works, not
  whether it predicts.

The honest limit: one simulator is not a factory. Real-data validation is the next step.

## What is new here?

Causal discovery, do-calculus simulation and drift detection each exist on their own. What we combined:

- causal discovery on a live machine;
- a cost-aware search for the smallest fix, with a stated confidence;
- a head-to-head test against the standard explainable-AI approach, judged by ground truth;
- automatic detection and rewiring when the machine's cause-effect structure changes.

All of it is transparent: every number can be traced to how it was computed.
