# LimitUpLab

LimitUpLab is an open-source analysis tool for tracking A-share limit-up stocks, market sentiment, board continuation probability, failed limit-up rate, and post-limit-up performance.

LimitUpLab 是一个面向 A 股涨停股的开源分析工具，用于追踪涨停股票、连板梯队、炸板率、次日表现和短线市场情绪。

## Vision

涨停股是 A 股短线情绪最集中的样本。LimitUpLab 希望把每日涨停、炸板、连板、次日表现等事件沉淀成可复盘、可统计、可验证的数据系统。

它不只是一个行情展示工具，而是一个围绕涨停事件的分析实验室：

- 最近涨停过的股票，后来都怎么走了？
- 首板、二板、三板的晋级概率分别是多少？
- 不同封板时间、换手率、成交额下的炸板风险有什么差异？
- 当前市场短线情绪是在升温、分歧，还是退潮？

## MVP

- Track daily A-share limit-up stocks
- Show recent price trends after limit-up events
- Calculate continuation probability by board height
- Calculate failed limit-up rate
- Analyze next-day performance after limit-up events
- Provide a simple web dashboard for review and exploration

## Core Features

### Daily Limit-Up Pool

每日涨停池，记录当日涨停股票的核心信息：

- 股票代码与名称
- 涨停日期
- 首次封板时间
- 最后封板时间
- 封板次数
- 炸板次数
- 连板高度
- 成交额
- 换手率
- 所属行业或概念

### Board Continuation Analysis

连板晋级统计，用于观察不同高度的涨停股继续晋级的概率：

- 首板晋级二板概率
- 二板晋级三板概率
- 三板晋级四板概率
- 高位连板断板概率
- 不同市场环境下的晋级差异

### Failed Limit-Up Analysis

炸板统计，用于分析涨停失败或封板不稳定的风险：

- 当日炸板率
- 按首次封板时间统计炸板率
- 按连板高度统计炸板率
- 按成交额、换手率、市值区间统计炸板率
- 炸板后次日修复概率

### Post Limit-Up Performance

涨停后的收益表现统计：

- 次日开盘涨幅
- 次日最高涨幅
- 次日收盘涨幅
- N 日收益分布
- 是否继续涨停
- 是否出现大幅回撤

### Market Sentiment Dashboard

短线情绪面板：

- 今日涨停数量
- 今日跌停数量
- 今日炸板率
- 连板最高高度
- 连板梯队分布
- 热门行业与概念
- 近期涨停股走势

## Example Questions

LimitUpLab 目标是帮助用户回答一些具体、可验证的问题：

- 上午涨停的股票，次日溢价是否高于下午涨停？
- 首板放量涨停和缩量涨停，哪个更容易晋级？
- 二板之后的炸板率是否明显上升？
- 一字板开板后的风险和机会分别如何？
- 市场涨停数量增加时，连板概率是否同步上升？
- 某个行业成为热点后，涨停持续性通常能维持几天？

## Possible Tech Stack

项目技术栈会随着实现推进逐步确定。初步方向：

- Data source: AKShare / Tushare / exchange public data
- Backend: Python
- Data processing: pandas / polars
- Database: SQLite for local MVP, PostgreSQL for production
- Dashboard: Streamlit / FastAPI + React
- Visualization: ECharts / Plotly

## Roadmap

- [ ] Initialize project structure
- [ ] Build daily limit-up stock data collector
- [ ] Store limit-up events in local database
- [ ] Calculate board continuation probability
- [ ] Calculate failed limit-up rate
- [ ] Add post-limit-up performance analysis
- [ ] Build first dashboard page
- [ ] Add historical backfill script
- [ ] Add documentation for data fields and formulas

## Disclaimer

LimitUpLab is for research, learning, and open-source data analysis only. It does not provide investment advice, trading signals, or financial recommendations.

LimitUpLab 仅用于学习、研究和开源数据分析，不构成任何投资建议、交易信号或收益承诺。股票市场有风险，实盘交易需谨慎。

## License

This project is planned to be released under the MIT License.
