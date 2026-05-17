import sqlite3
from datetime import date, time
from pathlib import Path
from typing import Protocol

from app.database import connect, initialize_database
from app.models import LimitUpEvent
from app.services.sample_data import SAMPLE_EVENTS


class LimitUpRepository(Protocol):
    def list_events(self) -> list[LimitUpEvent]:
        """Return limit-up events available to the analysis layer."""


class SampleLimitUpRepository:
    def list_events(self) -> list[LimitUpEvent]:
        return list(SAMPLE_EVENTS)


class SQLiteLimitUpRepository:
    def __init__(self, database_path: Path | None = None, seed_if_empty: bool = False):
        self.database_path = database_path
        self.seed_if_empty = seed_if_empty

    def list_events(self) -> list[LimitUpEvent]:
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            if self.seed_if_empty and self._count_events(connection) == 0:
                self.replace_events(SAMPLE_EVENTS, connection=connection)

            rows = connection.execute(
                """
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
                ORDER BY trade_date DESC, board_height DESC, first_limit_time DESC
                """
            ).fetchall()
        finally:
            connection.close()

        return [self._event_from_row(row) for row in rows]

    def replace_events(
        self,
        events: list[LimitUpEvent],
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owns_connection = connection is None
        active_connection = connection or connect(self.database_path)

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
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owns_connection = connection is None
        active_connection = connection or connect(self.database_path)

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
        connection = connect(self.database_path)
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

    def _count_events(self, connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT COUNT(*) AS count FROM limit_up_events").fetchone()
        return int(row["count"])

    def _event_to_record(self, event: LimitUpEvent) -> tuple[object, ...]:
        return (
            event.symbol,
            event.name,
            event.trade_date.isoformat(),
            event.first_limit_time.isoformat(timespec="minutes"),
            event.last_limit_time.isoformat(timespec="minutes"),
            event.seal_count,
            event.break_count,
            int(event.closed_limit),
            event.board_height,
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

    def _event_from_row(self, row: sqlite3.Row) -> LimitUpEvent:
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


_repository = SQLiteLimitUpRepository(seed_if_empty=True)


def get_limit_up_repository() -> LimitUpRepository:
    return _repository
