"""Quality case listings work without SQLite access when PostgreSQL is selected."""

import sqlite3

import pytest

from app.agents.react_runtime.lifecycle import Journal
from app.models import AgentChatRequest
from app.repositories.chat_session_repository import SQLiteChatSessionRepository
from app.repositories.quality_repository import QualityRepository


@pytest.fixture(params=["sqlite", "postgresql"])
def case_store(request, tmp_path, monkeypatch):
    if request.param == "postgresql":
        request.getfixturevalue("postgresql_database")
        path = None

        def forbidden_sqlite(*args, **kwargs):
            raise AssertionError("PostgreSQL quality operations must not open SQLite")

        monkeypatch.setattr(sqlite3, "connect", forbidden_sqlite)
    else:
        path = tmp_path / "quality-cases.sqlite"
    SQLiteChatSessionRepository(path).ensure_session("session", owner_id="owner")
    return QualityRepository(path), Journal(path)


def add_case(store, identity, sources=("feedback",)):
    repo, journal = store
    row, _ = journal.create(AgentChatRequest(
        session_id="session", message_id=identity, message=f"解释测试证据 {identity}",
    ), "owner")
    response = journal.finish(row["run_id"], "owner", {
        "answer": "测试用研究解释。", "intent": "research", "task_status": "complete",
        "generated_by": "quality-backend-test",
    })
    with repo.connection() as db:
        for index, source in enumerate(sources):
            case_id = repo._signal(db, row["run_id"], response, {
                "source": source, "code": f"signal-{index}", "severity": "medium",
                "summary": "测试信号", "detector_version": "test-v1",
            })
    return case_id


def test_empty_case_list(case_store):
    repo, _ = case_store
    assert repo.list_cases() == {"items": [], "total": 0}


def test_case_sources_are_distinct_but_signal_count_is_complete(case_store):
    repo, _ = case_store
    case_id = add_case(case_store, "multiple", ("feedback", "feedback", "delivery"))
    result = repo.list_cases()
    assert result["total"] == 1
    item = result["items"][0]
    assert item["case_id"] == case_id
    assert set(item["sources"]) == {"feedback", "delivery"}
    assert len(item["sources"]) == 2
    assert item["signal_count"] == 3
    assert item["execution_status"] == "succeeded"
    assert item["question"] == "解释测试证据 multiple"


def test_case_without_signals_has_empty_sources(case_store):
    repo, _ = case_store
    case_id = add_case(case_store, "no-signals")
    with repo.connection() as db:
        db.execute("DELETE FROM quality_signals WHERE case_id=?", (case_id,))
    result = repo.list_cases()
    assert result["total"] == 1
    assert result["items"][0]["sources"] == []
    assert result["items"][0]["signal_count"] == 0
    assert repo.list_cases(source="feedback") == {"items": [], "total": 0}


def test_case_filters_and_pagination_keep_total_and_order(case_store):
    repo, _ = case_store
    oldest = add_case(case_store, "oldest", ("feedback", "delivery"))
    middle = add_case(case_store, "middle", ("feedback",))
    newest = add_case(case_store, "newest", ("delivery",))
    with repo.connection() as db:
        for day, case_id in enumerate((oldest, middle, newest), start=1):
            db.execute("UPDATE quality_cases SET updated_at=? WHERE case_id=?",
                       (f"2026-10-{day:02d}T12:00:00+00:00", case_id))
        db.execute("""UPDATE quality_cases SET status='confirmed',severity='high',
            symptom='missing-date',version='test-fix' WHERE case_id=?""", (oldest,))
    page = repo.list_cases(source="feedback", limit=1, offset=1)
    assert page["total"] == 2
    assert [item["case_id"] for item in page["items"]] == [oldest]
    assert set(page["items"][0]["sources"]) == {"feedback", "delivery"}
    filtered = repo.list_cases(status="confirmed", severity="high", source="feedback",
                               symptom="missing-date", version="test-fix")
    assert filtered["total"] == 1
    assert [item["case_id"] for item in filtered["items"]] == [oldest]
    assert repo.list_cases(source="absent") == {"items": [], "total": 0}
    assert repo.list_cases(source="feedback", offset=2) == {"items": [], "total": 2}
