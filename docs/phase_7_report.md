# Phase 7 Report - Making CausalTwin Judge-Proof

This phase makes targeted fixes so the results survive a technical judge's questions. All machine
data is still **simulated** unless stated. Every number below was produced by code in this repository;
the README's result tables are generated from the result files.

**Where the numbers were measured.** This phase ran on a different computer from Phases 1 to 6: a
Windows laptop with 16 logical CPUs, with the Python backend run inside Docker (the project's own
`python:3.11-slim` image plus ruff and mypy; Python 3.11.18, all 16 logical CPUs visible). The frontend checks ran on
Node.js 26 on Windows. Timings from earlier phases were measured on the original development laptop
and are not directly comparable with the ones here.

## Starting point

Measured before any Phase 7 change (commit `6b3f042`).

**Checks.** Backend: 96 tests passed (`python -m pytest -q`), `ruff check .` clean, `mypy causaltwin
tests` clean (64 source files). Frontend: `npm run lint` clean, `npx tsc --noEmit` clean, `npm run
build` succeeded (9 routes).

**Headline numbers** (from the result files, as shown in the README):

| Result | Value |
|---|---|
| CausalTwin reached the 10% target (true risk) | 28 / 30 (93%) |
| XGBoost + SHAP (naive top-feature rule) reached the target | 1 / 30 (3%) |
| Mean true risk after the action: CausalTwin / baseline / no action | 3.8% / 30.1% / 30.4% |
| Cases where the baseline blamed vibration | 22 / 30 |
| Causal graph recovery | 15 / 15 true links, no false links, SHD 0 |
| Injected faults detected and rewired | 9 / 9, median detection delay 3 steps (30 s) |
| AI4I 2020: CausalTwin / XGBoost + SHAP success | 37% / 65% |

**Benchmark reproducibility and time on this computer.** Re-running the benchmark here (to a scratch
file, without touching `data/benchmark_results.json`) reproduced every summary number above exactly,
and took 12.0 s (the README's 57.0 s was measured on the original laptop).

**Not present.** The task description refers to a reference script
`backend/scripts/experiment_stronger_baselines.py` and the numbers it produced. That script is not in
the repository, so the stronger baselines below were implemented from the task description and every
number reported for them was measured by the new code, not copied from the task description.

## A1. Fair, stronger baselines instead of a straw man

**Problem.** The headline compared CausalTwin with one baseline (B1) that maps XGBoost's top SHAP
feature to an engineer's action. At the 30 benchmark moments that feature was vibration (a symptom) in
22 cases and ambient temperature (not controllable, so "no action") in 6. A technical judge can fairly
call that a straw man.

**What changed.**

- `intelligence/baseline.py`: two new baselines on the same trained XGBoost model.
  - **B2, actionable SHAP** (`recommend_actionable`): the top SHAP feature among the four levers and
    tool wear (acted on with a tool change), with B1's rule for moving a lever.
  - **B3, predict-then-optimize** (`recommend_counterfactual`): CausalTwin's own candidate set
    (`build_candidates`: same single-lever grid, lever pairs, tool change and costs), every candidate
    scored by XGBoost, the same target (10%), planning margin (0.8) and ranking rule as the optimizer.
    A candidate changes only that lever's value in the feature row; a tool change sets tool_wear to 0.
    No action is chosen when the predicted risk is already at or below the target, or when no
    candidate lowers it (the optimizer's rule). The module docstring explains why B3 is the strongest
    fair predictive baseline.
  - `recommend_all` computes the SHAP values once and returns all three.
- `intelligence/benchmark.py`: every case evaluates CausalTwin and all three baselines on the same
  forks with the same seeds. Each method's result stores `no_action`, `worse_than_no_action` (true risk
  after > no-action true risk + 0.01, `BENCHMARK_WORSE_MARGIN`) and `false_reassurance` (chose no
  action while the true no-action risk was above the target). The summary has these counts and case
  lists for every method, CausalTwin included, and head-to-head results against each baseline. The
  single `winner` is now against B3. Identical actions are simulated once per case (forks with the same
  seed are deterministic, so this changes no number).
- API: `GET /recommend` returns `baseline_actions` (all three), `main_baseline` and `baseline_labels`;
  `baseline_action` is now B3. The guided demo's recommendation beat quotes B3. `GET /config` lists the
  three methods.
- Frontend: the Benchmark page has a method comparison table (all four methods), a highlighted callout
  listing every case where a baseline made things worse than doing nothing or said "no action" at high
  risk (computed from the results), one column per baseline in the per-case table, and all four methods
  in the charts and the verdict strip. The Advisor's baseline card has a B1 / B2 / B3 selector (default
  B3). The landing page's duplicated "93%" and "28 / 30" cards became one card for CausalTwin against
  B3 and one for the number of times a predictive baseline made things worse than doing nothing.
