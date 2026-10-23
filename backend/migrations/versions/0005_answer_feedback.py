"""Add owner-scoped like/dislike feedback for Agent answers."""

from alembic import op
import sqlalchemy as sa

revision = "0005_answer_feedback"
down_revision = "0004_drop_quality_tables"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "answer_feedback",
        sa.Column("run_id", sa.Text, primary_key=True),
        sa.Column("owner_id", sa.Text, nullable=False),
        sa.Column("vote", sa.Text, nullable=False),
        sa.Column("reasons_json", sa.Text, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("comment", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["react_runs.run_id"], ondelete="CASCADE",
            deferrable=True, initially="DEFERRED",
        ),
        sa.CheckConstraint("vote IN ('up','down')", name="answer_feedback_vote"),
    )


def downgrade():
    op.drop_table("answer_feedback")
