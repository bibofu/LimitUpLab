"""Exercise real LangChain/OpenAI serialization against a local mock transport."""

import json
from contextlib import contextmanager

import httpx
import pytest

from app.services.langchain_provider import AuditedChatOpenAI, LangChainChatProvider
from app.services.llm_provider import (
    DisabledLLMProvider,
    NativeFunctionCallingError,
    NativeFunctionCallingUnavailable,
    OpenAIChatCompletionsProvider,
    capture_llm_usage,
    get_llm_provider,
)


USAGE = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}


def test_react_message_request_bounds_sdk_retry_and_timeout():
    from langchain_core.messages import HumanMessage
    calls = []
    def handle(request):
        calls.append(request)
        assert request.extensions["timeout"]["read"] == 3
        return httpx.Response(503, json={"error": {"message": "fixture unavailable"}})
    with provider_for(handle, retries=2) as provider, capture_llm_usage() as tracker:
        original = provider.chat_model.root_client.max_retries
        with pytest.raises(Exception):
            provider.generate_messages([HumanMessage(content="研究")], [], timeout_seconds=3)
        assert provider.chat_model.root_client.max_retries == original == 2
    assert len(calls) == 1 and tracker.failed_call_count == 1


# Prepare the completion fixture or observation used by the surrounding regression scenario.
def completion(message=None, usage=USAGE):
    return {
        "id": "chat-test", "object": "chat.completion", "created": 1,
        "model": "test-model",
        "choices": [{"index": 0, "finish_reason": "stop", "message": message or {
            "role": "assistant", "content": "依据数据回答。",
        }}],
        "usage": usage,
    }


# Prepare the tool message fixture or observation used by the surrounding regression scenario.
def tool_message(name="submit_agent_plan", arguments='{"capabilities":["limit_up_pool"]}'):
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call-1", "type": "function",
        "function": {"name": name, "arguments": arguments},
    }]}


# Prepare the sse fixture or observation used by the surrounding regression scenario.
def sse(usage=USAGE, chunks=("依据", "数据回答。")):
    events = [{
        "id": "chat-test", "object": "chat.completion.chunk", "created": 1,
        "model": "test-model", "choices": [{
            "index": 0, "delta": {"role": "assistant", "content": text},
            "finish_reason": None,
        }],
    } for text in chunks]
    events.append({
        "id": "chat-test", "object": "chat.completion.chunk", "created": 1,
        "model": "test-model", "choices": [], "usage": usage,
    })
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"


# Prepare the provider for fixture or observation used by the surrounding regression scenario.
@contextmanager
def provider_for(handler, *, native=True, retries=0):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = AuditedChatOpenAI(
            api_key="test-key", model="test-model", base_url="https://llm.test/v1",
            http_client=client, max_retries=retries, temperature=0.2,
            stream_usage=True, use_responses_api=False,
            extra_body={"thinking": {"type": "disabled"}},
        )
        yield LangChainChatProvider(
            api_key="test-key", model="test-model", base_url="https://llm.test/v1",
            timeout_seconds=20, planner_max_tokens=320, thinking_enabled=False,
            max_attempts=retries + 1, native_function_calling_enabled=native,
            chat_model=model,
        )


# Regression scenario: lcel prompt preserves json and usage.
def test_lcel_prompt_preserves_json_and_usage():
    payloads = []

    # Build the httpx.Response fixture used by the surrounding regression scenario.
    def handle(request):
        assert request.url.path == "/v1/chat/completions"
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    with provider_for(handle) as provider, capture_llm_usage() as tracker:
        result = provider.generate("依据 Facts", '{"股票":"样本","值":null}')
    payload = payloads[0]
    assert payload["messages"][-1]["content"] == '{"股票":"样本","值":null}'
    assert payload["thinking"] == {"type": "disabled"}
    assert "max_tokens" not in payload and "max_completion_tokens" not in payload
    assert result.content == "依据数据回答。"
    assert result.provider == "langchain-openai"
    assert tracker.call_count == 1 and tracker.total_tokens == 16
    assert tracker.token_usage_complete


