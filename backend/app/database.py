"""SQLite connection and schema initialization helpers."""

import os
import sqlite3
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = BACKEND_ROOT / "data" / "limituplab.sqlite"


def get_database_path() -> Path:
    """Return the configured SQLite database path."""

    configured_path = os.getenv("LIMITUPLAB_DATABASE_PATH")
    if configured_path:
        return Path(configured_path)
    return DEFAULT_DATABASE_PATH


def connect(database_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection and create the parent directory when needed."""

    path = database_path or get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    """Create database tables and indexes required by the current application."""

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
            market_sentiment TEXT NOT NULL,
            closed_limit INTEGER NOT NULL,
            feature_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (trade_date, symbol)
        )
        """
    )
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
        CREATE TABLE IF NOT EXISTS first_board_outcomes (
            base_trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            next_trade_date TEXT,
            next_open_pct REAL,
            next_high_pct REAL,
            next_close_pct REAL,
            three_day_high_pct REAL,
            three_day_close_pct REAL,
            max_drawdown_3d REAL,
            promoted_to_second_board INTEGER NOT NULL,
            outcome_ready INTEGER NOT NULL,
            outcome_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (base_trade_date, symbol)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_first_board_outcomes_symbol_date
        ON first_board_outcomes (symbol, base_trade_date)
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
    connection.commit()

