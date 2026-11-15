/**
 * Accessibility audit with axe-core (WCAG 2.1 levels A and AA) on every page, plus layout checks
 * at narrower widths than the 1280 px target: no page may scroll sideways at 1024 px (small
 * laptop) or 768 px (tablet).
 *
 * The audit fails on any violation, listing rule, impact and the elements concerned.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const PAGES = ["/", "/control", "/graph", "/whatif", "/advisor", "/benchmark", "/console", "/data"];

/** Open a page and wait until it shows live data. */
async function open(page: Page, path: string) {
  await page.goto(path);
  await expect(page.getByRole("status").filter({ hasText: "Live" })).toBeVisible();
  await page.waitForTimeout(1500); // entry animations finish, so colours are measured at full opacity
}

for (const path of PAGES) {
  test(`no WCAG 2.1 A/AA violations on ${path}`, async ({ page }) => {
    await open(page, path);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .exclude(".react-flow__minimap") // decorative overview of the graph, hidden from assistive tech
      .analyze();
    const summary = results.violations.map(
      (violation) =>
        `${violation.id} (${violation.impact}): ${violation.help} -> ${violation.nodes
          .slice(0, 3)
          .map((node) => node.target.join(" "))
          .join(" | ")}`,
    );
    expect(summary, summary.join("\n")).toEqual([]);
  });
}

for (const width of [1024, 768]) {
  test(`every page fits a ${width} px wide screen without horizontal scrolling`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const path of PAGES) {
      await open(page, path);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `horizontal overflow on ${path} at ${width} px`).toBeLessThanOrEqual(0);
    }
  });
}
