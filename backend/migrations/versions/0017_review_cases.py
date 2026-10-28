"""Add review signals, cases and case audit events."""

from alembic import op


revision = "0017_review_cases"
down_revision = "0016_answer_feedback"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE answer_review_cases (
        case_id TEXT PRIMARY KEY,
        run_id TEXT UNIQUE REFERENCES react_runs(run_id) ON DELETE SET NULL,
        session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
        owner_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'new'
            CHECK(status IN ('new','confirmed','fixing','verifying','closed','dismissed','insufficient')),
        source TEXT NOT NULL
            CHECK(source IN ('user_feedback','deterministic','judge','sample','manual')),
        severity TEXT NOT NULL DEFAULT 'low'
            CHECK(severity IN ('info','low','medium','high')),
        intent TEXT,
        task_status TEXT,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '{}',
        trace_url TEXT,
        symptom TEXT NOT NULL DEFAULT '',
        root_cause TEXT NOT NULL DEFAULT '',
        fix_commit TEXT NOT NULL DEFAULT '',
        fix_version TEXT NOT NULL DEFAULT '',
        verification TEXT NOT NULL DEFAULT '',
        revision INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        reviewed_at TIMESTAMPTZ,
        closed_at TIMESTAMPTZ)""")
    op.execute("""CREATE TABLE answer_review_signals (
        signal_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES react_runs(run_id) ON DELETE CASCADE,
        case_id TEXT REFERENCES answer_review_cases(case_id) ON DELETE SET NULL,
        session_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        kind TEXT NOT NULL
            CHECK(kind IN ('downvote','deterministic','judge','sample','run_error','grounding','manual')),
        severity TEXT NOT NULL
            CHECK(severity IN ('info','low','medium','high')),
        detector TEXT NOT NULL,
        detector_version TEXT NOT NULL,
        source_ref TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        processed_at TIMESTAMPTZ,
        UNIQUE(run_id, detector, detector_version, source_ref))""")
    op.execute("""CREATE TABLE answer_review_case_events (
        event_id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL REFERENCES answer_review_cases(case_id) ON DELETE CASCADE,
        action TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT,
        changes_json TEXT NOT NULL DEFAULT '{}',
        note TEXT NOT NULL DEFAULT '',
        actor TEXT NOT NULL DEFAULT 'admin',
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
    op.create_index(
        "answer_review_cases_status_updated",
        "answer_review_cases",
        ["status", "updated_at"],
    )
    op.create_index(
        "answer_review_signals_pending",
        "answer_review_signals",
        ["processed_at", "created_at"],
    )
    op.create_index(
        "answer_review_signals_case",
        "answer_review_signals",
        ["case_id", "created_at"],
    )
    op.create_index(
        "answer_review_case_events_case",
        "answer_review_case_events",
        ["case_id", "created_at"],
    )


def downgrade():
    raise RuntimeError(
        "Review cases contain user-derived evidence; restore a verified backup to downgrade"
    )
