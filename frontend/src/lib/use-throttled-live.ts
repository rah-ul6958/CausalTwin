"use client";

/**
 * Read a value from the live store, but re-render at most every `intervalMs`. Used for many small
 * charts (sensor cards, graph sparklines): redrawing ten Recharts charts on every batch of live
 * messages was the main source of long main-thread tasks in the Control Room.
 */

import { useCallback, useRef, useSyncExternalStore } from "react";

import { useLiveStore } from "@/lib/store";

type LiveState = ReturnType<typeof useLiveStore.getState>;

export function useThrottledLive<T>(selector: (state: LiveState) => T, intervalMs: number): T {
  // The last value handed to React; it changes only when listeners are told.
  const box = useRef(selector(useLiveStore.getState()));

  const subscribe = useCallback(
    (notify: () => void) => {
      let timer: ReturnType<typeof setTimeout> | null = null;
      let lastRun = 0;
      const run = () => {
        timer = null;
        lastRun = Date.now();
        const next = selector(useLiveStore.getState());
        if (next !== box.current) {
          box.current = next;
          notify();
        }
      };
      const unsubscribe = useLiveStore.subscribe(() => {
        const wait = intervalMs - (Date.now() - lastRun);
        if (wait <= 0) run();
        else if (timer === null) timer = setTimeout(run, wait);
      });
      run(); // pick up anything that changed before subscribing
      return () => {
        unsubscribe();
        if (timer !== null) clearTimeout(timer);
      };
    },
    [selector, intervalMs],
  );

  const getSnapshot = useCallback(() => box.current, []);
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
