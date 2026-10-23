"""Drop retired answer-quality tables."""

from alembic import op

revision = "0004_drop_quality_tables"
down_revision = "0003_single_champion"
branch_labels = None
depends_on = None

QUALITY_TABLES = (
    "quality_judge_calls",
    "quality_budget_reservations",
    "quality_budget_usage",
    "quality_evaluations",
    "quality_delivery",
    "quality_reviews",
    "quality_signals",
    "quality_feedback",
    "quality_evidence_bundles",
    "quality_jobs",
    "quality_cases",
    "quality_trace_snapshots",
)


def upgrade():
    for table in QUALITY_TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')


def downgrade():
    raise RuntimeError("Retired answer-quality tables are not restored")
