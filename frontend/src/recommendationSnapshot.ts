import type { RecommendationIntelligenceResponse } from "./types";

type Snapshot = Pick<RecommendationIntelligenceResponse,
  "stage" | "target_trade_date" | "relay_base_date" | "refreshed_at"
  | "finalized_at" | "warnings" | "display_context">;

const shanghaiClock = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
});

function clockParts(date: Date) {
  return Object.fromEntries(shanghaiClock.formatToParts(date)
    .filter((part) => part.type !== "literal")
    .map((part) => [part.type, part.value]));
}

/** Persisted naive timestamps are Shanghai local time, like the backend contract. */
export function formatSnapshotTime(value: string | null | undefined) {
  if (!value) return "时间未确认";
  const timePart = value.split("T")[1] ?? value.split(" ")[1];
  if (!timePart) return "时间未确认";
  const hasOffset = timePart.includes("+") || timePart.includes("-") || timePart.endsWith("Z");
  const parsed = new Date(hasOffset ? value : `${value}+08:00`);
  if (!Number.isFinite(parsed.getTime())) return "时间未确认";
  const p = clockParts(parsed);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}（上海时间）`;
}

/** Label the stored cohort without inventing a target date or changing its provenance. */
export function recommendationSnapshotView(snapshot: Snapshot, now: Date) {
  const p = clockParts(now);
  const today = `${p.year}-${p.month}-${p.day}`;
  const afterOpen = Number(p.hour) * 60 + Number(p.minute) >= 9 * 60 + 30;
  const target = snapshot.target_trade_date;
  const context = snapshot.display_context;
  const fallback = context?.is_history_fallback === true;
  const historical = fallback || (target !== null && target < today);
  const draftExpired = snapshot.stage === "draft" && target !== null
    && (target < today || (target === today && afterOpen));
  const targetLabel = target ? `${target} 目标日` : "目标日未确认";
  const title = historical
    ? (snapshot.stage === "final" ? "历史固化候选" : "历史候选快照")
    : draftExpired ? "已过期盘前草稿"
      : !target ? "目标日未确认的候选"
        : snapshot.stage === "final" ? "盘前固化候选"
          : snapshot.stage === "missed_cutoff" ? "未固化候选快照" : "盘前候选草稿";
  const notices: string[] = [];

  if (fallback && context) {
    const currentTarget = context.latest_target_trade_date
      ? `当前目标日 ${context.latest_target_trade_date}` : "当前目标日未确认";
    const state = context.latest_stage === "missed_cutoff"
      ? "未在开盘前固化，当前没有可展示候选"
      : context.latest_stage === "draft"
        ? "尚未固化，当前没有可展示候选"
        : "已固化，但当前没有可展示候选";
    notices.push(`${currentTarget}：${state}。下方回退展示历史快照，候选所属${targetLabel}。`);
    notices.push(`当前状态更新：${formatSnapshotTime(context.latest_refreshed_at)}。`);
  } else if (historical) {
    notices.push(`候选所属${targetLabel}，已是历史快照，仅供对应日期复盘。`);
  }
  if (!target) notices.push("交易日历或目标日尚未确认，无法判断候选对应的交易日。");
  if (draftExpired) {
    notices.push("该草稿已过目标日 09:30 开盘截止，未作为正式榜单固化，仅供历史参考。");
  } else if (snapshot.stage === "missed_cutoff") {
    notices.push("该目标日未在开盘前固化；不使用开盘后数据补算盘前榜单。");
  }
  const warnings = [...new Set([
    ...(fallback ? context?.latest_warnings ?? [] : []), ...snapshot.warnings,
  ].filter((warning) => warning.trim().length > 0))];

  return {
    title, targetLabel, historical, draftExpired, notices, warnings,
    baseLabel: snapshot.relay_base_date ?? "未确认",
    refreshedLabel: formatSnapshotTime(snapshot.refreshed_at),
    finalizedLabel: snapshot.finalized_at ? formatSnapshotTime(snapshot.finalized_at) : null,
    disclaimer: snapshot.stage === "final"
      ? "该排序已固化，供目标交易日复盘使用。"
      : "草稿仅供研究；只有目标交易日开盘前固化的榜单才进入正式复盘。",
  };
}
