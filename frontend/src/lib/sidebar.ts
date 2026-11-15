"use client";

/** The sidebar's expanded or collapsed state, remembered per browser (presentation only). */

import { useSyncExternalStore } from "react";

const STORAGE_KEY = "causaltwin-sidebar-expanded";
const listeners = new Set<() => void>();
/** The choice made in this page load (used when storage is blocked). */
let chosen: boolean | null = null;

function read(): boolean {
  if (chosen !== null) return chosen;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Expand or collapse the sidebar (the choice survives reloads where storage is allowed). */
export function setSidebarExpanded(expanded: boolean): void {
  chosen = expanded;
  try {
    window.localStorage.setItem(STORAGE_KEY, expanded ? "1" : "0");
  } catch {
    /* storage blocked: the choice lasts until the next reload */
  }
  for (const listener of listeners) listener();
}

/** Whether the sidebar is expanded (collapsed while rendering on the server). */
export function useSidebarExpanded(): boolean {
  return useSyncExternalStore(subscribe, read, () => false);
}