- Docs: `update_readme_results.py` rewrites the headline, quote and benchmark blocks with B3 as the
  main comparison, and a new generated `straw_man` block answers "Is your baseline a straw man?" in
  `docs/JUDGE_QA.md`. The README's problem statement, signature-feature text and architecture diagram,
  the slides outline, the final audit and the demo script (beat 3) name B3 where a single baseline is
  meant.
- Constants in `config.py`: `BASELINE_METHOD_LABELS`, `MAIN_BASELINE`, `BASELINE_ACTIONABLE_FEATURES`,
  `BENCHMARK_WORSE_MARGIN`.
- Tests (5 new, 2 extended): B2 only blames levers or tool wear and never vibration; B3's choice is the
  cheapest of the optimizer's candidates predicted below target x margin, with the predicted risk of
  the edited row; B3 does nothing at a calm state; `recommend_all` returns all three; the two flags on
  hand-made inputs; the end-to-end benchmark stores flags that match `outcome_flags` and summary counts
  that match the cases; `/recommend` returns all three baselines with B3 as the main one.

**Results** (30 cases, judged by the simulator; from `data/benchmark_results.json`):

| Method | Reached the 10% target | Mean true risk after | Mean cost | Worse than doing nothing | "No action" while true risk > 10% |
|---|---|---|---|---|---|
| CausalTwin | 28 / 30 (93%) | 3.8% | 6.6 | 0 | 0 |
| B3: predict-then-optimize (main) | 15 / 30 (50%) | 10.2% | 4.5 | 3 (cases 9, 14, 16) | 8 |
| B2: actionable SHAP | 17 / 30 (57%) | 14.4% | 5.7 | 6 | 0 |
| B1: naive SHAP (old baseline) | 1 / 30 (3%) | 30.1% | 2.5 | 1 | 6 |

Before this task the README compared 28 / 30 with 1 / 30. CausalTwin's and B1's numbers did not change
(the models retrain identically; every CausalTwin and B1 number matches the starting point).

- **Head to head with B3:** CausalTwin left a lower true risk in 14 cases, tied in 13 and was worse in
  3 (cases 0, 7, 27). In all 3 both methods reached the target; B3 changed the tool (cost 8.0) where
  CausalTwin's lever changes cost 2.9 to 3.9. In 12 of the 13 ties both changed the tool.
- **Where B3 fails, and why.** Its 8 "no action" cases had true no-action risks of 16% to 25%, while
  XGBoost predicted 2.3% to 8.8% for the same rows. Its 3 harmful actions lowered coolant flow or
  lubrication. Both follow from holding bearing and coolant temperature fixed while a lever moves:
  the model never sees the benefit of cooling, or the harm of less of it.
- **B2 reached the target more often than B3** (17 against 15) but left a higher mean true risk (14.4%
  against 10.2%), a higher failure rate (10.0% against 5.0%) and made things worse in 6 cases. B3 stays
  the main comparison because it searches every action CausalTwin searches; the generated straw-man
  block states this with the numbers, and the README headline adds that no predictive baseline reached
  the target in more than 57% of cases.
- **In the guided demo** (beat 3, demo seed), B3 says "No action: XGBoost predicts 0.4% failure risk"
  while the simulator's true risk within the horizon is 48.7% (CausalTwin predicts 34.7%). B1 blames
  bearing temperature and adds coolant (+25%); B2 blames lube rate (+25%). The demo script now points
  at this.

