"""Re-score completed traces for status-only oracle changes; never call a model."""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.models import AgentChatResponse
from evals.golden.cases import load_cases
from evals.golden.contracts import Case, Check, SUITE_VERSION, verdict
from evals.golden.reporting import digest, save_report
from scripts.run_agent_golden import tree_fingerprint


def require(condition, message):
    if not condition:
        raise ValueError(message)


def without_statuses(definition):
    value = deepcopy(definition)
    for turn in value["turns"]:
        turn["expect"].pop("statuses")
    return value


def _contracts_ast(content):
    tree = ast.parse(content)
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SUITE_VERSION"
                                            for t in node.targets))]
    return ast.dump(tree, include_attributes=False)


def verify_evaluator(manifest, current_hash):
    """Prove unchanged fixtures/judge code before reusing saved judgements."""
    if manifest.get("evaluator_hash") == current_hash:
        return
    commit = manifest.get("git_commit", "")
    require(isinstance(commit, str) and len(commit) == 40
            and all(c in "0123456789abcdef" for c in commit), "Cannot verify source evaluator commit")
    command = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]
    try:
        names = subprocess.check_output(command + ["ls-tree", "-r", "--name-only", commit,
            "--", "backend/evals"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).splitlines()
        original = {name: subprocess.check_output(command + ["show", f"{commit}:{name}"],
                    cwd=ROOT, stderr=subprocess.DEVNULL) for name in names if name.endswith(".py")}
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("Source evaluator revision is unavailable") from error
    require(digest({name: hashlib.sha256(data).hexdigest() for name, data in original.items()})
            == manifest.get("evaluator_hash"), "Source evaluator hash does not match its commit")
    current = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in (BACKEND / "evals").rglob("*.py")}
    require(current.keys() == original.keys(), "Evaluator file inventory changed")
    for name, content in current.items():
        if name == "backend/evals/golden/cases.py":
            continue  # Every selected definition is compared below, including fixture variants.
        if name == "backend/evals/golden/contracts.py":
            require(_contracts_ast(content) == _contracts_ast(original[name]), "Non-version contracts changed")
        else:
            require(content == original[name], f"Fixture or evaluator code changed: {name}")


