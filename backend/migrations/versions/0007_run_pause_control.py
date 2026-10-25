"""Durable user control intents; graph execution remains in native checkpoints."""

from alembic import op

revision = "0007_run_pause_control"
down_revision = "0006_long_term_memory"
branch_labels = None
depends_on = None


def upgrade():
    for definition in (
        "control_revision BIGINT NOT NULL DEFAULT 0 CHECK(control_revision>=0)",
        "resume_count BIGINT NOT NULL DEFAULT 0 CHECK(resume_count>=0)",
        "pause_checkpoint_id TEXT", "pause_interrupt_id TEXT", "paused_at TEXT",
        "pause_mode TEXT CHECK(pause_mode IN ('interrupt','checkpoint','delivery'))",
    ):
        op.execute(f"ALTER TABLE react_runs ADD COLUMN {definition}")
    op.execute("""CREATE TABLE run_control_operations (
        owner_id TEXT NOT NULL, operation_id TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES react_runs(run_id) ON DELETE CASCADE,
        action TEXT NOT NULL CHECK(action IN ('pause','resume')),
        expected_revision BIGINT NOT NULL CHECK(expected_revision>=0),
        request_hash TEXT NOT NULL, result_revision BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(owner_id,operation_id))""")
    op.create_index("run_control_operations_run", "run_control_operations", ["run_id"])


def downgrade():
    raise RuntimeError("Paused runs are user state; restore a verified backup to downgrade")
