"""Read-only data access for event-relative post-limit research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import sqlite3

from app.database import get_database_path


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

    path = database_path or get_database_path()
    if not path.exists():
        return PostLimitDataset([], [], [], [], None)
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
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
