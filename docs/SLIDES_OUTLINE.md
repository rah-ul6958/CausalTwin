# Slides outline (7 slides)

For whoever builds the deck. Each slide has a title, the one message it must land, the visuals to
use (screenshots are in `docs/screenshots/`), and the numbers to show. Numbers come from the generated
blocks below, which are refreshed from the result files by `backend/scripts/update_readme_results.py`;
copy them from here rather than typing them. Label anything from the simulator as simulated.

## Numbers for the deck (generated)

<!-- results:headline:start -->
- **93%** of 30 simulated high-risk cases brought to 10% true failure risk or less by CausalTwin, against **3%** for a standard XGBoost + SHAP approach.
- In **22 of 30** cases the standard approach blamed vibration, which has no causal effect on failure in the simulator.
- **9 of 9** injected physical faults were detected and the causal graph rewired; median detection delay **3 steps (30 s of machine time)**.
<!-- results:headline:end -->

## Slide 1. The problem: prediction is not understanding

- **Key message:** Predictive maintenance says "failure is likely" but not why or what to change, so
  engineers guess, and a wrong guess costs downtime.
- **Visual:** a plain alarm ("Failure risk: HIGH") next to three question marks: Why? Which lever? How much?
- **Numbers:** none of ours. If you use an industry figure for downtime cost, it must have a source;
  otherwise leave `[source needed]`.

## Slide 2. Why correlation fails: the vibration trap

- **Key message:** Vibration rises before failures, so standard ML (XGBoost + SHAP) blames it, but in
  this machine vibration does not cause failure; damping it leaves the risk unchanged.
- **Visual:** `03-graph-explorer.png` (correlation and causal overlay), or the landing page's animated
  illustration (`01-landing.png`, labelled "Illustration").
- **Numbers:** the vibration-blame count from the headline block; the trap check from the graph block
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
| Simulate 20,000 steps | 1.37 s | 1.39 s | 2.00 s | yes |
| Causal discovery on one 3,000-step window | 2.02 s | 2.04 s | 3.00 s | yes |
| Fit the causal model | 389 ms | 399 ms | 1.00 s | yes |
| One what-if simulation (200 rollouts) | 39 ms | 45 ms | 200 ms | yes |
| Recommendation search | 425 ms | 493 ms | 1.00 s | yes |
| Baseline XGBoost training | 6.39 s | 6.87 s | 10.00 s | yes |
| Full benchmark (30 cases) | 49.95 s | 49.95 s | 60.00 s | yes |

Measured 2026-11-15 on Windows (16 logical CPUs, Python 3.11.16) by `scripts/measure_timings.py`. Laptop timings vary up to about 3x with CPU throttling (see the Phase 2 report).
<!-- results:timings:end -->

## Slide 4. Live demo

- **Key message:** The guided demo: risk rises, cheapest causal fix, apply, risk drops, a hidden fault,
  drift detected, graph rewired, new advice.
- **Visual:** switch to the live app (script: `docs/DEMO_SCRIPT.md`). Fallback: screenshots
  `09-demo-risk-rises.png` to `13-demo-new-recommendation.png`.
- **Numbers:** read live from the screen; they are deterministic for the demo seed.

## Slide 5. Proof: benchmark and validation

- **Key message:** On 30 simulated high-risk moments, judged by the real simulator, CausalTwin's fix works
  far more often than the standard approach, and the losses are reported too.
- **Visual:** `06-benchmark.png`; a bar chart of the success rates below; the graph-recovery table.
- **Numbers:**

<!-- results:benchmark:start -->
| Metric | CausalTwin | XGBoost + SHAP |
|---|---|---|
| Reached the 10% target (true risk) | **28 / 30 (93%)** | 1 / 30 (3%) |
| Mean true risk after the action | 3.7% | 30.1% |
| Mean risk reduction | 26.7 points | 0.3 points |
| Failure rate within 30 steps (simulated futures) | 0.0% | 23.8% |
| Mean cost (illustrative units) | 6.6 | 2.5 |

No action: mean true risk 30.4%. Head to head: CausalTwin better in 29, tied in 1, worse in 0 case(s).
CausalTwin missed the target in 2 case(s): case 28 (tool_wear, true risk after 12.3%), case 29 (tool_wear, true risk after 11.2%).

| Scenario | Cases | CausalTwin reached target | Baseline reached target |
|---|---|---|---|
| normal | 6 | 100% | 0% |
| overload | 6 | 100% | 0% |
| cooling_failure | 6 | 100% | 17% |
| heat_wave | 6 | 100% | 0% |
| tool_wear | 6 | 67% | 0% |

What the baseline blamed (top SHAP feature): vibration 22, ambient_temp 6, lube_rate 1, tool_wear 1.
Settings: 6 situations per scenario, 8 seeds each, 30-step horizon; benchmark run took 40 s.
<!-- results:benchmark:end -->

<!-- results:graph:start -->
| Metric | With the failure edges | Between sensors and levers only |
|---|---|---|
| True edges | 15 | 13 |
| Edges found (correct) | 13 (13) | 11 (11) |
| Precision | 1.00 | 1.00 |
| Recall | 0.87 | 0.85 |
| F1 | 0.93 | 0.92 |
| Lag accuracy (correct edges) | 1.00 | 1.00 |
| Structural Hamming distance | 2 | 2 |

Missed: load_pct->tool_wear (lag 1), spindle_speed->tool_wear (lag 1). Extra (false) edges: none.
Trap check: discovered failure causes bearing_temp, tool_wear; vibration is not a discovered cause (failure-screen p = 0.93) although its correlation with failure is +0.25; predicted risk change when vibration is halved: 0.000.
<!-- results:graph:end -->

## Slide 6. Real-world use and scalability

- **Key message:** The same engine runs on any factory's sensor log today (Bring Your Own Data, with a
  clear "no ground truth" warning); live machine connectors and many-machine scaling are designed, not
  built.
- **Visual:** `08-data.png` (upload panel); a simple diagram: machine -> MTConnect/OPC UA/MQTT ->
  connector -> one twin per machine -> alerts and advice.
- **Numbers:** drift detection (how quickly a changed machine is noticed):

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
  source: `[source needed]`.
