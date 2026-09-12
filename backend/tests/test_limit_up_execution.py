"""The same event query must mean the same thing on every execution path."""

import pytest

from app.agents.query_contract import QUERY_CONTRACT_VERSION
from app.agents.query_contract_eval import (
    QueryContractEvalCase,
    query_contract_eval_report,
    run_query_contract_eval_suite,
)


# Build the AgentToolRegistry fixture used by the surrounding regression scenario.


# Prepare the empty execution fixture or observation used by the surrounding regression scenario.


# Regression scenario: eval report uses actual contract version even for empty suite.
@pytest.mark.parametrize("cases", [[], [QueryContractEvalCase("version", "今天涨停股票有哪些", {})]])
def test_eval_report_uses_actual_contract_version_even_for_empty_suite(cases):
    report = query_contract_eval_report(run_query_contract_eval_suite(cases))
    assert report["version"] == QUERY_CONTRACT_VERSION
    assert all(item["actual"]["version"] == report["version"] for item in report["results"])
