"""SQLite connection and schema initialization helpers."""

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, TypeVar

from app.config import env_bool


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = BACKEND_ROOT / "data" / "limituplab.sqlite"
CURRENT_SCHEMA_VERSION = 9
DEFAULT_BUSY_TIMEOUT_MS = 5_000
DEFAULT_LOCK_RETRY_ATTEMPTS = 3
DEFAULT_LOCK_RETRY_BASE_DELAY_SECONDS = 0.05
DEFAULT_WAL_AUTOCHECKPOINT_PAGES = 1_000
_connection_setup_lock = threading.Lock()
_schema_initialization_lock = threading.Lock()
Result = TypeVar("Result")


class ResilientSQLiteConnection(sqlite3.Connection):
    """SQLite connection that retries transient lock conflicts safely."""

    lock_retry_attempts: int = DEFAULT_LOCK_RETRY_ATTEMPTS
    lock_retry_base_delay_seconds: float = DEFAULT_LOCK_RETRY_BASE_DELAY_SECONDS

    def execute(self, sql: str, parameters=(), /) -> sqlite3.Cursor:
        return self._retry_locked(super().execute, sql, parameters)

    def executemany(self, sql: str, seq_of_parameters, /) -> sqlite3.Cursor:
        return self._retry_locked(super().executemany, sql, seq_of_parameters)

    def commit(self) -> None:
        self._retry_locked(super().commit)

    def _retry_locked(self, operation: Callable[..., Result], *args) -> Result:
        """Retry only SQLite busy/locked failures with bounded backoff."""

        attempts = max(1, self.lock_retry_attempts)
        for attempt in range(attempts):
            try:
                return operation(*args)
            except sqlite3.OperationalError as error:
                if not _is_lock_conflict(error) or attempt + 1 >= attempts:
                    raise
                time.sleep(
                    self.lock_retry_base_delay_seconds * (2**attempt)
                )
        raise RuntimeError("unreachable SQLite retry state")


def get_database_path() -> Path:
    """Return the configured SQLite database path."""

    configured_path = os.getenv("LIMITUPLAB_DATABASE_PATH")
    if configured_path:
        return Path(configured_path)
    return DEFAULT_DATABASE_PATH


