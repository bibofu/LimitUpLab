"""Aggregate recent financial news from structured Chinese market feeds."""

from __future__ import annotations

import html
import os
import re
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from app.models import FinanceNewsFacts, FinanceNewsItem


NewsLoader = Callable[[], list[FinanceNewsItem]]
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CACHE_TTL = timedelta(minutes=5)
_cache: dict[tuple[str, int, int], tuple[datetime, FinanceNewsFacts]] = {}
_cache_lock = threading.Lock()
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
)

_CATEGORY_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "A股",
        (
            "a股",
            "沪深",
            "上证",
            "深证",
            "创业板",
            "科创板",
            "北交所",
            "证监会",
            "交易所",
            "涨停",
            "上市公司",
        ),
    ),
    (
        "宏观",
        (
            "国务院",
            "央行",
            "财政部",
            "金融监管总局",
            "人民币",
            "利率",
            "lpr",
            "降准",
            "降息",
            "社融",
            "cpi",
            "pmi",
            "关税",
        ),
    ),
    (
        "产业",
        (
            "半导体",
            "芯片",
            "人工智能",
            "ai",
            "算力",
            "机器人",
            "新能源",
            "光伏",
            "储能",
            "医药",
            "军工",
            "汽车",
            "锂",
            "稀土",
        ),
    ),
    (
        "海外市场",
        ("港股", "美股", "纳斯达克", "道指", "美联储", "欧洲股市", "日经"),
    ),
    (
        "大宗商品",
        ("黄金", "白银", "原油", "期货", "有色", "铜价", "油价"),
    ),
)
_NOISE_TERMS = ("体育", "天气", "旅游攻略", "娱乐", "明星", "校园", "进课堂")


