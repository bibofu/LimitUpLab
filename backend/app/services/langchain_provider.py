"""LangChain model adapter; business planning and tool policy stay server-side."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Callable

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from openai import APIError

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

    # Execute the LangChain request or stream, normalize usage and return the common LLM result.
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
            f"LLM did not return exactly one valid {function_name} function call"
        )
    return json.dumps(calls[0]["args"], ensure_ascii=False, separators=(",", ":"))
