"""Run the same offline acceptance gates locally and in CI.

Uses the invoking Python and installed frontend dependencies. Each invocation
gets a fresh database and test workspace under ignored output/validation.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Check:
    name: str
    command: list[str]
    cwd: Path


# Construct the backend test/evaluation and frontend test/build commands for the selected
# acceptance scope.
def build_checks(scope: str, output: Path) -> list[Check]:
    checks = []
    if scope in {"all", "backend"}:
        checks.append(Check("pytest", [
            sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
            "--basetemp", str(output / "pytest-tmp"),
            "--junitxml", str(output / "pytest.xml"),
        ], ROOT / "backend"))
        for suite in ("core", "product"):
            checks.append(Check(f"eval-{suite}", [
                sys.executable, "scripts/run_agent_eval.py", "--suite", suite,
                "--mode", "offline", "--summary-only",
                "--failure-output", str(output / f"eval-{suite}-failures.json"),
            ], ROOT / "backend"))
    if scope in {"all", "frontend"}:
        # Calling npm through Node avoids shell quoting and .cmd execution on Windows.
        node = shutil.which("node")
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not node or not npm:
            raise RuntimeError("Node.js 24 and npm must be installed and on PATH.")
        npm_path = Path(npm).resolve()
        npm_cli = (
            npm_path.parent / "node_modules/npm/bin/npm-cli.js"
            if os.name == "nt" else npm_path
        )
        if not npm_cli.is_file():
            raise RuntimeError(f"Cannot locate the npm CLI: {npm_cli}")
        for name in ("test", "build"):
            checks.append(Check(f"frontend-{name}", [node, str(npm_cli), "run", name], ROOT / "frontend"))
    return checks


# Run each acceptance gate with its own log and timeout, then save an honest combined result.
def run_checks(checks: list[Check], output: Path, env: dict[str, str], timeout: int) -> dict:
    results = []
    for check in checks:
        started = perf_counter()
        log_path = output / f"{check.name}.log"
        print(f"[check] {check.name}", flush=True)
        error = None
        with log_path.open("w", encoding="utf-8") as log:
            try:
                completed = subprocess.run(
                    check.command, cwd=check.cwd, env=env, stdout=log,
                    stderr=subprocess.STDOUT, timeout=timeout, check=False,
                )
                code = completed.returncode
            except (OSError, subprocess.TimeoutExpired) as exc:
                code, error = 1, str(exc)
                log.write(f"\n{error}\n")
        result = {
            "name": check.name, "passed": code == 0, "exit_code": code,
            "duration_seconds": round(perf_counter() - started, 3),
            "log": log_path.name, "error": error,
        }
        results.append(result)
        print(f"[{'PASS' if result['passed'] else 'FAIL'}] {check.name} ({result['duration_seconds']}s)", flush=True)
        if not result["passed"]:
            print("\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]), flush=True)
    report = {
        "passed": bool(results) and all(item["passed"] for item in results),
        "python": platform.python_version(), "platform": platform.system(),
        "created_at": datetime.now(timezone.utc).isoformat(), "checks": results,
        "scope_note": "Offline regression only; not live-model accuracy or deployment acceptance.",
    }
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


# Create an isolated offline validation workspace and run the selected acceptance gates.
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("all", "backend", "frontend"), default="all")
    parser.add_argument("--timeout", type=int, default=1200, help="Seconds allowed per gate.")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output = ROOT / "output/validation" / run_id
    output.mkdir(parents=True)
    test_tmp = output / "test-tmp"
    test_tmp.mkdir()
    env = {**os.environ, "PYTHONUTF8": "1", "LIMITUPLAB_LLM_ENABLED": "false",
           "LIMITUPLAB_ENVIRONMENT": "development",
           "LIMITUPLAB_DATABASE_PATH": str(output / "validation.sqlite"),
           "LIMITUPLAB_TEST_TMP": str(test_tmp), "TMP": str(test_tmp), "TEMP": str(test_tmp)}
    print(f"Report directory: {output}", flush=True)
    try:
        checks = build_checks(args.scope, output)
    except RuntimeError as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        return 1
    report = run_checks(checks, output, env, args.timeout)
    print(f"Summary: {output / 'summary.json'}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
