import json

from langchain_core.messages import AIMessage, ToolMessage

from app.agents.result_paging import READ_RESULT_TOOL
from app.agents.tool_context import expand_tool_observation
from durable_runtime_fixtures import run_async
from test_durable_agent import runner, runtime_for, tool_call, world
from test_runner_production import call, input_review
from test_runner_shadow import ScriptedProvider


def test_graph_can_read_a_large_immutable_result_without_requerying_market(world):
    request, control, history = world.start("查询固定样本，然后只显示第1001行的代码")

    class PagingProvider(ScriptedProvider):
        def generate_messages(self, messages, tools, **kwargs):
            if len(self.calls) == 2:
                manifest = json.loads(messages[-1].content)["result_view"]
                assert manifest["kind"] == "manifest" and manifest["rows_returned"] == 0
                self.responses[0] = call(READ_RESULT_TOOL, {
                    "result_id": manifest["result_id"], "expected_hash": manifest["content_hash"],
                    "offset": 1000, "limit": 1, "fields": ["symbol", "name"],
                }, "page-1")
            if len(self.calls) == 3:
                page = expand_tool_observation(json.loads(messages[-1].content))
                assert page["offset"] == 1000 and page["row_count"] == 1
                self.responses[0] = AIMessage(content=f"第1001行代码为 {page['rows'][0]['symbol']}。")
            return super().generate_messages(messages, tools, **kwargs)

    provider = PagingProvider([input_review(), tool_call(), None, None])
    execution, market_calls = runner(request, provider, history=history, row_multiplier=1000)

    async def scenario():
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            assert result.status == "answered"
            assert result.tool_calls == ["limit_up_events", READ_RESULT_TOOL]
            assert len(market_calls) == 1
            assert len(result.tool_messages[0]["rows"]) == 3000
            assert result.tool_messages[1]["rows"] == [{"symbol": "000002", "name": "样本乙"}]
            assert all(len(message.content) < 20000 for message in provider.calls[-1] if isinstance(message, ToolMessage))
    run_async(scenario())
