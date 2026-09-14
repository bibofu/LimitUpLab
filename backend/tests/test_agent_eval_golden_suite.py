import json

import pytest

from app.agent_eval.golden_suite import assemble_golden_suite
from app.agent_eval.core_batch import write_json
from app.agent_eval.recorder import digest


def test_golden_suite_requires_existing_active_sources(tmp_path):
    with pytest.raises(FileNotFoundError):
        assemble_golden_suite([tmp_path / "missing"], [tmp_path / "missing-bundle"], tmp_path / "result")


def test_golden_suite_validates_and_deduplicates_baselines(tmp_path, monkeypatch):
    class FakeCase:
        status, case_id, case_version, mode = "active", "OFF-001", 1, "offline"
        def model_dump(self, **kwargs):
            return {"status": self.status, "case_id": self.case_id, "case_version": 1, "mode": self.mode}
    class FakeWorld:
        def model_dump(self, **kwargs):
            return {"world": "same"}
    case, world = FakeCase(), FakeWorld()
    monkeypatch.setattr("app.agent_eval.golden_suite.load_case", lambda path: case)
    monkeypatch.setattr("app.agent_eval.golden_suite.load_world", lambda path: world)
    source, expected = tmp_path / "source", tmp_path / "expected"
    (source / "OFF-001").mkdir(parents=True)
    expected.mkdir()
    write_json(source / "manifest.json", {"status": "active", "scope": "test", "cases": [{
        "case_id": "OFF-001", "case_version": 1, "mode": "offline", "scope": "test",
        "active_case_digest": digest(case.model_dump()), "baseline_digest": digest(world.model_dump())}]})
    write_json(expected / "suite.json", {"cases": [{"id": "OFF-001", "mode": "offline"}]})
    report = assemble_golden_suite([source], [expected], tmp_path / "result", required_counts=(1, 0))
    assert report["case_count"] == 1
    assert len(list((tmp_path / "result" / "baselines").glob("*.json"))) == 1
    saved = json.loads((tmp_path / "result" / "suite.json").read_text(encoding="utf-8"))
    assert saved["cases"][0]["baseline"].startswith("baselines/")
