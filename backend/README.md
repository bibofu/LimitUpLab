# LimitUpLab Backend

Python backend for collecting, storing, and analyzing A-share limit-up events.

## Quick Start

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.

## Endpoints

- `GET /health` - service health check
- `GET /api/market/summary` - market sentiment summary
- `GET /api/limit-up/events` - latest limit-up events
- `GET /api/analysis/continuation` - board continuation probability
- `GET /api/analysis/failed-rate` - failed limit-up rate by board height
- `GET /api/analysis/post-performance` - next-day and short-window return stats
