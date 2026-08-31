"""Market overview and post-close special-data API routes."""

import re
from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.collectors import (
    HithinkFinanceError,
    collect_market_indices,
)
from app.models import (
    DragonTigerReviewResponse,
    FinanceNewsItem,
    FinanceNewsPage,
    MarketSummary,
)
from app.repositories import get_limit_up_repository
from app.services.analysis import latest_trade_date, summarize_market
from app.services.dragon_tiger_review import load_dragon_tiger_review
from app.services.finance_news import collect_finance_news

router = APIRouter()

_DOMESTIC_MARKET_OVERRIDE_TERMS = (
    "a股",
    "沪深",
    "上证",
    "深证",
    "创业板",
    "科创板",
    "北交所",
    "证监会",
    "人民币",
    "沪交所",
    "深交所",
    "上市公司",
)
_ALLOWED_OVERSEAS_MARKET_TERMS = (
    "美股",
    "纳斯达克",
    "纳指",
    "道琼斯",
    "道指",
    "标普500",
    "标普 500",
    "纽交所",
    "纽约证券交易所",
    "中概股",
    "港股",
    "香港股市",
    "恒生指数",
    "恒生科技",
    "恒指",
    "港交所",
    "香港交易所",
    "英伟达",
    "特斯拉",
    "苹果公司",
    "微软",
    "亚马逊",
    "谷歌",
    "meta",
    "amd",
    "高通",
    "博通",
    "美光",
)
_FOREIGN_NEWS_TERMS = (
    "美国",
    "美联储",
    "特朗普",
    "欧洲",
    "欧盟",
    "英国",
    "法国",
    "德国",
    "意大利",
    "西班牙",
    "荷兰",
    "瑞典",
    "瑞士",
    "俄罗斯",
    "乌克兰",
    "日本",
    "韩国",
    "朝鲜",
    "印度",
    "巴基斯坦",
    "伊朗",
    "以色列",
    "土耳其",
    "沙特",
    "阿联酋",
    "加拿大",
    "澳大利亚",
    "巴西",
    "墨西哥",
    "印尼",
    "印度尼西亚",
    "越南",
    "泰国",
    "新加坡",
    "马来西亚",
    "菲律宾",
    "尼泊尔",
    "孟加拉国",
    "斯里兰卡",
    "阿富汗",
    "哈萨克斯坦",
    "乌兹别克斯坦",
    "埃及",
    "南非",
    "尼日利亚",
    "肯尼亚",
    "波兰",
    "比利时",
    "奥地利",
    "丹麦",
    "挪威",
    "芬兰",
    "希腊",
    "葡萄牙",
    "匈牙利",
    "塞尔维亚",
    "新西兰",
    "伊拉克",
    "叙利亚",
    "黎巴嫩",
    "卡塔尔",
    "科威特",
    "国际金价",
    "伦敦金",
    "纽约金",
    "布伦特原油",
)


@router.get("/news", response_model=FinanceNewsPage)
def get_finance_news(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=5, le=20),
) -> FinanceNewsPage:
    """Return one page of the latest 24-hour structured market-news feed."""

    try:
        facts = collect_finance_news(limit=2000, hours=24)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail="财经快讯数据源暂不可用，请稍后重试。",
        ) from exc
    ordered = sorted(
        (item for item in facts.items if _include_market_news(item)),
        key=lambda item: item.published_at,
        reverse=True,
    )
    total = len(ordered)
    total_pages = max(1, (total + page_size - 1) // page_size)
    bounded_page = min(page, total_pages)
    start = (bounded_page - 1) * page_size
    return FinanceNewsPage(
        fetched_at=facts.fetched_at,
        sources=facts.sources,
        page=bounded_page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        items=ordered[start : start + page_size],
    )


def _include_market_news(item: FinanceNewsItem) -> bool:
    """Keep domestic news while limiting overseas noise to US and HK equities."""

    text = f"{item.title} {item.summary}".lower()
    if any(term in text for term in _ALLOWED_OVERSEAS_MARKET_TERMS):
        return True
    if re.search(r"(?:nasdaq|nyse|aapl|nvda|tsla|\.hk\b|\d{4,5}\.hk)", text):
        return True
    if re.search(r"\b[a-z0-9\u4e00-\u9fff]+-(?:w|sw|b)(?:\b|：|:)", text):
        return True
    has_foreign_context = any(term in text for term in _FOREIGN_NEWS_TERMS)
    if not has_foreign_context:
        return True
    return any(term in text for term in _DOMESTIC_MARKET_OVERRIDE_TERMS)


@router.get("/summary", response_model=MarketSummary)
def get_market_summary() -> MarketSummary:
    """Return the latest objective market summary with index snapshots."""

    events = get_limit_up_repository().list_events()
    trade_date = latest_trade_date(events)
    try:
        indices = collect_market_indices(trade_date)
    except Exception:
        indices = []
    return summarize_market(
        events,
        indices=indices,
    )


@router.get("/overview", response_model=MarketSummary)
def get_market_overview() -> MarketSummary:
    """Return the dashboard overview payload."""

    events = get_limit_up_repository().list_events()
    trade_date = latest_trade_date(events)
    try:
        indices = collect_market_indices(trade_date)
    except Exception:
        indices = []
    return summarize_market(
        events,
        indices=indices,
    )


@router.get("/dragon-tiger", response_model=DragonTigerReviewResponse)
def get_dragon_tiger_review(
    trade_date: date | None = None,
) -> DragonTigerReviewResponse:
    """Return a deduplicated Tonghuashun Dragon-Tiger list for review."""

    events = get_limit_up_repository().list_events()
    target_date = trade_date or latest_trade_date(events)
    try:
        return load_dragon_tiger_review(
            events,
            trade_date=target_date,
        )
    except HithinkFinanceError as exc:
        raise HTTPException(
            status_code=502,
            detail="龙虎榜数据源暂不可用，请稍后重试。",
        ) from exc
