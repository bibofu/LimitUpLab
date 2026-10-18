import unittest
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.agents.tools import AgentToolRegistry
from app.collectors.sector_collector import SectorDailyRow, SectorRankingSnapshot, SectorSpotRow
from app.models import SectorPerformanceFacts
from app.services.sector_performance import build_sector_performance


class SectorPerformanceTest(unittest.TestCase):
    # Prepare the isolated fixtures and dependencies shared by the tests in this class.
    def setUp(self) -> None:
        self.today = date.today()
        self.spot_rows = [
            SectorSpotRow(
                sector_name="厨卫电器",
                rank=1,
                change_pct=4.57,
                amount_yi=21.64,
                net_inflow_yi=2.02,
                up_count=7,
                down_count=2,
                leader_name="亿田智能",
                leader_price=26.66,
                leader_change_pct=19.98,
                source="fake-spot",
            ),
            SectorSpotRow(
                sector_name="半导体",
                rank=87,
                change_pct=-7.44,
                amount_yi=4019.38,
                net_inflow_yi=-277.96,
                up_count=4,
                down_count=181,
                leader_name="斯达半导",
                leader_price=100.31,
                leader_change_pct=10.0,
                source="fake-spot",
            ),
        ]
        self.history_rows = [
            SectorDailyRow(
                trade_date=self.today - timedelta(days=25 - index),
                close=100 + index,
                change_pct=1.0,
                source="fake-history",
            )
            for index in range(26)
        ]

    # Regression scenario: named sector returns ranking breadth and trend.
    def test_named_sector_returns_ranking_breadth_and_trend(self) -> None:
        # The inline callback supplies the fixture value or replacement behavior used by this
        # test; it is evaluated only when the code under test calls it.
        response = build_sector_performance(
            "半导体板块",
            trade_date=self.today,
            spot_collector=lambda: self.spot_rows,
            history_collector=lambda _name, _start, _end: self.history_rows,
        )

        self.assertEqual(response.sector_name, "半导体")
        self.assertEqual(response.rank, 87)
        self.assertEqual(response.change_pct, -7.44)
        self.assertEqual(response.up_count, 4)
        self.assertEqual(response.down_count, 181)
        self.assertIsNotNone(response.return_5d_pct)
        self.assertEqual(response.sources, ["fake-spot", "fake-history"])

    # Regression scenario: empty sector returns market ranking.
    def test_empty_sector_returns_market_ranking(self) -> None:
        # The inline callback supplies the fixture value or replacement behavior used by this
        # test; it is evaluated only when the code under test calls it.
        response = build_sector_performance(
            spot_collector=lambda: self.spot_rows,
            history_collector=lambda _name, _start, _end: [],
        )

        self.assertIsNone(response.sector_name)
        self.assertEqual(response.top_sectors[0].sector_name, "厨卫电器")
        self.assertEqual(response.bottom_sectors[0].sector_name, "半导体")

    def test_dated_ranking_uses_complete_history_without_live_snapshot_or_named_probes(self) -> None:
        target = date(2026, 10, 16)
        historical_rows = [replace(row, source="dated-history") for row in self.spot_rows]
        ranking = Mock(return_value=SectorRankingSnapshot(
            target, date(2026, 10, 15), historical_rows, 2, [],
        ))
        spot, history = Mock(), Mock()
        response = build_sector_performance(
            trade_date=target, ranking_collector=ranking,
            spot_collector=spot, history_collector=history,
        )
        ranking.assert_called_once_with(target)
        spot.assert_not_called()
        history.assert_not_called()
        self.assertTrue(response.data_fresh)
        self.assertTrue(response.ranking_complete)
        self.assertEqual(response.data_as_of, target)
        self.assertEqual(response.previous_trade_date, date(2026, 10, 15))
        self.assertEqual(response.covered_sector_count, 2)
        self.assertEqual(response.top_sectors[0].sector_name, "厨卫电器")
        self.assertEqual(response.sources, ["dated-history"])

    def test_partial_history_cannot_present_subset_as_global_leaderboard(self) -> None:
        target = date(2026, 10, 16)
        response = build_sector_performance(
            trade_date=target,
            ranking_collector=lambda _: SectorRankingSnapshot(
                target, date(2026, 10, 15), self.spot_rows[:1], 2,
                ["sector_history_coverage:1/2"],
            ),
        )
        self.assertFalse(response.data_fresh)
        self.assertFalse(response.ranking_complete)
        self.assertEqual(response.covered_sector_count, 1)
        self.assertEqual(response.sector_count, 2)
        self.assertEqual(response.top_sectors, [])
        self.assertEqual(response.bottom_sectors, [])
        self.assertIn("sector_history_coverage:1/2", response.data_missing)

    def test_named_historical_sector_without_history_is_explicitly_missing(self) -> None:
        response = build_sector_performance(
            "半导体", trade_date=self.today - timedelta(days=2),
            spot_collector=lambda: self.spot_rows, history_collector=lambda *_: [],
        )
        self.assertFalse(response.data_fresh)
        self.assertIsNone(response.change_pct)
        self.assertIsNone(response.rank)
        self.assertEqual(response.top_sectors, [])
        self.assertIn("sector_history_unavailable", response.data_missing)

    def test_agent_resolves_latest_local_date_before_loading_sector_evidence(self) -> None:
        target = date(2026, 10, 16)
        registry = AgentToolRegistry(
            [SimpleNamespace(trade_date=target), SimpleNamespace(trade_date=date(2026, 10, 15))],
            first_board_repository=object(), hithink_collector=object(),
        )
        facts = SectorPerformanceFacts(
            trade_date=target, data_as_of=target, data_fresh=True, sector_count=2,
            covered_sector_count=2, ranking_complete=True,
        )
        with patch("app.agents.tools.build_sector_performance", return_value=facts) as build:
            result = registry.sector_performance()
        build.assert_called_once_with(sector=None, trade_date=target)
        self.assertEqual(result.result_status, "ok")
        self.assertEqual(result.output.data_as_of, target)

    def test_agent_keeps_incomplete_sector_result_partial(self) -> None:
        target = date(2026, 10, 16)
        registry = AgentToolRegistry([], first_board_repository=object(), hithink_collector=object())
        facts = SectorPerformanceFacts(
            trade_date=target, data_as_of=target, data_fresh=False, sector_count=2,
            covered_sector_count=1, ranking_complete=False,
            data_missing=["sector_history_coverage:1/2"],
        )
        with patch("app.agents.tools.build_sector_performance", return_value=facts):
            result = registry.sector_performance(trade_date=target)
        self.assertEqual(result.result_status, "partial")
        self.assertFalse(result.data_fresh)
        self.assertEqual(result.trace().result.status, "partial")
        self.assertEqual(result.trace_output["data_missing"], ["sector_history_coverage:1/2"])

    # Regression scenario: military alias returns all matching industry subsectors.
    def test_military_alias_returns_all_matching_industry_subsectors(self) -> None:
        military_rows = [
            SectorSpotRow(
                sector_name="军工装备",
                rank=45,
                change_pct=-0.59,
                amount_yi=395.7,
                net_inflow_yi=-5.37,
                up_count=28,
                down_count=53,
                leader_name="博云新材",
                leader_price=22.0,
                leader_change_pct=10.0,
                source="fake-industry",
            ),
            SectorSpotRow(
                sector_name="军工电子",
                rank=66,
                change_pct=-0.92,
                amount_yi=128.14,
                net_inflow_yi=-10.71,
                up_count=14,
                down_count=46,
                leader_name="科思科技",
                leader_price=32.25,
                leader_change_pct=2.87,
                source="fake-industry",
            ),
        ]

        # The inline callback supplies the fixture value or replacement behavior used by this
        # test; it is evaluated only when the code under test calls it.
        response = build_sector_performance(
            "国防军工",
            trade_date=self.today,
            spot_collector=lambda: military_rows,
            history_collector=lambda _name, _start, _end: [],
        )

        self.assertEqual(response.sector_name, "军工")
        self.assertEqual(response.sector_type, "industry_group")
        self.assertEqual(
            [item.sector_name for item in response.matched_sectors],
            ["军工装备", "军工电子"],
        )

    # Regression scenario: ai alias falls back to concept ranking.
    def test_ai_alias_falls_back_to_concept_ranking(self) -> None:
        concept_rows = [
            SectorSpotRow(
                sector_name="人工智能",
                rank=12,
                change_pct=1.26,
                amount_yi=None,
                net_inflow_yi=None,
                up_count=91,
                down_count=43,
                leader_name="概念龙头",
                leader_price=None,
                leader_change_pct=9.8,
                source="fake-concept",
            )
        ]

        # The inline callback supplies the fixture value or replacement behavior used by this
        # test; it is evaluated only when the code under test calls it.
        response = build_sector_performance(
            "AI",
            trade_date=self.today,
            spot_collector=lambda: self.spot_rows,
            history_collector=lambda _name, _start, _end: [],
            concept_spot_collector=lambda: concept_rows,
            concept_history_collector=lambda _name, _start, _end: self.history_rows,
        )

        self.assertEqual(response.sector_name, "人工智能")
        self.assertEqual(response.sector_type, "concept")
        self.assertEqual(response.rank, 12)
        self.assertEqual(response.change_pct, 1.26)
        self.assertEqual(response.sources, ["fake-concept", "fake-history"])

    # Regression scenario: named concept uses history when spot source is unavailable.
    def test_named_concept_uses_history_when_spot_source_is_unavailable(self) -> None:
        # The inline callback supplies the fixture value or replacement behavior used by this
        # test; it is evaluated only when the code under test calls it.
        response = build_sector_performance(
            "AI",
            trade_date=self.today,
            spot_collector=lambda: self.spot_rows,
            history_collector=lambda _name, _start, _end: [],
            concept_spot_collector=lambda: (_ for _ in ()).throw(
                RuntimeError("concept spot unavailable")
            ),
            concept_history_collector=lambda name, _start, _end: (
                self.history_rows if name == "人工智能" else []
            ),
        )

        self.assertEqual(response.sector_name, "人工智能")
        self.assertEqual(response.sector_type, "concept")
        self.assertEqual(response.sector_count, 0)
        self.assertIn("concept-spot-unavailable", response.sources)


if __name__ == "__main__":
    unittest.main()
