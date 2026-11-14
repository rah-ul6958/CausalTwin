# Phase 4 Report - Premium Frontend

All machine data in the app is **simulated**, and every view says so. Ground-truth values appear
only in clearly labelled validation and benchmark views. Every number in the UI comes from the
backend API: either computed results or configuration served by `GET /config`. Numbers below
come from the test runs, the live server's `/metrics` and `/logs`, and the production build.

## What was built

A Next.js 16 (App Router, TypeScript strict) "glass-box command center" in `frontend/`. It uses
only the real API and WebSocket.

| Route | Page | Main contents |
|---|---|---|
| `/` | Landing | Pitch, animated correlation-to-causation illustration (labelled "Illustration"), three key numbers loaded from `/benchmark` and `/drift-results`, How-it-works strip |
| `/control` | Control Room | Animated risk gauge (twin estimate with interval) and a labelled "Simulator ground truth" indicator, risk history chart, 10 live sensor charts (rolling buffer of 300 readings, about 4 redraws per second), simulation controls (pause, resume, step, reset, speed, scenario, inject fault, release), event timeline, alert feed |
| `/graph` | Causal Graph Explorer | React Flow graph with Correlation / Causal / Overlay toggle, edge drawer (lag, strength, p-value, bootstrap confidence bar, scatter of cause versus lagged effect, explanation), node drawer (equation, R squared or AUC, residual error, causes and effects), root-cause path in red, amber symptom tags, animated drift diff (removed edges fade, new edges draw in, changed edges flash), graph version history |
| `/whatif` | What-If Lab | One slider per lever with range and cost, debounced `/whatif`, ripple through the graph in lag order, before and after forecasts with 90% bands, risk before and after, time to effect, negligible-effect message |
| `/advisor` | Intervention Advisor | Recommendation card (change, risk before and after with intervals, cost, side effects, time to effect, expandable confidence breakdown, Apply), XGBoost + SHAP baseline side by side, root-cause path, symptoms and ineffective levers, verification panel (predicted versus observed after applying), sortable virtualized table of every evaluated option |
| `/benchmark` | Benchmark | Summary cards, comparison and "who was blamed" charts, every case including losses, graph recovery metrics with true versus discovered graph, drift delays per fault and false alarms, "Is the baseline fair?", methodology, re-run with live WebSocket progress |
| `/console` | System Console | Pipeline tracker (Ingest, Discover, Fit, Simulate, Optimize, Drift check) against the budgets, live activity log (filter by step, level and text; auto-scroll with pause), system metrics, model cards for every equation and the baseline, configuration viewer |
| `/data` | Data | Virtualized raw data table, event log, data quality report, CSV export, Bring Your Own Data (upload, column roles, analysis, graph and model cards, with a "no ground truth exists" banner) |

Shared pieces:
- **Transparency components:**
  - `HowComputed`: a drawer that shows the API's `provenance` block (method, window, parameters, timing, assumptions).
  - `ConfidenceBar`, `SimulatedBadge`, `GroundTruthBadge`, `TimingChip`, and `InfoTip` tooltips on technical terms.
- **Live data layer:**
  - A typed API client (`src/lib/api.ts`) and a WebSocket client with exponential reconnect, batching messages every 250 ms (`src/lib/ws.ts`).
  - A Zustand store with rolling buffers (`src/lib/store.ts`).
  - A visible connection indicator: Live, Connecting, or Offline - retrying.
- **Guided demo panel:** floating panel that plays the 8 backend demo beats (`/demo/start`, `/demo/next`). It navigates to the right page and spotlights the relevant panel, scrolling it into view.
- **Visual system:**
  - Colours: deep-space background with a drifting dot grid, glass panels, and a fixed palette: cyan for causal, violet for what-if, amber for warnings, red for risk, emerald for improvement.
  - Fonts: Space Grotesk, Inter and JetBrains Mono, with tabular numbers.
  - Motion: Framer Motion panel staggers and count-ups, a cursor-follow light on panels, and quick page crossfades via React `<ViewTransition>`.
  - Accessibility: `prefers-reduced-motion` turns off non-essential animation.
- Loading skeletons and error states with retry on every async panel. `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_WS_URL` configure the backend (see `frontend/.env.example`).

### Backend additions made for the frontend

