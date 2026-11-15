/**
 * Resilience: the UI never goes blank when the backend is unreachable, the live stream
 * reconnects after a dropped WebSocket, and random clicking in the Control Room (faults,
 * scenarios, steps, resets) leaves the app live and error-free.
 */

import { expect, test, type Page } from "@playwright/test";

const PAGES = ["/", "/control", "/graph", "/whatif", "/advisor", "/benchmark", "/console", "/data"];
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const CONNECTION = /^(Live|Connecting|Offline - retrying)$/;

/** The connection indicator in the top bar. */
function connectionStatus(page: Page) {
  return page.getByRole("status").filter({ hasText: CONNECTION });
}

/** Simulation step shown in the top bar. */
async function currentStep(page: Page): Promise<number> {
  const text = await page.getByTestId("sim-step").innerText();
  return Number(text.replace(/,/g, ""));
}

test("every page explains a backend outage instead of going blank", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  // Every REST call fails at the network level, and the live stream closes at once.
  await page.route(`${API}/**`, (route) => route.abort("connectionrefused"));
  await page.routeWebSocket(/\/ws\/stream/, (socket) => socket.close());
  for (const path of PAGES) {
    await page.goto(path);
    await expect(page.locator("main")).not.toBeEmpty();
    if (path !== "/") {
      // At least one panel shows the outage with a Retry button.
      await expect(page.getByRole("alert").filter({ hasText: /Cannot reach the CausalTwin backend/ }).first()).toBeVisible({ timeout: 15_000 });
      await expect(page.getByRole("button", { name: "Retry" }).first()).toBeVisible();
    } else {
      await expect(page.getByText(/Benchmark results are not available yet/)).toBeVisible({ timeout: 15_000 });
    }
  }
  expect(errors).toEqual([]);
});

test("the live stream reconnects after the WebSocket drops", async ({ page }) => {
  let connections = 0;
  await page.routeWebSocket(/\/ws\/stream/, (socket) => {
    connections += 1;
    const server = socket.connectToServer();
    if (connections === 1) {
      setTimeout(() => {
        socket.close();
        server.close();
      }, 2500);
    }
  });
  await page.goto("/control");
  await expect(connectionStatus(page)).toHaveText("Live");
  await expect(connectionStatus(page)).toHaveText(/Offline - retrying|Connecting/, { timeout: 10_000 });
  await expect(connectionStatus(page)).toHaveText("Live", { timeout: 20_000 });
  expect(connections).toBeGreaterThanOrEqual(2);
  // Data flows again after the reconnect.
  const before = await currentStep(page);
  await expect.poll(() => currentStep(page), { timeout: 15_000 }).toBeGreaterThan(before);
});

test("random clicking in the Control Room keeps the app live", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await request.post(`${API}/sim/control`, { data: { action: "reset" } });
  await request.post(`${API}/sim/control`, { data: { action: "resume" } });
  await page.goto("/control");
  await expect(connectionStatus(page)).toHaveText("Live");
  await expect(page.getByRole("button", { name: "Step 50" })).toBeVisible();

  // A fixed pseudo-random sequence, so a failure can be reproduced.
  let seed = 7;
  const next = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  const actions: (() => Promise<void>)[] = [
    async () => {
      await page.getByRole("button", { name: /Inject fault/ }).click();
      const items = page.getByRole("menuitem");
      await items.nth(Math.floor(next() * (await items.count()))).click();
    },
    async () => {
      await page.getByRole("combobox", { name: "Scenario" }).click();
      const options = page.getByRole("option");
      await options.nth(Math.floor(next() * (await options.count()))).click();
    },
    async () => page.getByRole("button", { name: "Step 50" }).click(),
    async () => {
      const clear = page.getByRole("button", { name: /^Clear / });
      if (await clear.count()) await clear.first().click();
    },
    async () => {
      const release = page.getByRole("button", { name: /^Release / });
      if (await release.count()) await release.first().click();
    },
    async () => page.getByRole("button", { name: /^(Pause|Resume)$/ }).first().click(),
  ];
  for (let index = 0; index < 18; index += 1) {
    await actions[Math.floor(next() * actions.length)]();
    await page.waitForTimeout(250 + next() * 400);
  }

  // Back to a known state: the machine must still be steppable and no error alert raised.
  await request.post(`${API}/sim/control`, { data: { action: "resume" } });
  await expect(connectionStatus(page)).toHaveText("Live");
  const before = await currentStep(page);
  await expect.poll(() => currentStep(page), { timeout: 15_000 }).toBeGreaterThan(before);
  await expect(page.getByText("System error", { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
  await request.post(`${API}/sim/control`, { data: { action: "reset" } });
  await request.post(`${API}/sim/control`, { data: { action: "resume" } });
});
