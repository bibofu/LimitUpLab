
from database_support import isolated_database_url, release_database_url
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from psycopg import IntegrityError

from app.database import begin_write, connect, initialize_database
from app.storage.schema import HEAD_REVISION


class DatabaseConcurrencyTest(unittest.TestCase):
    # Prepare the isolated fixtures and dependencies shared by the tests in this class.
    def setUp(self) -> None:
        self.database_url = isolated_database_url()
        self.addCleanup(release_database_url, self.database_url)

    # Connection settings and deferred foreign keys remain enforced in PostgreSQL.
    def test_connection_has_bounded_timeouts_and_foreign_keys(self) -> None:
        connection = connect(self.database_url)
        try:
            initialize_database(connection)
            statement_timeout = connection.execute("SHOW statement_timeout").fetchone()[0]
            lock_timeout = connection.execute("SHOW lock_timeout").fetchone()[0]
            timezone = connection.execute("SHOW timezone").fetchone()[0]
            schema_version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            connection.execute("INSERT INTO react_events(run_id,event,payload_json) VALUES ('absent','progress','{}')")
            with self.assertRaises(IntegrityError):
                connection.commit()
            connection.rollback()
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM react_events").fetchone()[0], 0)
        finally:
            connection.close()

        self.assertEqual(statement_timeout, "30s")
        self.assertEqual(lock_timeout, "10s")
        self.assertEqual(timezone, "UTC")
        self.assertEqual(schema_version, HEAD_REVISION)

    # Regression scenario: concurrent schema initialization is idempotent.
    def test_concurrent_schema_initialization_is_idempotent(self) -> None:
        barrier = threading.Barrier(8)

        # Prepare the initialize from worker fixture or observation used by the surrounding
        # regression scenario.
        def initialize_from_worker() -> str:
            barrier.wait(timeout=5)
            connection = connect(self.database_url)
            try:
                initialize_database(connection)
                return connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=8) as executor:
            # The inline callback supplies the fixture value or replacement behavior used by this
            # test; it is evaluated only when the code under test calls it.
            versions = list(executor.map(lambda _index: initialize_from_worker(), range(8)))

        connection = connect(self.database_url)
        try:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT table_name AS name FROM information_schema.tables WHERE table_schema=current_schema()"
                ).fetchall()
            }
        finally:
            connection.close()

        self.assertEqual(versions, [HEAD_REVISION] * 8)
        self.assertIn("limit_up_events", tables)
        self.assertIn("agent_usage_events", tables)

    # A PostgreSQL reader sees committed rows while another transaction is writing.
    def test_reader_is_not_blocked_by_uncommitted_writer(self) -> None:
        writer = connect(self.database_url)
        initialize_database(writer)
        writer.execute("CREATE TABLE concurrency_probe (value TEXT PRIMARY KEY)")
        writer.commit()
        begin_write(writer)
        writer.execute("INSERT INTO concurrency_probe VALUES ('uncommitted')")

        reader = connect(self.database_url)
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

    # A competing writer waits for the existing transaction lock.
    def test_competing_writer_waits_until_lock_is_released(self) -> None:
        first = connect(self.database_url)
        initialize_database(first)
        first.execute("CREATE TABLE concurrency_probe (value TEXT PRIMARY KEY)")
        first.commit()
        begin_write(first)
        first.execute("INSERT INTO concurrency_probe VALUES ('first')")
        attempting_write = threading.Event()

        # Prepare the write second fixture or observation used by the surrounding regression
        # scenario.
        def write_second() -> None:
            second = connect(self.database_url)
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

        connection = connect(self.database_url)
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

if __name__ == "__main__":
    unittest.main()
