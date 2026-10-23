"""Owner-scoped like/dislike feedback remains a product feature."""

from fastapi.testclient import TestClient

from app.agents.run_lifecycle import Journal
from app.main import app
from app.models import AgentChatRequest
from app.repositories import AnswerFeedbackRepository, ChatSessionRepository, FeedbackInput
from app.security import SESSION_COOKIE_NAME, sign_visitor_owner_id


def _run(owner: str, session_id: str = "feedback-session") -> str:
    ChatSessionRepository().create_session(session_id=session_id, owner_id=owner)
    journal = Journal()
    row, _ = journal.create(AgentChatRequest(
        session_id=session_id, message_id="feedback-question", message="解释已有研究数据",
    ), owner)
    return row["run_id"]


def test_feedback_is_owner_scoped_and_upserts(postgresql_database):
    owner = "visitor_" + "f" * 32
    run_id = _run(owner)
    repository = AnswerFeedbackRepository()

    saved = repository.save(run_id, owner, FeedbackInput(vote="up"))
    assert saved["vote"] == "up" and saved["reasons"] == []

    updated = repository.save(
        run_id, owner,
        FeedbackInput(vote="down", reasons=["data_error", "missing_source"], comment="日期不对"),
    )
    assert updated["vote"] == "down"
    assert updated["reasons"] == ["data_error", "missing_source"]
    assert updated["comment"] == "日期不对"
    assert repository.get(run_id, owner)["vote"] == "down"

    try:
        repository.get(run_id, "visitor_" + "e" * 32)
    except KeyError:
        pass
    else:
        raise AssertionError("feedback must not be readable by another owner")


def test_feedback_http_endpoints_return_the_saved_verdict(postgresql_database):
    owner = "visitor_" + "d" * 32
    run_id = _run(owner, session_id="feedback-http-session")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id(owner))
        saved = client.put(
            f"/api/agents/chat/runs/{run_id}/feedback",
            json={"vote": "down", "reasons": ["data_error"], "comment": ""},
        )
        assert saved.status_code == 200
        assert saved.json()["feedback"]["vote"] == "down"
        fetched = client.get(f"/api/agents/chat/runs/{run_id}/feedback")
        assert fetched.status_code == 200
        assert fetched.json()["feedback"]["reasons"] == ["data_error"]
