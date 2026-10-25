"""Owner-isolated explicit memories, deletion tombstones and operation receipts."""

from alembic import op

revision = "0006_long_term_memory"
down_revision = "0005_durable_runtime"
branch_labels = None
depends_on = None


def upgrade():
    # User memories outlive any individual chat. Provenance is verified on write
    # and intentionally has no cascading FK to chat/session/run history.
    op.execute("""CREATE TABLE long_term_memories (
        memory_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
        memory_type TEXT NOT NULL CHECK(memory_type IN ('preference','research_context','research_archive')),
        memory_key TEXT NOT NULL, value TEXT, topics_json TEXT NOT NULL,
        metadata_json TEXT NOT NULL, revision BIGINT NOT NULL CHECK(revision>0),
        expires_at TIMESTAMPTZ, deleted_at TIMESTAMPTZ,
        source_run_id TEXT, source_user_message TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(owner_id,memory_type,memory_key),
        CHECK((deleted_at IS NULL AND value IS NOT NULL) OR (deleted_at IS NOT NULL AND value IS NULL)))""")
    op.create_index("memory_owner_active", "long_term_memories", ["owner_id", "memory_type", "updated_at"],
                    postgresql_where="deleted_at IS NULL")
    op.execute("""CREATE TABLE memory_owner_versions (
        owner_id TEXT PRIMARY KEY, revision BIGINT NOT NULL CHECK(revision>=0))""")
    op.execute("""CREATE TABLE memory_operation_receipts (
        owner_id TEXT NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
        memory_id TEXT NOT NULL REFERENCES long_term_memories(memory_id),
        operation TEXT NOT NULL CHECK(operation IN ('put','delete')),
        result_revision BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(owner_id,operation_id))""")


def downgrade():
    raise RuntimeError("Long-term memories are user data; restore a verified backup to downgrade")
