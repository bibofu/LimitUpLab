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

For local development, copy the example env file and fill in your own API key:

```powershell
cd backend
Copy-Item .env.example .env
notepad .env
```

The backend loads `backend/.env` automatically. A typical DeepSeek-compatible
configuration looks like this:

```powershell
LIMITUPLAB_LLM_ENABLED=true
LIMITUPLAB_LLM_BASE_URL=https://api.deepseek.com
LIMITUPLAB_LLM_MODEL=deepseek-v4-flash
LIMITUPLAB_LLM_THINKING_ENABLED=false
LIMITUPLAB_LLM_PLANNER_MAX_TOKENS=320
LIMITUPLAB_LLM_ANSWER_MAX_TOKENS=480
DEEPSEEK_API_KEY=<your-api-key>
```

Chat uses non-thinking mode by default because tool selection and grounded
summaries are latency-sensitive structured tasks. Planner, local-tool and
answer latency plus prompt sizes are returned in `response.performance`.

If your network requires a local proxy, set `LIMITUPLAB_PROXY_URL` in `.env`.
The backend will apply it to `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` when
those process variables are not already set.

On Windows, prefer the startup script below. It also reads a `DEEPSEEK_API_KEY`
from the current process, User environment, or Machine environment when the key
is not present in `.env`:

```powershell
cd backend
.\scripts\start_backend.ps1 -Port 8001
```

If PowerShell script execution is disabled on Windows, use the wrapper instead:

```powershell
cd backend
.\scripts\start_backend.cmd -Port 8001
```

Do not commit real API keys to the repository.

## Local Data

The backend now uses SQLite for local persistence. By default, the database is
created at:

```text
backend/data/limituplab.sqlite
```

Agent structured responses use a small SQLite cache table (`agent_cache`) to
avoid repeated computation under concurrent website usage. The MVP currently
caches first-board ratings, rating backtests, and rating evaluations for a short
TTL. Cache keys include request parameters and a local event-data signature, so
freshly imported market data naturally produces new cache entries.

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

The pipeline writes immutable Top10 prediction snapshots. The latest available
trading date is stored as `live`; older missing dates are stored as
`historical_backtest`. Evaluation endpoints only read these snapshots and never
create historical predictions as a side effect. Outcome evaluation uses the
next trading day's open as the executable entry baseline; promotion and
intraday highs remain separate facts.

Before local demos, run the development health check. With `--ensure-data`, it
checks the expected local data date based on China market hours and runs the
daily update pipeline when the local database is stale:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\dev_check.py --ensure-data
```

The check writes a local report to `backend/data/dev_check_report.json`, which is
ignored by git.

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
- `GET /api/agents/system-health` - local runtime health for data freshness, LLM configuration and eval status
- `GET /api/agents/rating-backtest` - entry-open return, drawdown and promotion metrics by rating bucket
- `GET /api/agents/first-board-critic` - critic review for one first-board rating
- `GET /api/agents/rating-evaluation` - source-aware Evaluation Agent review for immutable predictions
- `GET /api/agents/runs` - recent Agent run traces for observability
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

## Agent Evals

The chat Agent has a deterministic regression suite for common questions and
safety boundaries. It checks intent routing, required/forbidden tool calls,
answer facts, warnings, and investment-advice guardrails without depending on a
live LLM provider:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_agent_eval.py
```

Eval cases live in `tests/fixtures/agent_eval_cases.json`.

To sample the configured live LLM planner and capture cases where the model
chooses weak tools or needs backend repair:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --mode live-llm
```

Live eval writes failed or backend-repaired samples to
`backend/data/agent_eval_failures.json` by default. This file is local output and
is ignored by git. Use `--fail-on-failures` when you explicitly want live eval
failures to return a non-zero exit code.

The chat Agent also exposes a general `limit_up_events` internal tool for
same-day limit-up questions such as continued-board lists, board-height filters,
intraday-broken limit-up events, and topic/industry filters. This keeps broad
limit-up questions from being incorrectly routed through first-board-only tools.


