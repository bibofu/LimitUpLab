"""Protect research timing, missingness and comparison semantics."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "research_limit_up_paths.py"
spec = importlib.util.spec_from_file_location("path_research", SCRIPT)
research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(research)


def bar(close=10, **kw):
    return {"open": close, "close": close, "low": close - .2, "high": close + .2,
            "volume": 100, "source": "test", **kw}


def test_first_and_second_board_require_previous_event_consistency():
    assert research.classify({"board_height": 1}, [bar()]) == ["first_board"]
    assert research.classify({"board_height": 2}, [bar()]) == []
    prev = {"board_height": 1, "closed_limit": 1}
    assert research.classify({"board_height": 2}, [bar()], prev) == ["second_board"]
    assert research.classify({"board_height": 1}, [bar()], prev) == []


def test_stabilization_requires_two_observed_post_anchor_days():
    anchor = {"board_height": 1}
    window = [bar(10), bar(9.4), bar(9.5, low=9.2, high=9.6)]
    assert "mild_stable" in research.classify(anchor, window)
    assert "mild_stable" not in research.classify(anchor, window[:2])


def test_volume_comparison_does_not_cross_source_labels():
    anchor = {"board_height": 1}
    window = [bar(10, volume=200), bar(10), bar(10)]
    assert "consolidation" in research.classify(anchor, window)
    window[-1]["source"] = "other"
    assert "consolidation" not in research.classify(anchor, window)


def test_missing_next_day_cannot_be_replaced_by_next_available_bar():
    dates = [str(x) for x in range(7)]
    bars = {("s", d): bar() for d in dates if d != "1"}
    row = {"symbol": "s", "signal_date": "0"}
    assert research.attach_outcome(row, bars, dates, {})["outcome_status"] == "missing_bar"
    bars[("s", "1")] = bar(10, open=9.9)
    result = research.attach_outcome(row, bars, dates, {})
    assert result["outcome_start"] == "1" and result["outcome_end"] == "5"
    assert abs(result["r5"] - (10 / 9.9 - 1) * 100) < 1e-9


def test_immature_is_distinct_from_missing_and_discontinuity():
    row = {"symbol": "s", "signal_date": "0"}
    assert research.attach_outcome(row, {}, ["0", "1"], {})["outcome_status"] == "immature"
    assert research.price_break([bar(10), bar(5)])
    assert not research.price_break([bar(10), bar(10.9)])


def test_dedup_keeps_first_even_if_its_outcome_is_missing():
    common = {"symbol": "s", "anchor_date": "0", "tags": ["mild_raw"]}
    rows = [{**common, "signal_date": "1", "outcome_status": "missing_bar"},
            {**common, "signal_date": "2", "outcome_status": "complete"}]
    assert research.first_triggers(rows)[0]["signal_date"] == "1"
    assert len(research.first_triggers(rows)) == 1
    assert len(research.nonoverlap(rows, [str(x) for x in range(8)])) == 1


def test_baseline_matches_exact_date_age_and_excludes_self():
    def row(symbol, age=2, date="1", r5=3):
        return {"symbol": symbol, "age": age, "signal_date": date,
                "r5": r5, "outcome_status": "complete"}
    signal = row("s", r5=10)
    pool = [signal, row("a", r5=4), row("b", age=1, r5=100), row("c", date="2", r5=100)]
    research.match_baselines([signal], pool)
    assert signal["baseline_n"] == 1
    assert signal["excess5"] == 6


def test_read_only_analysis_has_no_future_outcome_input_to_classification():
    # A future change alters labels but cannot alter a separately fixed signal.
    anchor = {"board_height": 1}
    observed = [bar(10), bar(9.4), bar(9.5, low=9.2, high=9.6)]
    tags = research.classify(anchor, observed)
    dates = [str(i) for i in range(8)]
    bars = {("s", str(i)): b for i, b in enumerate(observed + [bar(9.5)] * 5)}
    row = {"symbol": "s", "signal_date": "2"}
    before = research.attach_outcome(row, bars, dates, {})
    bars[("s", "7")] = bar(10)
    after = research.attach_outcome(row, bars, dates, {})
    assert before["r5"] != after["r5"]
    assert research.classify(anchor, observed) == tags
