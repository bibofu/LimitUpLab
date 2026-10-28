"""Owner-scoped answer feedback and its aggregate counters."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.agents.run_lifecycle import RunLifecycle
from app.database import connect
from app.main import app
from app.models import AgentChatRequest, AnswerFeedbackInput, ChatSessionMessage
from app.repositories import (
    AnswerFeedbackRepository,
    ChatSessionRepository,
    FeedbackTargetIncomplete,
    FeedbackTargetNotFound,
)
from app.security import SESSION_COOKIE_NAME, sign_visitor_owner_id


def _owner(suffix: str) -> str:
    return "visitor_" + suffix * 32


def _completed_run(owner: str, session_id: str) -> str:
    ChatSessionRepository().create_session(session_id=session_id, owner_id=owner)
    row, _ = RunLifecycle().create(
        AgentChatRequest(
            session_id=session_id,
            message_id=f"question-{session_id}",
            message="解释已有研究数据",
        ),
        owner,
    )
    with connect() as connection:
        connection.execute(
            "UPDATE react_runs SET response_json=?, active=0, run_status='completed' WHERE run_id=?",
            ('{"answer":"研究回答","task_status":"complete"}', row["run_id"]),
        )
    return row["run_id"]


def _append_answer(owner: str, session_id: str, run_id: str) -> None:
    ChatSessionRepository().append_message(
        ChatSessionMessage(
            message_id=f"answer-{run_id}",
            session_id=session_id,
            role="assistant",
            content="研究回答",
            run_id=run_id,
            metadata={},
            created_at=datetime.now(timezone.utc),
        ),
        owner_id=owner,
    )


def test_feedback_is_owner_scoped_and_normalizes(postgresql_database):
    owner = _owner("a")
    run_id = _completed_run(owner, "feedback-session")
    repository = AnswerFeedbackRepository()

    saved = repository.save(run_id, owner, AnswerFeedbackInput(vote="up"))
    assert saved.vote == "up" and saved.reasons == []

    positive = _completed_run(owner, "feedback-positive-normalize")
    saved_positive = repository.save(
        positive,
        owner,
        AnswerFeedbackInput(vote="up", reasons=["data_error"], comment="旧说明"),
    )
    assert saved_positive.reasons == [] and saved_positive.comment == ""

    updated = repository.save(
        run_id,
        owner,
        AnswerFeedbackInput(
            vote="down",
            reasons=["data_error", "data_error", "missing_source"],
            comment="  日期\x00不对  ",
        ),
    )
    assert updated.vote == "down"
    assert updated.reasons == ["data_error", "missing_source"]
    assert updated.comment == "日期不对"
    assert repository.get(run_id, owner).comment == "日期不对"

    with pytest.raises(FeedbackTargetNotFound):
        repository.get(run_id, _owner("b"))

    assert repository.delete(run_id, owner) is True
    assert repository.get(run_id, owner) is None
    assert repository.delete(run_id, owner) is False


def test_feedback_requires_a_completed_answer(postgresql_database):
    owner = _owner("c")
    session_id = "feedback-incomplete"
    ChatSessionRepository().create_session(session_id=session_id, owner_id=owner)
    row, _ = RunLifecycle().create(
        AgentChatRequest(
            session_id=session_id,
            message_id="question-incomplete",
            message="尚未完成的问题",
        ),
        owner,
    )

    with pytest.raises(FeedbackTargetIncomplete):
        AnswerFeedbackRepository().save(
            row["run_id"], owner, AnswerFeedbackInput(vote="down"),
        )


def test_feedback_http_round_trip_and_session_hydration(postgresql_database):
    owner = _owner("d")
    session_id = "feedback-http-session"
    run_id = _completed_run(owner, session_id)
    _append_answer(owner, session_id, run_id)

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id(owner))
        saved = client.put(
            f"/api/agents/chat/runs/{run_id}/feedback",
            json={"vote": "down", "reasons": ["wrong_scope"], "comment": "日期范围不对"},
        )
        assert saved.status_code == 200
        assert saved.json()["feedback"]["vote"] == "down"

        fetched = client.get(f"/api/agents/chat/runs/{run_id}/feedback")
        assert fetched.status_code == 200
        assert fetched.json()["feedback"]["reasons"] == ["wrong_scope"]

        detail = client.get(f"/api/agents/chat/sessions/{session_id}")
        assert detail.status_code == 200
        answer = next(
            item for item in detail.json()["messages"] if item["run_id"] == run_id
        )
        assert answer["feedback"]["comment"] == "日期范围不对"

        deleted = client.delete(f"/api/agents/chat/runs/{run_id}/feedback")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}

        after = client.get(f"/api/agents/chat/runs/{run_id}/feedback")
        assert after.json()["feedback"] is None


def test_deleting_a_session_removes_its_feedback(postgresql_database):
    owner = _owner("g")
    session_id = "feedback-delete-session"
    run_id = _completed_run(owner, session_id)
    repository = AnswerFeedbackRepository()
    repository.save(run_id, owner, AnswerFeedbackInput(vote="down"))

    assert ChatSessionRepository().delete_session(session_id, owner_id=owner) is True

    with connect() as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) AS count FROM answer_feedback WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert remaining["count"] == 0


def test_feedback_summary_and_admin_endpoint(postgresql_database, monkeypatch):
    up_owner = _owner("e")
    down_owner = _owner("f")
    up_run = _completed_run(up_owner, "feedback-summary-up")
    down_run = _completed_run(down_owner, "feedback-summary-down")
    repository = AnswerFeedbackRepository()
    repository.save(up_run, up_owner, AnswerFeedbackInput(vote="up"))
    repository.save(
        down_run,
        down_owner,
        AnswerFeedbackInput(vote="down", reasons=["data_error"]),
    )

    summary = repository.summary(days=7)
    assert summary.up_count == 1
    assert summary.down_count == 1
    assert summary.vote_count == 2
    assert summary.answered_count == 2
    assert summary.like_ratio == 0.5
    assert summary.feedback_coverage == 1.0
    assert summary.down_per_100_answers == 50.0
    assert [(item.reason, item.count) for item in summary.reasons] == [("data_error", 1)]

    monkeypatch.setenv("LIMITUPLAB_ADMIN_KEY", "k" * 32)
    with TestClient(app) as client:
        assert client.get("/api/agents/feedback/summary").status_code == 401
        response = client.get(
            "/api/agents/feedback/summary?days=7",
            headers={"X-LimitUpLab-Admin-Key": "k" * 32},
        )
        assert response.status_code == 200
        assert response.json()["vote_count"] == 2
