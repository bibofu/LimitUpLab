"""Add the LLM judge call ledger."""

from alembic import op


revision = "0019_review_judge_calls"
down_revision = "0018_review_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE answer_review_judge_calls (
        call_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES answer_review_jobs(job_id) ON DELETE CASCADE,
        run_id TEXT NOT NULL REFERENCES react_runs(run_id) ON DELETE CASCADE,
        model TEXT NOT NULL,
        judge_version TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('succeeded','error','skipped')),
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        total_tokens INTEGER,
        token_usage_complete INTEGER NOT NULL DEFAULT 0,
        estimated_cost_usd REAL NOT NULL DEFAULT 0,
        pricing_configured INTEGER NOT NULL DEFAULT 0,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        error_type TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
    op.create_index(
        "answer_review_judge_calls_time",
        "answer_review_judge_calls",
        ["created_at"],
    )
    op.create_index(
        "answer_review_judge_calls_job",
        "answer_review_judge_calls",
        ["job_id", "created_at"],
    )


def downgrade():
    raise RuntimeError(
        "Judge calls are cost evidence; restore a verified backup to downgrade"
    )
