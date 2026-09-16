import json

import pytest

from app.agent_eval.admission import promote_approved
from app.agent_eval.basic70 import DEFAULT_RECIPE, candidate_assets
from app.agent_eval.core_batch import write_json
from app.agent_eval.loader import load_case
from app.agent_eval.recorder import digest


def source_suite(tmp_path):
    items = json.loads(DEFAULT_RECIPE.read_text(encoding="utf-8"))["cases"][:3]
    entries = []
    for index, item in enumerate(items):
        case, world, _ = candidate_assets(item)
        if index == 0:
            case = case.model_copy(update={"status": "active"})
        write_json(tmp_path / (case.case_id + ".json"), case.model_dump(mode="json"))
        write_json(tmp_path / (case.case_id + "-world.json"), world.model_dump(mode="json"))
        entries.append({"id": case.case_id, "mode": case.mode, "status": case.status,
                        "case": case.case_id + ".json", "world": case.case_id + "-world.json",
                        "case_digest": digest(case.model_dump(mode="json")),
                        "world_digest": digest(world.model_dump(mode="json"))})
    write_json(tmp_path / "suite.json", {"cases": entries})
    return tmp_path / "suite.json"


def test_promote_only_approved_preserves_old_assets_and_pending(tmp_path):
    source = source_suite(tmp_path)
    before = source.read_bytes()
    dest = tmp_path / "golden"
    result = promote_approved(source, dest, ["OFF-B002"], approval_text="批准OFF-B002")
    assert result["case_count"] == 2 and result["new_active_golden"] == 1
    assert source.read_bytes() == before
    old = load_case(tmp_path / "OFF-B001.json")
    assert load_case(dest / "OFF-B001/case.json") == old
    assert load_case(tmp_path / "OFF-B002.json").status == "candidate"
    assert load_case(dest / "OFF-B002/case.json").status == "active"
    assert json.loads((dest / "pending.json").read_text())["cases"][0]["id"] == "OFF-B003"
    assert not result["answer_quality_approved"] and not result["release_eligible"]
    with pytest.raises(FileExistsError):
        promote_approved(source, dest, ["OFF-B002"], approval_text="批准")


@pytest.mark.parametrize("ids", [["OFF-B001"], ["missing"], ["OFF-B002", "OFF-B002"], []])
def test_bad_selection_rejected_before_writing(tmp_path, ids):
    source = source_suite(tmp_path)
    with pytest.raises(ValueError):
        promote_approved(source, tmp_path / "golden", ids, approval_text="批准")
    assert not (tmp_path / "golden").exists()


def test_stale_binding_rejected_before_writing(tmp_path):
    source = source_suite(tmp_path)
    case = tmp_path / "OFF-B002.json"
    data = json.loads(case.read_text(encoding="utf-8"))
    data["case_version"] += 1
    case.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        promote_approved(source, tmp_path / "golden", ["OFF-B002"], approval_text="批准")
    assert not (tmp_path / "golden").exists()
