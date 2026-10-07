"""Exercise real LangChain/OpenAI serialization against a local mock transport."""

import json
from asyncio import CancelledError
from contextlib import contextmanager

import httpx
import pytest
from openai import APIConnectionError

from app.services.langchain_provider import AuditedChatOpenAI, LangChainChatProvider
from app.services.llm_provider import (
    DisabledLLMProvider,
    NativeFunctionCallingError,
    NativeFunctionCallingUnavailable,
    capture_llm_usage,
    get_llm_provider,
)


USAGE = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}


def assert_stream_read_failure(error, injected_failure, *, message_prefix):
    """Accept raw transport failures or the SDK/provider's explicit cause chain."""
    if error is injected_failure:
        return
    assert type(error) is RuntimeError
    cause = error.__cause__
    assert isinstance(cause, APIConnectionError)
    assert str(error) == f"{message_prefix} ({type(cause).__name__})"
    seen = set()
    while cause is not None and id(cause) not in seen:
        if cause is injected_failure:
            return
        assert isinstance(cause, APIConnectionError)
        seen.add(id(cause))
        cause = cause.__cause__
    pytest.fail("SDK/provider exception lost the injected stream read failure")


class NativeToolStream(httpx.SyncByteStream):
    """Yield actual SSE frames, with an observation between provider chunks."""

    def __init__(self, fragments, *, checkpoint=None, failure=None, usage=USAGE):
        self.fragments = fragments
        self.checkpoint = checkpoint
        self.failure = failure
        self.usage = usage
        self.closed = False
        self.reached_usage = False

    def __iter__(self):
        for index, fragment in enumerate(self.fragments):
            call = {"index": 0, "function": {"arguments": fragment}}
            if index == 0:
                call.update(id="call-1", type="function")
                call["function"]["name"] = "finish"
            event = {
                "id": "chat-test", "object": "chat.completion.chunk", "created": 1,
                "model": "test-model", "choices": [{
                    "index": 0, "delta": {"tool_calls": [call]}, "finish_reason": None,
                }],
            }
            frame = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
            # Real transport chunks may split the bytes of a UTF-8 character.
            for offset in range(0, len(frame), 7):
                yield frame[offset:offset + 7]
            if self.checkpoint:
                self.checkpoint(index)
            if self.failure:
                raise self.failure
        self.reached_usage = True
        yield sse(usage=self.usage, chunks=()).encode()

    def close(self):
        self.closed = True


def test_react_native_stream_delivers_chunks_before_completion_and_merges_arguments():
    from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

    chunks = []
    payloads = []

    def checkpoint(index):
        assert len(chunks) == index + 1
        assert not body.closed and not body.reached_usage

    body = NativeToolStream(
        ('{"answer":"第一', '行\\n\\u', '6da8\\ud83d', '\\ude80"}'),
        checkpoint=checkpoint,
    )

    def handle(request):
        assert request.extensions["timeout"]["read"] == 3
        payloads.append(json.loads(request.content))
        return httpx.Response(200, stream=body, headers={"content-type": "text/event-stream"})

    definitions = [{"type": "function", "function": {
        "name": "finish", "description": "Finish", "parameters": {"type": "object"},
    }}]
    with provider_for(handle, retries=2) as provider, capture_llm_usage() as tracker:
        result = provider.generate_messages(
            [HumanMessage(content="研究")], definitions, timeout_seconds=3,
            on_chunk=chunks.append,
        )
        assert provider.chat_model.root_client.max_retries == 2
    assert type(result) is AIMessage
    assert all(isinstance(chunk, AIMessageChunk) for chunk in chunks)
    assert result.tool_calls == [{
        "name": "finish", "args": {"answer": "第一行\n涨🚀"},
        "id": "call-1", "type": "tool_call",
    }]
    assert body.closed and body.reached_usage and len(payloads) == 1
    assert payloads[0]["stream"] is True
    assert payloads[0]["stream_options"] == {"include_usage": True}
    assert payloads[0]["tool_choice"]["function"]["name"] == "finish"
    assert tracker.call_count == tracker.measured_call_count == 1
    assert tracker.failed_call_count == 0 and tracker.token_usage_complete
    assert (tracker.prompt_tokens, tracker.completion_tokens, tracker.total_tokens) == (12, 4, 16)


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 12}])
def test_react_native_stream_preserves_missing_usage(usage):
    body = NativeToolStream(('{}',), usage=usage)
    with provider_for(lambda _: httpx.Response(200, stream=body)) as provider:
        with capture_llm_usage() as tracker:
            provider.generate_messages([], [], on_chunk=lambda _: None)
    assert body.closed and not tracker.token_usage_complete
    assert tracker.call_count == 1 and tracker.measured_call_count == 0


