"""Add owner-scoped like/dislike feedback for completed Agent answers."""

from alembic import op


revision = "0016_answer_feedback"
down_revision = "0015_memory_embeddings"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE answer_feedback (
        run_id TEXT PRIMARY KEY REFERENCES react_runs(run_id) ON DELETE CASCADE,
        owner_id TEXT NOT NULL,
        vote TEXT NOT NULL CHECK (vote IN ('up','down')),
        reasons_json TEXT NOT NULL DEFAULT '[]',
        comment TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
    op.create_index(
        "answer_feedback_vote_updated",
        "answer_feedback",
        ["vote", "updated_at"],
    )
    op.create_index(
        "answer_feedback_owner",
        "answer_feedback",
        ["owner_id", "updated_at"],
    )


def downgrade():
    raise RuntimeError(
        "Answer feedback is user-derived state; restore a verified backup to downgrade"
    )
