import { fetchFinanceNews, fetchMarketSummary, fetchRecommendationIntelligence, PUBLIC_DATA_SCOPE } from "../api";
import type { FinanceNewsPage, MarketSummary, RecommendationIntelligenceResponse } from "../types";
import { createPublicSnapshot } from "./publicSnapshot";

const scope = PUBLIC_DATA_SCOPE;
const record = (value: unknown): value is Record<string, any> => value !== null && typeof value === "object" && !Array.isArray(value);
const strings = (value: unknown) => Array.isArray(value) && value.every(item => typeof item === "string");
const numeric = (value: unknown) => typeof value === "number" && Number.isFinite(value);
const nullableString = (value: unknown) => value === null || typeof value === "string";

export const marketSummarySnapshot = createPublicSnapshot<MarketSummary>({
  key: `${scope}:market-overview`, fetchValue: fetchMarketSummary, maxAgeMs: 24 * 60 * 60_000,
  valid: (value): value is MarketSummary => record(value) && typeof value.trade_date === "string"
    && [value.first_board_count, value.continued_board_count, value.max_board_height].every(numeric)
    && Array.isArray(value.indices) && value.indices.every(index => record(index)
      && typeof index.symbol === "string" && typeof index.name === "string"
      && numeric(index.close) && numeric(index.change_pct)),
});

export const recommendationSnapshot = createPublicSnapshot<RecommendationIntelligenceResponse>({
  key: `${scope}:recommendation-intelligence`, fetchValue: fetchRecommendationIntelligence,
  maxAgeMs: 24 * 60 * 60_000,
  valid: (value): value is RecommendationIntelligenceResponse => record(value)
    && ["draft", "final", "missed_cutoff"].includes(value.stage) && typeof value.refreshed_at === "string"
    && nullableString(value.target_trade_date) && nullableString(value.relay_base_date)
    && nullableString(value.finalized_at) && strings(value.warnings) && numeric(value.relay_display_limit)
    && (!value.display_context || (record(value.display_context)
      && (value.display_context.latest_warnings === undefined || strings(value.display_context.latest_warnings))))
    && Array.isArray(value.items) && value.items.every(item => record(item)
      && [item.name, item.symbol, item.base_trade_date].every(field => typeof field === "string")
      && [item.base_rank, item.rank, item.base_score, item.draft_score, item.dynamic_adjustment].every(numeric)
      && strings(item.update_reasons) && strings(item.close_information_reasons)
      && Array.isArray(item.latest_news) && item.latest_news.every((news: unknown) => record(news) && typeof news.title === "string")),
});

const newsPages = new Map<number, ReturnType<typeof createPublicSnapshot<FinanceNewsPage>>>();
export function financeNewsSnapshot(page: number) {
  let resource = newsPages.get(page);
  if (!resource) {
    resource = createPublicSnapshot<FinanceNewsPage>({
      key: `${scope}:finance-news:page=${page}:size=10`, fetchValue: async () => {
        const response = await fetchFinanceNews(page);
        // A clamped last page is valid for this delivery, but is not a reusable
        // snapshot of the requested page when the feed grows again later.
        if (response.page !== page && newsPages.get(page) === resource) newsPages.delete(page);
        return response;
      },
      maxAgeMs: 15 * 60_000,
      // Only the landing page persists across reloads; other pages stay bounded in memory.
      ...(page === 1 ? {} : { storage: () => null }),
      valid: (value): value is FinanceNewsPage => record(value) && typeof value.fetched_at === "string"
        && strings(value.sources) && [value.page, value.total, value.total_pages].every(numeric)
        && value.page > 0 && value.page <= page && Array.isArray(value.items)
        && value.items.every(item => record(item)
          && [item.title, item.summary, item.published_at, item.source, item.url, item.category].every(field => typeof field === "string")),
    });
    if (newsPages.size >= 10) newsPages.delete(newsPages.keys().next().value!);
    newsPages.set(page, resource);
  }
  return resource;
}

export async function refreshRecommendationSnapshots() {
  await Promise.all([recommendationSnapshot.refresh(true),
    ...[...newsPages.values()].filter(resource => resource.isObserved()).map(resource => resource.refresh(true))]);
}
