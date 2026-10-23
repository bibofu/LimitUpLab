"""LangChain model adapter; business planning and tool policy stay server-side."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Callable

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, message_chunk_to_message
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from openai import APIError

from app.services.agent_tracing import get_agent_tracer, usage_details
from app.services.protocol_diagnostics import response_diagnostics

from app.services.llm_provider import (
    LLMProvider,
    LLMResult,
    NativeFunctionCallingError,
    NativeFunctionCallingUnavailable,
    _parse_token_usage,
    _usage_tracker,
)


# Values are template inputs, so braces inside questions/Facts remain literal.
PROMPT = ChatPromptTemplate.from_messages([
    ("system", "{system_prompt}"),
    ("human", "{user_prompt}"),
])


class AuditedChatOpenAI(ChatOpenAI):
    """Preserve the existing compatible-provider wire and billing contracts.

    These narrow integration hooks are covered by wire-format tests and pinned to
    langchain-openai. Missing billing evidence must remain unknown in our ledger.
    """

    # Adapt the outgoing LangChain payload to the provider's supported request fields.
    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # ChatOpenAI renames this to max_completion_tokens. DeepSeek documents
        # max_tokens; preserve the field used by the existing requests adapter.
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        return payload

    # Adapt provider streaming chunks to LangChain's generation format.
    def _convert_chunk_to_generation_chunk(
        self, chunk: dict, default_chunk_class: type, base_generation_info: dict | None,
    ):
        result = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info,
        )
        if result is not None and isinstance(chunk.get("usage"), dict):
            result.message.response_metadata["token_usage"] = chunk["usage"]
        return result


class LangChainChatProvider(LLMProvider):
    """Use LCEL, bind_tools and native streaming behind the existing interface."""

    # Initialize LangChainChatProvider with the supplied dependencies and per-instance state.
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        planner_max_tokens: int,
        thinking_enabled: bool,
        max_attempts: int,
        native_function_calling_enabled: bool,
        chat_model: ChatOpenAI | None = None,
    ):
        self.model = model
        self.planner_max_tokens = planner_max_tokens
        self.native_function_calling_enabled = native_function_calling_enabled
        self.chat_model = chat_model if chat_model is not None else AuditedChatOpenAI(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max(0, max_attempts - 1),
            temperature=0.2,
            stream_usage=True,
            use_responses_api=False,
            extra_body={"thinking": {
                "type": "enabled" if thinking_enabled else "disabled",
            }},
        )

    # Select the model configuration for the current answer-generation task.
    def _answer_model(self, system_prompt: str) -> Runnable:
        if "Return only valid JSON" in system_prompt:
            return self.chat_model.bind(
                temperature=0.0, max_tokens=self.planner_max_tokens,
            )
        return self.chat_model

    # Generate a complete text response through the common LangChain execution and usage-
    # accounting path.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        return self._run(
            self._answer_model(system_prompt), system_prompt, user_prompt,
        )

    def generate_messages(
        self, messages, tools, *, timeout_seconds=30, max_tokens=4096,
        on_chunk: Callable[[AIMessageChunk], None] | None = None,
        tool_choice: str | None = None,
    ):
        """Trace one native turn without changing the provider contract."""

        with get_agent_tracer().generation(
            name="react_decision",
            model=self.model,
            input=_trace_messages(messages),
            parameters={"max_tokens": max_tokens, "tool_count": len(tools)},
            metadata={"tool_choice": tool_choice, "streaming": on_chunk is not None},
        ) as generation:
            message, usage = self._generate_messages(
                messages, tools,
                timeout_seconds=timeout_seconds,
                max_tokens=max_tokens,
                on_chunk=on_chunk,
                tool_choice=tool_choice,
            )
            generation.finish(output=_trace_message_output(message), usage=usage)
            return message

    # Preserve tool messages and optionally report native chunks as they arrive.
    def _generate_messages(
        self, messages, tools, *, timeout_seconds=30, max_tokens=4096,
        on_chunk: Callable[[AIMessageChunk], None] | None = None,
        tool_choice: str | None = None,
    ):
        """Preserve tool messages and optionally report native chunks as they arrive."""
        tracker = _usage_tracker.get()
        if tracker:
            tracker.begin_call(self.model)
        started = perf_counter()
        try:
            # Copying pydantic options alone does not reconfigure the existing SDK
            # client. Set timeout/retries on a request-local client as well.
            options = {
                "max_retries": 0, "request_timeout": timeout_seconds,
            }
            if self.chat_model.root_client is not None:
                client = self.chat_model.root_client.with_options(max_retries=0, timeout=timeout_seconds)
                options.update(root_client=client, client=client.chat.completions)
            model = self.chat_model.model_copy(update=options)
            choice = {"tool_choice": tools[0]["function"]["name"]} if len(tools) == 1 else {}
            thinking = (model.extra_body or {}).get("thinking")
            # When multiple tools are available and thinking is disabled, force a
            # structured tool decision. A direct-answer runner can pass
            # tool_choice="auto" so it can stop querying once facts suffice.
            if tool_choice is not None:
                choice = {"tool_choice": tool_choice}
            elif len(tools) > 1 and isinstance(thinking, dict) and thinking.get("type") == "disabled":
                choice = {"tool_choice": "required"}
            bound = model.bind_tools(tools, temperature=0, max_tokens=max_tokens, **choice) if tools else model.bind(
                temperature=0, max_tokens=max_tokens,
            )
            streamed_message = None
            stream_usage = None
            if on_chunk is None:
                message = bound.invoke(messages, config={"run_name": "react_decision"})
            else:
                stream = bound.stream(messages, config={"run_name": "react_decision"})
                try:
                    for chunk in stream:
                        if not isinstance(chunk, AIMessageChunk):
                            raise NativeFunctionCallingError("Expected an AIMessageChunk", diagnostics=
                                response_diagnostics(chunk, failure_category="response_type"))
                        streamed_message = chunk if streamed_message is None else streamed_message + chunk
                        if isinstance(chunk.response_metadata.get("token_usage"), dict):
                            stream_usage = chunk.response_metadata["token_usage"]
                        on_chunk(chunk)
                finally:
                    stream.close()
                message = message_chunk_to_message(streamed_message) if streamed_message is not None else None
            if not isinstance(message, AIMessage):
                raise NativeFunctionCallingError("Expected an AIMessage", diagnostics=
                    response_diagnostics(message, failure_category="response_type"))
            usage = message.usage_metadata or {}
            raw = stream_usage if on_chunk is not None else message.response_metadata.get("token_usage")
            tokens = _parse_token_usage(raw) if raw else (
                usage.get("input_tokens"), usage.get("output_tokens"), usage.get("total_tokens"),
            )
            result = LLMResult(
                content=_text_content(message), model=self.model, provider="langchain-openai",
                duration_ms=round((perf_counter() - started) * 1000),
                prompt_tokens=tokens[0], completion_tokens=tokens[1], total_tokens=tokens[2],
            )
            if tracker:
                tracker.complete_call(result)
            if streamed_message is not None:
                # LangChain accepts partial JSON while accumulating chunks. At EOF
                # require complete arguments before any tool is executable.
                for call in streamed_message.tool_call_chunks:
                    try:
                        arguments = json.loads(call.get("args") or "")
                    except (TypeError, ValueError) as error:
                        raise NativeFunctionCallingError("Malformed tool arguments", diagnostics=
                            response_diagnostics(streamed_message, failure_category="tool_arguments")) from error
                    if not isinstance(arguments, dict):
                        raise NativeFunctionCallingError("Malformed tool arguments", diagnostics=
                            response_diagnostics(streamed_message, failure_category="tool_arguments"))
            if message.invalid_tool_calls:
                raise NativeFunctionCallingError("Malformed tool arguments", diagnostics=
                    response_diagnostics(message, failure_category="tool_arguments"))
            ids = [call.get("id") for call in message.tool_calls]
            if any(not value for value in ids) or len(ids) != len(set(ids)):
                raise NativeFunctionCallingError("Missing or duplicate tool_call_id", diagnostics=
                    response_diagnostics(message, failure_category="tool_ids"))
            return message, usage_details(*tokens)
        except BaseException as error:
            if tracker:
                tracker.fail_call()
            if isinstance(error, APIError):
                raise RuntimeError(f"Model request failed ({type(error).__name__})") from error
            raise

    # Bind the requested structured function contract and return its arguments as the common LLM
    # result.
    def generate_function_call(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        function_name: str,
        function_description: str,
        parameters: dict[str, Any],
    ) -> LLMResult:
        if not self.native_function_calling_enabled:
            raise NativeFunctionCallingUnavailable(
                "Native function calling is disabled by configuration"
            )
        tool = {"type": "function", "function": {
            "name": function_name,
            "description": function_description,
            "parameters": parameters,
        }}
        choice = {"type": "function", "function": {"name": function_name}}
        bound = self.chat_model.bind_tools(
            [tool], tool_choice=choice,
            temperature=0.0, max_tokens=self.planner_max_tokens,
        )
        contract_chars = len(json.dumps(
            {"tools": [tool], "tool_choice": choice},
            ensure_ascii=False, separators=(",", ":"),
        ))
        return self._run(
            bound, system_prompt, user_prompt,
            function_name=function_name, contract_chars=contract_chars,
        )

    # Generate a response incrementally and forward visible text through the supplied delta
    # callback.
    def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        on_delta: Callable[[str], None],
    ) -> LLMResult:
        return self._run(
            self._answer_model(system_prompt), system_prompt, user_prompt,
            on_delta=on_delta,
        )

    def _run(
        self,
        model: Runnable,
        system_prompt: str,
        user_prompt: str,
        *,
        function_name: str | None = None,
        contract_chars: int = 0,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult:
        """Trace one text call while preserving the existing result contract."""

        with get_agent_tracer().generation(
            name=function_name or "grounded_answer",
            model=self.model,
            input={"system": _trace_text(system_prompt), "user": _trace_text(user_prompt)},
            metadata={"streaming": on_delta is not None},
        ) as generation:
            result = self._run_model(
                model, system_prompt, user_prompt,
                function_name=function_name, contract_chars=contract_chars,
                on_delta=on_delta,
            )
            generation.finish(
                output=_trace_text(result.content),
                usage=usage_details(
                    result.prompt_tokens, result.completion_tokens, result.total_tokens,
                ),
            )
            return result

    # Execute the LangChain request or stream, normalize usage and return the common LLM result.
    def _run_model(
        self,
        model: Runnable,
        system_prompt: str,
        user_prompt: str,
        *,
        function_name: str | None = None,
        contract_chars: int = 0,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult:
        chain = PROMPT | model
        inputs = {"system_prompt": system_prompt, "user_prompt": user_prompt}
        config = {"run_name": function_name or "grounded_answer"}
        started = perf_counter()
        tracker = _usage_tracker.get()
        if tracker is not None:
            tracker.begin_call(self.model)
        try:
            if on_delta is None:
                message = chain.invoke(inputs, config=config)
            else:
                message = None
                stream = chain.stream(inputs, config=config)
                try:
                    for chunk in stream:
                        message = chunk if message is None else message + chunk
                        delta = _text_content(chunk)
                        if delta:
                            on_delta(delta)
                finally:
                    stream.close()
            if not isinstance(message, AIMessage):
                raise RuntimeError("LLM response did not contain an AI message")
            content = (
                _function_arguments(message, function_name)
                if function_name else _text_content(message)
            )
            if not content:
                raise RuntimeError("LLM response did not contain text")
            usage = _parse_token_usage(message.response_metadata.get("token_usage"))
            result = LLMResult(
                content=content, model=self.model, provider="langchain-openai",
                duration_ms=round((perf_counter() - started) * 1000),
                prompt_chars=len(system_prompt) + len(user_prompt) + contract_chars,
                completion_chars=len(content),
                prompt_tokens=usage[0],
                completion_tokens=usage[1],
                total_tokens=usage[2],
                response_mode="function_call" if function_name else "text",
                function_name=function_name,
            )
        except Exception as error:
            # Count all failed logical calls, including interrupted stream callbacks.
            if tracker is not None:
                tracker.fail_call()
            if isinstance(error, APIError):
                raise RuntimeError(
                    f"LangChain LLM request failed ({type(error).__name__})"
                ) from error
            raise
        if tracker is not None:
            tracker.complete_call(result)
        return result


# Extract textual content from a LangChain message, including structured content blocks.
def _text_content(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(
        block if isinstance(block, str) else str(block.get("text", ""))
        for block in message.content
        if isinstance(block, str) or block.get("type") == "text"
    )


# Extract the requested native function call's arguments from an AI message.
def _function_arguments(message: AIMessage, function_name: str) -> str:
    calls = message.tool_calls
    if (
        message.invalid_tool_calls or len(calls) != 1
        or calls[0]["name"] != function_name
        or not isinstance(calls[0]["args"], dict)
    ):
        raise NativeFunctionCallingError(
            f"LLM did not return exactly one valid {function_name} function call", diagnostics=
            response_diagnostics(message, expected_tool=function_name, failure_category="tool_selection"),
        )
    return json.dumps(calls[0]["args"], ensure_ascii=False, separators=(",", ":"))


TRACE_TEXT_LIMIT = 6000


# Bound one traced string so a large tool result cannot dominate a trace.
def _trace_text(value: str) -> str:
    if len(value) <= TRACE_TEXT_LIMIT:
        return value
    return value[:TRACE_TEXT_LIMIT] + f"\n...[{len(value) - TRACE_TEXT_LIMIT} chars truncated]"


# Convert the conversation into a bounded, JSON-safe trace input.
def _trace_messages(messages) -> list[dict[str, Any]]:
    traced: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {
            "role": getattr(message, "type", type(message).__name__),
            "content": _trace_text(
                _text_content(message)
                if isinstance(message, BaseMessage) else str(message)
            ),
        }
        name = getattr(message, "name", None)
        if name:
            item["name"] = name
        calls = getattr(message, "tool_calls", None) or []
        if calls:
            item["tool_calls"] = [
                {"name": call.get("name"), "args": _trace_text(_trace_json(call.get("args")))}
                for call in calls
            ]
        traced.append(item)
    return traced


# Convert one model message into a bounded, JSON-safe trace output.
def _trace_message_output(message: AIMessage) -> dict[str, Any]:
    output: dict[str, Any] = {"content": _trace_text(_text_content(message))}
    if message.tool_calls:
        output["tool_calls"] = [
            {"name": call.get("name"), "args": _trace_text(_trace_json(call.get("args")))}
            for call in message.tool_calls
        ]
    return output


def _trace_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