@pytest.mark.parametrize("failure", [RuntimeError("cancel callback"), CancelledError()])
def test_react_native_stream_callback_cancellation_closes_connection(failure):
    body = NativeToolStream(('{"answer":"首块', '"}'))
    chunks = []

    def on_chunk(chunk):
        chunks.append(chunk)
        raise failure

    with provider_for(lambda _: httpx.Response(200, stream=body)) as provider:
        with capture_llm_usage() as tracker, pytest.raises(type(failure)):
            provider.generate_messages([], [], on_chunk=on_chunk)
    assert len(chunks) == 1 and body.closed and not body.reached_usage
    assert tracker.call_count == tracker.failed_call_count == 1
    assert tracker.measured_call_count == 0


def test_react_native_stream_read_failure_closes_connection_without_retry():
    failure = httpx.ReadError("stream interrupted")
    body = NativeToolStream(('{"answer":"首块',), failure=failure)
    requests = []
    chunks = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=body)

    with provider_for(handle, retries=2) as provider:
        with capture_llm_usage() as tracker, pytest.raises((httpx.ReadError, RuntimeError)) as caught:
            provider.generate_messages([], [], on_chunk=chunks.append)
    assert_stream_read_failure(caught.value, failure, message_prefix="Model request failed")
    assert len(chunks) == len(requests) == 1 and body.closed
    assert not body.reached_usage and not tracker.token_usage_complete
    assert tracker.call_count == tracker.failed_call_count == 1
    assert tracker.measured_call_count == 0


@pytest.mark.parametrize("arguments", ['{"answer":"truncated', '[]', 'broken JSON'])
def test_react_native_stream_rejects_incomplete_tool_arguments_after_counting_usage(arguments):
    body = NativeToolStream((arguments,))
    with provider_for(lambda _: httpx.Response(200, stream=body)) as provider:
        with capture_llm_usage() as tracker, pytest.raises(NativeFunctionCallingError):
            provider.generate_messages([], [], on_chunk=lambda _: None)
    assert body.closed
    assert tracker.call_count == tracker.measured_call_count == tracker.failed_call_count == 1
    assert tracker.total_tokens == 16


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


@pytest.mark.parametrize(("tool_count", "extra_body", "expected_choice"), [
    (2, {"thinking": {"type": "disabled"}}, "required"),
    (1, {"thinking": {"type": "disabled"}}, "named"),
    (0, {"thinking": {"type": "disabled"}}, None),
    (2, {"thinking": {"type": "enabled"}}, None),
    (2, {"thinking": {"type": "auto"}}, None),
    (2, {"thinking": None}, None),
    (2, {}, None),
    (2, None, None),
    (1, {"thinking": {"type": "enabled"}}, "named"),
    (0, {"thinking": {"type": "enabled"}}, None),
])
def test_react_tool_choice_matches_wire_thinking_mode_and_counts_once(
    tool_count, extra_body, expected_choice,
):
    from langchain_core.messages import HumanMessage

    payloads = []
    definitions = [{"type": "function", "function": {
        "name": name, "description": name,
        "parameters": {"type": "object", "properties": {}},
    }} for name in ("finish", "lookup")][:tool_count]

    def handle(request):
        payloads.append(json.loads(request.content))
        message = tool_message("finish", "{}") if definitions else None
        return httpx.Response(200, json=completion(message))

    with provider_for(handle) as provider, capture_llm_usage() as tracker:
        provider.chat_model.extra_body = extra_body
        result = provider.generate_messages([HumanMessage(content="研究")], definitions)

    assert len(payloads) == 1
    payload = payloads[0]
    if expected_choice == "named":
        assert payload["tool_choice"] == {"type": "function", "function": {"name": "finish"}}
    elif expected_choice is None:
        assert "tool_choice" not in payload
    else:
        assert payload["tool_choice"] == expected_choice
    if definitions:
        assert len(payload["tools"]) == tool_count
        assert result.tool_calls[0]["name"] == "finish"
    else:
        assert "tools" not in payload and result.content == "依据数据回答。"
    assert payload.get("thinking") == (extra_body or {}).get("thinking")
    assert tracker.call_count == tracker.measured_call_count == 1
    assert tracker.failed_call_count == 0 and tracker.token_usage_complete
    assert (tracker.prompt_tokens, tracker.completion_tokens, tracker.total_tokens) == (12, 4, 16)


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


