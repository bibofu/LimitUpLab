# LimitUpLab Backend

Python backend for collecting, storing, and analyzing A-share limit-up events
for after-close market review.

## Quick Start

Python 3.13 is recommended on Windows. Python 3.14 may try to build
`pydantic-core` from source for the pinned dependency set and require Visual
Studio C++ build tools.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

On Windows PowerShell:

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.

## LLM Explanation

LLM integration is disabled by default. When disabled, the Explanation Agent
uses a deterministic template fallback, so local development still works
without external API access.

To enable a DeepSeek/OpenAI-compatible provider:

```powershell
$env:LIMITUPLAB_LLM_ENABLED = "true"
$env:LIMITUPLAB_LLM_BASE_URL = "https://api.deepseek.com"
$env:LIMITUPLAB_LLM_MODEL = "deepseek-v4-flash"
$env:DEEPSEEK_API_KEY = "<your-api-key>"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Do not commit real API keys to the repository.

## Local Data

The backend now uses SQLite for local persistence. By default, the database is
created at:

```text
backend/data/limituplab.sqlite
```

If the database is empty, the development server seeds it with the bundled
sample events so the dashboard remains usable after a fresh clone.

To explicitly import the bundled sample data:

```bash
cd backend
python scripts/import_sample_data.py
```

To import real limit-up and failed limit-up events from AKShare:

```bash
cd backend
python scripts/import_limit_up_from_akshare.py --date 20260520 --replace-date
```

For normal daily use, run the full Agent data pipeline instead. It imports raw
limit-up events, rebuilds first-board features, backfills similar-case post bars
for top candidates, and prints a JSON health report:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\update_daily_data.py --date 20260810 --replace-date
```

If raw events are already imported and you only need to refresh derived Agent
data:

```powershell
.\.venv\Scripts\python.exe scripts\update_daily_data.py --date 20260810 --skip-import
```

Set `LIMITUPLAB_DATABASE_PATH` to use a different SQLite file:

```bash
LIMITUPLAB_DATABASE_PATH=/tmp/limituplab.sqlite uvicorn app.main:app --reload --port 8000
```

## Endpoints

- `GET /health` - service health check
- `GET /api/market/summary` - market sentiment summary with real index snapshots
- `GET /api/market/overview` - dashboard overview
- `GET /api/limit-up/events` - latest limit-up events
- `GET /api/limit-up/first-board` - first-board stocks for the latest trading day
- `GET /api/limit-up/continued-board` - continued-board stocks for the latest trading day
- `GET /api/limit-up/failed` - failed or unstable limit-up events for the latest trading day
- `GET /api/limit-up/recent?days=3` - recent trading-day limit-up review list
- `GET /api/analysis/continuation` - board continuation probability
- `GET /api/analysis/failed-rate` - failed limit-up rate by board height
- `GET /api/analysis/post-performance` - next-day and short-window return stats
- `GET /api/agents/first-board-ratings` - explainable first-board candidate ratings
- `GET /api/agents/data-health` - Agent raw-data, feature and similar-case cache health
- `GET /api/agents/rating-backtest` - rating bucket backtest and self-evaluation summary
- `GET /api/agents/first-board-critic` - critic review for one first-board rating
- `GET /api/agents/first-board-similar-cases` - historical similar first-board cases
- `POST /api/agents/chat` - tool-grounded Agent chat and LLM/template explanations
- `GET /api/stocks/{symbol}/kline?days=5` - recent daily K-line bars
- `GET /api/stocks/{symbol}/trading-day-kline?period=5` - after-close trading-day intraday K-line review

## Data Sources

Current collectors use AKShare-backed sources:

- 东方财富涨停池 and 炸板池 for limit-up events
- Index daily data for market snapshots
- Tencent/Sina historical K-line sources for stock daily and trading-day review charts

The product is not designed for live intraday monitoring. The trading-day K-line
endpoint is intended for after-close review of the latest persisted trading day.

## Tests

```bash
cd backend
python -m unittest discover -s tests
```


