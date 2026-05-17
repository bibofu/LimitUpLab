import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.repositories import SQLiteLimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS


TEST_TMP_ROOT = Path(
    os.getenv(
        "LIMITUPLAB_TEST_TMP",
        Path(__file__).resolve().parents[1]
        if os.name == "nt"
        else Path(__file__).resolve().parents[1],
    )
)


@contextmanager
def temporary_database_path():
    database_path = TEST_TMP_ROOT / f"limituplab-test-{uuid4().hex}.sqlite"
    try:
        yield database_path
    finally:
        for path in (
            database_path,
            database_path.with_name(f"{database_path.name}-wal"),
            database_path.with_name(f"{database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)


class SQLiteLimitUpRepositoryTest(unittest.TestCase):
    def test_replace_and_list_events(self) -> None:
        with temporary_database_path() as database_path:
            repository = SQLiteLimitUpRepository(database_path=database_path)

            repository.replace_events(SAMPLE_EVENTS)
            events = repository.list_events()

        self.assertEqual(len(events), len(SAMPLE_EVENTS))
        self.assertEqual(events[0].trade_date.isoformat(), "2026-05-15")
        self.assertEqual(events[0].symbol, "600519")

    def test_upsert_updates_existing_event(self) -> None:
        with temporary_database_path() as database_path:
            repository = SQLiteLimitUpRepository(database_path=database_path)
            original = SAMPLE_EVENTS[0]
            updated = original.model_copy(update={"name": "科大讯飞测试"})

            repository.upsert_events([original])
            repository.upsert_events([updated])
            events = repository.list_events()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].name, "科大讯飞测试")

    def test_seed_if_empty_bootstraps_sample_events(self) -> None:
        with temporary_database_path() as database_path:
            repository = SQLiteLimitUpRepository(
                database_path=database_path,
                seed_if_empty=True,
            )

            events = repository.list_events()

        self.assertEqual(len(events), len(SAMPLE_EVENTS))

    def test_delete_events_for_date(self) -> None:
        with temporary_database_path() as database_path:
            repository = SQLiteLimitUpRepository(database_path=database_path)

            repository.replace_events(SAMPLE_EVENTS)
            repository.delete_events_for_date("2026-05-15")
            events = repository.list_events()

        self.assertTrue(all(event.trade_date.isoformat() != "2026-05-15" for event in events))


if __name__ == "__main__":
    unittest.main()
