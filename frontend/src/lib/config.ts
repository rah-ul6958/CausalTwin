/** Runtime settings for the frontend (backend URLs come from environment variables). */

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? `${API_URL.replace(/^http/, "ws")}/ws/stream`;

/**
 * How long the UI waits for the backend before showing an error with Retry. Slightly longer
 * than the backend's own limits (30 s for heavy requests, 120 s for an upload analysis), so the
 * backend's clearer message normally arrives first. Demo beats run many simulation steps.
 */
export const REQUEST_TIMEOUT_MS = 35_000;
export const UPLOAD_TIMEOUT_MS = 130_000;
export const DEMO_TIMEOUT_MS = 300_000;

/** How many live points the charts keep (rolling buffer). */
export const LIVE_BUFFER_POINTS = 300;
/**
 * WebSocket messages are applied to the store in batches at this interval (about 2.5 redraws per
 * second; the live simulation makes at most 5 steps per second, so each redraw shows 1-2 new steps).
 */
export const STORE_FLUSH_MS = 400;
/** Messages kept while the tab is hidden (older ones are dropped; the store keeps a rolling buffer anyway). */
export const MAX_QUEUED_MESSAGES = 2000;
/** Upper bounds for live lists kept in memory. */
export const MAX_EVENTS = 500;
export const MAX_ALERTS = 200;
export const MAX_LOGS = 1500;
/** Log entries fetched from the REST API when the socket connects. */
export const SEED_LOGS = 400;
/** Routine per-step risk estimates are kept apart, so they cannot push other log entries out. */
export const MAX_ROUTINE_LOGS = 200;
/** How often the simulation status (running, scenario, faults) is refreshed from the API. */
export const STATUS_POLL_MS = 3000;
/** Debounce for what-if slider changes. */
export const WHATIF_DEBOUNCE_MS = 450;
