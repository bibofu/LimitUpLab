"""Sanitized generic web search with no-key provider fallbacks."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from app.collectors.network import without_proxy
from app.models import WebSearchFacts, WebSearchResult


SearchLoader = Callable[[str, int], list[WebSearchResult]]
_CACHE_TTL = timedelta(minutes=10)
_cache: dict[tuple[str, int], tuple[datetime, WebSearchFacts]] = {}
_cache_lock = threading.Lock()
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
)


def search_web(
    query: str,
    limit: int = 5,
    *,
    loaders: dict[str, SearchLoader] | None = None,
) -> WebSearchFacts:
    """Search the public web and return compact, untrusted evidence snippets."""

    normalized_query = " ".join(query.split())[:300]
    if not normalized_query:
        raise ValueError("web search query is required")
    bounded_limit = max(1, min(int(limit), 8))
    cache_key = (normalized_query, bounded_limit)
    now = datetime.now(timezone.utc)
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]

    available = loaders or {
        "so-html": _search_so,
        "so-news": _search_so_news,
        "bing-html": _search_bing,
        "duckduckgo-html": _search_duckduckgo,
    }
    configured = os.getenv("LIMITUPLAB_WEB_SEARCH_PROVIDER", "auto").strip().lower()
    if configured == "auto":
        provider_order = list(available)
        if _looks_like_news_query(normalized_query) and "so-news" in provider_order:
            provider_order.remove("so-news")
            provider_order.insert(0, "so-news")
    else:
        provider_order = [configured]
    errors: list[str] = []
    for provider in provider_order:
        loader = available.get(provider)
        if loader is None:
            errors.append(f"unsupported provider: {provider}")
            continue
        try:
            results = loader(normalized_query, bounded_limit)
            if not results:
                raise ValueError("provider returned no search results")
            facts = WebSearchFacts(
                query=normalized_query,
                fetched_at=now,
                provider=provider,
                results=results[:bounded_limit],
            )
            with _cache_lock:
                _cache[cache_key] = (now, facts)
            return facts
        except Exception as error:  # noqa: BLE001
            errors.append(f"{provider}: {error}")
    raise RuntimeError("web search failed: " + "; ".join(errors))


def _search_bing(query: str, limit: int) -> list[WebSearchResult]:
    with without_proxy():
        response = requests.get(
            "https://cn.bing.com/search",
            params={"q": query, "count": limit, "cc": "cn"},
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=_timeout_seconds(),
        )
        response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results: list[WebSearchResult] = []
    for item in soup.select("li.b_algo"):
        anchor = item.select_one("h2 a")
        if anchor is None:
            continue
        snippet_node = item.select_one(".b_caption p") or item.select_one("p")
        result = _result(
            title=anchor.get_text(" ", strip=True),
            url=str(anchor.get("href") or ""),
            snippet=snippet_node.get_text(" ", strip=True) if snippet_node else "",
        )
        if result is not None:
            results.append(result)
        if len(results) >= limit:
            break
    return results


def _search_so(query: str, limit: int) -> list[WebSearchResult]:
    with without_proxy():
        response = requests.get(
            "https://www.so.com/s",
            params={"q": query},
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=_timeout_seconds(),
        )
        response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results: list[WebSearchResult] = []
    for item in soup.select("li.res-list"):
        anchor = item.select_one("h3 a")
        if anchor is None:
            continue
        snippet_node = (
            item.select_one(".res-desc")
            or item.select_one(".summary")
            or item.select_one("p")
        )
        url = str(anchor.get("data-mdurl") or anchor.get("href") or "")
        result = _result(
            title=anchor.get_text(" ", strip=True),
            url=url,
            snippet=snippet_node.get_text(" ", strip=True) if snippet_node else "",
        )
        if result is not None:
            results.append(result)
        if len(results) >= limit:
            break
    return results


def _search_so_news(query: str, limit: int) -> list[WebSearchResult]:
    with without_proxy():
        response = requests.get(
            "https://news.so.com/ns",
            params={"q": query, "tn": "news"},
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=_timeout_seconds(),
        )
        response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results: list[WebSearchResult] = []
    for heading in soup.select("h3.g-title"):
        anchor = heading.find_parent("a")
        if anchor is None:
            continue
        container = anchor.find_parent(["li", "div"])
        snippet_node = (
            anchor.select_one(".summary")
            or (container.select_one(".summary") if container else None)
        )
        result = _result(
            title=str(anchor.get("title") or heading.get_text(" ", strip=True)),
            url=str(anchor.get("href") or ""),
            snippet=snippet_node.get_text(" ", strip=True) if snippet_node else "",
        )
        if result is not None:
            results.append(result)
        if len(results) >= limit:
            break
    return results


def _search_duckduckgo(query: str, limit: int) -> list[WebSearchResult]:
    with without_proxy():
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=_timeout_seconds(),
        )
        response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results: list[WebSearchResult] = []
    for item in soup.select(".result"):
        anchor = item.select_one("a.result__a")
        if anchor is None:
            continue
        snippet_node = item.select_one(".result__snippet")
        url = _unwrap_duckduckgo_url(str(anchor.get("href") or ""))
        result = _result(
            title=anchor.get_text(" ", strip=True),
            url=url,
            snippet=snippet_node.get_text(" ", strip=True) if snippet_node else "",
        )
        if result is not None:
            results.append(result)
        if len(results) >= limit:
            break
    return results


def _result(title: str, url: str, snippet: str) -> WebSearchResult | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    clean_title = " ".join(title.split())[:200]
    if not clean_title:
        return None
    return WebSearchResult(
        title=clean_title,
        url=url[:1000],
        domain=parsed.netloc.lower().removeprefix("www.")[:200],
        snippet=" ".join(snippet.split())[:500],
    )


def _unwrap_duckduckgo_url(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" not in parsed.netloc:
        return url
    target = parse_qs(parsed.query).get("uddg")
    return target[0] if target else url


def _timeout_seconds() -> float:
    raw = os.getenv("LIMITUPLAB_WEB_SEARCH_TIMEOUT_SECONDS", "12").strip()
    try:
        timeout = float(raw)
    except ValueError:
        return 12.0
    return max(3.0, min(timeout, 30.0))


def _looks_like_news_query(query: str) -> bool:
    return any(
        term in query
        for term in (
            "新闻",
            "消息",
            "资讯",
            "公告",
            "政策",
            "研报",
            "异动",
            "原因",
            "最新",
            "今天",
            "今日",
        )
    )
