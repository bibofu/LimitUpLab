import { BarChart3, MapPin } from "lucide-react";

import { formatAmount, formatNetAmount, formatOptionalPercent, formatPercent } from "../dashboardFormatters";
import type { FirstBoardRating, RecommendationIntelligenceItem, StockPositionAssessment } from "../types";
import { Panel } from "./Panel";



/**
 * Show the backend's price-position assessment and supporting evidence.
 */
export function StockPositionPanel({
  position,
  tradeDate,
  loading,
  error,
}: {
  position: StockPositionAssessment | null;
  tradeDate: string;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return (
      <Panel title="首板位置判断" icon={<MapPin size={18} />}>
        <div className="rating-detail-empty">正在分析价格位置...</div>
      </Panel>
    );
  }

  if (error || !position) {
    return (
      <Panel title="首板位置判断" icon={<MapPin size={18} />}>
        <div className="rating-detail-empty">{error ?? "暂无足够 K 线判断当前位置。"}</div>
      </Panel>
    );
  }

  return (
    <Panel title="首板位置判断" icon={<MapPin size={18} />}>
      <StockPositionDetail position={position} tradeDate={tradeDate} />
    </Panel>
  );
}



/**
 * Explain a first-board rating through its score breakdown, confidence, facts and risks.
 */
export function FirstBoardRatingDetail({
  intelligence,
  rating,
}: {
  intelligence: RecommendationIntelligenceItem | null;
  rating: FirstBoardRating;
}) {
  /** Render explainable first-board score details for the selected stock. */

  const scoreBreakdown = rating.score_breakdown.map((item) => {
    if (item.name === "龙虎榜资金" && intelligence) {
      return {
        ...item,
        evidence: [intelligence.dragon_tiger_on_list
          ? `当前龙虎榜已上榜，净买额 ${formatNetAmount(intelligence.dragon_tiger_net_buy_amount)}`
          : "当前龙虎榜数据未显示上榜"],
      };
    }
    if (item.name === "市场人气" && intelligence) {
      return {
        ...item,
        evidence: [intelligence.popularity_rank === null
          ? "当前接入榜单未覆盖该股，不推断榜外名次"
          : `当前人气排名第 ${intelligence.popularity_rank}`],
      };
    }
    return item;
  });
  const boardPatternScore = scoreBreakdown.find((item) => item.name === "上板形态");
  const marketCapScore = scoreBreakdown.find((item) => item.name === "市值偏好");
  const floatMarketCap = rating.facts.enrichment?.float_market_cap;
  const dragonTigerOnList = intelligence?.dragon_tiger_on_list
    ?? rating.facts.enrichment?.dragon_tiger_on_list
    ?? false;
  const popularityRank = intelligence?.popularity_rank
    ?? rating.facts.enrichment?.popularity_rank
    ?? null;
  const displayScore = intelligence?.draft_score ?? rating.score;
  const displayRating = displayScore >= 80
    ? "A"
    : displayScore >= 65
      ? "B"
      : displayScore >= 50
        ? "C"
        : "D";
  const capRule = floatMarketCap === null || floatMarketCap === undefined
    ? "流通市值数据缺失"
    : floatMarketCap <= 5_000_000_000
      ? "低市值档，因子加分"
      : floatMarketCap > 50_000_000_000
        ? "高市值档，因子降分"
        : "中间市值档，按区间评分";

  return (
    <Panel title="Agent 评分拆解" icon={<BarChart3 size={18} />}>
      <div className="rating-detail">
        <div className="rating-detail-head">
          <span className={`rating-badge rating-${displayRating.toLowerCase()}`}>
            {displayRating}
          </span>
          <div>
            <strong>{displayScore.toFixed(1)}</strong>
            <span>置信度 {formatPercent(rating.confidence)}</span>
          </div>
        </div>

        <div className="rating-rule-signals" aria-label="评分规则信号">
          <span>
            <small>上板形态</small>
            <strong>
              {rating.facts.is_one_word_board
                ? "一字板，形态评分降档"
                : "盘中上板，换手承接更充分"}
            </strong>
            {boardPatternScore ? (
              <em>{boardPatternScore.score.toFixed(1)} / {boardPatternScore.max_score.toFixed(1)}</em>
            ) : null}
          </span>
          <span>
            <small>市值偏好</small>
            <strong>{capRule}</strong>
            {marketCapScore ? (
              <em>{marketCapScore.score.toFixed(1)} / {marketCapScore.max_score.toFixed(1)}</em>
            ) : null}
          </span>
        </div>

        {rating.facts.enrichment ? (
          <div className="rating-enrichment-facts">
            <span>
              <small>近20日涨幅</small>
              <strong>{formatOptionalPercent(rating.facts.enrichment.return_20d_pct)}</strong>
            </span>
            <span>
              <small>距60日高点</small>
              <strong>{formatOptionalPercent(rating.facts.enrichment.distance_60d_high_pct)}</strong>
            </span>
            <span>
              <small>流通市值</small>
              <strong>
                {rating.facts.enrichment.float_market_cap === null
                  ? "暂无"
                  : formatAmount(rating.facts.enrichment.float_market_cap)}
              </strong>
            </span>
            <span>
              <small>上市日期</small>
              <strong>{rating.facts.enrichment.listing_date ?? "早期上市"}</strong>
            </span>
            <span>
              <small>龙虎榜</small>
              <strong>{dragonTigerOnList ? "上榜" : "当前数据未显示上榜"}</strong>
            </span>
            <span>
              <small>当前人气</small>
              <strong>
                {popularityRank === null
                  ? "当前榜单未覆盖"
                  : `第 ${popularityRank}`}
              </strong>
            </span>
          </div>
        ) : null}

        <div className="rating-detail-section">
          <h3>评分项</h3>
          <div className="score-breakdown-list">
            {scoreBreakdown.map((item) => (
              <div className="score-breakdown-item" key={item.name}>
                <div>
                  <strong>{item.name}</strong>
                  <span>{item.evidence.join("；")}</span>
                </div>
                <b>{item.score.toFixed(1)} / {item.max_score.toFixed(1)}</b>
              </div>
            ))}
          </div>
        </div>

        <TagSection title="主要理由" items={rating.reasons} tone="good" />
        <TagSection title="风险观察" items={rating.risks} tone="risk" />
        {rating.facts.data_missing.length > 0 ? (
          <TagSection title="缺失数据" items={rating.facts.data_missing} tone="muted" />
        ) : null}
      </div>
    </Panel>
  );
}


