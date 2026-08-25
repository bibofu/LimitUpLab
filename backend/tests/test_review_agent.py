import unittest
from datetime import date, datetime, timezone

from app.agents.review_agent import (
    _build_feature_comparison,
    _build_promotion_comparisons,
    build_review_agent_report,
)
from app.models import AgentEvaluationItem, AgentPrediction, ReviewAgentPick
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


class FakeReviewLLMProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls.append((system_prompt, user_prompt))
        if "planner" in system_prompt.lower():
            return LLMResult(
                content=(
                    '{"tool_calls":['
                    '{"name":"daily_high_score_picks"},'
                    '{"name":"pick_outcomes"},'
                    '{"name":"compare_success_failure_features"}'
                    "]}"
                ),
                model="fake-review",
                provider="fake",
            )
        return LLMResult(
            content=(
                '{"main_findings":["高分首板样本需要持续追踪兑现率"],'
                '"successful_patterns":["成功样本通常有更强后续高点"],'
                '"failed_patterns":["失败样本需要复盘市场环境和题材持续性"],'
                '"scoring_bias":["可能高估了首封时间"],'
                '"adjustment_suggestions":["降低弱市场环境下的置信度"],'
                '"confidence":0.77}'
            ),
            model="fake-review",
            provider="fake",
        )


class ReviewAgentTest(unittest.TestCase):
    def test_review_agent_uses_llm_planner_and_tools(self) -> None:
        provider = FakeReviewLLMProvider()

        report = build_review_agent_report(
            events=SAMPLE_EVENTS,
            start_date=date(2026, 5, 15),
            end_date=date(2026, 5, 15),
            min_score=70,
            provider=provider,
        )

        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(report.generated_by, "review-agent-tool-use-v4")
        self.assertEqual(report.confidence, 0.77)
        self.assertTrue(report.main_findings)
        self.assertEqual(
            [trace.name for trace in report.tool_results],
            [
                "daily_high_score_picks",
                "pick_outcomes",
                "compare_success_failure_features",
            ],
        )

    def test_feature_comparison_describes_success_and_failure_groups(self) -> None:
        evaluations: list[AgentEvaluationItem] = []
        predictions: dict[str, AgentPrediction] = {}
        for index in range(6):
            is_success = index < 3
            prediction_id = f"prediction-{index}"
            label = "success" if is_success else "miss"
            evaluations.append(
                AgentEvaluationItem(
                    prediction_id=prediction_id,
                    trade_date=date(2026, 8, 18),
                    symbol=f"60000{index}",
                    name=f"样本{index}",
                    score=88 if is_success else 84,
                    rating="A",
                    confidence=0.8,
                    prediction_source="live",
                    data_as_of=date(2026, 8, 18),
                    evaluation_label=label,
                    outcome_ready=True,
                    promoted_to_second_board=is_success,
                    next_open_to_close_pct=4 if is_success else -4,
                    max_drawdown_from_next_open_3d=-3 if is_success else -10,
                    lesson="测试复盘",
                    scoring_suggestion="测试建议",
                )
            )
            predictions[prediction_id] = AgentPrediction(
                prediction_id=prediction_id,
                trade_date=date(2026, 8, 18),
                symbol=f"60000{index}",
                name=f"样本{index}",
                score=88 if is_success else 84,
                rating="A",
                confidence=0.8,
                scoring_version="test-v1",
                prediction_source="live",
                data_as_of=date(2026, 8, 18),
                facts_json={
                    "first_limit_time": "09:30:00" if is_success else "10:00:00",
                    "break_count": 0 if is_success else 1,
                    "turnover_rate": 8,
                    "industry": "机械设备" if is_success else "医药生物",
                    "concept": "机器人" if is_success else "创新药",
                    "same_industry_limit_up_count": 6 if is_success else 2,
                    "enrichment": {
                        "float_market_cap": (
                            (4_000_000_000 + index * 1_000_000_000)
                            if is_success
                            else (10_000_000_000 + index * 1_000_000_000)
                        ),
                        "position": {
                            "primary": {
                                "label": "低位启动首板" if is_success else "高位震荡首板",
                            },
                        },
                        "return_20d_pct": 20 if is_success else 5,
                        "volume_ratio_5d": 1.8 if is_success else 0.9,
                    },
                },
                reasons=["测试理由"],
                risks=["测试风险"],
                created_at=datetime.now(timezone.utc),
            )

        comparison = _build_feature_comparison(evaluations, predictions)

        self.assertEqual(comparison["success_count"], 3)
        self.assertEqual(comparison["failed_count"], 3)
        successful_text = "".join(comparison["successful_patterns"])
        failed_text = "".join(comparison["failed_patterns"])
        self.assertTrue(comparison["successful_patterns"][0].startswith("选股画像"))
        self.assertIn("位置类型 低位启动首板 3只", successful_text)
        self.assertIn("主要题材 机器人 3只", successful_text)
        self.assertIn("主要行业 机械设备 3只", successful_text)
        self.assertIn("流通市值中位数 50.0 亿元", successful_text)
        self.assertIn("09:30", successful_text)
        self.assertIn("行业涨停平均 6.0 只", successful_text)
        self.assertIn("5 日量比 1.80", successful_text)
        self.assertIn("晋级率 100.0%", successful_text)
        self.assertIn("10:00", failed_text)
        self.assertIn("主要题材 创新药 3只", failed_text)
        self.assertIn("位置类型 高位震荡首板 3只", failed_text)
        self.assertIn("流通市值中位数 140.0 亿元", failed_text)
        self.assertIn("三日最大回撤平均 -10.00%", failed_text)

    def test_top10_promotion_is_compared_with_same_day_market_cohort(self) -> None:
        template = SAMPLE_EVENTS[2]

        def event(symbol: str, trade_date: date, height: int):
            return template.model_copy(
                update={
                    "symbol": symbol,
                    "name": f"股票{symbol}",
                    "trade_date": trade_date,
                    "board_height": height,
                    "closed_limit": True,
                }
            )

        base_date = date(2026, 8, 20)
        next_date = date(2026, 8, 21)
        events = [
            event("000001", base_date, 1),
            event("000002", base_date, 1),
            event("000003", base_date, 1),
            event("000001", next_date, 2),
            event("000002", next_date, 2),
        ]
        picks = [
            ReviewAgentPick(
                trade_date=base_date,
                symbol=symbol,
                name=f"股票{symbol}",
                score=90 - index,
                rating="A",
                confidence=0.8,
                prediction_source="live",
                data_as_of=base_date,
                evaluation_label="success" if symbol == "000001" else "miss",
                outcome_ready=True,
                promoted_to_second_board=symbol == "000001",
            )
            for index, symbol in enumerate(("000001", "000003"))
        ]

        comparisons = _build_promotion_comparisons(
            events=events,
            picks=picks,
            end_date=next_date,
        )

        self.assertEqual(len(comparisons), 1)
        comparison = comparisons[0]
        self.assertTrue(comparison.outcome_ready)
        self.assertEqual(comparison.next_trade_date, next_date)
        self.assertEqual(comparison.top_pick_promoted_count, 1)
        self.assertEqual(comparison.top_pick_sample_size, 2)
        self.assertEqual(comparison.top_pick_promotion_rate, 0.5)
        self.assertEqual(comparison.market_promoted_count, 2)
        self.assertEqual(comparison.market_first_board_sample_size, 3)
        self.assertEqual(comparison.market_promotion_rate, 0.6667)
        self.assertEqual(comparison.promotion_rate_delta, -0.1667)


if __name__ == "__main__":
    unittest.main()
