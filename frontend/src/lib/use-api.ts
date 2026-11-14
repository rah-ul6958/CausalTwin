"use client";

/** Small data-fetching hook: loading, error and retry for one API call, optional polling. */

import { useCallback, useEffect, useRef, useState } from "react";

export interface ApiState<T> {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
  reload: () => void;
}

/**
 * Runs `load` on mount and whenever `key` changes (and every `refreshMs` if given).
 * `key` is a string describing the inputs, so callers do not need dependency arrays.
 */
export function useApi<T>(load: () => Promise<T>, key = "", refreshMs?: number): ApiState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const loadRef = useRef(load);

  useEffect(() => {
    loadRef.current = load;
  });

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        const result = await loadRef.current();
        if (!cancelled) {
          setData(result);
          setError(undefined);
        }
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void run();
    const timer = refreshMs ? setInterval(() => void run(), refreshMs) : undefined;
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [key, nonce, refreshMs]);

  const reload = useCallback(() => {
    setLoading(true);
    setNonce((value) => value + 1);
  }, []);

  return { data, error, loading, reload };
}