/**
 * Render the detailed regime matches and measurements behind a position assessment.
 */
function StockPositionDetail({
  position,
  tradeDate,
}: {
  position: StockPositionAssessment;
  tradeDate: string;
}) {
  return (
    <section className="stock-position-detail">
      <header>
        <div>
          <span>{tradeDate} 收盘 · {position.bar_count} 根日 K</span>
        </div>
        <strong>{position.primary.label}</strong>
        <b>匹配度 {position.primary.score.toFixed(0)}</b>
      </header>
      <div className="stock-position-tags">
        {position.tags.map((tag) => <span key={tag}>{tag}</span>)}
      </div>
      <ul>
        {position.evidence.map((item) => <li key={item}>{item}</li>)}
      </ul>
      {position.alternatives.length > 0 ? (
        <small>
          次选：{position.alternatives.map((item) => `${item.label} ${item.score.toFixed(0)}`).join("；")}
        </small>
      ) : null}
    </section>
  );
}


/**
 * Render a titled group of evidence/risk tags from the supplied text items.
 */
function TagSection({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "good" | "risk" | "muted";
}) {
  return (
    <div className="rating-detail-section">
      <h3>{title}</h3>
      <div className="tag-list">
        {items.map((item) => (
          <span className={`detail-tag tag-${tone}`} key={item}>{item}</span>
        ))}
      </div>
    </div>
  );
}
