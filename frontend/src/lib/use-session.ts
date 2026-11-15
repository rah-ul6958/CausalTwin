"use client";

/** React access to the tab's session id (see session.ts). */

import { useSyncExternalStore } from "react";

import { sessionId } from "@/lib/session";

/** The id never changes while the page is open, so there is nothing to subscribe to. */
function subscribe(): () => void {
  return () => {};
}

/**
 * The tab's session id for rendering. Pages are prerendered on the server, where there is no tab,
 * so the first render (and hydration) gets "" and React renders again with the real id.
 */
export function useSessionId(): string {
  return useSyncExternalStore(subscribe, sessionId, () => "");
}
