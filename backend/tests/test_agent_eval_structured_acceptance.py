from app.agent_eval.structured_acceptance import _canonical_members, _selection_args, count_samples, selection_samples


def test_count_calibration_covers_language_and_fact_counterexamples():
    samples = count_samples("2026-09-11", 40)
    assert {sample[0] for sample in samples} == {"plain", "spaced", "approx", "wrong_count", "wrong_date", "multi"}
    assert any(claim[3] == "approximate" for _, _, claims in samples for claim in claims)
    assert any(len(claims) == 2 for _, _, claims in samples)
    assert any(claim[0] != "2026-09-11" for _, _, claims in samples for claim in claims)
    assert any(claim[2] != 40 for _, _, claims in samples for claim in claims if claim[1] == "limit_up_count")


def test_selection_calibration_preserves_order_duplicates_omissions_and_date():
    members = [{"symbol": "002161", "name": "远 望 谷"}, {"symbol": "600001", "name": "合成二"}]
    samples = {item[0]: item for item in selection_samples("2026-09-11", members)}
    assert set(samples) == {"plain", "table", "reverse", "omission", "duplicate", "wrong_date"}
    assert samples["reverse"][3] == list(reversed(_canonical_members(members)))
    assert len(samples["omission"][3]) == 1 and len(samples["duplicate"][3]) == 3
    assert samples["wrong_date"][2] != "2026-09-11"


def test_selection_route_arguments_are_narrow_but_keep_independent_filters():
    expected = {"trade_date": "2026-09-11", "row_selection": {"closed_limit": True,
        "market": "main_board", "board_height": 1, "min_break_count": 1,
        "order_by": "amount", "descending": True, "take": 10}}
    args = _selection_args(expected)
    assert args == {"trade_date": "2026-09-11", "limit": 100, "market": "main_board",
                    "board_height": 1, "event_status": "broken_intraday", "closed_only": True,
                    "sort_by": "amount", "sort_order": "desc"}
