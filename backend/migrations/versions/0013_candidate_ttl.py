"""Expire unreviewed candidates so they do not accumulate without a UI."""

from alembic import op


revision = "0013_candidate_ttl"
down_revision = "0012_memory_auto_activation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE long_term_memories
        ADD COLUMN candidate_expires_at TIMESTAMPTZ""")
    op.execute("""UPDATE long_term_memories
        SET candidate_expires_at=created_at+interval '30 days'
        WHERE status='candidate' AND candidate_expires_at IS NULL""")
    op.execute("""CREATE INDEX memory_candidate_expiry
        ON long_term_memories(candidate_expires_at)
        WHERE status='candidate'""")


def downgrade():
    raise RuntimeError(
        "Candidate TTL is user-derived operational state; restore a verified backup to downgrade"
    )
