"""Record whether a memory was written manually or by the safe auto policy."""

from alembic import op


revision = "0012_memory_auto_activation"
down_revision = "0011_memory_candidate_actions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE long_term_memories
        ADD COLUMN activation_kind TEXT NOT NULL DEFAULT 'none'
            CHECK(activation_kind IN ('none','manual','safe_auto')),
        ADD COLUMN activation_reason TEXT""")
    # Existing non-candidate rows predate automatic activation and were explicit.
    op.execute("""UPDATE long_term_memories SET activation_kind='manual'
        WHERE status<>'candidate'""")
    op.execute("""CREATE INDEX memory_owner_activation
        ON long_term_memories(owner_id,activation_kind,updated_at)""")


def downgrade():
    raise RuntimeError(
        "Memory activation provenance is user data; restore a verified backup to downgrade"
    )
