"use client";

/**
 * Simple or Expert view, remembered per browser (presentation only).
 * Simple view folds technical panels away behind "Show details"; Expert view shows everything.
 */

import { useSyncExternalStore } from "react";

export type ViewMode = "simple" | "expert";

const STORAGE_KEY = "causaltwin-view-mode";
const listeners = new Set<() => void>();
/** The choice made in this page load (used when storage is blocked). */
let chosen: ViewMode | null = null;

function read(): ViewMode {
  if (chosen !== null) return chosen;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "expert" ? "expert" : "simple";
  } catch {
    return "simple";
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Switch between Simple and Expert view (the choice survives reloads where storage is allowed). */
export function setViewMode(mode: ViewMode): void {
  chosen = mode;
  try {
    window.localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* storage blocked: the choice lasts until the next reload */
  }
  for (const listener of listeners) listener();
}

/** The current view (Simple while rendering on the server). */
export function useViewMode(): ViewMode {
  return useSyncExternalStore(subscribe, read, () => "simple");
}
