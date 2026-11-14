/**
 * The full guided demo, driven from the UI alone: open the demo panel, start, and click
 * "Next beat" through all eight beats. Each beat must navigate to its page, show its
 * narration, and leave no page errors. Key beats are screenshotted into docs/screenshots/.
 *
 * The demo resets and pauses the live loop. With one worker Playwright runs files in
 * alphabetical order, so this file ("story") runs after "smoke"; it also resets and
 * resumes the live loop at the end, leaving the server as it found it.
 */

import { expect, test, type Page } from "@playwright/test";

const SCREENSHOTS = "../docs/screenshots";
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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

test("the full demo story plays from the UI alone", async ({ page, request }) => {
  test.setTimeout(420_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));

  try {
    await page.goto("/control");
    await expect(page.getByRole("status").filter({ hasText: "Live" })).toBeVisible();
    await page.getByRole("button", { name: "Guided demo" }).click();
    const panel = demoPanel(page);
    await expect(panel).toBeVisible();
    await panel.getByRole("button", { name: /Start/ }).click();
    const next = panel.getByRole("button", { name: /Next beat/ });

    for (const [index, beat] of BEATS.entries()) {
      const isLast = index === BEATS.length - 1;
      if (index > 0) await next.click();
      // Beats run simulation steps on the server; some (risk rising, drift) take a while.
      await expect(panel.getByText(`Beat ${index + 1} of ${BEATS.length}`)).toBeVisible({ timeout: 150_000 });
      await expect(next).toHaveText(/Next beat/, { timeout: 150_000 }); // not "Running..."
      // After the last beat the demo is finished, so "Next beat" is disabled.
      if (isLast) await expect(next).toBeDisabled();
      else await expect(next).toBeEnabled();
      await expect(panel.locator("p.font-heading")).toHaveText(beat.title);
      await expect(page).toHaveURL(new RegExp(`${beat.path}$`));
      await expect(page.getByText(beat.pageText).first()).toBeVisible({ timeout: 45_000 });
      if (beat.screenshot) {
        await page.waitForTimeout(1500); // let the spotlight and charts settle
        await page.screenshot({ path: `${SCREENSHOTS}/${beat.screenshot}.png` });
      }
    }
    await expect(panel.getByText(/Twin risk/)).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    // Leave the server as the other tests expect it: a fresh, running live loop.
    await request.post(`${API}/sim/control`, { data: { action: "reset" } });
    await request.post(`${API}/sim/control`, { data: { action: "resume" } });
  }
});
