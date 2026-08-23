"""Small LLM provider abstraction with disabled-mode fallback."""

from __future__ import annotations

import os
import json
import threading
from dataclasses import dataclass
from time import perf_counter, sleep
from typing import Callable

import requests

from app.config import env_bool


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.1-mini"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_PLANNER_MAX_TOKENS = 320
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RETRY_DELAY_SECONDS = 0.5
_thread_local = threading.local()


@dataclass(frozen=True)
class LLMResult:
    """LLM call result with enough metadata for tracing."""

    content: str
    model: str
    provider: str
    duration_ms: int = 0
    prompt_chars: int = 0
    completion_chars: int = 0


class LLMProvider:
    """Interface for text generation providers."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Generate a response for a system/user prompt pair."""

        raise NotImplementedError

    def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        on_delta: Callable[[str], None],
    ) -> LLMResult:
        """Generate text and report chunks as they become available.

        Providers without native streaming remain compatible by emitting their
        complete response as one chunk.
        """

        result = self.generate(system_prompt, user_prompt)
        if result.content:
            on_delta(result.content)
        return result


class DisabledLLMProvider(LLMProvider):
    """Provider used when LLM integration is intentionally disabled."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Always report disabled mode."""

        raise RuntimeError("LLM is disabled")


class OpenAIChatCompletionsProvider(LLMProvider):
    """OpenAI-compatible Chat Completions provider."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        timeout_seconds: float = 20,
        planner_max_tokens: int = DEFAULT_PLANNER_MAX_TOKENS,
        thinking_enabled: bool = False,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = sleep,
    ):
        """Create an OpenAI-compatible provider."""

        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.planner_max_tokens = planner_max_tokens
        self.thinking_enabled = thinking_enabled
        self.max_attempts = max(1, max_attempts)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.session = session or _get_thread_session()
        self.sleep_fn = sleep_fn

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Call a Chat Completions compatible endpoint."""

        payload = self._build_payload(system_prompt, user_prompt)
        started_at = perf_counter()
        data = None
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.session.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
                break
            except (requests.RequestException, ValueError) as error:
                last_error = error
                if attempt >= self.max_attempts or not _is_retryable_error(error):
                    raise RuntimeError(f"LLM request failed: {error}") from error
                self.sleep_fn(self.retry_delay_seconds * (2 ** (attempt - 1)))

        if data is None:
            raise RuntimeError(f"LLM request failed: {last_error}")

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("LLM response shape is invalid") from error

        return LLMResult(
            content=content,
            model=self.model,
            provider="openai-chat-completions",
            duration_ms=round((perf_counter() - started_at) * 1000),
            prompt_chars=len(system_prompt) + len(user_prompt),
            completion_chars=len(content),
        )

    def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        on_delta: Callable[[str], None],
    ) -> LLMResult:
        """Stream Chat Completions SSE deltas to the caller."""

        payload = self._build_payload(system_prompt, user_prompt, stream=True)
        started_at = perf_counter()
        response = None
        chunks: list[str] = []
        try:
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_seconds,
                stream=True,
            )
            response.raise_for_status()
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                line = (
                    raw_line.decode("utf-8")
                    if isinstance(raw_line, bytes)
                    else str(raw_line)
                )
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                delta = event.get("choices", [{}])[0].get("delta", {}).get("content")
                if not delta:
                    continue
                chunks.append(str(delta))
                on_delta(str(delta))
        except (requests.RequestException, ValueError, UnicodeError, KeyError, IndexError, TypeError) as error:
            raise RuntimeError(f"LLM streaming request failed: {error}") from error
        finally:
            if response is not None:
                response.close()

        content = "".join(chunks)
        if not content:
            raise RuntimeError("LLM streaming response did not contain text")
        return LLMResult(
            content=content,
            model=self.model,
            provider="openai-chat-completions",
            duration_ms=round((perf_counter() - started_at) * 1000),
            prompt_chars=len(system_prompt) + len(user_prompt),
            completion_chars=len(content),
        )

    def _build_payload(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        stream: bool = False,
    ) -> dict:
        """Build the shared OpenAI-compatible request payload."""

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0 if "Return only valid JSON" in system_prompt else 0.2,
            "thinking": {
                "type": "enabled" if self.thinking_enabled else "disabled",
            },
        }
        if "Return only valid JSON" in system_prompt:
            payload["max_tokens"] = self.planner_max_tokens
        if stream:
            payload["stream"] = True
        return payload


def get_llm_provider() -> LLMProvider:
    """Create the configured LLM provider from environment variables."""

    if not env_bool("LIMITUPLAB_LLM_ENABLED"):
        return DisabledLLMProvider()

    api_key = (
        os.getenv("DEEPSEEK_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    if not api_key:
        return DisabledLLMProvider()

    model = os.getenv("LIMITUPLAB_LLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    base_url = (
        os.getenv("LIMITUPLAB_LLM_BASE_URL", "").strip()
        or os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL).strip()
    )
    timeout = _read_timeout_seconds()
    return OpenAIChatCompletionsProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=timeout,
        planner_max_tokens=_read_positive_int(
            "LIMITUPLAB_LLM_PLANNER_MAX_TOKENS",
            DEFAULT_PLANNER_MAX_TOKENS,
        ),
        thinking_enabled=env_bool("LIMITUPLAB_LLM_THINKING_ENABLED"),
        max_attempts=_read_positive_int(
            "LIMITUPLAB_LLM_MAX_ATTEMPTS",
            DEFAULT_MAX_ATTEMPTS,
        ),
        retry_delay_seconds=_read_non_negative_float(
            "LIMITUPLAB_LLM_RETRY_DELAY_SECONDS",
            DEFAULT_RETRY_DELAY_SECONDS,
        ),
    )


def _read_timeout_seconds() -> float:
    raw_timeout = os.getenv("LIMITUPLAB_LLM_TIMEOUT_SECONDS", "").strip()
    if not raw_timeout:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = float(raw_timeout)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    if timeout <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return timeout


def _read_positive_int(name: str, default: int) -> int:
    """Read a positive integer setting without making startup fragile."""

    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def _read_non_negative_float(name: str, default: float) -> float:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        return default
    return value if value >= 0 else default


def _is_retryable_error(error: Exception) -> bool:
    """Return whether a failed non-streaming request is safe to retry."""

    if isinstance(error, ValueError):
        return True
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(error, requests.HTTPError):
        status_code = getattr(error.response, "status_code", None)
        return status_code in {408, 409, 425, 429} or (
            isinstance(status_code, int) and status_code >= 500
        )
    return False


def _get_thread_session() -> requests.Session:
    """Reuse HTTPS connections within each backend worker thread."""

    session = getattr(_thread_local, "llm_session", None)
    if session is None:
        session = requests.Session()
        _thread_local.llm_session = session
    return session
