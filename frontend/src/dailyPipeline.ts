import type { MarketCollectionStatus } from "./types";

export const DAILY_COLLECTION_SCHEDULE = "15:30 提前采集展示，16:10 补齐核验";

export function dailyPipelineNotice(
  pipeline: MarketCollectionStatus | null,
  tradeDate: string,
): string | null {
  const run = pipeline;
  if (!run || run.trade_date !== tradeDate) return null;
  if (run.phase === "preview") {
    return "当前为提前采集数据，计划 16:10 补齐核验；部分数据可能尚未齐全。";
  }
  if (run.status === "running") return "正在补齐并核验收盘数据。";
  if (run.status === "partial" || run.status === "error") {
    return "收盘数据核验尚未全部通过，部分数据仍待补齐。";
  }
  return null;
}
