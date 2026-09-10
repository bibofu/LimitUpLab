"""Read-only, bounded loading for the consolidation observation pool."""
from datetime import date, datetime
from pathlib import Path
import sqlite3

from app.consolidation_models import ConsolidationPool, ObservationStrategy
from app.database import get_database_path
from app.post_limit_query_contract import PREMARKET_OBSERVATION_RECENT_LIMIT_DAYS
from app.services.consolidation import (
    completed_date_limit,
    observation_pool,
    screen_consolidation,
)


# Load the local event/bar dataset and build the requested observation pool at its data cutoff.
def load_consolidation_pool(as_of: date | None, now: datetime,
                            database_path: Path | None = None,
                            strategy: ObservationStrategy = "consolidation") -> ConsolidationPool:
    path = database_path or get_database_path()
    if not path.exists():
        return observation_pool(now, strategy, data_missing=["local_database"])
    limit = completed_date_limit(now)
    if as_of is not None and as_of > limit:
        raise ValueError("只能查询已结束交易日，当前交易日须在 15:30 后查询。")
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        available = [r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM stock_daily_bars WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 60", (limit.isoformat(),))]
        if not available:
            return observation_pool(now, strategy, data_missing=["daily_bars"])
        end = as_of.isoformat() if as_of else available[0]
        dates = sorted(r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM stock_daily_bars WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 25", (end,)))
        if not dates or end not in dates:
            raise ValueError("所选日期没有本地日K数据。")
        events = [dict(r) for r in conn.execute(
            "SELECT symbol,name,trade_date,closed_limit FROM limit_up_events WHERE trade_date BETWEEN ? AND ?", (dates[0], end))]
        symbols = sorted({
            e["symbol"]
            for e in events
            if e["closed_limit"]
            and e["trade_date"] in dates[-PREMARKET_OBSERVATION_RECENT_LIMIT_DAYS:]
        })
        bars = []
        # SQLite builds may limit bind variables; keep batches below that limit.
        for offset in range(0, len(symbols), 400):
            batch = symbols[offset:offset+400]
            bars.extend(dict(r) for r in conn.execute(
                "SELECT symbol,trade_date,open,high,low,close,volume,source FROM stock_daily_bars "
                "WHERE trade_date BETWEEN ? AND ? AND symbol IN ("+",".join("?" for _ in batch)+")",
                [dates[0], end, *batch]))
    result = screen_consolidation(events, bars, dates, date.fromisoformat(end), now, strategy)
    result.latest_data_date = date.fromisoformat(available[0])
    result.available_dates = [date.fromisoformat(d) for d in available[:30]]
    return result
