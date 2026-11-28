# Slides outline (7 slides)

For whoever builds the deck. Each slide has a title, the one message it must land, the visuals to
use (screenshots are in `docs/screenshots/`), and the numbers to show. Numbers come from the generated
blocks below, which are refreshed from the result files by `backend/scripts/update_readme_results.py`;
copy them from here rather than typing them. Label anything from the simulator as simulated.

## Numbers for the deck (generated)

<!-- results:headline:start -->
- **93%** of 30 simulated high-risk cases brought to 10% true failure risk or less by CausalTwin, against **50%** for the strongest predictive baseline (XGBoost predict-then-optimize: the same candidate actions, scored by a failure predictor; no predictive baseline reached the target in more than 57%).
- That baseline said no action was needed in **8 of 30** cases whose true risk was 16% to 25%, and made the machine riskier than doing nothing in **3**; CausalTwin did neither in any case.
- **9 of 9** injected physical faults were detected and the causal graph rewired; median detection delay **3 steps (30 s of machine time)**.
<!-- results:headline:end -->

## Slide 1. The problem: prediction is not understanding

- **Key message:** Predictive maintenance says "failure is likely" but not why or what to change, so
  engineers guess, and a wrong guess costs downtime.
- **Visual:** a plain alarm ("Failure risk: HIGH") next to three question marks: Why? Which lever? How much?
- **Numbers:** none of ours. If you use an industry figure for downtime cost, it must have a source;
  otherwise leave the number out (no `[source needed]` placeholders on slides).

## Slide 2. Why correlation fails: the vibration trap

- **Key message:** Vibration rises before failures, so standard ML (XGBoost + SHAP) blames it, but in
  this machine vibration does not cause failure; damping it leaves the risk unchanged.
- **Visual:** `03-graph-explorer.png` (correlation and causal overlay), or the landing page's animated
  illustration (`01-landing.png`, labelled "Illustration").
- **Numbers:** the vibration-blame count of B1 (naive SHAP) from the benchmark block; the trap check from the graph block
  (correlation with failure, failure-screen p-value, zero risk change when vibration is halved).

## Slide 3. CausalTwin architecture

- **Key message:** Discover causes from data, simulate interventions on the causal model, recommend the
  cheapest fix, watch for the machine changing, and check everything.
- **Visual:** the Mermaid architecture diagram from the README (render it to an image), with the eight
  pages of the app underneath.
- **Numbers:** the timings table (every step within its performance budget; no GPU, no deep learning).

<!-- results:timings:start -->
| Operation | Median | Slowest run | Budget | Within budget |
|---|---|---|---|---|
| Simulate 20,000 steps | 274 ms | 285 ms | 2.00 s | yes |
| Causal discovery on one 3,000-step window | 683 ms | 688 ms | 3.00 s | yes |
| Fit the causal model | 66 ms | 73 ms | 1.00 s | yes |
| One what-if simulation (200 rollouts) | 6 ms | 8 ms | 200 ms | yes |
| Recommendation search | 138 ms | 184 ms | 1.00 s | yes |
| Baseline XGBoost training | 261 ms | 267 ms | 10.00 s | yes |
| Full benchmark (30 cases) | 12.90 s | 12.90 s | 60.00 s | yes |

Measured 2026-11-28 on Linux (16 logical CPUs, Python 3.11.18) by `scripts/measure_timings.py`. Laptop timings vary up to about 3x with CPU throttling (see the Phase 2 report).
<!-- results:timings:end -->

## Slide 4. Live demo

- **Key message:** The guided demo: risk rises, cheapest causal fix, apply, risk drops, a hidden fault,
  drift detected, graph rewired, new advice.
- **Visual:** switch to the live app (script: `docs/DEMO_SCRIPT.md`). Fallback: screenshots
  `09-demo-risk-rises.png` to `13-demo-new-recommendation.png`.
- **Numbers:** read live from the screen; they are deterministic for the demo seed.

## Slide 5. Proof: benchmark and validation

- **Key message:** On 30 simulated high-risk moments, judged by the real simulator, CausalTwin's fix works
  far more often than the strongest predictive baseline we could build (B3: XGBoost scoring the very same
  candidate actions), and the losses are reported too. The predictive baselines sometimes said "no
  action needed" while the risk was high, or made things worse than doing nothing; CausalTwin never did.
  Have the answer to "Is your baseline a straw man?" ready from `docs/JUDGE_QA.md`.
- **Visual:** `06-benchmark.png`; a bar chart of the success rates below; the graph-recovery table.
- **Numbers:**

