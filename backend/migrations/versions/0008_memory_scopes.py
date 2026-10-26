"""Versioned scoped memories with explicit free-text identities.

Additive foundation for the long-term memory refactor. Existing memories remain
readable through the same table and old API. The effective content version stays
in ``revision``; management counters and embeddings are separate so background
work cannot invalidate an in-flight answer.
"""

from alembic import op


revision = "0008_memory_scopes"
down_revision = "0007_run_pause_control"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE long_term_memories
        ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'global'
            CHECK(scope_type IN ('global','tool','intent','topic','entity','condition')),
        ADD COLUMN scope_json TEXT NOT NULL DEFAULT '{}',
        ADD COLUMN scope_key TEXT NOT NULL DEFAULT 'global',
        ADD COLUMN canonical_key TEXT,
        ADD COLUMN structured_json TEXT NOT NULL DEFAULT '{}',
        ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('candidate','active','superseded','revoked','expired','deleted')),
        ADD COLUMN management_revision BIGINT NOT NULL DEFAULT 0
            CHECK(management_revision>=0),
        ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'legacy'
            CHECK(source_kind IN ('legacy','explicit','auto','import')),
        ADD COLUMN source_quote TEXT,
        ADD COLUMN content_hash TEXT""")
    # Preserve the old typed identity as the canonical key.
    op.execute("UPDATE long_term_memories SET canonical_key=memory_key")
    op.execute("""UPDATE long_term_memories
        SET status=CASE
            WHEN deleted_at IS NOT NULL THEN 'deleted'
            WHEN expires_at IS NOT NULL AND expires_at<=clock_timestamp() THEN 'expired'
            ELSE 'active'
        END""")
    op.create_index(
        "memory_owner_status",
        "long_term_memories",
        ["owner_id", "status", "updated_at"],
    )
    op.create_index(
        "memory_owner_scope",
        "long_term_memories",
        ["owner_id", "scope_key", "status"],
    )
    op.create_index(
        "memory_canonical_active",
        "long_term_memories",
        ["owner_id", "memory_type", "scope_key", "canonical_key"],
        unique=True,
        postgresql_where=(
            "canonical_key IS NOT NULL AND status='active' AND deleted_at IS NULL"
        ),
    )
    op.execute("""CREATE TABLE memory_relations (
        relation_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        from_memory_id TEXT NOT NULL REFERENCES long_term_memories(memory_id) ON DELETE CASCADE,
        to_memory_id TEXT NOT NULL REFERENCES long_term_memories(memory_id) ON DELETE CASCADE,
        relation TEXT NOT NULL
            CHECK(relation IN ('supersedes','contradicts','exception_to','duplicate_of','merged_into')),
        reason TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(owner_id, from_memory_id, to_memory_id, relation))""")


def downgrade():
    raise RuntimeError(
        "Scoped memory contains user data; restore a verified backup to downgrade"
    )
