"""Documented output contracts for market, event, rating and stock tools.

Phase 1 keeps the existing tool implementations and payloads unchanged. These
models describe the fields the model receives; ``tool_output_contracts`` turns
them into a compact ``field_definitions`` map.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OutputModel(BaseModel):
    """Base for documentation models; provider extensions must not break them."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Market summary and index trend
# ---------------------------------------------------------------------------


class HotConceptRow(OutputModel):
    name: str = Field(description="概念名称")
    limit_up_count: int = Field(description="该概念收盘涨停家数")
    intraday_opened_count: int = Field(description="该概念盘中曾开板家数")


class MarketSummaryPayload(OutputModel):
    trade_date: date = Field(description="最新本地交易日")
    limit_up_count: int = Field(description="收盘封住涨停的股票数")
    first_board_count: int = Field(description="收盘封住的首板股票数")
    continued_board_count: int = Field(description="收盘封住的连板股票数")
    intraday_opened_count: int = Field(description="盘中曾开板的涨停股数")
    intraday_opened_rate: float = Field(
        description="盘中曾开板股数占涨停总数的比例", json_schema_extra={"unit": "%"}
    )
    unsealed_count: int = Field(description="收盘未回封的炸板股数")
    unsealed_rate: float = Field(
        description="收盘未回封股数占涨停总数的比例", json_schema_extra={"unit": "%"}
    )
    limit_down_count: int | None = Field(
        default=None, description="跌停股数；未查询跌停池时为null"
    )
    limit_down_source: str | None = Field(
        default=None, description="跌停数据来源；未查询时为null"
    )
    max_board_height: int = Field(description="当日最高连板高度")
    hot_industries: list[str] = Field(description="涨停股集中的行业")
    hot_concepts: list[HotConceptRow] = Field(
        description="涨停概念热度，每项含name、limit_up_count、intraday_opened_count"
    )


class MarketIndexRow(OutputModel):
    name: str = Field(description="指数名称")
    symbol: str = Field(description="指数代码")
    start_date: date = Field(description="区间第一个交易日")
    end_date: date = Field(description="区间最后一个交易日")
    start_close: float = Field(description="区间首个收盘点位")
    end_close: float = Field(description="区间最后收盘点位")
    change_pct: float | None = Field(
        default=None,
        description="截止交易日相对上一交易日的点位变化；不是区间收益",
        json_schema_extra={"unit": "%"},
    )
    return_pct: float = Field(
        description="区间累计点位涨跌幅", json_schema_extra={"unit": "%"}
    )
    max_drawdown_pct: float = Field(
        description="区间内峰谷最大回撤", json_schema_extra={"unit": "%"}
    )
    positive_days: int = Field(description="区间上涨交易日数")
    negative_days: int = Field(description="区间下跌交易日数")
    points: list[dict[str, Any]] = Field(
        description="逐日收盘序列，每项含trade_date、close、change_pct"
    )
    source: str = Field(description="指数数据来源")


class MarketIndexPayload(OutputModel):
    requested_days: int = Field(description="请求的交易日窗口长度")
    requested_end_date: date = Field(description="请求的截止交易日")
    data_as_of: date = Field(description="实际数据截止交易日")
    data_fresh: bool = Field(description="数据是否覆盖请求的截止交易日")
    indices: list[MarketIndexRow] = Field(description="三大指数走势")


# ---------------------------------------------------------------------------
# Daily promotion and post-limit research
# ---------------------------------------------------------------------------


class DailyBoardPromotionRow(OutputModel):
    trade_date: date = Field(description="本次晋级观察交易日")
    previous_trade_date: date = Field(description="用于分母的前一交易日")
    sample_size: int = Field(description="前一交易日收盘涨停股票数")
    promoted_count: int = Field(description="本次晋级一板的股票数")
    probability: float = Field(description="总晋级率", json_schema_extra={"unit": "%"})
    first_board_sample_size: int = Field(description="前一交易日首板样本数")
    first_board_promoted_count: int = Field(description="首板晋级二板股票数")
    first_board_probability: float | None = Field(
        default=None, description="首板到二板晋级率", json_schema_extra={"unit": "%"}
    )
    continued_board_sample_size: int = Field(description="前一交易日连板样本数")
    continued_board_promoted_count: int = Field(description="连板继续晋级股票数")
    continued_board_probability: float | None = Field(
        default=None, description="连板梯队晋级率", json_schema_extra={"unit": "%"}
    )
    buckets: list[dict[str, Any]] = Field(
        description="按板数分组的晋级桶，每项含from_board_height、to_board_height、sample_size、promoted_count、probability"
    )
    promoted_stocks: list[dict[str, Any]] = Field(
        description="晋级成功股票，每项含symbol、name、industry、concept、from_board_height、to_board_height、first_limit_time、break_count及开盘表现"
    )
    first_board_opening_evaluable_count: int = Field(
        description="首板晋级中可评价次日开盘类型的样本数"
    )
    first_board_high_open_count: int = Field(description="首板晋级中高开样本数")
    first_board_flat_open_count: int = Field(description="首板晋级中平开样本数")
    first_board_low_open_count: int = Field(description="首板晋级中低开样本数")
    first_board_opening_missing_symbols: list[str] = Field(
        description="缺少次日开盘数据、未参与开盘类型统计的股票代码"
    )


