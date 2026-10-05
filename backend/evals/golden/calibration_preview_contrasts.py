"""Synthetic observed-view diagnostics, not observations from a real model run.

Production view construction and metadata capture use an in-memory provider.
All expected labels remain evaluator-authored and require independent review.
"""

from copy import deepcopy
import json

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.runtime import SYSTEM
from evals.golden.calibration import CalibrationCase, SOURCE
from evals.golden.calibration_contrasts import _lineage_cases
from evals.golden.runtime_metadata import RuntimeMetadataCapture


class _SyntheticProvider:
    def generate_messages(self, messages, *args, **kwargs):
        return AIMessage(content="Synthetic metadata capture completed")


def _preview_inputs():
    """Build the exact bounded view, with stable IDs and timestamps for replay."""
    payload = {
        "trade_date": "2026-09-22", "universe_count": 10,
        "top_candidates": [{"symbol": f"SYNTHETIC_INCLUDED_{index}", "name": f"合成候选{name}"}
                           for index, name in enumerate("甲乙丙丁")],
        "filtered_out": [{"symbol": f"SYNTHETIC_EXCLUDED_{index}", "name": f"合成排除{name}",
                          "included": False, "excluded_reasons": ["不满足合成资格"], "data_missing": []}
                         for index, name in enumerate("甲乙丙丁戊己")],
        "source": SOURCE, "synthetic": True, "source_truncated": False, "data_missing": [],
    }
    store = EvidenceStore()
    generated_key = store.add(tool="first_board_ratings", payload=deepcopy(payload), state="ok",
        arguments={"trade_date": "2026-09-22", "symbols": None})
    key = "ev_synthetic_preview"
    record = store.records.pop(generated_key)
    record.update(evidence_id=key, retrieved_at="2026-09-22T18:00:00+08:00")
    store.records[key] = record
    content = json.dumps(store.view(key), ensure_ascii=False)
    message = ToolMessage(content=content, name="first_board_ratings", tool_call_id="synthetic-preview-call")
    context = {"anchor_date": "2026-09-22", "page_default_date": None, "page_default_symbol": None,
               "available_local_dates": ["2026-09-22"]}
    capture = RuntimeMetadataCapture(_SyntheticProvider())
    capture.generate_messages([
        SystemMessage(content=SYSTEM + "\n可信运行上下文：" + json.dumps(context, ensure_ascii=False)), message,
    ], [])
    # The system-message hash is deliberately not part of these view-only fixtures.
    metadata = tuple(item for item in capture.snapshots if item["origin"] == "agent_evidence_view")
    traces = ({"name": "first_board_ratings", "status": "success", "summary": "Synthetic rating preview",
               "input": deepcopy(record["arguments"]), "output": deepcopy(payload)},)
    return deepcopy(store.records), traces, metadata, content


def _preview_cases():
    evidence, traces, metadata, _ = _preview_inputs()
    base = "2026-09-22合成评级输入事件10条，候选4只。"
    preview = base + "排除明细的工具预览展示4条，另有2条在预览中省略；这里说的是预览压缩，不是上游缺失。"
    upstream = base + "来源只提供4条排除明细，另有2条在上游缺失。"
    missing_observation = deepcopy(metadata)
    for item in missing_observation:
        item["metadata"].pop("preview_omissions", None)
    return [CalibrationCase(key, "说明合成评级输入事件数和候选数。", answer,
        ("如实给出2026-09-22合成评级输入事件10条、候选4只。",),
        (True, True, True, factual), deepcopy(evidence), business_traces=deepcopy(traces),
        runtime_metadata=deepcopy(runtime), provenance="synthetic-diagnostic; in-memory observed-view capture")
        for key, answer, factual, runtime in (
            ("preview_omission_observed", preview, True, metadata),
            ("preview_omission_claimed_upstream_missing", upstream, False, metadata),
            ("preview_omission_without_observation", preview, None, missing_observation),
        )]


def _independence_cases():
    origin = next(case for case in _lineage_cases() if case.id == "source_lineage_shared")
    base = ("2026-09-22分别查询synthetic_pool_0和synthetic_pool_1，均返回56只；"
            "来源标识分别为synthetic-feed-0和synthetic-feed-1。两次调用各自独立执行。")
    return [CalibrationCase(key, "分别核对两个合成入口的数量并说明来源关系。", answer,
        ("如实给出两个工具在2026-09-22各自返回的数量。",),
        (True, True, source, True), deepcopy(origin.evidence), business_traces=deepcopy(origin.business_traces))
        for key, answer, source in (
            ("query_independence_explicit_shared_upstream", base + "独立指请求分别执行，两个入口仍共享同一上游。", True),
            ("query_independence_inferred_upstream", base + "因此数据采集来自两个相互独立的上游。", False),
        )]


def _partial_absence_case():
    payload = {"trade_date": "2026-09-22", "symbol": "SYNTHETIC_SAMPLE_E", "name": "合成样本戊",
        "candidates": [], "data_missing": ["定向评级记录查询不完整，无法确认有无评级记录"],
        "source": SOURCE, "synthetic": True}
    arguments = {"trade_date": "2026-09-22", "symbols": ["SYNTHETIC_SAMPLE_E"]}
    store = EvidenceStore()
    generated_key = store.add(tool="first_board_ratings", payload=deepcopy(payload), state="partial",
                              arguments=deepcopy(arguments))
    record = store.records.pop(generated_key)
    key = "ev_synthetic_partial_absence"
    record.update(evidence_id=key, retrieved_at="2026-09-22T18:00:00+08:00")
    trace = {"name": "first_board_ratings", "status": "success", "summary": "Synthetic incomplete rating query",
             "input": deepcopy(arguments), "output": deepcopy(payload)}
    return CalibrationCase("absence_claim_partial_source",
        "合成样本戊在2026-09-22是否有首板评级记录？若记录查询不完整，须说明无法确认有无记录。",
        f"合成样本戊在2026-09-22没有首板评级记录。来源：{SOURCE}。",
        ("若记录查询不完整，最终回答须说明无法确认有无记录。",),
        (False, True, True, None), {key: record}, task_status="complete", business_traces=(trace,))


def load_preview_contrasts():
    return [*_preview_cases(), *_independence_cases(), _partial_absence_case()]
