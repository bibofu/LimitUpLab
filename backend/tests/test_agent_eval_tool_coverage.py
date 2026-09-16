import json
import sys

import pytest

from app.agent_eval.tool_coverage import DEFAULT_REGISTRY, preflight_tool_coverage, write_coverage_report


def test_real_registration_does_not_construct_business_registry(monkeypatch):
    from app.agents.tools import AgentToolRegistry

    def forbidden(*args, **kwargs):
        raise AssertionError("business registry must not be constructed")

    monkeypatch.setattr(AgentToolRegistry, "__init__", forbidden)
    result = preflight_tool_coverage()
    assert result["registration_valid"]
    assert result["registered_tool_count"] == result["declared_entry_count"] == 26
    assert result["declared_counts"] == {
        "local_priority": {"partial": 3, "planned": 13},
        "external_dependency": {"deferred": 10},
    }
    assert result["model_calls"] == result["business_tool_calls"] == 0
    assert result["data_readiness"] == "not_checked"
    for field in ("quality_verified", "coverage_refs_verified", "release_eligible"):
        assert result[field] is False
    profiles = {item["tool"]: item["enabled_profiles"] for item in result["tools"]}
    assert profiles["market_summary"] == ["v1_close_review", "extended"]
    assert profiles["web_search"] == ["extended"]


@pytest.mark.parametrize("change,code", [
    ("missing", "missing_tools"), ("unknown", "unknown_tools"),
    ("duplicate", "duplicate_tools"), ("status", "invalid_registry"),
    ("blank_notes", "invalid_registry"), ("missing_status", "invalid_registry"),
    ("missing_refs", "invalid_registry"), ("unexpected_refs", "invalid_registry"),
])
def test_invalid_registration(tmp_path, change, code):
    book = json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    entry = book["tools"][0]
    if change == "missing":
        book["tools"].pop()
    elif change == "unknown":
        entry["tool"] = "unknown_tool"
    elif change == "duplicate":
        book["tools"].append(dict(entry))
    elif change == "status":
        entry["status"] = "passed"
    elif change == "blank_notes":
        entry["notes"] = "   "
    elif change == "missing_status":
        del entry["status"]
    elif change == "missing_refs":
        entry["coverage_refs"] = []
    else:
        entry["status"] = "planned"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(book), encoding="utf-8")
    result = preflight_tool_coverage(path)
    assert not result["registration_valid"]
    assert code in {item["code"] for item in result["errors"]}


def test_cli_and_report_preservation(tmp_path, monkeypatch, capsys):
    from app.agent_eval.__main__ import main

    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["agent_eval", "preflight-tool-coverage", "--output", str(output)])
    assert main() == 0
    report = json.loads(capsys.readouterr().out)
    assert json.loads(output.read_text(encoding="utf-8")) == report
    with pytest.raises(FileExistsError):
        write_coverage_report(output, {})
    assert json.loads(output.read_text(encoding="utf-8")) == report
    monkeypatch.setattr(sys, "argv", ["agent_eval", "preflight-tool-coverage", "--registry", str(tmp_path / "absent.json")])
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "invalid_registry"


def test_malformed_json(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text("{", encoding="utf-8")
    assert not preflight_tool_coverage(path)["registration_valid"]