class PostLimitCandidateRow(OutputModel):
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    anchor_date: str = Field(description="作为形态锚点的收盘涨停日")
    board_height: int | None = Field(
        default=None, description="锚点日的连板高度，1为首板"
    )
    industry: str = Field(description="所属行业")
    concept: str = Field(description="所属概念/题材")
    signal_date: str = Field(description="本次观察的截止交易日")
    anchor_age: int = Field(description="截止日相对锚点日的交易日数")
    anchor_close: float = Field(description="锚点日收盘价", json_schema_extra={"unit": "元"})
    close: float = Field(description="截止日收盘价", json_schema_extra={"unit": "元"})
    anchor_change_pct: float = Field(
        description="截止日收盘价相对锚点日收盘价的涨跌幅",
        json_schema_extra={"unit": "%"},
    )
    peak_date: str = Field(description="锚点后运行高点日期")
    peak_price: float = Field(description="锚点后运行高点价格", json_schema_extra={"unit": "元"})
    peak_drawdown_pct: float = Field(
        description="截止日收盘价相对运行高点的回撤", json_schema_extra={"unit": "%"}
    )
    range_low: float = Field(description="锚点后区间最低价", json_schema_extra={"unit": "元"})
    range_high: float = Field(description="锚点后区间最高价", json_schema_extra={"unit": "元"})
    range_pct: float = Field(
        description="锚点后区间振幅", json_schema_extra={"unit": "%"}
    )
    volume_ratio: float = Field(
        description="锚点后日均成交量相对锚点日成交量的倍数；不是量比指标"
    )
    latest_change_pct: float = Field(
        description="截止日相对前一交易日收盘的涨跌幅", json_schema_extra={"unit": "%"}
    )
    close_position_pct: float = Field(
        description="截止日收盘价在当日最低到最高区间中的位置",
        json_schema_extra={"unit": "%"},
    )
    source: str = Field(description="截止日K线来源")
    latest_close_above_previous: bool = Field(description="截止日收盘是否高于前一日收盘")
    latest_close_above_previous_high: bool = Field(
        description="截止日收盘是否高于前一日最高价"
    )
    two_closes_rising: bool = Field(description="最近连续两个收盘价是否抬升")
    matched_shapes: list[str] = Field(description="命中的涨停后形态代码")
    matched_shape_labels: list[str] = Field(description="命中的涨停后形态中文名")
    reasons: list[str] = Field(description="命中理由")
    risks: list[str] = Field(description="形态风险提示")


class PostLimitScreenPayload(OutputModel):
    rule_version: str = Field(description="形态规则版本")
    generated_at: str = Field(description="结果生成时间")
    data_as_of: str = Field(description="数据截止交易日")
    latest_data_date: str | None = Field(
        default=None, description="本地最新完整日K日期"
    )
    market: str = Field(description="市场范围")
    query_contract: dict[str, Any] = Field(description="本次查询参数")
    status: str = Field(description="结果状态")
    snapshot_kind: str = Field(description="结果类型，当前为重算观察")
    shape: str | None = Field(default=None, description="请求的单一形态")
    shapes: list[str] = Field(description="请求的形态集合")
    shape_labels: dict[str, str] = Field(description="形态代码到中文名的映射")
    rules: dict[str, Any] = Field(description="本次使用的形态阈值")
    pool_count: int = Field(description="候选池股票数")
    evaluable_count: int = Field(description="可完成形态评价的股票数")
    coverage_ratio: float = Field(
        description="可评价覆盖率", json_schema_extra={"unit": "%"}
    )
    pending_data_count: int = Field(description="因数据缺口未评价的股票数")
    matched_count: int = Field(description="命中形态的股票数")
    returned_count: int = Field(description="本次返回的股票数")
    candidates: list[PostLimitCandidateRow] = Field(description="命中形态的候选")
    exclusions: dict[str, int] = Field(description="被排除原因及数量")
    data_missing: list[str] = Field(description="数据缺口代码")
    warnings: list[str] = Field(description="结果边界说明")


class PostLimitPathRow(OutputModel):
    trade_date: str = Field(description="交易日")
    day: str = Field(description="相对锚点的T+N标签")
    open: float = Field(description="开盘价", json_schema_extra={"unit": "元"})
    high: float = Field(description="最高价", json_schema_extra={"unit": "元"})
    low: float = Field(description="最低价", json_schema_extra={"unit": "元"})
    close: float = Field(description="收盘价", json_schema_extra={"unit": "元"})
    volume: float = Field(description="成交量，沿用日K来源单位")
    change_from_anchor_close_pct: float = Field(
        description="收盘价相对锚点日收盘价的涨跌幅", json_schema_extra={"unit": "%"}
    )
    drawdown_from_running_peak_pct: float = Field(
        description="收盘价相对运行高点的回撤", json_schema_extra={"unit": "%"}
    )
    volume_vs_anchor: float = Field(description="成交量相对锚点日成交量的倍数")
    states: list[str] = Field(description="当日路径状态标签")


