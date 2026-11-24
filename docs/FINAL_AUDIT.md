# Final audit against the judging criteria

For each criterion: what proves it (features, screens, numbers) and what is still missing. Numbers
come from the generated block below. Status checked at the end of Phase 6 (closing the gaps found in
Phase 5); see `docs/phase_5_report.md` and `docs/phase_6_report.md` for the test runs behind it.

<!-- results:headline:start -->
- **93%** of 30 simulated high-risk cases brought to 10% true failure risk or less by CausalTwin, against **50%** for the strongest predictive baseline (XGBoost predict-then-optimize: the same candidate actions, scored by a failure predictor; no predictive baseline reached the target in more than 57%).
- That baseline said no action was needed in **8 of 30** cases whose true risk was 16% to 25%, and made the machine riskier than doing nothing in **3**; CausalTwin did neither in any case.
- **9 of 9** injected physical faults were detected and the causal graph rewired; median detection delay **3 steps (30 s of machine time)**.
<!-- results:headline:end -->

## Required outputs from the brief

| Required output | Present | Where to demonstrate it | Evidence |
|---|---|---|---|
| Working causal AI engine | Yes | Graph Explorer, System Console | Graph recovery against the true graph (README, "Causal graph recovery") |
| Dynamic causal graph (continuously updated) | Yes | Graph Explorer: live values on nodes, drift rewiring animation, version history; guided demo beat 7 | Drift table: every injected fault detected and the graph rewired |
| What-if simulator | Yes | What-If Lab (sliders, ripple through the graph, before/after forecasts) | Intervention accuracy table (direction and size of change against the real simulator) |
| Intervention recommendation engine (smallest intervention) | Yes | Advisor: cheapest change that meets the target, every evaluated option in a sortable table | Correlation Trap benchmark |
| Confidence / explanation layer | Yes | "How was this computed?" drawers, confidence bars and breakdowns, root-cause paths, symptoms list, model cards, activity log | Confidence computed from bootstrap, rollouts and held-out accuracy (docs/JUDGE_QA.md) |
| Demonstration on a simulated dataset | Yes | Guided demo (8 beats, fixed seed), Benchmark page, sample CSVs in `backend/data/samples/` | Demo plays end to end from the UI; five runs in a row were identical |

## Technical Implementation (25%)

- **Engine:**
  - PCMCI with full-condition MCI and a joint lag test, plus a logistic failure screen and moving-block
    bootstrap confidence.
  - Linear causal equations, batched Monte Carlo what-if, and a cost-aware optimizer with an independent
    re-check.
  - CUSUM drift detection with re-discovery and partial refits.
- **System:**
  - FastAPI with typed Pydantic schemas and a provenance block on every result, plus a WebSocket stream.
  - Next.js frontend, strict TypeScript.
- **Quality:**
  - 96 backend tests: simulator, engine, intelligence, API, hardening, concurrency chaos, connectors,
    per-viewer sessions, the AI4I check, docs.
  - 42 browser test runs: every page, the full demo, two viewers side by side, outage, reconnect, random
    clicking, an axe-core accessibility audit and narrow screens in Chromium; the page smoke tests also
    in Firefox and WebKit.
  - ruff, mypy and ESLint all clean.
- **Performance:** every step within its budget (README timings table, measured by
  `scripts/measure_timings.py`).
- **Load:** 25 and 50 simultaneous simulated viewers against one backend process, measured (README,
  "Many simultaneous viewers").
- **Gaps:** watched simulations share the CPU, so heavy load slows them (about 1.8 steps per second
  instead of 5 with 25 busy viewers and five simulations on a laptop); Edge and mobile browsers are not
  tested.

## Innovation and Originality (20%)

- **Correlation Trap benchmark:** the standard explainable-AI workflow (XGBoost + SHAP, top feature
  mapped to an action) against causal recommendations, both judged by the real simulator, losses included.
  Since Phase 7 the main comparison is the strongest predictive baseline (B3, predict-then-optimize: the
  same candidate actions scored by XGBoost), with two SHAP baselines (B1, B2) still reported, and two
  flags per method: made things worse than doing nothing, and "no action" while the risk was high.
- **Causal drift detection with live rewiring:** the twin notices its own structure is outdated,
  re-discovers it and invalidates old advice.
- **Glass-box UI:** every number traceable to its method, data window, parameters, timing and
  assumptions; ground truth shown only as a labelled check.
- **Gap:** the individual methods are established (PCMCI, do-interventions, CUSUM); the originality is in
  combining them and proving the result.

## Problem Solving and Impact (20%)

- **Answers why, which lever and how much:** root-cause paths; the cheapest change below the risk
  target; time to effect, side effects and cost.
- **Measured advantage over the standard approach:** see the headline numbers.
- **Real-world path:**
  - Bring Your Own Data works on any CSV, with a "no ground truth" warning.
  - A live MTConnect connector and log replay are built and tested (against a stand-in agent).
  - On the third-party AI4I 2020 dataset, discovery found the documented failure mechanism.
- **Gaps:**
  - not validated on a real machine;
  - on AI4I, the linear failure model's advice lost to XGBoost + SHAP (threshold-type failures);
  - no industry impact figures are given: the README states no downtime statistic without a linked source.

## User Experience (15%)

- **Pages:** eight, with a guided demo that navigates and highlights each beat.
- **States:** loading skeletons, error messages with Retry on every panel, and a visible connection
  status that reconnects on its own.
