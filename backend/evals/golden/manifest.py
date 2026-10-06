"""Shared, secret-free run provenance for Agent and judge-only evaluations."""

from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import subprocess
from uuid import uuid4

from evals.golden.reporting import digest

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent


def code_fingerprint():
    # Retain the Agent CLI's file set and path-relative hashing contract.
    files = [*sorted((BACKEND / "app").rglob("*.py")), *sorted((BACKEND / "evals").rglob("*.py")),
             BACKEND / "scripts/run_agent_golden.py", BACKEND / "requirements.txt"]
    return digest({path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files})


def tree_fingerprint(directory):
    return digest({path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted(directory.rglob("*.py"))})


def git_commit():
    try:
        return subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"],
                                       cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def library_versions():
    result = {}
    for name in ("langchain-core", "langchain-openai", "langgraph", "pydantic", "openai", "httpx"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None  # Missing is not an invented version.
    return result


def run_identity():
    started = datetime.now(timezone.utc)
    return {"run_id": started.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8],
            "created_at": started.isoformat()}


def provider_configuration(provider):
    """Hash effective settings; serialize only retry counts, never raw configuration."""
    model = getattr(provider, "chat_model", None)
    settings = {name: getattr(model, name, None) for name in (
        "openai_api_base", "request_timeout", "temperature", "max_tokens", "top_p",
        "frequency_penalty", "presence_penalty", "seed", "reasoning_effort",
        "extra_body", "model_kwargs", "max_retries", "use_responses_api",
    )}
    settings["provider_options"] = {name: getattr(provider, name, None) for name in (
        "base_url", "thinking_enabled", "timeout_seconds", "planner_max_tokens",
        "max_tokens", "answer_max_tokens", "max_attempts", "retry_delay_seconds",
        "native_function_calling_enabled",
    )}
    retries = {}
    if model is not None:
        value = getattr(model, "max_retries", None)
        retries["chat_model"] = value if type(value) is int else None
    for name in ("root_client", "root_async_client"):
        root = getattr(model, name, None)
        if root is not None:
            value = getattr(root, "max_retries", None)
            retries[name] = value if type(value) is int else None
            settings[name] = {"endpoint": str(getattr(root, "base_url", "")),
                              "timeout": str(getattr(root, "timeout", "")), "max_retries": value}
    observed = set(retries.values())
    disabled = bool(retries) and observed == {0}
    return {"settings_sha256": digest(settings),
            "sdk_retries": next(iter(observed)) if len(observed) == 1 else None,
            "sdk_retry_settings": retries,
            "sdk_retry_status": "disabled" if disabled else "not_applicable" if model is None else "unverified",
            "budget_unit": "logical provider calls; reservations persisted before calls"}


def require_disabled_sdk_retries(provider):
    """Fail before evaluation if a configured SDK client still retries or is unknown."""
    configuration = provider_configuration(provider)
    if getattr(provider, "chat_model", None) is not None and configuration["sdk_retry_status"] != "disabled":
        raise ValueError("Evaluation SDK retries must be verifiably disabled before model calls")
    return configuration