class PostLimitPathPayload(OutputModel):
    rule_version: str = Field(description="形态规则版本")
    generated_at: str = Field(description="结果生成时间")
    data_as_of: str = Field(description="数据截止交易日")
    latest_data_date: str | None = Field(default=None, description="本地最新完整日K日期")
    market: str = Field(description="市场范围")
    query_contract: dict[str, Any] = Field(description="本次查询参数")
    status: str = Field(description="结果状态")
    symbol: str | None = Field(default=None, description="股票代码")
    name: str | None = Field(default=None, description="股票名称")
    anchor: dict[str, Any] | None = Field(
        description="涨停锚点，含symbol、name、anchor_date、board_height、industry、concept"
    )
    metrics: dict[str, Any] | None = Field(
        description="锚点相对指标，字段含义同post_limit_screen候选"
    )
    matched_shapes: list[str] = Field(description="命中的形态代码")
    matched_shape_labels: list[str] = Field(description="命中的形态中文名")
    rules: dict[str, Any] = Field(description="本次使用的形态阈值")
    path: list[PostLimitPathRow] = Field(description="涨停后逐日路径")
    data_missing: list[str] = Field(description="数据缺口代码")
    warnings: list[str] = Field(description="结果边界说明")


class PostLimitStatisticsPayload(OutputModel):
    rule_version: str = Field(description="统计规则版本")
    generated_at: str = Field(description="结果生成时间")
    data_as_of: str = Field(description="数据截止交易日")
    latest_data_date: str | None = Field(default=None, description="本地最新完整日K日期")
    market: str = Field(description="市场范围")
    query_contract: dict[str, Any] = Field(description="本次查询参数")
    status: str = Field(description="结果状态")
    snapshot_kind: str = Field(description="结果类型，当前为重算历史研究")
    shapes: list[str] = Field(description="统计的形态集合")
    shape_labels: dict[str, str] = Field(description="形态代码到中文名的映射")
    rules: dict[str, Any] = Field(description="本次使用的形态阈值")
    signal_dates: list[str] = Field(description="纳入统计的信号交易日")
    requested_signal_days: int = Field(description="请求的信号日窗口")
    signal_count: int = Field(description="首次触发信号数")
    complete_sample_count: int = Field(description="D+5结果完整的样本数")
    complete_signal_date_count: int = Field(description="完整样本覆盖的信号日数")
    sample_quality: str = Field(description="样本质量，sufficient或insufficient")
    comparison_allowed: bool = Field(description="是否允许跨形态比较")
    summaries: list[dict[str, Any]] = Field(
        description="每个形态的D+1/D+3/D+5均值、中位数、MAE、MFE和样本质量"
    )
    groups: list[dict[str, Any]] = Field(
        description="可选分组统计，每项含group、sample_count、signal_date_count、d5均值和中位数"
    )
    outcome_missing: dict[str, int] = Field(description="未来结果缺失原因及数量")
    data_missing: list[str] = Field(description="数据缺口代码")
    input_fingerprint: str = Field(description="本次输入数据的指纹")
    warnings: list[str] = Field(description="统计边界说明")


# ---------------------------------------------------------------------------
# Sector, popularity and Dragon-Tiger facts
# ---------------------------------------------------------------------------


class SectorRankingRow(OutputModel):
    sector_name: str = Field(description="板块名称")
    rank: int = Field(description="当前目录中的涨跌幅排名")
    change_pct: float = Field(description="板块涨跌幅", json_schema_extra={"unit": "%"})
    amount_yi: float | None = Field(
        default=None, description="板块成交额", json_schema_extra={"unit": "亿元"}
    )
    up_count: int | None = Field(default=None, description="板块内上涨家数")
    down_count: int | None = Field(default=None, description="板块内下跌家数")
    leader_name: str | None = Field(default=None, description="领涨股名称")
    leader_change_pct: float | None = Field(
        default=None, description="领涨股涨跌幅", json_schema_extra={"unit": "%"}
    )


