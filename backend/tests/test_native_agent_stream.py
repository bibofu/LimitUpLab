"""Native streaming publishes only fenced metadata; delivery remains separate."""

import asyncio
import json
import threading
from types import SimpleNamespace

from langchain_core.messages import AIMessage
import pytest

from app.agents.runtime_errors import RecoverableRunError
from app.routers import react_chat
from app.services.agent_stream import NativeStreamBridge
from app.services.runtime_coordination import LeaseLost
from durable_runtime_fixtures import run_async
from test_durable_agent import list_answer, runner, runtime_for, tool_call, world
from test_runner_production import input_review
from test_runner_shadow import ScriptedProvider


def test_native_custom_and_updates_are_replayed_without_candidate_text(world, monkeypatch):
    request, control, history = world.start()
    provider = ScriptedProvider([input_review(), tool_call(), list_answer()])
    execution, calls = runner(request, provider, history=history)

    async def scenario():
        async with runtime_for(world) as runtime:
            async def forbidden_invoke(*args, **kwargs):
                raise AssertionError("durable runtime must consume native astream")
            monkeypatch.setattr(runtime.graph, "ainvoke", forbidden_invoke)
            result = await runtime.run(execution, control)
            events = world.journal.events(control.row["run_id"], world.owner)
            kinds = [event["event"] for event in events]
            assert "status" in kinds and "tool_start" in kinds and "tool_end" in kinds
            assert kinds.index("tool_start") < kinds.index("tool_end")
            payloads = [json.loads(event["payload_json"]) for event in events]
            assert any(payload.get("phase") == "node_output" for payload in payloads)
            assert any(payload.get("tool_call_id") == "tool-1" for payload in payloads)
            assert all(payload["run_id"] == control.row["run_id"] for payload in payloads)
            assert all("answer" not in payload and "messages" not in payload for payload in payloads)
            assert result.answer not in json.dumps(payloads, ensure_ascii=False)
            assert "completed" not in kinds and len(calls) == 1
            assert world.journal.get(control.row["run_id"], world.owner)["response_json"] is None
            frames = react_chat.stream(world.journal, control.row, world.owner).body_iterator
            before_delivery = [await anext(frames) for _ in range(len(events) + 1)]
            await frames.aclose()
            assert any("event: tool_start" in frame for frame in before_delivery)
            assert any("event: tool_end" in frame for frame in before_delivery)
            assert not any("event: completed" in frame or result.answer in frame for frame in before_delivery)
            delivered = world.deliver(request, control, result)
            assert delivered["answer"] == result.answer
            cursor = events[1]["seq"]
            assert all(event["seq"] > cursor for event in world.journal.events(control.row["run_id"], world.owner, cursor))

    run_async(scenario())


def test_public_projection_ignores_raw_state_and_untrusted_message_text(world):
    _, control, _ = world.start()

    async def scenario():
        bridge = NativeStreamBridge(control, ["limit_up_events"])
        await bridge.consume("custom", {"kind": "status", "stage": "planning",
            "message": "secret-draft", "answer": "secret-draft", "metadata": {"key": "secret-draft"}})
        await bridge.consume("updates", {"agent": {"done": True, "answer": "secret-draft",
            "conversation": {"long_term_memories": ["secret-draft"]}}})
        await bridge.consume("custom", {"kind": "completed", "stage": "checking", "answer": "secret-draft"})
        await bridge.consume("custom", {"kind": "tool_end", "stage": "tools", "tool_name": "unregistered-name",
                                       "tool_call_id": "invalid identifier\n", "status": "error"})
        events = world.journal.events(control.row["run_id"], world.owner)
        assert len(events) == 3
        serialized = json.dumps(events)
        assert "secret-draft" not in serialized and "unregistered-name" not in serialized
        assert "invalid identifier" not in serialized and "completed" not in serialized

    run_async(scenario())


def test_custom_progress_is_persisted_while_model_node_is_still_running(world):
    entered, release = threading.Event(), threading.Event()
    class BlockingProvider(ScriptedProvider):
        def generate_messages(self, messages, tools, **kwargs):
            if len(self.calls) == 1:
                entered.set()
                assert release.wait(timeout=8)
            return super().generate_messages(messages, tools, **kwargs)
    request, control, history = world.start()
    execution, _ = runner(request, BlockingProvider([input_review(), AIMessage(content="固定说明")]), history=history)
    async def scenario():
        async with runtime_for(world) as runtime:
            task = asyncio.create_task(runtime.run(execution, control))
            try:
                deadline = asyncio.get_running_loop().time() + 4
                while asyncio.get_running_loop().time() < deadline:
                    events = await asyncio.to_thread(world.journal.events, control.row["run_id"], world.owner)
                    if entered.is_set() and len(events) >= 3:
                        break
                    await asyncio.sleep(.01)
                assert entered.is_set() and len(events) >= 3 and not task.done()
                assert all(event["event"] == "status" for event in events)
            finally:
                release.set()
                await task
    run_async(scenario())


def test_stream_event_write_rejects_worker_after_lease_replacement(world):
    _, control, _ = world.start()
    control.execution.release()
    replacement = world.journal.claim(control.row["run_id"])
    try:
        async def scenario():
            with pytest.raises(LeaseLost):
                await NativeStreamBridge(control).publish({"kind": "status", "stage": "planning"})
        run_async(scenario())
        assert world.journal.events(control.row["run_id"], world.owner) == []
    finally:
        replacement.release()


def test_interrupted_native_stream_resumes_without_repeating_committed_tool(world):
    request, control, history = world.start()
    execution, calls = runner(request, ScriptedProvider([input_review(), tool_call(), ConnectionError("private-error")]), history=history)

    async def scenario():
        async with runtime_for(world) as runtime:
            with pytest.raises(RecoverableRunError):
                await runtime.run(execution, control)
        assert world.journal.get(control.row["run_id"], world.owner)["response_json"] is None
        execution.provider = ScriptedProvider([list_answer()])
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
        events = world.journal.events(control.row["run_id"], world.owner)
        assert any(json.loads(event["payload_json"]).get("phase") == "resume" for event in events)
        assert [event["event"] for event in events].count("tool_start") == 1
        assert "private-error" not in json.dumps(events)
        assert len(calls) == 1 and result.status == "answered"

    run_async(scenario())


def test_update_with_done_cannot_substitute_for_final_persisted_snapshot(world, monkeypatch):
    request, control, history = world.start()
    execution, _ = runner(request, ScriptedProvider([]), history=history)

    async def scenario():
        async with runtime_for(world) as runtime:
            async def snapshot(*args, **kwargs):
                return SimpleNamespace(values={}, next=())
            async def premature(*args, **kwargs):
                yield "updates", {"agent": {"done": True, "answer": "uncheckpointed-draft"}}
            monkeypatch.setattr(runtime.graph, "aget_state", snapshot)
            monkeypatch.setattr(runtime.graph, "astream", premature)
            with pytest.raises(RecoverableRunError):
                await runtime.run(execution, control)
            assert world.journal.get(control.row["run_id"], world.owner)["response_json"] is None
    run_async(scenario())