# Regression scenario: bind tools forces named function and preserves schema.
@pytest.mark.parametrize("function_name", ["submit_agent_plan", "update_session_memory"])
def test_bind_tools_forces_named_function_and_preserves_schema(function_name):
    payloads = []
    schema = {"type": "object", "properties": {"capabilities": {"type": "array", "items": {"type": "string"}}}}

    # Build the httpx.Response fixture used by the surrounding regression scenario.
    def handle(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=completion(tool_message(function_name)))

    with provider_for(handle) as provider:
        result = provider.generate_function_call(
            "选择能力", "涨停名单", function_name=function_name,
            function_description="Plan", parameters=schema,
        )
    payload = payloads[0]
    assert payload["tool_choice"]["function"]["name"] == function_name
    assert payload["tools"][0]["function"]["parameters"] == schema
    assert payload["max_tokens"] == 320
    assert "max_completion_tokens" not in payload
    assert payload["temperature"] == 0.0
    assert result.function_name == function_name
    assert result.response_mode == "function_call"
    assert json.loads(result.content)["capabilities"] == ["limit_up_pool"]


# Regression scenario: invalid function call fails and is counted.
@pytest.mark.parametrize("message", [
    {"role": "assistant", "content": "plain text"},
    tool_message("wrong_function"), tool_message(arguments="broken JSON"),
    {**tool_message(), "tool_calls": tool_message()["tool_calls"] * 2},
])
def test_invalid_function_call_fails_and_is_counted(message):
    # The inline callback supplies the fixture value or replacement behavior used by this test; it
    # is evaluated only when the code under test calls it.
    with provider_for(lambda _: httpx.Response(200, json=completion(message))) as provider:
        with capture_llm_usage() as tracker, pytest.raises(NativeFunctionCallingError):
            provider.generate_function_call(
                "Plan", "Question", function_name="submit_agent_plan",
                function_description="Plan", parameters={"type": "object"},
            )
    assert tracker.call_count == tracker.failed_call_count == 1
    assert not tracker.token_usage_complete


# Regression scenario: missing usage is never invented.
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("usage", [USAGE, None, {"prompt_tokens": 12}, {}])
def test_missing_usage_is_never_invented(streaming, usage):
    payloads = []

    # Prepare the handle fixture or observation used by the surrounding regression scenario.
    def handle(request):
        payloads.append(json.loads(request.content))
        return (httpx.Response(200, text=sse(usage), headers={"content-type": "text/event-stream"})
                if streaming else httpx.Response(200, json=completion(usage=usage)))

    deltas = []
    with provider_for(handle) as provider, capture_llm_usage() as tracker:
        result = (provider.stream_generate("Answer", "Facts", deltas.append)
                  if streaming else provider.generate("Answer", "Facts"))
    assert result.content == "依据数据回答。"
    if streaming:
        assert deltas == ["依据", "数据回答。"]
        assert payloads[0]["stream_options"] == {"include_usage": True}
    assert tracker.token_usage_complete == (usage == USAGE)
    assert result.total_tokens == (16 if usage == USAGE else None)


# Regression scenario: function disabled preserves json fallback.
def test_function_disabled_preserves_json_fallback():
    payloads = []

    # Build the httpx.Response fixture used by the surrounding regression scenario.
    def handle(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    with provider_for(handle, native=False) as provider:
        with pytest.raises(NativeFunctionCallingUnavailable):
            provider.generate_function_call(
                "Plan", "Question", function_name="submit_agent_plan",
                function_description="Plan", parameters={"type": "object"},
            )
        provider.generate("Return only valid JSON", "Question")
    assert len(payloads) == 1
    assert "tools" not in payloads[0]
    assert payloads[0]["max_tokens"] == 320


# Regression scenario: http failure is counted without leaking response.
def test_http_failure_is_counted_without_leaking_response():
    # The inline callback supplies the fixture value or replacement behavior used by this test; it
    # is evaluated only when the code under test calls it.
    with provider_for(lambda _: httpx.Response(401, json={"error": {"message": "sensitive"}})) as provider:
        with capture_llm_usage() as tracker, pytest.raises(RuntimeError) as error:
            provider.generate("Answer", "Facts")
    assert "sensitive" not in str(error.value)
    assert tracker.failed_call_count == 1


# Regression scenario: transient http error retries within one logical call.
def test_transient_http_error_retries_within_one_logical_call():
    calls = []

    # Build the httpx.Response fixture used by the surrounding regression scenario.
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"message": "temporarily unavailable"}})
        return httpx.Response(200, json=completion())

    with provider_for(handle, retries=1) as provider, capture_llm_usage() as tracker:
        result = provider.generate("Answer", "Facts")
    assert len(calls) == 2
    assert result.total_tokens == 16
    assert tracker.call_count == 1 and tracker.failed_call_count == 0


