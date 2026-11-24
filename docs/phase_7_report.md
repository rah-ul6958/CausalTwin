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
