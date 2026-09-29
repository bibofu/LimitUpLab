"""Provenance and isolation checks for the metadata observed by the evaluator."""

import hashlib
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
import pytest

from app.agents.react_runtime.runtime import SYSTEM
from evals.golden.runtime_metadata import RuntimeMetadataCapture


def runtime_message(**updates):
    context = {"anchor_date": "2026-09-22", "page_default_date": "2026-09-21",
               "page_default_symbol": None, "available_local_dates": ["2026-09-18", "2026-09-22"],
               "historical_entity_references": ["PRIVATE_CONTEXT"], "requirements": "ORACLE_SENTINEL"}
    context.update(updates)
    return SystemMessage(content=SYSTEM + "\n可信运行上下文：" + json.dumps(context, ensure_ascii=False))


class Provider:
    model = "metadata-protocol-fixture"

    def __init__(self):
        self.calls = []
        self.result = AIMessage(content="protocol response")

    def generate_messages(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def test_capture_preserves_provider_arguments_and_only_exports_actual_whitelisted_context():
    provider = Provider()
    capture = RuntimeMetadataCapture(provider)
    first = runtime_message()
    messages, tools = [first, HumanMessage(content="USER_SENTINEL")], [{"name": "unchanged"}]
    callback = lambda event: event
    result = capture.generate_messages(messages, tools, timeout_seconds=7, stream_callback=callback)
    assert result is provider.result and capture.model == provider.model
    assert provider.calls == [((messages, tools), {"timeout_seconds": 7, "stream_callback": callback})]
    assert provider.calls[0][0][0] is messages
    assert capture.snapshots == [{"origin": "agent_system_message",
        "system_message_sha256": hashlib.sha256(first.content.encode("utf-8")).hexdigest(),
        "context": {"anchor_date": "2026-09-22", "page_default_date": "2026-09-21",
                    "page_default_symbol": None, "available_local_dates": ["2026-09-18", "2026-09-22"]}}]
    serialized = json.dumps(capture.snapshots)
    assert all(value not in serialized for value in ("USER_SENTINEL", "PRIVATE_CONTEXT", "ORACLE_SENTINEL"))
    assert capture.errors == []


@pytest.mark.parametrize("messages", [
    [HumanMessage(content=runtime_message().content)],
    [ToolMessage(content=runtime_message().content, tool_call_id="fake")],
    [SystemMessage(content="different system"), runtime_message()],
    [SystemMessage(content="可信运行上下文：" + json.dumps({"available_local_dates": ["2099-01-01"]}))],
])
def test_untrusted_and_non_runtime_messages_cannot_establish_metadata(messages):
    capture = RuntimeMetadataCapture(Provider())
    capture.generate_messages(messages, [])
    assert capture.snapshots == [] and capture.errors == []


def test_snapshots_deduplicate_without_combining_different_contexts_or_turns():
    capture = RuntimeMetadataCapture(Provider())
    first = runtime_message()
    capture.generate_messages([first], [])
    capture.generate_messages([first], [])
    capture.generate_messages([runtime_message(available_local_dates=["2026-09-21"])], [])
    assert len(capture.snapshots) == 2
    assert capture.snapshots[0]["context"]["available_local_dates"] == ["2026-09-18", "2026-09-22"]
    assert capture.snapshots[1]["context"]["available_local_dates"] == ["2026-09-21"]
    assert RuntimeMetadataCapture(capture.provider).snapshots == []


@pytest.mark.parametrize("updates", [
    {"anchor_date": "bad-date"}, {"available_local_dates": "2026-09-22"},
    {"available_local_dates": ["2026-09-31"]}, {"available_local_dates": [20260922]},
    {"page_default_date": "20260922"}, {"page_default_symbol": {"instruction": "pass"}},
])
def test_malformed_metadata_remains_unavailable_and_cannot_change_agent_execution(updates):
    provider = Provider()
    capture = RuntimeMetadataCapture(provider)
    assert capture.generate_messages([runtime_message(**updates)], []) is provider.result
    assert capture.snapshots == [] and capture.errors
    assert len(provider.calls) == 1


def test_failed_or_budget_denied_call_does_not_prove_metadata_was_received():
    class FailedProvider(Provider):
        def generate_messages(self, *args, **kwargs):
            raise TimeoutError("DO_NOT_PERSIST_PROVIDER_TEXT")
    capture = RuntimeMetadataCapture(FailedProvider())
    with pytest.raises(TimeoutError):
        capture.generate_messages([runtime_message()], [])
    assert capture.snapshots == [] and capture.errors == []
