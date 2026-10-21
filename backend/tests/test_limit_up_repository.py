
from database_support import isolated_database_url, release_database_url
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.repositories import LimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS


TEST_TMP_ROOT = Path(
    os.getenv(
        "LIMITUPLAB_TEST_TMP",
        Path(__file__).resolve().parents[1]
        if os.name == "nt"
        else Path(__file__).resolve().parents[1],
    )
)


# Prepare the temporary database path fixture or observation used by the surrounding regression
# scenario.
@contextmanager
def temporary_database_url():
    database_url = isolated_database_url()
    try:
        yield database_url
    finally:
        release_database_url(database_url)


class LimitUpRepositoryTest(unittest.TestCase):
    def test_latest_events_match_history_filter_and_follow_new_writes(self) -> None:
        with temporary_database_url() as database_url:
            repository = LimitUpRepository(database_url=database_url)
            self.assertEqual(repository.list_latest_events(), [])
            repository.replace_events(SAMPLE_EVENTS)
            history = repository.list_events()
            latest_date = max(event.trade_date for event in history)
            expected = [event for event in history if event.trade_date == latest_date]
            self.assertEqual(repository.list_latest_events(), expected)
            updated = expected[0].model_copy(update={"name": "已更新"})
            repository.upsert_events([updated])
            self.assertEqual(repository.list_latest_events()[0].name, "已更新")
            repository.delete_events_for_date(latest_date)
            self.assertTrue(all(event.trade_date < latest_date for event in repository.list_latest_events()))

    def test_latest_events_preserve_sample_bootstrap(self) -> None:
        with temporary_database_url() as database_url:
            repository = LimitUpRepository(database_url=database_url, seed_if_empty=True)
            events = repository.list_latest_events()
            latest_date = max(event.trade_date for event in SAMPLE_EVENTS)
            self.assertEqual({event.trade_date for event in events}, {latest_date})
            self.assertEqual(len(events), sum(event.trade_date == latest_date for event in SAMPLE_EVENTS))

    # Regression scenario: replace and list events.
    def test_replace_and_list_events(self) -> None:
        with temporary_database_url() as database_url:
            repository = LimitUpRepository(database_url=database_url)

            repository.replace_events(SAMPLE_EVENTS)
            events = repository.list_events()

        self.assertEqual(len(events), len(SAMPLE_EVENTS))
        self.assertEqual(events[0].trade_date.isoformat(), "2026-05-15")
        self.assertEqual(events[0].symbol, "600519")

    # Regression scenario: upsert updates existing event.
    def test_upsert_updates_existing_event(self) -> None:
        with temporary_database_url() as database_url:
            repository = LimitUpRepository(database_url=database_url)
            original = SAMPLE_EVENTS[0]
            updated = original.model_copy(update={"name": "科大讯飞测试"})

            repository.upsert_events([original])
            repository.upsert_events([updated])
            events = repository.list_events()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].name, "科大讯飞测试")

    # Regression scenario: seed if empty bootstraps sample events.
    def test_seed_if_empty_bootstraps_sample_events(self) -> None:
        with temporary_database_url() as database_url:
            repository = LimitUpRepository(
                database_url=database_url,
                seed_if_empty=True,
            )

            events = repository.list_events()

        self.assertEqual(len(events), len(SAMPLE_EVENTS))

    # Regression scenario: delete events for date.
    def test_delete_events_for_date(self) -> None:
        with temporary_database_url() as database_url:
            repository = LimitUpRepository(database_url=database_url)

            repository.replace_events(SAMPLE_EVENTS)
            repository.delete_events_for_date("2026-05-15")
            events = repository.list_events()

        self.assertTrue(all(event.trade_date.isoformat() != "2026-05-15" for event in events))

    # Regression scenario: legacy unclosed event does not keep rolling board count.
    def test_legacy_unclosed_event_does_not_keep_rolling_board_count(self) -> None:
        with temporary_database_url() as database_url:
            repository = LimitUpRepository(database_url=database_url)
            failed_event = SAMPLE_EVENTS[3].model_copy(
                update={"closed_limit": False, "board_height": 6}
            )

            repository.upsert_events([failed_event])
            events = repository.list_events()

        self.assertEqual(events[0].board_height, 1)
        self.assertFalse(events[0].closed_limit)


if __name__ == "__main__":
    unittest.main()
