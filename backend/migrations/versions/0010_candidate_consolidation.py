"""Allow one active identity to coexist with a pending update candidate.

The original key-only uniqueness prevented a candidate from coexisting with
the active memory it proposes to replace. Scope is part of memory identity, so
active uniqueness moves to ``(owner, type, key, scope)`` while candidate
openness remains enforced by ``memory_candidate_open``.
"""

from alembic import op


revision = "0010_candidate_consolidation"
down_revision = "0009_memory_extraction"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE long_term_memories
        ADD COLUMN supersedes_memory_id TEXT,
        ADD COLUMN supersedes_revision BIGINT,
        ADD CONSTRAINT memory_supersedes_pair CHECK (
            (supersedes_memory_id IS NULL AND supersedes_revision IS NULL)
            OR (supersedes_memory_id IS NOT NULL AND supersedes_revision > 0)
        )""")
    op.execute("""ALTER TABLE long_term_memories
        DROP CONSTRAINT IF EXISTS long_term_memories_owner_id_memory_type_memory_key_key""")
    op.execute("""CREATE UNIQUE INDEX memory_active_identity
        ON long_term_memories(owner_id,memory_type,memory_key,scope_key)
        WHERE status='active' AND deleted_at IS NULL""")
    op.execute("""CREATE INDEX memory_candidate_target
        ON long_term_memories(owner_id,supersedes_memory_id)
        WHERE status='candidate' AND deleted_at IS NULL""")


def downgrade():
    raise RuntimeError(
        "Candidate supersession links are user data; restore a verified backup to downgrade"
    )