class SectorPerformancePayload(OutputModel):
    requested_sector: str | None = Field(default=None, description="请求的板块名")
    sector_name: str | None = Field(default=None, description="解析后的板块名")
    sector_type: str = Field(description="板块类型：industry、concept或industry_group")
    trade_date: date = Field(description="请求的观察交易日")
    data_as_of: date = Field(description="实际数据截止交易日")
    data_fresh: bool = Field(description="数据是否覆盖请求日期")
    previous_trade_date: date | None = Field(
        default=None, description="用于涨跌对比的前一交易日"
    )
    covered_sector_count: int | None = Field(
        default=None, description="本次排名覆盖的板块数"
    )
    ranking_complete: bool | None = Field(
        default=None, description="排名目录是否完整覆盖目标日"
    )
    ranking_universe: str | None = Field(
        default=None, description="排名目录口径说明"
    )
    data_missing: list[str] = Field(description="数据缺口")
    rank: int | None = Field(default=None, description="指定板块的排名")
    sector_count: int = Field(description="排名目录板块总数")
    change_pct: float | None = Field(
        default=None, description="指定板块涨跌幅", json_schema_extra={"unit": "%"}
    )
    amount_yi: float | None = Field(
        default=None, description="指定板块成交额", json_schema_extra={"unit": "亿元"}
    )
    net_inflow_yi: float | None = Field(
        default=None, description="指定板块资金净流入", json_schema_extra={"unit": "亿元"}
    )
    up_count: int | None = Field(default=None, description="指定板块上涨家数")
    down_count: int | None = Field(default=None, description="指定板块下跌家数")
    leader_name: str | None = Field(default=None, description="指定板块领涨股")
    leader_price: float | None = Field(
        default=None, description="领涨股价格", json_schema_extra={"unit": "元"}
    )
    leader_change_pct: float | None = Field(
        default=None, description="领涨股涨跌幅", json_schema_extra={"unit": "%"}
    )
    return_5d_pct: float | None = Field(
        default=None, description="指定板块5日区间收益", json_schema_extra={"unit": "%"}
    )
    return_20d_pct: float | None = Field(
        default=None, description="指定板块20日区间收益", json_schema_extra={"unit": "%"}
    )
    top_sectors: list[SectorRankingRow] = Field(description="涨跌幅靠前板块")
    bottom_sectors: list[SectorRankingRow] = Field(description="涨跌幅靠后板块")
    matched_sectors: list[SectorRankingRow] = Field(description="名称匹配的板块")
    history: list[dict[str, Any]] = Field(
        description="指定板块逐日历史，每项含trade_date、close、change_pct"
    )
    sources: list[str] = Field(description="数据来源")


class SectorStockRow(OutputModel):
    rank: int = Field(description="本次趋势比较排名")
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    trend_score: float = Field(description="趋势综合分")
    trend: str = Field(description="趋势标签：rising、oscillating、falling或insufficient")
    data_as_of: date = Field(description="该股数据截止交易日")
    latest_close: float = Field(description="最新收盘价", json_schema_extra={"unit": "元"})
    return_5d_pct: float | None = Field(
        default=None, description="5日区间涨跌幅", json_schema_extra={"unit": "%"}
    )
    return_20d_pct: float | None = Field(
        default=None, description="20日区间涨跌幅", json_schema_extra={"unit": "%"}
    )
    ma5: float | None = Field(default=None, description="5日均线", json_schema_extra={"unit": "元"})
    ma10: float | None = Field(default=None, description="10日均线", json_schema_extra={"unit": "元"})
    ma20: float | None = Field(default=None, description="20日均线", json_schema_extra={"unit": "元"})
    volume_ratio_5d: float | None = Field(
        default=None, description="近5日量比"
    )
    max_drawdown_pct: float | None = Field(
        default=None, description="区间最大回撤", json_schema_extra={"unit": "%"}
    )


class SectorStockRankingPayload(OutputModel):
    requested_sector: str = Field(description="请求的板块名")
    sector_name: str = Field(description="解析后的板块名")
    sector_category: str = Field(description="板块类别：industry或concept")
    sector_thscode: str = Field(description="板块数据源标识")
    requested_days: int = Field(description="趋势比较窗口")
    requested_limit: int = Field(description="请求返回数量")
    data_as_of: date = Field(description="数据截止交易日")
    member_count: int = Field(description="板块成分股总数")
    analyzed_count: int = Field(description="完成趋势比较的股票数")
    missing_count: int = Field(description="因数据缺失未比较的股票数")
    truncated_count: int = Field(description="因数量上限未返回的股票数")
    items: list[SectorStockRow] = Field(description="趋势排名")
    sources: list[str] = Field(description="数据来源")
    warnings: list[str] = Field(description="覆盖范围说明")


class HotStockRow(OutputModel):
    symbol: str = Field(description="股票代码")
    thscode: str = Field(description="数据源带市场后缀的股票标识")
    name: str = Field(description="股票名称")
    rank: int = Field(description="数据源当前人气排名；不是筛选后序号或涨跌幅排名")
    heat: int | None = Field(default=None, description="数据源返回的人气热度值")
    rank_change: int | None = Field(
        default=None, description="数据源排名变化，比较时点未确认；不是股价涨跌幅"
    )
    rank_trend: str | None = Field(
        default=None, description="数据源排名趋势：up、down或flat；不是股价走势"
    )
    previous_trade_day_rank: int | None = Field(
        default=None, description="上一交易日同源排名；未取得基准时为null"
    )
    previous_trade_day_rank_change: int | None = Field(
        default=None, description="相对上一交易日同源排名变化；未取得基准时为null"
    )
    last_price: float | None = Field(
        default=None, description="补充行情的最新价", json_schema_extra={"unit": "元"}
    )
    change_pct: float | None = Field(
        default=None, description="补充行情的涨跌幅", json_schema_extra={"unit": "%"}
    )
    turnover_yi: float | None = Field(
        default=None, description="补充行情的成交额", json_schema_extra={"unit": "亿元"}
    )


