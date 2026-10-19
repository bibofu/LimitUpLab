"""Read-only data access for event-relative post-limit research."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from datetime import date
from pathlib import Path

from app.database import connect_readonly


@dataclass(frozen=True)
class PostLimitDataset:
    events: list[dict]
    bars: list[dict]
    calendar: list[str]
    event_dates: list[str]
    latest_data_date: date | None


def load_post_limit_dataset(
    as_of: date | None = None,
    *,
    database_path: Path | None = None,
) -> PostLimitDataset:
    """Load one consistent local snapshot without writing or remote fallback."""

    try:
        connection = connect_readonly(database_path)
    except FileNotFoundError:
        return PostLimitDataset([], [], [], [], None)
    with closing(connection), connection:
        latest = connection.execute(
            "SELECT MAX(trade_date) FROM stock_daily_bars"
        ).fetchone()[0]
        if latest is None:
            return PostLimitDataset([], [], [], [], None)
        end = min(as_of.isoformat(), latest) if as_of else latest
        events = [
            dict(row)
            for row in connection.execute(
                "SELECT symbol,name,trade_date,closed_limit,board_height,industry,concept "
                "FROM limit_up_events WHERE trade_date <= ? ORDER BY trade_date,symbol",
                (end,),
            )
        ]
        bars = [
            dict(row)
            for row in connection.execute(
                "SELECT symbol,trade_date,open,high,low,close,volume,source "
                "FROM stock_daily_bars WHERE trade_date <= ? ORDER BY trade_date,symbol",
                (end,),
            )
        ]
    calendar = sorted({row["trade_date"] for row in bars})
    event_dates = sorted({row["trade_date"] for row in events})
    return PostLimitDataset(
        events=events,
        bars=bars,
        calendar=calendar,
        event_dates=event_dates,
        latest_data_date=date.fromisoformat(latest),
    )
