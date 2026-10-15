"""Reviewed scalar display fields; a catalog entry never proves row availability.

Descriptions guide semantic matching, not keyword substitutions. Ambiguous or
tool-specific metrics outside this small catalog remain unbound.
"""

DISPLAY_FIELD_CATALOG = {
    key: {"meaning": meaning, "model": "LimitUpEvent", "tools": ["limit_up_events"]}
    for key, meaning in {
        "symbol": "股票代码",
        "name": "股票名称",
        "trade_date": "事件交易日期",
        "board_height": "涨停连板高度、板数，1为首板",
        "closed_limit": "收盘是否封住涨停",
        "seal_count": "封板次数",
        "break_count": "开板次数、炸板次数；不是连板高度",
        "first_limit_time": "首次封板时间",
        "last_limit_time": "最后封板时间",
        "amount": "成交金额，元",
        "turnover_rate": "换手率，百分比",
        "industry": "行业",
        "concept": "概念、题材",
    }.items()
}
DISPLAY_FIELD_CATALOG.update({
    key: {"meaning": meaning, "model": "FirstBoardRating", "tools": ["first_board_ratings"]}
    for key, meaning in {
        "score": "首板规则评分",
        "rating": "首板评级",
        "confidence": "首板评级置信度",
    }.items()
})

# Shared identity fields keep their stock meanings and also identify index rows.
for key, meaning in {"symbol": "股票代码或指数代码，按当前查询对象区分",
                     "name": "股票名称或指数名称，按当前查询对象区分"}.items():
    DISPLAY_FIELD_CATALOG[key]["meaning"] = meaning
    DISPLAY_FIELD_CATALOG[key]["tools"].append("market_index_trend")

DISPLAY_FIELD_CATALOG.update({
    key: {"meaning": meaning, "model": "MarketIndexTrendItem", "tools": ["market_index_trend"]}
    for key, meaning in {
        "start_date": "指数所选走势窗口的起始交易日",
        "end_date": "指数所选走势窗口的截止交易日",
        "start_close": "指数窗口起始交易日收盘点位，点",
        "end_close": "指数窗口截止交易日收盘点位，点",
        "return_pct": (
            "指定指数在所选窗口内的区间涨跌幅，百分比，按(末日收盘/首日收盘-1)*100计算；"
            "不是单日涨跌幅。未明确对象或窗口的泛称涨跌幅不能自动绑定此字段"
        ),
        "max_drawdown_pct": "指数所选窗口内按收盘点位计算的最大回撤，非正百分比",
        "positive_days": "指数所选窗口内相邻收盘上涨的交易日数量，不是连续上涨天数",
        "negative_days": "指数所选窗口内相邻收盘下跌的交易日数量，不是连续下跌天数",
        "source": "该条指数走势记录实际返回的数据来源标识",
    }.items()
})