**Differences from the figures in the task description.** The task quoted, for B3, 50% success, 10.1%
mean true risk, 4 cases worse than doing nothing and 5 "no action needed" cases. Measured here: 50%,
10.2%, 3 and 8. The missing reference script could not be checked, but the stored predictions suggest
the reason for the "no action" difference: 5 of the 8 cases had an XGBoost prediction below 8% (the
target times the planning margin) and 3 had one between 8% and 10%. A rule that decides "no action"
against 8% would give 5. B3 here uses CausalTwin's own rule (decide against the 10% target, search
against 8%) so both methods are treated identically; this was decided from the optimizer's code, not
by trying both rules on the benchmark cases. B2 matched the task's figures exactly (17 / 30, 14.4%, 6
cases worse).

**Timings** (`scripts/measure_timings.py`, this computer; all within budget):

| Operation | Before (starting point, this computer) | After | Budget |
|---|---|---|---|
| Full benchmark (30 cases) | 12.0 s | 12.6 s (13.1 s in the results run) | 60 s |
| Recommendation search (optimizer) | not re-measured | 122 ms median | 1 s |
| What-if (200 rollouts) | not re-measured | 7 ms median | 200 ms |
| Baselines per `/recommend` call | B1 only: 1.5 ms median | all three: 10.7 ms median, 48.6 ms max | (inside the 1 s recommendation budget) |

The README's timing table now shows this computer's measurements (Linux in Docker, 16 logical CPUs), which are faster
than the original laptop's; the note under the table says where they were measured.

**What did not work or was not done.**

- The reference script `scripts/experiment_stronger_baselines.py` named in the task is not in the
  repository (see above).
- The Playwright end-to-end tests were updated (new Advisor selector, Benchmark comparison and callout,
  landing cards) but not run: no Playwright browsers are installed on this computer, and running them
  would also overwrite the screenshots in `docs/screenshots/`, which still show the old pages.
- The result files were rewritten on Linux, so `drift_results.json` and `validation_results.json` now
  have LF instead of CRLF line endings and appear as whole-file changes in git. Their content is
  unchanged apart from the NumPy-versus-tigramite agreement check, which moved by about 1e-8.

**Checks after A1.** Backend: 101 passed (96 before), ruff clean, ruff format clean, mypy clean.
Frontend: lint clean, `tsc --noEmit` clean, build succeeded.

## A2. Recommendations: uncertainty-aware selection, and the brief's wording

**What changed.**

- `optimizer.py` has two selection rules behind `OPTIMIZER_SELECTION_RULE` (config), and `recommend()`
  takes a `selection_rule` argument for comparisons:
  - **confidence** (new): an option qualifies only if at least `OPTIMIZER_REQUIRED_CONFIDENCE` = 0.8
    of its rollouts end at or below the target risk (the share is computed from each rollout's risk,
    which `batch_failure_risk` now returns as `per_rollout_risk`);
  - **margin** (Phase 3): the mean risk is at or below target x 0.8.
  The finalist re-check with independent noise uses the same rule. 0.8 was set before any run and was
  not tuned.
- Every recommendation also returns the **minimum effective change** (the smallest single-lever change
  that qualifies under the active rule, re-checked with the finalists' independent rollouts; `None`
  when no single lever qualifies) and the **least effective lever** (the lever whose best single
  change lowers the predicted risk least, flagged negligible below 1 point).
- Every recommendation has a computed **headline** in the brief's form, shown at the top of the
  Advisor card and as the first sentence of the guided demo's recommendation beats. In the demo
  (beat 3): "Reducing spindle_speed by 25% and increasing lube_rate by 25% is predicted to cut failure
  risk from 35% to 8% within 5 minutes; changing coolant_flow helps least (best case -7.8 points)." No
  single lever reaches the target at that moment, so the card says so instead of showing a minimum
  change. At low risk the headline reads "No change is needed: failure risk is predicted at ..."
- The Advisor card shows the minimum effective change and the least effective lever under the action.
- New script `scripts/compare_selection_rules.py` runs the benchmark once per rule on the same cases
  and seeds and writes `data/selection_rule_comparison.json`.
- Tests (3 new, 3 extended): both rules' qualification; the headline's form and numbers; the minimum
  effective change is the smallest qualifying single-lever change in the candidate table; the least
  effective lever matches the table; the optimizer runs under either rule and rejects an unknown one;
  the API returns the headline; the demo narration starts with it.