def rescore_report(source: Path, output: Path, *, cases: list[Case] | None = None) -> dict:
    source, output = Path(source).resolve(), Path(output).resolve()
    require(not output.exists(), "Output directory already exists; original reports cannot be overwritten")
    raw = source.read_bytes()
    original = json.loads(raw)
    require(original.get("mode") == "live-model-frozen-tools", "Expected a live-model frozen-tool report")
    report = deepcopy(original)
    manifest = report["manifest"]
    ids, trials = manifest["case_ids"], manifest["trials"]
    require(isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids)
            and len(ids) == len(set(ids)), "Invalid or duplicate planned case IDs")
    require(type(trials) is int and trials > 0, "Invalid trial count")
    selected = list(cases if cases is not None else load_cases())
    current = {case.id: case.model_dump(mode="json") for case in selected}
    require(len(current) == len(selected) and all(i in current for i in ids), "Unknown or duplicate current cases")
    evaluator_hash = tree_fingerprint(BACKEND / "evals")
    verify_evaluator(manifest, evaluator_hash)
    expected_keys = {(case_id, trial) for case_id in ids for trial in range(1, trials + 1)}
    seen, definitions = set(), {}
    for result in report["results"]:
        key = (result["case_id"], result["trial"])
        require(type(key[1]) is int and key in expected_keys and key not in seen, "Duplicate or unplanned trial")
        seen.add(key)
        before = result["definition"]
        require(Case.model_validate(before).model_dump(mode="json") == before, "Invalid stored definition")
        require(before["id"] == key[0], "Definition case ID mismatch")
        require(key[0] not in definitions or definitions[key[0]] == before, "Trial definitions disagree")
        definitions[key[0]] = deepcopy(before)
        after = current[key[0]]
        require(without_statuses(before) == without_statuses(after), "Only turn.expect.statuses may change")
        require(result.get("completed") is True and result.get("planned_turns") == len(before["turns"])
                and len(result["turns"]) == len(before["turns"]), "Incomplete trial or turn list")
        require(all(result.get(field) == before[field] for field in ("category", "family", "split", "tags")),
                "Trial classification differs from its definition")
        source_verdict = verdict([Check(name="turn", passed=(True if t["verdict"] == "pass"
            else False if t["verdict"] == "fail" else None)) for t in result["turns"]])
        require(result["verdict"] == "harness_error" or result["verdict"] == source_verdict,
                "Stored trial verdict disagrees with its turns")
        require(not result.get("unsupported_tools") or result["verdict"] == "harness_error",
                "Fixture gaps must retain harness_error priority")
        for index, (turn, old_turn, new_turn) in enumerate(zip(result["turns"], before["turns"], after["turns"])):
            require(type(turn["index"]) is int and turn["index"] == index
                    and turn["user"] == old_turn["user"] and turn["session"] == old_turn["session"],
                    "Stored turn user, session or index differs from its definition")
            require(turn["expected"] == old_turn["expect"], "Stored turn oracle differs from its definition")
            response = AgentChatResponse.model_validate(turn["response"])
            require(response.session_id == f"golden-{key[0]}-t{key[1]}-{old_turn['session']}",
                    "Response session differs from its planned turn")
            checks = [Check.model_validate(check) for check in turn["checks"]]
            require(turn["verdict"] == verdict(checks), "Stored turn verdict disagrees with its checks")
            statuses = [check for check in turn["checks"] if check["name"] == "status"]
            require(len(statuses) == 1, "Each turn requires one status check")
            status = statuses[0]
            require(status["expected"] == old_turn["expect"]["statuses"]
                    and status["actual"] == response.task_status
                    and status["passed"] is (response.task_status in status["expected"]),
                    "Stored status check disagrees with its original oracle or response")
            status["expected"] = deepcopy(new_turn["expect"]["statuses"])
            status["passed"] = response.task_status in status["expected"]
            turn["expected"]["statuses"] = deepcopy(status["expected"])
            turn["verdict"] = verdict([Check.model_validate(check) for check in turn["checks"]])
        if result["verdict"] != "harness_error":
            result["verdict"] = verdict([Check(name="turn", passed=(True if t["verdict"] == "pass"
                else False if t["verdict"] == "fail" else None)) for t in result["turns"]])
        result["definition"] = deepcopy(after)
    require(seen == expected_keys, "Missing planned trials")
    require(digest([definitions[i] for i in ids]) == manifest["dataset_hash"], "Original dataset_hash mismatch")
    manifest.update(suite_version=SUITE_VERSION, dataset_hash=digest([current[i] for i in ids]),
                    evaluator_hash=evaluator_hash)
    report["rescore_provenance"] = {
        "source_path": str(source), "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_manifest": deepcopy(original["manifest"]), "rescored_at": datetime.now(timezone.utc).isoformat(),
        "added_model_calls": 0, "scope": "Only turn.expect.statuses and derived verdicts; no new Agent or judge execution",
    }
    require(source.read_bytes() == raw, "Source report changed while validating")
    output.mkdir(parents=True, exist_ok=False)
    save_report(report, output)
    markdown = output / "report.md"
    notice = ("# Offline status-only rescoring of recorded traces\n\n"
              "**No new Agent run or judge execution; added model calls: 0.** "
              "The live-model mode and generation hashes below describe the original run.\n\n"
              f"- Original report: `{source}`\n"
              f"- Original SHA-256: `{report['rescore_provenance']['source_sha256']}`\n"
              f"- Original generation commit: `{original['manifest'].get('git_commit')}`\n"
              f"- Rescored at: `{report['rescore_provenance']['rescored_at']}`\n\n---\n\n")
    markdown.write_text(notice + markdown.read_text(encoding="utf-8"), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Completed source report.json")
    parser.add_argument("--output", required=True, type=Path, help="New, nonexistent output directory")
    args = parser.parse_args(argv)
    try:
        report = rescore_report(args.input, args.output)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.error(str(error))
    print(json.dumps({"output": str(args.output.resolve()), "added_model_calls": 0,
                      "verdict_counts": report["summary"]["verdict_counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
