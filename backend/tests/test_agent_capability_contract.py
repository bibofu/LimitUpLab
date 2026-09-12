import unittest

from app.agents.capability_contract import CAPABILITY_BY_NAME, available_capability_names


class AgentCapabilityContractTest(unittest.TestCase):


    # Regression scenario: capability is the single workflow manifest.
    def test_capability_is_the_single_workflow_manifest(self) -> None:
        capability = CAPABILITY_BY_NAME["first_board_rating"]

        self.assertIn("首板评级前10名", capability.examples)
        self.assertEqual(capability.required_tools[0].name, "first_board_ratings")
        self.assertIn("研究评级", capability.answer_guidance)


    # Regression scenario: capability catalog only exposes available workflows.
    def test_capability_catalog_only_exposes_available_workflows(self) -> None:
        names = set(available_capability_names({"hot_stock_ranking", "limit_up_events"}))

        self.assertEqual(names, {"popularity", "limit_up_pool"})


if __name__ == "__main__":
    unittest.main()
