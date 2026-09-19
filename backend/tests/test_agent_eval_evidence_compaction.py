from copy import deepcopy

import pytest

from app.agent_eval.evidence_compaction import MARKER, compact_packet, restore
from app.agent_eval.recorder import canonical_json
from app.agent_eval.trace_review import review_trace
from test_agent_eval_trace_review import Judge
from test_agent_eval_checks import artifact, no_external_calls
from test_agent_eval_event_facts import bundle


def packet(rows):
    return {"answer": "unchanged", "evidence": [{"evidence_id": "e1", "arguments": {"limit": 100},
            "payload": {"items": rows, "data_missing": ["volume"], "source_truncated": False}}]}


def rich_rows():
    return [{"symbol": "000001", "trade_date": "2026-09-11", "amount": 123.45,
             "closed_limit": True, "missing_field": None, "name": "中文",
             "source_reference": "local", "nested_object": {"items": [1, 2]}} for _ in range(12)]


def test_roundtrip_preserves_all_rows_fields_types_and_duplicates():
    original = packet(rich_rows())
    before = deepcopy(original)
    encoded, report = compact_packet(original)
    assert report["applied"] and report["roundtrip_verified"]
    assert report["original_digest"] == report["restored_digest"]
    assert restore(encoded) == original == before
    assert canonical_json(restore(encoded)) == canonical_json(original)
    assert len(restore(encoded)["evidence"][0]["payload"]["items"]) == 12
    assert report["encoded_packet_chars"] + report["instruction_chars"] < report["original_packet_chars"]


@pytest.mark.parametrize("rows", [[], [{"x": 1}], [{"x": None}, {}], [True, None, 0, "0"],
                                 [{"x": 1}, {"y": 2}]])
def test_small_or_heterogeneous_inputs_stay_exact(rows):
    original = packet(rows)
    encoded, report = compact_packet(original)
    assert restore(encoded) == original
    assert not report["applied"]


def test_marker_collision_falls_back_without_interpreting_source_data():
    original = packet(rich_rows())
    original["evidence"][0]["payload"]["extra"] = {MARKER: "untrusted source"}
    encoded, report = compact_packet(original)
    assert encoded == original
    assert report == {"applied": False, "reason": "reserved_key_collision"}


def test_malformed_table_does_not_silently_drop_cells():
    with pytest.raises(ValueError):
        restore({MARKER: {"columns": ["a"], "values": [[1, 2]]}})


def test_nested_table_roundtrip():
    original = packet([{"nested_rows": rich_rows(), "empty": []} for _ in range(3)])
    encoded, report = compact_packet(original)
    assert report["applied"]
    assert restore(encoded) == original


def test_trace_preflight_compacts_by_default_without_model_calls(bundle):
    case, _, response, _ = bundle
    ordinary = review_trace(case, response, compact_evidence=False)
    compact = review_trace(case, response)
    assert ordinary["judge"]["compaction"]["reason"] == "disabled"
    assert compact["judge"]["calls"] == 0
    assert compact["deterministic"] == ordinary["deterministic"]
    assert compact["judge"]["input_chars"] <= ordinary["judge"]["input_chars"]
    assert compact["judge"]["original_input_chars"] == ordinary["judge"]["input_chars"]
    assert compact["judge"]["chars_saved"] >= 0


def test_default_compaction_can_fit_budget_without_retry_or_mutation(bundle):
    case, _, response, _ = bundle
    before = response.model_dump(mode="json")
    raw = review_trace(case, response, compact_evidence=False)["judge"]["input_chars"]
    encoded = review_trace(case, response)["judge"]["input_chars"]
    assert encoded < raw
    provider = Judge()
    result = review_trace(case, response, provider=provider, max_input_chars=encoded)
    assert result["judge"]["budget_decision"] == "within_budget"
    assert result["judge"]["status"] == "completed" and provider.calls == 1
    skipped = Judge()
    result = review_trace(case, response, provider=skipped, max_input_chars=encoded - 1)
    assert result["judge"]["excess_chars"] == 1 and skipped.calls == 0
    assert result["judge"]["status"] == "input_budget_exceeded"
    assert response.model_dump(mode="json") == before


def test_dry_review_reports_budget_excess_without_provider(bundle):
    case, _, response, _ = bundle
    result = review_trace(case, response, max_input_chars=1000)
    assert result["judge"]["status"] == "disabled"
    assert result["judge"]["budget_decision"] == "exceeded"
    assert result["judge"]["budget_unit"] == "characters_not_tokens"
    assert result["judge"]["calls"] == 0


def test_compacted_packet_reaches_judge_with_decoding_instructions(bundle):
    from app.agent_eval.evidence_compaction import INSTRUCTION
    from app.agent_eval.trace_review import JUDGE_SYSTEM
    from app.agent_eval.recorder import digest
    import json

    class InspectJudge(Judge):
        def generate_messages(self, messages, tools, **kwargs):
            assert messages[0].content == JUDGE_SYSTEM + "\n" + INSTRUCTION
            encoded = json.loads(messages[1].content)
            assert MARKER in messages[1].content
            recovered = restore(encoded)
            assert isinstance(recovered["evidence"][0]["payload"]["events"], list)
            return super().generate_messages(messages, tools, **kwargs)

    case, _, response, _ = bundle
    report = review_trace(case, response, provider=InspectJudge(), compact_evidence=True)
    assert report["judge"]["compaction"]["applied"]
    assert report["judge"]["status"] == "completed"
    assert report["judge"]["prompt_digest"] == digest(JUDGE_SYSTEM + "\n" + INSTRUCTION)
