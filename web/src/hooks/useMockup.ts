/**
 * The debounced 2D preview, shared by every mode that has one.
 *
 * It is one call behind a hook on purpose: phase 4 replaces the server
 * round-trip with a local port of the classifier, and only this file changes.
 */
import { useEffect, useState } from "react";
import { mockup } from "../api";
import type { JobParams } from "../types";

const DEBOUNCE_MS = 350;

export function useMockup(file: File | null, params: Partial<JobParams>, deps: unknown[]) {
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!file) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setBusy(true);
      try {
        const next = await mockup(file, params, controller.signal);
        setUrl((old) => {
          if (old) URL.revokeObjectURL(old);
          return next;
        });
        setError("");
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "preview failed");
        }
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    }, DEBOUNCE_MS);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file, ...deps]);

  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);

  return { url, busy, error };
}
