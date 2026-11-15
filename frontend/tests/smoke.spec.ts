/**
 * Smoke test: every page loads, shows live data from the running backend, and is screenshotted
 * into docs/screenshots/. Uncaught page errors fail the test.
 */

import { expect, test, type Page } from "@playwright/test";

const SCREENSHOTS = "../docs/screenshots";

async function open(page: Page, path: string): Promise<string[]> {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(path);
  await expect(page.getByRole("status").filter({ hasText: "Live" })).toBeVisible();
  return errors;
}

async function capture(page: Page, name: string) {
  if (test.info().project.name !== "chromium") return; // documentation screenshots come from Chromium only
  await page.waitForTimeout(1200); // let entry animations settle
  await page.screenshot({ path: `${SCREENSHOTS}/${name}.png`, fullPage: true });
}

test("landing shows live benchmark numbers", async ({ page }) => {
  const errors = await open(page, "/");
  await expect(page.getByText(/high-risk cases brought to .* true risk or less by CausalTwin/)).toBeVisible();
  await expect(page.getByText(/blamed vibration/)).toBeVisible();
  await capture(page, "01-landing");
  expect(errors).toEqual([]);
});

test("control room streams live data", async ({ page }) => {
  const errors = await open(page, "/control");
  const risk = page.getByTestId("risk-panel");
  await expect(risk.getByText("Simulator ground truth")).toBeVisible();
  await expect(risk.locator(".tabular").first()).toContainText("%");
  await expect(page.getByText("Live sensors")).toBeVisible();
  await expect(page.locator(".recharts-line-curve").first()).toBeVisible();
  await capture(page, "02-control-room");
  expect(errors).toEqual([]);
});

test("graph explorer renders the causal graph and edge evidence", async ({ page }) => {
  const errors = await open(page, "/graph");
  const canvas = page.getByTestId("graph-canvas");
  await expect(canvas.locator(".react-flow__node")).toHaveCount(11);
  await page.getByRole("radio", { name: "Overlay" }).click();
  await capture(page, "03-graph-explorer");
  await page.getByRole("radio", { name: "Causal" }).click();
  await canvas.locator(".react-flow__edge").first().click({ force: true });
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(errors).toEqual([]);
});

test("what-if lab simulates a lever change", async ({ page }) => {
  const errors = await open(page, "/whatif");
  const slider = page.getByRole("slider", { name: /Coolant flow/ });
  await slider.focus();
  for (let index = 0; index < 25; index += 1) await page.keyboard.press("ArrowRight");
  await expect(page.getByText(/percentage points/).first()).toBeVisible();
  await capture(page, "04-whatif-lab");
  expect(errors).toEqual([]);
});

test("advisor shows a recommendation and the baseline", async ({ page }) => {
  const errors = await open(page, "/advisor");
  const card = page.getByTestId("recommendation-card");
  await expect(card.getByText("Predicted risk after")).toBeVisible();
  await expect(page.getByText("Standard ML baseline for the same moment")).toBeVisible();
  await expect(page.getByText(/Every evaluated option/)).toBeVisible();
  await capture(page, "05-advisor");
  expect(errors).toEqual([]);
});

test("benchmark shows results including losses", async ({ page }) => {
  const errors = await open(page, "/benchmark");
  await expect(page.getByRole("heading", { name: "Every case" })).toBeVisible();
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await expect(page.getByText("Is the baseline fair?")).toBeVisible();
  await capture(page, "06-benchmark");
  expect(errors).toEqual([]);
});

test("console shows the pipeline and log stream", async ({ page }) => {
  const errors = await open(page, "/console");
  await expect(page.getByText("Activity log")).toBeVisible();
  await expect(page.getByRole("log").locator("div").first()).toBeVisible();
  await expect(page.getByText("Model cards")).toBeVisible();
  await capture(page, "07-console");
  expect(errors).toEqual([]);
});

test("data page shows raw data and quality", async ({ page }) => {
  const errors = await open(page, "/data");
  await expect(page.getByRole("table", { name: /Recent simulated sensor rows/ })).toBeVisible();
  await expect(page.getByText("Data quality report")).toBeVisible();
  await capture(page, "08-data");
  expect(errors).toEqual([]);
});

test("every page fits a 1280 px laptop screen without horizontal scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  for (const path of ["/", "/control", "/graph", "/whatif", "/advisor", "/benchmark", "/console", "/data"]) {
    await page.goto(path);
    await expect(page.getByRole("status").filter({ hasText: "Live" })).toBeVisible();
    await page.waitForTimeout(800);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(0);
  }
  await page.goto("/control");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SCREENSHOTS}/14-control-room-1280.png` });
});
