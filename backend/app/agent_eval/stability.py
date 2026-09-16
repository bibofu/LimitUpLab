"""Verify formal artifacts and aggregate independent formal runs into a stability panel."""

import json
from pathlib import Path
from statistics import mean

from app.agent_eval.core_batch import write_json
from app.agent_eval.full_answer_judge import JUDGE_SYSTEM as FULL_ANSWER_SYSTEM
from app.agent_eval.recorder import digest
from app.agent_eval.semantic_acceptance import JUDGE_SYSTEM as SEMANTIC_SYSTEM


CONTROL_TRACES = {
    "react_input_security", "react_decision", "react_policy", "react_observe",
    "react_compliance", "react_answer_check", "react_execution",
}


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _content_digest(path: Path):
    value = _read_json(path) if path.suffix == ".json" else path.read_text(encoding="utf-8")
    return digest(value)


def verify_formal_manifest(manifest_path: Path, *, verify_inputs: bool = True):
    manifest_path = manifest_path.resolve()
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != "formal-agent-run-manifest-v1":
        raise ValueError("unsupported formal manifest schema")
    root = manifest_path.parent
    checked_artifacts = []
    for name, expected in manifest.get("artifacts", {}).items():
        path = root / name
        if not path.is_file() or _content_digest(path) != expected:
            raise ValueError(f"formal artifact missing or changed: {name}")
        checked_artifacts.append(name)
    if "report.json" not in checked_artifacts:
        raise ValueError("formal manifest does not bind report.json")
    report = _read_json(root / "report.json")
    if (report.get("suite_id") != manifest.get("suite_id")
            or report.get("case_count") != manifest.get("case_count")
            or report.get("model") != manifest.get("model")):
        raise ValueError("formal report identity differs from manifest")
    prompts = manifest.get("judge_prompt_digests", {})
    if prompts != {"semantic": digest(SEMANTIC_SYSTEM), "full_answer": digest(FULL_ANSWER_SYSTEM)}:
        raise ValueError("formal judge prompt binding is stale")
    checked_inputs = []
    if verify_inputs:
        suite = manifest.get("inputs", {}).get("suite", {})
        suite_path = Path(suite.get("path", "")) / "suite.json"
        if not suite_path.is_file() or digest(_read_json(suite_path)) != suite.get("digest"):
            raise ValueError("formal suite input is missing or changed")
        checked_inputs.append("suite")
        run = manifest.get("inputs", {}).get("run", {})
        batch_path = Path(run.get("path", "")) / "batch-results.json"
        if not batch_path.is_file() or digest(_read_json(batch_path)) != run.get("batch_digest"):
            raise ValueError("formal run input is missing or changed")
        checked_inputs.append("run")
        for position, acceptance in enumerate(manifest.get("inputs", {}).get("acceptances", [])):
            path = Path(acceptance.get("path", ""))
            if not path.is_file() or digest(_read_json(path)) != acceptance.get("digest"):
                raise ValueError(f"formal acceptance input is missing or changed: {position}")
        checked_inputs.append("acceptances")
    return {
        "schema_version": "formal-manifest-verification-v1",
        "valid": True,
        "manifest": str(manifest_path),
        "artifacts": sorted(checked_artifacts),
        "inputs": checked_inputs,
    }


def _route_signature(response):
    route = []
    for trace in response.get("tool_results", []):
        name = trace.get("name")
        if name and name not in CONTROL_TRACES:
            route.append(name)
    return route


def _numeric_summary(values):
    usable = [value for value in values if value is not None]
    return None if not usable else {"min": min(usable), "mean": mean(usable), "max": max(usable)}


