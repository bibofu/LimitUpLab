import { useEffect, useSyncExternalStore } from "react";
import type { PublicSnapshot } from "../utils/publicSnapshot";

/** Render a bounded last-known snapshot immediately, then revalidate in the background. */
export function usePublicSnapshot<T>(resource: PublicSnapshot<T>, enabled = true, intervalMs?: number) {
  const state = useSyncExternalStore(resource.subscribe, resource.read, resource.read);
  useEffect(() => {
    if (!enabled) return;
    void resource.refresh();
    const timer = intervalMs ? window.setInterval(() => { void resource.refresh(); }, intervalMs) : undefined;
    return () => { if (timer !== undefined) window.clearInterval(timer); };
  }, [resource, enabled, intervalMs]);
  const needsRecovery = enabled && state.data === null && !state.loading && !state.error;
  useEffect(() => {
    // A read can expire an already mounted snapshot without changing the route
    // or polling interval. Recover that empty state, but leave errors retryable.
    if (needsRecovery) void resource.refresh();
  }, [resource, needsRecovery]);
  return { ...state, loading: enabled && (state.loading || (state.data === null && !state.error)),
    reload: () => enabled ? resource.refresh(true) : Promise.resolve() };
}
