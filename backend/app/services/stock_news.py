"""Collect and cache stock-specific news from structured providers."""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.collectors.network import without_proxy
from app.models import StockNewsFacts, StockNewsItem
from app.repositories.stock_news_repository import SQLiteStockNewsRepository


StockNewsLoader = Callable[[str, str, datetime], list[StockNewsItem]]
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CACHE_TTL = timedelta(minutes=10)
_EASTMONEY_SOURCE = "东方财富个股资讯"


def collect_stock_news(
    *,
    symbol: str,
    name: str,
    days: int = 7,
    limit: int = 10,
    repository: SQLiteStockNewsRepository | None = None,
    loaders: Mapping[str, StockNewsLoader] | None = None,
    now: datetime | None = None,
) -> StockNewsFacts:
    """Return recent, deduplicated news for one resolved A-share stock."""

    normalized_symbol = symbol.strip()
    normalized_name = name.strip() or normalized_symbol
    if len(normalized_symbol) != 6 or not normalized_symbol.isdigit():
        raise ValueError("stock news requires a six-digit A-share symbol")
    bounded_days = max(1, min(int(days), 30))
    bounded_limit = max(1, min(int(limit), 20))
    current_time = _as_shanghai_time(now or datetime.now(_SHANGHAI))
    published_since = current_time - timedelta(days=bounded_days)
    active_repository = repository or SQLiteStockNewsRepository()
    available = loaders or {_EASTMONEY_SOURCE: _load_eastmoney_stock_news}

    if loaders is None and all(
        _is_fresh(
            active_repository.last_success_at(
                symbol=normalized_symbol,
                source=source,
            ),
            current_time,
        )
        for source in available
    ):
        cached = active_repository.list_items(
            symbol=normalized_symbol,
            published_since=published_since,
            limit=bounded_limit * 3,
        )
        return _facts(
            symbol=normalized_symbol,
            name=normalized_name,
            now=current_time,
            days=bounded_days,
            limit=bounded_limit,
            cache_status="cached",
            items=cached,
            sources=list(available),
            errors=[],
        )

    successful_sources: list[str] = []
    errors: list[str] = []
    for source, loader in available.items():
        try:
            items = _deduplicate_items(
                loader(normalized_symbol, normalized_name, current_time)
            )
            active_repository.upsert_items(items)
            active_repository.record_sync(
                symbol=normalized_symbol,
                source=source,
                attempted_at=current_time,
                error_message=None,
            )
            successful_sources.append(source)
        except Exception as error:  # noqa: BLE001
            message = _clean_text(error, limit=240) or error.__class__.__name__
            errors.append(f"{source}: {message}")
            active_repository.record_sync(
                symbol=normalized_symbol,
                source=source,
                attempted_at=current_time,
                error_message=message,
            )

    cached = active_repository.list_items(
        symbol=normalized_symbol,
        published_since=published_since,
        limit=bounded_limit * 3,
    )
    return _facts(
        symbol=normalized_symbol,
        name=normalized_name,
        now=current_time,
        days=bounded_days,
        limit=bounded_limit,
        cache_status="live" if successful_sources else "stale",
        items=cached,
        sources=successful_sources or list(available),
        errors=errors,
    )


def _load_eastmoney_stock_news(
    symbol: str,
    name: str,
    fetched_at: datetime,
) -> list[StockNewsItem]:
    """Load Eastmoney's stock-search news through the installed AKShare adapter."""

    import akshare as ak

    with without_proxy():
        frame = ak.stock_news_em(symbol=symbol)
    items: list[StockNewsItem] = []
    for row in frame.to_dict(orient="records"):
        title = _clean_text(row.get("新闻标题"), limit=240)
        summary = _clean_text(row.get("新闻内容"), limit=800)
        published_at = _parse_time(row.get("发布时间"))
        source = _clean_text(row.get("文章来源"), limit=80) or "东方财富"
        url = str(row.get("新闻链接") or "").strip()
        if not title or published_at is None or not _valid_url(url):
            continue
        relevance = _relevance_score(
            symbol=symbol,
            name=name,
            title=title,
            summary=summary,
        )
        # Symbol-only mentions inside broad market tables are usually noise.
        if relevance < 0.6:
            continue
        items.append(
            StockNewsItem(
                symbol=symbol,
                name=name,
                title=title,
                summary=summary,
                published_at=published_at,
                source=source,
                url=url.replace("http://", "https://", 1),
                item_type=_classify_item(title, summary),
                relevance_score=relevance,
                fetched_at=fetched_at,
            )
        )
    return items


def _facts(
    *,
    symbol: str,
    name: str,
    now: datetime,
    days: int,
    limit: int,
    cache_status: str,
    items: list[StockNewsItem],
    sources: list[str],
    errors: list[str],
) -> StockNewsFacts:
    data_missing = list(errors)
    if not items:
        data_missing.append(f"最近 {days} 天没有获取到与该股票直接相关的新闻。")
    deduplicated = _deduplicate_items(items)
    return StockNewsFacts(
        symbol=symbol,
        name=name,
        fetched_at=now,
        window_days=days,
        cache_status=cache_status,
        sources=list(dict.fromkeys(sources)),
        items=deduplicated[:limit],
        data_missing=list(dict.fromkeys(data_missing)),
    )


def _deduplicate_items(items: list[StockNewsItem]) -> list[StockNewsItem]:
    """Keep the newest copy when providers repeat the same normalized title."""

    ranked = sorted(
        items,
        key=lambda item: (item.published_at, item.relevance_score, len(item.summary)),
        reverse=True,
    )
    selected: list[StockNewsItem] = []
    seen_titles: set[str] = set()
    for item in ranked:
        title_key = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", item.title.lower())
        if not title_key or title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        selected.append(item)
    return selected


def _is_fresh(last_success_at: datetime | None, now: datetime) -> bool:
    if last_success_at is None:
        return False
    return now - _as_shanghai_time(last_success_at) <= _CACHE_TTL


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _as_shanghai_time(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _as_shanghai_time(parsed)


def _as_shanghai_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_SHANGHAI)
    return value.astimezone(_SHANGHAI)


def _clean_text(value: object, *, limit: int) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())[:limit]


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _relevance_score(*, symbol: str, name: str, title: str, summary: str) -> float:
    title_lower = title.lower()
    combined = f"{title} {summary}".lower()
    score = 0.0
    if symbol in title_lower:
        score += 0.6
    elif symbol in combined:
        score += 0.4
    if name and name != symbol and name.lower() in title_lower:
        score += 0.6
    elif name and name != symbol and name.lower() in combined:
        score += 0.4
    return min(1.0, round(score, 2))


def _classify_item(title: str, summary: str) -> str:
    combined = f"{title} {summary}"
    categories = (
        ("regulatory", ("监管", "问询函", "立案", "处罚", "警示函")),
        ("announcement_report", ("公告", "年度报告", "中期报告", "中报", "季报", "业绩快报")),
        ("research", ("研报", "券商", "机构调研", "评级")),
    )
    for category, terms in categories:
        if any(term in combined for term in terms):
            return category
    return "news"
