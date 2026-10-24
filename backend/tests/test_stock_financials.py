"""Financial-statement tool contracts and cumulative/single-quarter semantics."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.agents.runner.tool_view import model_result
from app.agents.tool_payload import payload_of
from app.agents.tools import AgentToolRegistry
from app.collectors.hithink_finance_collector import (
    HithinkBalanceSheetFact,
    HithinkCashFlowStatementFact,
    HithinkIncomeStatementFact,
)
from app.services.stock_financials import build_stock_financials_facts


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


class FakeCollector:
    def __init__(self, *, income=None, balance=None, cash_flow=None) -> None:
        self.income = income or []
        self.balance = balance or []
        self.cash_flow = cash_flow or []

    def collect_a_share_symbol_names(self) -> dict[str, str]:
        return {"600519": "贵州茅台"}

    def collect_income_statements(self, thscode, *, period="quarterly", limit=6):
        return self.income

    def collect_balance_sheets(self, thscode, *, period="quarterly", limit=6):
        return self.balance

    def collect_cash_flow_statements(self, thscode, *, period="quarterly", limit=6):
        return self.cash_flow


def income_rows() -> list[HithinkIncomeStatementFact]:
    return [
        HithinkIncomeStatementFact(
            thscode="600519.SH",
            fiscal_year=2026,
            fiscal_period="Q2",
            report_date=date(2026, 8, 22),
            period_end=date(2026, 6, 30),
            operating_income=90_703_260_964.48,
            net_profit=46_033_330_566.78,
            parent_holder_net_profit=44_516_880_421.86,
            basic_eps=35.57,
            operating_costs=9_473_762_565.88,
            operating_profit=61_411_291_686.27,
        ),
        HithinkIncomeStatementFact(
            thscode="600519.SH",
            fiscal_year=2026,
            fiscal_period="Q1",
            report_date=date(2026, 4, 25),
            period_end=date(2026, 3, 31),
            operating_income=53_909_252_220.51,
            net_profit=28_153_831_489.89,
            parent_holder_net_profit=27_242_512_886.45,
            basic_eps=21.76,
            operating_costs=5_520_729_200.32,
            operating_profit=37_537_008_686.42,
        ),
    ]


def test_income_service_keeps_cumulative_and_derives_single_quarter():
    payload = build_stock_financials_facts(
        symbol="600519",
        statement="income",
        stock_name="贵州茅台",
        collector=FakeCollector(income=income_rows()),
        now=NOW,
    )

    q2, q1 = payload["rows"]
    assert q2["operating_income"] == 90_703_260_964.48
    assert q2["operating_income_single_quarter"] == pytest.approx(36_794_008_743.97)
    assert q2["net_profit_single_quarter"] == pytest.approx(17_879_499_076.89)
    assert q2["parent_holder_net_profit_single_quarter"] == pytest.approx(17_274_367_535.41)
    assert q2["basic_eps_single_quarter"] == pytest.approx(13.81)
    assert q1["operating_income_single_quarter"] == q1["operating_income"]
    assert payload["data_as_of"] == "2026-06-30"
    assert payload["period_basis"].startswith("Q1-Q4为年初至今累计")
    assert payload["complete"] is True
    assert payload["symbol"] == "600519"
    assert payload["thscode"] == "600519.SH"
    assert "贵州茅台" in payload["summary"]
    assert "单季" in payload["summary"]


class RecordingCollector(FakeCollector):
    def __init__(self, *, income=None) -> None:
        super().__init__(income=income)
        self.requested_limits: list[int] = []

    def collect_income_statements(self, thscode, *, period="quarterly", limit=6):
        self.requested_limits.append(limit)
        return self.income[:limit]


def test_service_fetches_one_extra_quarter_so_the_oldest_returned_row_is_derivable():
    q2, q1 = income_rows()
    q3 = HithinkIncomeStatementFact(
        thscode="600519.SH",
        fiscal_year=2026,
        fiscal_period="Q3",
        report_date=date(2026, 10, 25),
        period_end=date(2026, 9, 30),
        operating_income=130_000_000_000.00,
        net_profit=65_000_000_000.00,
        parent_holder_net_profit=63_000_000_000.00,
        basic_eps=50.00,
        operating_costs=13_000_000_000.00,
        operating_profit=88_000_000_000.00,
    )
    collector = RecordingCollector(income=[q3, q2, q1])

    payload = build_stock_financials_facts(
        symbol="600519",
        statement="income",
        limit=2,
        collector=collector,
        now=NOW,
    )

    assert collector.requested_limits == [3]
    assert len(payload["rows"]) == 2
    assert payload["rows"][0]["operating_income_single_quarter"] == pytest.approx(
        130_000_000_000.00 - 90_703_260_964.48
    )
    assert payload["rows"][1]["operating_income_single_quarter"] == pytest.approx(
        36_794_008_743.97
    )
    assert not any("最早季度" in warning for warning in payload["warnings"])


def test_balance_sheet_has_no_single_quarter_fields():
    payload = build_stock_financials_facts(
        symbol="600519",
        statement="balance",
        collector=FakeCollector(balance=[
            HithinkBalanceSheetFact(
                thscode="600519.SH",
                fiscal_year=2026,
                fiscal_period="Q2",
                report_date=date(2026, 8, 22),
                period_end=date(2026, 6, 30),
                assets_total=309_050_784_569.31,
                total_current_assets=260_724_668_103.40,
                non_current_nets_total=48_326_116_465.91,
                cash=53_518_798_979.08,
                accounts_receivable=570_895.04,
                total_debt=46_954_432_394.95,
                holder_equity_total=262_096_352_174.36,
            )
        ]),
        now=NOW,
    )

    row = payload["rows"][0]
    assert row["assets_total"] == 309_050_784_569.31
    assert row["total_debt"] == 46_954_432_394.95
    assert not any(key.endswith("_single_quarter") for key in row)
    assert payload["period_basis"].startswith("资产负债表为期末时点值")


def test_annual_request_does_not_derive_single_quarter():
    payload = build_stock_financials_facts(
        symbol="600519",
        statement="income",
        period="annual",
        collector=FakeCollector(income=[
            HithinkIncomeStatementFact(
                thscode="600519.SH",
                fiscal_year=2025,
                fiscal_period="FY",
                report_date=date(2026, 4, 17),
                period_end=date(2025, 12, 31),
                operating_income=168_838_102_514.79,
                net_profit=85_310_324_833.67,
                parent_holder_net_profit=82_320_067_101.68,
                basic_eps=65.66,
            )
        ]),
        now=NOW,
    )

    assert payload["rows"][0]["fiscal_period"] == "FY"
    assert not any(key.endswith("_single_quarter") for key in payload["rows"][0])
    assert payload["period_basis"].startswith("FY为全年累计")


def test_cash_flow_derives_single_quarter_for_all_additive_fields():
    payload = build_stock_financials_facts(
        symbol="600519",
        statement="cash_flow",
        collector=FakeCollector(cash_flow=[
            HithinkCashFlowStatementFact(
                thscode="600519.SH", fiscal_year=2026, fiscal_period="Q2",
                report_date=date(2026, 8, 22), period_end=date(2026, 6, 30),
                act_cash_flow_net=70_690_750_119.06,
                invest_cash_flow_net=25_640_543_520.60,
                financing_cash_flow_net=-37_944_297_802.12,
                pay_fixed_assets_etc_cash=832_142_752.28,
                pay_dividends_profits_interest_cash=35_044_145_842.75,
                cash_equivalents_net_addition=58_385_486_034.90,
            ),
            HithinkCashFlowStatementFact(
                thscode="600519.SH", fiscal_year=2026, fiscal_period="Q1",
                report_date=date(2026, 4, 25), period_end=date(2026, 3, 31),
                act_cash_flow_net=26_909_891_269.13,
                invest_cash_flow_net=10_000_000_000.00,
                financing_cash_flow_net=-20_000_000_000.00,
                pay_fixed_assets_etc_cash=400_000_000.00,
                pay_dividends_profits_interest_cash=15_000_000_000.00,
                cash_equivalents_net_addition=20_000_000_000.00,
            ),
        ]),
        now=NOW,
    )

    q2 = payload["rows"][0]
    assert q2["act_cash_flow_net_single_quarter"] == pytest.approx(43_780_858_849.93)
    assert q2["invest_cash_flow_net_single_quarter"] == pytest.approx(15_640_543_520.60)
    assert q2["financing_cash_flow_net_single_quarter"] == pytest.approx(-17_944_297_802.12)


def test_empty_statement_reports_missing_without_inventing_zero():
    payload = build_stock_financials_facts(
        symbol="600519",
        statement="income",
        collector=FakeCollector(),
        now=NOW,
    )

    assert payload["rows"] == []
    assert payload["complete"] is False
    assert payload["data_as_of"] is None
    assert payload["data_missing"] == ["hithink_finance:no_disclosed_reports"]
    assert "未取得" in payload["summary"]


@pytest.mark.parametrize("statement", ["metrics", "", "cashflow"])
def test_unknown_statement_is_rejected(statement):
    with pytest.raises(ValueError, match="statement must be"):
        build_stock_financials_facts(
            symbol="600519", statement=statement, collector=FakeCollector(),
        )


def test_registry_tool_result_and_model_view_preserve_identity():
    registry = AgentToolRegistry(
        events=[],
        first_board_repository=SimpleNamespace(database_url=None),
        hithink_collector=FakeCollector(income=income_rows()),
    )

    result = registry.stock_financials("600519", "income")
    payload = payload_of(result)
    view = model_result("stock_financials", result, payload, result.result_status)

    assert result.name == "stock_financials"
    assert result.result_status == "ok"
    assert view["status"] == "ok"
    assert view["stock_name"] == "贵州茅台"
    assert view["row_count"] == 2
    assert view["rows"][0]["operating_income_single_quarter"] == pytest.approx(36_794_008_743.97)
    assert "operating_income_single_quarter" in view["field_definitions"]["rows"]
    assert "period_basis" in view["field_definitions"]["metadata"]
    assert view["summary"] == result.summary
