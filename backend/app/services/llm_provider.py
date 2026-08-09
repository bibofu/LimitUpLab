"""Small LLM provider abstraction with disabled-mode fallback."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.1-mini"


@dataclass(frozen=True)
class LLMResult:
    """LLM call result with enough metadata for tracing."""

    content: str
    model: str
    provider: str


class LLMProvider:
    """Interface for text generation providers."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Generate a response for a system/user prompt pair."""

        raise NotImplementedError


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
    ):
        """Create an OpenAI-compatible provider."""

        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Call a Chat Completions compatible endpoint."""

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as error:
            raise RuntimeError(f"LLM request failed: {error}") from error

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("LLM response shape is invalid") from error

        return LLMResult(content=content, model=self.model, provider="openai-chat-completions")


def get_llm_provider() -> LLMProvider:
    """Create the configured LLM provider from environment variables."""

    enabled = os.getenv("LIMITUPLAB_LLM_ENABLED", "false").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
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
    timeout = float(os.getenv("LIMITUPLAB_LLM_TIMEOUT_SECONDS", "20"))
    return OpenAIChatCompletionsProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=timeout,
    )
