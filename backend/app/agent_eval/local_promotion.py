"""One-tool runner over a read-only, transaction-consistent local snapshot."""

from contextlib import closing
from datetime import date, datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from app.agent_eval.local_capture import LocalSummaryRegistry
from fastapi.encoders import jsonable_encoder

from app.agent_eval.recorder import digest
from app.agent_eval.core_batch import write_json
from app.agents.query_contract import query_reference_date_override
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import AgentToolRegistry
from app.repositories.first_board_repository import SQLiteFirstBoardRepository
from app.repositories.limit_up_repository import SQLiteLimitUpRepository


class SnapshotBars:
    def __init__(self, bars):
        self.bars = tuple(bars)

    def list_daily_bars_for_symbols(self, symbols, *, end_date=None):
        selected = set(symbols)
        return [bar for bar in self.bars if bar.symbol in selected
                and (end_date is None or bar.trade_date <= end_date)]


class LocalPromotionRegistry(LocalSummaryRegistry):
    def __init__(self, events, bars):
        super().__init__(events)
        self.first_board_repository = SnapshotBars(bars)

    def schemas(self):
        return AgentToolRegistry.schemas(self)

    def is_enabled(self, name):
        return name == "daily_board_promotion"


def record_local_promotion(database: Path, start_date: date, anchor: datetime,
                           destination: Path, *, days: int = 5):
    if anchor.utcoffset() is None:
        raise ValueError("anchor must be timezone-aware")
    end_date = anchor.astimezone(ZoneInfo("Asia/Shanghai")).date()
    if not 0 <= (end_date - start_date).days <= 366 or not 1 <= days <= 60:
        raise ValueError("require a 0..366-day snapshot window and 1..60 result days")
    if destination.exists():
        raise FileExistsError(destination)
    # No initialize_database, collector, writable connection or live fallback.
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        bounds = (start_date.isoformat(), end_date.isoformat())
        event_rows = connection.execute(
            "SELECT * FROM limit_up_events WHERE trade_date BETWEEN ? AND ? "
            "ORDER BY trade_date, symbol", bounds).fetchall()
        bar_rows = connection.execute(
            "SELECT * FROM stock_daily_bars WHERE trade_date BETWEEN ? AND ? "
            "ORDER BY symbol, trade_date", bounds).fetchall()
    if not event_rows:
        raise ValueError("no local events in the requested snapshot window")
    events_repo = SQLiteLimitUpRepository(database, seed_if_empty=False)
    bars_repo = SQLiteFirstBoardRepository(database)
    events = [events_repo._event_from_row(row) for row in event_rows]
    bars = [bars_repo._bar_from_row(row) for row in bar_rows]
    gateway = ToolGateway(LocalPromotionRegistry(events, bars), EvidenceStore())
    with query_reference_date_override(end_date):
        arguments = gateway.validate({"name": "daily_board_promotion",
                                      "args": {"days": days, "end_date": end_date.isoformat()}})
        result, payload, state = gateway.execute("daily_board_promotion", arguments)
    # This tool returns a list. Preserve its native Gateway shape; do not pretend
    # this execution artifact is a dict-only frozen recording or a golden case.
    body = {
        "schema_version": "local-tool-execution-v1", "tool": "daily_board_promotion",
        "anchor_datetime": anchor.isoformat(), "arguments": arguments,
        "payload": payload, "state": state, "trace_output": result.trace_output,
        "summary": result.summary, "privacy_status": "unreviewed",
        "source_manifest": {
            "source": "local-sqlite-readonly-transaction", "start_date": bounds[0],
            "end_date": bounds[1], "event_rows": len(event_rows), "bar_rows": len(bar_rows),
            "events_digest": digest([dict(row) for row in event_rows]),
            "bars_digest": digest([dict(row) for row in bar_rows]),
            "scope": "observed local window; not proof of calendar completeness or point-in-time history",
        },
    }
    body = jsonable_encoder(body)
    checksum = digest(body)
    write_json(destination, {"body": body, "checksum": checksum})
    items = body["trace_output"]["items"]
    return {
        "tool": "daily_board_promotion", "observed_days": len(items),
        "data_readiness": "available" if items else "insufficient",
        "model_calls": 0, "business_tool_calls": 1,
        "quality_verified": False, "release_eligible": False,
        "capture": str(destination), "checksum": checksum,
    }
