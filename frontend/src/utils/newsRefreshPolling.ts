import type { FinanceNewsPage } from "../types";
import type { PublicSnapshot } from "./publicSnapshot";

/** Follow a server-side refresh briefly, without retrying failures or owning shared requests. */
export function followNewsRefresh(resource: PublicSnapshot<FinanceNewsPage>, onTimeout: () => void, {
  now = Date.now,
  schedule = (callback: () => void, delay: number) => window.setTimeout(callback, delay),
  cancel = (timer: number) => window.clearTimeout(timer),
  budgetMs = 60_000,
  intervalMs = 1_000,
} = {}) {
  let stopped = false;
  let timer: number | undefined;
  const deadline = now() + budgetMs;
  const next = () => {
    if (stopped) return;
    const state = resource.read();
    if (state.error || state.data?.refresh_error || !state.data?.refreshing) return;
    const remaining = deadline - now();
    if (remaining <= 0) { onTimeout(); return; }
    timer = schedule(() => {
      timer = undefined;
      if (stopped) return;
      if (now() >= deadline) { onTimeout(); return; }
      // A normal snapshot is fresh for 30 seconds; these polls must bypass that gate.
      void resource.refresh(true).then(next);
    }, Math.min(intervalMs, remaining));
  };
  next();
  return () => {
    stopped = true;
    if (timer !== undefined) cancel(timer);
  };
}
