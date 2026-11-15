# Phase 5 Report - Hardening, Containers, Deployment Files, Docs and Demo Assets

All machine data is **simulated**. Result numbers in the README and the docs are generated from the
result files by `backend/scripts/update_readme_results.py`; a test fails if they go stale. Numbers in
this report were measured during this phase; how they were measured is stated next to them.

**Not deployed.** As requested, nothing was deployed to Render, Vercel or anywhere else, and nothing was
pushed. The deployment files are ready, and `docs/DEPLOYMENT.md` lists the manual steps. Docker Desktop
was started on the development laptop to build and test the images locally.

## What was built

### Step 1: end-to-end hardening

**Backend**

- **Thread safety:**
  - Before: the background loop, demo beats and API requests changed the live simulator from different
    threads with no lock.
  - Now: one re-entrant lock in `LiveLoop` guards every change. Engine reads during a computation hold
    it too, so a drift rewiring cannot happen halfway through a recommendation.
- **A loop that cannot die silently:**
  - Before: if a step raised an error, the background task ended and the app stayed frozen.
  - Now: steps run in a worker thread. A failure pauses the loop, logs an `error` entry and raises a
    "System error" alert; Resume continues.
- **Heavy requests:**
  - What-if, root cause, recommendation, multi-step runs and upload analysis run in worker threads.
  - At most 2 run at once; more get 503 "busy, retry".
  - Each has a timeout: 30 s, or 120 s for uploads, then 504 with an explanation.
  - Demo beats are serialized; a double click gets a clear 409.
- **Input validation:**
  - What-if limits: horizon at most 120 steps, 10 to 1,000 rollouts, at most 8 interventions,
    noise-scale bounds, lever settings inside each lever's range, percent changes from -90% to +300%.
  - `NaN` and infinity are rejected. String fields have length limits.
  - Validation errors return a readable one-line `detail`.
- **Unexpected errors:** they return JSON 500 with a clear message and are logged. They pass through the
  CORS middleware, so the browser can read them.
- **Upload limits:** 20 MB, 50,000 rows and 40 columns. Oversized uploads are refused from the
  `Content-Length` header before the body is read.
- **Public-deployment switches:**
  - `CAUSALTWIN_ALLOW_BENCHMARK_RUN=0` disables benchmark re-runs with a clear 403.
  - `CAUSALTWIN_CORS_ORIGIN_REGEX` allows Vercel preview addresses.
  - `CAUSALTWIN_DISCOVERY_JOBS` sets the number of discovery worker processes (memory).
- **Demo beats:** a failed beat is retried on the next click instead of skipped. The beat-2 narration no
  longer prints a raw Python dictionary.

**Frontend**

- **Request timeouts:** every API call times out (35 s; 130 s for uploads; 300 s for demo beats) with a
  plain message.
- **No endless skeletons:** every API-backed panel now shows the error with a Retry button instead of a
  skeleton forever.
- **New pages:** a route error boundary, a global error page and a not-found page.
- **Live stream:** WebSocket reconnect now adds random jitter to its 1 to 10 s backoff.
- **Errors in the UI:** "error" level in the Console's log filter; "System error" alerts in the Control
  Room.

**New tests**

| Test | What it checks |
|---|---|
| `backend/tests/test_hardening.py` (9 tests) | Readable 422s (including `NaN`), lever and percent limits, benchmark switch (403), upload limits (400/413), JSON 500 with CORS headers, CORS regex, busy (503) and timeout (504), loop pauses and reports on a failing step then resumes, 45 seeded random API calls while the loop runs never give a 5xx |
| `backend/tests/test_chaos.py` | 4 threads x 40 random actions at once (faults, scenarios, steps, resets, recommend and apply, what-if, demo beats) while the loop runs at 100 steps/s: no unexpected error, no deadlock, the loop keeps stepping |
| `backend/tests/test_docs.py` (2 tests) | Every generated block in README.md and docs/ matches the result files |
| `frontend/tests/resilience.spec.ts` (3 tests) | Every page with the backend unreachable shows errors with Retry, never blank; a dropped WebSocket reconnects and data flows again; 18 seeded random clicks in the Control Room (faults, scenarios, steps, clears, release, pause and resume) leave the app live with no page errors |
| `frontend/tests/story.spec.ts` | `DEMO_RUNS=5` plays the guided demo five times in a row with Restart and requires identical narration in every run |

