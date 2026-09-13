"""Supervise one disposable offline worker; retain partial output on timeout."""

import json
import os
from pathlib import Path
import subprocess
import sys


def run_offline(case: Path, world: Path, output_dir: Path, *, wall_seconds: int = 240) -> dict:
    if not 1 <= wall_seconds <= 900:
        raise ValueError("wall_seconds must be between 1 and 900")
    case, world, output_dir = case.resolve(), world.resolve(), output_dir.resolve()
    if not case.is_file() or not world.is_file():
        raise ValueError("case and world must exist")
    output_dir.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-m", "app.agent_eval.worker", "--case", str(case), "--world", str(world),
               "--output-dir", str(output_dir), "--wall-seconds", str(wall_seconds), "--allow-llm"]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (output_dir / "worker.log").open("x", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[2],
                                   stdout=log, stderr=log, creationflags=flags)
        try:
            code = process.wait(timeout=wall_seconds + 15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            code = None
        except BaseException:
            process.kill()
            process.wait()
            raise
    report = {"worker_exit_code": code, "timed_out": code is None, "release_eligible": False}
    path = output_dir / "summary.json"
    if code is not None and path.is_file():
        report.update(json.loads(path.read_text(encoding="utf-8")))
    else:
        report.update(verdict="unscorable", primary_cause="evaluator_failure", total_tokens=None,
                      detail="worker incomplete; inspect retained per-call artifacts")
        failure = output_dir / "worker-error.json"
        if failure.is_file():
            report.update(json.loads(failure.read_text(encoding="utf-8")))
    with (output_dir / "supervisor.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return report
