"""Bounded real local-tool execution, with baseline drift checked before LLM use."""

from contextlib import closing, contextmanager
import sqlite3

from app.agent_eval.core_batch import LocalResearchRegistry
from app.agent_eval.local_capture import read_local_session
from app.agent_eval.local_promotion import SnapshotBars
from app.agents.query_contract import query_reference_date_override
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import AgentToolRegistry
from app.repositories.first_board_repository import SQLiteFirstBoardRepository


class HistoricalDataDrift(ValueError):
    pass


class HistoricalLiveRegistry(LocalResearchRegistry):
    def __init__(self, database, baseline, *, allow_limit_down=False):
        self.allow_limit_down = allow_limit_down
        self.include_promotion = any(r.tool == "daily_board_promotion" for r in baseline.recordings)
        if any(day > baseline.anchor_datetime.date() for day in baseline.trading_calendar.trading_dates):
            raise ValueError("historical baseline cannot load future sessions")
        events = []
        for day in baseline.trading_calendar.trading_dates:
            anchor = baseline.anchor_datetime.replace(year=day.year, month=day.month, day=day.day)
            _, _, loaded = read_local_session(database, anchor)
            events.extend(loaded)
        super().__init__(events)
        if self.include_promotion:
            # Materialize actual bars once; subsequent tools never open a writable repository.
            with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT * FROM stock_daily_bars WHERE trade_date BETWEEN ? AND ? ORDER BY symbol,trade_date",
                    (min(baseline.trading_calendar.trading_dates).isoformat(),
                     baseline.anchor_datetime.date().isoformat())).fetchall()
            repository = SQLiteFirstBoardRepository(database)
            self.first_board_repository = SnapshotBars([repository._bar_from_row(row) for row in rows])
        self.reference_date = baseline.anchor_datetime.date()
        self.baseline_checks = []
        # Execute real production tools, never execute_frozen_calls or lookup by arguments.
        gateway = ToolGateway(self, EvidenceStore())
        with self.anchored():
            for recording in baseline.recordings:
                args = gateway.validate({"name":recording.tool,"args":recording.arguments})
                _, payload, state = gateway.execute(recording.tool, args)
                if payload != recording.observation.payload or state != recording.observation.state:
                    raise HistoricalDataDrift(f"historical tool output differs from versioned baseline: {recording.id}")
                self.baseline_checks.append(recording.id)
        self.attempts = []  # No frozen matching takes place in this registry.

    @property
    def enabled_tool_names(self):
        return frozenset({"market_summary", "limit_up_events", "market_event_pool"} | (
            {"daily_board_promotion"} if self.include_promotion else set()))

    def is_enabled(self, name):
        return name in self.enabled_tool_names

    def schemas(self):
        return AgentToolRegistry.schemas(self)

    def market_summary(self, *, include_limit_down=False):
        if include_limit_down and not self.allow_limit_down:
            raise ValueError("historical run has not enabled public limit-down collection")
        # Invoke the actual collector, never replay an old true/false observation.
        return AgentToolRegistry.market_summary(self, include_limit_down=include_limit_down)

    @contextmanager
    def anchored(self):
        with query_reference_date_override(self.reference_date):
            yield self
