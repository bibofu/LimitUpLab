import unittest

from app.services.llm_provider import OpenAIChatCompletionsProvider


class FakeResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "{\"ok\":true}"}}]}


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return FakeResponse()


class FakeStreamingResponse:
    def __init__(self) -> None:
        self.closed = False

    def raise_for_status(self) -> None:
        pass

    def iter_lines(self):
        yield b'data: {"choices":[{"delta":{"content":"\\u6839\\u636e"}}]}'
        yield b'data: {"choices":[{"delta":{"content":" facts"}}]}'
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


class LLMProviderTest(unittest.TestCase):
    def test_planner_uses_non_thinking_mode_and_small_token_budget(self) -> None:
        session = FakeSession()
        provider = OpenAIChatCompletionsProvider(
            api_key="test-key",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            planner_max_tokens=320,
            answer_max_tokens=700,
            session=session,  # type: ignore[arg-type]
        )

        result = provider.generate(
            "Return only valid JSON. No markdown.",
            "Choose a tool.",
        )

        payload = session.calls[0]["json"]
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 320)
        self.assertEqual(result.content, '{"ok":true}')
        self.assertGreater(result.prompt_chars, 0)

    def test_answer_uses_bounded_answer_budget(self) -> None:
        session = FakeSession()
        provider = OpenAIChatCompletionsProvider(
            api_key="test-key",
            answer_max_tokens=700,
            session=session,  # type: ignore[arg-type]
        )

        provider.generate("Answer in Chinese.", "Facts")

        self.assertEqual(session.calls[0]["json"]["max_tokens"], 700)

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
        self.assertTrue(session.calls[0]["stream"])
        self.assertTrue(session.response.closed)


if __name__ == "__main__":
    unittest.main()
