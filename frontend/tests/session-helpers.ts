/**
 * Helpers for tests that call the backend directly: every browser tab has its own live
 * simulation (its session), so a direct API call must carry the page's session id to act on
 * the simulation that page shows.
 */

import type { APIRequestContext, APIResponse, Page } from "@playwright/test";

import { SESSION_HEADER, SESSION_STORAGE_KEY } from "../src/lib/session";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** The session id the page's tab uses (set by the app on its first request). */
export async function pageSessionId(page: Page): Promise<string> {
  const id = await page.evaluate((key) => window.sessionStorage.getItem(key), SESSION_STORAGE_KEY);
  if (!id) throw new Error("The page has no session id yet; open a page of the app first.");
  return id;
}

/** POST a /sim/control action to the page's own live simulation. */
export async function controlPageSimulation(page: Page, request: APIRequestContext, action: string): Promise<APIResponse> {
  return request.post(`${API}/sim/control`, {
    data: { action },
    headers: { [SESSION_HEADER]: await pageSessionId(page) },
  });
}

/** POST a JSON body to a backend path, acting on the page's own live simulation. */
export async function postForPage(page: Page, request: APIRequestContext, path: string, data?: unknown): Promise<APIResponse> {
  return request.post(`${API}${path}`, { data, headers: { [SESSION_HEADER]: await pageSessionId(page) } });
}
