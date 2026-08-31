"""SQLite cache for normalized stock-specific news."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from app.database import connect, initialize_database
from app.models import StockNewsItem


class SQLiteStockNewsRepository:
    """Persist deduplicated stock news and per-source synchronization state."""

    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    def upsert_items(self, items: list[StockNewsItem]) -> None:
        """Insert or refresh normalized articles without creating duplicates."""

        if not items:
            return
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.executemany(
                """
                INSERT INTO stock_news_items (
                    item_id, symbol, name, title, summary, published_at,
                    source, url, item_type, relevance_score, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    name = excluded.name,
                    title = excluded.title,
                    summary = excluded.summary,
                    published_at = excluded.published_at,
                    item_type = excluded.item_type,
                    relevance_score = excluded.relevance_score,
                    fetched_at = excluded.fetched_at
                """,
                [
                    (
                        _item_id(item),
                        item.symbol,
                        item.name,
                        item.title,
                        item.summary,
                        item.published_at.isoformat(),
                        item.source,
                        item.url,
                        item.item_type,
                        item.relevance_score,
                        item.fetched_at.isoformat(),
                    )
                    for item in items
                ],
            )
            connection.commit()
        finally:
            connection.close()

    def list_items(
        self,
        *,
        symbol: str,
        published_since: datetime,
        limit: int,
    ) -> list[StockNewsItem]:
        """Return recent cached articles in descending publication order."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT *
                FROM stock_news_items
                WHERE symbol = ? AND published_at >= ?
                ORDER BY published_at DESC, relevance_score DESC, item_id ASC
                LIMIT ?
                """,
                (symbol, published_since.isoformat(), max(1, min(limit, 50))),
            ).fetchall()
        finally:
            connection.close()
        return [
            StockNewsItem(
                symbol=row["symbol"],
                name=row["name"],
                title=row["title"],
                summary=row["summary"],
                published_at=datetime.fromisoformat(row["published_at"]),
                source=row["source"],
                url=row["url"],
                item_type=row["item_type"],
                relevance_score=row["relevance_score"],
                fetched_at=datetime.fromisoformat(row["fetched_at"]),
            )
            for row in rows
        ]

    def record_sync(
        self,
        *,
        symbol: str,
        source: str,
        attempted_at: datetime,
        error_message: str | None,
    ) -> None:
        """Record one bounded provider attempt while preserving last success."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO stock_news_sync_state (
                    symbol, source, last_attempt_at, last_success_at, error_message
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol, source) DO UPDATE SET
                    last_attempt_at = excluded.last_attempt_at,
                    last_success_at = CASE
                        WHEN excluded.error_message IS NULL
                        THEN excluded.last_success_at
                        ELSE stock_news_sync_state.last_success_at
                    END,
                    error_message = excluded.error_message
                """,
                (
                    symbol,
                    source,
                    attempted_at.isoformat(),
                    attempted_at.isoformat() if error_message is None else None,
                    error_message,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def last_success_at(self, *, symbol: str, source: str) -> datetime | None:
        """Return the provider's most recent successful synchronization time."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT last_success_at
                FROM stock_news_sync_state
                WHERE symbol = ? AND source = ?
                """,
                (symbol, source),
            ).fetchone()
        finally:
            connection.close()
        if row is None or not row["last_success_at"]:
            return None
        return datetime.fromisoformat(row["last_success_at"])


def _item_id(item: StockNewsItem) -> str:
    identity = "|".join(
        (
            item.symbol,
            item.source.strip().lower(),
            item.url.strip().lower(),
            "".join(item.title.lower().split()),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
