import json

import pytest

from app.agents.react_runtime.answer_stream import FinishAnswerStream


def chunk(args, name=None, index=0, **extra):
    return {"name": name, "args": args, "index": index, **extra}


@pytest.mark.parametrize("width", [1, 2, 3, 5, 13, 1000])
@pytest.mark.parametrize("ascii_only", [True, False])
def test_decodes_answer_at_arbitrary_boundaries_and_field_order(width, ascii_only):
    answer = '中文 😀 "引号" \\ /\n\r\t\b\f'
    document = json.dumps({"nested": {"answer": "secret"}, "count": 123.5,
                           "answer": answer, "tail": [True, None]}, ensure_ascii=ascii_only)
    deltas = []
    stream = FinishAnswerStream(deltas.append)
    for offset in range(0, len(document), width):
        stream.feed([chunk(document[offset:offset + width], "finish" if offset == 0 else None,
                           id="fragment" if offset % 2 else None)])
    assert "".join(deltas) == answer
    assert not stream.disabled


def test_emits_before_json_and_answer_are_complete():
    deltas = []
    stream = FinishAnswerStream(deltas.append)
    stream.feed([chunk('{"answer":"第', "finish")])
    assert deltas == ["第"]
    stream.feed([chunk('一段\\uD83D')])
    assert deltas[-1] == "一段"
    stream.feed([chunk('\\uDE00"}')])
    assert "".join(deltas) == "第一段😀"


def test_fragmented_name_buffers_arguments_until_finish_is_confirmed():
    deltas = []
    stream = FinishAnswerStream(deltas.append)
    stream.feed([chunk('{"answer":"hello', "fin")])
    assert not deltas
    stream.feed([chunk('"}', "ish")])
    assert deltas == ["hello"]


@pytest.mark.parametrize("name", ["query", "finish_extra"])
def test_other_tool_arguments_never_stream(name):
    deltas = []
    stream = FinishAnswerStream(deltas.append)
    stream.feed([chunk('{"answer":"secret"}', name)])
    assert not deltas


def test_second_tool_invalidates_previously_streamed_answer_once():
    deltas, resets = [], []
    stream = FinishAnswerStream(deltas.append, lambda: resets.append(True))
    stream.feed([chunk('{"answer":"preview', "finish")])
    stream.feed([chunk('{}', "query", 1)])
    stream.feed([chunk('rest"}')])
    stream.invalidate()
    assert deltas == ["preview"]
    assert resets == [True]


def test_parallel_batch_does_not_emit_a_preview():
    deltas = []
    stream = FinishAnswerStream(deltas.append)
    stream.feed([chunk('{"answer":"secret"}', "finish"), chunk('{}', "query", 1)])
    assert not deltas


@pytest.mark.parametrize("suffix", ['\\q"}', '\\uZZZZ"}', '\\uDC00"}',
                                    '\\uD800\\u0041"}', '\\uD800"}', '\n"}',
                                    '","answer":"again"}'])
def test_malformed_answer_and_duplicate_answer_invalidate(suffix):
    deltas = []
    stream = FinishAnswerStream(deltas.append)
    stream.feed([chunk('{"answer":"preview', "finish")])
    stream.feed([chunk(suffix)])
    assert stream.disabled
    assert deltas == ["preview"]


@pytest.mark.parametrize("prefix", ['"value":NaN,', '"value":Infinity,',
                                    '"value":1e,', '"value":[1,],', '"value":01,'])
def test_invalid_preceding_json_never_exposes_answer(prefix):
    deltas = []
    FinishAnswerStream(deltas.append).feed([chunk('{'+prefix+'"answer":"secret"}', "finish")])
    assert not deltas


@pytest.mark.parametrize("invalidate", [False, True])
def test_callback_cancellation_propagates(invalidate):
    def cancelled(*_):
        raise InterruptedError("cancelled")
    stream = FinishAnswerStream(cancelled, cancelled)
    with pytest.raises(InterruptedError):
        if invalidate:
            stream.invalidate()
        else:
            stream.feed([chunk('{"answer":"hello', "finish")])