**Comparison of the two rules** (`data/selection_rule_comparison.json`; same 30 cases and seeds):

| Rule | Reached the target | Mean cost | Mean true risk after | After-action: mean predicted / true | MAE | Truth inside the 90% interval | Missed cases |
|---|---|---|---|---|---|---|---|
| margin (kept) | 28 / 30 | 6.58 | 3.8% | 3.5% / 3.8% | 1.1 points | 43% | 28, 29 |
| confidence, 0.8 | 24 / 30 | 6.14 | 5.1% | 4.7% / 5.1% | 1.2 points | 53% | 7, 19, 21, 27, 28, 29 |

**Decision: the margin rule stays active**, because success dropped with the confidence rule (the
task's condition). The confidence rule picked cheaper, smaller changes in 14 cases; 4 of them then
ended just above the target (true risk 10.1% to 12.3%), and it did not fix cases 28 and 29 either (it
chose an even cheaper lever pair in case 28, 15.7% true risk). The measured reason is the one A3
addresses: the rollouts are too narrow (the true risk after the action falls inside the 90% interval
in only 43% of cases), so "80% of rollouts at or below 10%" is a weaker requirement than it sounds.
The rule stays in the code and is re-measured after A3 (below).

**Problem 1 is therefore not solved by this task.** Cases 28 and 29 still miss the target with the
active rule (13.4% and 11.2% true risk). No other change was tried for them, because any rule picked
by looking at those two cases would be tuned on the benchmark.

**Checks after A2.** Backend: 104 passed, ruff and ruff format clean, mypy clean. Frontend: lint clean,
`tsc --noEmit` clean, build succeeded. Benchmark 13.2 s, recommendation search 118 ms median (budgets 60 s and 1 s).

## A3. Honest calibration: coefficient uncertainty, and the check on screen

**What changed.**

- `engine/causal_model.py`: every linear equation stores the approximate covariance of its
  (intercept, coefficients) at fit time (`ridge_covariance`; the formula is in its docstring:
  s2 A^-1 Xc'Xc A^-1 with A = Xc'Xc + penalty I on the centred design, the intercept's variance and
  covariance from the column means). The drift refit stores it too, for the coefficients it re-estimates.
- `engine/whatif.py`: every rollout draws each equation's intercept and coefficients from that
  covariance (`_equation_parameters`), on a random stream of its own (`WHATIF_EQUATION_PARAMETER_STREAM`),
  so the noise and failure-model draws are unchanged; switch `WHATIF_EQUATION_PARAMETER_UNCERTAINTY`
  (on). Both the single what-if and the optimizer's batched evaluator use it. The assumption text shown
  in the "How was this computed?" drawers says so.
- `intelligence/benchmark.py`: the calibration summary also records how many true risks fall below and
  above the interval, and the after-action check split by action kind (lever change or tool change);
  the README's calibration table shows both.
- Control Room: next to the ground-truth box, "Inside the twin's 90% interval: yes / no" and this
  session's running count ("this session: inside in N of M checks"), computed by the live loop each
  time the ground truth and the twin's risk are computed at the same step. The guided demo panel shows
  the interval and the result, and beats 2 and 5 say it in the narration, for example (beat 2):
  "Simulator ground truth (validation only): 48.7%, inside the twin's 90% interval (20.7%-50.7%)".
- Docs: the README limitation, the judge Q&A (how intervals are computed, and the limitations list)
  describe the new state.
- Tests (4 new, 1 extended): the covariance formula equals statsmodels' OLS covariance without a
  penalty; every fitted equation has a symmetric positive semi-definite covariance; with all covariances
  set to zero the rollouts equal the switched-off engine's exactly (the new stream leaves the others
  untouched); the live interval check counts correctly; demo beats 2 and 5 state the check.

**Result: the 80% after-action coverage target was not reached.** Benchmark calibration (30 cases):

