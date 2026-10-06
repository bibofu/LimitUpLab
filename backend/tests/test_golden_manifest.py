"""Native calibration provenance is saved before calls and never exposes secrets."""

from copy import copy
from dataclasses import asdict, replace
from datetime import datetime
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage
import pytest

from evals.golden import calibration, manifest
from evals.golden.reporting import digest


class Client:
    max_retries = 3
    base_url = "https://private-user:private-password@fixture.invalid/v1?token=private-token"
    timeout = 31
    chat = SimpleNamespace(completions=object())

    def with_options(self, **options):
        result = copy(self)
        result.__dict__.update(options)
        return result


class Model(SimpleNamespace):
    def model_copy(self, *, update):
        result = copy(self)
        result.__dict__.update(update)
        return result


class Provider:
    model = "scripted-native-manifest"
    api_key = "private-api-key"

    def __init__(self, callback=None):
        self.chat_model = Model(root_client=Client(), root_async_client=Client(), max_retries=3,
                                extra_body={"thinking": {"type": "disabled"}})
        self.calls = []
        self.callback = callback

    def generate_messages(self, messages, tools, **kwargs):
        self.calls.append(self.chat_model)
        if self.callback:
            self.callback(self)
        finding = {"reason": "No claims in greeting", "evidence_relation": "no_claim"}
        return AIMessage(content="", tool_calls=[{"name": "submit_golden_visible_audit", "id": "audit",
                          "args": {key: dict(finding) for key in ("factual", "source", "safety")}}])


def one_case():
    return next(case for case in calibration.load_calibration_cases() if case.id == "greeting_without_source_claim")


def test_calibration_saves_native_manifest_and_reservation_before_actual_call(tmp_path):
    path = tmp_path / "calibration.json"
    seen = []

    def before_call(configured):
        saved = json.loads(path.read_text(encoding="utf-8"))
        native = saved["manifest"]
        assert saved["results"] == [] and saved["logical_calls_used"] == 1
        assert native["run_id"] == saved["run_id"] and native["created_at"] == saved["created_at"]
        assert datetime.fromisoformat(native["created_at"]).utcoffset().total_seconds() == 0
        assert native["scope"] == "judge-calibration" and "profile" not in native
        assert native["dataset_hash"] == digest([asdict(one_case())])
        assert native["provider"] == "Provider" and native["libraries"]["pydantic"]
        assert native["libraries"]["openai"] and native["libraries"]["httpx"]
        assert all(len(native[key]) == 64 for key in ("code_hash", "production_hash", "evaluator_hash"))
        assert native["effective_model_configuration"]["sdk_retry_settings"] == {
            "chat_model": 0, "root_client": 0, "root_async_client": 0}
        assert configured.chat_model.root_client.max_retries == configured.chat_model.root_async_client.max_retries == 0
        assert configured.chat_model.max_retries == 0
        seen.append(native)

    provider = Provider(before_call)
    report = calibration.run_calibration(provider, cases=[one_case()], max_calls=1, output=path)
    assert report["summary"]["counts"] == {"match": 1} and seen == [report["manifest"]]
    assert provider.chat_model.max_retries == provider.chat_model.root_client.max_retries == 3
    serialized = path.read_text(encoding="utf-8")
    assert not any(secret in serialized for secret in (
        "private-user", "private-password", "private-token", "private-api-key", "fixture.invalid"))