class HotStockPayload(OutputModel):
    source: str = Field(description="榜单数据源")
    source_label: str = Field(description="榜单数据源中文名")
    captured_at: str = Field(description="榜单抓取时间")
    captured_at_beijing: str = Field(description="榜单抓取时间，北京时间")
    quote_captured_at: str | None = Field(default=None, description="补充行情抓取时间")
    data_fresh: bool = Field(description="榜单是否在新鲜窗口内")
    age_seconds: int = Field(description="榜单抓取至今秒数")
    period: str = Field(description="榜单周期")
    rank_change_reference: str = Field(description="rank_change的来源口径")
    rank_change_comparison_date: str | None = Field(
        default=None, description="rank_change的比较日期；未确认时为null"
    )
    previous_trade_day: str | None = Field(
        default=None, description="上一交易日基准；未查询时为null"
    )
    metric_definitions: dict[str, str] = Field(description="指标口径说明")
    field_availability: dict[str, str] = Field(description="字段可用性说明")
    requested_count: int = Field(description="请求返回数量")
    count: int = Field(description="实际返回数量")
    complete: bool = Field(description="是否达到请求数量")
    items: list[HotStockRow] = Field(description="人气榜单")


class DragonTigerRow(OutputModel):
    symbol: str = Field(description="股票代码")
    thscode: str = Field(description="数据源带市场后缀的股票标识")
    name: str = Field(description="股票名称")
    trade_date: str = Field(description="榜单披露/交易日期")
    change_pct: float | None = Field(
        default=None, description="来源涨跌幅", json_schema_extra={"unit": "%"}
    )
    buy_amount: float | None = Field(
        default=None, description="统计区间买入额", json_schema_extra={"unit": "元"}
    )
    sell_amount: float | None = Field(
        default=None, description="统计区间卖出额", json_schema_extra={"unit": "元"}
    )
    net_buy_amount: float | None = Field(
        default=None, description="统计区间净买入额", json_schema_extra={"unit": "元"}
    )
    net_rate: float | None = Field(
        default=None, description="净买额占成交额比例", json_schema_extra={"unit": "%"}
    )
    organization_net_buy_amount: float | None = Field(
        default=None, description="机构净买入额", json_schema_extra={"unit": "元"}
    )
    hot_money_net_buy_amount: float | None = Field(
        default=None, description="游资净买入额", json_schema_extra={"unit": "元"}
    )
    hot_rank: int | None = Field(default=None, description="来源热度排名")
    range_days: int | None = Field(
        default=None, description="该条记录的统计区间天数；同股不同区间须分别保留"
    )
    limit_reason: str | None = Field(default=None, description="来源提供的上榜原因")
    concepts: list[str] = Field(description="相关题材")


class DragonTigerPayload(OutputModel):
    source: str = Field(description="龙虎榜数据源")
    trade_date: str | None = Field(default=None, description="榜单交易日")
    board_type: str = Field(description="榜单类型：all、org或hot_money")
    stock_count: int = Field(description="去重股票数")
    matched_count: int | None = Field(default=None, description="匹配查询的记录数")
    returned_count: int = Field(description="实际返回记录数")
    source_truncated: bool = Field(description="来源是否截断")
    items: list[DragonTigerRow] = Field(description="龙虎榜记录；同股不同区间分别保留")
    overview: dict[str, Any] = Field(
        description="确定性概览，含记录数、去重股票数、各range_days金额合计和前5排名"
    )


# ---------------------------------------------------------------------------
# Events, ratings and recent returns
# ---------------------------------------------------------------------------


class LimitUpEventRow(OutputModel):
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    trade_date: date = Field(description="涨停事件交易日")
    first_limit_time: str = Field(description="首次封板时间")
    last_limit_time: str = Field(description="最后封板时间")
    seal_count: int = Field(description="封板次数")
    break_count: int = Field(description="开板/炸板次数；不是连板高度")
    closed_limit: bool = Field(description="收盘是否封住涨停")
    board_height: int = Field(description="连板高度，1为首板")
    amount: float = Field(description="成交金额", json_schema_extra={"unit": "元"})
    turnover_rate: float = Field(
        description="换手率", json_schema_extra={"unit": "%"}
    )
    industry: str = Field(description="所属行业")
    concept: str = Field(description="所属概念/题材")
    next_open_pct: float = Field(
        description="次日开盘涨跌幅", json_schema_extra={"unit": "%"}
    )
    next_high_pct: float = Field(
        description="次日最高价涨跌幅", json_schema_extra={"unit": "%"}
    )
    next_close_pct: float = Field(
        description="次日收盘涨跌幅", json_schema_extra={"unit": "%"}
    )
    three_day_return_pct: float = Field(
        description="后续3日累计涨跌幅", json_schema_extra={"unit": "%"}
    )
    five_day_return_pct: float = Field(
        description="后续5日累计涨跌幅", json_schema_extra={"unit": "%"}
    )
    continued_next_day: bool = Field(description="次日是否继续涨停")


