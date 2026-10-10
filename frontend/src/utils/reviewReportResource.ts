import type { ReviewAgentReportResponse } from "../types";

export type ReviewSummaryStatus = "idle" | "generating" | "ready" | "fallback" | "error";

export interface ReviewReportState {
  report: ReviewAgentReportResponse | null;
  loading: boolean;
  error: string | null;
  generating: boolean;
  summaryError: string | null;
  summaryStatus: ReviewSummaryStatus;
}

type FetchReview = (params: {
  end_date: string;
  top_per_day: number;
  follow_days: number;
  use_llm: boolean;
  refresh_facts?: boolean;
}) => Promise<ReviewAgentReportResponse>;

interface Entry {
  state: ReviewReportState;
  listeners: Set<() => void>;
  attemptedSummary: boolean;
  revision: number;
  refreshFacts: boolean;
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
        state: { report: null, loading: false, error: null, generating: false, summaryError: null, summaryStatus: "idle" },
        listeners: new Set(),
        attemptedSummary: false,
        revision: 0,
        refreshFacts: false,
      };
      entries.set(endDate, entry);
    }
    return entry;
  };
  const publish = (entry: Entry, patch: Partial<ReviewReportState>) => {
    entry.state = { ...entry.state, ...patch };
    entry.listeners.forEach(listener => listener());
  };
  const request = (endDate: string, useLlm: boolean, refreshFacts = false) => fetchReview({
    end_date: endDate, top_per_day: 10, follow_days: 5, use_llm: useLlm,
    ...(refreshFacts ? { refresh_facts: true } : {}),
  });
  const generate = async (endDate: string, entry: Entry, retry = false, facts?: ReviewAgentReportResponse) => {
    const currentReport = facts ?? entry.state.report;
    if (!currentReport || (!facts && entry.state.loading) || entry.state.generating) return;
    if (entry.attemptedSummary && !retry) return;
    entry.attemptedSummary = true;
    const revision = entry.revision;
    // Publish initial facts and the pending summary together: rule data is not a failed model call.
    publish(entry, { report: currentReport, loading: false, generating: true, summaryError: null, summaryStatus: "generating" });
    try {
      const report = await request(endDate, true);
      if (revision !== entry.revision) return;
      // Adopt facts and narrative together, since outcomes may have been backfilled in between.
      const summaryStatus = report.generation_mode === "llm" ? "ready"
        : report.generation_mode === "deterministic" ? "fallback" : "error";
      publish(entry, {
        report, generating: false, summaryStatus,
        summaryError: summaryStatus === "error" ? "模型总结来源未确认，请重试。" : null,
      });
    } catch (error) {
      if (revision !== entry.revision) return;
      publish(entry, {
        generating: false,
        summaryStatus: "error",
        summaryError: error instanceof Error ? error.message : "复盘总结生成失败，请重试",
      });
    }
  };

  const load = async (endDate: string, retry = false) => {
    const entry = entryFor(endDate);
    if (entry.state.loading) return;
    if (entry.state.report) {
      await generate(endDate, entry);
      return;
    }
    if (entry.state.error && !retry) return;
    const revision = entry.revision;
    publish(entry, { loading: true, error: null });
    try {
      const report = await request(endDate, false, entry.refreshFacts);
      if (revision !== entry.revision) return;
      entry.refreshFacts = false;
      // Leaving before local facts arrive should not initiate a paid request.
      if (entry.listeners.size && !entry.attemptedSummary) {
        await generate(endDate, entry, false, report);
      } else {
        publish(entry, { report, loading: false, summaryStatus: "idle" });
      }
    } catch (error) {
      if (revision !== entry.revision) return;
      publish(entry, {
        loading: false,
        error: error instanceof Error ? error.message : "复盘数据加载失败，请重试",
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
    load,
    async refreshFacts() {
      const pending: Promise<void>[] = [];
      for (const [endDate, entry] of [...entries]) {
        // Invalidate old model/fact requests together, including inactive dates.
        entry.revision++;
        entry.attemptedSummary = true;
        entry.refreshFacts = true;
        publish(entry, { report: null, loading: false, error: null, generating: false, summaryError: null, summaryStatus: "idle" });
        if (entry.listeners.size) pending.push(load(endDate));
      }
      await Promise.all(pending);
    },
    regenerate: (endDate: string) => generate(endDate, entryFor(endDate), true),
  };
}