| Addition | Why |
|---|---|
| `api/info_routes.py`: `GET /config`, `/events`, `/alerts`, `/graph/history`, `/risk/history`, `/drift-results`, `/validation/true-graph` | The UI shows settings, histories and labelled ground truth from the API instead of hardcoding them |
| `run_id` on live frames, risk updates and status (increases on every reset) | A reset or demo start used to freeze the charts (new steps were lower than old ones); the store now detects a new run, clears it and re-seeds |
| `/risk/history` (last 600 estimates) | The risk chart has data as soon as a page opens, also while the loop is paused |
| `true_risk_step` in risk updates | Ground truth is recomputed only every 10 steps (and, in the demo's fast-forward, at the end of each batch); charts now plot it only where it was computed instead of carrying a stale value forward |
| Routine activity-log buffer (`routine` flag, `GET /logs?routine=include\|exclude\|only`) | The per-step risk estimate (5 per second) was pushing every other entry out of the log; it now has its own buffer and a toggle in the Console |
| Alerts written to the activity log as warnings; graph versions recorded on confirmed drift | Console level filter and version history |
| `utils/formatting.py` | Explanations read "4,300 to 3,440" instead of "4.3e+03 to 3.44e+03" |
| `PERFORMANCE_BUDGETS_MS`, `LIVE_RISK_HISTORY_POINTS`, `ACTIVITY_LOG_MAX_*` in `config.py` | Single source of truth for the new constants |

## How to run

```bash
# Backend (from backend/)
.venv/Scripts/python.exe -m uvicorn causaltwin.api.app:app --port 8000

# Frontend (from frontend/)
npm install
cp .env.example .env.local      # only if the backend is not on localhost:8000
npm run build && npx next start -p 3000      # or: npm run dev
# open http://localhost:3000

# Checks
npm run lint && npx tsc --noEmit
npx playwright install chromium               # once
npm run test:e2e                              # starts both servers if they are not running
```

The first backend start after a source change retrains and caches the models (15.8 s measured in
this phase); later starts load the cache.

## Checks performed

| Check | Result |
|---|---|
| `npm run build` | Passes. All 8 pages (plus the not-found page) prerender as static shells; data loads client-side |
| `npm run lint` (ESLint 9, Next config, React Compiler hook rules) and `npx tsc --noEmit` | No errors, no warnings |
| Backend `pytest -q` | **69 passed** in 86 s (Phase 4 added 3: information endpoints, routine log buffer, value formatting) |
| Backend `ruff check`, `mypy causaltwin tests` | Clean (50 files) |
| Playwright smoke tests (`tests/smoke.spec.ts`), live backend | 9 passed: each page loads and shows live data with no page errors, plus a check that no page scrolls horizontally at 1280 px |
| Playwright demo story (`tests/story.spec.ts`) | Passed in 33 s. It drives the whole demo from the UI alone: open the panel, Start, then Next through all 8 beats. Each beat must navigate to its page, show its title and narration, and render the expected content (for example "Applied at step", "Version 2" after drift) |
| Screenshot review | Every page and five demo beats reviewed by eye from the saved screenshots |

About "manually verify the demo story": the story was checked by the Playwright script above,
which uses only the UI (no API shortcuts), and by reviewing the screenshot of each key beat. A
person clicking through it in a browser was not part of this phase's checks.

The review of the first round of screenshots found these problems, now fixed:
- The demo panel did not float. A custom CSS class overrode Tailwind's `fixed` utility, so the panel sat at the bottom of the page. Custom component classes now live in `@layer components`.
- The demo navigated to the graph after the drift had already been pushed, so the rewiring animation was never seen. A graph update younger than 60 s is now replayed when the page opens.
- The verification chart drew a stale ground-truth value as if it had been measured (see `true_risk_step` above).
- Hardcoded figures in UI text, now read from the API:
  - "5 minutes", "30 steps" and "50 rollouts".
  - The 10% alert threshold.
  - The fault step and seconds per step in the drift panel.
  - The baseline's 20% test split.
  - "five scenarios".
- The risk axis showed repeated "0%" ticks, a long unit was truncated, and the reduction column showed exponent notation.
- The landing text said the twin "proves its answer". It now says the twin runs on a simulator whose true causes are known, so answers are checked against that ground truth.
- The landing KPI said "fixed". It now says "brought to 10% true risk or less", with the target read from the benchmark settings.

## Measured results

Live server on the development laptop, with the live loop running at 5 steps/s:

| Operation | Measured | Budget |
|---|---|---|
| Recommendation search, `GET /recommend` (6 calls) | 0.45-0.89 s, one call 1.18 s | < 1 s |
| What-if, 200 rollouts | 33-41 ms | < 200 ms |
| Discovery on 3,000 steps (session start) | 1.31 s | < 3 s |
| Fit causal model | 0.39 s | < 1 s |
| Baseline XGBoost training | 5.9 s | < 10 s |
| Re-discovery after drift (600 steps) | 1.55 s | - |
| Guided demo, all 8 beats through the UI | 33 s including page loads and screenshots | - |

Frontend bundle: 1.93 MB of JavaScript across all routes (605 KB gzipped), with no 3D or WebGL
libraries. Charts and graph nodes are memoized, and WebSocket messages are applied in batches
every 250 ms.

Screenshots (`docs/screenshots/`):
- One per page: `01-landing` to `08-data`.
- Demo beats: `09-demo-risk-rises`, `10-demo-recommendation`, `11-demo-applied`, `12-demo-drift-rewire`, `13-demo-new-recommendation`.
- `14-control-room-1280` at laptop width.

## Known issues and limitations

- **One recommendation search took 1.18 s** on the live server, over the 1 s budget, while the live loop and other requests shared the CPU. The other five calls took 0.45-0.89 s. The Console flags any over-budget run.
- **The twin under-predicts risk in the demo's hot afternoon.** In the demo screenshot the twin says 37.9% where the ground truth says 58.3%. The live ground truth comes from only 4 simulator forks every 10 steps, so it is itself noisy. Both values are shown side by side.
- **Ground truth is sparse in fast-forward.** During demo beats it is computed only at the end of each batch. The verification chart shows dots where it was computed and labels the line between them as interpolation.
- **Browser coverage:**
  - Tested only in Chromium (Playwright). Page crossfades use the View Transitions API; browsers without it switch pages instantly.
  - The layout targets 1280 px and wider. Narrower screens stack the panels but were not tested.
- **Accessibility was not formally audited.** Main controls have aria labels and keyboard access. The connection status, benchmark progress and demo narration are announced through `aria-live`. Live sensor values are deliberately not announced, because they change several times a second. Colour contrast was not measured with a tool.
- **Small text in compact graphs.** The What-If ripple graph and the benchmark's true versus discovered graphs use small labels; the full Graph Explorer is the readable view.
- **The demo panel covers the bottom-right corner** of the page while open. It can be closed and reopened from the top bar.
- **Small race after a reset.** A history request already in flight when the simulation resets can briefly add rows from the old run. The next reconnect or reset re-seeds.
- **The backend serves one live simulation per process.** Every browser sees, and controls, the same machine.