# Regression scenario: interrupted stream closes connection and does not replay text.
def test_interrupted_stream_closes_connection_and_does_not_replay_text():
    class BrokenStream(httpx.SyncByteStream):
        closed = False

        # Simulate the dependency failure required by this regression scenario so its error or
        # fallback path is exercised.
        def __iter__(self):
            yield sse(chunks=("已输出",)).split("\n\n", 1)[0].encode() + b"\n\n"
            raise httpx.ReadError("interrupted stream")

        # Release the temporary resources owned by this test fixture.
        def close(self):
            self.closed = True

    body = BrokenStream()
    calls = []

    # Build the httpx.Response fixture used by the surrounding regression scenario.
    def handle(request):
        calls.append(request)
        return httpx.Response(200, stream=body, headers={"content-type": "text/event-stream"})

    deltas = []
    with provider_for(handle, retries=1) as provider:
        with capture_llm_usage() as tracker, pytest.raises(httpx.ReadError):
            provider.stream_generate("Answer", "Facts", deltas.append)
    assert deltas == ["已输出"] and len(calls) == 1
    assert body.closed
    assert tracker.failed_call_count == 1 and not tracker.token_usage_complete


# Regression scenario: empty stream fails instead of succeeding with usage only.
def test_empty_stream_fails_instead_of_succeeding_with_usage_only():
    # The inline callback supplies the fixture value or replacement behavior used by this test; it
    # is evaluated only when the code under test calls it.
    response = lambda _: httpx.Response(200, text=sse(chunks=()), headers={"content-type": "text/event-stream"})
    with provider_for(response) as provider:
        with capture_llm_usage() as tracker, pytest.raises(RuntimeError, match="did not contain text"):
            # The inline callback supplies the fixture value or replacement behavior used by this
            # test; it is evaluated only when the code under test calls it.
            provider.stream_generate("Answer", "Facts", lambda _: None)
    assert tracker.failed_call_count == 1


# Regression scenario: deepseek final content chunk preserves usage.
def test_deepseek_final_content_chunk_preserves_usage():
    last = {
        "id": "chat-test", "object": "chat.completion.chunk", "created": 1,
        "model": "test-model", "usage": USAGE,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": "完成"}, "finish_reason": "stop"}],
    }
    # The inline callback supplies the fixture value or replacement behavior used by this test; it
    # is evaluated only when the code under test calls it.
    response = lambda _: httpx.Response(200, text=f"data: {json.dumps(last)}\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})
    with provider_for(response) as provider, capture_llm_usage() as tracker:
        # The inline callback supplies the fixture value or replacement behavior used by this
        # test; it is evaluated only when the code under test calls it.
        result = provider.stream_generate("Answer", "Facts", lambda _: None)
    assert result.content == "完成" and tracker.total_tokens == 16
    assert tracker.token_usage_complete


# Regression scenario: default backend and explicit rollback.
def test_default_backend_and_explicit_rollback(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_LLM_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("LIMITUPLAB_LLM_BACKEND", raising=False)
    assert isinstance(get_llm_provider(), LangChainChatProvider)
    monkeypatch.setenv("LIMITUPLAB_LLM_BACKEND", "requests")
    assert isinstance(get_llm_provider(), OpenAIChatCompletionsProvider)
    monkeypatch.setenv("LIMITUPLAB_LLM_BACKEND", "typo")
    with pytest.raises(ValueError, match="LIMITUPLAB_LLM_BACKEND"):
        get_llm_provider()
    monkeypatch.setenv("LIMITUPLAB_LLM_ENABLED", "false")
    assert isinstance(get_llm_provider(), DisabledLLMProvider)
