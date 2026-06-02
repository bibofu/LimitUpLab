# AGENTS.md

Development guidance for AI coding agents working on LimitUpLab.

This file is for Codex, Claude Code, and any other coding assistant that edits
this repository. Read it before making changes.

## Project Purpose

LimitUpLab is an after-close A-share limit-up review and research tool.

The product helps users review and analyze:

- daily limit-up and failed limit-up events
- first-board and continued-board structure
- seal quality and intraday break pressure
- market sentiment and index context
- stock detail K-line context after market close

LimitUpLab is not a live trading system and must not provide investment advice,
buy/sell recommendations, target prices, or promised returns.

## Current Architecture

```text
backend/
  app/
    main.py                 FastAPI entrypoint
    models.py               Pydantic API models
    database.py             SQLite connection and schema setup
    collectors/             AKShare-backed data collectors
    repositories/           persistence adapters
    routers/                API route modules
    services/               pure analysis helpers and sample data
  scripts/                  import scripts
  tests/                    unittest test suite

frontend/
  src/
    App.tsx                 dashboard, stock lists, stock detail page
    api.ts                  backend API client
    types.ts                frontend API types
    styles.css              dashboard styling
```

Backend data flow:

```text
AKShare collectors -> import scripts -> SQLite -> repository -> analysis/routes -> frontend
```

The default SQLite database is:

```text
backend/data/limituplab.sqlite
```

Database files, virtual environments, build output, and dependency folders must
not be committed.

## Data Sources And Scope

Current real data source:

- AKShare / Eastmoney limit-up and failed limit-up pools
- AKShare index daily data
- AKShare stock daily and intraday K-line sources

Treat external data as unstable. AKShare field names and provider behavior can
change, so collectors should keep parsing logic isolated and covered by tests
where practical.

The app is designed for after-close review. Do not add live intraday monitoring
or alerting unless the product scope is explicitly changed.

## Important Data Semantics

Be precise with these terms:

- `closed_limit = true`: the event closed at limit-up.
- `closed_limit = false`: the event came from the failed/open-board pool and did
  not close as a normal sealed limit-up event.
- `break_count > 0`: the stock opened or broke the seal at least once intraday.
- Current `failed_count` in `MarketSummary` means `break_count > 0`, so it is a
  seal-quality or intraday-break metric, not strictly only "closed_limit=false".

If you change these semantics, update:

- backend models and analysis tests
- frontend labels and copy
- README / docs

## Collaboration Rules

Prefer branch-based work when multiple agents are involved:

```bash
git checkout main
git pull --ff-only
git checkout -b codex/task-name
```

Use separate branches for different agents, for example:

```text
codex/backend-similar-market
claude/frontend-similar-market-panel
```

Do not let two agents edit the same large file at the same time unless the work
is deliberately coordinated. High-conflict files include:

- `frontend/src/App.tsx`
- `frontend/src/styles.css`
- `backend/app/services/analysis.py`
- `backend/app/models.py`

Before switching agents:

```bash
git status
git add .
git commit -m "Describe current work"
git push
```

If the work is not ready to commit, use a clearly named WIP branch rather than
leaving uncommitted changes for another agent to inherit.

## Suggested Agent Division

Codex is a good default owner for:

- backend repositories and services
- collectors and import scripts
- API contract changes
- tests and data semantics
- code review for correctness

Claude Code is a good default owner for:

- frontend layout and UX polish
- copywriting and interface clarity
- visual hierarchy and dashboard composition
- review of user flows

Either agent may work anywhere when asked, but keep ownership clear for each
task.

## Development Commands

Backend setup:

```bash
cd backend
python -m venv .venv
pip install -r requirements.txt
```

Windows PowerShell often works better with Python 3.13:

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run backend:

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Windows PowerShell:

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Run frontend:

```bash
cd frontend
npm install
npm run dev
```

Import real data:

```bash
cd backend
python scripts/import_limit_up_from_akshare.py --date 20260520 --replace-date
```

Run tests:

```bash
cd backend
python -m unittest discover -s tests
```

Build frontend:

```bash
cd frontend
npm run build
```

On this Windows environment, `npm.cmd run build` may be needed, and Vite may
require elevated execution because `esbuild` starts a child process.

## Testing Expectations

For backend logic changes, run:

```bash
cd backend
python -m unittest discover -s tests
python -m compileall app scripts tests
```

For frontend changes, run:

```bash
cd frontend
npm run build
```

If a command cannot be run because of permissions, network, or environment
limits, report that clearly in the final response.

## Coding Guidelines

Backend:

- Keep analysis functions pure when possible.
- Keep AKShare-specific parsing inside collectors.
- Keep SQLite access inside repositories/database helpers.
- Add tests for data semantics, parsing, and statistical behavior.
- Prefer standard library tools unless a dependency is already justified.

Frontend:

- Keep the app as a usable dashboard, not a landing page.
- Preserve after-close review framing.
- Avoid investment advice or predictive wording.
- Keep labels consistent with backend metrics.
- Make stock list rows and charts readable on desktop and mobile.

Comments:

- Public functions and important business helpers should have docstrings or
  JSDoc.
- Comments should explain intent, data semantics, or non-obvious external
  behavior.
- Avoid comments that merely restate a line of code.

## Git Hygiene

Never commit:

- `backend/data/*.sqlite`
- `.venv/`
- `node_modules/`
- `dist/`
- `.claude/settings.local.json`
- local caches, logs, or temporary test databases

Before committing:

```bash
git status --short
git diff --stat
```

Use concise commit messages:

```text
Add SQLite persistence
Add AKShare import script
Clarify failed-limit metric labels
```

## Agent Feature Direction

See `agent-dev.md` for product-level agent planning.

The short version:

- Facts first, narrative second.
- Use deterministic facts and statistics before any LLM output.
- Do not let an LLM invent market data.
- Keep every generated review tied to structured facts.
- Avoid buy/sell advice, return promises, and personalized recommendations.

