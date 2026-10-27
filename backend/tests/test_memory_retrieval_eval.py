"""Offline memory retrieval evaluation dataset and scoring contracts."""

from scripts.evaluate_memory_retrieval import (
    SnapshotRepository,
    aggregate,
    build_pool_items,
    evaluate_case,
    load_cases,
)


def test_dataset_references_existing_pool_memories():
    data = load_cases()
    case_ids = [case["id"] for case in data["cases"]]
    assert len(case_ids) == len(set(case_ids))
    for pool_name, records in data["pools"].items():
        memory_ids = [record["memory_id"] for record in records]
        assert len(memory_ids) == len(set(memory_ids)), pool_name
        known = set(memory_ids)
        for case in data["cases"]:
            if case["pool"] != pool_name:
                continue
            assert set(case.get("expect") or []).issubset(known), case["id"]
            assert set(case.get("forbid") or []).issubset(known), case["id"]


def test_lexical_evaluation_marks_expected_and_forbidden_hits():
    owner = "visitor_" + "a4" * 16
    items = build_pool_items([
        {
            "memory_id": "research_first_board",
            "memory_type": "research_context",
            "key": "research:first_board",
            "value": "首板接力研究关注晋级率",
            "topics": ["首板接力"],
        },
        {
            "memory_id": "research_risk",
            "memory_type": "research_context",
            "key": "research:risk",
            "value": "风险事件关注减持公告",
            "topics": ["风险事件"],
        },
    ], owner=owner, foreign_owner=owner)
    repository = SnapshotRepository(items, {})
    row = evaluate_case(
        repository, owner,
        {
            "id": "lexical",
            "query": "首板接力",
            "expect": ["research_first_board"],
            "forbid": ["research_risk"],
        },
        None,
        use_vectors=False,
    )
    assert row["exact"] is True
    assert row["expected_hits"] == ["research_first_board"]
    assert row["forbidden_hits"] == []
    assert row["vector_status"] == "disabled"


def test_aggregate_counts_false_injection_cases_and_denominator():
    rows = [
        {"expected_hits": ["a"], "expected_recall": 1.0, "first_rank": 1, "exact": True,
         "forbidden_hits": [], "has_forbid": True, "selected": ["a"], "tokens": 10},
        {"expected_hits": ["b"], "expected_recall": 1.0, "first_rank": 2, "exact": False,
         "forbidden_hits": ["x"], "has_forbid": True, "selected": ["x", "b"], "tokens": 20},
    ]
    metrics = aggregate(rows)
    assert metrics["exact_success"] == 1
    assert metrics["forbidden_checked_cases"] == 2
    assert metrics["forbidden_hit_cases"] == 1
    assert metrics["forbidden_hits"] == 1
    assert metrics["mrr"] == 0.75