def collect_finance_news(
    query: str | None = None,
    limit: int = 8,
    hours: int = 48,
    *,
    loaders: Mapping[str, NewsLoader] | None = None,
    now: datetime | None = None,
) -> FinanceNewsFacts:
    """Collect, deduplicate and rank recent market-relevant financial news."""

    normalized_query = " ".join((query or "").split())[:100] or None
    bounded_limit = max(1, min(int(limit), 12))
    bounded_hours = max(1, min(int(hours), 168))
    current_time = _as_shanghai_time(now or datetime.now(_SHANGHAI))
    cache_key = (normalized_query or "", bounded_limit, bounded_hours)
    if loaders is None:
        with _cache_lock:
            cached = _cache.get(cache_key)
            if cached and current_time - cached[0] < _CACHE_TTL:
                return cached[1]

    available = loaders or {
        "东方财富": _load_eastmoney,
        "同花顺": _load_tonghuashun,
    }
    collected: list[FinanceNewsItem] = []
    successful_sources: list[str] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(available)) as executor:
        futures = {
            executor.submit(loader): source
            for source, loader in available.items()
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                items = future.result()
                if not items:
                    raise ValueError("source returned no news")
                collected.extend(items)
                successful_sources.append(source)
            except Exception as error:  # noqa: BLE001
                errors.append(f"{source}: {error}")

    if not collected:
        raise RuntimeError("finance news collection failed: " + "; ".join(errors))

    earliest = current_time - timedelta(hours=bounded_hours)
    recent = [
        item
        for item in collected
        if earliest <= _as_shanghai_time(item.published_at) <= current_time + timedelta(minutes=5)
    ]
    ranked = _rank_and_deduplicate(
        recent,
        query=normalized_query,
        now=current_time,
    )
    facts = FinanceNewsFacts(
        query=normalized_query,
        fetched_at=current_time,
        window_hours=bounded_hours,
        sources=successful_sources,
        items=ranked[:bounded_limit],
    )
    if loaders is None:
        with _cache_lock:
            _cache[cache_key] = (current_time, facts)
    return facts


def _load_eastmoney() -> list[FinanceNewsItem]:
    with _direct_session() as session:
        response = session.get(
            "https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
            params={
                "client": "web",
                "biz": "web_724",
                "fastColumn": "102",
                "sortEnd": "",
                "pageSize": "200",
                "req_trace": str(int(datetime.now().timestamp() * 1000)),
            },
            headers={
                "User-Agent": _USER_AGENT,
                "Referer": "https://kuaixun.eastmoney.com/",
            },
            timeout=_timeout_seconds(),
        )
    response.raise_for_status()
    rows = response.json().get("data", {}).get("fastNewsList", [])
    items: list[FinanceNewsItem] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        url = (
            code
            if urlparse(code).scheme in {"http", "https"}
            else f"https://finance.eastmoney.com/a/{code}.html"
        )
        item = _news_item(
            title=row.get("title"),
            summary=row.get("summary"),
            published_at=row.get("showTime"),
            source="东方财富",
            url=url,
        )
        if item is not None:
            items.append(item)
    return items


def _load_tonghuashun() -> list[FinanceNewsItem]:
    with _direct_session() as session:
        response = session.get(
            "https://news.10jqka.com.cn/tapp/news/push/stock",
            params={"page": "1", "tag": "", "track": "website"},
            headers={
                "User-Agent": _USER_AGENT,
                "Referer": "https://news.10jqka.com.cn/realtimenews.html",
            },
            timeout=_timeout_seconds(),
        )
    response.raise_for_status()
    rows = response.json().get("data", {}).get("list", [])
    items: list[FinanceNewsItem] = []
    for row in rows:
        item = _news_item(
            title=row.get("title"),
            summary=row.get("digest"),
            published_at=row.get("rtime"),
            source="同花顺",
            url=urljoin("https://news.10jqka.com.cn/", str(row.get("url") or "")),
        )
        if item is not None:
            items.append(item)
    return items


def _news_item(
    *,
    title: object,
    summary: object,
    published_at: object,
    source: str,
    url: str,
) -> FinanceNewsItem | None:
    clean_title = _clean_text(title, limit=220)
    clean_summary = _clean_text(summary, limit=700)
    if not clean_title:
        clean_title = clean_summary[:80]
    parsed_time = _parse_published_at(published_at)
    parsed_url = urlparse(url)
    if not clean_title or parsed_time is None or parsed_url.scheme not in {"http", "https"}:
        return None
    combined = f"{clean_title} {clean_summary}".lower()
    category, relevance = _classify(combined)
    return FinanceNewsItem(
        title=clean_title,
        summary=clean_summary,
        published_at=parsed_time,
        source=source,
        url=url[:1000],
        category=category,
        relevance_score=relevance,
    )


def _rank_and_deduplicate(
    items: list[FinanceNewsItem],
    *,
    query: str | None,
    now: datetime,
) -> list[FinanceNewsItem]:
    selected: dict[str, FinanceNewsItem] = {}
    query_terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query or "")]
    for item in items:
        combined = f"{item.title} {item.summary}".lower()
        if any(term in combined for term in _NOISE_TERMS) and item.relevance_score <= 0:
            continue
        age_hours = max(0.0, (now - _as_shanghai_time(item.published_at)).total_seconds() / 3600)
        query_bonus = sum(4.0 for term in query_terms if len(term) > 1 and term in combined)
        score = item.relevance_score + max(0.0, 6.0 - age_hours / 6.0) + query_bonus
        candidate = item.model_copy(update={"relevance_score": round(score, 2)})
        key = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", item.title.lower())
        previous = selected.get(key)
        if previous is None or (
            len(candidate.summary), candidate.published_at
        ) > (len(previous.summary), previous.published_at):
            selected[key] = candidate
    return sorted(
        selected.values(),
        key=lambda item: (item.relevance_score, item.published_at),
        reverse=True,
    )


def _classify(text: str) -> tuple[str, float]:
    best_category = "公司"
    best_hits = 0
    total_hits = 0
    for category, terms in _CATEGORY_TERMS:
        hits = sum(1 for term in terms if term in text)
        total_hits += hits
        if hits > best_hits:
            best_category = category
            best_hits = hits
    if not best_hits and not any(term in text for term in ("公司", "股份", "集团", "证券", "银行")):
        best_category = "其他"
    noise_penalty = 4 if any(term in text for term in _NOISE_TERMS) else 0
    return best_category, float(total_hits * 2 - noise_penalty)


def _parse_published_at(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _as_shanghai_time(value)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=_SHANGHAI)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_shanghai_time(parsed)


def _as_shanghai_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_SHANGHAI)
    return value.astimezone(_SHANGHAI)


def _clean_text(value: object, *, limit: int) -> str:
    raw = BeautifulSoup(html.unescape(str(value or "")), "html.parser").get_text(" ")
    return " ".join(raw.split())[:limit]


def _timeout_seconds() -> float:
    raw = os.getenv("LIMITUPLAB_FINANCE_NEWS_TIMEOUT_SECONDS", "8").strip()
    try:
        timeout = float(raw)
    except ValueError:
        return 8.0
    return max(3.0, min(timeout, 20.0))


def _direct_session() -> requests.Session:
    """Create a client isolated from stale process-level proxy variables."""

    session = requests.Session()
    session.trust_env = False
    return session
