/**
 * The full guided demo, driven from the UI alone: open the demo panel, start, and click
 * "Next beat" through all eight beats. Each beat must navigate to its page, show its
 * narration, and leave no page errors. Key beats are screenshotted into docs/screenshots/.
 *
 * Set DEMO_RUNS=5 to play the whole demo five times in a row (with the panel's Restart
 * button): every run must tell exactly the same story, beat by beat, because the demo uses a
 * fixed seed. Per-beat durations of every run are saved as demo-runs.json in the test output.
 *
 * The demo resets and pauses the live loop of this page's own simulation (every browser tab
 * has its own), so it does not disturb other tests or other viewers.
 */

import { writeFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

const SCREENSHOTS = "../docs/screenshots";
const RUNS = Math.max(1, Number(process.env.DEMO_RUNS ?? "1"));

interface RunRecord {
  narration: string[];
  seconds: number[];
}

/** Each beat: the page it navigates to, text the page must show, and an optional screenshot name. */
const BEATS: { title: RegExp; path: string; pageText: RegExp; screenshot?: string }[] = [
  { title: /Normal operation/i, path: "/control", pageText: /Twin risk estimate|Simulator ground truth/ },
  { title: /risk rises/i, path: "/control", pageText: /Simulator ground truth/, screenshot: "09-demo-risk-rises" },
  { title: /recommend/i, path: "/advisor", pageText: /Predicted risk after/, screenshot: "10-demo-recommendation" },
  { title: /appl/i, path: "/advisor", pageText: /Applied at step/, screenshot: "11-demo-applied" },
  { title: /Risk drops/i, path: "/control", pageText: /Risk over time/ },
  { title: /filter|fault/i, path: "/control", pageText: /Alerts/ },
  { title: /drift/i, path: "/graph", pageText: /Version 2/, screenshot: "12-demo-drift-rewire" },
  { title: /recommend/i, path: "/advisor", pageText: /Predicted risk after/, screenshot: "13-demo-new-recommendation" },
];

/** The floating demo panel. */
function demoPanel(page: Page) {
  return page.getByRole("complementary", { name: "Guided demo" });
}

test(`the full demo story plays from the UI alone (${RUNS} run${RUNS > 1 ? "s" : ""})`, async ({ page }, testInfo) => {
  test.setTimeout(60_000 + RUNS * 300_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const runs: RunRecord[] = [];

  try {
    await page.goto("/control");
    await expect(page.getByRole("status").filter({ hasText: "Live" })).toBeVisible();
    await page.getByRole("button", { name: "Guided demo" }).click();
    const panel = demoPanel(page);
    await expect(panel).toBeVisible();
    const next = panel.getByRole("button", { name: /Next beat/ });
    const story = panel.locator('[aria-live="polite"]');

    for (let run = 0; run < RUNS; run += 1) {
      const record: RunRecord = { narration: [], seconds: [] };
      for (const [index, beat] of BEATS.entries()) {
        const isLast = index === BEATS.length - 1;
        const started = Date.now();
        if (index === 0) await panel.getByRole("button", { name: run === 0 ? "Start" : "Restart" }).click();
        else await next.click();
        // Beats run simulation steps on the server; some (risk rising, drift) take a while.
        await expect(panel.getByText(`Beat ${index + 1} of ${BEATS.length}`)).toBeVisible({ timeout: 150_000 });
        await expect(next).toHaveText(/Next beat/, { timeout: 150_000 }); // not "Running..."
        record.seconds.push((Date.now() - started) / 1000);
        // After the last beat the demo is finished, so "Next beat" is disabled.
        if (isLast) await expect(next).toBeDisabled();
        else await expect(next).toBeEnabled();
        await expect(panel.locator("p.font-heading")).toHaveText(beat.title);
        await expect(page).toHaveURL(new RegExp(`${beat.path}$`));
        await expect(page.getByText(beat.pageText).first()).toBeVisible({ timeout: 45_000 });
        record.narration.push(await story.innerText());
        if (beat.screenshot && run === 0) {
          await page.waitForTimeout(1500); // let the spotlight and charts settle
          await page.screenshot({ path: `${SCREENSHOTS}/${beat.screenshot}.png` });
        }
      }
      await expect(panel.getByText(/Twin risk/)).toBeVisible();
      runs.push(record);
    }
    // A fixed seed means every run must produce the same narration, numbers included.
    for (const record of runs.slice(1)) expect(record.narration).toEqual(runs[0].narration);
    expect(errors).toEqual([]);
  } finally {
    writeFileSync(testInfo.outputPath("demo-runs.json"), JSON.stringify(runs, null, 2));
  }
});