<!-- results:benchmark:start -->
| Metric | CausalTwin | B3: predict-then-optimize | B2: actionable SHAP | B1: naive SHAP |
|---|---|---|---|---|
| Reached the 10% target (true risk) | **28 / 30 (93%)** | 15 / 30 (50%) | 17 / 30 (57%) | 1 / 30 (3%) |
| Mean true risk after the action | **3.8%** | 10.2% | 14.4% | 30.1% |
| Mean risk reduction | **26.7 points** | 20.2 points | 16.0 points | 0.3 points |
| Failure rate within 30 steps (simulated futures) | **0.0%** | 5.0% | 10.0% | 23.8% |
| Mean cost (illustrative units) | **6.6** | 4.5 | 5.7 | 2.5 |
| Made things worse than doing nothing | **0** | 3 | 6 | 1 |
| Said no action while the true risk was above the target | **0** | 8 | 0 | 6 |

B1 maps the top SHAP feature to an engineer's action (vibration becomes damping, ambient temperature means no action). B2 takes the top SHAP feature among the four levers and tool wear. B3, the main comparison, scores CausalTwin's own candidate actions (same grid, costs, target and planning margin) with the XGBoost model, which can only change a lever's value in its input row.

No action: mean true risk 30.4%. Head to head against B3: CausalTwin better in 14, tied in 13, worse in 3 case(s): 0, 7, 27; against B2: better in 13, tied in 12, worse in 5; against B1: better in 29, tied in 1, worse in 0.
CausalTwin missed the target in 2 case(s): case 28 (tool_wear, true risk after 13.4%), case 29 (tool_wear, true risk after 11.2%).
B3: predict-then-optimize made things worse than doing nothing in case(s) 9, 14, 16; said no action while the risk was high in case(s) 8, 10, 11, 13, 15, 19, 21, 29.
B2: actionable SHAP made things worse than doing nothing in case(s) 8, 11, 14, 18, 23, 24.
B1: naive SHAP made things worse than doing nothing in case(s) 11; said no action while the risk was high in case(s) 0, 7, 8, 19, 21, 28.

| Scenario | Cases | CausalTwin reached target | B3: predict-then-optimize reached target | B2: actionable SHAP reached target | B1: naive SHAP reached target |
|---|---|---|---|---|---|
| normal | 6 | 100% | 83% | 83% | 0% |
| overload | 6 | 100% | 17% | 33% | 0% |
| cooling_failure | 6 | 100% | 17% | 67% | 17% |
| heat_wave | 6 | 100% | 67% | 33% | 0% |
| tool_wear | 6 | 67% | 67% | 67% | 0% |

What B1: naive SHAP blamed (top SHAP feature): vibration 22, ambient_temp 6, lube_rate 1, tool_wear 1.

What B2: actionable SHAP blamed (top actionable SHAP feature): tool_wear 17, lube_rate 7, coolant_flow 4, spindle_speed 1, load_pct 1.

Calibration: the twin's predicted risk against the simulator's true risk in the same cases.

| Twin prediction | Cases | Mean predicted | Mean true | Mean absolute error | Truth inside the 90% interval | Truth below / above it | Correlation |
|---|---|---|---|---|---|---|---|
| Risk with no action | 30 | 30.8% | 30.4% | 4.0 points | 90% | 3 / 0 | 0.96 |
| Risk after its own action | 30 | 3.5% | 3.8% | 1.1 points | 43% | 16 / 1 | 0.97 |
| ... after a lever change | 11 | 7.7% | 9.4% | 1.8 points | 91% | 0 / 1 | 0.22 |
| ... after a tool change | 19 | 1.1% | 0.5% | 0.7 points | 16% | 16 / 0 | 0.91 |

Settings: 6 situations per scenario, 8 seeds each, 30-step horizon; benchmark run took 14 s.
<!-- results:benchmark:end -->

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

## Slide 6. Real-world use and scalability

- **Key message:** The same engine runs on any factory's sensor log today (Bring Your Own Data, with a
  clear "no ground truth" warning), and a live MTConnect connector exists (tested against a stand-in
  agent, not yet a real machine). On third-party data (AI4I 2020) it found the documented failure
  mechanism, but its linear failure model advised poorly on threshold-type failures: say so. Many-machine
  scaling is designed, not built; one server handled 50 simultaneous viewers, and every browser tab gets
  its own live simulation (up to a CPU-bound limit; later viewers share one).
- **Visual:** `08-data.png` (upload panel); a simple diagram: machine -> MTConnect/OPC UA/MQTT ->
  connector -> one twin per machine -> alerts and advice.
- **Numbers:** the AI4I comparison and the load test from the README ("Third-party data", "Many
  simultaneous viewers"), and drift detection (how quickly a changed machine is noticed):

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

## Slide 7. Impact and next steps

- **Key message:** Small and medium manufacturers get experiment-free answers to "why" and "what to
  change", with evidence they can check.
- **Next steps (honest):** validate on a real machine's data with its engineers; a live MTConnect/OPC UA
  connector; non-linear effects and hidden-confounder checks; one twin per machine at factory scale.
- **Numbers:** repeat the two headline numbers; nothing new. Any market or downtime statistic needs a
  real, linked source; otherwise leave it out.