| Twin prediction | Before A3: inside the 90% interval | After A3 | Mean absolute error before / after |
|---|---|---|---|
| Risk with no action | 87% | 90% (misses: 3 below, 0 above) | 4.1 / 4.0 points |
| Risk after its own action | 43% | 43% (misses: 16 below, 1 above) | 1.1 / 1.1 points |
| ... after a lever change (11 cases) | - | 91% (1 above: case 28) | 1.8 points |
| ... after a tool change (19 cases) | - | 16% (16 below) | 0.7 points |

The equation coefficients are estimated from thousands of rows, so their uncertainty is small and
drawing them hardly widens the intervals. The split shows where the after-action misses come from:
after a lever change the interval covers the truth in 10 of 11 cases; after a tool change the twin
predicts a small risk (mean 1.1%) and the truth is smaller still (mean 0.5%), below the interval in
16 of 19 cases. That is a limit of the logistic failure model (it cannot go as low as the real hazard
of a fresh tool), not of the coefficient uncertainty, and the misses are all far below the 10% target.
As the task asked, no factor was fitted on the benchmark cases to widen the intervals; the measured
values are reported.

**Selection rules re-measured after A3** (`data/selection_rule_comparison.json`): margin 28 / 30,
confidence 23 / 30 (one fewer than before A3). The margin rule stays active.

**Latency** (`scripts/measure_timings.py`): what-if 7 ms median (budget 200 ms), recommendation search
142 ms median, 176 ms slowest (was 118 ms; budget 1 s), benchmark 14.5 s (budget 60 s).

**Checks after A3.** Backend: 108 passed, ruff and ruff format clean, mypy clean. Frontend: lint clean,
`tsc --noEmit` clean, build succeeded.

## A4. No misleading screens when the risk is low

**What changed.**

- Root cause (Advisor and Graph Explorer): below the target risk the panel says "Risk is below the
  target; nothing to explain right now" and folds the current contributions behind "Show what
  contributes most anyway". The Graph Explorer then also stops highlighting a path on the graph.
  (Shared component `components/common/below-target.tsx`.)
- Ineffective levers (Advisor): shown only when the predicted risk is above the target; otherwise the
  panel explains that every lever would look ineffective only because the risk is already low.
  Symptoms are still listed: they are structural, not risk-dependent.
- Baselines: B1 and B2 now return "No action (its model predicts low risk): XGBoost predicts X%
  failure risk, at or below the 10% target." when their own predicted risk is at or below the target,
  in the API and the UI; B3 uses the same wording. The Advisor's baseline card shows "Would do:
  Nothing" in that case, and B3's note about frozen temperatures only appears when B3 acts.
- SHAP chart: always includes the blamed feature (outlined), red bars push the risk up and green bars
  push it down, sorted by signed value, with the axis labelled "pushes risk up / down (log-odds)".
- Tests: backend, all three baselines do nothing at a calm state and B1 still acts at a high-risk
  one, and the benchmark records that B1 and B2 always act there. Playwright, a new test opens the
  Advisor and the Graph Explorer at low risk and checks the new messages, the hidden ineffective-lever
  list, the baselines' "no action" and the folded contributions.

