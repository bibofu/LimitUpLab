import unittest
from datetime import date, time

from app.agents import build_first_board_ratings
from app.models import LimitUpEvent
from app.services.first_board_critic import build_first_board_critic
from app.services.sample_data import SAMPLE_EVENTS


def make_event(
    symbol: str,
    name: str,
    *,
    board_height: int = 1,
    closed_limit: bool = True,
    amount: float = 800_000_000,
) -> LimitUpEvent:
    """Create a compact first-board event for filter tests."""

    return LimitUpEvent(
        symbol=symbol,
        name=name,
        trade_date=date(2026, 5, 16),
        first_limit_time=time(9, 40),
        last_limit_time=time(9, 40),
        seal_count=1,
        break_count=0,
        closed_limit=closed_limit,
        board_height=board_height,
        amount=amount,
        turnover_rate=6.0,
        industry="机器人",
        concept="机器人",
        next_open_pct=0.0,
        next_high_pct=0.0,
        next_close_pct=0.0,
        three_day_return_pct=0.0,
        five_day_return_pct=0.0,
        continued_next_day=False,
    )


class FirstBoardAgentTest(unittest.TestCase):
    def test_build_first_board_ratings_filters_latest_candidates(self) -> None:
        response = build_first_board_ratings(SAMPLE_EVENTS)

        self.assertEqual(response.trade_date.isoformat(), "2026-05-15")
        self.assertEqual([item.facts.symbol for item in response.candidates], ["301489"])
        self.assertEqual(response.universe_count, 5)
        self.assertTrue(
            any(
                result.symbol == "603083" and "收盘未封住" in result.excluded_reasons
                for result in response.filtered_out
            )
        )

    def test_rating_has_breakdown_reasons_risks_and_confidence(self) -> None:
        response = build_first_board_ratings(SAMPLE_EVENTS)
        rating = response.candidates[0]

        self.assertEqual(rating.rating, "C")
        self.assertGreater(rating.score, 0)
        self.assertLess(rating.confidence, 0.9)
        self.assertGreaterEqual(len(rating.score_breakdown), 5)
        self.assertIn("listing_date", rating.facts.data_missing)
        self.assertTrue(rating.reasons)
        self.assertTrue(rating.risks)

    def test_filter_excludes_special_boards_and_small_amount(self) -> None:
        events = [
            make_event("002001", "普通股票"),
            make_event("688001", "科创样本"),
            make_event("430001", "北交样本"),
            make_event("002002", "*ST样本"),
            make_event("002003", "小额样本", amount=50_000_000),
        ]

        response = build_first_board_ratings(events)

        self.assertEqual([item.facts.symbol for item in response.candidates], ["002001"])
        reasons = {
            result.symbol: result.excluded_reasons
            for result in response.filtered_out
        }
        self.assertIn("科创板股票", reasons["688001"])
        self.assertIn("北交所股票", reasons["430001"])
        self.assertIn("ST 或退市风险警示", reasons["002002"])
        self.assertIn("成交额过小", reasons["002003"])

    def test_output_avoids_investment_advice_terms(self) -> None:
        response = build_first_board_ratings(SAMPLE_EVENTS)
        rendered = response.model_dump_json()

        forbidden_terms = ["买入", "卖出", "仓位", "目标价", "收益承诺"]
        for term in forbidden_terms:
            self.assertNotIn(term, rendered)

    def test_first_board_critic_challenges_rating_without_changing_score(self) -> None:
        ratings = build_first_board_ratings(SAMPLE_EVENTS)
        rating = ratings.candidates[0]

        critic = build_first_board_critic(
            events=SAMPLE_EVENTS,
            symbol=rating.facts.symbol,
            trade_date=rating.facts.trade_date,
            similar_limit=0,
        )

        self.assertEqual(critic.symbol, rating.facts.symbol)
        self.assertEqual(critic.score, rating.score)
        self.assertEqual(critic.rating, rating.rating)
        self.assertLessEqual(critic.suggested_confidence, critic.original_confidence)
        self.assertTrue(critic.counter_evidence)
        self.assertTrue(critic.review_questions)
        self.assertIn("listing_date", critic.missing_data)


if __name__ == "__main__":
    unittest.main()