class LimitUpEventsPayload(OutputModel):
    trade_date: str = Field(description="查询日期或最近选中交易日")
    start_trade_date: str | None = Field(default=None, description="多日查询的首个交易日")
    recent_trade_days: int = Field(description="最近交易日窗口")
    selected_trade_day_count: int = Field(description="实际选中的交易日数")
    group_by: str | None = Field(default=None, description="聚合维度")
    market: str | None = Field(default=None, description="市场板块")
    market_label: str | None = Field(default=None, description="市场板块中文名")
    event_status: str = Field(description="事件状态过滤")
    highest_only: bool = Field(description="是否只保留最高板")
    sort_by: str = Field(description="排序字段")
    sort_order: str = Field(description="排序方向")
    matched_count: int = Field(description="过滤后命中事件数")
    unique_stock_count: int = Field(description="去重股票数")
    returned_count: int = Field(description="实际返回事件数")
    sector_summary: list[dict[str, Any]] = Field(
        description="按行业或题材聚合的命中数量"
    )
    unclassified_event_count: int = Field(description="无法归入聚合维度的事件数")
    events: list[LimitUpEventRow] = Field(description="涨停事件")


class MarketEventRow(OutputModel):
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    event_type: str = Field(description="事件类型：limit_up、limit_down或broken_board")
    change_pct: float | None = Field(
        default=None, description="来源涨跌幅；数据未提供时为null",
        json_schema_extra={"unit": "%"},
    )
    industry: str | None = Field(default=None, description="所属行业")
    concept: str | None = Field(default=None, description="所属概念/题材")
    board_height: int | None = Field(default=None, description="连板高度")
    first_limit_time: str | None = Field(default=None, description="首次封板时间")
    break_count: int | None = Field(default=None, description="开板次数")
    closed_limit: bool | None = Field(default=None, description="收盘是否封住涨停")


class MarketEventPoolPayload(OutputModel):
    trade_date: str = Field(description="事件交易日")
    event_type: str = Field(description="事件类型")
    event_label: str = Field(description="事件类型中文名")
    market: str | None = Field(default=None, description="市场板块")
    market_label: str | None = Field(default=None, description="市场板块中文名")
    query: str | None = Field(default=None, description="关键词过滤")
    result_mode: str = Field(description="返回模式")
    matched_count: int | None = Field(default=None, description="匹配数量")
    returned_count: int = Field(description="实际返回数量")
    items: list[MarketEventRow] = Field(description="事件名单")
    source: str = Field(description="数据来源")
    availability: str | None = Field(
        default=None, description="收盘数据不可用原因；可用时为null"
    )
    checked_at: str | None = Field(default=None, description="可用性检查时间")
    data_missing: list[str] = Field(description="数据缺口")
    complete: bool | None = Field(default=None, description="结果是否完整")


class FirstBoardRatingRow(OutputModel):
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    trade_date: date = Field(description="首板交易日")
    first_limit_time: str = Field(description="首次封板时间")
    last_limit_time: str = Field(description="最后封板时间")
    seal_count: int = Field(description="封板次数")
    break_count: int = Field(description="开板/炸板次数；不是连板高度")
    closed_limit: bool = Field(description="收盘是否封住涨停")
    is_one_word_board: bool = Field(description="是否一字板")
    board_height: int = Field(description="连板高度，首板为1")
    amount: float = Field(description="成交金额", json_schema_extra={"unit": "元"})
    turnover_rate: float = Field(
        description="换手率", json_schema_extra={"unit": "%"}
    )
    industry: str = Field(description="所属行业")
    concept: str = Field(description="所属概念/题材")
    same_industry_limit_up_count: int = Field(description="同行业涨停家数")
    same_concept_limit_up_count: int = Field(description="同概念涨停家数")
    market_limit_up_count: int = Field(description="全市场涨停家数")
    market_first_board_count: int = Field(description="全市场首板家数")
    market_failed_limit_up_rate: float = Field(
        description="全市场炸板率", json_schema_extra={"unit": "%"}
    )
    market_max_board_height: int = Field(description="全市场最高连板高度")
    enrichment: dict[str, Any] | None = Field(
        default=None,
        description="首板补充事实，含流通市值、量比、20日涨幅、龙虎榜和人氣排名等",
    )
    data_missing: list[str] = Field(description="评分事实缺口")
    score: float = Field(description="首板规则评分")
    rating: str = Field(description="首板评级A/B/C/D")
    confidence: float = Field(description="评级置信度")
    score_breakdown: list[dict[str, Any]] = Field(
        description="评分维度明细，每项含name、score、max_score、evidence"
    )
    reasons: list[str] = Field(description="支持评分的理由")
    risks: list[str] = Field(description="评分风险提示")


