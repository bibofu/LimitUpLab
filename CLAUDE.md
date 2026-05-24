# LimitUpLab — Claude Code Project Guide

## Project

LimitUpLab is an open-source A-share limit-up stock review and research tool.
- **Stack**: Python FastAPI (backend) + TypeScript React (frontend) + SQLite
- **Data source**: AKShare (wraps Eastmoney)
- **Positioning**: after-close review lab, NOT trading advice or live monitoring
- **Product rule**: "facts first, narrative second"

## Build & Dev Commands

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate  # windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Import real data
cd backend
python scripts/import_limit_up_from_akshare.py --date 20260520 --replace-date

# Frontend
cd frontend
npm install
npm run dev             # opens http://localhost:5173
```

## Architecture

```
backend/app/
  main.py               FastAPI entry, CORS, route registration
  models.py             Pydantic models (LimitUpEvent, MarketSummary, StockKLineBar, etc.)
  database.py           SQLite connection + schema init
  collectors/           AKShare data collectors (network-bound)
  repositories/         SQLite persistence layer (SQLiteLimitUpRepository)
  routers/              API routes (market, limit_up, analysis, stocks)
  services/
    analysis.py         Pure stateless analysis helpers (no db/network I/O)
    sample_data.py      Hardcoded fallback events + indices
  agents/               (planned) LLM agent layer

frontend/src/
  App.tsx               All components in one file (SPA with react-router)
  api.ts                API client (wraps fetch)
  types.ts              Shared frontend TS types
  styles.css            Responsive CSS, red-green Chinese market colors
```

## Code Conventions

### Python
- No docstrings or comments unless the WHY is non-obvious
- Analysis functions are pure (no db/network), testable with plain lists of events
- Parsing helpers are file-local (`_safe_int`, `_safe_float`, `_parse_hhmmss`)
- Dates: `date` objects internally, `YYYYMMDD` or ISO strings at boundaries
- Time values in AKShare come as `HHMMSS` strings, normalized to `datetime.time`
- Collector functions raise `ValueError` for bad input, `RequestException` for network issues
- Routers return Pydantic response models directly; `response_model=` declared on decorators
- DB writes happen only in repository/scripts; services never write to disk

### SQLite
- Table: `limit_up_events`, PK on `(trade_date, symbol)`
- Repository uses `upsert_events` (INSERT ON CONFLICT DO UPDATE) for idempotent imports
- When DB is empty, `SQLiteLimitUpRepository(seed_if_empty=True)` auto-seeds sample data
- All connections closed in `finally` blocks

### TypeScript / React
- No external chart libraries; SVG candlestick chart drawn by hand
- Market convention: red (up) / green (down) — `#d92d20` / `#039855`
- Single `App.tsx` file holds all components; routing via `react-router-dom` v6
- All state fetched from backend on load; no client-side persistence
- CSS uses `min()` for responsive widths, CSS Grid for layouts

### General
- No emojis in code, commit messages, or UI
- No backwards-compatibility shims or feature flags
- New features go in the pattern of existing modules (collector → repository → service → router)
- Frontend features reuse existing API client patterns (`request<T>(path)`)

## Key API Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| GET | `/api/market/overview` | Dashboard summary + sentiment + indices |
| GET | `/api/limit-up/events` | All events, newest first |
| GET | `/api/limit-up/first-board` | Latest-day first-board events |
| GET | `/api/limit-up/continued-board` | Latest-day continued-board events |
| GET | `/api/limit-up/failed` | Latest-day events with break_count > 0 |
| GET | `/api/limit-up/recent?days=3` | Events from last N trading days |
| GET | `/api/analysis/continuation` | Continuation probability by board height |
| GET | `/api/analysis/failed-rate` | Break rate by board height |
| GET | `/api/analysis/post-performance` | Avg returns by board height |
| GET | `/api/stocks/{symbol}/kline?days=5` | Daily K-line bars |
| GET | `/api/stocks/{symbol}/trading-day-kline?period=5` | Intraday K-line for review |

## Planned Agent Features (agent-dev.md)

Phase order: Similar Market → Daily/Stock Review (rule-based) → LLM narrative upgrade → Research Query
- Agents receive facts from `services/`, not raw DB access
- LLM output must be flagged: `facts` (structured) + `narrative` (human-readable)
- Guardrails: no buy/sell advice, no target prices, no return promises
- Cache agent output by `(trade_date, symbol, algorithm_version)`

## Agent Collaboration Notes

When working alongside Codex or other AI agents on this project:
- One agent per git branch; merge before switching
- Prefer splitting work by layer: one on backend, one on frontend
- New API contracts should be agreed (model shape + route) before both sides implement
- Commit messages in English, describing why not what
