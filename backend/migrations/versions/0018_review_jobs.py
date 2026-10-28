"""Add the review sampling and detector job outbox."""

from alembic import op


revision = "0018_review_jobs"
down_revision = "0017_review_cases"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE answer_review_jobs (
        job_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES react_runs(run_id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK(kind IN ('deterministic','sample','judge')),
        priority INTEGER NOT NULL DEFAULT 100,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','processing','succeeded','skipped','failed','deferred')),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
        available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        locked_by TEXT,
        locked_until TIMESTAMPTZ,
        detector_version TEXT NOT NULL,
        config_fingerprint TEXT NOT NULL,
        result_json TEXT,
        error_message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        finished_at TIMESTAMPTZ,
        UNIQUE(run_id, kind, config_fingerprint))""")
    op.create_index(
        "answer_review_jobs_pending",
        "answer_review_jobs",
        ["status", "priority", "available_at"],
    )
    op.create_index(
        "answer_review_jobs_run",
        "answer_review_jobs",
        ["run_id", "kind"],
    )


def downgrade():
    raise RuntimeError(
        "Review jobs contain user-derived state; restore a verified backup to downgrade"
    )