**Measured timings.** `backend/scripts/measure_timings.py` writes `backend/data/timings.json`, from which
the README's table is generated. Run on the development laptop with no servers running; median of
repeated runs:

| Operation | Median | Budget |
|---|---|---|
| Simulate 20,000 steps | 1.37 s | 2 s |
| Causal discovery on one window | 2.02 s | 3 s |
| Fit the causal model | 0.39 s | 1 s |
| One what-if (200 rollouts) | 39 ms | 200 ms |
| Recommendation search | 0.43 s | 1 s |
| Baseline XGBoost training | 6.39 s | 10 s |
| Full benchmark | 49.95 s | 60 s |

### Step 2: containers

- **`backend/Dockerfile`:**
  - Base and user: Python 3.11 slim, non-root user (uid 10001).
  - Startup: health check through `scripts/healthcheck.py`; uvicorn listens on `$PORT`.
  - XGBoost is installed as `xgboost-cpu`: the same package without the GPU libraries.
  - `scripts/precompute.py` trains the models during the build and checks that the committed result
    files exist (it computes any missing one), so a container starts with everything ready.
  - Math libraries run one thread each (see "Findings").
- **`frontend/Dockerfile`:** Node 22, multi-stage, Next.js standalone output, enabled only in the image
  through `NEXT_OUTPUT=standalone`. The backend URLs are build arguments.
- **`docker-compose.yml`:** `docker compose up --build` starts both. The frontend waits for the backend's
  health check.
- **Environment examples:** `.env.example` (compose), `backend/.env.example` (every backend variable),
  and `frontend/.env.example`. `.gitignore` now keeps the examples.

### Step 3: deployment files (not deployed)

- **`render.yaml`:** Blueprint for the backend.
  - Docker runtime, health check `/health`, plan `free`.
  - Environment: discovery jobs 1, benchmark runs disabled, CORS values asked for on the first deploy.
- **`docs/DEPLOYMENT.md`:** step-by-step guide for Render and Vercel.
  - Choosing a plan.
  - Setting the CORS origins and the `wss://` URL.
  - Post-deploy checklist.
  - Waking the free service before judging.
  - Troubleshooting and the local backup.
- **Render facts checked on 2026-11-15** against render.com/docs/free, render.com/pricing and the
  Blueprint spec:
  - The free plan has 0.1 CPU and 512 MB, sleeps after 15 idle minutes and takes about a minute to wake.
  - 750 instance-hours per month; the disk is ephemeral.
  - Plan IDs were renamed (`starter` is now `0.5c-512mb`); legacy names still work.

### Steps 4 to 6: documents

- **`README.md`** covers the brief's 12 points:
  - The problem, the six required outputs mapped to the app and code, the signature features.
  - A Mermaid architecture diagram and the method step by step.
  - Results: generated tables.
  - Limitations, real-world use, the scalability design, setup and citations.
- **`backend/scripts/update_readme_results.py`** refreshes the generated blocks in README.md,
  SLIDES_OUTLINE.md, JUDGE_QA.md, FINAL_AUDIT.md and DEMO_SCRIPT.md; `--check` verifies them.
- **`docs/DEMO_SCRIPT.md`:**
  - A 3-minute script, beat by beat, with clicks and lines.
  - A 30-second version and a backup plan.
  - A checklist for recording the video.
- **`docs/SLIDES_OUTLINE.md`:** 7 slides, with numbers from the generated blocks.
- **`docs/JUDGE_QA.md`:** 11 prepared questions with honest answers.
- **`docs/FINAL_AUDIT.md`:** for each judging criterion, the evidence and the gaps; the six required
  outputs checked; gaps fixed in this phase and gaps left.

## How to run

```bash
docker compose up --build                       # whole system; http://localhost:3000
cd backend && python -m pytest -q               # backend tests
cd backend && python scripts/measure_timings.py # timings (stop servers first), then:
cd backend && python scripts/update_readme_results.py
cd frontend && npm run test:e2e                 # end-to-end, against running servers
cd frontend && DEMO_RUNS=5 npx playwright test tests/story.spec.ts
```

## Test results