@pytest.mark.parametrize(("arguments", "category"), [
    ('{"private_argument":"SECRET_CANARY', "unterminated_string"),
    ('{"private_argument":', "unexpected_end"), ("SECRET_CANARY", "syntax"),
])
def test_native_protocol_diagnostics_preserve_structure_without_response_text(arguments, category):
    payload = completion(tool_message(arguments=arguments))
    payload["choices"][0]["finish_reason"] = "length"
    payload["choices"][0]["message"]["content"] = "SECRET_CANARY"
    with provider_for(lambda _: httpx.Response(200, json=payload)) as provider:
        with pytest.raises(NativeFunctionCallingError) as caught:
            provider.generate_messages([], [])
    diagnostic = caught.value.diagnostics
    assert diagnostic["finish_reason"] == "length" and diagnostic["response_kind"] == "ai_message"
    assert diagnostic["tool_call_count"] == 0 and diagnostic["invalid_tool_call_count"] == 1
    assert diagnostic["tool_id_status"] == "valid"
    assert diagnostic["arguments"][0]["json_failure"] == category
    serialized = json.dumps(diagnostic)
    assert "SECRET_CANARY" not in serialized and "private_argument" not in serialized and "call-1" not in serialized


def test_native_protocol_diagnostics_retain_duplicate_id_status_without_id_values():
    message = tool_message(arguments="{}")
    message["tool_calls"] *= 2
    with provider_for(lambda _: httpx.Response(200, json=completion(message))) as provider:
        with pytest.raises(NativeFunctionCallingError) as caught:
            provider.generate_messages([], [])
    assert caught.value.diagnostics["tool_id_status"] == "duplicate"
    assert caught.value.diagnostics["tool_call_count"] == 2


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
    failure = httpx.ReadError("interrupted stream")

    class BrokenStream(httpx.SyncByteStream):
        closed = False

        # Simulate the dependency failure required by this regression scenario so its error or
        # fallback path is exercised.
        def __iter__(self):
            yield sse(chunks=("已输出",)).split("\n\n", 1)[0].encode() + b"\n\n"
            raise failure

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
        with capture_llm_usage() as tracker, pytest.raises((httpx.ReadError, RuntimeError)) as caught:
            provider.stream_generate("Answer", "Facts", deltas.append)
    assert_stream_read_failure(caught.value, failure, message_prefix="LangChain LLM request failed")
    assert deltas == ["已输出"] and len(calls) == 1
    assert body.closed
    assert tracker.call_count == tracker.failed_call_count == 1
    assert tracker.measured_call_count == 0 and not tracker.token_usage_complete


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


# Regression scenario: production configuration exposes only the ReAct-capable backend.
def test_default_backend_and_requests_rejection(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_LLM_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("LIMITUPLAB_LLM_BACKEND", raising=False)
    assert isinstance(get_llm_provider(), LangChainChatProvider)
    monkeypatch.setenv("LIMITUPLAB_LLM_BACKEND", "requests")
    with pytest.raises(ValueError, match="only langchain"):
        get_llm_provider()
    monkeypatch.setenv("LIMITUPLAB_LLM_BACKEND", "typo")
    with pytest.raises(ValueError, match="LIMITUPLAB_LLM_BACKEND"):
        get_llm_provider()
    monkeypatch.setenv("LIMITUPLAB_LLM_BACKEND", "langchain")
    monkeypatch.setenv("LIMITUPLAB_LLM_ENABLED", "false")
    assert isinstance(get_llm_provider(), DisabledLLMProvider)