def connect(database_path: Path | None = None) -> sqlite3.Connection:
    """Open one consistently configured, concurrent-safe SQLite connection."""

    path = database_path or get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    busy_timeout_ms = _bounded_int(
        "LIMITUPLAB_SQLITE_BUSY_TIMEOUT_MS",
        DEFAULT_BUSY_TIMEOUT_MS,
        minimum=100,
        maximum=60_000,
    )
    connection = sqlite3.connect(
        path,
        timeout=busy_timeout_ms / 1_000,
        factory=ResilientSQLiteConnection,
    )
    connection.lock_retry_attempts = _bounded_int(
        "LIMITUPLAB_SQLITE_LOCK_RETRY_ATTEMPTS",
        DEFAULT_LOCK_RETRY_ATTEMPTS,
        minimum=1,
        maximum=10,
    )
    connection.lock_retry_base_delay_seconds = _bounded_float(
        "LIMITUPLAB_SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS",
        DEFAULT_LOCK_RETRY_BASE_DELAY_SECONDS,
        minimum=0.0,
        maximum=2.0,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        if env_bool("LIMITUPLAB_SQLITE_WAL_ENABLED", True):
            current_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            if str(current_mode).lower() != "wal":
                # Changing journal mode is persistent and must not race between
                # first connections to a newly created database.
                with _connection_setup_lock:
                    current_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                    if str(current_mode).lower() != "wal":
                        connection.execute("PRAGMA journal_mode = WAL").fetchone()
        connection.execute("PRAGMA synchronous = NORMAL")
        wal_checkpoint_pages = _bounded_int(
            "LIMITUPLAB_SQLITE_WAL_AUTOCHECKPOINT_PAGES",
            DEFAULT_WAL_AUTOCHECKPOINT_PAGES,
            minimum=100,
            maximum=100_000,
        )
        connection.execute(f"PRAGMA wal_autocheckpoint = {wal_checkpoint_pages}")
    except Exception:
        connection.close()
        raise
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    """Apply the schema once using a serialized, cross-process write transaction."""

    if _database_schema_version(connection) >= CURRENT_SCHEMA_VERSION:
        return

    with _schema_initialization_lock:
        if _database_schema_version(connection) >= CURRENT_SCHEMA_VERSION:
            return
        # Legacy callers may have created pre-versioned tables on this
        # connection before asking the initializer to migrate them. The old
        # initializer committed those writes as well, so preserve that contract.
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        try:
            # Another process may have completed the migration while this
            # connection waited for the write lock.
            if _database_schema_version(connection) < CURRENT_SCHEMA_VERSION:
                _apply_schema(connection)
                connection.execute(
                    f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}"
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _apply_schema(connection: sqlite3.Connection) -> None:
    """Create and migrate all tables inside the caller's schema transaction."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS limit_up_events (
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            first_limit_time TEXT NOT NULL,
            last_limit_time TEXT NOT NULL,
            seal_count INTEGER NOT NULL,
            break_count INTEGER NOT NULL,
            closed_limit INTEGER NOT NULL,
            board_height INTEGER NOT NULL,
            amount REAL NOT NULL,
            turnover_rate REAL NOT NULL,
            industry TEXT NOT NULL,
            concept TEXT NOT NULL,
            next_open_pct REAL NOT NULL,
            next_high_pct REAL NOT NULL,
            next_close_pct REAL NOT NULL,
            three_day_return_pct REAL NOT NULL,
            five_day_return_pct REAL NOT NULL,
            continued_next_day INTEGER NOT NULL,
            PRIMARY KEY (trade_date, symbol)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_limit_up_events_trade_date
        ON limit_up_events (trade_date)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS first_board_features (
            trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            first_limit_minutes INTEGER NOT NULL,
            first_limit_bucket TEXT NOT NULL,
            break_count INTEGER NOT NULL,
            seal_count INTEGER NOT NULL,
            turnover_rate REAL NOT NULL,
            turnover_bucket TEXT NOT NULL,
            amount REAL NOT NULL,
            amount_log REAL NOT NULL,
            amount_bucket TEXT NOT NULL,
            industry TEXT NOT NULL,
            concept TEXT NOT NULL,
            same_industry_limit_up_count INTEGER NOT NULL,
            same_concept_limit_up_count INTEGER NOT NULL,
            market_limit_up_count INTEGER NOT NULL,
            market_first_board_count INTEGER NOT NULL,
            market_failed_limit_up_rate REAL NOT NULL,
            market_failed_rate_bucket TEXT NOT NULL,
            market_max_board_height INTEGER NOT NULL,
            closed_limit INTEGER NOT NULL,
            feature_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (trade_date, symbol)
        )
        """
    )
    _drop_column_if_exists(connection, "first_board_features", "market_sentiment")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_first_board_features_date
        ON first_board_features (trade_date)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_first_board_features_recall
        ON first_board_features (
            first_limit_bucket,
            break_count,
            turnover_bucket,
            amount_bucket,
            market_failed_rate_bucket
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_first_board_features_symbol_date
        ON first_board_features (symbol, trade_date)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_daily_bars (
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            amount REAL NOT NULL,
            change_pct REAL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (symbol, trade_date)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_stock_daily_bars_symbol_date
        ON stock_daily_bars (symbol, trade_date)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_intraday_bars (
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            period_minutes INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            amount REAL NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (symbol, trade_date, period_minutes, timestamp)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_stock_intraday_bars_lookup
        ON stock_intraday_bars (symbol, trade_date, period_minutes, timestamp)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS first_board_outcomes (
            base_trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            next_trade_date TEXT,
            next_open_pct REAL,
            next_high_pct REAL,
            next_close_pct REAL,
            next_open_to_high_pct REAL,
            next_open_to_low_pct REAL,
            next_open_to_close_pct REAL,
            three_day_high_pct REAL,
            three_day_close_pct REAL,
            max_drawdown_3d REAL,
            three_day_open_to_high_pct REAL,
            three_day_open_to_close_pct REAL,
            max_drawdown_from_next_open_3d REAL,
            promoted_to_second_board INTEGER NOT NULL,
            next_day_ready INTEGER NOT NULL DEFAULT 0,
            three_day_ready INTEGER NOT NULL DEFAULT 0,
            outcome_ready INTEGER NOT NULL,
            outcome_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (base_trade_date, symbol)
        )
        """
    )
    _ensure_columns(
        connection,
        "first_board_outcomes",
        {
            "next_open_to_high_pct": "REAL",
            "next_open_to_low_pct": "REAL",
            "next_open_to_close_pct": "REAL",
            "three_day_open_to_high_pct": "REAL",
            "three_day_open_to_close_pct": "REAL",
            "max_drawdown_from_next_open_3d": "REAL",
            "next_day_ready": "INTEGER NOT NULL DEFAULT 0",
            "three_day_ready": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_first_board_outcomes_symbol_date
        ON first_board_outcomes (symbol, base_trade_date)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS first_board_enrichment_snapshots (
            trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            kline_bar_count INTEGER NOT NULL,
            return_5d_pct REAL,
            return_20d_pct REAL,
            return_60d_pct REAL,
            distance_20d_high_pct REAL,
            distance_60d_high_pct REAL,
            volume_ratio_5d REAL,
            volatility_20d REAL,
            close_above_ma20 INTEGER,
            ma_alignment TEXT NOT NULL,
            listing_date TEXT,
            listing_age_days INTEGER,
            float_market_cap REAL,
            float_market_cap_source TEXT,
            recent_limit_up_count_20d INTEGER NOT NULL,
            recent_limit_up_count_60d INTEGER NOT NULL,
            industry_first_board_count INTEGER NOT NULL,
            industry_continued_board_count INTEGER NOT NULL,
            industry_failed_count INTEGER NOT NULL,
            industry_max_board_height INTEGER NOT NULL,
            industry_first_limit_rank INTEGER,
            previous_first_board_promotion_rate REAL,
            market_first_board_seal_rate REAL,
            dragon_tiger_on_list INTEGER NOT NULL,
            dragon_tiger_net_buy_amount REAL,
            dragon_tiger_buy_amount REAL,
            dragon_tiger_sell_amount REAL,
            dragon_tiger_reason TEXT,
            dragon_tiger_source TEXT,
            popularity_rank INTEGER,
            popularity_rank_change INTEGER,
            popularity_snapshot_at TEXT,
            popularity_source TEXT,
            position_json TEXT,
            data_missing_json TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (trade_date, symbol)
        )
        """
    )
    _ensure_columns(
        connection,
        "first_board_enrichment_snapshots",
        {
            "dragon_tiger_source": "TEXT",
            "popularity_source": "TEXT",
            "position_json": "TEXT",
        },
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_first_board_enrichment_symbol_date
        ON first_board_enrichment_snapshots (symbol, trade_date DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            session_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner_updated
        ON chat_sessions (owner_id, archived_at, updated_at DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            message_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL,
            run_id TEXT,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
        ON chat_messages (session_id, created_at ASC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_session_memories (
            session_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            memory_version TEXT NOT NULL,
            summary TEXT NOT NULL,
            research_goal TEXT NOT NULL,
            stock_symbols_json TEXT NOT NULL,
            topics_json TEXT NOT NULL,
            date_scope TEXT,
            constraints_json TEXT NOT NULL,
            unresolved_questions_json TEXT NOT NULL,
            summarized_message_count INTEGER NOT NULL,
            last_message_id TEXT,
            generation_mode TEXT NOT NULL,
            model TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_session_memories_owner_updated
        ON chat_session_memories (owner_id, updated_at DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_type TEXT NOT NULL,
            status TEXT NOT NULL,
            intent TEXT,
            tool_calls_json TEXT NOT NULL,
            input_json TEXT NOT NULL,
            output_json TEXT,
            error_message TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_runs_session_started
        ON agent_runs (session_id, started_at DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_usage_events (
            usage_id TEXT PRIMARY KEY,
            run_id TEXT,
            session_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            ip_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            model TEXT,
            llm_call_count INTEGER NOT NULL DEFAULT 0,
            failed_llm_call_count INTEGER NOT NULL DEFAULT 0,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            token_usage_complete INTEGER NOT NULL DEFAULT 0,
            planner_prompt_chars INTEGER NOT NULL DEFAULT 0,
            answer_prompt_chars INTEGER NOT NULL DEFAULT 0,
            answer_chars INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd REAL NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            duration_ms INTEGER,
            error_message TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_usage_owner_started
        ON agent_usage_events (owner_id, started_at DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_usage_started
        ON agent_usage_events (started_at DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_pipeline_runs (
            run_id TEXT PRIMARY KEY,
            trade_date TEXT NOT NULL,
            trigger TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL,
            report_json TEXT,
            error_message TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daily_pipeline_runs_started
        ON daily_pipeline_runs (started_at DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daily_pipeline_runs_date_status
        ON daily_pipeline_runs (trade_date, status)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_review_snapshots (
            as_of_date TEXT PRIMARY KEY,
            start_date TEXT NOT NULL,
            report_json TEXT NOT NULL,
            generated_by TEXT NOT NULL,
            generated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daily_review_snapshots_generated
        ON daily_review_snapshots (generated_at DESC)
        """
    )
    _ensure_agent_predictions_schema(connection)
    _ensure_agent_live_prediction_snapshots_schema(connection)
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_predictions_date
        ON agent_predictions (trade_date)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_predictions_symbol_date
        ON agent_predictions (symbol, trade_date)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_cache (
            cache_key TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_cache_scope_expires
        ON agent_cache (scope, expires_at)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_news_items (
            item_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            published_at TEXT NOT NULL,
            source TEXT NOT NULL,
            url TEXT NOT NULL,
            item_type TEXT NOT NULL,
            relevance_score REAL NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_stock_news_symbol_published
        ON stock_news_items (symbol, published_at DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_news_sync_state (
            symbol TEXT NOT NULL,
            source TEXT NOT NULL,
            last_attempt_at TEXT NOT NULL,
            last_success_at TEXT,
            error_message TEXT,
            PRIMARY KEY (symbol, source)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS recommendation_intelligence_snapshots (
            refresh_id TEXT PRIMARY KEY,
            refreshed_at TEXT NOT NULL,
            status TEXT NOT NULL,
            response_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recommendation_intelligence_refreshed
        ON recommendation_intelligence_snapshots (refreshed_at DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS scoring_policies (
            version TEXT PRIMARY KEY,
            parent_version TEXT,
            status TEXT NOT NULL,
            factor_weights_json TEXT NOT NULL,
            source TEXT NOT NULL,
            rationale_json TEXT NOT NULL,
            training_start_date TEXT,
            training_end_date TEXT,
            created_at TEXT NOT NULL,
            activated_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_scoring_policies_status_created
        ON scoring_policies (status, created_at DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS scoring_policy_runs (
            run_id TEXT PRIMARY KEY,
            champion_version TEXT NOT NULL,
            challenger_version TEXT NOT NULL,
            promotion_eligible INTEGER NOT NULL,
            activated INTEGER NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_scoring_policy_runs_created
        ON scoring_policy_runs (created_at DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS first_board_discovery_snapshots (
            data_as_of TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            target_trade_date TEXT,
            response_json TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (data_as_of, strategy_version)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_first_board_discovery_created
        ON first_board_discovery_snapshots (created_at DESC)
        """
    )
    _repair_legacy_failed_pool_board_heights(connection)


def _database_schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def _is_lock_conflict(error: sqlite3.OperationalError) -> bool:
    message = str(error).lower()
    return "locked" in message or "busy" in message


def _bounded_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(os.getenv(name, "").strip())
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _bounded_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(os.getenv(name, "").strip())
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _repair_legacy_failed_pool_board_heights(connection: sqlite3.Connection) -> None:
    """Remove rolling limit-up counts previously stored as consecutive boards."""

    connection.execute(
        """
        UPDATE limit_up_events
        SET board_height = 1
        WHERE closed_limit = 0 AND board_height > 1
        """
    )


def _ensure_columns(
    connection: sqlite3.Connection,
    table_name: str,
    definitions: dict[str, str],
) -> None:
    """Add backward-compatible columns missing from an existing SQLite table."""

    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, definition in definitions.items():
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
            )


def _drop_column_if_exists(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> None:
    """Drop an obsolete SQLite column while preserving existing table data."""

    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name in columns:
        connection.execute(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")


def _ensure_agent_predictions_schema(connection: sqlite3.Connection) -> None:
    """Migrate prediction snapshots to source-aware immutable uniqueness."""

    table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'agent_predictions'"
    ).fetchone()
    if table is None:
        _create_agent_predictions_table(connection)
        return

    table_sql = str(table["sql"] or "").lower().replace(" ", "")
    source_aware_unique = (
        "unique(trade_date,symbol,scoring_version,prediction_source)" in table_sql
    )
    if source_aware_unique:
        return

    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(agent_predictions)").fetchall()
    }
    source_expression = (
        "COALESCE(prediction_source, 'historical_backtest')"
        if "prediction_source" in columns
        else "'historical_backtest'"
    )
    as_of_expression = (
        "COALESCE(data_as_of, trade_date)" if "data_as_of" in columns else "trade_date"
    )
    connection.execute("ALTER TABLE agent_predictions RENAME TO agent_predictions_legacy")
    _create_agent_predictions_table(connection)
    connection.execute(
        f"""
        INSERT INTO agent_predictions (
            prediction_id, trade_date, symbol, name, score, rating, confidence,
            scoring_version, prediction_source, data_as_of, facts_json,
            reasons_json, risks_json, created_at
        )
        SELECT
            prediction_id, trade_date, symbol, name, score, rating, confidence,
            scoring_version, {source_expression}, {as_of_expression}, facts_json,
            reasons_json, risks_json, created_at
        FROM agent_predictions_legacy
        """
    )
    connection.execute("DROP TABLE agent_predictions_legacy")


def _create_agent_predictions_table(connection: sqlite3.Connection) -> None:
    """Create the source-aware immutable prediction snapshot table."""

    connection.execute(
        """
        CREATE TABLE agent_predictions (
            prediction_id TEXT PRIMARY KEY,
            trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            score REAL NOT NULL,
            rating TEXT NOT NULL,
            confidence REAL NOT NULL,
            scoring_version TEXT NOT NULL,
            prediction_source TEXT NOT NULL,
            data_as_of TEXT NOT NULL,
            facts_json TEXT NOT NULL,
            reasons_json TEXT NOT NULL,
            risks_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(trade_date, symbol, scoring_version, prediction_source)
        )
        """
    )


def _ensure_agent_live_prediction_snapshots_schema(
    connection: sqlite3.Connection,
) -> None:
    """Create daily live batches and adopt the earliest legacy live snapshot."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_live_prediction_snapshots (
            trade_date TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL UNIQUE,
            scoring_version TEXT NOT NULL,
            data_as_of TEXT NOT NULL,
            top_limit INTEGER NOT NULL,
            prediction_count INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    existing_dates = {
        row["trade_date"]
        for row in connection.execute(
            "SELECT trade_date FROM agent_live_prediction_snapshots"
        ).fetchall()
    }
    live_rows = connection.execute(
        """
        SELECT *
        FROM agent_predictions
        WHERE prediction_source = 'live'
        ORDER BY trade_date ASC, created_at ASC, scoring_version ASC, score DESC, symbol ASC
        """
    ).fetchall()
    rows_by_date: dict[str, list[sqlite3.Row]] = {}
    for row in live_rows:
        rows_by_date.setdefault(row["trade_date"], []).append(row)

    for trade_date, rows in rows_by_date.items():
        if trade_date in existing_dates:
            continue
        first = rows[0]
        scoring_version = first["scoring_version"]
        created_at = first["created_at"]
        selected = [
            row
            for row in rows
            if row["scoring_version"] == scoring_version
            and row["created_at"] == created_at
        ]
        payload = {
            "trade_date": trade_date,
            "candidates": [
                {
                    "facts": _json_object(row["facts_json"]),
                    "score": row["score"],
                    "rating": row["rating"],
                    "confidence": row["confidence"],
                    "score_breakdown": [],
                    "reasons": _json_list(row["reasons_json"]),
                    "risks": _json_list(row["risks_json"]),
                }
                for row in selected
            ],
            "filtered_out": [],
            "universe_count": len(selected),
            "generated_by": scoring_version,
            "snapshot_source": "live",
            "data_as_of": first["data_as_of"],
            "snapshot_created_at": created_at,
        }
        snapshot_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        content_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO agent_live_prediction_snapshots (
                trade_date, snapshot_id, scoring_version, data_as_of,
                top_limit, prediction_count, snapshot_json, content_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_date,
                f"live-{trade_date}-{content_hash[:12]}",
                scoring_version,
                first["data_as_of"],
                len(selected),
                len(selected),
                snapshot_json,
                content_hash,
                created_at,
            ),
        )
        connection.execute(
            """
            DELETE FROM agent_predictions
            WHERE trade_date = ?
              AND prediction_source = 'live'
              AND NOT (scoring_version = ? AND created_at = ?)
            """,
            (trade_date, scoring_version, created_at),
        )


def _json_object(value: str) -> dict[str, object]:
    """Decode a legacy JSON object without aborting the schema migration."""

    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _json_list(value: str) -> list[object]:
    """Decode a legacy JSON list without aborting the schema migration."""

    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []
