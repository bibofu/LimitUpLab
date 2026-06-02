import unittest

from app.agents import build_daily_review, build_daily_review_facts
from app.services.analysis import summarize_market
from app.services.sample_data import SAMPLE_EVENTS


class DailyReviewAgentTest(unittest.TestCase):
    def test_build_daily_review_facts(self) -> None:
        summary = summarize_market(SAMPLE_EVENTS)
        facts = build_daily_review_facts(summary=summary, events=SAMPLE_EVENTS)

        self.assertEqual(facts.trade_date.isoformat(), "2026-05-15")
        self.assertEqual(facts.limit_up_count, 5)
        self.assertEqual(facts.unstable_count, 4)
        self.assertEqual(facts.unclosed_count, 2)
        self.assertEqual(facts.board_ladder[0].board_height, 4)
        self.assertIn("封板稳定性偏弱", facts.risk_signals)

    def test_build_daily_review_narrative_uses_facts(self) -> None:
        summary = summarize_market(SAMPLE_EVENTS)
        review = build_daily_review(summary=summary, events=SAMPLE_EVENTS)

        self.assertEqual(review.facts.sentiment, "cooling")
        self.assertIn("2026-05-15 短线复盘", review.narrative)
        self.assertIn("涨停事件 5 个", review.narrative)
        self.assertIn("不构成投资建议", review.narrative)


if __name__ == "__main__":
    unittest.main()
