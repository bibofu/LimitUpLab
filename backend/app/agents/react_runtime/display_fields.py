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
