"""Documented output contracts for market, event, rating and stock tools.

Phase 1 keeps the existing tool implementations and payloads unchanged. These
models describe the fields the model receives; ``tool_output_contracts`` turns
them into a compact ``field_definitions`` map.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import LimitUpEvent


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
    touched_limit_count: int = Field(description="当日触及涨停的股票总数，等于收盘涨停数加收盘未回封数；两个比例的共同分母")
    intraday_opened_count: int = Field(description="盘中曾开板的股票数，包含最终回封和未回封股票")
    intraday_opened_rate: float = Field(
        description="盘中曾开板数 / 当日触及涨停总数；不是除以收盘涨停数", json_schema_extra={"unit": "0至1比例"}
    )
    unsealed_count: int = Field(description="收盘未回封的炸板股数")
    unsealed_rate: float = Field(
        description="收盘未回封数 / 当日触及涨停总数", json_schema_extra={"unit": "0至1比例"}
    )
    rate_basis: str = Field(description="开板率与未回封率的分子、分母及数值单位")
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
    events: list[LimitUpEvent] = Field(description="涨停事件")


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
        description="全市场盘中曾开板数 / 当日触及涨停总数；包含最终回封股票，不是收盘未回封率", json_schema_extra={"unit": "0至1比例"}
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
    selection_scope: str = Field(description="persisted_snapshot仅为历史固化批次；calculated_full_pool为重新计算的完整评级池；另受requested_symbols限制")
    scope_note: str = Field(description="固化批次、完整候选池与本次返回范围的证据边界")
    requested_symbols: list[str] = Field(description="本次额外选择的股票；非空时不能将返回数量称为整个候选池")
    candidate_count: int = Field(description="本次返回候选数，兼容旧字段；不代表全部评级合格数")
    returned_candidate_count: int = Field(description="本次实际返回候选数，已应用symbols选择")
    snapshot_candidate_count: int = Field(description="来源评分池或固化批次保存的候选数，应用symbols选择之前")
    snapshot_top_limit: int | None = Field(default=None, description="固化批次实际记录的候选保留上限；null表示无此证据，不得根据返回10条推断Top10")
    universe_count: int = Field(description="来源快照记录的评分输入事件数，包含被硬筛排除的非首板等事件；不是入池数")
    filtered_out_count: int = Field(description="来源快照记录的硬筛排除数；不包含评级合格但未选入固化批次的记录")
    filtered_out: list[dict[str, Any]] = Field(description="来源快照保存的硬筛排除记录及原因，范围独立于symbols选择")
    recorded_eligible_candidate_count: int | None = Field(default=None, description="来源记录的universe_count减filtered_out_count；重新计算分支为完整合格池大小，旧快照仅证明其记录范围")
    not_selected_count: int | None = Field(default=None, description="来源记录的合格数减固化候选数；未保留进快照，不是硬筛排除")
    source_truncated: bool | None = Field(default=None, description="true为已证明固化候选少于来源记录的合格池；null表示源完整性未知；false仅重新计算完整池可确认")
    candidate_pool_complete: bool | None = Field(default=None, description="本次是否覆盖全部评级合格池；false表示已知不完整，null表示固化来源无法证明完整，不能把未知当完整")
    top_candidates: list[FirstBoardRatingRow] = Field(description="评分候选")
    position_classification: dict[str, Any] = Field(
        description="仅对本次返回候选按首板前K线位置分组，含低位启动、超跌反弹等；不代表完整市场或被省略的合格候选"
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