- **Honesty markers:** a Simulated badge everywhere (a "Live data" badge when connected to a machine);
  ground truth clearly labelled and absent for real data.
- **Accessibility:** no WCAG 2.1 A/AA violations found by axe-core on any page (including colour
  contrast); keyboard-scrollable lists; reduced-motion support.
- **Layout:** no horizontal scrolling at 1440, 1280, 1024 and 768 px (tested).
- **Gaps:** automated audits do not replace testing with screen-reader users; phone widths not tested.

## Scalability and Feasibility (10%)

- **Lightweight:** no GPU and no deep learning. A twin is a handful of equations, and a risk estimate
  takes milliseconds.
- **Small servers:** the Docker image runs within 512 MB. At a local stand-in for Render's free plan
  (0.1 CPU), the full demo takes about half a minute (docs/DEPLOYMENT.md).
- **Protected under load:** concurrency limits, timeouts, input and upload limits, and a live loop that
  pauses and reports instead of crashing.
- **Design for many machines** (one twin per machine, discovery only on drift, incremental refits): see
  the README.
- **Measured load:** 50 simultaneous viewers got every status answer and kept their live streams; heavy
  requests beyond two at a time got "busy, retry".
- **One simulation per viewer:** each browser tab gets its own private live simulation, demo and stream,
  so judges cannot disturb each other (tested with two browsers side by side).
- **Gaps:**
  - multi-machine operation is a design, not built;
  - private simulations are limited by CPU (4 by default, 2 on Render's free plan); later viewers share
    one, and the top bar says so.

## Presentation and Demo (10%)

- **Spoken script:** docs/DEMO_SCRIPT.md has a 3-minute script matched to the guided demo, a 30-second
  version, a backup plan and a video-recording checklist.
- **Deck and Q&A:** docs/SLIDES_OUTLINE.md (7 slides with generated numbers) and docs/JUDGE_QA.md.
- **Screenshots:** every page and the key demo beats, in `docs/screenshots/`.
- **Gaps (manual):**
  - the deck itself;
  - the backup video and README GIF;
  - the live deployment link (deployment was not part of this run, by request).

## Gaps fixed in Phase 5

- **Concurrency:** API threads, the demo and the background loop could change the live simulator at the
  same time. A lock now serializes every change, and a chaos test (4 threads, 160 random actions)
  passes.
- **Silent loop death:** a failing step used to stop the background loop silently. It now pauses,
  writes an error to the activity log and raises an alert.
- **Unbounded requests:**
  - What-if requests had no limits; a single one could exhaust the server.
  - BYOD uploads had no size limits.
  - Heavy requests had no timeouts.
- **A request crash:** a `NaN` in a request body caused a server error; it now gets a readable 422.
- **Error handling:**
  - Unexpected errors now return readable JSON that the browser can read (CORS headers included).
  - Pages could show endless loading skeletons when the backend was down; every panel now shows the
    error with Retry.
  - Browser requests had no timeout.
- **Small containers:** the demo's drift beat took more than 6 minutes under a 0.1-CPU limit because of
  spinning math-library threads; it now takes about 20 s.
- **Demo polish:**
  - the demo narration printed a raw Python dictionary;
  - a failed demo beat was skipped instead of retried.

## Gaps closed after Phase 5 (Phase 6)

- **Two always-missed causal links found.** Rare tool-chipping jumps hid the effect of load and speed on
  tool wear. Jumps that a variable's causes cannot explain are now left out of its test and fit (and kept
  in its noise). Graph recovery is now 15 of 15 true links with no false ones, in all five scenarios.
- **Risk under-prediction investigated and quantified.** The cause is estimation noise in the failure
  model (about 90 failures), not a bias. Every risk estimate now includes the failure model's
  coefficient uncertainty, and the benchmark measures calibration (prediction against truth in every
  case). A longer training history was tried and rejected on the evidence (worse calibration, false
  drift confirmations).
- **Drift confirmation tightened.** Changes are confirmed only in the equation whose predictions drifted
  (all nine real faults still detected; no false confirmations in 60,000 fault-free steps).
- **AI4I 2020 stretch goal done**, with an honest result (see above).
- **Live machine connector built:** MTConnect and log replay, with a "Live data" mode in the UI.
- **Accessibility audit, other browsers and narrow screens**: tested and fixed (see User Experience).
- **Load test:** 25 and 50 viewers measured; heavy requests now work on a snapshot so the live loop
  keeps more of its pace (2.4 to 3.1 steps per second at 25 viewers).
- **Faster baseline training** (6.4 s to 1.4 s) with an identical decision threshold.
- **One simulation per viewer:** every browser tab has its own live simulation, so one judge's Reset,
  fault or demo no longer changes what others see (limited number; see Scalability).

## Gaps left (honest list)

- Not deployed (by request); `render.yaml`, Dockerfiles and docs/DEPLOYMENT.md are ready.
- No validation on a real machine (none available); the connector is tested against a stand-in agent.
- The failure model is linear in its causes: threshold-type failures (AI4I) are advised poorly. A
  non-linear failure model is the next engineering step.
- The twin can under-predict risk in extreme states at the edge of its training data (visible in the demo
  next to the ground truth); intervals after an action are too narrow.
- Private simulations are limited by CPU (4 by default, 2 on Render's free plan); viewers beyond the
  limit share one. Many-machine operation is designed, not built.
- Manual items: slides, video, GIF and the live URL in the README (full list in `docs/phase_7_report.md`).
