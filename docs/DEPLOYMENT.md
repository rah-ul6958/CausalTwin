# Deploying CausalTwin

The backend (FastAPI with WebSockets) runs on **Render** from `backend/Dockerfile`; the frontend
(Next.js) runs on **Vercel**. Account sign-ups and dashboard clicks cannot be automated, so this
guide lists every step. Nothing in this repository deploys anything by itself.

Render facts below were checked against Render's documentation on 2026-11-15
(https://render.com/docs/free, https://render.com/pricing, https://render.com/docs/blueprint-spec).
Check them again before relying on them; hosting plans change.

## 0. Before you start

- The repository must be on GitHub (or GitLab/Bitbucket) so Render and Vercel can read it.
- Run the checks locally first (see the README): backend tests, frontend build, `docker compose up --build`.
- No secrets are needed: the app has no database, no API keys and no logins.

## 1. Choose the Render plan

| Plan (Blueprint `plan`) | CPU / RAM | Price | What to expect |
|---|---|---|---|
| `free` | 0.1 CPU / 512 MB | $0 | Sleeps after 15 minutes without inbound traffic; waking takes about a minute. 750 free instance hours per workspace per month. Ephemeral disk. |
| `0.5c-512mb` (legacy name `starter`) | 0.5 CPU / 512 MB | $7/month | Always on. Recommended for judging. |
| `1c-2g` (legacy name `standard`) | 1 CPU / 2 GB | $25/month | Closest to laptop speed. |

**Local stand-in for the free plan** (`docker run --cpus 0.1 --memory 512m`, Docker Desktop on the
development laptop; Render's real behaviour may differ):

- **Start-up:** the container reports healthy about 30 s after starting, with the models baked into the image.
- **Memory:** about 200 MB of the 512 MB limit, with `CAUSALTWIN_DISCOVERY_JOBS=1`.
- **Guided demo:** all 8 beats took 31 s in total; the drift beat alone took 20 s.
- **Requests:** recommendation search 1.1-1.2 s; one what-if 40 ms.
- **Live loop:** about 3 steps per second instead of 5.

The same stand-in with two judges at once (`CAUSALTWIN_MAX_SESSIONS=2`, each with a private simulation;
measured after per-viewer simulations were added, before the live-loop pacing fix, so the live pace
may now be slightly higher):

- **Both watching:** each simulation ran at about 1.9 steps per second; a recommendation took about 8 s
  (median) instead of 1.1 s, and a what-if about 1.2 s.
- **One judge plays the guided demo while the other watches:** the demo took 75 s in total instead of
  31 s (the drift beat 56 s), and the watching judge's simulation ran at about 1.2 steps per second.
- **Memory:** at most 266 MB of the 512 MB limit.

So the free plan works for two judges at a time, slowly; a paid plan is recommended for judging.

The image sets the numeric libraries to one thread each. Without this, idle math threads ate the
small CPU quota and the drift beat took more than 6 minutes.

`render.yaml` uses `plan: free`. To use a paid plan, change that line before step 2, or change the
instance type in the Render dashboard afterwards.

The free plan is too slow for live judging (0.1 CPU, and it sleeps after 15 idle minutes). Section 9
gives two ways to run the judged demo: a larger Render instance for the judging window only, or the
laptop exposed through a Cloudflare quick tunnel. The choice is yours; `render.yaml` stays on `free`.

## 2. Backend on Render (Blueprint)

1. Sign in at https://dashboard.render.com and connect your Git provider if asked.
2. Choose **New > Blueprint**, select this repository and the branch to deploy.
3. Render reads `render.yaml` from the repository root and shows one web service,
   `causaltwin-backend` (Docker runtime, build context `backend/`, health check `/health`).
4. Render asks for the values marked `sync: false`:
   - `CAUSALTWIN_CORS_ORIGINS`: enter `http://localhost:3000` for now. You will replace it with the
     Vercel address in step 4.
   - `CAUSALTWIN_CORS_ORIGIN_REGEX`: leave empty, or see step 4 for Vercel preview deployments.
5. Click **Apply** (or **Deploy Blueprint**). The first build installs the dependencies and trains the
   models inside the image; expect several minutes.
6. When the service shows **Live**, copy its address, for example
   `https://causaltwin-backend.onrender.com` (yours may have a suffix). This is `BACKEND_URL` below.
7. Check it: open `BACKEND_URL/health` in a browser. Expected: `{"status":"ok","engine_fitted":true,...}`.

Already set by `render.yaml`:

- **`CAUSALTWIN_DISCOVERY_JOBS=1`:** saves memory.
- **`CAUSALTWIN_ALLOW_BENCHMARK_RUN=0`:** visitors cannot start the one-minute benchmark. Cached results still show.
- **`CAUSALTWIN_MAX_SESSIONS=2`:** each browser tab gets its own private live simulation, so one judge's
  Reset, fault or guided demo does not change what another judge sees. At most two exist at once on the
  free plan, because every watched simulation needs CPU; use 3 or 4 on a paid plan. Later viewers share
  one simulation, and the top bar says **Shared** instead of **Private**. A private simulation steps only
  while its tab is open; when all slots are taken, a new viewer takes over one nobody has watched for
  30 seconds, and the server deletes unwatched ones after 10 minutes.
- **`CAUSALTWIN_AUTOSTART=1`:** the shared simulation (health checks, scripts, viewers beyond the limit)
  is running from start-up. Like the private ones, it only steps while someone watches it, so an unused
  simulation costs no CPU.
- **`PORT`:** Render sets it itself; the container listens on it.

All backend variables are documented in `backend/.env.example`, including `CAUSALTWIN_DATA_SOURCE`
for connecting a real machine over MTConnect instead of the simulator (README, "Real-world use"). Leave
it unset for the simulated demo.

## 3. Frontend on Vercel

1. Sign in at https://vercel.com and choose **Add New... > Project**.
2. Import this repository.
3. In **Configure Project**:
   - **Root Directory:** `frontend` (click Edit and select the folder).
   - **Framework Preset:** Next.js (detected automatically).
   - Leave the build and output settings at their defaults.
4. Under **Environment Variables**, add:
   - `NEXT_PUBLIC_API_URL` = `BACKEND_URL` (for example `https://causaltwin-backend.onrender.com`, no
     trailing slash)
   - `NEXT_PUBLIC_WS_URL` = the same address with `wss://` and `/ws/stream`, for example
     `wss://causaltwin-backend.onrender.com/ws/stream`
5. Click **Deploy**. When it finishes, copy the production address, for example
   `https://causaltwin.vercel.app`. This is `FRONTEND_URL` below.

`NEXT_PUBLIC_` variables are compiled into the browser code at build time. After changing them,
redeploy: **Deployments > ... > Redeploy**.

## 4. Allow the frontend in the backend's CORS settings

1. In Render, open `causaltwin-backend` and go to **Environment**.
2. Set `CAUSALTWIN_CORS_ORIGINS` to `FRONTEND_URL` exactly, with `https://` and no trailing slash.
   Several addresses can be separated by commas.
3. Optional: to also allow Vercel preview deployments, set `CAUSALTWIN_CORS_ORIGIN_REGEX` to a pattern
   such as `https://causaltwin-.*\.vercel\.app`. Use your project's prefix. A pattern that matches
   every `*.vercel.app` address would let any Vercel site call the API.
4. Save. Render restarts the service with the new settings.

## 5. Post-deploy checklist

Open `FRONTEND_URL` in a fresh browser window and tick each item:

- [ ] `BACKEND_URL/health` answers `"status":"ok"` and `"engine_fitted":true`.
- [ ] The top bar shows the green **Live** indicator, and the step counter increases. This means the
      WebSocket connected over `wss://`. In the browser's developer tools (Network, filter "WS"),
      `/ws/stream?session=...` shows status 101.
- [ ] The top bar shows **Private**. Open the app in a second, private browser window: it also says
      **Private**, and a fault injected in one window does not appear in the other.
- [ ] The **Simulated** badge is visible on every page.
- [ ] The Control Room charts move, and the risk gauge shows the twin estimate and the ground truth.
- [ ] **Guided demo** runs end to end: open it, Start, then Next through all 8 beats. Each beat opens
      its page; beat 7 rewires the graph on the Graph Explorer (Version 2 in the history); beat 8
      shows a new recommendation.
- [ ] The **Benchmark** page loads the cached results. **Re-run benchmark** explains that re-runs are
      disabled on this server.
- [ ] The What-If Lab answers when a slider moves, and the Advisor shows a recommendation next to the
      predictive baselines (B3 selected; B2 and B1 one click away).
- [ ] The System Console shows log entries and pipeline timings.
- [ ] Data page: **Export CSV** downloads a file, and a small CSV upload returns a graph with the
      "no ground truth" banner.
- [ ] The browser console shows no CORS errors.

## 6. Before judging: wake the backend

On the free plan the backend sleeps after 15 minutes without inbound traffic, and the first request
then waits about a minute while it starts.

1. Two to five minutes before you present, open `BACKEND_URL/health` and wait for `"status":"ok"`.
2. Then open `FRONTEND_URL` and leave the tab open; the live connection keeps the service busy.
3. If the top bar says **Offline - retrying**, wait; it reconnects on its own when the backend is up.
4. If the free hours for the month are used up, the service is suspended until the next month.
   Use the local backup (section 8) or a paid plan.

A restart also resets every live simulation and demo, because the filesystem and memory are not
kept between restarts. Each open tab then gets a fresh simulation and redraws its charts.

## 7. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Panels say "Cannot reach the CausalTwin backend" and the browser console shows a CORS error | `CAUSALTWIN_CORS_ORIGINS` does not exactly match `FRONTEND_URL` (scheme, no trailing slash). Fix it in Render (step 4). |
| Top bar stays "Offline - retrying" but `/health` works | `NEXT_PUBLIC_WS_URL` is wrong (needs `wss://` and `/ws/stream`), or the frontend was not redeployed after changing it. |
| "The backend did not answer within 35 s" | The backend is waking up or overloaded. Wait and press Retry. |
| "The server is busy with other computations" (503) | More than two heavy requests at once. Retry after a few seconds. |
| A request answers 504 after 30 s | The server is too slow for the plan. A demo beat may be holding the simulation; wait for it, or use a larger plan. |
| The service restarts repeatedly with out-of-memory errors | Keep `CAUSALTWIN_DISCOVERY_JOBS=1`, or use a plan with more memory. |
| The top bar says **Shared** | All private simulations are in use by open tabs. Close unused tabs (a slot frees 30 s after a tab closes), reload, or raise `CAUSALTWIN_MAX_SESSIONS` on a larger plan. The tooltip explains the reason. |
| Every viewer's simulation is slow | Several tabs are open, and each watched simulation needs CPU. Close unused tabs, lower `CAUSALTWIN_MAX_SESSIONS`, or use a larger plan. |
| The build fails on Render | Check the build log. The image needs no secrets; a clean `docker build backend` locally must succeed first. |

## 8. Backup: run everything locally

If the network or the hosting fails during judging:

```bash
docker compose up --build      # first time; afterwards: docker compose up
```

Open http://localhost:3000. The backend is on http://localhost:8000. Settings are in `.env.example`.

## 9. The judged demo: two options

Pick one before the judging day and rehearse it once. Both keep `render.yaml` unchanged.

### Option A: a 1 CPU / 2 GB Render instance during the judging window

The Render instance type with 1 CPU and 2 GB (Blueprint name `1c-2g`, legacy name `standard`; see the
table in section 1 and Render's pricing page for the current price) is close to laptop speed. Use it
only for the judging window and switch back afterwards.

1. Deploy as in sections 2 to 5 first (the free plan is fine for setting up).
2. On the judging day, at least 30 minutes before you present, open https://dashboard.render.com,
   select `causaltwin-backend`, then **Settings > Instance Type**, choose the instance with 1 CPU and
   2 GB, and confirm. Render redeploys the service on the new instance; wait until it shows **Live**.
3. Open **Environment**, set `CAUSALTWIN_MAX_SESSIONS` to `3` and save. Render restarts the service.
   (Three private simulations at once: one per judge's tab plus one spare. Viewers beyond that share
   one simulation, as described in section 2.)
4. Open `BACKEND_URL/health` and wait for `"status":"ok"`, then run the post-deploy checklist in
   section 5 once, including the guided demo.
5. Do not push to the deployed branch during the judging window. With Blueprint auto-sync on, a sync
   can apply the `plan: free` and `CAUSALTWIN_MAX_SESSIONS: "2"` values from `render.yaml` again. If
   that happens, repeat steps 2 and 3.
6. After judging, set **Instance Type** back to **Free** and `CAUSALTWIN_MAX_SESSIONS` back to `2`, so
   the paid instance stops costing money. Check Render's pricing page for how partial months are billed.

The frontend on Vercel needs no change for this option.

### Option B: the laptop, exposed with Cloudflare quick tunnels

Everything runs on the laptop with `docker compose`; two free Cloudflare quick tunnels give it public
`https://...trycloudflare.com` addresses, one for the frontend and one for the API. No Cloudflare
account is needed. Cloudflare describes quick tunnels as meant for testing, with no uptime guarantee
and a limit on concurrent requests; read
https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/
before relying on one. The laptop must stay awake, plugged in and online for the whole session.

1. Install `cloudflared`:
   - Windows: `winget install --id Cloudflare.cloudflared`
   - macOS: `brew install cloudflared`
   - Linux: the package for your distribution from https://pkg.cloudflare.com/ (see Cloudflare's
     download page).
2. Build the images once while you still have a fast connection: `docker compose build`.
3. Open a terminal and start the API tunnel. Leave this window open for the whole session:

   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```

   It prints an address such as `https://api-words-1234.trycloudflare.com`. This is `API_TUNNEL`.
4. Open a second terminal and start the frontend tunnel. Leave it open too:

   ```bash
   cloudflared tunnel --url http://localhost:3000
   ```

   Its address is `FRONTEND_TUNNEL`.
5. In the repository root, copy `.env.example` to `.env` and set (no trailing slashes):

   ```bash
   NEXT_PUBLIC_API_URL=https://API_TUNNEL
   NEXT_PUBLIC_WS_URL=wss://API_TUNNEL/ws/stream
   CAUSALTWIN_CORS_ORIGINS=https://FRONTEND_TUNNEL,http://localhost:3000
   CAUSALTWIN_MAX_SESSIONS=4
   CAUSALTWIN_ALLOW_BENCHMARK_RUN=0
   ```

   Replace `API_TUNNEL` and `FRONTEND_TUNNEL` with the host names from steps 3 and 4.
   `CAUSALTWIN_ALLOW_BENCHMARK_RUN=0` stops visitors from starting the one-minute benchmark re-run.
6. Rebuild the frontend with these addresses and start everything (the `NEXT_PUBLIC_` values are
   compiled into the frontend, so the rebuild is required):

   ```bash
   docker compose up --build
   ```

7. Check from a phone on mobile data (not the laptop's network): open `https://API_TUNNEL/health`, then
   `https://FRONTEND_TUNNEL`. The top bar must show **Live** and **Private**. Run the post-deploy
   checklist in section 5.
8. Give the judges `https://FRONTEND_TUNNEL`.
9. If either `cloudflared` window is closed or restarts, its address changes. Start it again, update
   `.env` (step 5) and run `docker compose up --build` again.
10. To stop: `Ctrl+C` in the compose window and in both tunnel windows. The public addresses stop
    working immediately.

The app has no logins and no secrets, so a public address exposes only the demo itself; anyone with
the address can use it while the tunnels run.
