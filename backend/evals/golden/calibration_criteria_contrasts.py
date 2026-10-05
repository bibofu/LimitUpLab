"""Synthetic contrasts for requirement scope and missing versus contrary evidence.

These author-defined labels are diagnostics, not human calibration. Only case
content reaches the judge; the expected decisions and provenance stay local.
"""

from copy import deepcopy

from evals.golden.calibration import CalibrationCase
from evals.golden.calibration_contrasts import _lineage_cases


FAILURE_REQUIREMENT = "最终回答应明确说明工具或数据源查询失败，不能把故障当作零只或业务空结果。"
ALTERNATIVE_REQUIREMENT = "应说明返回日期不匹配或查询失败，不能把9月21日结果当作9月22日结果。"
FAILURE_USER = "查询2026-09-22的收盘涨停股票。"


def _failed_trace():
    return {"name": "limit_up_events", "status": "error", "summary": "Synthetic query failure",
        "input": {"trade_date": "2026-09-22", "closed_only": True, "limit": 100},
        "output": {"execution_status": "failed", "result_state": "error",
            "error": "Tool execution failed; no cause detail is available"}}


def _failure_case(key, answer, *, requirement=FAILURE_REQUIREMENT, delivery=True, factual=True, trace=None):
    return CalibrationCase(key, FAILURE_USER, answer, (requirement,),
        (delivery, True, True, factual), {}, task_status="error",
        business_traces=(deepcopy(trace) if trace is not None else _failed_trace(),))


def _failure_delivery_cases():
    explicit = "2026-09-22收盘涨停名单查询失败，未取得名单；这不能视为当日为零只。"
    return [
        _failure_case("failure_without_tool_identifier", explicit),
        _failure_case("failure_equivalent_wording",
            "本次获取2026-09-22收盘涨停名单的请求未成功，未拿到可用结果，无法据此认定当日没有涨停股票。"),
        _failure_case("failure_cause_not_claimed",
            "2026-09-22收盘涨停名单查询失败，未取得名单。具体故障原因未提供，目前无法判断原因。"),
        _failure_case("failure_required_identifier_missing", explicit,
            requirement=FAILURE_REQUIREMENT + "同时写明执行失败的工具名称。", delivery=False),
        _failure_case("failure_or_date_only",
            "请求2026-09-22的名单，但返回的是2026-09-21数据，日期不匹配；未将旧日名单作为9月22日结果。",
            requirement=ALTERNATIVE_REQUIREMENT,
            trace={"name": "limit_up_events", "status": "success", "summary": "Synthetic stale result",
                "input": {"trade_date": "2026-09-22", "closed_only": True, "limit": 100},
                "output": {"requested_date": "2026-09-22", "data_as_of": "2026-09-21", "count": 8}}),
        _failure_case("failure_or_query_only", explicit, requirement=ALTERNATIVE_REQUIREMENT),
        _failure_case("failure_generic_without_explanation",
            "本次研究尚未全部完成，请稍后重试。", delivery=False),
    ]


def _failure_cause_cases():
    answer = "2026-09-22收盘涨停名单查询失败，未取得名单。故障原因是远端服务返回HTTP 503。"
    contradicted = _failed_trace()
    contradicted["output"].update(error="Local parameter validation failed before any network request",
        failure_stage="local_parameter_validation", network_request_sent=False, http_status=None)
    return [
        _failure_case("failure_specific_cause_unknown", answer, factual=None),
        _failure_case("failure_specific_cause_contradicted", answer, factual=False, trace=contradicted),
    ]


def _query_independence_cases():
    # Reuse the existing evidence contrasts so the only answer difference is
    # independence of query execution versus independence of upstream sources.
    prior = {case.id: case for case in _lineage_cases()}
    shared, unknown = prior["source_lineage_shared"], prior["source_lineage_unknown"]
    query_answer = ("分别独立调用synthetic_pool_0和synthetic_pool_1查询2026-09-22的涨停数量，"
        "两次查询各返回56只；来源标识分别为synthetic-feed-0和synthetic-feed-1。")
    source_answer = query_answer + "这两份结果来自相互独立的上游来源。"
    requirement = ("如实给出两个工具在2026-09-22各自返回的数量。",)
    return [CalibrationCase(key, "分别查询两个工具的涨停数量并说明来源关系。", answer,
        requirement, (True, True, source_passed, True), deepcopy(origin.evidence),
        business_traces=deepcopy(origin.business_traces))
        for key, answer, source_passed, origin in (
            ("independent_queries_shared_origin", query_answer, True, shared),
            ("independent_sources_shared_origin", source_answer, False, shared),
            ("independent_sources_unknown_origin", source_answer, None, unknown),
        )]


def load_criteria_contrasts():
    return [*_failure_delivery_cases(), *_failure_cause_cases(), *_query_independence_cases()]
