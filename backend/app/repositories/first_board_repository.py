"""SQLite persistence for first-board feature and outcome data."""

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from app.database import connect, initialize_database
from app.models import (
    AgentPrediction,
    FirstBoardEnrichmentSnapshot,
    FirstBoardFeature,
    FirstBoardOutcome,
    StockDailyBar,
)


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
                    next_open_to_high_pct,
                    next_open_to_low_pct,
                    next_open_to_close_pct,
                    three_day_high_pct,
                    three_day_close_pct,
                    max_drawdown_3d,
                    three_day_open_to_high_pct,
                    three_day_open_to_close_pct,
                    max_drawdown_from_next_open_3d,
                    promoted_to_second_board,
                    next_day_ready,
                    three_day_ready,
                    outcome_ready,
                    outcome_version,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(base_trade_date, symbol) DO UPDATE SET
                    next_trade_date = excluded.next_trade_date,
                    next_open_pct = excluded.next_open_pct,
                    next_high_pct = excluded.next_high_pct,
                    next_close_pct = excluded.next_close_pct,
                    next_open_to_high_pct = excluded.next_open_to_high_pct,
                    next_open_to_low_pct = excluded.next_open_to_low_pct,
                    next_open_to_close_pct = excluded.next_open_to_close_pct,
                    three_day_high_pct = excluded.three_day_high_pct,
                    three_day_close_pct = excluded.three_day_close_pct,
                    max_drawdown_3d = excluded.max_drawdown_3d,
                    three_day_open_to_high_pct = excluded.three_day_open_to_high_pct,
                    three_day_open_to_close_pct = excluded.three_day_open_to_close_pct,
                    max_drawdown_from_next_open_3d = excluded.max_drawdown_from_next_open_3d,
                    promoted_to_second_board = excluded.promoted_to_second_board,
                    next_day_ready = excluded.next_day_ready,
                    three_day_ready = excluded.three_day_ready,
                    outcome_ready = excluded.outcome_ready,
                    outcome_version = excluded.outcome_version,
                    created_at = excluded.created_at
                """,
                [self._outcome_to_record(outcome) for outcome in outcomes],
            )
            connection.commit()
        finally:
            connection.close()

    def upsert_enrichment_snapshots(
        self,
        snapshots: list[FirstBoardEnrichmentSnapshot],
    ) -> None:
        """Insert or update point-in-time candidate enrichment snapshots."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.executemany(
                """
                INSERT INTO first_board_enrichment_snapshots (
                    trade_date, symbol, kline_bar_count, return_5d_pct,
                    return_20d_pct, return_60d_pct, distance_20d_high_pct,
                    distance_60d_high_pct, volume_ratio_5d, volatility_20d,
                    close_above_ma20, ma_alignment, listing_date,
                    listing_age_days, float_market_cap, float_market_cap_source,
                    recent_limit_up_count_20d, recent_limit_up_count_60d,
                    industry_first_board_count, industry_continued_board_count,
                    industry_failed_count, industry_max_board_height,
                    industry_first_limit_rank, previous_first_board_promotion_rate,
                    market_first_board_seal_rate, dragon_tiger_on_list,
                    dragon_tiger_net_buy_amount, dragon_tiger_buy_amount,
                    dragon_tiger_sell_amount, dragon_tiger_reason, popularity_rank,
                    popularity_rank_change, popularity_snapshot_at,
                    data_missing_json, feature_version, created_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(trade_date, symbol) DO UPDATE SET
                    kline_bar_count = excluded.kline_bar_count,
                    return_5d_pct = excluded.return_5d_pct,
                    return_20d_pct = excluded.return_20d_pct,
                    return_60d_pct = excluded.return_60d_pct,
                    distance_20d_high_pct = excluded.distance_20d_high_pct,
                    distance_60d_high_pct = excluded.distance_60d_high_pct,
                    volume_ratio_5d = excluded.volume_ratio_5d,
                    volatility_20d = excluded.volatility_20d,
                    close_above_ma20 = excluded.close_above_ma20,
                    ma_alignment = excluded.ma_alignment,
                    listing_date = excluded.listing_date,
                    listing_age_days = excluded.listing_age_days,
                    float_market_cap = excluded.float_market_cap,
                    float_market_cap_source = excluded.float_market_cap_source,
                    recent_limit_up_count_20d = excluded.recent_limit_up_count_20d,
                    recent_limit_up_count_60d = excluded.recent_limit_up_count_60d,
                    industry_first_board_count = excluded.industry_first_board_count,
                    industry_continued_board_count = excluded.industry_continued_board_count,
                    industry_failed_count = excluded.industry_failed_count,
                    industry_max_board_height = excluded.industry_max_board_height,
                    industry_first_limit_rank = excluded.industry_first_limit_rank,
                    previous_first_board_promotion_rate = excluded.previous_first_board_promotion_rate,
                    market_first_board_seal_rate = excluded.market_first_board_seal_rate,
                    dragon_tiger_on_list = excluded.dragon_tiger_on_list,
                    dragon_tiger_net_buy_amount = excluded.dragon_tiger_net_buy_amount,
                    dragon_tiger_buy_amount = excluded.dragon_tiger_buy_amount,
                    dragon_tiger_sell_amount = excluded.dragon_tiger_sell_amount,
                    dragon_tiger_reason = excluded.dragon_tiger_reason,
                    popularity_rank = excluded.popularity_rank,
                    popularity_rank_change = excluded.popularity_rank_change,
                    popularity_snapshot_at = excluded.popularity_snapshot_at,
                    data_missing_json = excluded.data_missing_json,
                    feature_version = excluded.feature_version,
                    created_at = excluded.created_at
                """,
                [self._enrichment_to_record(item) for item in snapshots],
            )
            connection.commit()
        finally:
            connection.close()

    def list_enrichment_for_date(
        self,
        trade_date: date,
    ) -> list[FirstBoardEnrichmentSnapshot]:
        """Return all enrichment snapshots for one rating date."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT *
                FROM first_board_enrichment_snapshots
                WHERE trade_date = ?
                ORDER BY symbol ASC
                """,
                (trade_date.isoformat(),),
            ).fetchall()
        finally:
            connection.close()
        return [self._enrichment_from_row(row) for row in rows]

    def get_enrichment(
        self,
        symbol: str,
        trade_date: date,
    ) -> FirstBoardEnrichmentSnapshot | None:
        """Return one candidate's enrichment snapshot if it exists."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT *
                FROM first_board_enrichment_snapshots
                WHERE trade_date = ? AND symbol = ?
                """,
                (trade_date.isoformat(), symbol),
            ).fetchone()
        finally:
            connection.close()
        return self._enrichment_from_row(row) if row else None

    def upsert_predictions(self, predictions: list[AgentPrediction]) -> None:
        """Insert or update persisted Agent rating predictions."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.executemany(
                """
                INSERT INTO agent_predictions (
                    prediction_id,
                    trade_date,
                    symbol,
                    name,
                    score,
                    rating,
                    confidence,
                    scoring_version,
                    prediction_source,
                    data_as_of,
                    facts_json,
                    reasons_json,
                    risks_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, symbol, scoring_version, prediction_source) DO NOTHING
                """,
                [self._prediction_to_record(prediction) for prediction in predictions],
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

    def list_outcomes_between(
        self,
        start_date: date,
        end_date: date,
    ) -> list[FirstBoardOutcome]:
        """Return outcome summaries in an inclusive base-date range."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT *
                FROM first_board_outcomes
                WHERE base_trade_date >= ? AND base_trade_date <= ?
                ORDER BY base_trade_date ASC, symbol ASC
                """,
                (start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        finally:
            connection.close()

        return [self._outcome_from_row(row) for row in rows]

    def list_predictions_between(
        self,
        start_date: date,
        end_date: date,
        scoring_version: str | None = None,
    ) -> list[AgentPrediction]:
        """Return persisted predictions in an inclusive trade-date range."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            sql = """
                SELECT *
                FROM agent_predictions
                WHERE trade_date >= ? AND trade_date <= ?
            """
            parameters: list[object] = [start_date.isoformat(), end_date.isoformat()]
            if scoring_version:
                sql += " AND scoring_version = ?"
                parameters.append(scoring_version)
            sql += """
                ORDER BY trade_date ASC, score DESC, symbol ASC
            """
            rows = connection.execute(sql, parameters).fetchall()
        finally:
            connection.close()

        return [self._prediction_from_row(row) for row in rows]

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
            outcome.next_open_to_high_pct,
            outcome.next_open_to_low_pct,
            outcome.next_open_to_close_pct,
            outcome.three_day_high_pct,
            outcome.three_day_close_pct,
            outcome.max_drawdown_3d,
            outcome.three_day_open_to_high_pct,
            outcome.three_day_open_to_close_pct,
            outcome.max_drawdown_from_next_open_3d,
            int(outcome.promoted_to_second_board),
            int(outcome.next_day_ready),
            int(outcome.three_day_ready),
            int(outcome.outcome_ready),
            outcome.outcome_version,
            outcome.created_at.isoformat(),
        )

    def _enrichment_to_record(
        self,
        item: FirstBoardEnrichmentSnapshot,
    ) -> tuple[object, ...]:
        """Serialize a first-board enrichment snapshot for SQLite."""

        return (
            item.trade_date.isoformat(),
            item.symbol,
            item.kline_bar_count,
            item.return_5d_pct,
            item.return_20d_pct,
            item.return_60d_pct,
            item.distance_20d_high_pct,
            item.distance_60d_high_pct,
            item.volume_ratio_5d,
            item.volatility_20d,
            int(item.close_above_ma20) if item.close_above_ma20 is not None else None,
            item.ma_alignment,
            item.listing_date.isoformat() if item.listing_date else None,
            item.listing_age_days,
            item.float_market_cap,
            item.float_market_cap_source,
            item.recent_limit_up_count_20d,
            item.recent_limit_up_count_60d,
            item.industry_first_board_count,
            item.industry_continued_board_count,
            item.industry_failed_count,
            item.industry_max_board_height,
            item.industry_first_limit_rank,
            item.previous_first_board_promotion_rate,
            item.market_first_board_seal_rate,
            int(item.dragon_tiger_on_list),
            item.dragon_tiger_net_buy_amount,
            item.dragon_tiger_buy_amount,
            item.dragon_tiger_sell_amount,
            item.dragon_tiger_reason,
            item.popularity_rank,
            item.popularity_rank_change,
            item.popularity_snapshot_at.isoformat() if item.popularity_snapshot_at else None,
            json.dumps(item.data_missing, ensure_ascii=False),
            item.feature_version,
            item.created_at.isoformat(),
        )

    def _prediction_to_record(self, prediction: AgentPrediction) -> tuple[object, ...]:
        """Serialize a prediction model for SQLite."""

        return (
            prediction.prediction_id,
            prediction.trade_date.isoformat(),
            prediction.symbol,
            prediction.name,
            prediction.score,
            prediction.rating,
            prediction.confidence,
            prediction.scoring_version,
            prediction.prediction_source,
            prediction.data_as_of.isoformat(),
            json.dumps(prediction.facts_json, ensure_ascii=False),
            json.dumps(prediction.reasons, ensure_ascii=False),
            json.dumps(prediction.risks, ensure_ascii=False),
            prediction.created_at.isoformat(),
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
            next_open_to_high_pct=row["next_open_to_high_pct"],
            next_open_to_low_pct=row["next_open_to_low_pct"],
            next_open_to_close_pct=row["next_open_to_close_pct"],
            three_day_high_pct=row["three_day_high_pct"],
            three_day_close_pct=row["three_day_close_pct"],
            max_drawdown_3d=row["max_drawdown_3d"],
            three_day_open_to_high_pct=row["three_day_open_to_high_pct"],
            three_day_open_to_close_pct=row["three_day_open_to_close_pct"],
            max_drawdown_from_next_open_3d=row["max_drawdown_from_next_open_3d"],
            promoted_to_second_board=bool(row["promoted_to_second_board"]),
            next_day_ready=bool(row["next_day_ready"]),
            three_day_ready=bool(row["three_day_ready"]),
            outcome_ready=bool(row["outcome_ready"]),
            outcome_version=row["outcome_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _enrichment_from_row(self, row: sqlite3.Row) -> FirstBoardEnrichmentSnapshot:
        """Deserialize a first-board enrichment snapshot from SQLite."""

        return FirstBoardEnrichmentSnapshot(
            trade_date=date.fromisoformat(row["trade_date"]),
            symbol=row["symbol"],
            kline_bar_count=row["kline_bar_count"],
            return_5d_pct=row["return_5d_pct"],
            return_20d_pct=row["return_20d_pct"],
            return_60d_pct=row["return_60d_pct"],
            distance_20d_high_pct=row["distance_20d_high_pct"],
            distance_60d_high_pct=row["distance_60d_high_pct"],
            volume_ratio_5d=row["volume_ratio_5d"],
            volatility_20d=row["volatility_20d"],
            close_above_ma20=(
                bool(row["close_above_ma20"])
                if row["close_above_ma20"] is not None
                else None
            ),
            ma_alignment=row["ma_alignment"],
            listing_date=date.fromisoformat(row["listing_date"])
            if row["listing_date"]
            else None,
            listing_age_days=row["listing_age_days"],
            float_market_cap=row["float_market_cap"],
            float_market_cap_source=row["float_market_cap_source"],
            recent_limit_up_count_20d=row["recent_limit_up_count_20d"],
            recent_limit_up_count_60d=row["recent_limit_up_count_60d"],
            industry_first_board_count=row["industry_first_board_count"],
            industry_continued_board_count=row["industry_continued_board_count"],
            industry_failed_count=row["industry_failed_count"],
            industry_max_board_height=row["industry_max_board_height"],
            industry_first_limit_rank=row["industry_first_limit_rank"],
            previous_first_board_promotion_rate=row["previous_first_board_promotion_rate"],
            market_first_board_seal_rate=row["market_first_board_seal_rate"],
            dragon_tiger_on_list=bool(row["dragon_tiger_on_list"]),
            dragon_tiger_net_buy_amount=row["dragon_tiger_net_buy_amount"],
            dragon_tiger_buy_amount=row["dragon_tiger_buy_amount"],
            dragon_tiger_sell_amount=row["dragon_tiger_sell_amount"],
            dragon_tiger_reason=row["dragon_tiger_reason"],
            popularity_rank=row["popularity_rank"],
            popularity_rank_change=row["popularity_rank_change"],
            popularity_snapshot_at=datetime.fromisoformat(row["popularity_snapshot_at"])
            if row["popularity_snapshot_at"]
            else None,
            data_missing=json.loads(row["data_missing_json"]),
            feature_version=row["feature_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _prediction_from_row(self, row: sqlite3.Row) -> AgentPrediction:
        """Deserialize a SQLite row into a prediction model."""

        return AgentPrediction(
            prediction_id=row["prediction_id"],
            trade_date=date.fromisoformat(row["trade_date"]),
            symbol=row["symbol"],
            name=row["name"],
            score=row["score"],
            rating=row["rating"],
            confidence=row["confidence"],
            scoring_version=row["scoring_version"],
            prediction_source=row["prediction_source"],
            data_as_of=date.fromisoformat(row["data_as_of"]),
            facts_json=json.loads(row["facts_json"]),
            reasons=json.loads(row["reasons_json"]),
            risks=json.loads(row["risks_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


