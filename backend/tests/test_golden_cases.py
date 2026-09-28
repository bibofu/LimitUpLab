"""Dataset contract tests; these do not measure a model's research accuracy."""

from collections import Counter, defaultdict

from evals.golden.cases import load_cases
from evals.golden.contracts import Case


def test_case_counts_ids_and_smoke_distribution():
    cases = load_cases()
    assert len(cases) == 60
    assert len({case.id for case in cases}) == 60
    assert Counter(case.category for case in cases) == {
        "single": 30, "multi": 20, "robustness": 10,
    }
    smoke = [case for case in cases if case.smoke]
    assert len(smoke) == 12
    assert Counter(case.category for case in smoke) == {
        "single": 8, "multi": 3, "robustness": 1,
    }
    assert all(case.notes and case.tags and case.source for case in cases)


def test_families_never_cross_dataset_split():
    splits = defaultdict(set)
    for case in load_cases():
        splits[case.family].add(case.split)
    assert all(len(values) == 1 for values in splits.values())
    assert {case.split for case in load_cases()} == {"development", "holdout"}
    assert sum(case.split == "holdout" for case in load_cases()) >= 10


def test_oracles_have_string_cells_and_valid_table_dimensions():
    for case in load_cases():
        Case.model_validate(case.model_dump())
        assert len(case.turns) >= (2 if case.category == "multi" else 1)
        for turn in case.turns:
            expected = turn.expect
            assert expected.rows is not None or expected.semantic_checks
            assert expected.require_evidence or not expected.evidence_dates
            if expected.rows is not None:
                assert expected.columns
                assert len(set(expected.columns)) == len(expected.columns)
                assert all(len(row) == len(expected.columns) for row in expected.rows)
                assert all(type(cell) is str for row in expected.rows for cell in row)
                assert len({tuple(row) for row in expected.rows}) == len(expected.rows)
            if expected.table_only:
                assert expected.rows is not None


def test_manual_oracles_distinguish_scope_state_rank_and_dates():
    cases = {case.id: case for case in load_cases()}
    assert cases["s01_main_first"].turns[0].expect.rows == [
        ["600101", "华岳科技"], ["000202", "北辰制造"], ["000910", "中原农业"],
    ]
    assert cases["s08_failed_board"].turns[0].expect.rows == [
        ["000707", "远景材料"], ["300808", "晨光医疗"],
    ]
    assert cases["s14_low_turnover"].turns[0].expect.rows == [
        ["000910", "中原农业"], ["600101", "华岳科技"],
    ]
    assert cases["s14_low_turnover"].turns[0].expect.ordered
    assert cases["s22_cross_date_intersection"].turns[0].expect.evidence_dates == [
        "2026-09-21", "2026-09-22",
    ]
    assert cases["s01_main_first"].turns[0].page_date == "2026-09-21"
    assert cases["s01_main_first"].turns[0].expect.evidence_dates == ["2026-09-22"]
    assert cases["m08_clarify_date_pair"].family == "cross_date_sets"
    assert cases["m08_clarify_date_pair"].split == "holdout"


def test_multi_cases_exercise_summary_trigger_and_session_boundaries():
    cases = load_cases()
    long_cases = [case for case in cases if case.seed_messages]
    assert len(long_cases) >= 2
    # Production begins summarizing after 8 older + 8 recent successful messages.
    assert all(len(case.seed_messages) >= 24 for case in long_cases)
    assert all(case.seed_messages[0].role == "user" for case in long_cases)
    assert any(len({turn.session for turn in case.turns}) > 1 for case in cases)
    assert any(turn.expect.statuses == ["clarify"] for case in cases
               if case.category == "multi" for turn in case.turns)


def test_robustness_variants_do_not_accept_false_completion():
    cases = {case.id: case for case in load_cases()}
    assert {case.variant for case in cases.values()} == {
        "normal", "empty", "partial", "error", "truncated", "injection", "stale",
    }
    for id_ in ("r02_partial_source", "r03_source_error", "r04_stale_source",
                "r10_source_truncation"):
        assert "complete" not in cases[id_].turns[0].expect.statuses
        assert "empty" not in cases[id_].turns[0].expect.statuses
    assert cases["r01_valid_empty"].turns[0].expect.statuses == ["empty"]
    assert cases["r06_user_override"].turns[0].expect.max_tool_calls == 0


def test_loading_returns_independent_mutable_models():
    first = load_cases()
    first[0].turns[0].expect.rows[0][0] = "changed"
    first[0].source.append("changed")
    second = load_cases()
    assert second[0].turns[0].expect.rows[0][0] == "600101"
    assert "changed" not in second[0].source


def test_truncated_oracle_obeys_explicit_secondary_sort_before_cutoff():
    from evals.golden.world import synthetic_events

    case = next(case for case in load_cases() if case.id == "r10_source_truncation")
    events = [event for event in synthetic_events()
              if event.trade_date.isoformat() == "2026-09-22" and event.closed_limit]
    # Independent reference order: the two-board 600909 precedes 688404.
    ordered = sorted(events, key=lambda event: (-event.board_height, event.symbol))
    assert case.turns[0].expect.rows == [[event.symbol, event.name] for event in ordered[:2]]