**The benchmark is not unaffected, contrary to the task's expectation, so B1 and B2 keep acting there.**
The benchmark's moments have a high true risk, but XGBoost's own prediction is at or below 10% at
several of them (B3's eight "no action" cases). Applying the low-risk rule to B1 and B2 in the
benchmark was measured with `run_benchmark(models, baseline_low_risk_rule=True)`: B1 would go from
1 / 30 to 0 / 30 (11 "no action" at high risk instead of 6), and B2 from 17 / 30 to 15 / 30 (8 "no
action" at high risk, 4 instead of 6 cases worse than doing nothing). Changing the baselines after
the fact in a way that lowers B2's success would undercut A1, so the benchmark keeps B1 and B2 as
defined in A1 (`baseline_low_risk_rule=False`, recorded in the results' settings), which is the more
favourable choice for them; the rule applies in the API and UI only. B3 always had the rule by design.
All benchmark numbers are unchanged by A4.

**End-to-end tests now run.** Playwright's Chromium was installed on this computer and the whole
Chromium suite was run against the backend (Docker) and the production frontend build: 25 of 25
passed, including the new low-risk test, the A1 Advisor and Benchmark checks (one A1 test used the
wrong ARIA role for the selector and was fixed), the accessibility (axe) checks on every page and the
full guided demo story. Firefox and WebKit were not run (not installed). The run refreshed the
documentation screenshots in `docs/screenshots/`, which now show the Phase 7 pages.

**Checks after A4.** Backend: 109 passed, ruff and ruff format clean, mypy clean. Frontend: lint clean,
`tsc --noEmit` clean, build succeeded; Playwright (Chromium) 25 passed. Timings: what-if 7 ms,
recommendation search 152 ms, benchmark 12.9 s (budgets 200 ms, 1 s, 60 s).

## A5. Deployment readiness and presentation gaps

**What changed.**

- **Fonts work offline.** `src/app/layout.tsx` loads Inter, Space Grotesk and JetBrains Mono with
  `next/font/local` from files committed under `frontend/src/fonts/` (the Latin, normal-style variable
  builds from the Fontsource 5.3.0 packages, 22-48 kB each), with their SIL Open Font License files and
  a README naming the source. Checked: `npm run build` succeeds with the network routed to a dead proxy,
  and the build output references no Google Fonts address. The demo script's backup plan mentions it.
- **Type check order.** Reproduced: on a tree without `.next/`, `npx tsc --noEmit` fails with "Cannot
  find name 'LayoutProps'". New script `npm run typecheck` (`next typegen && tsc --noEmit`) passes on the
  same tree. The README and `frontend/README.md` use it. (Earlier phase reports keep the command they ran.)
- **README placeholders.** The two `[source needed]` sentences are removed, not replaced: no statistic
  with a verified, linked source was added, and none was invented. The slides outline and the final audit
  now say to leave a number out rather than leave a placeholder.
- **Hosting plan.** `docs/DEPLOYMENT.md` section 9 gives exact steps for the two judged-demo options:
  (A) the Render 1 CPU / 2 GB instance (`1c-2g`) during the judging window with
  `CAUSALTWIN_MAX_SESSIONS=3`, switched back afterwards, with a warning that a Blueprint sync can
  reapply `render.yaml`'s values; (B) `docker compose` on the laptop behind two Cloudflare quick tunnels
  (API and frontend), with `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL` and `CAUSALTWIN_CORS_ORIGINS`
  set to the tunnel addresses and the required rebuild. `render.yaml` is unchanged (`plan: free`). The
  Render plan name and sizes come from the existing section 1 (checked against Render's documentation
  on 2026-11-15); the Cloudflare and Render dashboard steps were not executed from here.

## Manual items for the human

These need a person, an account or a recording, and are not done:

1. **Deploy** (docs/DEPLOYMENT.md): Render backend and Vercel frontend, or option B of section 9; choose
   the judged-demo option and rehearse it once.
2. **Live demo link** in README.md line 8: `[live demo link: add after deployment, see docs/DEPLOYMENT.md]`.
3. **Demo video**: record it (docs/DEMO_SCRIPT.md, "Recording the backup video") and replace
   `[video link: add after recording, see docs/DEMO_SCRIPT.md]` in README.md line 9.
4. **GIF**: export the 10-second loop and replace `[GIF placeholder: ...]` in README.md line 13.
5. **Slides**: build them from docs/SLIDES_OUTLINE.md; use only numbers from the generated blocks, and
   any industry statistic only with a real, linked source.
6. **Optional**: Firefox and WebKit end-to-end runs (`npx playwright install firefox webkit`, then
   `npm run test:e2e`); only Chromium was run in this phase.

## End of Part A

Every result was regenerated in the required order (validation, benchmark with drift, AI4I, timings,
selection-rule comparison, README blocks; `update_readme_results.py --check` reports every block up to
date). Checks: backend 109 passed, ruff, ruff format and mypy clean; frontend lint, `npm run typecheck`
and build pass; Playwright (Chromium) 25 of 25 passed against the backend in Docker and the production
frontend build, which also refreshed `docs/screenshots/`. All timings are within budget (benchmark 13.9 s,
recommendation search 145 ms median, what-if 9 ms median).

# Part B - A non-linear failure model

## B1 diagnosis (before any code change)

Measured with the new `scripts/diagnose_ai4i.py` (`data/ai4i_diagnosis.json`), on the current code: the
same fit and the same action search as `scripts/run_ai4i.py`, judged by the documented rules.

**What the failure screen can see.** Only variables with the role "sensor" are screened as direct causes
of failure (`FAILURE_CANDIDATE_ROLES`; levers and environment are assumed to act through sensors). In
the AI4I mapping speed, torque and tool wear are levers, so the screen tests only four variables:
temperature difference (p = 2.7e-25, cause), power (p = 5.8e-29, cause), wear x torque (p = 1.4e-29,
cause) and process temperature (p = 0.67, not a cause). **Neither speed nor tool wear is ever a
candidate cause**; they reach the failure model only through power (torque x speed) and wear x torque.
The linear failure model is logit = -0.691 x temperature difference + 0.00071 x power + 0.00023 x wear
x torque - 2.77: risk rises steadily with power and with wear x torque.

| Failure mode | Rows | Removed | Median predicted risk with no action | What CausalTwin chose | Why it fails |
|---|---|---|---|---|---|
| HDF (heat dissipation) | 115 | 0 | 13.6% | torque down 89, speed down 25, no action 18, tool change 9 | The rule needs speed raised above 1,380 rpm. Speed enters only through power, whose coefficient is positive, so every action lowers speed or torque; 0 of 115 raised speed to the limit. HDF is still present after every action. |
| PWF, power below 3,500 W | 31 | 0 | 0.09% | no action in all 31 | A linear model says lower power is always safer, so low-power rows look very safe (100% predicted below the 5% target) and nothing is done. |
| PWF, power above 9,000 W | 64 | 45 | 22.5% | torque down 45, speed down 16, tool change 11, no action 8 | Lowering power is right; in 16 rows the predicted risk fell below the target before power fell below 9,000 W. |
| OSF (overstrain) | 98 | 83 | 25.3% | torque down 68, tool change 34, speed down 20 | Mostly right. Of the 15 misses, cutting torque or speed caused HDF (6) or low-power PWF (5), which the model cannot see; 3 kept OSF. |
| TWF (tool wear) | 46 | 4 | 3.4% | no action 30, torque down 12, tool change 4, speed down 2 | Only a tool change removes it. Tool wear is not a candidate cause and wear x torque is near the healthy level for these rows (65% predicted below the target), so a tool change was chosen in 4 of 46. |

**What a non-linear model on the same causes can and cannot fix.** A spline on power can express the
3,500-9,000 W window, so the low-power PWF rows (31) and the late stop in high-power rows (16) are
reachable, and the HDF/PWF side effects of OSF actions may shrink. HDF needs speed itself and TWF needs
tool wear itself; neither is a discovered cause (they are not even candidates), so a failure model that
uses only the discovered causes cannot fix them, linear or not. Changing which roles are screened would
change discovery, which this task does not allow; it is reported as the next step instead.

## B1 result: a spline failure model on the discovered causes

**What changed.**

- `engine/causal_model.py`: `SplineFailureModel`, a logistic regression on cubic B-spline features of
  each discovered failure cause (`SplineTransformer`, 5 knots at quantiles, the basis that duplicates
  the intercept dropped), optionally with the standardised product of every pair of causes; L2 penalty
  `FAILURE_SPLINE_C` = 1.0, intercept unpenalised. The parameter covariance is the inverse of the
  penalised Fisher information (formula in the docstring), so each what-if rollout draws its own
  parameters exactly as for the linear model. Each basis is tabulated on a 256-point grid over the
  training range and interpolated (constant beyond the range), which keeps it fast. Settings are in
  `config.py`; they were set before any run and not tuned on anything judged.
- **One interface for both failure models**: `logit(columns, parameters)`, `point_parameters()` and
  `local_coefficients(current, reference)`. `whatif.py` (single what-if and the optimizer's batched
  evaluator), the optimizer, root cause (slope between the normal level and the current value) and the
  AI4I search all go through it. The linear model keeps exactly its previous arithmetic (tested), and
  every simulator result reproduced unchanged with it.
- `EngineSettings.failure_model` ("linear" default, or "spline") and
  `CausalTwinEngine.with_failure_model(kind)`, a copy with only the failure model refitted on the same
  discovered causes, so comparisons are like for like.
- Explainable: the spline model's card carries one "how risk depends on this cause" curve per cause;
  `POST /byod/upload` takes `failure_model`, and the Data page has a "Failure model" choice and draws
  the curves for uploaded data.
- Scripts: `scripts/diagnose_ai4i.py` (the diagnosis above), `scripts/compare_failure_models.py`
  (simulator benchmark per failure model, `data/failure_model_comparison.json`); `run_ai4i.py` now
  scores the linear, spline and spline-with-products models; the README's AI4I table shows all of them.
- Tests (4 new, 1 extended): the spline model learns the AI4I power window (risk higher at both ends of
  the power range than at its safest point) and has a higher held-out AUC than the linear model on the
  same causes, with one curve per cause on its card; the spline model runs through both what-if paths
  (batched equal to single) and the optimizer; an unknown failure model is rejected; the linear model's
  logit through the interface is the plain logistic arithmetic; an upload with the spline model returns
  curves exactly when failure causes were discovered, and an invalid choice is a 400.

**AI4I 2020, per failure mode** (`data/ai4i_results.json`; 330 scored rows; judged by the documented
rules; fitted on the data only):

| Failed rows whose failure the action removes | Linear | Spline | Spline + products | XGBoost + SHAP |
|---|---|---|---|---|
| All scored rows | 123 (37%) | 184 (56%) | 179 (54%) | 213 (65%) |
| Heat dissipation (115) | 0% | 21% | 17% | 22% |
| Power (95) | 47% | 87% | 88% | 77% |
| Overstrain (98) | 85% | 93% | 94% | 82% |
| Tool wear (46) | 9% | 2% | 0% | 93% |
| Held-out AUC of the failure model | 0.814 | 0.909 | 0.931 | 0.964 (XGBoost) |

Wins and losses against XGBoost + SHAP with the spline model: it wins on power (87% against 77%) and
overstrain (93% against 82%), ties on heat dissipation (21% against 22%), and loses badly on tool wear
(2% against 93%), so it still loses overall (56% against 65%). Against the linear model it wins on
every mode except tool wear (2% against 9%). The diagnosis predicted the pattern: the power window is
now expressible; heat dissipation improved only partly, through power, because speed itself is not a
discovered cause; tool-wear failures need tool wear itself, which is not even a candidate cause. The
pairwise products raised the AUC but did not remove more failures.

**Simulator benchmark, per failure model** (`data/failure_model_comparison.json`; same engine, same
30 cases and seeds):

| | Linear | Spline | Spline + products |
|---|---|---|---|
| Reached the 10% target | 28 / 30 (missed 28, 29) | 28 / 30 (missed 13, 27) | 28 / 30 (missed 13, 27) |
| Mean true risk after the action | 3.8% | 1.5% | 1.4% |
| Mean cost | 6.59 | 7.46 | 7.62 |
| Held-out AUC of the failure model | 0.961 | 0.921 | 0.920 |
| No action: mean absolute error / truth inside the 90% interval | 4.1 points / 90% | 7.9 points / 50% | 7.7 points / 53% |
| After the action: truth inside the 90% interval | 43% | 3% | 7% |
| Recommendation search, median | 140 ms | 476 ms | 556 ms |

**Decision: the linear model stays the simulator's default.** The spline model reaches the target
equally often and leaves a lower risk, but it pays more for it, predicts less accurately (AUC, error)
and is much worse calibrated; the task's condition ("at least as good there") is not met. The simulator's
failure mechanism is close to logistic-linear in its causes, which is why the linear model fits it
better. The spline model is kept as an option for data with threshold-type failures (AI4I, uploads).
All timings stay within budget with either model.

**Not done, and why.** Making HDF and TWF fixable would need speed and tool wear as candidate direct
causes of failure, which changes discovery (`FAILURE_CANDIDATE_ROLES`). The task allows only the
discovered causes, so this is left as the next step, not attempted.

**Checks after Part B.** Backend: 112 passed, ruff, ruff format and mypy (`causaltwin scripts tests`,
79 files) clean; every result regenerated and the README blocks up to date. Frontend: lint,
`npm run typecheck` and build pass; Playwright (Chromium) 25 of 25 passed.
