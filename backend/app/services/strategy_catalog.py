"""Single source of truth for user-visible post-limit strategies."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.post_limit import POST_LIMIT_RULE_VERSION
from app.services.scoring_policy import DEFAULT_SCORING_POLICY_VERSION
from app.strategy_models import StrategyDefinition


@dataclass(frozen=True)
class _StrategySpec:
    strategy_id: str
    name: str
    description: str
    lifecycle_stage: str
    shape: str | None
    maturity: str
    output_type: str


_SPECS = (
    _StrategySpec("relay_one_to_two", "一进二接力", "从收盘首板中形成次日接力研究排名。", "首板收盘至次日", None, "forward_validation", "ranked_research"),
    _StrategySpec("high_drawdown", "高位回撤", "观察近期涨停后从局部运行高点明显回撤的股票。", "涨停后1至4日", "high_drawdown", "exploratory", "observation_pool"),
    _StrategySpec("volume_consolidation", "横盘缩量", "观察近期涨停后窄幅整理且成交量收缩的股票。", "涨停后2至4日", "volume_consolidation", "exploratory", "observation_pool"),
    _StrategySpec("pullback_stabilizing", "回撤企稳", "观察涨停后回撤并出现收盘修复证据的股票。", "涨停后2至4日", "pullback_stabilizing", "exploratory", "observation_pool"),
    _StrategySpec("strong_nonconsecutive", "强势不连板", "观察断板后仍维持涨停收盘上方结构的股票。", "涨停后2至4日", "strong_nonconsecutive", "exploratory", "observation_pool"),
    _StrategySpec("broken_board_repair", "断板修复", "观察连板断开后重新突破前一日高点的股票。", "连板后2至4日", "broken_board_repair", "exploratory", "observation_pool"),
    _StrategySpec("second_to_third", "二进三", "观察当日收盘二板且前一交易日为首板的股票。", "二板收盘", "second_to_third", "exploratory", "observation_pool"),
)

STRATEGY_IDS = tuple(item.strategy_id for item in _SPECS)


def strategy_shape(strategy_id: str) -> str | None:
    spec = _spec(strategy_id)
    return spec.shape


def strategy_id_for_shape(shape: str) -> str:
    for spec in _SPECS:
        if spec.shape == shape:
            return spec.strategy_id
    raise KeyError(shape)


def resolve_strategy_id(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "一进二": "relay_one_to_two",
        "一进二接力": "relay_one_to_two",
        "2进3": "second_to_third",
        "二进三": "second_to_third",
    }
    if normalized in aliases:
        return aliases[normalized]
    for spec in _SPECS:
        if normalized in {spec.strategy_id, spec.name.lower(), str(spec.shape or "")}:
            return spec.strategy_id
    raise KeyError(value)


def get_strategy_definition(strategy_id: str) -> StrategyDefinition:
    spec = _spec(strategy_id)
    ranked = spec.output_type == "ranked_research"
    return StrategyDefinition(
        strategy_id=spec.strategy_id,
        name=spec.name,
        description=spec.description,
        version=DEFAULT_SCORING_POLICY_VERSION if ranked else POST_LIMIT_RULE_VERSION,
        lifecycle_stage=spec.lifecycle_stage,
        maturity=spec.maturity,
        output_type=spec.output_type,
        universe=(
            "沪深一进二规则支持范围内的收盘首板"
            if ranked else "最近发生过可验证收盘涨停的沪深主板股票"
        ),
        cutoff_contract="仅使用指定 data_as_of 当日收盘及此前已完成交易日数据",
        required_data=(
            ["涨停事件", "首板Facts", "评分输入", "Outcome"]
            if ranked else ["涨停事件", "连续20日OHLCV", "交易日历"]
        ),
        missing_data_policy="关键字段缺失时显式记录，不以默认值补造候选",
        outcome_metrics=["D+1开盘至收盘", "D+3路径", "D+5路径", "MAE", "MFE"],
        promotion_gate=(
            "至少60个成熟Outcome日，样本外优于基线且风险指标不退化"
            if ranked else "完成不可变前向样本积累后方可申请排名资格"
        ),
    )


def list_strategy_definitions() -> list[StrategyDefinition]:
    return [get_strategy_definition(item.strategy_id) for item in _SPECS]


def _spec(strategy_id: str) -> _StrategySpec:
    for spec in _SPECS:
        if spec.strategy_id == strategy_id:
            return spec
    raise KeyError(strategy_id)
