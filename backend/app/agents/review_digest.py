"""Compose the new summary from exact-date facts without altering saved outcomes."""

from collections import Counter, defaultdict
from datetime import date, time
from decimal import Decimal
from typing import Iterable

from app.agents.review_digest_leaders import build_digest_leaders
from app.agents.review_digest_dragon_tiger import attach_first_board_dragon_tiger
from app.agents.review_digest_profiles import build_digest_group
from app.agents.review_digest_prices import exact_price, next_open_fact
from app.agents.review_market_leaders import _finite, _position
from app.review_digest_models import DigestOverview, DigestStock, ReviewDigest


def build_review_digest(*, picks, predictions, promotion_comparisons, events,
                        repository, end_date: date, trade_dates: Iterable[date]) -> ReviewDigest:
    calendar = sorted({day for day in trade_dates if day <= end_date})
    candidate_dates = [day for day in calendar if day < end_date][-5:]
    selected = {}
    for pick in picks:
        if pick.trade_date in candidate_dates:
            selected.setdefault((pick.trade_date, pick.symbol), pick)
    selected_picks = list(selected.values())
    bars_by_symbol = defaultdict(list)
    if selected_picks:
        for bar in repository.list_daily_bars_for_symbols(
            sorted({pick.symbol for pick in selected_picks}), end_date=end_date,
        ):
            if bar.trade_date <= end_date:
                bars_by_symbol[bar.symbol].append(bar)
    candidates = [candidate_stock(
        pick, predictions.get((pick.trade_date, pick.symbol)), bars_by_symbol[pick.symbol], calendar, end_date,
    ) for pick in selected_picks]
    excellent, weak, ordinary, missing = [], [], [], []
    for stock in candidates:
        group = performance_group(stock)
        {"excellent": excellent, "weak": weak, "ordinary": ordinary, "missing": missing}[group].append(stock)
    excellent.sort(key=lambda stock: (-stock.return_pct, stock.symbol))
    weak.sort(key=lambda stock: (stock.return_pct, stock.symbol))
    leaders, market_notes = build_digest_leaders(
        events=events, repository=repository, end_date=end_date, trade_dates=calendar,
    )
    attach_first_board_dragon_tiger(candidates + leaders, repository, predictions)
    overview = build_digest_overview(
        promotion_comparisons, candidate_dates, len(candidates),
        len(excellent), len(weak), len(ordinary), len(missing),
    )
    notes = [
        "候选取截止日前5个交易日的历史每日Top10；每只股票在同一首板日只计一次，缺日不以更早批次替换。",
        "优秀：截止日收盘相对首板收盘上涨≥9.8%；较差：下跌>5%；两者之间为普通表现。缺任一精确日期收盘不分组。",
        "同一截止日下各批候选观察天数不同；画像是当前样本分布，不能据此认定因果或未来走强概率。",
        "画像的参照为全部候选，包含普通表现和收盘数据不足的候选；每项比例只使用该项特征有效的样本。",
        f"本期候选具有盘前终选前向验证资格的记录{sum(pick.time_cohort == 'premarket_final' for pick in selected_picks)}条；收盘基线与历史补算不计入该资格。",
        *market_notes,
    ]
    if len(candidate_dates) < 5:
        notes.append(f"交易日历仅提供{len(candidate_dates)}个首板日，本期不足5日。")
    missing_dates = sorted(set(candidate_dates) - {pick.trade_date for pick in selected_picks})
    if missing_dates:
        notes.append("候选批次缺失：" + "、".join(day.isoformat() for day in missing_dates))
    if end_date not in calendar:
        notes.append("截止日不在已核验交易日历中，候选收益暂不分组。")
    sources = Counter(
        (prediction.facts_json.get("enrichment") or {}).get("float_market_cap_source")
        for pick in selected_picks if (prediction := predictions.get((pick.trade_date, pick.symbol))) is not None
        and isinstance(prediction.facts_json.get("enrichment"), dict)
    )
    if sources["derived_from_amount_and_turnover"]:
        notes.append(f"候选中{sources['derived_from_amount_and_turnover']}条首板市值源自当日成交额/换手率推算，应视为估计值。")
    return ReviewDigest(
        as_of_date=end_date, candidate_dates=candidate_dates, market_dates=calendar[-5:],
        overview=overview,
        excellent=build_digest_group("excellent", excellent, candidates),
        weak=build_digest_group("weak", weak, candidates),
        leaders=build_digest_group("leaders", leaders), notes=notes,
    )


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() not in {"", "未知", "数据不足", "结构不明"} else None


def concept_labels(value) -> list[str]:
    """Known provider separators only; do not infer new concepts from prose."""
    if not isinstance(value, str):
        return []
    pieces = [value]
    for separator in (";", "；", "、", "|", ",", "，", "+"):
        pieces = [part for piece in pieces for part in piece.split(separator)]
    return list(dict.fromkeys(label for piece in pieces if (label := _text(piece))))


def _clock(value):
    try:
        parsed = value if isinstance(value, time) else time.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.strftime("%H:%M") if parsed != time(0) else None


