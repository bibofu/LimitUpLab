"""Question planning checks never execute agents or certify actual coverage."""

import json
from pathlib import Path

import pytest

from app.agent_eval.blueprints import coverage, load_blueprints, save_blueprint_report
from app.agent_eval.loader import load_case


BOOK = Path(__file__).resolve().parents[1] / "evals/blueprints/core40_live48.json"


def modified(tmp_path, modify):
    data = json.loads(BOOK.read_text(encoding="utf-8"))
    modify(data)
    path = tmp_path / "book.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_counts_and_profiles_do_not_imply_runnable_or_extended_coverage():
    book = load_blueprints(BOOK)
    report = coverage(book)
    assert report["counts"] == {"offline": 40, "live_historical": 30, "live_current": 12, "live_external_canary": 6}
    assert not report["runnable"] and not report["release_eligible"]
    assert report["primary_normal_offline_gaps"]["v1_close_review"] == []
    assert len(report["primary_normal_offline_gaps"]["extended"]) == 24
    deferred = next(row for row in report["tools"] if row["tool"] == "web_search")
    assert not deferred["profiles"]["v1_close_review"]["enabled"]
    assert deferred["profiles"]["extended"]["primary_normal_offline"] == ["OFF-026"]
    # Adding/removing a tool must surface a planning obligation, not silently inherit a count.
    assert len(report["tools"]) == 26
    assert all(row["profiles"]["extended"]["asset_readiness"] == "not_assessed" for row in report["tools"])


@pytest.mark.parametrize("change", ["duplicate_id", "duplicate_question", "unknown_tool", "profile", "argument", "bounds", "binding", "historical_current"])
def test_invalid_blueprints_fail_loudly(tmp_path, change):
    def modify(data):
        first = data["cases"][0]
        if change == "duplicate_id":
            data["cases"][1]["id"] = first["id"]
        elif change == "duplicate_question":
            data["cases"][1]["question"] = first["question"]
        elif change == "unknown_tool":
            first["tools"].append("invented_tool")
        elif change == "profile":
            first["tools"].append("web_search")
        elif change == "argument":
            first["arguments"]["market_summary"]["days"] = 5
        elif change == "bounds":
            data["cases"][1]["arguments"]["market_index_trend"]["days"] = 999
        elif change == "binding":
            first["question"] += "{undeclared}"
        else:
            first["mode"] = "live_historical"
    with pytest.raises(ValueError):
        load_blueprints(modified(tmp_path, modify))


def test_bound_parameter_errors_not_hidden_by_unresolved_symbol(tmp_path):
    def modify(data):
        case = next(c for c in data["cases"] if c["id"] == "OFF-014")
        case["arguments"]["post_limit_path"]["recent_limit_days"] = 999
    with pytest.raises(ValueError):
        load_blueprints(modified(tmp_path, modify))


def test_blueprints_cannot_be_loaded_as_executable_cases(tmp_path):
    data = json.loads(BOOK.read_text(encoding="utf-8"))["cases"][0]
    path = tmp_path / "not-a-case.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_case(path)


def test_report_export_is_exclusive_and_contains_all_questions(tmp_path):
    book = load_blueprints(BOOK)
    output = tmp_path / "report"
    summary = save_blueprint_report(book, output)
    assert summary["counts"]["offline"] == 40
    rendered = (output / "questions.md").read_text(encoding="utf-8")
    assert all(case.question in rendered for case in book.cases)
    assert (output / "coverage.json").is_file()
    with pytest.raises(FileExistsError):
        save_blueprint_report(book, output)
