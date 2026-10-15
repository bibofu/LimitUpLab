"""Reviewed table-row columns; a catalog entry never proves row availability.

Descriptions guide semantic matching, not keyword substitutions. The catalog
covers scalar row columns, not all answerable summary metrics. Unknown columns
stay unbound; the current task retains the complete request and actual tool
evidence determines which fields can be delivered.
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

# Rankings are first-class evidence, including through select/intersection.
# These fields come from the normalized provider row, not an inferred ordering.
DISPLAY_FIELD_CATALOG.update({
    key: {"meaning": meaning, "model": "HithinkHotStockFact", "tools": ["hot_stock_ranking"]}
    for key, meaning in {
        "thscode": "数据源的带市场后缀股票标识",
        "rank": "所选数据源当前人气热度榜的原始排名；不是筛选后重排的序号或涨跌幅排名",
        "heat": "所选数据源返回的人气热度值，不是价格或收益；源未提供时为缺失",
        "rank_change": "所选数据源返回的人气排名变化值，沿用来源的比较时点和符号口径；不是股价涨跌幅",
        "rank_trend": "所选数据源返回的人气排名趋势，up上升、down下降、flat持平；不是股价走势",
    }.items()
})
for key in ("symbol", "name"):
    DISPLAY_FIELD_CATALOG[key]["tools"].append("hot_stock_ranking")

DISPLAY_FIELD_CATALOG.update({
    key: {"meaning": meaning, "model": "StockKLineBar", "tools": ["stock_kline"]}
    for key, meaning in {
        "open": "个股该交易日开盘价，元，沿用工具返回的价格口径",
        "close": "个股该交易日收盘价，元，沿用工具返回的价格口径",
        "high": "个股该交易日最高价，元",
        "low": "个股该交易日最低价，元",
        "volume": "个股该交易日成交量，沿用工具返回的计量单位，不等同于成交金额",
        "source": "该条个股日K线记录实际返回的数据来源标识",
        "change_pct": (
            "单日涨跌幅，百分比：stock_kline使用每个交易日来源返回的原始日涨跌幅；"
            "market_index_trend为截止交易日相对上一交易日的指数点位变化。"
            "明确对象且查询当日或逐日涨跌幅时使用此字段；不是窗口区间涨跌幅return_pct。"
            "来源缺失时为缺失，不能填0或用未复权收盘价变动率代替"
        ),
        "close_to_close_pct": (
            "个股相邻有效记录的未复权收盘价变动率，百分比，基准见previous_close及previous_trade_date；"
            "除权日不等于来源日涨跌幅change_pct，不能替代用户要求的日涨跌幅"
        ),
    }.items()
})
DISPLAY_FIELD_CATALOG["change_pct"]["tools"].append("market_index_trend")
for key in ("trade_date", "amount"):
    DISPLAY_FIELD_CATALOG[key]["tools"].append("stock_kline")
DISPLAY_FIELD_CATALOG["trade_date"]["meaning"] = "涨停事件或个股日K线记录对应的交易日期"
