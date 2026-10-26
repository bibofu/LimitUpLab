"""Explicit delete candidates and scoped exception linkage.

A deletion is still only a proposal until confirmation. It must carry the
active target and exact revision, so it reuses the supersession columns while
``candidate_action`` distinguishes replacement from removal.
"""

from alembic import op


revision = "0011_memory_candidate_actions"
down_revision = "0010_candidate_consolidation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE long_term_memories
        ADD COLUMN candidate_action TEXT NOT NULL DEFAULT 'upsert'
            CHECK(candidate_action IN ('upsert','delete')),
        ADD CONSTRAINT memory_delete_candidate_target CHECK (
            candidate_action='upsert' OR supersedes_memory_id IS NOT NULL
        )""")
    op.execute("""CREATE INDEX memory_exception_relation
        ON memory_relations(owner_id,to_memory_id,from_memory_id)
        WHERE relation='exception_to'""")


def downgrade():
    raise RuntimeError(
        "Candidate delete actions are user-derived state; restore a verified backup to downgrade"
    )
