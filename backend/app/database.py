import os
import sqlite3
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = BACKEND_ROOT / "data" / "limituplab.sqlite"


def get_database_path() -> Path:
    configured_path = os.getenv("LIMITUPLAB_DATABASE_PATH")
    if configured_path:
        return Path(configured_path)
    return DEFAULT_DATABASE_PATH


def connect(database_path: Path | None = None) -> sqlite3.Connection:
    path = database_path or get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
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
    connection.commit()
