"""Record usage of long-term memories without changing effective content."""

from alembic import op


revision = "0014_memory_usage"
down_revision = "0013_candidate_ttl"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE long_term_memories
        ADD COLUMN use_count BIGINT NOT NULL DEFAULT 0 CHECK(use_count>=0),
        ADD COLUMN last_used_at TIMESTAMPTZ""")
    op.execute("""CREATE INDEX memory_owner_usage
        ON long_term_memories(owner_id,last_used_at DESC)
        WHERE status='active' AND deleted_at IS NULL""")


def downgrade():
    raise RuntimeError(
        "Memory usage counters are operational user data; restore a verified backup to downgrade"
    )