def build_stability_panel(formal_dirs: list[Path], destination: Path, *, required_runs: int = 3):
    if destination.exists():
        raise FileExistsError(destination)
    if len(formal_dirs) < required_runs:
        raise ValueError(f"stability panel requires at least {required_runs} formal runs")
    formal_dirs = [path.resolve() for path in formal_dirs]
    if len(set(formal_dirs)) != len(formal_dirs):
        raise ValueError("stability panel requires distinct formal directories")
    manifests, reports = [], []
    for folder in formal_dirs:
        verify_formal_manifest(folder / "manifest.json")
        manifests.append(_read_json(folder / "manifest.json"))
        reports.append(_read_json(folder / "report.json"))
    identity = {(item["suite_id"], item["model"], item["case_count"],
                 item["inputs"]["suite"]["digest"], tuple(item["runtime_versions"]))
                for item in manifests}
    if len(identity) != 1:
        raise ValueError("formal runs do not share suite, model, case count and runtime")
    case_orders = [[item["case_id"] for item in report["cases"]] for report in reports]
    if any(order != case_orders[0] for order in case_orders[1:]):
        raise ValueError("formal reports do not contain the same ordered cases")
    run_roots = [Path(report["run_root"]) for report in reports]
    if len(set(run_roots)) != len(run_roots):
        raise ValueError("stability panel requires distinct run roots")
    identities, cases = set(), []
    for case_id in case_orders[0]:
        observations, routes = [], []
        for run_index, (report, run_root) in enumerate(zip(reports, run_roots), start=1):
            item = next(entry for entry in report["cases"] if entry["case_id"] == case_id)
            request = _read_json(run_root / case_id / "request.json")
            identity_pair = (request.get("session_id"), request.get("message_id"))
            if None in identity_pair or identity_pair in identities:
                raise ValueError("stability runs reuse or omit session/message identity")
            identities.add(identity_pair)
            response = _read_json(run_root / case_id / "response.json")
            route = _route_signature(response)
            routes.append(route)
            observations.append({
                "run": run_index,
                "core": item["core_contract_verdict"],
                "terminal": item["terminal_verdict"],
                "full_answer": item["full_answer_verdict"],
                "actual_terminal": item["actual_terminal"],
                "failure_cause": item.get("failure_cause"),
                "route": route,
                "agent_model_calls": item.get("agent_model_calls"),
                "agent_tokens": item.get("agent_tokens"),
                "elapsed_seconds": item.get("elapsed_seconds"),
            })
        stable = all(item["core"] == item["terminal"] == item["full_answer"] == "pass"
                     for item in observations)
        terminal_values = sorted({item["actual_terminal"] for item in observations})
        route_values = []
        for route in routes:
            if route not in route_values:
                route_values.append(route)
        cases.append({
            "case_id": case_id,
            "stable_pass": stable,
            "pass_counts": {
                "core": sum(item["core"] == "pass" for item in observations),
                "terminal": sum(item["terminal"] == "pass" for item in observations),
                "full_answer": sum(item["full_answer"] == "pass" for item in observations),
            },
            "terminal_values": terminal_values,
            "terminal_volatile": len(terminal_values) > 1,
            "route_variants": route_values,
            "route_volatile": len(route_values) > 1,
            "agent_model_calls": _numeric_summary([item["agent_model_calls"] for item in observations]),
            "agent_tokens": _numeric_summary([item["agent_tokens"] for item in observations]),
            "elapsed_seconds": _numeric_summary([item["elapsed_seconds"] for item in observations]),
            "runs": observations,
        })
    stable_count = sum(item["stable_pass"] for item in cases)
    destination.mkdir(parents=True)
    report = {
        "schema_version": "agent-stability-panel-v1",
        "suite_id": manifests[0]["suite_id"],
        "model": manifests[0]["model"],
        "runtime_versions": manifests[0]["runtime_versions"],
        "run_count": len(reports),
        "required_runs": required_runs,
        "case_count": len(cases),
        "stable_case_count": stable_count,
        "unstable_case_count": len(cases) - stable_count,
        "stable_pass_rate": stable_count / len(cases),
        "terminal_volatile_case_count": sum(item["terminal_volatile"] for item in cases),
        "route_volatile_case_count": sum(item["route_volatile"] for item in cases),
        "unique_request_identities": len(identities),
        "run_totals": [{
            "formal_dir": str(folder),
            "run_root": report_item["run_root"],
            "core_pass_rate": report_item["core_contract_pass_rate"],
            "terminal_accuracy": report_item["terminal_accuracy"],
            "full_answer_pass_rate": report_item["full_answer_pass_rate"],
            "infrastructure_success_rate": report_item["infrastructure_success_rate"],
            "agent_model_calls": report_item["agent_model_calls"],
            "agent_tokens": report_item["agent_tokens"],
            "judge_model_calls": report_item["judge_model_calls"],
            "judge_tokens": report_item["judge_tokens"],
            "latency_seconds": report_item["latency_seconds"],
        } for folder, report_item in zip(formal_dirs, reports)],
        "cases": cases,
        "stability_eligible": len(reports) >= required_runs and stable_count == len(cases),
        "release_eligible": False,
        "scope_note": "Local30 stability only; not the project-wide release gate.",
    }
    write_json(destination / "report.json", report)
    lines = ["# Local30 Stability Panel", "",
             f"独立运行：{len(reports)}；稳定通过：{stable_count}/{len(cases)}；"
             f"终态波动：{report['terminal_volatile_case_count']}；路线波动：{report['route_volatile_case_count']}。", "",
             "| Case | Stable | Core | Terminal | Full answer | Terminal variants | Route variants |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for item in cases:
        lines.append(f"| {item['case_id']} | {'pass' if item['stable_pass'] else 'fail'} | "
                     f"{item['pass_counts']['core']}/{len(reports)} | {item['pass_counts']['terminal']}/{len(reports)} | "
                     f"{item['pass_counts']['full_answer']}/{len(reports)} | {len(item['terminal_values'])} | "
                     f"{len(item['route_variants'])} |")
    (destination / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    index = {
        "schema_version": "agent-stability-artifact-index-v1",
        "suite_id": report["suite_id"],
        "suite_digest": manifests[0]["inputs"]["suite"]["digest"],
        "copy_policy": "Copy every indexed formal directory and run root, preserving each directory tree.",
        "formal_runs": [{
            "formal_dir": str(folder),
            "formal_manifest_digest": digest(manifest),
            "run_root": report_item["run_root"],
            "run_batch_digest": manifest["inputs"]["run"]["batch_digest"],
        } for folder, manifest, report_item in zip(formal_dirs, manifests, reports)],
        "required_source_inputs": manifests[0]["inputs"],
    }
    write_json(destination / "artifact-index.json", index)
    artifact_names = ["report.json", "README.md", "artifact-index.json"]
    panel_manifest = {
        "schema_version": "agent-stability-manifest-v1",
        "suite_id": report["suite_id"],
        "formal_manifest_digests": [digest(item) for item in manifests],
        "artifacts": {name: _content_digest(destination / name) for name in artifact_names},
    }
    write_json(destination / "manifest.json", panel_manifest)
    return report


def verify_stability_manifest(manifest_path: Path):
    manifest_path = manifest_path.resolve()
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != "agent-stability-manifest-v1":
        raise ValueError("unsupported stability manifest schema")
    for name, expected in manifest.get("artifacts", {}).items():
        path = manifest_path.parent / name
        if not path.is_file() or _content_digest(path) != expected:
            raise ValueError(f"stability artifact missing or changed: {name}")
    report = _read_json(manifest_path.parent / "report.json")
    if report.get("suite_id") != manifest.get("suite_id"):
        raise ValueError("stability report identity differs from manifest")
    return {"schema_version": "stability-manifest-verification-v1", "valid": True,
            "manifest": str(manifest_path), "artifacts": sorted(manifest["artifacts"])}
