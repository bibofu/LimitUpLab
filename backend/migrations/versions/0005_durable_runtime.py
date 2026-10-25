"""Durable run metadata, immutable tool observations and native checkpoints.

Checkpoint DDL is frozen from langgraph-checkpoint-postgres 3.0.5 migrations
0..9. Ordinary indexes are created transactionally by Alembic, instead of the
upstream setup command's concurrent indexes. Runtime never executes DDL.
"""

from alembic import op

revision = "0005_durable_runtime"
down_revision = "0004_drop_quality_tables"
branch_labels = None
depends_on = None


def upgrade():
    for definition in (
        "thread_id TEXT", "runtime_version TEXT NOT NULL DEFAULT 'legacy'",
        "run_status TEXT NOT NULL DEFAULT 'pending'", "updated_at TEXT",
        "finished_at TEXT", "recovery_count INTEGER NOT NULL DEFAULT 0",
    ):
        op.execute(f"ALTER TABLE react_runs ADD COLUMN {definition}")
    op.execute("""UPDATE react_runs SET thread_id=session_id, updated_at=started_at,
        run_status=CASE WHEN response_json IS NOT NULL THEN 'completed'
                        WHEN active=1 THEN 'interrupted' ELSE 'failed' END""")
    op.execute("ALTER TABLE react_runs ALTER COLUMN thread_id SET NOT NULL")
    op.execute("""CREATE TABLE agent_tool_results (
        run_id TEXT NOT NULL REFERENCES react_runs(run_id) ON DELETE CASCADE,
        call_id TEXT NOT NULL, signature TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('started','complete')),
        payload_json TEXT, created_at TEXT NOT NULL,
        PRIMARY KEY(run_id,call_id))""")
    op.execute("CREATE TABLE checkpoint_migrations (v INTEGER PRIMARY KEY)")
    op.execute("""CREATE TABLE checkpoints (
        thread_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
        checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL,
        parent_checkpoint_id TEXT, type TEXT, checkpoint JSONB NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}',
        PRIMARY KEY(thread_id,checkpoint_ns,checkpoint_id))""")
    op.execute("""CREATE TABLE checkpoint_blobs (
        thread_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
        checkpoint_ns TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL,
        version TEXT NOT NULL, type TEXT NOT NULL, blob BYTEA,
        PRIMARY KEY(thread_id,checkpoint_ns,channel,version))""")
    op.execute("""CREATE TABLE checkpoint_writes (
        thread_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
        checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL,
        task_id TEXT NOT NULL, idx INTEGER NOT NULL, channel TEXT NOT NULL,
        type TEXT, blob BYTEA NOT NULL, task_path TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(thread_id,checkpoint_ns,checkpoint_id,task_id,idx))""")
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        op.create_index(table + "_thread_id_idx", table, ["thread_id"])
    for version in range(10):
        op.execute(f"INSERT INTO checkpoint_migrations(v) VALUES ({version})")


def downgrade():
    raise RuntimeError("Durable runtime contains user state; restore a verified backup to downgrade")
