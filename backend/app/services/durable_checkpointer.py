"""Native LangGraph persistence with the application's transactional fencing.

Only the cursor/transaction boundary is adapted; serialization, checkpoint
formats and pending-write semantics remain upstream AsyncPostgresSaver's.
"""

from contextlib import asynccontextmanager
from contextvars import ContextVar
from hashlib import sha256

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

from app.services.runtime_coordination import LeaseLost

_WRITE_GUARD = ContextVar("checkpoint_write_guard", default=None)
WRITE_LOCK = int.from_bytes(sha256(b"limituplab:write").digest()[:8], "big", signed=True)


def psycopg_url(url):
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


class FencedPostgresSaver(AsyncPostgresSaver):
    """Every native write verifies run ownership under the shared DB write lock."""

    async def aput(self, config, checkpoint, metadata, new_versions):
        token = _WRITE_GUARD.set(config["configurable"])
        try:
            return await super().aput(config, checkpoint, metadata, new_versions)
        finally:
            _WRITE_GUARD.reset(token)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        token = _WRITE_GUARD.set(config["configurable"])
        try:
            return await super().aput_writes(config, writes, task_id, task_path)
        finally:
            _WRITE_GUARD.reset(token)

    @asynccontextmanager
    async def _cursor(self, *, pipeline=False):
        # A pool checkout owns its transaction. No DB transaction spans a model
        # or tool call, and no session-level advisory lock can leak into the pool.
        async with self.conn.connection() as conn:
            async with conn.transaction(), conn.cursor(binary=True, row_factory=dict_row) as cur:
                if pipeline:
                    guard = _WRITE_GUARD.get()
                    if guard is None:
                        raise LeaseLost("Checkpoint writes require an execution owner")
                    await cur.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK,))
                    await cur.execute("""SELECT r.run_id FROM react_runs r
                        JOIN runtime_leases l ON l.name='react:' || r.run_id
                        JOIN chat_sessions s ON s.session_id=r.session_id
                        WHERE r.run_id=%s AND r.owner_id=%s AND s.owner_id=%s
                          AND r.thread_id=%s AND r.active=1
                          AND l.owner_token=%s AND l.fence=%s
                          AND l.expires_at>EXTRACT(EPOCH FROM clock_timestamp())""",
                        (guard.get("run_id"), guard.get("owner_id"), guard.get("owner_id"),
                         guard.get("thread_id"), guard.get("lease_token"), guard.get("fence")))
                    if await cur.fetchone() is None:
                        raise LeaseLost("Checkpoint execution lease expired or was replaced")
                yield cur

    async def setup(self):
        raise RuntimeError("Checkpoint schema is managed by Alembic")

    async def adelete_thread(self, thread_id):
        raise RuntimeError("Delete through owner-checked ChatSessionRepository; FK cleanup is atomic")
