"""SQLite persistence for first-board feature and outcome data."""

import sqlite3
from datetime import date, datetime
from pathlib import Path

from app.database import connect, initialize_database
from app.models import FirstBoardFeature, FirstBoardOutcome, StockDailyBar


class SQLiteFirstBoardRepository:
    """Repository for local similar-case retrieval data tables."""

    def __init__(self, database_path: Path | None = None):
        """Create a repository bound to a SQLite database path."""

        self.database_path = database_path

    def upsert_features(self, features: list[FirstBoardFeature]) -> None:
        """Insert or update derived first-board feature rows."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.executemany(
                """
                INSERT INTO first_board_features (
                    trade_date,
                    symbol,
                    name,
                    first_limit_minutes,
                    first_limit_bucket,
                    break_count,
                    seal_count,
                    turnover_rate,
                    turnover_bucket,
                    amount,
                    amount_log,
                    amount_bucket,
                    industry,
                    concept,
                    same_industry_limit_up_count,
                    same_concept_limit_up_count,
                    market_limit_up_count,
                    market_first_board_count,
                    market_failed_limit_up_rate,
                    market_failed_rate_bucket,
                    market_max_board_height,
                    market_sentiment,
                    closed_limit,
                    feature_version,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, symbol) DO UPDATE SET
                    name = excluded.name,
                    first_limit_minutes = excluded.first_limit_minutes,
                    first_limit_bucket = excluded.first_limit_bucket,
                    break_count = excluded.break_count,
                    seal_count = excluded.seal_count,
                    turnover_rate = excluded.turnover_rate,
                    turnover_bucket = excluded.turnover_bucket,
                    amount = excluded.amount,
                    amount_log = excluded.amount_log,
                    amount_bucket = excluded.amount_bucket,
                    industry = excluded.industry,
                    concept = excluded.concept,
                    same_industry_limit_up_count = excluded.same_industry_limit_up_count,
                    same_concept_limit_up_count = excluded.same_concept_limit_up_count,
                    market_limit_up_count = excluded.market_limit_up_count,
                    market_first_board_count = excluded.market_first_board_count,
                    market_failed_limit_up_rate = excluded.market_failed_limit_up_rate,
                    market_failed_rate_bucket = excluded.market_failed_rate_bucket,
                    market_max_board_height = excluded.market_max_board_height,
                    market_sentiment = excluded.market_sentiment,
                    closed_limit = excluded.closed_limit,
                    feature_version = excluded.feature_version,
                    created_at = excluded.created_at
                """,
                [self._feature_to_record(feature) for feature in features],
            )
            connection.commit()
        finally:
            connection.close()

    def upsert_daily_bars(self, bars: list[StockDailyBar]) -> None:
        """Insert or update local stock daily bars."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.executemany(
                """
                INSERT INTO stock_daily_bars (
                    symbol,
                    trade_date,
                    open,
                    high,
                    low,
                    close,
                    volume,
                    amount,
                    change_pct,
                    source,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, trade_date) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    amount = excluded.amount,
                    change_pct = excluded.change_pct,
                    source = excluded.source,
                    created_at = excluded.created_at
                """,
                [self._bar_to_record(bar) for bar in bars],
            )
            connection.commit()
        finally:
            connection.close()

    def upsert_outcomes(self, outcomes: list[FirstBoardOutcome]) -> None:
        """Insert or update first-board outcome summaries."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.executemany(
                """
                INSERT INTO first_board_outcomes (
                    base_trade_date,
                    symbol,
                    next_trade_date,
                    next_open_pct,
                    next_high_pct,
                    next_close_pct,
                    three_day_high_pct,
                    three_day_close_pct,
                    max_drawdown_3d,
                    promoted_to_second_board,
                    outcome_ready,
                    outcome_version,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(base_trade_date, symbol) DO UPDATE SET
                    next_trade_date = excluded.next_trade_date,
                    next_open_pct = excluded.next_open_pct,
                    next_high_pct = excluded.next_high_pct,
                    next_close_pct = excluded.next_close_pct,
                    three_day_high_pct = excluded.three_day_high_pct,
                    three_day_close_pct = excluded.three_day_close_pct,
                    max_drawdown_3d = excluded.max_drawdown_3d,
                    promoted_to_second_board = excluded.promoted_to_second_board,
                    outcome_ready = excluded.outcome_ready,
                    outcome_version = excluded.outcome_version,
                    created_at = excluded.created_at
                """,
                [self._outcome_to_record(outcome) for outcome in outcomes],
            )
            connection.commit()
        finally:
            connection.close()

    def list_features_for_date(self, trade_date: date | str) -> list[FirstBoardFeature]:
        """Return first-board feature rows for a trading date."""

        value = trade_date.isoformat() if isinstance(trade_date, date) else trade_date
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT *
                FROM first_board_features
                WHERE trade_date = ?
                ORDER BY first_limit_minutes ASC, symbol ASC
                """,
                (value,),
            ).fetchall()
        finally:
            connection.close()

        return [self._feature_from_row(row) for row in rows]

    def get_feature(self, symbol: str, trade_date: date | str) -> FirstBoardFeature | None:
        """Return one persisted first-board feature row if it exists."""

        value = trade_date.isoformat() if isinstance(trade_date, date) else trade_date
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT *
                FROM first_board_features
                WHERE symbol = ? AND trade_date = ?
                """,
                (symbol, value),
            ).fetchone()
        finally:
            connection.close()

        return self._feature_from_row(row) if row else None

    def recall_similar_features(
        self,
        target: FirstBoardFeature,
        earliest_trade_date: date,
        limit: int = 500,
    ) -> list[FirstBoardFeature]:
        """Coarsely recall historical candidates using indexed feature buckets."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT *
                FROM first_board_features
                WHERE trade_date < ?
                  AND trade_date >= ?
                  AND closed_limit = 1
                  AND ABS(break_count - ?) <= 1
                  AND (
                    first_limit_bucket = ?
                    OR turnover_bucket = ?
                    OR amount_bucket = ?
                    OR market_failed_rate_bucket = ?
                    OR industry = ?
                    OR concept = ?
                  )
                ORDER BY trade_date DESC, first_limit_minutes ASC
                LIMIT ?
                """,
                (
                    target.trade_date.isoformat(),
                    earliest_trade_date.isoformat(),
                    target.break_count,
                    target.first_limit_bucket,
                    target.turnover_bucket,
                    target.amount_bucket,
                    target.market_failed_rate_bucket,
                    target.industry,
                    target.concept,
                    limit,
                ),
            ).fetchall()
        finally:
            connection.close()

        return [self._feature_from_row(row) for row in rows]

    def list_feature_trade_dates_before(self, trade_date: date) -> list[date]:
        """Return persisted feature dates before target date, newest first."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT DISTINCT trade_date
                FROM first_board_features
                WHERE trade_date < ?
                ORDER BY trade_date DESC
                """,
                (trade_date.isoformat(),),
            ).fetchall()
        finally:
            connection.close()

        return [date.fromisoformat(row["trade_date"]) for row in rows]

    def list_daily_bars(self, symbol: str) -> list[StockDailyBar]:
        """Return persisted daily bars for one stock ordered by date."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT *
                FROM stock_daily_bars
                WHERE symbol = ?
                ORDER BY trade_date ASC
                """,
                (symbol,),
            ).fetchall()
        finally:
            connection.close()

        return [self._bar_from_row(row) for row in rows]

    def list_post_bars(
        self,
        symbol: str,
        base_trade_date: date,
        limit: int = 6,
    ) -> list[StockDailyBar]:
        """Return daily bars from a case's first-board date onward."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT *
                FROM stock_daily_bars
                WHERE symbol = ? AND trade_date >= ?
                ORDER BY trade_date ASC
                LIMIT ?
                """,
                (symbol, base_trade_date.isoformat(), limit),
            ).fetchall()
        finally:
            connection.close()

        return [self._bar_from_row(row) for row in rows]


    def has_post_bars(self, symbol: str, base_trade_date: date) -> bool:
        """Return whether local post-first-board bars exist for a case."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM stock_daily_bars
                WHERE symbol = ? AND trade_date >= ?
                """,
                (symbol, base_trade_date.isoformat()),
            ).fetchone()
        finally:
            connection.close()

        return bool(row and row["count"] > 0)

    def get_outcome(
        self,
        symbol: str,
        base_trade_date: date,
    ) -> FirstBoardOutcome | None:
        """Return the derived outcome for one historical first-board case."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT *
                FROM first_board_outcomes
                WHERE symbol = ? AND base_trade_date = ?
                """,
                (symbol, base_trade_date.isoformat()),
            ).fetchone()
        finally:
            connection.close()

        return self._outcome_from_row(row) if row else None

    def _feature_to_record(self, feature: FirstBoardFeature) -> tuple[object, ...]:
        """Serialize a first-board feature model for SQLite."""

        return (
            feature.trade_date.isoformat(),
            feature.symbol,
            feature.name,
            feature.first_limit_minutes,
            feature.first_limit_bucket,
            feature.break_count,
            feature.seal_count,
            feature.turnover_rate,
            feature.turnover_bucket,
            feature.amount,
            feature.amount_log,
            feature.amount_bucket,
            feature.industry,
            feature.concept,
            feature.same_industry_limit_up_count,
            feature.same_concept_limit_up_count,
            feature.market_limit_up_count,
            feature.market_first_board_count,
            feature.market_failed_limit_up_rate,
            feature.market_failed_rate_bucket,
            feature.market_max_board_height,
            feature.market_sentiment,
            int(feature.closed_limit),
            feature.feature_version,
            feature.created_at.isoformat(),
        )

    def _bar_to_record(self, bar: StockDailyBar) -> tuple[object, ...]:
        """Serialize a daily bar model for SQLite."""

        return (
            bar.symbol,
            bar.trade_date.isoformat(),
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
            bar.amount,
            bar.change_pct,
            bar.source,
            bar.created_at.isoformat(),
        )

    def _outcome_to_record(self, outcome: FirstBoardOutcome) -> tuple[object, ...]:
        """Serialize an outcome model for SQLite."""

        return (
            outcome.base_trade_date.isoformat(),
            outcome.symbol,
            outcome.next_trade_date.isoformat() if outcome.next_trade_date else None,
            outcome.next_open_pct,
            outcome.next_high_pct,
            outcome.next_close_pct,
            outcome.three_day_high_pct,
            outcome.three_day_close_pct,
            outcome.max_drawdown_3d,
            int(outcome.promoted_to_second_board),
            int(outcome.outcome_ready),
            outcome.outcome_version,
            outcome.created_at.isoformat(),
        )

    def _feature_from_row(self, row: sqlite3.Row) -> FirstBoardFeature:
        """Deserialize a SQLite row into a feature model."""

        return FirstBoardFeature(
            trade_date=date.fromisoformat(row["trade_date"]),
            symbol=row["symbol"],
            name=row["name"],
            first_limit_minutes=row["first_limit_minutes"],
            first_limit_bucket=row["first_limit_bucket"],
            break_count=row["break_count"],
            seal_count=row["seal_count"],
            turnover_rate=row["turnover_rate"],
            turnover_bucket=row["turnover_bucket"],
            amount=row["amount"],
            amount_log=row["amount_log"],
            amount_bucket=row["amount_bucket"],
            industry=row["industry"],
            concept=row["concept"],
            same_industry_limit_up_count=row["same_industry_limit_up_count"],
            same_concept_limit_up_count=row["same_concept_limit_up_count"],
            market_limit_up_count=row["market_limit_up_count"],
            market_first_board_count=row["market_first_board_count"],
            market_failed_limit_up_rate=row["market_failed_limit_up_rate"],
            market_failed_rate_bucket=row["market_failed_rate_bucket"],
            market_max_board_height=row["market_max_board_height"],
            market_sentiment=row["market_sentiment"],
            closed_limit=bool(row["closed_limit"]),
            feature_version=row["feature_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _bar_from_row(self, row: sqlite3.Row) -> StockDailyBar:
        """Deserialize a SQLite row into a daily bar model."""

        return StockDailyBar(
            symbol=row["symbol"],
            trade_date=date.fromisoformat(row["trade_date"]),
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
            amount=row["amount"],
            change_pct=row["change_pct"],
            source=row["source"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _outcome_from_row(self, row: sqlite3.Row) -> FirstBoardOutcome:
        """Deserialize a SQLite row into an outcome model."""

        return FirstBoardOutcome(
            base_trade_date=date.fromisoformat(row["base_trade_date"]),
            symbol=row["symbol"],
            next_trade_date=date.fromisoformat(row["next_trade_date"])
            if row["next_trade_date"]
            else None,
            next_open_pct=row["next_open_pct"],
            next_high_pct=row["next_high_pct"],
            next_close_pct=row["next_close_pct"],
            three_day_high_pct=row["three_day_high_pct"],
            three_day_close_pct=row["three_day_close_pct"],
            max_drawdown_3d=row["max_drawdown_3d"],
            promoted_to_second_board=bool(row["promoted_to_second_board"]),
            outcome_ready=bool(row["outcome_ready"]),
            outcome_version=row["outcome_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


