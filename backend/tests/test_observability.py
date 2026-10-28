"""Observability totals, ratios, buckets and the protected summary API."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.database import connect
from app.main import app
from app.services.observability import ObservabilityService
from app.services.review_cases import ReviewCaseService
from app.services.review_time import SHANGHAI
from review_fixtures import completed_run, downvote, owner as fixture_owner


def _usage(run_id: str, owner: str, session_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        db.execute(
            """
            INSERT INTO agent_usage_events (
                usage_id, run_id, session_id, owner_id, ip_hash, status, model,
                llm_call_count, failed_llm_call_count, prompt_tokens,
                completion_tokens, total_tokens, token_usage_complete,
                planner_prompt_chars, answer_prompt_chars, answer_chars,
                estimated_cost_usd, started_at, finished_at, duration_ms,
                error_message
            )
            VALUES (?, ?, ?, ?, ?, 'success', 'test-model', 2, 0, 100, 50, 150, 1,
                    1000, 2000, 120, 0.002, ?, ?, 1234, NULL)
            """,
            (
                "usage_" + run_id, run_id, session_id, owner, "ip-hash",
                now, now,
            ),
        )


def _judge_call(run_id: str) -> None:
    with connect() as db:
        db.execute(
            """
            INSERT INTO answer_review_jobs (
                job_id, run_id, kind, priority, status, detector_version,
                config_fingerprint, created_at, finished_at
            )
            VALUES ('job_obs', ?, 'judge', 50, 'succeeded',
                    'answer-judge-v1', 'obs-test', clock_timestamp(), clock_timestamp())
            """,
            (run_id,),
        )
        db.execute(
            """
            INSERT INTO answer_review_judge_calls (
                call_id, job_id, run_id, model, judge_version, prompt_version,
                status, prompt_tokens, completion_tokens, total_tokens,
                token_usage_complete, estimated_cost_usd, pricing_configured,
                duration_ms
            )
            VALUES ('call_obs', 'job_obs', ?, 'test-judge', 'answer-judge-v1',
                    'answer-judge-prompt-v1', 'succeeded', 10, 5, 15, 1, 0, 0, 20)
            """,
            (run_id,),
        )


def test_observability_summary_counts_denominators_and_buckets(postgresql_database):
    owner = fixture_owner("d1")
    session_id = "observability-session"
    run_id = completed_run(owner, session_id)
    _usage(run_id, owner, session_id)
    downvote(owner, run_id, ["data_error"], "数字不对")
    ReviewCaseService().aggregate_signals(limit=10)
    _judge_call(run_id)

    summary = ObservabilityService().summary(days=7)
    totals = summary.totals
    assert len(summary.buckets) == 7
    assert totals.request_count == 1
    assert totals.answered_count == 1
    assert totals.distinct_owners == 1
    assert totals.distinct_sessions == 1
    assert totals.total_tokens == 150
    assert totals.measured_request_count == 1
    assert totals.incomplete_token_count == 0
    assert totals.estimated_cost_usd == 0.002
    assert totals.vote_count == 1
    assert totals.like_ratio == 0.0
    assert totals.feedback_coverage == 1.0
    assert totals.down_per_100_answers == 100.0
    assert totals.case_count == 1
    assert totals.open_case_count == 1
    assert totals.p50_duration_ms == 1234.0
    assert totals.p95_duration_ms == 1234.0
    assert totals.judge_call_count == 1
    assert totals.judge_total_tokens == 15
    assert totals.judge_priced_call_count == 0
    assert summary.feedback_reasons[0].reason == "data_error"
    assert summary.case_status_counts == {"new": 1}
    today = datetime.now(SHANGHAI).date()
    bucket = next(item for item in summary.buckets if item.date == today)
    assert bucket.request_count == 1
    assert bucket.answered_count == 1
    assert bucket.down_count == 1
    assert bucket.case_count == 1
    assert any("匿名 cookie" in note for note in summary.notes)


def test_observability_api_requires_the_admin_key(postgresql_database, monkeypatch):
    owner = fixture_owner("d2")
    session_id = "observability-api"
    run_id = completed_run(owner, session_id)
    _usage(run_id, owner, session_id)
    monkeypatch.setenv("LIMITUPLAB_ADMIN_KEY", "o" * 32)
    headers = {"X-LimitUpLab-Admin-Key": "o" * 32}

    with TestClient(app) as client:
        assert client.get("/api/admin/observability/summary").status_code == 401
        response = client.get(
            "/api/admin/observability/summary?days=7", headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["totals"]["request_count"] == 1
        assert response.json()["timezone"] == "Asia/Shanghai"
