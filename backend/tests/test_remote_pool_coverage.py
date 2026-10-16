"""Remote pool limits and upstream pages must not become false totals."""

from datetime import date
from types import SimpleNamespace

from app.agents.tools import AgentToolRegistry
from app.agents.react_runtime.contracts import Compute
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.partial_delivery import partial_delivery
from app.agents.react_runtime.task_contract import TaskInterpretation
from app.collectors.hithink_finance_collector import HithinkLimitUpFact, HithinkLimitUpPoolSnapshot


def registry(*, returned=10, total=10):
    rows = [HithinkLimitUpFact(
        symbol=f"{i:06}", thscode=f"{i:06}.SZ", name=f"合成{i}", is_st=False,
        is_new=False, last_price=1, change_pct=10, limit_up_time="09:30:00",
        limit_up_reason=None, board_height=2, board_height_text="2板",
        seal_amount=None, max_seal_amount=None,
    ) for i in range(1, returned + 1)]
    snapshot = HithinkLimitUpPoolSnapshot(
        trade_date=date(2026, 10, 16), page=1, page_size=200, total=total, items=rows,
    )
    return AgentToolRegistry([], first_board_repository=object(),
        hithink_collector=SimpleNamespace(collect_limit_up_pool=lambda **_: snapshot))


def recover(result, limit=3):
    store = EvidenceStore()
    source = store.add(tool=result.name, payload=result.output,
        state=result.result_status, arguments=result.input)
    selected = store.compute(Compute(evidence_id=source, sort_by="symbol",
        descending=False, limit=limit))
    task = TaskInterpretation(current_task="展示代码升序的前3只，并列出封单金额。",
        output_contract={"table_required": True,
            "field_requests": [{"requested": "代码", "field": "symbol"},
                               {"requested": "封单金额", "field": "seal_amount"}],
            "row_selection": {"requested": "前3只", "limit": limit}})
    return partial_delivery(store, task, selected)


def test_local_limit_retains_full_filtered_total_and_prevents_false_recovery_total():
    result = registry().remote_limit_up_pool(board_height=2, limit=5)
    assert result.output["matched_count"] == 10
    assert result.output["returned_count"] == len(result.output["items"]) == 5
    assert result.output["source_truncated"] is True
    assert result.output["complete"] is False
    assert result.result_status == "partial"
    assert "筛选共匹配 10 只" in result.summary and "本次返回 5 只" in result.summary
    assert recover(result) is None


def test_upstream_page_cannot_prove_total_filtered_count():
    result = registry(returned=200, total=300).remote_limit_up_pool(board_height=2, limit=100)
    assert result.output["matched_count"] is None
    assert result.output["upstream_total"] == 300
    assert result.output["returned_count"] == 100
    assert result.output["source_truncated"] is True
    assert result.output["complete"] is False
    assert result.result_status == "partial"
    assert "完整匹配总数未获取" in result.summary
    assert recover(result) is None


def test_no_matches_on_incomplete_page_is_partial_not_proven_empty():
    result = registry(returned=200, total=300).remote_limit_up_pool(board_height=9)
    assert result.output["items"] == []
    assert result.output["matched_count"] is None
    assert result.output["source_truncated"] is True
    assert result.result_status == "partial"


def test_complete_no_matches_is_true_empty():
    result = registry().remote_limit_up_pool(board_height=9)
    assert result.output["items"] == []
    assert result.output["matched_count"] == result.output["returned_count"] == 0
    assert result.output["source_truncated"] is False
    assert result.output["complete"] is True
    assert result.result_status == "empty"


def test_complete_collection_can_recover_true_total_and_selected_rows():
    result = registry().remote_limit_up_pool(board_height=2, limit=100)
    assert result.output["matched_count"] == result.output["returned_count"] == 10
    assert result.output["source_truncated"] is False
    assert result.result_status == "ok"
    final = recover(result)
    assert final is not None
    assert "本轮筛选范围内共10条，展示3条" in final.answer
