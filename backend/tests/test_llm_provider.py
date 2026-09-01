import unittest

import requests

from app.services.llm_provider import (
    NativeFunctionCallingError,
    NativeFunctionCallingUnavailable,
    OpenAIChatCompletionsProvider,
)


class FakeResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": "{\"ok\":true}"}}],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 4,
                "total_tokens": 16,
            },
        }


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return FakeResponse()


class FakeFunctionResponse(FakeResponse):
    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-plan",
                                "type": "function",
                                "function": {
                                    "name": "submit_agent_plan",
                                    "arguments": (
                                        '{"intent_label":"limit_up_query",'
                                        '"capabilities":["limit_up_pool"],'
                                        '"context_mode":"standalone",'
                                        '"context_capabilities":[],"safety":"normal",'
                                        '"tool_calls":[],"answer_directly":""}'
                                    ),
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 8,
                "total_tokens": 28,
            },
        }


class FakeFunctionSession(FakeSession):
    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return FakeFunctionResponse()


class MissingFunctionCallResponse(FakeResponse):
    def json(self) -> dict:
        return {"choices": [{"message": {"content": "no function"}}]}


class MissingFunctionCallSession(FakeSession):
    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return MissingFunctionCallResponse()


class FakeStreamingResponse:
    def __init__(self) -> None:
        self.closed = False

    def raise_for_status(self) -> None:
        pass

    def iter_lines(self):
        yield b'data: {"choices":[{"delta":{"content":"\\u6839\\u636e"}}]}'
        yield b'data: {"choices":[{"delta":{"content":" facts"}}]}'
        yield b'data: {"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3,"total_tokens":11}}'
        yield b"data: [DONE]"

    def close(self) -> None:
        self.closed = True


class FakeStreamingSession(FakeSession):
    def __init__(self) -> None:
        super().__init__()
        self.response = FakeStreamingResponse()

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


class TransientFailureSession(FakeSession):
    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if len(self.calls) == 1:
            raise requests.Timeout("temporary timeout")
        return FakeResponse()


class LLMProviderTest(unittest.TestCase):
    def test_native_function_call_forces_named_tool_and_parses_arguments(self) -> None:
        session = FakeFunctionSession()
        provider = OpenAIChatCompletionsProvider(
            api_key="test-key",
            model="deepseek-v4-flash",
            planner_max_tokens=320,
            session=session,  # type: ignore[arg-type]
        )
        parameters = {
            "type": "object",
            "properties": {"intent_label": {"type": "string"}},
            "required": ["intent_label"],
        }

        result = provider.generate_function_call(
            "Call submit_agent_plan exactly once.",
            "Plan this question.",
            function_name="submit_agent_plan",
            function_description="Submit an Agent plan.",
            parameters=parameters,
        )

        payload = session.calls[0]["json"]
        self.assertEqual(
            payload["tool_choice"],
            {
                "type": "function",
                "function": {"name": "submit_agent_plan"},
            },
        )
        self.assertEqual(
            payload["tools"][0]["function"]["parameters"],
            parameters,
        )
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["max_tokens"], 320)
        self.assertEqual(result.response_mode, "function_call")
        self.assertEqual(result.function_name, "submit_agent_plan")
        self.assertEqual(result.total_tokens, 28)
        self.assertIn('"capabilities":["limit_up_pool"]', result.content)

    def test_native_function_call_rejects_missing_call(self) -> None:
        provider = OpenAIChatCompletionsProvider(
            api_key="test-key",
            session=MissingFunctionCallSession(),  # type: ignore[arg-type]
        )

        with self.assertRaises(NativeFunctionCallingError):
            provider.generate_function_call(
                "Call submit_agent_plan.",
                "Plan.",
                function_name="submit_agent_plan",
                function_description="Submit a plan.",
                parameters={"type": "object"},
            )

    def test_native_function_call_can_be_disabled_for_compatible_provider(self) -> None:
        provider = OpenAIChatCompletionsProvider(
            api_key="test-key",
            native_function_calling_enabled=False,
            session=FakeFunctionSession(),  # type: ignore[arg-type]
        )

        with self.assertRaises(NativeFunctionCallingUnavailable):
            provider.generate_function_call(
                "Call submit_agent_plan.",
                "Plan.",
                function_name="submit_agent_plan",
                function_description="Submit a plan.",
                parameters={"type": "object"},
            )

    def test_planner_uses_non_thinking_mode_and_small_token_budget(self) -> None:
        session = FakeSession()
        provider = OpenAIChatCompletionsProvider(
            api_key="test-key",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            planner_max_tokens=320,
            session=session,  # type: ignore[arg-type]
        )

        result = provider.generate(
            "Return only valid JSON. No markdown.",
            "Choose a tool.",
        )

        payload = session.calls[0]["json"]
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 320)
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(result.content, '{"ok":true}')
        self.assertGreater(result.prompt_chars, 0)
        self.assertEqual(result.total_tokens, 16)

    def test_answer_does_not_send_a_token_limit(self) -> None:
        session = FakeSession()
        provider = OpenAIChatCompletionsProvider(
            api_key="test-key",
            session=session,  # type: ignore[arg-type]
        )

        provider.generate("Answer in Chinese.", "Facts")

        self.assertNotIn("max_tokens", session.calls[0]["json"])

    def test_exhaustive_list_does_not_send_a_token_limit(self) -> None:
        session = FakeSession()
        provider = OpenAIChatCompletionsProvider(
            api_key="test-key",
            session=session,  # type: ignore[arg-type]
        )

        provider.generate("EXHAUSTIVE_LIST_OUTPUT", "Facts")

        self.assertNotIn("max_tokens", session.calls[0]["json"])

    def test_stream_answer_emits_native_sse_deltas(self) -> None:
        session = FakeStreamingSession()
        provider = OpenAIChatCompletionsProvider(
            api_key="test-key",
            model="deepseek-v4-flash",
            session=session,  # type: ignore[arg-type]
        )
        deltas: list[str] = []

        result = provider.stream_generate(
            "Answer in Chinese.",
            "Facts",
            deltas.append,
        )

        self.assertEqual(deltas, ["\u6839\u636e", " facts"])
        self.assertEqual(result.content, "\u6839\u636e facts")
        self.assertTrue(session.calls[0]["json"]["stream"])
        self.assertEqual(
            session.calls[0]["json"]["stream_options"],
            {"include_usage": True},
        )
        self.assertTrue(session.calls[0]["stream"])
        self.assertTrue(session.response.closed)
        self.assertEqual(result.prompt_tokens, 8)
        self.assertEqual(result.completion_tokens, 3)
        self.assertEqual(result.total_tokens, 11)

    def test_non_streaming_request_retries_transient_timeout(self) -> None:
        session = TransientFailureSession()
        delays: list[float] = []
        provider = OpenAIChatCompletionsProvider(
            api_key="test-key",
            max_attempts=2,
            retry_delay_seconds=0.25,
            session=session,  # type: ignore[arg-type]
            sleep_fn=delays.append,
        )

        result = provider.generate(
            "Return only valid JSON. No markdown.",
            "Choose a tool.",
        )

        self.assertEqual(result.content, '{"ok":true}')
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(delays, [0.25])


if __name__ == "__main__":
    unittest.main()
