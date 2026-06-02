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
- `GET /api/agents/daily-review` - rule-based after-close daily review
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
