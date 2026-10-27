"""Add pgvector embeddings to active long-term memories."""

from alembic import op


revision = "0015_memory_embeddings"
down_revision = "0014_memory_usage"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    op.execute("""ALTER TABLE long_term_memories
        ADD COLUMN embedding public.vector(512),
        ADD COLUMN embedding_model TEXT,
        ADD COLUMN embedding_source_hash TEXT,
        ADD COLUMN embedding_updated_at TIMESTAMPTZ""")
    op.execute("""CREATE INDEX memory_embedding_hnsw
        ON long_term_memories USING hnsw (embedding public.vector_cosine_ops)
        WHERE embedding IS NOT NULL AND status='active' AND deleted_at IS NULL""")


def downgrade():
    raise RuntimeError(
        "Memory embeddings are derived user data; restore a verified backup to downgrade"
    )