def candidate_stock(pick, prediction, bars, calendar, end_date) -> DigestStock:
    facts = prediction.facts_json if prediction else {}
    enrichment = facts.get("enrichment")
    enrichment = enrichment if isinstance(enrichment, dict) else {}
    first = exact_price(bars, symbol=pick.symbol, day=pick.trade_date, field="close")
    last = exact_price(bars, symbol=pick.symbol, day=end_date, field="close")
    next_day, next_open, missing = next_open_fact(
        bars=bars, symbol=pick.symbol, first_board_date=pick.trade_date,
        trade_dates=calendar, end_date=end_date,
    )
    if first is None:
        missing.append("首板日收盘价缺失或冲突")
    if last is None:
        missing.append("截止日收盘价缺失或冲突")
    ready = pick.trade_date in calendar and end_date in calendar
    if not ready:
        missing.append("观察交易日历未确认")
    change = float((Decimal(str(last)) / Decimal(str(first)) - 1) * 100) if ready and first and last else None
    breaks = _finite(facts.get("break_count"))
    if breaks is not None and not float(breaks).is_integer():
        breaks = None
    stock = DigestStock(
        symbol=pick.symbol, name=pick.name, first_board_date=pick.trade_date,
        observed_days=calendar.index(end_date) - calendar.index(pick.trade_date) if ready else None,
        first_close=first, cutoff_close=last, return_pct=change,
        next_trade_date=next_day, next_open_pct=next_open,
        position_label=_position(enrichment) or _position({"position": facts.get("position")}),
        industry=_text(facts.get("industry")), concepts=concept_labels(facts.get("concept")),
        float_market_cap=_finite(enrichment.get("float_market_cap"), positive=True),
        first_limit_time=_clock(facts.get("first_limit_time")),
        break_count=int(breaks) if breaks is not None else None,
        turnover_rate=_finite(facts.get("turnover_rate"), positive=True), data_missing=missing,
    )
    for key, label in (("position_label", "首板位置"), ("industry", "行业"),
                       ("float_market_cap", "首板流通市值"), ("first_limit_time", "首封时间")):
        if getattr(stock, key) is None:
            stock.data_missing.append(label + "缺失")
    return stock


def performance_group(stock: DigestStock) -> str:
    if stock.return_pct is None or stock.first_close is None or stock.cutoff_close is None:
        return "missing"
    # Compare prices with exact decimal thresholds, never rounded display returns.
    first, last = Decimal(str(stock.first_close)), Decimal(str(stock.cutoff_close))
    if last >= first * Decimal("1.098"):
        return "excellent"
    if last < first * Decimal("0.95"):
        return "weak"
    return "ordinary"


def build_digest_overview(comparisons, dates, count, excellent, weak, ordinary, missing):
    daily = {item.trade_date: item for item in comparisons if item.trade_date in dates}
    ready = [item for item in daily.values() if item.outcome_ready
             and item.top_pick_sample_size > 0 and item.market_first_board_sample_size > 0
             and item.top_pick_promotion_rate is not None and item.market_promotion_rate is not None]
    candidate_n = sum(item.top_pick_sample_size for item in ready)
    candidate_k = sum(item.top_pick_promoted_count for item in ready)
    market_n = sum(item.market_first_board_sample_size for item in ready)
    market_k = sum(item.market_promoted_count for item in ready)
    candidate_rate = candidate_k / candidate_n if candidate_n else None
    market_rate = market_k / market_n if market_n else None
    advantage = (candidate_rate - market_rate) * 100 if candidate_n and market_n else None
    delta = lambda item: item.top_pick_promoted_count / item.top_pick_sample_size - item.market_promoted_count / item.market_first_board_sample_size
    outperform = sum(delta(item) > 0 for item in ready)
    best = max(ready, key=lambda item: (delta(item), item.top_pick_promotion_rate, item.trade_date)) if ready else None
    if ready:
        relation = f"高于{advantage:.1f}" if advantage > 0 else f"低于{abs(advantage):.1f}" if advantage < 0 else "持平，差值0.0"
        headline = (
            f"过去{len(dates)}个首板日，候选1进2晋级率{candidate_rate:.1%}（{candidate_k}/{candidate_n}），"
            f"同期全部首板{market_rate:.1%}（{market_k}/{market_n}），{relation}个百分点；"
            f"可比较的{len(ready)}天中有{outperform}天领先，{best.trade_date:%m月%d日}相对市场表现最好。"
        )
    else:
        headline = f"过去{len(dates)}个首板日共有{count}个候选，暂无可同时核验候选与全市场1进2结果的日期。"
    return DigestOverview(
        headline=headline, candidate_count=count, excellent_count=excellent, weak_count=weak,
        ordinary_count=ordinary, unobserved_count=missing, comparable_days=len(ready),
        outperform_days=outperform, best_date=best.trade_date if best else None,
        candidate_promoted=candidate_k, candidate_promotion_total=candidate_n,
        candidate_promotion_rate=candidate_rate, market_promoted=market_k,
        market_promotion_total=market_n, market_promotion_rate=market_rate, advantage_pp=advantage,
    )
