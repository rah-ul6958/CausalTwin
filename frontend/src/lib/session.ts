/**
 * This browser tab's session id. The backend gives every session its own live simulation, so
 * one viewer's Reset, fault or guided demo does not change what other viewers see.
 *
 * The id lives in sessionStorage: it survives a reload of the tab but not closing it, and every
 * new tab gets a new one (a duplicated tab copies it, so it shares the original's simulation).
 */

/** Must match SESSION_HEADER and SESSION_QUERY_PARAMETER in backend/causaltwin/config.py. */
export const SESSION_HEADER = "X-CausalTwin-Session";
export const SESSION_QUERY_PARAMETER = "session";
export const SESSION_STORAGE_KEY = "causaltwin-session";

let cachedId: string | null = null;

/** A random id: a UUID where the browser offers one (secure pages), otherwise 32 random hex digits. */
function randomId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** The tab's session id ("" while rendering on the server, where there is no tab). */
export function sessionId(): string {
  if (cachedId) return cachedId;
  if (typeof window === "undefined") return "";
  try {
    cachedId = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!cachedId) {
      cachedId = randomId();
      window.sessionStorage.setItem(SESSION_STORAGE_KEY, cachedId);
    }
  } catch {
    // Storage can be blocked (private modes, strict settings): keep an id for this page load only.
    cachedId = cachedId ?? randomId();
  }
  return cachedId;
}

/** A URL with the session id added as a query parameter (for the WebSocket and download links). */
export function withSession(url: string): string {
  const id = sessionId();
  if (!id) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}${SESSION_QUERY_PARAMETER}=${encodeURIComponent(id)}`;
}
