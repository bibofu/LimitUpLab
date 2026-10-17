"""Independent, credential-free Judge snapshots; credentials stay in the worker."""

import os
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import env_bool
from app.services.llm_provider import DEFAULT_MODEL, DEFAULT_OPENAI_BASE_URL


class JudgeConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=160)
    base_url: str = Field(min_length=1, max_length=500)
    timeout_seconds: int = Field(default=35, ge=5, le=120)
    max_output_tokens: int = Field(default=4800, ge=512, le=12000)
    max_input_chars: int = Field(default=60000, ge=4000, le=120000)
    max_calls_per_job: int = Field(default=2, ge=1, le=4)
    daily_call_limit: int = Field(default=50, ge=1, le=10000)
    max_concurrency: int = Field(default=1, ge=1, le=4)
    manual_trigger: bool = True
    prompt_hash: str = Field(min_length=64, max_length=64)
    schema_hash: str = Field(min_length=64, max_length=64)
    input_version: str = Field(min_length=1, max_length=80)

    @field_validator("base_url")
    @classmethod
    def credential_free_endpoint(cls, value):
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Judge endpoint must not contain credentials or query parameters")
        return value.rstrip("/")


def judge_api_key():
    return (os.getenv("LIMITUPLAB_QUALITY_JUDGE_API_KEY", "").strip()
            or os.getenv("DEEPSEEK_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip())


def judge_configuration():
    from app.services.quality_judge import judge_identity
    values = {
        **judge_identity(),
        "model": os.getenv("LIMITUPLAB_QUALITY_JUDGE_MODEL", "").strip()
                 or os.getenv("LIMITUPLAB_LLM_MODEL", "").strip() or DEFAULT_MODEL,
        "base_url": os.getenv("LIMITUPLAB_QUALITY_JUDGE_BASE_URL", "").strip()
                    or os.getenv("LIMITUPLAB_LLM_BASE_URL", "").strip()
                    or os.getenv("OPENAI_BASE_URL", "").strip() or DEFAULT_OPENAI_BASE_URL,
    }
    for name in ("timeout_seconds", "max_output_tokens", "max_input_chars", "max_calls_per_job",
                 "daily_call_limit", "max_concurrency"):
        raw = os.getenv("LIMITUPLAB_QUALITY_JUDGE_" + name.upper(), "").strip()
        if raw:
            values[name] = raw
    return JudgeConfiguration.model_validate(values)


def judge_status():
    try:
        config = judge_configuration()
    except ValueError:
        return {"enabled": False, "manual_only": True, "reason": "语义评审配置无效，请检查服务端配置。"}
    enabled = env_bool("LIMITUPLAB_QUALITY_JUDGE_ENABLED", True) and bool(judge_api_key())
    return {"enabled": enabled, "manual_only": True, "model": config.model,
            "max_calls_per_job": config.max_calls_per_job, "daily_call_limit": config.daily_call_limit,
            "max_concurrency": config.max_concurrency,
            "reason": None if enabled else "语义评审已关闭或尚未配置模型密钥。"}


def create_judge_provider(config):
    """Do not mutate the chat provider or permit an old job to choose credentials."""
    from app.services.langchain_provider import LangChainChatProvider
    current = judge_configuration()
    if not judge_status()["enabled"]:
        raise ValueError("Judge is disabled")
    if config.model != current.model or config.base_url != current.base_url:
        raise ValueError("Judge model configuration changed; enqueue a new evaluation")
    if any(getattr(config, key) != getattr(current, key) for key in ("prompt_hash", "schema_hash", "input_version")):
        raise ValueError("Judge protocol changed; enqueue a new evaluation")
    return LangChainChatProvider(api_key=judge_api_key(), model=config.model, base_url=config.base_url,
        timeout_seconds=config.timeout_seconds, planner_max_tokens=config.max_output_tokens,
        thinking_enabled=False, max_attempts=1, native_function_calling_enabled=True)
