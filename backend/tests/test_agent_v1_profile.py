
from database_support import isolated_database_url, release_database_url
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.agents.tools import EXTENDED_AGENT_PROFILE, V1_AGENT_PROFILE, V1_CLOSED_MARKET_TOOL_NAMES, AgentToolRegistry, TOOL_SCHEMAS
from app.repositories import FirstBoardRepository
from app.services.sample_data import SAMPLE_EVENTS


class AgentV1ProfileTest(unittest.TestCase):
    # Prepare the isolated fixtures and dependencies shared by the tests in this class.
    def setUp(self) -> None:
        self.database_url = (
            isolated_database_url()
        )
        self.repository = FirstBoardRepository(self.database_url)
        self.addCleanup(self._cleanup_database)

    # Release the temporary resources owned by this test fixture.
    def _cleanup_database(self) -> None:
        for suffix in ("", "-shm", "-wal"):
            release_database_url(self.database_url)

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
        self.assertNotEqual(schema_names, {schema.name for schema in TOOL_SCHEMAS})
        self.assertNotIn("stock_financials", schema_names)
        self.assertIn("index_search", schema_names)
        self.assertIn("index_quote", schema_names)
        self.assertIn("index_constituents", schema_names)
        self.assertIn("hot_stock_ranking", schema_names)
        self.assertIn("finance_news", schema_names)
        self.assertIn("stock_news", schema_names)
        self.assertIn("stock_activity", schema_names)
        self.assertIn("sector_performance", schema_names)
        self.assertNotIn("web_search", schema_names)
        self.assertNotIn("remote_limit_up_pool", schema_names)

    # Regression scenario: extended profile exposes the same current catalog.
    def test_extended_profile_exposes_the_current_catalog(self) -> None:
        registry = AgentToolRegistry(
            events=SAMPLE_EVENTS,
            first_board_repository=self.repository,
            profile=EXTENDED_AGENT_PROFILE,
        )

        schema_names = {schema.name for schema in registry.schemas()}
        self.assertEqual(schema_names, {schema.name for schema in TOOL_SCHEMAS})
        self.assertTrue(set(V1_CLOSED_MARKET_TOOL_NAMES).issubset(schema_names))
        self.assertIn("stock_financials", schema_names)
        self.assertIn("hot_stock_ranking", schema_names)

if __name__ == "__main__":
    unittest.main()
