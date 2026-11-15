/**
 * Per-viewer simulations: two browser tabs (separate browser contexts, like two judges on
 * two laptops) each get a private live simulation. A fault injected or a pause in one tab
 * does not show up in the other.
 */

import { expect, test, type Page } from "@playwright/test";

import { pageSessionId, postForPage } from "./session-helpers";

/** Simulation step shown in the top bar. */
async function currentStep(page: Page): Promise<number> {
  const text = await page.getByTestId("sim-step").innerText();
  return Number(text.replace(/,/g, ""));
}

test("two viewers have independent live simulations", async ({ browser, request }) => {
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  try {
    const viewerA = await contextA.newPage();
    const viewerB = await contextB.newPage();
    await viewerA.goto("/control");
    await viewerB.goto("/control");
    for (const viewer of [viewerA, viewerB]) {
      await expect(viewer.getByTestId("session-badge")).toHaveText(/Private/);
      await expect(viewer.getByRole("status").filter({ hasText: /^Live$/ })).toBeVisible();
    }
    expect(await pageSessionId(viewerA)).not.toEqual(await pageSessionId(viewerB));

    // A fault in A's simulation appears in A's top bar only.
    const injected = await postForPage(viewerA, request, "/inject-fault", { fault: "lube_leak" });
    expect(injected.ok()).toBeTruthy();
    await expect(viewerA.getByText(/Fault: Lube leak/i)).toBeVisible();
    await expect(viewerB.getByText(/Fault:/)).toHaveCount(0);

    // Pausing A (from its own top bar) leaves B running.
    await viewerA.getByRole("button", { name: "Pause the live simulation" }).click();
    await expect(viewerA.getByRole("button", { name: "Resume the live simulation" })).toBeVisible();
    // A step that was already running when Pause arrived may still show up; wait until A stands still.
    let pausedStep = await currentStep(viewerA);
    await expect
      .poll(async () => {
        const previous = pausedStep;
        await viewerA.waitForTimeout(1000);
        pausedStep = await currentStep(viewerA);
        return pausedStep - previous;
      })
      .toBe(0);
    const stepB = await currentStep(viewerB);
    await expect.poll(() => currentStep(viewerB), { timeout: 15_000 }).toBeGreaterThan(stepB);
    expect(await currentStep(viewerA)).toBe(pausedStep);
    await expect(viewerB.getByRole("button", { name: "Pause the live simulation" })).toBeVisible();

    // The CSV download (a plain link, so no header) carries the tab's session id too.
    await viewerA.goto("/data");
    const exportLink = viewerA.getByRole("link", { name: /Export CSV/ });
    await expect(exportLink).toHaveAttribute("href", new RegExp(`session=${await pageSessionId(viewerA)}$`));
  } finally {
    await contextA.close();
    await contextB.close();
  }
});
