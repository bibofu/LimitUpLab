"""Enforce a single active champion independently of worker count."""

from alembic import op
import sqlalchemy as sa

revision = "0003_single_champion"
down_revision = "0002_runtime_coordination"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("one_champion_policy", "scoring_policies", ["status"],
                    unique=True, postgresql_where=sa.text("status = 'champion'"))


def downgrade():
    op.drop_index("one_champion_policy", table_name="scoring_policies")
