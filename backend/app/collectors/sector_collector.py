"""Industry and concept data with the Tonghuashun CLI preferred over fallbacks."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Any

import akshare as ak

from app.collectors.hithink_finance_collector import (
    HithinkFinanceCollector,
    HithinkIndexCatalogFact,
    HithinkIndexSnapshotFact,
)
from app.collectors.network import without_proxy
from app.collectors.process_timeout import run_in_killable_process


@dataclass(frozen=True)
class SectorSpotRow:
    """Normalized latest industry-sector ranking row."""

    sector_name: str
    rank: int
    change_pct: float
    amount_yi: float | None
    net_inflow_yi: float | None
    up_count: int | None
    down_count: int | None
    leader_name: str | None
    leader_price: float | None
    leader_change_pct: float | None
    source: str


@dataclass(frozen=True)
class SectorDailyRow:
    """Normalized completed daily sector-index row."""

    trade_date: date
    close: float
    change_pct: float | None
    source: str


_SPOT_CACHE_TTL = timedelta(minutes=5)
_HISTORY_CACHE_TTL = timedelta(minutes=15)
_CATALOG_CACHE_TTL = timedelta(hours=24)
_CLI_SNAPSHOT_TIMEOUT_SECONDS = 60
_CLI_BATCH_SIZE = 100
_CLI_MAX_WORKERS = 4
_INDUSTRY_SUPPLEMENT_TIMEOUT_SECONDS = 5
_catalog_cache: dict[str, tuple[datetime, list[HithinkIndexCatalogFact]]] = {}
_spot_cache: tuple[datetime, list[SectorSpotRow]] | None = None
_concept_spot_cache: tuple[datetime, list[SectorSpotRow]] | None = None
_history_cache: dict[
    tuple[str, date, date], tuple[datetime, list[SectorDailyRow]]
] = {}
_concept_history_cache: dict[
    tuple[str, date, date], tuple[datetime, list[SectorDailyRow]]
] = {}
_cache_lock = threading.Lock()


def collect_sector_spot() -> list[SectorSpotRow]:
    """Prefer CLI industry ranking, then AKShare THS and Eastmoney."""

    global _spot_cache
    now = datetime.now()
    with _cache_lock:
        if _spot_cache and now - _spot_cache[0] < _SPOT_CACHE_TTL:
            return list(_spot_cache[1])

    errors: list[str] = []
    for source, loader, normalizer in (
        (
            "hithink-finance.index.snapshot.industry",
            lambda: _load_hithink_spot("industry"),
            lambda rows, _source: rows,
        ),
        ("akshare-ths-industry-summary", _load_ths_spot, _normalize_ths_spot),
        ("akshare-eastmoney-industry-spot", _load_eastmoney_spot, _normalize_em_spot),
    ):
        try:
            rows = normalizer(loader(), source)
            if not rows:
                raise ValueError("provider returned no sector rows")
            with _cache_lock:
                _spot_cache = (now, rows)
            return list(rows)
        except Exception as error:  # noqa: BLE001
            errors.append(f"{source}: {error}")
    raise RuntimeError("unable to collect sector spot data: " + "; ".join(errors))


def collect_sector_history(
    sector_name: str,
    start_date: date,
    end_date: date,
) -> list[SectorDailyRow]:
    """Collect completed sector daily history with a short in-process cache."""

    cache_key = (sector_name, start_date, end_date)
    now = datetime.now()
    with _cache_lock:
        cached = _history_cache.get(cache_key)
        if cached and now - cached[0] < _HISTORY_CACHE_TTL:
            return list(cached[1])

    errors: list[str] = []
    providers = (
        (
            "hithink-finance.index.history.industry",
            lambda name, start, end: _load_hithink_history("industry", name, start, end),
            lambda rows, _source: rows,
        ),
        (
            "akshare-ths-industry-history",
            _load_ths_history,
            _normalize_ths_history,
        ),
        (
            "akshare-eastmoney-industry-history",
            _load_eastmoney_history,
            _normalize_em_history,
        ),
    )
    for source, loader, normalizer in providers:
        try:
            frame = loader(sector_name, start_date, end_date)
            rows = normalizer(frame, source)
            if not rows:
                raise ValueError("provider returned no history rows")
            with _cache_lock:
                _history_cache[cache_key] = (now, rows)
            return list(rows)
        except Exception as error:  # noqa: BLE001
            errors.append(f"{source}: {error}")
    raise RuntimeError(
        f"unable to collect sector history for {sector_name}: " + "; ".join(errors)
    )


def collect_concept_spot() -> list[SectorSpotRow]:
    """Prefer the complete CLI concept ranking, with Eastmoney fallback."""

    global _concept_spot_cache
    now = datetime.now()
    with _cache_lock:
        if (
            _concept_spot_cache
            and now - _concept_spot_cache[0] < _SPOT_CACHE_TTL
        ):
            return list(_concept_spot_cache[1])

    errors: list[str] = []
    for source, loader in (
        ("hithink-finance.index.snapshot.cn_concept", lambda: _load_hithink_spot("cn_concept")),
        (
            "akshare-eastmoney-concept-spot",
            lambda: _normalize_em_spot(
                _load_eastmoney_concept_spot(), "akshare-eastmoney-concept-spot"
            ),
        ),
    ):
        try:
            rows = loader()
            if not rows:
                raise ValueError("provider returned no concept rows")
            with _cache_lock:
                _concept_spot_cache = (now, rows)
            return list(rows)
        except Exception as error:  # noqa: BLE001
            errors.append(f"{source}: {error}")
    raise RuntimeError("unable to collect concept spot data: " + "; ".join(errors))


def collect_concept_history(
    concept_name: str,
    start_date: date,
    end_date: date,
) -> list[SectorDailyRow]:
    """Collect completed concept-index history with provider fallback."""

    cache_key = (concept_name, start_date, end_date)
    now = datetime.now()
    with _cache_lock:
        cached = _concept_history_cache.get(cache_key)
        if cached and now - cached[0] < _HISTORY_CACHE_TTL:
            return list(cached[1])

    errors: list[str] = []
    providers = (
        (
            "hithink-finance.index.history.cn_concept",
            lambda name, start, end: _load_hithink_history("cn_concept", name, start, end),
            lambda rows, _source: rows,
        ),
        (
            "akshare-eastmoney-concept-history",
            _load_eastmoney_concept_history,
            _normalize_em_history,
        ),
        (
            "akshare-ths-concept-history",
            _load_ths_concept_history,
            _normalize_ths_history,
        ),
    )
    for source, loader, normalizer in providers:
        try:
            frame = loader(concept_name, start_date, end_date)
            rows = normalizer(frame, source)
            if not rows:
                raise ValueError("provider returned no concept history rows")
            with _cache_lock:
                _concept_history_cache[cache_key] = (now, rows)
            return list(rows)
        except Exception as error:  # noqa: BLE001
            errors.append(f"{source}: {error}")
    raise RuntimeError(
        f"unable to collect concept history for {concept_name}: "
        + "; ".join(errors)
    )


def _hithink_catalog(
    collector: HithinkFinanceCollector, category: str
) -> list[HithinkIndexCatalogFact]:
    now = datetime.now()
    with _cache_lock:
        cached = _catalog_cache.get(category)
        if cached and now - cached[0] < _CATALOG_CACHE_TTL:
            return list(cached[1])
    indexes = collector.collect_index_catalog(category)
    if not indexes or len({item.thscode for item in indexes}) != len(indexes):
        raise ValueError("index catalog is empty or has duplicate codes")
    with _cache_lock:
        _catalog_cache[category] = (now, indexes)
    return list(indexes)


def _hithink_snapshots(
    collector: HithinkFinanceCollector, indexes: list[HithinkIndexCatalogFact]
) -> list[HithinkIndexSnapshotFact]:
    """Bound concurrent batches and reject partial rankings instead of truncating."""

    executor = ThreadPoolExecutor(max_workers=_CLI_MAX_WORKERS)
    futures = [
        executor.submit(collector.collect_index_snapshots, indexes[offset : offset + _CLI_BATCH_SIZE])
        for offset in range(0, len(indexes), _CLI_BATCH_SIZE)
    ]
    rows: list[HithinkIndexSnapshotFact] = []
    try:
        for future in as_completed(futures, timeout=_snapshot_budget_seconds()):
            rows.extend(future.result())
    finally:
        # Running calls have their own finite timeout; pending batches are cancelled on failure.
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
    expected = {item.thscode for item in indexes}
    actual = {item.thscode for item in rows}
    if actual != expected or len(rows) != len(indexes):
        raise ValueError(f"incomplete index ranking: expected {len(expected)}, got {len(actual)}")
    if any(item.change_pct is None or not isfinite(item.change_pct) for item in rows):
        raise ValueError("index ranking contains missing percentage changes")
    return rows


def _snapshot_budget_seconds() -> float:
    raw = os.getenv("LIMITUPLAB_SECTOR_SNAPSHOT_TIMEOUT_SECONDS", str(_CLI_SNAPSHOT_TIMEOUT_SECONDS))
    try:
        value = float(raw)
        if isfinite(value) and value > 0:
            return min(value, 300)
    except ValueError:
        pass
    return _CLI_SNAPSHOT_TIMEOUT_SECONDS


def _load_hithink_spot(category: str) -> list[SectorSpotRow]:
    collector = HithinkFinanceCollector()
    indexes = _hithink_catalog(collector, category)
    snapshots = _hithink_snapshots(collector, indexes)
    ordered = sorted(snapshots, key=lambda item: (-float(item.change_pct), item.name))
    rows = [
        SectorSpotRow(
            sector_name=item.name,
            rank=rank,
            change_pct=float(item.change_pct),
            amount_yi=item.turnover / 100_000_000 if item.turnover is not None else None,
            net_inflow_yi=None,
            up_count=None,
            down_count=None,
            leader_name=None,
            leader_price=None,
            leader_change_pct=None,
            source=f"hithink-finance.index.snapshot.{category}",
        )
        for rank, item in enumerate(ordered, 1)
    ]
    if category != "industry":
        # Eastmoney concepts may have different constituents even when names match.
        return rows
    try:
        # Optional breadth must not keep a successful CLI ranking waiting on an
        # upstream request with no network timeout. The top-level loader is spawn-safe.
        supplement_frame = run_in_killable_process(
            _load_ths_spot, timeout_seconds=_INDUSTRY_SUPPLEMENT_TIMEOUT_SECONDS,
        )
        supplements = {
            item.sector_name: item
            for item in _normalize_ths_spot(supplement_frame, "akshare-ths-industry-summary")
        }
    except Exception:  # noqa: BLE001
        return rows
    enriched: list[SectorSpotRow] = []
    for row in rows:
        supplement = supplements.get(row.sector_name)
        if supplement is None:
            enriched.append(row)
            continue
        enriched.append(replace(
            row,
            amount_yi=row.amount_yi if row.amount_yi is not None else supplement.amount_yi,
            net_inflow_yi=supplement.net_inflow_yi,
            up_count=supplement.up_count,
            down_count=supplement.down_count,
            leader_name=supplement.leader_name,
            leader_price=supplement.leader_price,
            leader_change_pct=supplement.leader_change_pct,
            source=f"{row.source}+{supplement.source}",
        ))
    return enriched


def _load_hithink_history(
    category: str, name: str, start_date: date, end_date: date
) -> list[SectorDailyRow]:
    collector = HithinkFinanceCollector()
    matches = [item for item in _hithink_catalog(collector, category) if item.name == name]
    if len(matches) != 1:
        raise ValueError(f"no unique Tonghuashun index for {category}: {name}")
    history = collector.collect_index_history(
        matches[0].thscode, start_date=start_date - timedelta(days=14), end_date=end_date
    )
    bars = sorted(history.items, key=lambda item: item.trade_date)
    rows: list[SectorDailyRow] = []
    previous: float | None = None
    for bar in bars:
        if start_date <= bar.trade_date <= end_date:
            rows.append(SectorDailyRow(
                trade_date=bar.trade_date,
                close=bar.close,
                change_pct=_change_pct(bar.close, previous),
                source=f"hithink-finance.index.history.{category}",
            ))
        previous = bar.close
    if not rows:
        raise ValueError("CLI index history has no rows in the requested interval")
    return rows


# Fetch the Tonghuashun industry snapshot through the configured market-data library.
def _load_ths_spot():
    with without_proxy():
        return ak.stock_board_industry_summary_ths()


# Fetch the Eastmoney industry snapshot through the configured market-data library.
def _load_eastmoney_spot():
    with without_proxy():
        return ak.stock_board_industry_name_em()


# Fetch the Eastmoney concept-sector snapshot.
def _load_eastmoney_concept_spot():
    with without_proxy():
        return ak.stock_board_concept_name_em()


# Fetch Tonghuashun industry history for the requested date interval.
def _load_ths_history(sector_name: str, start_date: date, end_date: date):
    with without_proxy():
        return ak.stock_board_industry_index_ths(
            symbol=sector_name,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
        )


# Fetch Eastmoney industry history for the requested date interval.
def _load_eastmoney_history(sector_name: str, start_date: date, end_date: date):
    with without_proxy():
        return ak.stock_board_industry_hist_em(
            symbol=sector_name,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            period="日k",
            adjust="",
        )


# Fetch Eastmoney concept history for the requested date interval.
def _load_eastmoney_concept_history(
    concept_name: str,
    start_date: date,
    end_date: date,
):
    with without_proxy():
        return ak.stock_board_concept_hist_em(
            symbol=concept_name,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            period="daily",
            adjust="",
        )


# Fetch Tonghuashun concept history for the requested date interval.
def _load_ths_concept_history(
    concept_name: str,
    start_date: date,
    end_date: date,
):
    with without_proxy():
        return ak.stock_board_concept_index_ths(
            symbol=concept_name,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
        )


# Translate Tonghuashun snapshot columns into the shared sector-row representation.
def _normalize_ths_spot(frame, source: str) -> list[SectorSpotRow]:
    return [
        SectorSpotRow(
            sector_name=str(row.get("板块") or "").strip(),
            rank=_integer(row.get("序号")) or index + 1,
            change_pct=_number(row.get("涨跌幅")) or 0.0,
            amount_yi=_number(row.get("总成交额")),
            net_inflow_yi=_number(row.get("净流入")),
            up_count=_integer(row.get("上涨家数")),
            down_count=_integer(row.get("下跌家数")),
            leader_name=_text(row.get("领涨股")),
            leader_price=_number(row.get("领涨股-最新价")),
            leader_change_pct=_number(row.get("领涨股-涨跌幅")),
            source=source,
        )
        for index, row in enumerate(frame.to_dict("records"))
        if str(row.get("板块") or "").strip()
    ]


# Translate Eastmoney snapshot columns into the shared sector-row representation.
def _normalize_em_spot(frame, source: str) -> list[SectorSpotRow]:
    return [
        SectorSpotRow(
            sector_name=str(row.get("板块名称") or "").strip(),
            rank=_integer(row.get("排名")) or index + 1,
            change_pct=_number(row.get("涨跌幅")) or 0.0,
            amount_yi=None,
            net_inflow_yi=None,
            up_count=_integer(row.get("上涨家数")),
            down_count=_integer(row.get("下跌家数")),
            leader_name=_text(row.get("领涨股票")),
            leader_price=None,
            leader_change_pct=_number(row.get("领涨股票-涨跌幅")),
            source=source,
        )
        for index, row in enumerate(frame.to_dict("records"))
        if str(row.get("板块名称") or "").strip()
    ]


# Translate Tonghuashun history columns into dated sector bars.
def _normalize_ths_history(frame, source: str) -> list[SectorDailyRow]:
    records = frame.to_dict("records")
    closes = [_number(row.get("收盘价")) for row in records]
    rows: list[SectorDailyRow] = []
    for index, row in enumerate(records):
        close = closes[index]
        if close is None:
            continue
        previous = closes[index - 1] if index > 0 else None
        rows.append(
            SectorDailyRow(
                trade_date=_date_value(row.get("日期")),
                close=close,
                change_pct=_change_pct(close, previous),
                source=source,
            )
        )
    # The key compares trade date.
    return sorted(rows, key=lambda item: item.trade_date)


# Translate Eastmoney history columns into dated sector bars.
def _normalize_em_history(frame, source: str) -> list[SectorDailyRow]:
    rows = [
        SectorDailyRow(
            trade_date=_date_value(row.get("日期")),
            close=_required_number(row.get("收盘")),
            change_pct=_number(row.get("涨跌幅")),
            source=source,
        )
        for row in frame.to_dict("records")
        if _number(row.get("收盘")) is not None
    ]
    # The key compares trade date.
    return sorted(rows, key=lambda item: item.trade_date)


# Compute the percentage change from the previous value when a usable denominator exists.
# A None result represents the unavailable or inapplicable branch; callers must check it before
# using the value.
def _change_pct(current: float, previous: float | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100, 2)


# Convert a date field to the representation required by this storage or source boundary.
def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# Parse a numeric provider/report field; an unparseable value follows the explicit missing-value
# branch below.
# A None result represents the unavailable or inapplicable branch; callers must check it before
# using the value.
def _number(value: Any) -> float | None:
    if value is None or str(value).strip() in {"", "-", "nan", "None"}:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


# Require a usable numeric field and reject rows that cannot supply it.
def _required_number(value: Any) -> float:
    number = _number(value)
    if number is None:
        raise ValueError(f"expected numeric value, got {value!r}")
    return number


# Convert the source field into the integer representation expected by the domain model.
def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


# Normalize the source value into the text representation used by this boundary.
def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text and text != "-" else None
