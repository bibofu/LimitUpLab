"""PostgreSQL persistence for the sector catalog, daily metrics and constituents."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Iterator

from app.database import connect, initialize_database


class SectorRepository:
    """Fast local reads after the after-close sector sync has run."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url

    def upsert_catalog(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._connection() as db:
            for row in rows:
                db.execute(
                    """
                    INSERT INTO sector_catalog(thscode, name, category, in_summary, updated_at)
                    VALUES (?, ?, ?, ?, clock_timestamp())
                    ON CONFLICT(thscode) DO UPDATE SET
                        name = excluded.name,
                        category = excluded.category,
                        in_summary = excluded.in_summary,
                        updated_at = clock_timestamp()
                    """,
                    (row["thscode"], row["name"], row["category"],
                     1 if row.get("in_summary") else 0),
                )

    def catalog(
        self, category: str | None = None, *, summary_only: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if category:
            clauses.append("category=?")
            params.append(category)
        if summary_only:
            clauses.append("in_summary=1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as db:
            rows = db.execute(
                f"SELECT thscode, name, category, in_summary FROM sector_catalog {where} "
                "ORDER BY category, name",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def catalog_updated_at(self) -> datetime | None:
        with self._connection() as db:
            row = db.execute("SELECT MAX(updated_at) FROM sector_catalog").fetchone()
        return row[0] if row else None

    def replace_summary_flags(self, thscodes: list[str]) -> None:
        """Mark exactly the 90-industry ranking universe for the current day."""

        with self._connection() as db:
            db.execute("UPDATE sector_catalog SET in_summary=0 WHERE category='industry'")
            for thscode in thscodes:
                db.execute(
                    "UPDATE sector_catalog SET in_summary=1 WHERE thscode=?",
                    (thscode,),
                )

    def upsert_daily_snapshot(self, rows: list[dict[str, Any]]) -> None:
        """Store ranked snapshot facts without discarding an already loaded close."""

        if not rows:
            return
        with self._connection() as db:
            for row in rows:
                db.execute(
                    """
                    INSERT INTO sector_daily(
                        trade_date, thscode, close, change_pct, amount_yi,
                        net_inflow_yi, up_count, down_count, leader_name,
                        leader_price, leader_change_pct, source, captured_at)
                    VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trade_date, thscode) DO UPDATE SET
                        change_pct = excluded.change_pct,
                        amount_yi = excluded.amount_yi,
                        net_inflow_yi = excluded.net_inflow_yi,
                        up_count = excluded.up_count,
                        down_count = excluded.down_count,
                        leader_name = excluded.leader_name,
                        leader_price = excluded.leader_price,
                        leader_change_pct = excluded.leader_change_pct,
                        source = excluded.source,
                        captured_at = excluded.captured_at
                    """,
                    (
                        row["trade_date"], row["thscode"], row.get("change_pct"),
                        row.get("amount_yi"), row.get("net_inflow_yi"),
                        row.get("up_count"), row.get("down_count"),
                        row.get("leader_name"), row.get("leader_price"),
                        row.get("leader_change_pct"), row["source"],
                        row.get("captured_at") or datetime.now(timezone.utc),
                    ),
                )

    def upsert_daily_history(self, rows: list[dict[str, Any]]) -> None:
        """Fill daily closes; an existing snapshot keeps its official change."""

        if not rows:
            return
        with self._connection() as db:
            for row in rows:
                db.execute(
                    """
                    INSERT INTO sector_daily(trade_date, thscode, close, change_pct, source)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(trade_date, thscode) DO UPDATE SET
                        close = excluded.close,
                        change_pct = COALESCE(sector_daily.change_pct, excluded.change_pct),
                        source = CASE WHEN sector_daily.change_pct IS NULL
                                      THEN excluded.source ELSE sector_daily.source END
                    """,
                    (
                        row["trade_date"], row["thscode"], row["close"],
                        row.get("change_pct"), row["source"],
                    ),
                )

    def daily_ranking(
        self, trade_date: date, category: str, *, summary_only: bool = False,
    ) -> list[dict[str, Any]]:
        summary = "AND c.in_summary=1" if summary_only else ""
        with self._connection() as db:
            rows = db.execute(
                f"""
                SELECT c.name, c.thscode, c.category, d.close, d.change_pct,
                       d.amount_yi, d.net_inflow_yi, d.up_count, d.down_count,
                       d.leader_name, d.leader_price, d.leader_change_pct,
                       d.source, d.captured_at
                FROM sector_daily d
                JOIN sector_catalog c ON c.thscode = d.thscode
                WHERE d.trade_date=? AND c.category=? AND d.change_pct IS NOT NULL {summary}
                ORDER BY d.change_pct DESC, d.thscode
                """,
                (trade_date, category),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_daily_date(
        self, category: str, *, summary_only: bool = False,
        on_or_before: date | None = None,
    ) -> date | None:
        summary = "c.in_summary=1" if summary_only else ""
        clauses = ["c.category=?", "d.change_pct IS NOT NULL", summary]
        params: list[Any] = [category]
        if on_or_before is not None:
            clauses.append("d.trade_date<=?")
            params.append(on_or_before)
        with self._connection() as db:
            row = db.execute(
                f"""
                SELECT MAX(d.trade_date)
                FROM sector_daily d
                JOIN sector_catalog c ON c.thscode = d.thscode
                WHERE {' AND '.join(clause for clause in clauses if clause)}
                """,
                tuple(params),
            ).fetchone()
        return row[0] if row else None

    def previous_daily_date(
        self, trade_date: date, category: str, *, summary_only: bool = False,
    ) -> date | None:
        summary = "AND c.in_summary=1" if summary_only else ""
        with self._connection() as db:
            row = db.execute(
                f"""
                SELECT MAX(d.trade_date)
                FROM sector_daily d
                JOIN sector_catalog c ON c.thscode = d.thscode
                WHERE c.category=? AND d.trade_date<? {summary}
                """,
                (category, trade_date),
            ).fetchone()
        return row[0] if row else None

    def daily_history(self, thscode: str, end_date: date, limit: int) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT trade_date, close, change_pct, source
                FROM sector_daily
                WHERE thscode=? AND trade_date<=? AND close IS NOT NULL
                ORDER BY trade_date DESC
                LIMIT ?
                """,
                (thscode, end_date, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def daily_row(self, trade_date: date, thscode: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM sector_daily WHERE trade_date=? AND thscode=?",
                (trade_date, thscode),
            ).fetchone()
        return dict(row) if row else None

    def coverage(
        self, trade_date: date, category: str, *, summary_only: bool = False,
    ) -> tuple[int, int]:
        summary = "AND c.in_summary=1" if summary_only else ""
        with self._connection() as db:
            covered = db.execute(
                f"""
                SELECT COUNT(*)
                FROM sector_daily d
                JOIN sector_catalog c ON c.thscode = d.thscode
                WHERE d.trade_date=? AND c.category=? AND d.change_pct IS NOT NULL {summary}
                """,
                (trade_date, category),
            ).fetchone()[0]
            expected = db.execute(
                f"SELECT COUNT(*) FROM sector_catalog c WHERE c.category=? {summary}",
                (category,),
            ).fetchone()[0]
        return int(covered), int(expected)

    def replace_constituents(self, thscode: str, rows: list[dict[str, str]]) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM sector_constituents WHERE thscode=?", (thscode,))
            for row in rows:
                db.execute(
                    """
                    INSERT INTO sector_constituents(thscode, symbol, name, updated_at)
                    VALUES (?, ?, ?, clock_timestamp())
                    ON CONFLICT(thscode, symbol) DO UPDATE SET
                        name = excluded.name,
                        updated_at = clock_timestamp()
                    """,
                    (thscode, row["symbol"], row["name"]),
                )

    def constituents(self, thscode: str) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT symbol, name FROM sector_constituents WHERE thscode=? ORDER BY symbol",
                (thscode,),
            ).fetchall()
        return [dict(row) for row in rows]

    def constituents_updated_at(self, thscode: str) -> datetime | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT MAX(updated_at) FROM sector_constituents WHERE thscode=?",
                (thscode,),
            ).fetchone()
        return row[0] if row else None

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        db = connect(self.database_url)
        try:
            initialize_database(db)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
