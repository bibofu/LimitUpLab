"""Synthetic evidence contrasts for the omissions found in the v1.3 run.

These evaluator-authored labels are diagnostics, not independently human-signed
gold labels. Fixture provenance and expected decisions never enter judge input.
"""

from copy import deepcopy
import hashlib
import json

from evals.golden.calibration import CalibrationCase, SOURCE, _evidence


SYNTHETIC_CONTEXT = {"anchor_date": "2026-09-22", "page_default_date": None,
    "page_default_symbol": None, "available_local_dates": ["2026-09-18", "2026-09-21", "2026-09-22"]}
SYNTHETIC_SYSTEM_MESSAGE = "Synthetic calibration system context:\n" + json.dumps(
    SYNTHETIC_CONTEXT, ensure_ascii=False, sort_keys=True)
SYNTHETIC_RUNTIME_METADATA = ({"origin": "agent_system_message",
    "system_message_sha256": hashlib.sha256(SYNTHETIC_SYSTEM_MESSAGE.encode()).hexdigest(),
    "context": SYNTHETIC_CONTEXT},)

# Exact final body from pre-fix-v13-20260929/r04_stale_source, trial 1.
# The accompanying failure evidence below is synthetic, not a replayed live trace.
R04_ORIGINAL_FINAL = "本次研究尚未全部完成。\n\n部分证据或回答校验未完成，请缩小范围后重试。"


def _date_cases():
    user = "本地现在有哪些日期可以查？"
    requirement = ("最终列出的本地可查询日期须与Agent实际获得的运行元数据一致。",)
    answer = "本地可查询日期为2026-09-18、2026-09-21、2026-09-22。"
    return [
        CalibrationCase("runtime_dates_supported", user, answer, requirement,
            (True, True, True, True), {}, runtime_metadata=deepcopy(SYNTHETIC_RUNTIME_METADATA)),
        CalibrationCase("runtime_dates_fabricated", user, answer.replace("2026-09-18", "2026-09-20"), requirement,
            (False, True, True, False), {}, runtime_metadata=deepcopy(SYNTHETIC_RUNTIME_METADATA)),
        CalibrationCase("runtime_dates_unknown", user, answer, requirement,
            (None, True, True, None), {}),
    ]


def _lineage_cases():
    cases = []
    for suffix, upstreams, source_passed in (
        ("independent", ("synthetic-origin-A", "synthetic-origin-B"), True),
        ("shared", ("synthetic-origin-A", "synthetic-origin-A"), False),
        ("unknown", (None, None), None),
    ):
        evidence, traces = {}, []
        for index, upstream in enumerate(upstreams):
            tool, source = f"synthetic_pool_{index}", f"synthetic-feed-{index}"
            payload = {"trade_date": "2026-09-22", "count": 56, "source": source, "synthetic": True}
            if upstream is not None:
                payload["lineage"] = {"ultimate_upstream": upstream,
                    "acquisition": "separate_original_collection", "lineage_complete": True}
            record = _evidence(payload)["ev_calibration"]
            # Each synthetic feed ID identifies an entry point; lineage describes its origin.
            record.update(evidence_id=f"ev_lineage_{index}", tool=tool, sources=[source])
            record["payload"] = deepcopy(payload)
            record["rows"] = [deepcopy(payload)]
            evidence[record["evidence_id"]] = record
            traces.append({"name": tool, "status": "success", "summary": "Synthetic lineage observation",
                "input": {"trade_date": "2026-09-22"}, "output": deepcopy(payload)})
        cases.append(CalibrationCase(f"source_lineage_{suffix}", "核对两个工具各自的涨停数量和来源关系。",
            "2026-09-22两个工具均返回56只；来源为synthetic-feed-0和synthetic-feed-1，"
            "来自两个相互独立的上游。这些均为合成诊断数据。",
            ("如实给出两个工具在2026-09-22各自返回的数量。",),
            (True, True, source_passed, True), evidence, business_traces=tuple(traces)))
    return cases


def _failure_cases():
    failed = {"name": "limit_up_events", "summary": "Synthetic stale-date query failure", "status": "error",
        "input": {"trade_date": "2026-09-22", "market": "main_board", "board_height": 1, "closed_only": True},
        "output": {"execution_status": "failed", "result_state": "error", "error_type": "ValueError",
            "error": "Tool execution failed; preserve other results and report missing evidence"}}
    requirement = ("不能用工具回退到9月21日的数据充当9月22日结果，应说明日期不匹配或查询失败。",)
    explicit = "2026-09-22主板首板收盘封板名单查询失败，未取得可核验名单；不能把查询失败视为当日没有符合条件的股票。"
    cases = []
    for suffix, answer, passed in (
        ("explicit_final", explicit, True),
        ("generic_final", "本次研究没有全部完成，请稍后重试。", False),
        ("r04_original_final", R04_ORIGINAL_FINAL, False),
    ):
        cases.append(CalibrationCase(f"query_failure_{suffix}", "查询2026-09-22主板首板收盘封板股票。",
            answer, requirement, (passed, True, True, True), {}, drafts=(explicit,),
            task_status="error", business_traces=(deepcopy(failed),),
            provenance="synthetic-evidence; answer copied from v1.3 r04 trial 1" if suffix == "r04_original_final"
                       else "synthetic-diagnostic"))
    return cases


def _filter_cases():
    rows = [{"symbol": f"SYNTHETIC_{index}", "name": name}
        for index, name in enumerate(("合成甲", "合成乙", "合成丙", "合成丁", "合成戊"))]
    answer = f"2026-09-22名单共5只：合成甲、合成乙、合成丙、合成丁、合成戊。本次查询已剔除ST与新股。来源：{SOURCE}。"
    cases = []
    for suffix, applied in (("applied", True), ("not_applied", False), ("unknown", None)):
        args = {"trade_date": "2026-09-22", "market": "main_board", "board_height": 1, "closed_only": True}
        payload = {"trade_date": "2026-09-22", "count": 5, "rows": deepcopy(rows), "source": SOURCE, "synthetic": True}
        if applied is not None:
            args.update(exclude_st=applied, exclude_new=applied)
            payload["filter_audit"] = {"exclude_st_executed": applied, "exclude_new_executed": applied,
                "execution_history_complete": True, "other_filter_steps": []}
        trace = {"name": "synthetic_list_query", "status": "success", "summary": "Synthetic filter execution",
            "input": args, "output": deepcopy(payload)}
        cases.append(CalibrationCase(f"additional_filters_{suffix}", "列出2026-09-22主板首板收盘封板股票。",
            answer, ("最终交付日期、5只股票名称与数量，名单须与返回结果一致。",),
            (True, True, True, applied), _evidence(payload), business_traces=(trace,)))
    return cases


def load_additional_contrasts():
    return [*_date_cases(), *_lineage_cases(), *_failure_cases(), *_filter_cases()]
