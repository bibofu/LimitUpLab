"""Build user-facing Dragon-Tiger review facts from normalized source rows."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
from threading import Lock
from time import monotonic

from app.collectors.hithink_finance_collector import (
    HithinkDragonTigerFact,
    HithinkDragonTigerSnapshot,
    HithinkFinanceCollector,
)
from app.models import (
    DragonTigerReviewItem,
    DragonTigerReviewResponse,
    FirstBoardEnrichmentSnapshot,
    LimitUpEvent,
)
from app.repositories import SQLiteFirstBoardRepository

_MAIN_BOARD_AND_CHINEXT_PREFIXES = (
    "000",
    "001",
    "002",
    "003",
    "300",
    "301",
    "600",
    "601",
    "603",
    "605",
)
_CACHE_TTL_SECONDS = 300.0
_review_cache: dict[date, tuple[float, DragonTigerReviewResponse]] = {}
_review_cache_lock = Lock()


def load_dragon_tiger_review(
    events: Sequence[LimitUpEvent],
    *,
    trade_date: date,
    repository: SQLiteFirstBoardRepository | None = None,
) -> DragonTigerReviewResponse:
    """Load a cached review and synchronize late facts into rating snapshots."""

    now = monotonic()
    with _review_cache_lock:
        cached = _review_cache.get(trade_date)
        if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
            response = cached[1]
        else:
            snapshot = HithinkFinanceCollector().collect_dragon_tiger(
                trade_date=trade_date,
                board_type="all",
                limit=200,
            )
            response = build_dragon_tiger_review(
                snapshot,
                events,
                trade_date=trade_date,
            )
            _review_cache[trade_date] = (monotonic(), response)

        active_repository = repository or SQLiteFirstBoardRepository()
        updates = merge_dragon_tiger_review_enrichments(
            response,
            active_repository.list_enrichment_for_date(response.trade_date),
        )
        if updates:
            active_repository.upsert_enrichment_snapshots(updates)
        return response


def build_dragon_tiger_review(
    snapshot: HithinkDragonTigerSnapshot,
    events: Sequence[LimitUpEvent],
    *,
    trade_date: date,
) -> DragonTigerReviewResponse:
    """Deduplicate source rows and connect matching stocks to local event detail."""

    effective_date = snapshot.trade_date or trade_date
    event_lookup = {
        event.symbol: event
        for event in events
        if event.trade_date == effective_date
    }
    representative_rows: dict[str, HithinkDragonTigerFact] = {}
    for fact in snapshot.items:
        if not _is_main_board_or_chinext_symbol(fact.symbol):
            continue
        display_name = fact.name or (
            event_lookup[fact.symbol].name if fact.symbol in event_lookup else ""
        )
        if _is_st_name(display_name):
            continue
        current = representative_rows.get(fact.symbol)
        if current is None or _row_priority(fact) > _row_priority(current):
            representative_rows[fact.symbol] = fact

    items = [
        DragonTigerReviewItem(
            symbol=fact.symbol,
            name=fact.name or (
                event_lookup[fact.symbol].name if fact.symbol in event_lookup else ""
            ),
            change_pct=fact.change_pct,
            buy_amount=fact.buy_amount,
            sell_amount=fact.sell_amount,
            net_buy_amount=fact.net_buy_amount,
            net_rate=fact.net_rate,
            organization_net_buy_amount=fact.organization_net_buy_amount,
            hot_money_net_buy_amount=fact.hot_money_net_buy_amount,
            hot_rank=fact.hot_rank,
            range_days=fact.range_days,
            limit_reason=fact.limit_reason,
            concepts=_unique_text(fact.concepts),
            detail_trade_date=(
                effective_date if fact.symbol in event_lookup else None
            ),
        )
        for fact in representative_rows.values()
    ]
    items.sort(key=_display_order)

    return DragonTigerReviewResponse(
        trade_date=effective_date,
        source=snapshot.source,
        stock_count=len(items),
        net_inflow_count=sum(1 for item in items if (item.net_buy_amount or 0) > 0),
        net_outflow_count=sum(1 for item in items if (item.net_buy_amount or 0) < 0),
        organization_count=sum(
            1 for item in items if item.organization_net_buy_amount is not None
        ),
        hot_money_count=sum(
            1 for item in items if item.hot_money_net_buy_amount is not None
        ),
        items=items,
    )


def merge_dragon_tiger_review_enrichments(
    response: DragonTigerReviewResponse,
    enrichments: Sequence[FirstBoardEnrichmentSnapshot],
    *,
    refreshed_at: datetime | None = None,
) -> list[FirstBoardEnrichmentSnapshot]:
    """Merge latest Dragon-Tiger facts into matching persisted rating snapshots."""

    item_lookup = {item.symbol: item for item in response.items}
    timestamp = refreshed_at or datetime.now(timezone.utc)
    updates: list[FirstBoardEnrichmentSnapshot] = []
    for enrichment in enrichments:
        item = item_lookup.get(enrichment.symbol)
        if item is None:
            continue
        values = {
            "dragon_tiger_on_list": True,
            "dragon_tiger_net_buy_amount": item.net_buy_amount,
            "dragon_tiger_buy_amount": item.buy_amount,
            "dragon_tiger_sell_amount": item.sell_amount,
            "dragon_tiger_reason": item.limit_reason,
            "dragon_tiger_source": response.source,
        }
        if item.hot_rank is not None:
            values.update(
                {
                    "popularity_rank": item.hot_rank,
                    "popularity_snapshot_at": timestamp,
                    "popularity_source": f"{response.source}-dragon-tiger",
                    "data_missing": [
                        missing
                        for missing in enrichment.data_missing
                        if missing
                        not in ("popularity_snapshot", "eastmoney_popularity")
                    ],
                }
            )
        if all(getattr(enrichment, key) == value for key, value in values.items()):
            continue
        updates.append(
            enrichment.model_copy(update={**values, "created_at": timestamp})
        )
    return updates

def _row_priority(fact: HithinkDragonTigerFact) -> tuple[int, float, int]:
    """Prefer the single-day record, then the row with richer trading evidence."""

    range_priority = 2 if fact.range_days == 1 else 1 if fact.range_days else 0
    net_amount = abs(fact.net_buy_amount or 0)
    return range_priority, net_amount, len(fact.concepts)


def _display_order(item: DragonTigerReviewItem) -> tuple[bool, float, str]:
    """Show the strongest net inflows first while keeping missing values last."""

    return (
        item.net_buy_amount is None,
        -(item.net_buy_amount or 0),
        item.symbol,
    )


def _unique_text(values: Sequence[str]) -> list[str]:
    """Preserve concept order while dropping blank and duplicate labels."""

    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _is_main_board_or_chinext_symbol(symbol: str) -> bool:
    """Keep Shanghai/Shenzhen main-board and ChiNext stocks only."""

    return (
        len(symbol) == 6
        and symbol.isdigit()
        and symbol.startswith(_MAIN_BOARD_AND_CHINEXT_PREFIXES)
    )


def _is_st_name(name: str) -> bool:
    """Return whether an A-share name carries an ST risk-warning marker."""

    return "ST" in name.upper().replace(" ", "")