class FirstBoardRatingsPayload(OutputModel):
    trade_date: date = Field(description="评级日期")
    snapshot_source: str = Field(description="评级来源：live、historical_backtest或calculated")
    data_as_of: date | None = Field(default=None, description="评级数据截止日")
    candidate_count: int = Field(description="入池候选数")
    filtered_out_count: int = Field(description="被过滤掉的候选数")
    top_candidates: list[FirstBoardRatingRow] = Field(description="评分候选")
    position_classification: dict[str, Any] = Field(
        description="按首板前K线位置分组的候选，含低位启动、超跌反弹等"
    )
    recommendation_draft: dict[str, Any] | None = Field(
        default=None, description="盘前研究排序草稿；仅最新日期返回"
    )


class FirstBoardFilterPayload(OutputModel):
    trade_date: str | None = Field(default=None, description="评级日期")
    matched_count: int = Field(description="匹配候选数")
    source: str = Field(description="数据来源")
    items: list[FirstBoardRatingRow] = Field(description="匹配的首板候选")


class RecentLimitUpReturnRow(OutputModel):
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    return_pct: float = Field(
        description="基准日至截止日的区间累计收盘价涨跌幅；不是单日涨跌幅",
        json_schema_extra={"unit": "%"},
    )
    start_date: str = Field(description="区间涨幅的基准收盘日期")
    end_date: str = Field(description="区间涨幅的截止收盘日期")
    start_close: float = Field(description="基准收盘价", json_schema_extra={"unit": "元"})
    end_close: float = Field(description="截止收盘价", json_schema_extra={"unit": "元"})
    window_start_date: str = Field(description="累计涨幅窗口第一个交易日")
    observed_intervals: int = Field(description="实际观测的交易日间隔数")
    last_limit_date: str = Field(description="候选范围内最近一次收盘涨停日")
    limit_up_count: int = Field(description="候选范围内已记录的收盘涨停次数")
    price_basis: str = Field(description="价格口径，当前为未复权收盘价")
    sources: list[str] = Field(description="日K数据来源")


class RecentReturnsPayload(OutputModel):
    status: str = Field(description="结果状态")
    source: str = Field(description="数据来源")
    sources: list[str] = Field(description="涉及的日K来源")
    scope: str = Field(description="候选范围说明")
    end_date: str | None = Field(default=None, description="截止交易日")
    available_data_as_of: str | None = Field(default=None, description="本地可用数据截止日")
    recent_trade_days: int = Field(description="候选期交易日窗口")
    return_trade_days: int = Field(description="收益窗口交易日数")
    candidate_start_date: str | None = Field(default=None, description="候选期起始日")
    candidate_end_date: str | None = Field(default=None, description="候选期截止日")
    candidate_trade_dates: list[str] = Field(description="候选期交易日")
    selected_trade_day_count: int = Field(description="实际候选交易日数")
    return_trade_dates: list[str] = Field(description="收益计算使用的交易日")
    start_date: str | None = Field(default=None, description="收益基准日")
    window_start_date: str | None = Field(default=None, description="收益窗口首日")
    calendar_source: str = Field(description="日历来源")
    calendar_basis: str = Field(description="日历口径")
    calendar_verified: bool = Field(description="是否核验交易所日历")
    calendar_note: str = Field(description="日历边界说明")
    warnings: list[str] = Field(description="覆盖范围警告")
    price_basis: str = Field(description="价格口径")
    return_definition: str = Field(description="区间收益计算公式")
    candidate_count: int = Field(description="候选股票数")
    analyzed_count: int = Field(description="可计算收益的股票数")
    missing_count: int = Field(description="数据不完整未计算的股票数")
    matched_count: int = Field(description="返回股票数")
    returned_count: int = Field(description="实际返回股票数")
    source_truncated: bool = Field(description="来源是否截断")
    items: list[RecentLimitUpReturnRow] = Field(description="可计算收益的候选")
    missing_items: list[dict[str, Any]] = Field(description="未计算股票及逐项原因")
    data_missing: list[str] = Field(description="数据缺口")


# ---------------------------------------------------------------------------
# Stock K-line, news and activity
# ---------------------------------------------------------------------------