def test_interruption_retains_start_manifest_without_inventing_a_result(tmp_path):
    def interrupt(provider):
        raise KeyboardInterrupt()
    path = tmp_path / "interrupted.json"
    with pytest.raises(KeyboardInterrupt):
        calibration.run_calibration(Provider(interrupt), cases=[one_case()], output=path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["manifest"]["case_ids"] == [one_case().id]
    assert saved["logical_calls_used"] == 1 and saved["results"] == []
    assert saved["summary"]["complete"] is False


def test_manifest_persistence_failure_prevents_provider_entry(tmp_path, monkeypatch):
    def fail_replace(*args):
        raise PermissionError("fixture-private-path")
    monkeypatch.setattr(type(tmp_path), "replace", fail_replace)
    provider = Provider()
    with pytest.raises(PermissionError):
        calibration.run_calibration(provider, cases=[one_case()], output=tmp_path / "blocked.json")
    assert provider.calls == []


def test_misbehaving_retry_clone_is_detected_before_model_entry(tmp_path):
    class IgnoringClient(Client):
        def with_options(self, **options):
            return self
    provider = Provider()
    provider.chat_model.root_async_client = IgnoringClient()
    with pytest.raises(ValueError, match="verifiably disabled"):
        calibration.run_calibration(provider, cases=[one_case()], output=tmp_path / "unsafe.json")
    assert provider.calls == [] and not (tmp_path / "unsafe.json").exists()


def test_unknown_retry_configuration_is_not_falsely_reported_as_zero():
    assert manifest.provider_configuration(SimpleNamespace())["sdk_retries"] is None
    unknown = SimpleNamespace(chat_model=SimpleNamespace(max_retries=None))
    assert manifest.provider_configuration(unknown)["sdk_retry_status"] == "unverified"
    with pytest.raises(ValueError, match="verifiably disabled"):
        manifest.require_disabled_sdk_retries(unknown)


def test_calibration_actual_sdk_429_performs_one_http_attempt(tmp_path):
    import httpx
    from app.services.langchain_provider import AuditedChatOpenAI, LangChainChatProvider
    path = tmp_path / "rate-limited.json"
    requests = []

    def limited(request):
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["manifest"]["effective_model_configuration"]["sdk_retry_status"] == "disabled"
        assert saved["logical_calls_used"] == 1
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": "synthetic rate limit",
            "type": "rate_limit_error", "code": "rate_limit_exceeded"}})

    with httpx.Client(transport=httpx.MockTransport(limited)) as client:
        chat = AuditedChatOpenAI(model="fixture", api_key="synthetic-key", max_retries=2,
            base_url="https://fixture.invalid/v1", http_client=client)
        provider = LangChainChatProvider(api_key="synthetic-key", model="fixture",
            base_url="https://fixture.invalid/v1", timeout_seconds=1, planner_max_tokens=128,
            thinking_enabled=False, max_attempts=3, native_function_calling_enabled=True, chat_model=chat)
        report = calibration.run_calibration(provider, cases=[one_case()], max_calls=1, output=path)
    assert len(requests) == report["logical_calls_used"] == 1
    assert report["summary"]["counts"] == {"review": 1}
    assert report["results"][0]["phase_errors"] == {"visible_audit": "RuntimeError"}


def test_calibration_identity_dataset_and_configuration_distinguish_runs():
    original = one_case()
    first = calibration.run_calibration(Provider(), cases=[original], max_calls=1)
    changed = replace(original, answer=original.answer + " 新的问候。")
    second = calibration.run_calibration(Provider(), cases=[changed], max_calls=1)
    assert first["run_id"] != second["run_id"]
    assert first["manifest"]["dataset_hash"] != second["manifest"]["dataset_hash"]
    provider = Provider()
    before = manifest.provider_configuration(provider)
    provider.api_key = "rotated-key"
    assert manifest.provider_configuration(provider) == before
    provider.chat_model.extra_body = {"thinking": {"type": "enabled"}}
    assert manifest.provider_configuration(provider)["settings_sha256"] != before["settings_sha256"]


def test_shared_fingerprint_keeps_agent_entrypoint_file_set_and_ignores_secrets(tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    files = {"app/tool.py": "v1", "evals/cases.py": "v1", "scripts/run_agent_golden.py": "entry", "requirements.txt": "deps"}
    for name, content in files.items():
        file = backend / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(manifest, "BACKEND", backend)
    monkeypatch.setattr(manifest, "ROOT", tmp_path)
    initial = manifest.code_fingerprint()
    (backend / ".env").write_text("SECRET=do-not-hash", encoding="utf-8")
    assert manifest.code_fingerprint() == initial
    (backend / "app/tool.py").write_text("v2", encoding="utf-8")
    assert manifest.code_fingerprint() != initial
