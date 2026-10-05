import hashlib
import json

from scripts.audit_golden_artifacts import audit_artifacts, main


def test_audit_preserves_files_and_distinguishes_missing_from_changed(tmp_path):
    original = b'{"score": "original"}'
    fingerprint = hashlib.sha256(original).hexdigest()
    (tmp_path / "valid.json").write_bytes(original)
    (tmp_path / "changed.json").write_bytes(b'{"score": "changed"}')
    expected = {name: fingerprint for name in ("valid.json", "changed.json", "missing.json")}
    result = audit_artifacts(tmp_path, expected)
    assert result["complete"] is False
    assert result["counts"] == {"verified": 1, "mismatch": 1, "missing": 1}
    assert (tmp_path / "valid.json").read_bytes() == original
    assert not (tmp_path / "missing.json").exists()
    assert "original" not in json.dumps(result)


def test_empty_inventory_is_not_verified(tmp_path):
    assert audit_artifacts(tmp_path, {})["complete"] is False


def test_missing_published_reports_exit_nonzero_without_recreating_them(tmp_path, capsys):
    assert main(["--root", str(tmp_path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["counts"] == {"missing": 4}
    assert list(tmp_path.iterdir()) == []
