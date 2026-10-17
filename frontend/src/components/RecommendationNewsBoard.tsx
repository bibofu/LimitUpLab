import { ChevronLeft, ChevronRight, ExternalLink, Newspaper } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useFinanceNews } from "../hooks/useFinanceNews";
import { formatSnapshotTime } from "../recommendationSnapshot";
import type { FinanceNewsItem } from "../types";


interface RecommendationNewsViewItem {
  key: string;
  title: string;
  summary: string;
  publishedAt: string;
  source: string;
  url: string;
  category: string;
}


/**
 * Show paginated research news with its fetch state and source links.
 */
export function RecommendationNewsBoard() {
  const [page, setPage] = useState(1);
  const { news, loading, error, reload, waitingTooLong } = useFinanceNews(page, "history");
  const refreshError = error || news?.refresh_error;
  const updating = !refreshError && !waitingTooLong && (loading || news?.refreshing);

  useEffect(() => {
    if (news && !loading && news.page !== page) setPage(news.page);
  }, [news, loading, page]);

  const visibleNews = useMemo(
    () => (news?.items ?? []).map(marketNewsViewItem),
    [news],
  );
  const pageNumbers = news ? paginationWindow(news.page, news.total_pages) : [];

  return (
    <section className="recommendation-news-board" aria-label="盘前实时新闻">
      <header className="recommendation-news-header">
        <div>
          <Newspaper size={18} />
          <span>
            <strong>实时新闻</strong>
            <small>
              {news ? `近 24 小时（以来源抓取为截止） · ${news.sources.join(" · ")} · 共 ${news.total} 条`
                : "近 24 小时（以来源抓取为截止）"}
            </small>
          </span>
        </div>
        <span className="recommendation-news-refresh">
          来源抓取：{news ? formatSnapshotTime(news.fetched_at) : "—"}
        </span>
      </header>
      {!news && !refreshError ? (
        <div className="recommendation-news-state" role="status">
          正在获取近24小时新闻
        </div>
      ) : null}
      {refreshError ? (
        <div className="resource-refresh-status" role="status">
          {news ? "新闻更新失败，继续显示上次快讯" : "新闻暂不可用"}
          <button type="button" onClick={() => void reload()}>重新加载新闻</button>
        </div>
      ) : null}
      {news && !refreshError && (updating || news.stale || waitingTooLong) ? (
        <div className="recommendation-news-status" role="status">
          {waitingTooLong ? "更新时间较长，继续显示上次快讯" : updating ? "显示上次快讯，正在更新" : "显示上次快讯"}
          {waitingTooLong ? <button type="button" onClick={() => void reload()}>重新加载新闻</button> : null}
        </div>
      ) : null}
      {news && visibleNews.length === 0 ? (
        <div className="recommendation-news-state">
          该次抓取的近 24 小时范围内没有市场快讯。
        </div>
      ) : null}
      {visibleNews.length > 0 ? (
        <div className="recommendation-news-list">
          {visibleNews.map((item) => (
            <article className="recommendation-news-item" key={item.key}>
              <time dateTime={item.publishedAt}>{formatRecommendationNewsTime(item.publishedAt)}</time>
              <div className="recommendation-news-body">
                <a href={item.url} target="_blank" rel="noreferrer">
                  <strong>{item.title}</strong>
                  <ExternalLink size={13} aria-hidden="true" />
                </a>
                <span>{item.source} · {item.category}</span>
              </div>
            </article>
          ))}
        </div>
      ) : null}
      {news && news.total_pages > 1 ? (
        <footer className="recommendation-news-pagination" aria-label="市场快讯分页">
          <span>第 {news.page} / {news.total_pages} 页</span>
          <div>
            <button
              aria-label="上一页"
              disabled={news.page <= 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
              title="上一页"
              type="button"
            >
              <ChevronLeft size={15} />
            </button>
            {pageNumbers.map((pageNumber) => (
              <button
                aria-current={pageNumber === news.page ? "page" : undefined}
                className={pageNumber === news.page ? "active" : undefined}
                key={pageNumber}
                onClick={() => setPage(pageNumber)}
                type="button"
              >
                {pageNumber}
              </button>
            ))}
            <button
              aria-label="下一页"
              disabled={news.page >= news.total_pages}
              onClick={() => setPage((value) => Math.min(news.total_pages, value + 1))}
              title="下一页"
              type="button"
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </footer>
      ) : null}
    </section>
  );
}


/**
 * Convert a market-news record into the common news-board display shape.
 */
function marketNewsViewItem(item: FinanceNewsItem): RecommendationNewsViewItem {
  return {
    key: `${item.source}-${item.url}-${item.published_at}`,
    title: item.title,
    summary: item.summary,
    publishedAt: item.published_at,
    source: item.source,
    url: item.url,
    category: item.category,
  };
}


/**
 * Choose the bounded range of page numbers around the current page.
 */
function paginationWindow(current: number, total: number): number[] {
  const visible = Math.min(5, total);
  const start = Math.max(1, Math.min(current - 2, total - visible + 1));
  return Array.from({ length: visible }, (_, index) => start + index);
}


/**
 * Render the publication time used by the intelligence news board.
 */
function formatRecommendationNewsTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.slice(5, 16).replace("T", " ");
  const now = new Date();
  const sameDay = parsed.toDateString() === now.toDateString();
  return new Intl.DateTimeFormat("zh-CN", {
    month: sameDay ? undefined : "2-digit",
    day: sameDay ? undefined : "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}
