import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.agents.capability_contract import available_capability_names
from app.agents.tools import EXTENDED_AGENT_PROFILE, V1_AGENT_PROFILE, V1_CLOSED_MARKET_TOOL_NAMES, V1_DEFERRED_REALTIME_TOOL_NAMES, AgentToolRegistry
from app.repositories import SQLiteFirstBoardRepository
from app.services.sample_data import SAMPLE_EVENTS


class AgentV1ProfileTest(unittest.TestCase):
    # Prepare the isolated fixtures and dependencies shared by the tests in this class.
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"agent-v1-profile-{uuid4().hex}.sqlite"
        )
        self.repository = SQLiteFirstBoardRepository(self.database_path)
        self.addCleanup(self._cleanup_database)

    # Release the temporary resources owned by this test fixture.
    def _cleanup_database(self) -> None:
        for suffix in ("", "-shm", "-wal"):
            Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)

    # Prepare the empty execution fixture or observation used by the surrounding regression

    # Regression scenario: default profile exposes close tools and read only external facts.
    def test_default_profile_exposes_close_tools_and_read_only_external_facts(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LIMITUPLAB_AGENT_PROFILE", None)
            registry = AgentToolRegistry(
                events=SAMPLE_EVENTS,
                first_board_repository=self.repository,
            )

        schema_names = {schema.name for schema in registry.schemas()}
        self.assertEqual(registry.profile, V1_AGENT_PROFILE)
        self.assertEqual(schema_names, set(V1_CLOSED_MARKET_TOOL_NAMES))
        self.assertTrue(schema_names.isdisjoint(V1_DEFERRED_REALTIME_TOOL_NAMES))
        self.assertIn("hot_stock_ranking", {schema.name for schema in registry.schemas()})
        self.assertIn("finance_news", {schema.name for schema in registry.schemas()})
        self.assertIn("stock_news", {schema.name for schema in registry.schemas()})
        self.assertIn("stock_activity", {schema.name for schema in registry.schemas()})
        self.assertIn("sector_performance", {schema.name for schema in registry.schemas()})
        self.assertNotIn("web_search", schema_names)

    # Regression scenario: extended profile preserves deferred v2 tools.
    def test_extended_profile_preserves_deferred_v2_tools(self) -> None:
        registry = AgentToolRegistry(
            events=SAMPLE_EVENTS,
            first_board_repository=self.repository,
            profile=EXTENDED_AGENT_PROFILE,
        )

        schema_names = {schema.name for schema in registry.schemas()}
        self.assertTrue(V1_DEFERRED_REALTIME_TOOL_NAMES.issubset(schema_names))
        self.assertIn("hot_stock_ranking", {schema.name for schema in registry.schemas()})

    # Regression scenario: v1 capability catalog includes read only external workflows.
    def test_v1_capability_catalog_includes_read_only_external_workflows(self) -> None:
        catalog = available_capability_names(V1_CLOSED_MARKET_TOOL_NAMES)

        self.assertIn("first_board_rating", catalog)
        self.assertIn("limit_up_pool", catalog)
        self.assertIn("popularity", catalog)
        self.assertIn("finance_news", catalog)
        self.assertIn("stock_news", catalog)


if __name__ == "__main__":
    unittest.main()
