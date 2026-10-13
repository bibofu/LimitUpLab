import type { ReviewOverviewEvent, ReviewOverviewResult } from "./reviewOverviewStream.ts";

export interface OverviewState {
  status: "idle" | "generating" | "checking" | "ready" | "error";
  text: string;
  model: string | null;
  error: string | null;
}
type Stream = (endDate: string, onEvent: (event: ReviewOverviewEvent) => void,
               signal: AbortSignal) => Promise<ReviewOverviewResult>;

/** Generation is exclusively user initiated; reset withdraws stale or unverified drafts. */
export function createReviewOverviewResource(stream: Stream) {
  const initial = (): OverviewState => ({ status: "idle", text: "", model: null, error: null });
  let state = initial();
  let revision = 0;
  let controller: AbortController | null = null;
  const listeners = new Set<() => void>();
  const publish = (patch: Partial<OverviewState>) => {
    state = { ...state, ...patch };
    listeners.forEach(listener => listener());
  };
  return {
    getState: () => state,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    reset() {
      revision++;
      controller?.abort();
      controller = null;
      publish(initial());
    },
    async generate(endDate: string) {
      if (controller) return;
      const ownRevision = ++revision;
      controller = new AbortController();
      publish({ status: "generating", text: "", model: null, error: null });
      try {
        const result = await stream(endDate, event => {
          if (ownRevision !== revision) return;
          if (event.event === "delta") publish({ text: state.text + event.data.text });
          if (event.event === "checking") publish({ status: "checking" });
        }, controller.signal);
        if (ownRevision !== revision) return;
        publish({ status: "ready", text: result.text, model: result.model });
      } catch (error) {
        if (ownRevision !== revision) return;
        publish({ status: "error", text: "", model: null,
          error: error instanceof Error ? error.message : "本次总结未完成，请重试。" });
      } finally {
        if (ownRevision === revision) controller = null;
      }
    },
  };
}
