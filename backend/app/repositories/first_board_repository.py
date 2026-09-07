"""SQLite persistence for first-board feature and outcome data."""

import hashlib
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from app.database import connect, initialize_database
from app.services.prediction_time import assess_prediction_time, close_provenance, provenance_errors
from app.models import (
    AgentPrediction,
    FirstBoardEnrichmentSnapshot,
    FirstBoardFeature,
    FirstBoardOutcome,
    FirstBoardRatingsResponse,
    StockDailyBar,
    StockIntradayKLineBar,
    RecommendationIntelligenceResponse,
)


class SQLiteFirstBoardRepository:
    """Repository for first-board features, outcomes and daily bars."""

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
                    closed_limit,
                    feature_version,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def replace_intraday_bars(
        self,
        *,
        symbol: str,
        trade_date: date,
        period_minutes: int,
        bars: list[StockIntradayKLineBar],
        source: str,
    ) -> None:
        """Atomically replace one stock-day-period intraday cache entry."""

        if not bars:
            return
        created_at = datetime.now().astimezone().isoformat()
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                DELETE FROM stock_intraday_bars
                WHERE symbol = ? AND trade_date = ? AND period_minutes = ?
                """,
                (symbol, trade_date.isoformat(), period_minutes),
            )
            connection.executemany(
                """
                INSERT INTO stock_intraday_bars (
                    symbol, trade_date, period_minutes, timestamp,
                    open, high, low, close, volume, amount, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        symbol,
                        trade_date.isoformat(),
                        period_minutes,
                        bar.timestamp.isoformat(),
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.amount,
                        source,
                        created_at,
                    )
                    for bar in bars
                ],
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
                    dragon_tiger_sell_amount, dragon_tiger_reason,
                    dragon_tiger_source, popularity_rank,
                    popularity_rank_change, popularity_snapshot_at, popularity_source,
                    position_json, data_missing_json, feature_version, created_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    dragon_tiger_source = excluded.dragon_tiger_source,
                    popularity_rank = excluded.popularity_rank,
                    popularity_rank_change = excluded.popularity_rank_change,
                    popularity_snapshot_at = excluded.popularity_snapshot_at,
                    popularity_source = excluded.popularity_source,
                    position_json = excluded.position_json,
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

    def upsert_predictions(self, predictions: list[AgentPrediction]) -> int:
        """Persist predictions and return the number of newly inserted rows.

        Historical rows remain version-aware. Live rows are grouped into one
        immutable batch per trade date so a later policy cannot rewrite the
        picks that users actually saw after that close.
        """

        historical = [
            item for item in predictions if item.prediction_source == "historical_backtest"
        ]
        live_by_date: dict[date, list[AgentPrediction]] = {}
        for item in predictions:
            if item.prediction_source == "live":
                live_by_date.setdefault(item.trade_date, []).append(item)

        for trade_date, items in live_by_date.items():
            versions = {item.scoring_version for item in items}
            created_times = {item.created_at for item in items}
            data_dates = {item.data_as_of for item in items}
            if (
                len(versions) != 1
                or len(created_times) != 1
                or data_dates != {trade_date}
            ):
                raise ValueError(
                    f"Live prediction batch for {trade_date.isoformat()} is inconsistent."
                )

        inserted = self._upsert_historical_predictions(historical)
        for trade_date, items in live_by_date.items():
            first = items[0]
            payload = {
                "trade_date": trade_date.isoformat(),
                "candidates": [
                    {
                        "facts": item.facts_json,
                        "score": item.score,
                        "rating": item.rating,
                        "confidence": item.confidence,
                        "score_breakdown": [],
                        "reasons": item.reasons,
                        "risks": item.risks,
                    }
                    for item in sorted(
                        items,
                        key=lambda item: (-item.score, item.symbol),
                    )
                ],
                "filtered_out": [],
                "universe_count": len(items),
                "generated_by": first.scoring_version,
                "snapshot_source": "live",
                "data_as_of": first.data_as_of.isoformat(),
                "snapshot_created_at": first.created_at.isoformat(),
                "prediction_provenance": first.prediction_provenance or close_provenance(),
            }
            inserted += self._insert_live_prediction_batch(
                predictions=items,
                snapshot_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                top_limit=len(items),
            )
        return inserted

    def persist_live_prediction_snapshot(
        self,
        *,
        ratings: FirstBoardRatingsResponse,
        predictions: list[AgentPrediction],
        top_limit: int,
        data_as_of: date,
        created_at: datetime,
        replace: bool = False,
        final_response: RecommendationIntelligenceResponse | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> int:
        """Insert a live batch, optionally replacing its provisional same-day draft."""

        if final_response is not None:
            from app.services.prediction_time import validate_final_response
            validate_final_response(final_response)
            if final_response.prediction_provenance != ratings.prediction_provenance:
                raise ValueError("Final response and relay snapshot provenance differ.")

        if data_as_of < ratings.trade_date:
            raise ValueError("Live prediction data_as_of cannot precede its base date.")
        if any(
            item.prediction_source != "live"
            or item.trade_date != ratings.trade_date
            or item.scoring_version != ratings.generated_by
            or item.data_as_of != data_as_of
            or item.created_at != created_at
            for item in predictions
        ):
            raise ValueError("Live prediction rows do not match the snapshot metadata.")
        snapshot_symbols = [item.facts.symbol for item in ratings.candidates]
        prediction_symbols = [item.symbol for item in predictions]
        if snapshot_symbols != prediction_symbols:
            raise ValueError(
                "Live prediction rows do not match the snapshot candidate order."
            )

        snapshot = ratings.model_copy(
            update={
                "snapshot_source": "live",
                "data_as_of": data_as_of,
                    "snapshot_created_at": created_at,
                    "prediction_provenance": (
                        predictions[0].prediction_provenance if predictions
                        else ratings.prediction_provenance
                    ) or close_provenance(),
            }
        )
        return self._insert_live_prediction_batch(
            predictions=predictions,
            snapshot_json=json.dumps(
                snapshot.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            ),
            top_limit=max(top_limit, 0),
            empty_batch_metadata=(
                ratings.trade_date,
                ratings.generated_by,
                data_as_of,
                created_at,
            ),
            replace_existing=replace,
            final_response_json=final_response.model_dump_json() if final_response else None,
            before_commit=before_commit,
        )

    def get_live_prediction_snapshot(
        self,
        trade_date: date,
    ) -> FirstBoardRatingsResponse | None:
        """Return the exact live rating response first persisted for one date."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT snapshot_json
                FROM agent_live_prediction_snapshots
                WHERE trade_date = ?
                """,
                (trade_date.isoformat(),),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        snapshot = FirstBoardRatingsResponse.model_validate_json(row["snapshot_json"])
        if snapshot.snapshot_created_at is None or snapshot.data_as_of is None:
            return None
        verdict = assess_prediction_time(SimpleNamespace(
            prediction_source="live", scoring_version=snapshot.generated_by,
            trade_date=snapshot.trade_date, data_as_of=snapshot.data_as_of,
            created_at=snapshot.snapshot_created_at,
            prediction_provenance=snapshot.prediction_provenance,
        ))
        return snapshot if verdict.research_eligible else None

    def _upsert_historical_predictions(
        self,
        predictions: list[AgentPrediction],
    ) -> int:
        """Insert versioned historical snapshots without rewriting existing rows."""

        if not predictions:
            return 0
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            before = connection.total_changes
            connection.executemany(
                """
                INSERT INTO agent_predictions (
                    prediction_id, trade_date, symbol, name, score, rating,
                    confidence, scoring_version, prediction_source, data_as_of,
                    facts_json, reasons_json, risks_json, created_at, provenance_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, symbol, scoring_version, prediction_source) DO NOTHING
                """,
                [self._prediction_to_record(item) for item in predictions],
            )
            inserted = connection.total_changes - before
            connection.commit()
            return inserted
        finally:
            connection.close()

    def _insert_live_prediction_batch(
        self,
        *,
        predictions: list[AgentPrediction],
        snapshot_json: str,
        top_limit: int,
        empty_batch_metadata: tuple[date, str, date, datetime] | None = None,
        replace_existing: bool = False,
        final_response_json: str | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> int:
        """Insert one live batch under a per-date transaction lock."""

        if predictions:
            first = predictions[0]
            trade_date = first.trade_date
            scoring_version = first.scoring_version
            data_as_of = first.data_as_of
            created_at = first.created_at
        elif empty_batch_metadata is not None:
            trade_date, scoring_version, data_as_of, created_at = empty_batch_metadata
        else:
            raise ValueError("Empty live batches require explicit metadata.")
        payload = json.loads(snapshot_json)
        provenance = payload.get("prediction_provenance") or close_provenance()
        errors = provenance_errors(base_date=trade_date, data_as_of=data_as_of,
                                   created_at=created_at, provenance=provenance)
        if errors:
            raise ValueError("Invalid live prediction time: " + ", ".join(errors))
        if any(item.prediction_provenance and item.prediction_provenance != provenance for item in predictions):
            raise ValueError("Prediction time provenance differs within the batch.")
        predictions = [item.model_copy(update={"prediction_provenance": provenance}) for item in predictions]
        payload["prediction_provenance"] = provenance
        snapshot_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        content_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute("BEGIN IMMEDIATE")
            if final_response_json is not None:
                existing_final = connection.execute(
                    "SELECT 1 FROM recommendation_prediction_finals WHERE target_trade_date = ?",
                    (provenance["target_trade_date"],),
                ).fetchone()
                if existing_final:
                    connection.rollback()
                    return 0
            existing = connection.execute(
                """
                SELECT *
                FROM agent_live_prediction_snapshots
                WHERE trade_date = ?
                """,
                (trade_date.isoformat(),),
            ).fetchone()
            if existing is not None:
                if not replace_existing:
                    connection.rollback()
                    return 0
                old_provenance = json.loads(existing["snapshot_json"]).get("prediction_provenance") or {}
                if old_provenance.get("stage") == "premarket_final":
                    connection.rollback()
                    return 0
                if provenance.get("stage") != "premarket_final":
                    raise ValueError("Only a validated premarket final may supersede a close baseline.")
                original_rows = connection.execute(
                    "SELECT * FROM agent_predictions WHERE trade_date = ? AND prediction_source = 'live'",
                    (trade_date.isoformat(),),
                ).fetchall()
                connection.execute("""
                    INSERT OR IGNORE INTO prediction_snapshot_archive
                    (snapshot_id, trade_date, snapshot_record_json, prediction_rows_json, archived_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (existing["snapshot_id"], trade_date.isoformat(),
                      json.dumps(dict(existing), ensure_ascii=False),
                      json.dumps([dict(row) for row in original_rows], ensure_ascii=False),
                      created_at.isoformat()))
                connection.execute(
                    """
                    DELETE FROM agent_predictions
                    WHERE trade_date = ? AND prediction_source = 'live'
                    """,
                    (trade_date.isoformat(),),
                )
                connection.execute(
                    """
                    DELETE FROM agent_live_prediction_snapshots
                    WHERE trade_date = ?
                    """,
                    (trade_date.isoformat(),),
                )
            connection.execute(
                """
                INSERT INTO agent_live_prediction_snapshots (
                    trade_date, snapshot_id, scoring_version, data_as_of,
                    top_limit, prediction_count, snapshot_json, content_hash, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_date.isoformat(),
                    f"live-{trade_date.isoformat()}-{content_hash[:12]}",
                    scoring_version,
                    data_as_of.isoformat(),
                    max(top_limit, 0),
                    len(predictions),
                    snapshot_json,
                    content_hash,
                    created_at.isoformat(),
                ),
            )
            if predictions:
                connection.executemany(
                    """
                    INSERT INTO agent_predictions (
                        prediction_id, trade_date, symbol, name, score, rating,
                        confidence, scoring_version, prediction_source, data_as_of,
                        facts_json, reasons_json, risks_json, created_at, provenance_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [self._prediction_to_record(item) for item in predictions],
                )
            if final_response_json is not None:
                connection.execute("""
                    INSERT INTO recommendation_prediction_finals
                    (target_trade_date, finalized_at, response_json) VALUES (?, ?, ?)
                """, (provenance["target_trade_date"], created_at.isoformat(), final_response_json))
            if before_commit is not None:
                before_commit()
            connection.commit()
            return len(predictions)
        except Exception:
            connection.rollback()
            raise
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

    def list_daily_bars_for_symbols(
        self,
        symbols: list[str],
        *,
        end_date: date | None = None,
    ) -> list[StockDailyBar]:
        """Return daily bars for a bounded stock set with batched SQLite reads."""

        if not symbols:
            return []
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = []
            for offset in range(0, len(symbols), 400):
                batch = symbols[offset:offset + 400]
                placeholders = ",".join("?" for _ in batch)
                date_clause = " AND trade_date <= ?" if end_date else ""
                parameters = [*batch, *([end_date.isoformat()] if end_date else [])]
                rows.extend(connection.execute(
                    f"SELECT * FROM stock_daily_bars WHERE symbol IN ({placeholders})"
                    f"{date_clause} ORDER BY symbol,trade_date",
                    parameters,
                ).fetchall())
        finally:
            connection.close()
        return [self._bar_from_row(row) for row in rows]

    def list_intraday_bars(
        self,
        *,
        symbol: str,
        trade_date: date,
        period_minutes: int,
    ) -> list[StockIntradayKLineBar]:
        """Return cached intraday bars for one stock and completed trade date."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT timestamp, open, high, low, close, volume, amount
                FROM stock_intraday_bars
                WHERE symbol = ? AND trade_date = ? AND period_minutes = ?
                ORDER BY timestamp ASC
                """,
                (symbol, trade_date.isoformat(), period_minutes),
            ).fetchall()
        finally:
            connection.close()

        return [
            StockIntradayKLineBar(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                amount=row["amount"],
            )
            for row in rows
        ]

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
            item.dragon_tiger_source,
            item.popularity_rank,
            item.popularity_rank_change,
            item.popularity_snapshot_at.isoformat() if item.popularity_snapshot_at else None,
            item.popularity_source,
            json.dumps(item.position.model_dump(mode="json"), ensure_ascii=False)
            if item.position
            else None,
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
            json.dumps(prediction.prediction_provenance, ensure_ascii=False, sort_keys=True),
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
            dragon_tiger_source=row["dragon_tiger_source"],
            popularity_rank=row["popularity_rank"],
            popularity_rank_change=row["popularity_rank_change"],
            popularity_snapshot_at=datetime.fromisoformat(row["popularity_snapshot_at"])
            if row["popularity_snapshot_at"]
            else None,
            popularity_source=row["popularity_source"],
            position=json.loads(row["position_json"]) if row["position_json"] else None,
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
            prediction_provenance=json.loads(row["provenance_json"]),
        )


