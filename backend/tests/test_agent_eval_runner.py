import unittest
from pathlib import Path

from app.agents.eval_runner import load_eval_cases, run_agent_eval_suite
from app.routers.agents import get_agent_eval_report
from app.services.sample_data import SAMPLE_EVENTS


class AgentEvalRunnerTest(unittest.TestCase):
    def test_fixture_eval_suite_passes_against_deterministic_agent(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "agent_eval_cases.json"
        suite = run_agent_eval_suite(
            cases=load_eval_cases(fixture_path),
            events=SAMPLE_EVENTS,
        )

        failure_report = {
            result.case_id: result.failures
            for result in suite.results
            if not result.passed
        }
        self.assertTrue(suite.ok, failure_report)
        self.assertEqual(suite.total, 11)

    def test_eval_report_route_returns_quality_summary(self) -> None:
        report = get_agent_eval_report()

        self.assertEqual(report.mode, "offline")
        self.assertEqual(report.total, 11)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.pass_rate, 1.0)
        self.assertTrue(report.results)


if __name__ == "__main__":
    unittest.main()
