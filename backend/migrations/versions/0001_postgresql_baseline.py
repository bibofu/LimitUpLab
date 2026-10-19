"""Frozen PostgreSQL baseline for SQLite v12 and ReAct/quality storage."""

from alembic import op

from app.storage.schema import baseline_metadata

revision = "0001_postgresql_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    baseline_metadata().create_all(op.get_bind(), checkfirst=False)
    op.execute("""CREATE FUNCTION reject_immutable_update() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Audit record is immutable' USING ERRCODE = '23514';
        END $$""")
    for name, table in (
        ("quality_reviews_no_update", "quality_reviews"),
        ("quality_bundles_no_update", "quality_evidence_bundles"),
    ):
        op.execute(f"CREATE TRIGGER {name} BEFORE UPDATE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION reject_immutable_update()")
    op.execute("""CREATE TRIGGER quality_evaluations_terminal_no_update
        BEFORE UPDATE ON quality_evaluations FOR EACH ROW
        WHEN (OLD.execution_status <> 'running')
        EXECUTE FUNCTION reject_immutable_update()""")


def downgrade():
    raise RuntimeError("Baseline downgrade is destructive; restore a verified backup instead")
