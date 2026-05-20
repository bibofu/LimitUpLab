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

## Current Scaffold

The repository is now split into a Python backend and a TypeScript React frontend:

```text
backend/
  app/
    main.py                 FastAPI application entry
    models.py               Pydantic API/data models
    database.py             SQLite connection and schema setup
    collectors/             AKShare market, limit-up, and K-line collectors
    repositories/           SQLite-backed persistence layer
    routers/                API routes
    services/               analysis functions and fallback sample data
  scripts/                  data import scripts
  requirements.txt

frontend/
  src/
    App.tsx                 dashboard page
    api.ts                  API client
    types.ts                shared frontend types
    styles.css              responsive UI styling
  package.json
  vite.config.ts
```

The backend uses SQLite for local persistence. If the database is empty, the development server seeds bundled sample events so the dashboard still works after a fresh clone. Real limit-up, failed limit-up, index, daily K-line, and trading-day intraday review data are collected through AKShare-backed data sources.

## Quick Start

Start the backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Import real limit-up and failed limit-up events for a trading day:

```bash
cd backend
python scripts/import_limit_up_from_akshare.py --date 20260520 --replace-date
```

Start the frontend in another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## API

- `GET /health`
- `GET /api/market/overview`
- `GET /api/market/summary`
- `GET /api/limit-up/events`
- `GET /api/limit-up/first-board`
- `GET /api/limit-up/continued-board`
- `GET /api/limit-up/failed`
- `GET /api/limit-up/recent?days=3`
- `GET /api/analysis/continuation`
- `GET /api/analysis/failed-rate`
- `GET /api/analysis/post-performance`
- `GET /api/stocks/{symbol}/kline?days=5`
- `GET /api/stocks/{symbol}/trading-day-kline?period=5`

## First Product Flow

The dashboard now starts with a market overview page instead of a dense all-in-one table:

- 首板票数量
- 连板票数量和最高连板高度
- 炸板票数量和炸板率
- 涨停成交额
- 上证指数、深证成指、创业板指走势
- 热门题材热度
- 最近三个交易日涨停票走势入口

Users can click into:

- `/stocks/first-board` 首板票
- `/stocks/continued-board` 连板票
- `/stocks/failed` 炸板票
- `/stocks/recent-limit-up` 近三日涨停票复盘
- `/stocks/:symbol` 个股详情页

Because the product is currently positioned as an after-close review tool, stock lists only show data available as of the latest trading day's close. Forward-looking fields such as next-day close or 1/3/5-day returns are intentionally kept out of the current stock detail tables.

The stock detail page focuses on review context:

- 封板信息：高度、封板状态、首次/最后封板时间、封板次数、炸板次数
- 五日日 K：最近 5 个交易日的开高低收
- 交易日走势：收盘后读取该交易日的 5 分钟 K 线，用于复盘从开盘到收盘的走势过程，不提供实时盘中监控

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

### Stock Detail Review

个股详情页用于把涨停事件放回价格走势里复盘：

- 最近 5 个交易日的日 K
- 最新交易日的 5 分钟 K 线走势
- 涨停高度、封板/炸板信息
- 成交额、换手率、行业和题材

## Example Questions

LimitUpLab 目标是帮助用户回答一些具体、可验证的问题：

- 上午涨停的股票，次日溢价是否高于下午涨停？
- 首板放量涨停和缩量涨停，哪个更容易晋级？
- 二板之后的炸板率是否明显上升？
- 一字板开板后的风险和机会分别如何？
- 市场涨停数量增加时，连板概率是否同步上升？
- 某个行业成为热点后，涨停持续性通常能维持几天？

## Roadmap

- [x] Initialize project structure
- [x] Build first dashboard page
- [x] Add sample limit-up event analysis API
- [x] Build daily limit-up stock data collector
- [x] Store limit-up events in local database
- [x] Add real market index snapshots
- [x] Add stock detail page with daily and trading-day K-line review
- [ ] Calculate board continuation probability from persisted data
- [ ] Calculate failed limit-up rate from persisted data
- [ ] Add post-limit-up performance analysis from historical quotes
- [ ] Add historical backfill script
- [ ] Add documentation for data fields and formulas

## Disclaimer

LimitUpLab is for research, learning, and open-source data analysis only. It does not provide investment advice, trading signals, or financial recommendations.

LimitUpLab 仅用于学习、研究和开源数据分析，不构成任何投资建议、交易信号或收益承诺。股票市场有风险，实盘交易需谨慎。

## License

This project is planned to be released under the MIT License.
