# CausalTwin frontend

The glass-box command center for CausalTwin: a Next.js (App Router, TypeScript strict) app that
shows only what the backend API computes. All machine data in this UI is simulated and labelled
as such.

## Pages

| Route | Page |
|---|---|
| `/` | Landing: the idea, an illustration of the correlation trap, live benchmark numbers |
| `/control` | Control Room: risk gauge and history, live sensors, controls, events, alerts |
| `/graph` | Graph Explorer: causal vs correlation graph, edge evidence, node equations, drift diffs, versions |
| `/whatif` | What-If Lab: move levers, see the simulated risk and downstream forecasts |
| `/advisor` | Advisor: root cause, the cheapest fix, the ML baseline's advice, verification after applying |
| `/benchmark` | Benchmark: CausalTwin vs XGBoost + SHAP across many simulated cases, losses included |
| `/console` | System Console: pipeline timings against budgets, activity log, model cards, configuration |
| `/data` | Data: raw rows, event log, data quality, Bring Your Own Data upload, CSV export |

## Run

Start the backend first (from `../backend`):

```bash
.venv/Scripts/python.exe -m uvicorn causaltwin.api.app:app --port 8000
```

Then the frontend:

```bash
npm install
cp .env.example .env.local   # only needed if the backend is not on localhost:8000
npm run build
npx next start -p 3000       # or: npm run dev
```

Open http://localhost:3000.

## Checks

```bash
npm run lint
npx tsc --noEmit
npx playwright install chromium   # once
npm run test:e2e                  # smoke test per page, then the full guided demo
```

The end-to-end tests start the backend and frontend if they are not already running, and
save screenshots to `../docs/screenshots/`. The guided-demo test resets the live simulation.

## Code layout

- `src/app/` routes (one thin page per route) and global styles
- `src/components/pages/` one component per page
- `src/components/graph/`, `charts/`, `transparency/`, `shell/`, `common/` shared pieces
- `src/components/ui/` shadcn/ui primitives
- `src/lib/` typed API client, WebSocket client, Zustand live store, formatting, config