class StockKLineBarRow(OutputModel):
    trade_date: date = Field(description="交易日")
    open: float = Field(description="开盘价", json_schema_extra={"unit": "元"})
    close: float = Field(description="收盘价", json_schema_extra={"unit": "元"})
    high: float = Field(description="最高价", json_schema_extra={"unit": "元"})
    low: float = Field(description="最低价", json_schema_extra={"unit": "元"})
    volume: float = Field(description="成交量，沿用来源单位；不是成交金额")
    amount: float = Field(description="成交额", json_schema_extra={"unit": "元"})
    source: str = Field(description="该条K线的数据来源")
    change_pct: float | None = Field(
        default=None,
        description="来源返回的单日涨跌幅；缺失时不得用未复权收盘价变动率代替",
        json_schema_extra={"unit": "%"},
    )
    previous_close: float | None = Field(
        default=None, description="前一交易日收盘价", json_schema_extra={"unit": "元"}
    )
    previous_trade_date: date | None = Field(default=None, description="前一交易日")
    change_pct_basis: str | None = Field(default=None, description="change_pct的来源口径")
    close_to_close_pct: float | None = Field(
        default=None,
        description="相邻有效记录的未复权收盘价变动率；基准见previous_close",
        json_schema_extra={"unit": "%"},
    )


class StockKLinePayload(OutputModel):
    symbol: str = Field(description="股票代码")
    requested_days: int = Field(description="请求的观察交易日数")
    requested_end_date: date = Field(description="请求的截止交易日")
    data_as_of: date = Field(description="实际数据截止交易日")
    data_fresh: bool = Field(description="数据是否覆盖请求截止日")
    trend: str = Field(description="均线排列标签：rising、falling、oscillating或insufficient")
    latest_close: float = Field(description="最新收盘价", json_schema_extra={"unit": "元"})
    return_5d_pct: float | None = Field(
        default=None, description="5个交易日区间涨跌幅", json_schema_extra={"unit": "%"}
    )
    return_10d_pct: float | None = Field(
        default=None, description="10个交易日区间涨跌幅", json_schema_extra={"unit": "%"}
    )
    return_20d_pct: float | None = Field(
        default=None, description="20个交易日区间涨跌幅", json_schema_extra={"unit": "%"}
    )
    ma5: float | None = Field(default=None, description="5日均线", json_schema_extra={"unit": "元"})
    ma10: float | None = Field(default=None, description="10日均线", json_schema_extra={"unit": "元"})
    ma20: float | None = Field(default=None, description="20日均线", json_schema_extra={"unit": "元"})
    volume_ratio_5d: float | None = Field(default=None, description="近5日量比")
    max_drawdown_pct: float | None = Field(
        default=None, description="展示窗口内峰谷最大回撤", json_schema_extra={"unit": "%"}
    )
    sources: list[str] = Field(description="K线来源")
    bars: list[StockKLineBarRow] = Field(description="日K线")
    data_missing: list[str] = Field(description="数据缺口")


class FinanceNewsRow(OutputModel):
    title: str = Field(description="新闻标题")
    summary: str = Field(description="新闻摘要")
    published_at: datetime = Field(description="发布时间")
    source: str = Field(description="来源")
    url: str = Field(description="原文链接")
    category: str = Field(description="新闻类别")
    relevance_score: float = Field(description="与查询主题的相关性分数")


class FinanceNewsPayload(OutputModel):
    query: str | None = Field(default=None, description="主题词；仅用于排序加权")
    fetched_at: datetime = Field(description="抓取时间")
    window_hours: int = Field(description="按抓取时间回溯的小时数")
    sources: list[str] = Field(description="聚合来源")
    items: list[FinanceNewsRow] = Field(description="去重后的财经快讯")


class StockNewsRow(OutputModel):
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    title: str = Field(description="新闻标题")
    summary: str = Field(description="新闻摘要")
    published_at: datetime = Field(description="发布时间")
    source: str = Field(description="来源")
    url: str = Field(description="原文链接")
    item_type: str = Field(description="条目类型，如新闻或公告")
    relevance_score: float = Field(description="相关性分数")
    fetched_at: datetime = Field(description="抓取时间")


class StockNewsPayload(OutputModel):
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    fetched_at: datetime = Field(description="抓取时间")
    window_days: int = Field(description="回溯自然日数")
    cache_status: str = Field(description="缓存状态")
    sources: list[str] = Field(description="新闻来源")
    items: list[StockNewsRow] = Field(description="个股新闻")
    data_missing: list[str] = Field(description="数据缺口")


class StockActivityPayload(OutputModel):
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    fetched_at: datetime = Field(description="汇总抓取时间")
    data_as_of: date | None = Field(default=None, description="K线数据截止交易日")
    kline: dict[str, Any] | None = Field(
        default=None, description="K线事实，字段含义同stock_kline"
    )
    recent_limit_up_events: list[dict[str, Any]] = Field(
        description="近期涨停记录，每项含trade_date、board_height、closed_limit、first_limit_time、break_count、industry、concept"
    )
    rating_context: dict[str, Any] = Field(
        description="首板评分补充事实，含流通市值、量比、20日涨幅、龙虎榜和人氣排名"
    )
    news: dict[str, Any] = Field(description="个股新闻，字段含义同stock_news")
    data_missing: list[str] = Field(description="数据缺口")
