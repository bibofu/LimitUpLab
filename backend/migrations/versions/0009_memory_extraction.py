"""Background memory candidate extraction with a transactional outbox."""

from alembic import op


revision = "0009_memory_extraction"
down_revision = "0008_memory_scopes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE memory_extraction_jobs (
        job_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES react_runs(run_id) ON DELETE CASCADE,
        source_message_id TEXT NOT NULL,
        extractor_version TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','processing','succeeded','skipped','failed')),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
        available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        locked_by TEXT,
        locked_until TIMESTAMPTZ,
        result_json TEXT,
        error_message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        finished_at TIMESTAMPTZ,
        UNIQUE(source_message_id, extractor_version))""")
    op.create_index(
        "memory_extraction_pending",
        "memory_extraction_jobs",
        ["status", "available_at"],
    )
    op.create_index(
        "memory_extraction_owner",
        "memory_extraction_jobs",
        ["owner_id", "created_at"],
    )
    op.create_index(
        "memory_candidate_open",
        "long_term_memories",
        ["owner_id", "memory_type", "scope_key", "canonical_key"],
        unique=True,
        postgresql_where=(
            "canonical_key IS NOT NULL AND status='candidate' AND deleted_at IS NULL"
        ),
    )


def downgrade():
    raise RuntimeError(
        "Memory extraction jobs contain user-derived state; restore a verified backup to downgrade"
    )
