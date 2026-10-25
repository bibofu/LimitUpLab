"""Synthetic, versioned tool world; none of these rows describe real markets."""

from copy import deepcopy
import json

from langchain_core.messages import AIMessage, ToolMessage

from app.agents.context_builder import build_context
from app.agents.structured_references import references_from_committed_result

OLD_DATE = "2026-09-01"
NEW_DATE = "2026-09-02"
SOURCE = "fixture://memory-golden/v1"
ROWS = [
    {"symbol": "600001", "name": "样本甲", "close": 10.0},
    {"symbol": "000002", "name": "样本乙", "close": 20.0},
    {"symbol": "300003", "name": "样本丙", "close": 30.0},
]
ORDER = ["000002", "600001", "300003"]

# Scripted semantic classifications for this fixed world only. Production does
# not infer these intents from wording; its input reviewer supplies the schema.
REFERENCE_INTENTS = {
    "第一只最近走势": ("entities", 1), "第二只最近5日走势": ("entities", 2),
    "第三只的来源": ("entities", 3), "第2只换手率": ("entities", 2),
    "第2名是什么股票": ("entities", 2), "第二个的成交额": ("entities", 2),
    "第3支历史行情": ("entities", 3), "请解释第一名的数据缺口": ("entities", 1),
    "刚才那批走势": ("entities", None), "这批股票的缺口": ("entities", None),
    "上面那批的来源": ("entities", None), "继续按上次条件": ("conditions", None),
    "第四只走势": ("entities", 4), "第0只走势": ("entities", 0),
    "第二只走势": ("entities", 2), "第二只最近5日走势怎么样？": ("entities", 2),
    "第一只": ("entities", 1), "刚才那批": ("entities", None),
    "完整列出刚才那批": ("entities", None),
}


def committed_response(*, run_id="run-old", order=None, answer=None, **updates):
    order = ORDER if order is None else order
    if answer is None:
        answer = "| 股票代码 |\n|---|\n" + "\n".join(f"| {symbol} |" for symbol in order)
    return {"run_id": run_id, "task_status": "complete", "answer": answer,
            "tool_results": [{"name": "fixture_ratings", "status": "success", "output": {
                "status": "ok", "rows": deepcopy(ROWS), "trade_date": OLD_DATE,
                "source": SOURCE, "filters": {"board_height": 1}, "sort_rules": "fixture_score_desc",
                "query_scope": {"scope_version": 1, "tool": "fixture_ratings",
                                "arguments": {"board_height": 1, "sort_by": "score", "sort_order": "desc"},
                                "explicit_arguments": {"board_height": 1, "sort_by": "score"}},
            }}], **updates}


def references(**kwargs):
    result = committed_response(**kwargs)
    return references_from_committed_result(result["run_id"], result)


def context(query="第二只最近5日走势怎么样？", refs=None, conversation=None, working=None, reference_intent=None, **options):
    state = {"active_references": references() if refs is None else refs, **(conversation or {})}
    if reference_intent is None:
        scripted = REFERENCE_INTENTS.get(query)
        reference_intent = ({"kind": scripted[0], "quote": query, "ordinal": scripted[1]}
                            if scripted else {"kind": "none"})
        if scripted and scripted[0] == "entities" and scripted[1] is None:
            reference_intent["all_entities"] = True
    return build_context(system_prompt="只用本轮工具事实做研究。", query=query,
                         conversation=state, working_messages=working or [], reference_intent=reference_intent, **options)


def context_data(result):
    return json.loads(result.messages[1].content.split("\n", 1)[1])


class FixedToolWorld:
    """Observe exact arguments and return a newer immutable data version."""

    def __init__(self):
        self.calls = []

    def query(self, symbols, *, trade_date=NEW_DATE):
        self.calls.append({"symbols": list(symbols), "trade_date": trade_date})
        assert symbols and all(symbol in {row["symbol"] for row in ROWS} for symbol in symbols)
        selected = [dict(row, close=row["close"] + 1) for symbol in symbols for row in ROWS if row["symbol"] == symbol]
        return {"status": "ok", "rows": selected, "row_count": len(selected),
                "trade_date": trade_date, "source": SOURCE + "/new-snapshot"}


def tool_messages(payload, *, call_id="current-observation"):
    return [AIMessage(content="", tool_calls=[{"name": "fixture_quote", "args": {}, "id": call_id}]),
            ToolMessage(content=json.dumps(payload, ensure_ascii=False), tool_call_id=call_id)]
