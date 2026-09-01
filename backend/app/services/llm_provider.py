"""Small LLM provider abstraction with disabled-mode fallback."""

from __future__ import annotations

import os
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter, sleep
from typing import Any, Callable

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
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    response_mode: str = "text"
    function_name: str | None = None


class NativeFunctionCallingUnavailable(RuntimeError):
    """Raised when a provider cannot use the native function-calling protocol."""


class NativeFunctionCallingError(RuntimeError):
    """Raised when a native function-call response violates its contract."""


@dataclass
class LLMUsageTracker:
    """Request-local aggregate of provider calls and exact token usage."""

    call_count: int = 0
    failed_call_count: int = 0
    measured_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str | None = None

    def begin_call(self, model: str) -> None:
        self.call_count += 1
        self.model = model

    def complete_call(self, result: LLMResult) -> None:
        if (
            result.prompt_tokens is None
            or result.completion_tokens is None
            or result.total_tokens is None
        ):
            return
        self.measured_call_count += 1
        self.prompt_tokens += result.prompt_tokens
        self.completion_tokens += result.completion_tokens
        self.total_tokens += result.total_tokens

    def fail_call(self) -> None:
        self.failed_call_count += 1

    @property
    def token_usage_complete(self) -> bool:
        return self.call_count > 0 and self.measured_call_count == self.call_count


_usage_tracker: ContextVar[LLMUsageTracker | None] = ContextVar(
    "llm_usage_tracker",
    default=None,
)


@contextmanager
def capture_llm_usage():
    """Collect provider usage for all calls made in the current execution context."""

    tracker = LLMUsageTracker()
    token = _usage_tracker.set(tracker)
    try:
        yield tracker
    finally:
        _usage_tracker.reset(token)


