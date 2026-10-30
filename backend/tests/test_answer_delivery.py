"""Candidate answer streaming never replaces the committed delivery gate."""

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.runner.answer_delivery import AnswerDelivery
from runner_node_harness import NodeRunner
from test_runner_shadow import ScriptedProvider, call, registry, request


def collector():
    events = []

    def emit(kind, payload):
        events.append((kind, payload))

    return events, emit


def test_deltas_are_coalesced_with_code_point_offsets():
    events, emit = collector()
    delivery = AnswerDelivery(emit)

    delivery.feed("中📊")  # First frame flushes immediately.
    assert events == [
        ("answer_start", {"revision": 1, "provisional": True}),
        ("answer_delta", {"offset": 0, "delta": "中📊"}),
    ]

    delivery.feed("文")
    delivery.finish()
    assert events[-1] == ("answer_delta", {"offset": 2, "delta": "文"})
    assert "".join(item["delta"] for kind, item in events if kind == "answer_delta") == "中📊文"


def test_withdraw_clears_the_visible_draft_and_the_next_round_uses_a_new_revision():
    events, emit = collector()
    delivery = AnswerDelivery(emit)
    delivery.feed("尚未完成的草稿")
    delivery.withdraw("正在继续查询数据")
    assert events[-1] == ("answer_reset", {"revision": 1, "message": "正在继续查询数据"})

    delivery.begin_round()
    delivery.feed("正式回答")
    delivery.finish()
    starts = [item for kind, item in events if kind == "answer_start"]
    assert [item["revision"] for item in starts] == [1, 2]
    assert events[-1] == ("answer_delta", {"offset": 0, "delta": "正式回答"})


def test_compliance_signal_withdraws_the_draft_and_blocks_the_round():
    events, emit = collector()
    delivery = AnswerDelivery(emit)
    delivery.feed("先给结论")
    assert events[-1][0] == "answer_delta"

    delivery.feed("，建议买入该股")
    delivery.finish()
    assert events[-1] == ("answer_reset", {"revision": 1, "message": "回答正在接受合规核验"})
    assert "建议买入" not in mode_text(events)

    delivery.feed("继续输出")
    delivery.finish()
    assert "继续输出" not in mode_text(events)

    delivery.begin_round()
    delivery.feed("客观研究事实")
    delivery.finish()
    assert mode_text(events) == "客观研究事实"


def test_sanitizer_rewrites_a_split_identifier_without_keeping_a_stale_prefix():
    events, emit = collector()
    delivery = AnswerDelivery(emit)
    delivery.feed("limit_up")
    delivery.feed("_events")
    delivery.finish()

    assert events[0] == ("answer_start", {"revision": 1, "provisional": True})
    assert events[-3][0] == "answer_reset"
    assert events[-2] == ("answer_start", {"revision": 2, "provisional": True})
    assert events[-1] == ("answer_delta", {"offset": 0, "delta": "涨停事件数据"})
    assert mode_text(events) == "涨停事件数据"


def mode_text(events):
    """Text visible after the last reset, as a reader would see it."""

    text = ""
    for kind, payload in events:
        if kind == "answer_start":
            text = ""
        elif kind == "answer_reset":
            text = ""
        elif kind == "answer_delta":
            assert payload["offset"] == len(text)
            text += payload["delta"]
    return text


def test_a_broken_emitter_disables_streaming_without_failing_the_answer():
    def emit(kind, payload):
        raise RuntimeError("transport unavailable")

    delivery = AnswerDelivery(emit)
    delivery.feed("答案")
    delivery.finish()
    delivery.feed("更多")
    delivery.finish()


class StreamingProvider(ScriptedProvider):
    """Emit native chunks before returning the scripted message."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self.options = []

    def generate_messages(self, messages, tools, **kwargs):
        self.calls.append(messages)
        self.options.append({"tools": tools, **kwargs})
        chunks, response = self.steps.pop(0)
        for text in chunks:
            if kwargs.get("on_chunk") is not None:
                kwargs["on_chunk"](AIMessageChunk(content=text))
        if isinstance(response, Exception):
            raise response
        return response


def test_runner_publishes_the_final_answer_and_withdraws_a_tool_round():
    provider = StreamingProvider([
        (["我先查询数据"], call("market_summary", {})),
        (["最新交易日涨停", "数量已取得。"], AIMessage(content="最新交易日涨停数量已取得。")),
    ])
    events, emit = collector()
    runner = NodeRunner(request(), registry(), provider)
    runner.enable_answer_delivery(emit)
    result = runner.run_nodes()

    assert result.status == "answered"
    assert events[0] == ("answer_start", {"revision": 1, "provisional": True})
    assert events[1] == ("answer_delta", {"offset": 0, "delta": "我先查询数据"})
    assert events[2][0] == "answer_reset"
    assert events[-1][0] == "answer_delta"
    assert mode_text(events) == "最新交易日涨停数量已取得。"


def test_runner_does_not_stream_when_no_channel_is_attached():
    provider = StreamingProvider([
        ([], AIMessage(content="直接回答")),
    ])
    result = NodeRunner(request(), registry(), provider).run_nodes()
    assert result.status == "answered"