| Check | Result |
|---|---|
| Backend `pytest -q` | **81 passed** in 35 s (66 earlier phases + 3 Phase 4 + 12 Phase 5) |
| `ruff check`, `ruff format --check`, `mypy causaltwin tests` | Clean (54 files) |
| Frontend `npm run lint`, `npx tsc --noEmit`, `npm run build` | Pass; all 8 pages (plus not-found) prerender as static shells |
| Playwright, local dev servers | All 13 passed (the resilience file was re-run after fixing its own text check; see Findings) |
| Playwright, Docker stack (`docker compose up`) | **13 passed** (1.0 min) |
| Guided demo 5 times in a row from the UI (Docker stack) | Passed; narration identical across all runs; 5.7-6.4 s of server time per full demo |
| `update_readme_results.py --check` | All generated blocks up to date |

## Findings and fixes (what broke and why)

- **Chaos testing:**
  - A `NaN` in a request body produced a 500, because the validation handler echoed the invalid value
    back and JSON cannot encode `NaN`. It now returns only each error's location and message: a 422.
  - The concurrency chaos test and the seeded API sequence now pass with the lock. Without the lock,
    nothing stopped a reset from replacing the simulator in the middle of a step.
- **Containers at small CPU limits:**
  - The problem: in a local stand-in for Render's free plan (`docker run --cpus 0.1 --memory 512m`), the
    demo's drift beat had not finished after more than 6 minutes. The container sees 16 CPUs, so OpenBLAS
    started 16 threads in one process (35 threads in total). Idle math threads spin and use up the tiny
    CPU quota.
  - The fix: one thread per math library in the image. Measured afterwards with the same limits:
    - start-up: healthy after 33 s;
    - memory: 204 MB of 512 MB;
    - the whole 8-beat demo: 33 s, with the drift beat at 20 s;
    - requests: recommendation 1.1-1.2 s, what-if 40 ms;
    - live loop: about 3 steps per second (target 5).
  - These are local Docker measurements; Render's real behaviour may differ.
- **Image sizes:** backend 1.1 GB (NumPy, SciPy, scikit-learn, numba and llvmlite, XGBoost, SHAP,
  statsmodels); frontend 314 MB. On a 16-CPU laptop the backend image builds in a few minutes and
  trains its models in 10-25 s.
- **A false alarm in our own test:** the UI chaos test's check for "System error" also matched the new
  Alerts subtitle "...and system errors". The check now matches the text exactly.

## Known limitations

- Not deployed: Render behaviour is documented from its docs and approximated locally, not observed.
- The free plan is slow: about 3 live steps per second and recommendations around 1 s in the local
  stand-in. For judging, `0.5c-512mb` or larger is recommended.
- One live simulation per server, shared by every viewer; a public audience can interfere (heavy-request
  limits keep it responsive, but one viewer's Reset affects everyone).
- Timeouts cannot stop a computation that is already running (Python threads cannot be killed); the
  client gets 504 and the work finishes in the background.
- The upload size check relies on `Content-Length`; a chunked upload without that header is still read
  up to the limit and then refused.
- The backend image is large (1.1 GB) because of the scientific Python stack.
- Earlier limitations still hold:
  - simulator only;
  - linear effects and no hidden confounders assumed;
  - two true links always missed;
  - the twin under-predicts risk in extreme states.

## Manual steps that remain (for the team)

1. **Push the repository** to GitHub (or another Git host). Nothing was pushed.
2. **Render:** create an account, deploy the Blueprint (`render.yaml`), choose a plan (`0.5c-512mb`
   recommended for judging), and set `CAUSALTWIN_CORS_ORIGINS` (docs/DEPLOYMENT.md, steps 1-2).
3. **Vercel:** create an account, import the repository with root directory `frontend`, and set
   `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_WS_URL` (`wss://.../ws/stream`) (step 3).
4. **CORS:** put the Vercel address into `CAUSALTWIN_CORS_ORIGINS` on Render (step 4), then work through
   the post-deploy checklist (step 5).
5. **README:** put the live URL where it says `[live demo link: ...]`.
6. **Video:** record the backup demo with the checklist in docs/DEMO_SCRIPT.md, then add the video link
   and, optionally, the GIF to the README.
7. **Slides:** build them from docs/SLIDES_OUTLINE.md.
8. **Statistics:** fill in or remove every `[source needed]` placeholder (README and slides). Use only
   statistics with a citable source.
9. **On judging day:** wake the backend 2-5 minutes before presenting (docs/DEPLOYMENT.md, step 6).
