"""Real API/SQLite/engine integration with a bounded fake model transport."""

from database_support import isolated_database_url

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
import pytest

from app.agents.react_runtime.lifecycle import Journal
from app.models import AgentChatRequest
from app.quality_judge_models import DIMENSIONS
from app.repositories.chat_session_repository import ChatSessionRepository
from app.repositories.quality_repository import QualityRepository
from app.routers import quality
from app.services.quality_judge_worker import process_judge_jobs


ADMIN = {"X-LimitUpLab-Admin-Key": "judge-fixture-admin"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_ADMIN_KEY", ADMIN["X-LimitUpLab-Admin-Key"])
    monkeypatch.setenv("LIMITUPLAB_QUALITY_JUDGE_ENABLED", "true")
    monkeypatch.setenv("LIMITUPLAB_QUALITY_JUDGE_MODEL", "fixture-judge")
    monkeypatch.setenv("LIMITUPLAB_QUALITY_JUDGE_BASE_URL", "https://fixture.invalid/v1")
    monkeypatch.setenv("LIMITUPLAB_QUALITY_JUDGE_API_KEY", "private-fixture-key")
    monkeypatch.setenv("LIMITUPLAB_QUALITY_NORMAL_SAMPLE_RATE", "0")
    monkeypatch.setenv("LIMITUPLAB_QUALITY_JUDGE_DAILY_CALL_LIMIT", "50")
    monkeypatch.setenv("LIMITUPLAB_QUALITY_JUDGE_MAX_CALLS_PER_JOB", "2")
    path = isolated_database_url('judge-api.sqlite')
    ChatSessionRepository(path).ensure_session("session", owner_id="owner")
    journal = Journal(path)
    row, _ = journal.create(AgentChatRequest(session_id="session", message_id="question",
                           message="请分别说明数据来源和日期。"), "owner")
    journal.finish(row["run_id"], "owner", {"answer": "只有一段笼统说明。", "intent": "research", "task_status": "complete"})
    repo = QualityRepository(path)
    app = FastAPI()
    app.include_router(quality.router)
    app.dependency_overrides[quality.repository] = lambda: repo
    with TestClient(app) as client:
        yield client, repo, row["run_id"]


class FakeJudge:
    def __init__(self, *, fail=True, error=None, malformed=False):
        self.calls, self.fail, self.error, self.malformed = 0, fail, error, malformed

    def generate_messages(self, messages, tools, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        if self.malformed:
            return AIMessage(content="unstructured")
        view = json.loads(messages[-1].content)
        assert "private-fixture-key" not in messages[-1].content
        decisions = {name: {"verdict": "pass", "reason": "测试范围内通过", "unknown_reason": None, "findings": []}
                     for name in DIMENSIONS}
        if self.fail:
            requirement = next(item for item in view["evidence_catalog"] if item["kind"] == "requirement")
            decisions["intent_delivery"] = {"verdict": "fail", "reason": "没有交付用户要求的来源与日期", "unknown_reason": None,
                "findings": [{"failure_kind": "missing_delivery", "summary": "遗漏数据来源与日期", "severity": "medium",
                    "answer_quote": None, "requirement_quote": {"ref_id": requirement["ref_id"], "text": requirement["value"]},
                    "evidence_refs": [requirement["ref_id"]]}]}
        return AIMessage(content="", tool_calls=[{"name": tools[0]["function"]["name"], "args": decisions, "id": "decision-1"}],
                         usage_metadata={"input_tokens": 50, "output_tokens": 40, "total_tokens": 90})


def enqueue(client, run_id):
    response = client.post(f"/api/admin/quality/runs/{run_id}/judge", headers=ADMIN, json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_manual_only_authorization_strict_input_and_idempotent_jobs(setup):
    client, repo, run_id = setup
    provider = FakeJudge()
    assert process_judge_jobs(repo, provider_factory=lambda _: provider)["processed"] == 0
    assert provider.calls == 0
    for path in ("judge/config", f"runs/{run_id}/quality-jobs"):
        assert client.get("/api/admin/quality/" + path).status_code == 401
    url = f"/api/admin/quality/runs/{run_id}/judge"
    assert client.post(url, json={}).status_code == 401
    assert client.post(url, headers=ADMIN, json={"model": "untrusted", "automatic": True}).status_code == 422
    config = client.get("/api/admin/quality/judge/config", headers=ADMIN).json()
    assert config["enabled"] and config["manual_only"]
    first = enqueue(client, run_id)
    assert first["created"] and enqueue(client, run_id)["created"] is False
    assert provider.calls == 0  # Enqueue HTTP path does not call the provider.
    jobs = client.get(f"/api/admin/quality/runs/{run_id}/quality-jobs", headers=ADMIN).json()
    assert len([j for j in jobs["items"] if j["job_kind"] == "llm_judge"]) == 1
    assert client.post("/api/admin/quality/runs/absent/judge", headers=ADMIN, json={}).status_code == 404
    with repo.connection() as db:
        assert "private-fixture-key" not in " ".join(row[0] for row in db.execute("SELECT config_json FROM quality_jobs"))


def test_semantic_finding_closes_through_human_review_and_new_report_does_not_reopen(setup, monkeypatch):
    client, repo, run_id = setup
    enqueue(client, run_id)
    provider = FakeJudge()
    assert process_judge_jobs(repo, provider_factory=lambda _: provider)["succeeded"] == 1
    assert provider.calls == 1
    history = repo.evaluation_history(run_id)["items"][0]
    assert history["verdict"] == "fail" and history["report"]["metadata"]["model_tokens"] == 90
    assert history["report"]["metadata"]["judge_input"]["evidence_catalog"]
    case = repo.list_cases(source="llm_judge")["items"][0]
    assert case["status"] == "pending"
    url = "/api/admin/quality/cases/" + case["case_id"]
    for state in ("confirmed", "in_progress", "verifying", "closed"):
        response = client.patch(url, headers=ADMIN, json={"status": state, "expected_revision": case["revision"],
            "symptom": "缺少来源", "root_cause": "验收夹具", "fix_version": "fixture", "note": "人工核对测试记录"})
        assert response.status_code == 200, response.text
        case = response.json()["case"]
    monkeypatch.setenv("LIMITUPLAB_QUALITY_JUDGE_MAX_OUTPUT_TOKENS", "4900")
    assert enqueue(client, run_id)["created"]
    process_judge_jobs(repo, provider_factory=lambda _: provider)
    detail = client.get(url, headers=ADMIN).json()
    assert detail["case"]["status"] == "closed" and detail["case"]["revision"] == 4
    assert detail["has_new_signals_since_review"] and len(detail["reviews"]) == 4
    assert client.get("/api/admin/quality/evaluations?evaluator_kind=llm_judge", headers=ADMIN).json()["total"] == 2


def test_transport_failure_unknown_usage_and_global_job_cap_survive_retry(setup):
    client, repo, run_id = setup
    enqueued = enqueue(client, run_id)
    provider = FakeJudge(error=TimeoutError("private provider exception"))
    for _ in range(3):
        process_judge_jobs(repo, provider_factory=lambda _: provider)
        with repo.connection() as db:
            db.execute("UPDATE quality_jobs SET available_at=0 WHERE id=?", (enqueued["job_id"],))
    assert provider.calls == 2
    assert repo.list_cases()["total"] == 0
    history = repo.evaluation_history(run_id)
    assert all(item["verdict"] == "unknown" for item in history["items"])
    calls = repo.judge_call_history(enqueued["job_id"])["items"]
    assert len(calls) == 2 and all(item["total_tokens"] is None for item in calls)
    assert "private provider exception" not in json.dumps(history)
    assert next(j for j in repo.job_history(run_id)["items"] if j["job_kind"] == "llm_judge")["status"] == "dead"


def test_protocol_repair_is_budgeted_and_does_not_manufacture_case(setup):
    client, repo, run_id = setup
    enqueued = enqueue(client, run_id)
    provider = FakeJudge(malformed=True)
    assert process_judge_jobs(repo, provider_factory=lambda _: provider)["failed"] == 1
    assert provider.calls == 2
    assert len(repo.judge_call_history(enqueued["job_id"])["items"]) == 2
    assert repo.list_cases()["total"] == 0
    assert repo.evaluation_history(run_id)["items"][0]["report"]["metadata"]["error_code"] == "invalid_judge_protocol"


def test_native_adapter_repair_keeps_both_measured_calls_in_one_job_attempt(setup):
    import httpx
    from test_langchain_provider import completion, provider_for, tool_message, USAGE
    from test_quality_judge import passing
    from app.services.quality_judge import TOOL_NAME

    client, repo, run_id = setup
    enqueued = enqueue(client, run_id)
    wire_calls = []

    def handle(request):
        wire_calls.append(json.loads(request.content))
        arguments = "broken JSON" if len(wire_calls) == 1 else json.dumps(passing())
        return httpx.Response(200, json=completion(tool_message(TOOL_NAME, arguments)))

    with provider_for(handle, retries=2) as provider:
        result = process_judge_jobs(repo, provider_factory=lambda _: provider)
    assert result["processed"] == result["succeeded"] == 1
    assert result["failed"] == result["deferred"] == 0
    assert len(wire_calls) == 2
    calls = repo.judge_call_history(enqueued["job_id"])["items"]
    assert len(calls) == 2 and {call["phase"] for call in calls} == {"initial", "repair"}
    by_phase = {call["phase"]: call for call in calls}
    assert by_phase["initial"]["status"] == "error"
    assert by_phase["initial"]["error_type"] == "NativeFunctionCallingError"
    assert by_phase["repair"]["status"] == "succeeded"
    assert all(call["attempt"] == 1 and call["total_tokens"] == USAGE["total_tokens"] for call in calls)
    assert all(call["prompt_tokens"] == USAGE["prompt_tokens"] and call["completion_tokens"] == USAGE["completion_tokens"]
               for call in calls)
    job = next(item for item in repo.job_history(run_id)["items"] if item["job_id"] == enqueued["job_id"])
    assert job["status"] == "done" and job["attempts"] == 1
    history = repo.evaluation_history(run_id)
    assert history["total"] == 1 and history["items"][0]["execution_status"] == "succeeded"
    report = history["items"][0]["report"]
    assert report["metadata"]["model_calls"] == report["metadata"]["protocol_attempts"] == 2
    assert report["metadata"]["model_tokens"] == 2 * USAGE["total_tokens"]
    assert report["metadata"]["usage_complete"] is True
    assert all(check["verdict"] != "fail" for check in report["checks"])
    assert repo.list_cases()["total"] == 0


def test_kill_switch_leaves_explicit_work_queued_and_blocks_new_requests(setup, monkeypatch):
    client, repo, run_id = setup
    enqueue(client, run_id)
    monkeypatch.setenv("LIMITUPLAB_QUALITY_JUDGE_ENABLED", "false")
    provider = FakeJudge()
    result = process_judge_jobs(repo, provider_factory=lambda _: provider)
    assert result["enabled"] is False and provider.calls == 0
    assert client.post(f"/api/admin/quality/runs/{run_id}/judge", headers=ADMIN, json={}).status_code == 503
    assert next(j for j in repo.job_history(run_id)["items"] if j["job_kind"] == "llm_judge")["status"] == "pending"
