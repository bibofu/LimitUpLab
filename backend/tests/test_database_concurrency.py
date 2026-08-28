import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.database import CURRENT_SCHEMA_VERSION, connect, initialize_database


class DatabaseConcurrencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"database-concurrency-{uuid4().hex}.sqlite"
        )
        self.addCleanup(self._remove_database_files)
        self.environment = patch.dict(
            os.environ,
            {
                "LIMITUPLAB_SQLITE_WAL_ENABLED": "true",
                "LIMITUPLAB_SQLITE_BUSY_TIMEOUT_MS": "100",
                "LIMITUPLAB_SQLITE_LOCK_RETRY_ATTEMPTS": "5",
                "LIMITUPLAB_SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS": "0.02",
                "LIMITUPLAB_SQLITE_WAL_AUTOCHECKPOINT_PAGES": "200",
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_connection_enables_wal_busy_timeout_and_foreign_keys(self) -> None:
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
            synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(journal_mode, "wal")
        self.assertEqual(foreign_keys, 1)
        self.assertEqual(busy_timeout, 100)
        self.assertEqual(synchronous, 1)  # NORMAL
        self.assertEqual(schema_version, CURRENT_SCHEMA_VERSION)

    def test_concurrent_schema_initialization_is_idempotent(self) -> None:
        barrier = threading.Barrier(8)

        def initialize_from_worker() -> int:
            barrier.wait(timeout=5)
            connection = connect(self.database_path)
            try:
                initialize_database(connection)
                return connection.execute("PRAGMA user_version").fetchone()[0]
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=8) as executor:
            versions = list(executor.map(lambda _index: initialize_from_worker(), range(8)))

        connection = connect(self.database_path)
        try:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            connection.close()

        self.assertEqual(versions, [CURRENT_SCHEMA_VERSION] * 8)
        self.assertIn("limit_up_events", tables)
        self.assertIn("agent_usage_events", tables)

    def test_wal_reader_is_not_blocked_by_uncommitted_writer(self) -> None:
        writer = connect(self.database_path)
        initialize_database(writer)
        writer.execute("CREATE TABLE concurrency_probe (value TEXT PRIMARY KEY)")
        writer.commit()
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO concurrency_probe VALUES ('uncommitted')")

        reader = connect(self.database_path)
        try:
            started_at = time.monotonic()
            count = reader.execute("SELECT COUNT(*) FROM concurrency_probe").fetchone()[0]
            duration = time.monotonic() - started_at
        finally:
            reader.close()
            writer.rollback()
            writer.close()

        self.assertEqual(count, 0)
        self.assertLess(duration, 0.5)

    def test_competing_writer_retries_until_lock_is_released(self) -> None:
        first = connect(self.database_path)
        initialize_database(first)
        first.execute("CREATE TABLE concurrency_probe (value TEXT PRIMARY KEY)")
        first.commit()
        first.execute("BEGIN IMMEDIATE")
        first.execute("INSERT INTO concurrency_probe VALUES ('first')")
        attempting_write = threading.Event()

        def write_second() -> None:
            second = connect(self.database_path)
            try:
                initialize_database(second)
                attempting_write.set()
                second.execute("INSERT INTO concurrency_probe VALUES ('second')")
                second.commit()
            finally:
                second.close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(write_second)
            self.assertTrue(attempting_write.wait(timeout=2))
            time.sleep(0.25)
            first.commit()
            future.result(timeout=3)
        first.close()

        connection = connect(self.database_path)
        try:
            values = [
                row["value"]
                for row in connection.execute(
                    "SELECT value FROM concurrency_probe ORDER BY value"
                ).fetchall()
            ]
        finally:
            connection.close()

        self.assertEqual(values, ["first", "second"])

    def _remove_database_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
