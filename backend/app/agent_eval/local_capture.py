"""Narrow read-only recording of the production market_summary implementation."""

from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from app.agent_eval.models import CalendarSpec
from app.agent_eval.recorder import capture_tool, digest, save_capture, verify_replay
from app.agents.tools import AgentToolRegistry, V1_AGENT_PROFILE
from app.repositories.limit_up_repository import SQLiteLimitUpRepository


class LocalSummaryRegistry(AgentToolRegistry):
    def __init__(self, events):
        # This one tool only consumes events; do not construct repositories/collectors.
        self.events = events
        self.profile = V1_AGENT_PROFILE

    def schemas(self):
        return [item for item in super().schemas() if item.name == "market_summary"]

    def is_enabled(self, name):
        return name == "market_summary"

    def market_summary(self, *, include_limit_down: bool = False):
        if include_limit_down:
            raise ValueError("local capture does not enable remote limit-down collection")
        return super().market_summary(include_limit_down=False)


def record_local_summary(database: Path, anchor: datetime, destination: Path) -> dict:
    if anchor.utcoffset() is None:
        raise ValueError("anchor must be timezone-aware")
    if destination.exists():
        raise FileExistsError(destination)
    day, rows, events = read_local_session(database, anchor)
    registry = LocalSummaryRegistry(events)
    artifact = capture_tool(
        registry, tool="market_summary", arguments={"include_limit_down": False},
        anchor_datetime=anchor, recording_id="local-market-summary-" + day.isoformat(),
        provenance="production market_summary over read-only local market events",
        source_manifest={"source": "local-sqlite-limit-up-events", "trade_date": day.isoformat(),
                         "row_count": len(rows), "selected_rows_digest": digest(rows),
                         "scope": "single-session capture; historical revisions not excluded"},
    )
    calendar = CalendarSpec(id="observed-single-session", version=1, start_date=day,
                            end_date=day, trading_dates=[day])
    report = verify_replay(artifact, calendar=calendar, latest_local_trade_date=day)
    if not report["passed"]:
        raise ValueError("record/replay mismatch: " + str(report["checks"]))
    save_capture(artifact, destination)
    return {**report, "trade_date": day.isoformat(), "source_rows": len(rows),
            "limit_up_count": artifact.body.recording.observation.payload["limit_up_count"],
            "privacy_status": artifact.body.privacy_status}


def read_local_session(database: Path, anchor: datetime):
    """Read one observed session, without opening a writable repository connection."""
    if anchor.utcoffset() is None:
        raise ValueError("anchor must be timezone-aware")
    day = anchor.astimezone(ZoneInfo("Asia/Shanghai")).date()
    # Read exactly the requested date. A missing date is not replaced with a sample or latest.
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM limit_up_events WHERE trade_date = ? "
            "ORDER BY board_height DESC, first_limit_time DESC, symbol", (day.isoformat(),),
        ).fetchall()
    if not rows:
        raise ValueError("no local events for anchor date; choose a verified data window")
    repository = SQLiteLimitUpRepository(database, seed_if_empty=False)
    events = [repository._event_from_row(row) for row in rows]
    return day, [dict(row) for row in rows], events