class LLMProvider:
    """Interface for text generation providers."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Generate a response for a system/user prompt pair."""

        raise NotImplementedError

    def generate_function_call(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        function_name: str,
        function_description: str,
        parameters: dict[str, Any],
    ) -> LLMResult:
        """Force one native function call and return its JSON arguments as content.

        Custom and test providers stay source-compatible: callers can catch this
        explicit signal and use the legacy prompt-to-JSON path.
        """

        raise NativeFunctionCallingUnavailable(
            f"{type(self).__name__} does not support native function calling"
        )

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
        native_function_calling_enabled: bool = True,
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
        self.native_function_calling_enabled = native_function_calling_enabled
        self.session = session or _get_thread_session()
        self.sleep_fn = sleep_fn

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Call a Chat Completions compatible endpoint."""

        payload = self._build_payload(system_prompt, user_prompt)
        started_at = perf_counter()
        tracker = _usage_tracker.get()
        if tracker is not None:
            tracker.begin_call(self.model)
        data = self._post_completion(payload, tracker)

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            if tracker is not None:
                tracker.fail_call()
            raise RuntimeError("LLM response shape is invalid") from error

        usage = _parse_token_usage(data.get("usage"))
        result = LLMResult(
            content=content,
            model=self.model,
            provider="openai-chat-completions",
            duration_ms=round((perf_counter() - started_at) * 1000),
            prompt_chars=len(system_prompt) + len(user_prompt),
            completion_chars=len(content),
            prompt_tokens=usage[0],
            completion_tokens=usage[1],
            total_tokens=usage[2],
        )
        if tracker is not None:
            tracker.complete_call(result)
        return result

    def generate_function_call(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        function_name: str,
        function_description: str,
        parameters: dict[str, Any],
    ) -> LLMResult:
        """Force one OpenAI-compatible function call and validate its arguments."""

        if not self.native_function_calling_enabled:
            raise NativeFunctionCallingUnavailable(
                "Native function calling is disabled by configuration"
            )
        payload = self._build_payload(
            system_prompt,
            user_prompt,
            planner=True,
        )
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": function_name,
                    "description": function_description,
                    "parameters": parameters,
                },
            }
        ]
        payload["tool_choice"] = {
            "type": "function",
            "function": {"name": function_name},
        }
        function_contract_chars = len(
            json.dumps(
                {
                    "tools": payload["tools"],
                    "tool_choice": payload["tool_choice"],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        started_at = perf_counter()
        tracker = _usage_tracker.get()
        if tracker is not None:
            tracker.begin_call(self.model)
        data = self._post_completion(payload, tracker)

        try:
            message = data["choices"][0]["message"]
            function_call = _matching_function_call(message, function_name)
            raw_arguments = function_call["arguments"]
            arguments = (
                raw_arguments
                if isinstance(raw_arguments, dict)
                else json.loads(str(raw_arguments))
            )
            if not isinstance(arguments, dict):
                raise TypeError("function arguments must be a JSON object")
        except (KeyError, IndexError, TypeError, ValueError) as error:
            if tracker is not None:
                tracker.fail_call()
            raise NativeFunctionCallingError(
                f"LLM did not return a valid {function_name} function call"
            ) from error

        content = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        usage = _parse_token_usage(data.get("usage"))
        result = LLMResult(
            content=content,
            model=self.model,
            provider="openai-chat-completions",
            duration_ms=round((perf_counter() - started_at) * 1000),
            prompt_chars=(
                len(system_prompt) + len(user_prompt) + function_contract_chars
            ),
            completion_chars=len(content),
            prompt_tokens=usage[0],
            completion_tokens=usage[1],
            total_tokens=usage[2],
            response_mode="function_call",
            function_name=function_name,
        )
        if tracker is not None:
            tracker.complete_call(result)
        return result

    def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        on_delta: Callable[[str], None],
    ) -> LLMResult:
        """Stream Chat Completions SSE deltas to the caller."""

        payload = self._build_payload(system_prompt, user_prompt, stream=True)
        started_at = perf_counter()
        tracker = _usage_tracker.get()
        if tracker is not None:
            tracker.begin_call(self.model)
        response = None
        chunks: list[str] = []
        usage: tuple[int | None, int | None, int | None] = (None, None, None)
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
                if event.get("usage") is not None:
                    usage = _parse_token_usage(event.get("usage"))
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {}).get("content")
                if not delta:
                    continue
                chunks.append(str(delta))
                on_delta(str(delta))
        except (requests.RequestException, ValueError, UnicodeError, KeyError, IndexError, TypeError) as error:
            if tracker is not None:
                tracker.fail_call()
            raise RuntimeError(f"LLM streaming request failed: {error}") from error
        finally:
            if response is not None:
                response.close()

        content = "".join(chunks)
        if not content:
            if tracker is not None:
                tracker.fail_call()
            raise RuntimeError("LLM streaming response did not contain text")
        result = LLMResult(
            content=content,
            model=self.model,
            provider="openai-chat-completions",
            duration_ms=round((perf_counter() - started_at) * 1000),
            prompt_chars=len(system_prompt) + len(user_prompt),
            completion_chars=len(content),
            prompt_tokens=usage[0],
            completion_tokens=usage[1],
            total_tokens=usage[2],
        )
        if tracker is not None:
            tracker.complete_call(result)
        return result

    def _build_payload(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        stream: bool = False,
        planner: bool = False,
    ) -> dict:
        """Build the shared OpenAI-compatible request payload."""

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": (
                0.0
                if planner or "Return only valid JSON" in system_prompt
                else 0.2
            ),
            "thinking": {
                "type": "enabled" if self.thinking_enabled else "disabled",
            },
        }
        if planner or "Return only valid JSON" in system_prompt:
            payload["max_tokens"] = self.planner_max_tokens
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _post_completion(
        self,
        payload: dict[str, Any],
        tracker: LLMUsageTracker | None,
    ) -> dict[str, Any]:
        """POST one non-streaming completion with bounded retries."""

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
                if not isinstance(data, dict):
                    raise ValueError("LLM response body must be a JSON object")
                return data
            except (requests.RequestException, ValueError) as error:
                last_error = error
                if attempt >= self.max_attempts or not _is_retryable_error(error):
                    if tracker is not None:
                        tracker.fail_call()
                    raise RuntimeError(f"LLM request failed: {error}") from error
                self.sleep_fn(self.retry_delay_seconds * (2 ** (attempt - 1)))
        if tracker is not None:
            tracker.fail_call()
        raise RuntimeError(f"LLM request failed: {last_error}")


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
        native_function_calling_enabled=env_bool(
            "LIMITUPLAB_LLM_NATIVE_FUNCTION_CALLING",
            True,
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


def _parse_token_usage(
    usage: object,
) -> tuple[int | None, int | None, int | None]:
    """Parse standard Chat Completions usage without inventing missing values."""

    if not isinstance(usage, dict):
        return None, None, None
    try:
        prompt_tokens = int(usage["prompt_tokens"])
        completion_tokens = int(usage["completion_tokens"])
        total_tokens = int(usage["total_tokens"])
    except (KeyError, TypeError, ValueError):
        return None, None, None
    return prompt_tokens, completion_tokens, total_tokens


def _matching_function_call(
    message: object,
    function_name: str,
) -> dict[str, Any]:
    """Return one matching modern or legacy Chat Completions function call."""

    if not isinstance(message, dict):
        raise TypeError("message must be an object")
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            if isinstance(function, dict) and function.get("name") == function_name:
                return function
    legacy = message.get("function_call")
    if isinstance(legacy, dict) and legacy.get("name") == function_name:
        return legacy
    raise ValueError(f"missing function call: {function_name}")
