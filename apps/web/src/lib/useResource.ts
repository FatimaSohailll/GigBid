"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Loads data, reloads on demand, and can poll without flashing a loading state. */
export function useResource<T>(load: () => Promise<T>, key: string, pollMs?: number) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const latest = useRef(load);
  latest.current = load;

  const reload = useCallback(async () => {
    try {
      setData(await latest.current());
      setError(undefined);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Something went wrong.");
    }
  }, []);

  useEffect(() => {
    setData(undefined);
    void reload();
  }, [key, reload]);

  useEffect(() => {
    if (!pollMs) return;
    const timer = setInterval(() => void reload(), pollMs);
    return () => clearInterval(timer);
  }, [pollMs, reload]);

  return { data, error, loading: data === undefined && error === undefined, reload };
}
