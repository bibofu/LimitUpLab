"""Append-only scope diagnostics; labels are authored, not human calibration."""

from copy import deepcopy

from evals.golden.calibration import CalibrationCase, SOURCE, _evidence


# Independent copies freeze diagnostics even if future task requirements change.
RATING_SCOPE = "只回答该股票该日是否有首板评级记录，不展开其他股票名单、评级列表或额外统计。"
FILTER_ASSERTION = (
    "如回答额外声称已剔除ST或新股，须与实际工具入参、明确筛选语义和执行记录一致；"
    "明确未执行应判不符，筛选证据不足应保留未知，不能由数量正确或结果恰好无ST/新股推断已筛选。"
    "未作此类断言即可满足本条，不要求主动讨论ST或新股。"
)


def _observation(key, tool, arguments, payload, *, state="ok"):
    record = _evidence(payload)["ev_calibration"]
    record.update(evidence_id=key, tool=tool, arguments=deepcopy(arguments), result_state=state)
    record["rows"] = deepcopy(payload.get("items", [record["payload"]]))
    trace = {"name": tool, "status": "success", "summary": "Synthetic scope diagnostic",
             "input": deepcopy(arguments), "output": deepcopy(record["payload"])}
    return record, trace


def _rating_scope_cases():
    absent, query = _observation("ev_absent", "synthetic_rating_lookup",
        {"trade_date": "2026-09-22", "symbol": "688404"},
        {"trade_date": "2026-09-22", "symbol": "688404", "name": "青岚芯片", "count": 0, "items": []},
        state="empty")
    pool, pool_query = _observation("ev_pool", "synthetic_rating_pool",
        {"trade_date": "2026-09-22"},
        {"trade_date": "2026-09-22", "count": 2, "items": [
            {"symbol": "600101", "name": "华岳科技", "rating": "A"},
            {"symbol": "000202", "name": "北辰制造", "rating": "B"}]})
    short = "青岚芯片在2026-09-22没有首板评级记录。"
    return [CalibrationCase(key,
        "青岚芯片在2026-09-22的首板评级是什么？只回答是否有评级记录。", answer,
        ("明确青岚芯片没有该日首板评级记录。", RATING_SCOPE),
        (True, scope_passed, True, True, True),
        {"ev_absent": deepcopy(absent), "ev_pool": deepcopy(pool)}, task_status="empty",
        business_traces=(deepcopy(query), deepcopy(pool_query)))
        for key, answer, scope_passed in (
            ("rating_existence_only", short, True),
            ("rating_existence_with_extra_list", short + "另外，当日华岳科技评级A、北辰制造评级B。", False),
        )]


def _count_filter_cases():
    cases = []
    plain = f"2026-09-22沪深主板收盘涨停数量是5只。来源：{SOURCE}。"
    for suffix, applied, claim in (("applied", True, True), ("not_applied", False, True),
                                    ("unknown", None, True), ("no_claim", False, False)):
        arguments = {"trade_date": "2026-09-22", "market": "main_board", "closed_only": True}
        payload = {"trade_date": "2026-09-22", "market": "main_board", "count": 5}
        if applied is not None:
            arguments.update(exclude_st=applied, exclude_new=applied)
            payload["filter_audit"] = {"exclude_st_executed": applied, "exclude_new_executed": applied,
                "execution_history_complete": True, "other_filter_steps": []}
        record, trace = _observation("ev_count", "synthetic_count_query", arguments, payload)
        supported = applied if claim else True
        cases.append(CalibrationCase(f"memory_count_filters_{suffix}", "同日只看沪深主板，数量呢？",
            plain + ("本次查询已剔除ST和新股。" if claim else ""),
            ("沪深主板收盘涨停数量是5。", FILTER_ASSERTION),
            (True, supported, True, True, supported), {"ev_count": record}, business_traces=(trace,)))
    return cases


def load_scope_contrasts():
    return [*_rating_scope_cases(), *_count_filter_cases()]
