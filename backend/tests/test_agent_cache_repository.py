import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.repositories import SQLiteAgentCacheRepository


TEST_TMP_ROOT = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1]))


class AgentCacheRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)
        self.database_path = TEST_TMP_ROOT / f"agent-cache-test-{uuid4().hex}.sqlite"

    def tearDown(self) -> None:
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def test_set_and_get_json_before_expiry(self) -> None:
        repository = SQLiteAgentCacheRepository(database_path=self.database_path)

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

    def test_expired_json_is_removed(self) -> None:
        repository = SQLiteAgentCacheRepository(database_path=self.database_path)
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
