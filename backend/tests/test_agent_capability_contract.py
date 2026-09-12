import json
import unittest

from app.agents.capability_contract import (
    CAPABILITY_BY_NAME,
    capability_answer_instruction,
    capability_schema_prompt,
    ensure_capability_tool_calls,
    infer_capabilities_from_facts,
    normalize_capabilities,
)


class AgentCapabilityContractTest(unittest.TestCase):


    # Regression scenario: tool plans infer single tool capability.
    def test_tool_plans_infer_single_tool_capability(self) -> None:
        capabilities = normalize_capabilities(
            None,
            tool_calls=[{"name": "finance_news", "arguments": {}}],
        )

        self.assertEqual(capabilities, ("finance_news",))

    # Regression scenario: capability is the single workflow manifest.
    def test_capability_is_the_single_workflow_manifest(self) -> None:
        capability = CAPABILITY_BY_NAME["first_board_rating"]

        self.assertIn("首板评级前10名", capability.examples)
        self.assertEqual(capability.required_tools[0].name, "first_board_ratings")
        self.assertIn("研究评级", capability.answer_guidance)

    # Regression scenario: capability answer guidance is injected progressively.
    def test_capability_answer_guidance_is_injected_progressively(self) -> None:
        guidance = capability_answer_instruction(
            ("market_environment", "popularity")
        )

        self.assertIn("CAPABILITY_RESPONSE_CONTRACTS", guidance)
        self.assertIn("- market_environment:", guidance)
        self.assertNotIn("- popularity:", guidance)

    # Regression scenario: composite capability is recovered from executed facts.
    def test_composite_capability_is_recovered_from_executed_facts(self) -> None:
        capabilities = infer_capabilities_from_facts(
            (),
            {
                "market_summary": {},
                "market_index_trend": {},
                "sector_performance": {},
                "hot_stock_ranking": {},
            },
        )

        self.assertEqual(capabilities, ("market_environment",))

    # Regression scenario: compound capabilities merge required evidence.
    def test_compound_capabilities_merge_required_evidence(self) -> None:
        calls = ensure_capability_tool_calls(
            ("popularity", "limit_up_pool"),
            [{"name": "hot_stock_ranking", "arguments": {"limit": 100}}],
            allowed_tool_names={"hot_stock_ranking", "limit_up_events"},
        )

        self.assertEqual(
            [call["name"] for call in calls],
            ["hot_stock_ranking", "limit_up_events"],
        )
        self.assertEqual(calls[0]["arguments"]["limit"], 100)
        self.assertEqual(calls[0]["arguments"]["source"], "auto")

    # Regression scenario: market environment contract requires four evidence groups.
    def test_market_environment_contract_requires_four_evidence_groups(self) -> None:
        calls = ensure_capability_tool_calls(
            ("market_environment",),
            [],
            allowed_tool_names={
                "market_summary",
                "market_index_trend",
                "sector_performance",
                "hot_stock_ranking",
            },
        )

        self.assertEqual(
            [call["name"] for call in calls],
            [
                "market_summary",
                "market_index_trend",
                "sector_performance",
                "hot_stock_ranking",
            ],
        )
        self.assertTrue(calls[-1]["arguments"]["enrich_performance"])

    # Regression scenario: capability catalog only exposes available workflows.
    def test_capability_catalog_only_exposes_available_workflows(self) -> None:
        payload = json.loads(
            capability_schema_prompt({"hot_stock_ranking", "limit_up_events"})
        )
        names = {item["name"] for item in payload}

        self.assertEqual(names, {"popularity", "limit_up_pool"})


if __name__ == "__main__":
    unittest.main()
