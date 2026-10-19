"""Shared latest-news snapshot, using the existing persistent JSON cache table."""

from contextlib import closing
from datetime import timedelta, timezone
from app.database import begin_write, connect, initialize_database
from app.models import FinanceNewsFacts

KEY = "system:finance-news-latest"


def read_snapshot(url):
    with closing(connect(database_url=url)) as db:
        initialize_database(db)
        row = db.execute("SELECT payload_json FROM agent_cache WHERE cache_key=?", (KEY,)).fetchone()
        return FinanceNewsFacts.model_validate_json(row[0]) if row else None


def write_snapshot(url, facts):
    raw = facts.model_dump_json()
    if len(raw.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Latest-news snapshot exceeds size limit")
    stamp = facts.fetched_at.astimezone(timezone.utc)
    with closing(connect(database_url=url)) as db, db:
        initialize_database(db)
        begin_write(db)
        result = db.execute("""INSERT INTO agent_cache(cache_key,scope,payload_json,expires_at,created_at)
            VALUES(?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET
            payload_json=excluded.payload_json,expires_at=excluded.expires_at,created_at=excluded.created_at
            WHERE excluded.created_at >= agent_cache.created_at""",
            (KEY, "latest-news", raw, (stamp + timedelta(days=7)).isoformat(), stamp.isoformat()))
        if not result.rowcount:
            raise ValueError("Latest-news snapshot would regress shared data")
