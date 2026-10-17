import pytest

from app.services.quality_judge_config import JudgeConfiguration, judge_configuration, judge_status
from app.services.quality_judge import judge_identity


def test_judge_configuration_is_independent_and_does_not_persist_credentials(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-private-credential")
    monkeypatch.setenv("LIMITUPLAB_QUALITY_JUDGE_ENABLED", "true")
    monkeypatch.setenv("LIMITUPLAB_LLM_MODEL", "chat-model")
    monkeypatch.setenv("LIMITUPLAB_QUALITY_JUDGE_MODEL", "judge-model")
    config = judge_configuration()
    assert config.model == "judge-model" and config.manual_trigger
    assert "test-private-credential" not in config.model_dump_json()
    assert judge_status()["enabled"] and judge_status()["manual_only"]
    monkeypatch.setenv("LIMITUPLAB_QUALITY_JUDGE_ENABLED", "false")
    assert not judge_status()["enabled"]


@pytest.mark.parametrize("url", ["https://user:secret@host/v1", "https://host/v1?api_key=secret", "https://host/#token", "file:///secret"])
def test_endpoint_cannot_leak_credentials_into_jobs(url):
    with pytest.raises(ValueError):
        JudgeConfiguration(model="fixture", base_url=url, **judge_identity())


def test_invalid_budget_fails_closed_without_exposing_settings(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_QUALITY_JUDGE_DAILY_CALL_LIMIT", "-1")
    status = judge_status()
    assert status["enabled"] is False and status["manual_only"] is True
    assert "reason" in status
