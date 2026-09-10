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
LIMITUPLAB_LLM_BACKEND=langchain
LIMITUPLAB_LLM_BASE_URL=https://api.deepseek.com
LIMITUPLAB_LLM_MODEL=deepseek-v4-flash
LIMITUPLAB_LLM_THINKING_ENABLED=false
LIMITUPLAB_LLM_PLANNER_MAX_TOKENS=320
DEEPSEEK_API_KEY=<your-api-key>
```

The default model backend uses LangChain `ChatPromptTemplate`, LCEL,
`ChatOpenAI.bind_tools` and native streaming behind the existing `LLMProvider`
interface. Business query contracts, Tool Policy and SQLite memory remain owned
by this application. Set `LIMITUPLAB_LLM_BACKEND=requests` and restart to use the
original adapter. The SDK controls LangChain retry backoff; the existing
`LIMITUPLAB_LLM_RETRY_DELAY_SECONDS` setting applies only to Requests.
See [integration and verification details](../docs/LangChain_Integration.md).

Chat uses non-thinking mode by default because tool selection and grounded
summaries are latency-sensitive structured tasks. Planner, local-tool and
answer latency plus prompt sizes are returned in `response.performance`.

The chat Agent also has on-demand external market tools:

- `market_index_trend` fetches date-aligned 2-20 trading-day performance for
  the Shanghai Composite, Shenzhen Component and ChiNext Index.
- `sector_performance` fetches industry-sector ranking, change, breadth,
  turnover, fund flow, leader and recent trend through AKShare providers.
- `web_search` retrieves sanitized public search-result titles, URLs and
  snippets. It uses no-key providers with fallback and treats every snippet as
  untrusted external evidence.

Optional search settings:

```powershell
LIMITUPLAB_WEB_SEARCH_PROVIDER=auto
LIMITUPLAB_WEB_SEARCH_TIMEOUT_SECONDS=12
```

Search results can be stale or incomplete. Numerical market conclusions should
come from structured market tools; search is used for news, catalysts and facts
that are not present in local storage.

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
limit-up events, rebuilds first-board features, backfills tracked Top10 post bars,
and prints a JSON health report:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\update_daily_data.py --date 20260810 --replace-date
```

If raw events are already imported and you only need to refresh derived Agent
data:

```powershell
.\.venv\Scripts\python.exe scripts\update_daily_data.py --date 20260810 --skip-import
```

The pipeline stores same-day after-close batches as `close_baseline`; historical
backfills are always `historical_backtest`, even when they are the latest local
data date. The compatibility `live` label alone does not establish forward
eligibility. Evaluation endpoints only read these snapshots and never
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

## Automated Daily Close Loop

Run one complete close-loop update manually:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_daily_close_loop.py --trigger manual
```

The runner resolves the latest closed A-share trading date, uses a process lock,
retries transient failures, persists execution history, and writes the latest
JSON report under `backend/data`. Both the runner and direct update script require
the base date's 15:30 Asia/Shanghai gate for a new close baseline. A separate
pre-open final must pass the next-trading-day calendar, 08:00–09:30 window and
information-cutoff checks. Only these `premarket_final` samples count in current
forward benchmarks; legacy close batches and backtests are grouped separately
by scoring version. Original baselines are archived transactionally before a
final takes over the active slot. See [Prediction time contract](../docs/Prediction_Time_Contract.md).

`python scripts/audit_prediction_times.py` produces a read-only local audit.
Add `--apply` to create a consistent backup and append hash-bound annotations
without rewriting original predictions or saved review reports. Invalid saved
reports are marked and normal review requests recompute eligible samples.

Install the Windows Task Scheduler entry from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\daily_close_loop_task.ps1 -Mode Install
```

It runs at 16:10 on weekdays, starts after a missed trigger when possible, and
prevents overlapping instances. Use `-Mode Status` or `-Mode Uninstall` to
inspect or remove it.

## Endpoints

- `GET /health` - service health check
- `GET /api/market/summary` - objective limit-up summary with real index snapshots
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
- `GET /api/agents/scoring-policies` - active Champion and recent scoring policy versions
- `POST /api/agents/scoring-policies/optimize` - constrained walk-forward Challenger generation; shadow mode by default
- `GET /api/agents/data-health` - Agent raw-data, feature and enrichment health
- `GET /api/agents/system-health` - local runtime health for data freshness, LLM configuration and eval status
- `GET /api/agents/daily-pipeline-status` - recent automated daily close-loop runs
- `GET /api/agents/rating-backtest` - entry-open return, drawdown and promotion metrics by rating bucket
- `GET /api/agents/prediction-quality-audit` - source/version-aware prediction coverage and deterministic baseline audit
- `GET /api/agents/first-board-critic` - critic review for one first-board rating
- `GET /api/agents/rating-evaluation` - source-aware Evaluation Agent review for immutable predictions
- `GET /api/agents/runs` - recent Agent run traces for observability
- `POST /api/agents/chat/sessions` - create a persistent chat session
- `GET /api/agents/chat/sessions` - list resumable chat sessions
- `GET /api/agents/chat/sessions/{session_id}` - restore messages and Agent response metadata
- `PATCH /api/agents/chat/sessions/{session_id}` - rename a chat session
- `DELETE /api/agents/chat/sessions/{session_id}` - permanently delete a chat session, messages, and run traces
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

Chat Eval V2 evaluates Query Understanding, raw Planner output, Tool Policy,
frozen tool Execution, Grounding, Final Answer and Efficiency independently.
The public Dev split has 120 cases; a private 40-case Holdout is injected with
`LIMITUPLAB_EVAL_HOLDOUT_PATH`.

The retired 50-case Golden was reviewed item by item in
`tests/fixtures/agent_chat_eval_v1_migration.json`; date, fixture, scope and
relation-evidence conflicts are rejected rather than silently copied into V2.

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --dataset dev --mode offline --trials 1 --summary-only
```

Offline mode replays deterministic plans and versioned tool facts. Raw Planner
accuracy is N/A. Live mode invokes the real Planner and Answer model while keeping
the tool facts frozen:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --dataset dev --mode live --sample-size 40 --trials 3 --seed night-1
```

The full release command requires Dev + Holdout, three trials and an independent
pinned Judge model:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --dataset all --mode live --trials 3 --judge
```

Completed artifacts are written under `output/agent-eval/<run_id>/`. The API only
reads `latest.json`; it never starts an evaluation. Online shadow mode samples
already persisted traces without rerunning or exposing the original question.
See `docs/Agent_Golden_Eval.md` for schema, Judge settings, metrics and gates.

The chat Agent also exposes a general `limit_up_events` internal tool for
same-day limit-up questions such as continued-board lists, board-height filters,
intraday-broken limit-up events, and topic/industry filters. This keeps broad
limit-up questions from being incorrectly routed through first-board-only tools.


