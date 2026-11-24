# Demo script

A 3-minute spoken demo that follows the app's **Guided demo** mode, a 30-second version, a backup
plan, and a checklist for recording the backup video.

The guided demo uses a fixed random seed, so it tells the same story every time (checked: five runs in
a row from the UI gave identical narration, numbers included). Numbers that change with the machine's
state, such as the current risk, are read from the screen. Benchmark numbers come from the block below,
which is generated from the result files.

## Numbers to quote (generated from the result files)

<!-- results:quote:start -->
> "We tested this on 30 high-risk moments in the simulator. CausalTwin's fix brought the true risk under the target in 28 of 30. The most thorough predictive baseline we built, XGBoost scoring the very same candidate actions, managed 15 of 30: in 8 of the 30 it said no action was needed while the true risk was 16 to 25 percent, and in 3 its change made the machine riskier than doing nothing. It cannot know that changing a lever also changes the temperatures it reads."
>
> "The discovered graph found 15 of the 15 true cause-effect links with 0 false ones, and the twin detected 9 of 9 injected machine faults within 20 to 40 seconds of machine time."
<!-- results:quote:end -->

## Before you start (2 minutes before)

1. Wake the backend: open `BACKEND_URL/health` and wait for `"status":"ok"` (see docs/DEPLOYMENT.md).
   If you run locally, start `docker compose up` instead.
2. Open the app, check that the top bar shows the green **Live** indicator.
3. Browser full screen (F11), zoom 100% (110% on a large projector), notifications off.
4. Open the **Control Room**. Keep the Guided demo panel closed until you start.

## The 3-minute script

The guided demo panel sits at the bottom right. Every **Next beat** click runs real simulation steps
on the server, opens the right page and highlights the part to look at. Beats normally take a second
or two. On a small free server, beat 7 can take about 20 seconds; the line for it is written to fill
that time.

| Time | Click | Say |
|---|---|---|
| 0:00 | (Control Room open) | "Most predictive maintenance tells you a failure is likely. It does not tell you why, or what to change. CausalTwin does both, and it checks itself. Everything here is a simulated CNC spindle; we built the simulator, which is why we can prove our answers." |
| 0:15 | Top bar: **Guided demo**, then **Start** | **Beat 1, normal operation.** "This gauge is the twin's estimate of the failure risk in the next five minutes, learned from sensor data. The green box is simulator ground truth: the twin never sees it; it is only there so you can check us." |
| 0:35 | **Next beat** | **Beat 2, risk rises.** "A hot afternoon and an aggressive job: higher load and speed, less coolant. The bearing heats up and the risk climbs to (read the gauge)." |
| 0:55 | **Next beat** (opens the Advisor) | **Beat 3, recommendation.** "CausalTwin simulated hundreds of possible changes and recommends the cheapest one that brings the predicted risk under ten percent (read the change from the card: slow the spindle, add lubrication). It shows the causal path, the cost, the side effects and how sure it is. Next to it is the strongest predictive baseline we built: the very same search over possible changes, scored by an XGBoost failure predictor. Here it says no action is needed (read its predicted risk from the card: under one percent). The ground-truth value in the demo panel says the real risk is far higher (read it). That is false reassurance, and the benchmark counts how often it happens. (Optional: click B1 to show the classic SHAP answer, blame bearing temperature and add coolant.)" |
| 1:25 | **Next beat** | **Beat 4, apply.** "We apply it to the running machine. The verification panel compares what we predicted with what the machine actually does." |
| 1:40 | **Next beat** (Control Room) | **Beat 5, risk drops.** "Five minutes after the change, the risk is where CausalTwin said it would be (read both numbers from the panel), and the ground truth agrees. It is a five-minute promise: as the tool keeps wearing, risk creeps up again, and the live alert would catch that." |
| 1:50 | **Next beat** | **Beat 6, a hidden fault.** "Now something physical changes that nobody tells the twin about: the coolant filter clogs. The twin notices that its own predictions for coolant temperature stopped matching reality: that is a drift alarm." |
| 2:10 | **Next beat** (Graph Explorer) | **Beat 7, drift.** "It collects fresh data, re-runs causal discovery and compares the old and new graphs. The link from coolant flow to coolant temperature has almost vanished, so the graph rewires itself, here, live, and the affected equations are refitted. Advice based on the old graph is marked outdated." (While it runs: "This step re-discovers the whole structure from data; on our small cloud server it takes a few seconds.") |
| 2:35 | **Next beat** (Advisor) | **Beat 8, new advice.** "Same aggressive job, changed machine. The new recommendation no longer relies on coolant, because coolant no longer works. It uses spindle speed and lubrication instead." |
| 2:45 | (Close the panel; optional: open **Benchmark**) | Say the two quotes from "Numbers to quote" above. "Every number you saw is computed by code, and the benchmark lists the cases we lose too." |
| 3:00 | | End. |

If you have extra time: on the **Graph Explorer**, click **Correlation**, then **Causal**. "With
correlation, vibration looks like the strongest warning sign. With causation, it has no arrow into
failure. It is a symptom." That is the Correlation Trap in one click.

## The 30-second version

1. (Graph Explorer, Correlation view) "Standard AI sees that vibration goes with failure."
2. Click **Causal**. "CausalTwin finds that vibration is only a symptom; heat and tool wear cause failures."
3. Open **Advisor**. "So it recommends the cheapest change that really lowers the risk, and shows its evidence."
4. Open **Benchmark** and say the first quote from "Numbers to quote".

## Backup plan

| Problem | What to do |
|---|---|
| The cloud backend is asleep or slow | Open `BACKEND_URL/health` and wait about a minute; keep talking through the Benchmark page, which needs no live data. |
| No network, or the hosting is down | Run locally: `docker compose up` (build the images beforehand with `docker compose up --build`, so no download is needed), then open http://localhost:3000. Test this the day before. |
| The laptop cannot run Docker | `cd backend && uvicorn causaltwin.api.app:app --port 8000` and `cd frontend && npm run start` (after `npm run build`). |
| Nothing runs | Play the recorded backup video, then walk through the screenshots in `docs/screenshots/`. |
| A beat shows an error | Press **Next beat** again; a failed beat is retried, not skipped. **Restart** replays the story from the start. |

## Recording the backup video

- [ ] Run the stack locally (`docker compose up`) so the network cannot slow the recording.
- [ ] Screen at 1920 x 1080, browser zoom 100%, full screen (F11); bookmarks bar, extensions and other
      tabs hidden; notifications and chat apps off.
- [ ] Warm up: open every page once, then run the guided demo once all the way through.
- [ ] Before recording, press **Reset** in the Control Room's simulation controls, wait 20 seconds for
      the charts to fill, and close the demo panel.
- [ ] Recorder (for example OBS Studio): 1920 x 1080, 30 fps, MP4 (H.264), microphone level checked
      with a 10-second test clip.
- [ ] Record the 3-minute script above, speaking over the clicks. Keep it under 3 minutes.
- [ ] Watch the result once with sound. Check that the risk numbers and the graph rewiring are readable.
- [ ] Upload (unlisted), then put the link in README.md where it says `[video link: ...]`.
- [ ] Optional: export a 10-second GIF of beats 6 and 7 (the graph rewiring) for the README's GIF
      placeholder.
