import type { ReviewAgentReportResponse } from "../types";

export interface ReviewReportState {
  report: ReviewAgentReportResponse | null;
  loading: boolean;
  error: string | null;
  generating: boolean;
  summaryError: string | null;
}

type FetchReview = (params: {
  end_date: string;
  top_per_day: number;
  follow_days: number;
  use_llm: boolean;
}) => Promise<ReviewAgentReportResponse>;

interface Entry {
  state: ReviewReportState;
  listeners: Set<() => void>;
  attemptedSummary: boolean;
}

/** Share work across StrictMode and route remounts; only an explicit retry repeats a model call. */
export function createReviewReportResource(fetchReview: FetchReview) {
  const entries = new Map<string, Entry>();
  const entryFor = (endDate: string) => {
    let entry = entries.get(endDate);
    if (!entry) {
      if (entries.size >= 8) {
        for (const [key, cached] of entries) {
          if (!cached.listeners.size && !cached.state.loading && !cached.state.generating) {
            entries.delete(key);
            break;
          }
        }
      }
      entry = {
        state: { report: null, loading: false, error: null, generating: false, summaryError: null },
        listeners: new Set(),
        attemptedSummary: false,
      };
      entries.set(endDate, entry);
    }
    return entry;
  };
  const publish = (entry: Entry, patch: Partial<ReviewReportState>) => {
    entry.state = { ...entry.state, ...patch };
    entry.listeners.forEach(listener => listener());
  };
  const request = (endDate: string, useLlm: boolean) => fetchReview({
    end_date: endDate, top_per_day: 10, follow_days: 5, use_llm: useLlm,
  });
  const generate = async (endDate: string, entry: Entry, retry = false) => {
    if (!entry.state.report || entry.state.loading || entry.state.generating) return;
    if (entry.attemptedSummary && !retry) return;
    entry.attemptedSummary = true;
    publish(entry, { generating: true, summaryError: null });
    try {
      const report = await request(endDate, true);
      // Adopt facts and narrative together, since outcomes may have been backfilled in between.
      publish(entry, { report, generating: false });
    } catch (error) {
      publish(entry, {
        generating: false,
        summaryError: error instanceof Error ? error.message : "复盘总结生成失败，请重试",
      });
    }
  };

  return {
    getState: (endDate: string) => entryFor(endDate).state,
    subscribe(endDate: string, listener: () => void) {
      const entry = entryFor(endDate);
      entry.listeners.add(listener);
      return () => { entry.listeners.delete(listener); };
    },
    async load(endDate: string, retry = false) {
      const entry = entryFor(endDate);
      if (entry.state.loading) return;
      if (entry.state.report) {
        await generate(endDate, entry);
        return;
      }
      if (entry.state.error && !retry) return;
      publish(entry, { loading: true, error: null });
      try {
        const report = await request(endDate, false);
        publish(entry, { report, loading: false });
        // Leaving before local facts arrive should not initiate a paid request.
        if (entry.listeners.size) await generate(endDate, entry);
      } catch (error) {
        publish(entry, {
          loading: false,
          error: error instanceof Error ? error.message : "复盘数据加载失败，请重试",
        });
      }
    },
    regenerate: (endDate: string) => generate(endDate, entryFor(endDate), true),
  };
}
