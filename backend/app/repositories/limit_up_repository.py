"""Persistence adapters for limit-up events."""

from app.storage.postgres import PostgresConnection, Record
from datetime import date, time
from typing import Protocol

from app.database import begin_write, connect, initialize_database
from app.models import LimitUpEvent
from app.services.sample_data import SAMPLE_EVENTS
from app.services.relay_universe import is_relay_research_security


class LimitUpReader(Protocol):
    """Read interface used by routers and analysis services."""

    def list_events(self) -> list[LimitUpEvent]:
        """Return limit-up events available to the analysis layer."""

    def list_latest_events(self) -> list[LimitUpEvent]:
        """Return only the newest persisted trading day's events."""


class LimitUpRepository:
    """PostgreSQL repository for persisted limit-up events."""

    def __init__(self, database_url: str | None = None, seed_if_empty: bool = False):
        """Create a repository bound to a PostgreSQL URL.

        When `seed_if_empty` is true, the first read bootstraps bundled sample
        events so a fresh clone can render the dashboard before importing data.
        """

        self.database_url = database_url
        self.seed_if_empty = seed_if_empty

    def list_events(self) -> list[LimitUpEvent]:
        """Load all persisted events ordered newest and strongest first."""

        return self._list_events(latest_only=False)

    def list_latest_events(self) -> list[LimitUpEvent]:
        """Use the trade-date index without materializing the full history."""

        return self._list_events(latest_only=True)

    def _list_events(self, *, latest_only: bool) -> list[LimitUpEvent]:
        """Read events from one consistent PostgreSQL query."""

        connection = connect(self.database_url)
        try:
            initialize_database(connection)
            if self.seed_if_empty and self._count_events(connection) == 0:
                self.replace_events(SAMPLE_EVENTS, connection=connection)

            date_filter = (
                "WHERE trade_date = (SELECT MAX(trade_date) FROM limit_up_events)"
                if latest_only else ""
            )
            rows = connection.execute(
                f"""
                SELECT
                    symbol,
                    name,
                    trade_date,
                    first_limit_time,
                    last_limit_time,
                    seal_count,
                    break_count,
                    closed_limit,
                    board_height,
                    amount,
                    turnover_rate,
                    industry,
                    concept,
                    next_open_pct,
                    next_high_pct,
                    next_close_pct,
                    three_day_return_pct,
                    five_day_return_pct,
                    continued_next_day
                FROM limit_up_events
                {date_filter}
                ORDER BY trade_date DESC, board_height DESC, first_limit_time DESC
                """
            ).fetchall()
        finally:
            connection.close()

        return [self._event_from_row(row) for row in rows]

    def replace_events(
        self,
        events: list[LimitUpEvent],
        connection: PostgresConnection | None = None,
    ) -> None:
        """Replace the entire event table with the provided event list."""

        owns_connection = connection is None
        active_connection = connection or connect(self.database_url)

        try:
            initialize_database(active_connection)
            active_connection.execute("DELETE FROM limit_up_events")
            self.upsert_events(events, connection=active_connection)
            active_connection.commit()
        finally:
            if owns_connection:
                active_connection.close()

    def upsert_events(
        self,
        events: list[LimitUpEvent],
        connection: PostgresConnection | None = None,
    ) -> None:
        """Insert or update events using `(trade_date, symbol)` as the key."""

        owns_connection = connection is None
        active_connection = connection or connect(self.database_url)

        try:
            initialize_database(active_connection)
            active_connection.executemany(
                """
                INSERT INTO limit_up_events (
                    symbol,
                    name,
                    trade_date,
                    first_limit_time,
                    last_limit_time,
                    seal_count,
                    break_count,
                    closed_limit,
                    board_height,
                    amount,
                    turnover_rate,
                    industry,
                    concept,
                    next_open_pct,
                    next_high_pct,
                    next_close_pct,
                    three_day_return_pct,
                    five_day_return_pct,
                    continued_next_day
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, symbol) DO UPDATE SET
                    name = excluded.name,
                    first_limit_time = excluded.first_limit_time,
                    last_limit_time = excluded.last_limit_time,
                    seal_count = excluded.seal_count,
                    break_count = excluded.break_count,
                    closed_limit = excluded.closed_limit,
                    board_height = excluded.board_height,
                    amount = excluded.amount,
                    turnover_rate = excluded.turnover_rate,
                    industry = excluded.industry,
                    concept = excluded.concept,
                    next_open_pct = excluded.next_open_pct,
                    next_high_pct = excluded.next_high_pct,
                    next_close_pct = excluded.next_close_pct,
                    three_day_return_pct = excluded.three_day_return_pct,
                    five_day_return_pct = excluded.five_day_return_pct,
                    continued_next_day = excluded.continued_next_day
                """,
                [self._event_to_record(event) for event in events],
            )
            active_connection.commit()
        finally:
            if owns_connection:
                active_connection.close()

    def delete_events_for_date(self, trade_date: date | str) -> None:
        """Delete all persisted events for a single trading date."""

        connection = connect(self.database_url)
        try:
            initialize_database(connection)
            value = trade_date.isoformat() if isinstance(trade_date, date) else trade_date
            connection.execute(
                "DELETE FROM limit_up_events WHERE trade_date = ?",
                (value,),
            )
            connection.commit()
        finally:
            connection.close()

    def replace_relay_events_for_date(
        self, trade_date: date, events: list[LimitUpEvent],
    ) -> None:
        """Replace a verified relay scope while retaining excluded raw records."""

        if any(event.trade_date != trade_date for event in events):
            raise ValueError("Relay replacement events must match the target date")
        connection = connect(self.database_url)
        try:
            initialize_database(connection)
            begin_write(connection)
            rows = connection.execute(
                "SELECT symbol, name FROM limit_up_events WHERE trade_date = ?",
                (trade_date.isoformat(),),
            ).fetchall()
            connection.executemany(
                "DELETE FROM limit_up_events WHERE trade_date = ? AND symbol = ?",
                [
                    (trade_date.isoformat(), row["symbol"])
                    for row in rows
                    if is_relay_research_security(row["symbol"], row["name"])
                ],
            )
            self.upsert_events(events, connection=connection)
        finally:
            connection.close()

    def _count_events(self, connection: PostgresConnection) -> int:
        """Return the current number of persisted event rows."""

        row = connection.execute("SELECT COUNT(*) AS count FROM limit_up_events").fetchone()
        return int(row["count"])

    def _event_to_record(self, event: LimitUpEvent) -> tuple[object, ...]:
        """Serialize a Pydantic event to a PostgreSQL record tuple."""

        return (
            event.symbol,
            event.name,
            event.trade_date.isoformat(),
            event.first_limit_time.isoformat(timespec="minutes"),
            event.last_limit_time.isoformat(timespec="minutes"),
            event.seal_count,
            event.break_count,
            int(event.closed_limit),
            event.board_height if event.closed_limit else 1,
            event.amount,
            event.turnover_rate,
            event.industry,
            event.concept,
            event.next_open_pct,
            event.next_high_pct,
            event.next_close_pct,
            event.three_day_return_pct,
            event.five_day_return_pct,
            int(event.continued_next_day),
        )

    def _event_from_row(self, row: Record) -> LimitUpEvent:
        """Deserialize a PostgreSQL row into a `LimitUpEvent` model."""

        return LimitUpEvent(
            symbol=row["symbol"],
            name=row["name"],
            trade_date=date.fromisoformat(row["trade_date"]),
            first_limit_time=time.fromisoformat(row["first_limit_time"]),
            last_limit_time=time.fromisoformat(row["last_limit_time"]),
            seal_count=row["seal_count"],
            break_count=row["break_count"],
            closed_limit=bool(row["closed_limit"]),
            board_height=row["board_height"],
            amount=row["amount"],
            turnover_rate=row["turnover_rate"],
            industry=row["industry"],
            concept=row["concept"],
            next_open_pct=row["next_open_pct"],
            next_high_pct=row["next_high_pct"],
            next_close_pct=row["next_close_pct"],
            three_day_return_pct=row["three_day_return_pct"],
            five_day_return_pct=row["five_day_return_pct"],
            continued_next_day=bool(row["continued_next_day"]),
        )


_repository = LimitUpRepository(seed_if_empty=True)


def get_limit_up_repository() -> LimitUpReader:
    """Return the process-wide repository used by API routes."""

    return _repository
