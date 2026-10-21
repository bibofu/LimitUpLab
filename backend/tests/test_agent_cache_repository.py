
from database_support import isolated_database_url, release_database_url
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.repositories import AgentCacheRepository


TEST_TMP_ROOT = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1]))


class AgentCacheRepositoryTest(unittest.TestCase):
    # Prepare the isolated fixtures and dependencies shared by the tests in this class.
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)
        self.database_url = isolated_database_url()

    # Release the test resources and restore the environment after this test scope.
    def tearDown(self) -> None:
        release_database_url(self.database_url)

    # Regression scenario: set and get json before expiry.
    def test_set_and_get_json_before_expiry(self) -> None:
        repository = AgentCacheRepository(database_url=self.database_url)

        repository.set_json(
            cache_key="scope:key",
            scope="scope",
            payload={"answer": "ok", "count": 2},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )

        self.assertEqual(
            repository.get_json("scope:key"),
            {"answer": "ok", "count": 2},
        )

    # Regression scenario: expired json is removed.
    def test_expired_json_is_removed(self) -> None:
        repository = AgentCacheRepository(database_url=self.database_url)
        repository.set_json(
            cache_key="scope:expired",
            scope="scope",
            payload={"answer": "old"},
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

        self.assertIsNone(repository.get_json("scope:expired"))
        self.assertEqual(repository.delete_expired(), 0)


if __name__ == "__main__":
    unittest.main()
