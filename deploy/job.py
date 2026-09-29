"""Run production jobs with a container watchdog and the shared release lock."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import signal
import subprocess
import sys
import time
from uuid import uuid4

from release import MAINTENANCE, REPO, deployment_lock

TERMINATION_SIGNALS = (signal.SIGTERM, signal.SIGINT) + ((signal.SIGHUP,) if hasattr(signal, "SIGHUP") else ())


class JobTimeout(RuntimeError):
    """The job exceeded its total allowed runtime."""


def positive_seconds(value: str) -> int:
    seconds = int(value)
    if not 1 <= seconds <= 86400:
        raise argparse.ArgumentTypeError("timeout must be between 1 and 86400 seconds")
    return seconds


def trade_date(value: str) -> str:
    if len(value) != 8 or not value.isascii() or not value.isdigit():
        raise argparse.ArgumentTypeError("date must be YYYYMMDD")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must be a valid YYYYMMDD date") from error
    return value


def attempts(value: str) -> int:
    count = int(value)
    if not 1 <= count <= 10:
        raise argparse.ArgumentTypeError("max-attempts must be between 1 and 10")
    return count


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    jobs = parser.add_subparsers(dest="job", required=True)
    daily = jobs.add_parser("daily-update", allow_abbrev=False)
    daily.add_argument("--timeout-seconds", type=positive_seconds, default=3600)
    daily.add_argument("--date", type=trade_date)
    daily.add_argument("--max-attempts", type=attempts)
    backup = jobs.add_parser("backup", allow_abbrev=False)
    backup.add_argument("--timeout-seconds", type=positive_seconds, default=900)
    return parser.parse_args(argv)


def command_for(args: argparse.Namespace, name: str) -> list[str]:
    if args.job == "daily-update":
        command = ["docker", "compose", "--env-file", ".env.production", "--profile", "jobs",
                   "run", "--detach", "--no-deps", "--name", name, "daily-update"]
        # The host deployment lock provides cross-container exclusion. Keep this
        # inner lock ephemeral so a killed container leaves no six-hour stale lock.
        command += ["python", "scripts/run_daily_close_loop.py", "--trigger",
                    "manual" if args.date else "scheduled",
                    "--lock-path", "/tmp/daily_close_loop.lock"]
        if args.date:
            command += ["--date", args.date]
        if args.max_attempts is not None:
            command += ["--max-attempts", str(args.max_attempts)]
        return command
    return ["docker", "run", "--detach", "--name", name,
            "-v", "limituplab-data:/app/data", "-v", "/var/backups/limituplab:/backups",
            "limituplab-backend:local", "python", "scripts/backup_database.py",
            "--database", "/app/data/limituplab.sqlite", "--output-dir", "/backups",
            "--retain-count", "14"]


def run(command: list[str], *, timeout: float = 30, capture: bool = True):
    return subprocess.run(command, cwd=REPO, check=False, timeout=timeout, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def state_for(name: str, *, timeout: float = 30) -> dict | None:
    result = run(["docker", "inspect", "--format", "{{json .State}}", name], timeout=timeout)
    if result.returncode == 0:
        state = json.loads(result.stdout)
        if not isinstance(state, dict) or "Status" not in state:
            raise RuntimeError(f"Invalid container state: {name}")
        return state
    # An inspect error alone is not evidence that a container is gone: the daemon
    # may be unavailable. Confirm absence with a successful inventory request.
    listed = run(["docker", "container", "ls", "--all", "--format", "{{.Names}}"], timeout=timeout)
    if listed.returncode == 0 and name not in listed.stdout.splitlines():
        return None
    raise RuntimeError(f"Cannot verify container state: {name}")


def stopped(state: dict | None) -> bool:
    return state is None or (
        state.get("Status") in {"exited", "dead"}
        and state.get("Running") is False
        and not state.get("Restarting", False)
    )


def stop_and_confirm(name: str) -> None:
    # Always address the actual container, never just the compose client. Even
    # when stop/kill returns an error, inspect may prove that it already exited.
    for command, timeout in ((["docker", "stop", "--time", "20", name], 30),
                             (["docker", "kill", name], 15)):
        try:
            run(command, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"Container cleanup command failed: {name}: {error}", file=sys.stderr, flush=True)
        try:
            if stopped(state_for(name)):
                return
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError(f"Container stop could not be confirmed: {name}")


def require_recovery(name: str, reason: str) -> None:
    # Preserve any existing maintenance details. Other jobs and release.py check
    # this marker while holding the same lock before they touch production data.
    while True:
        try:
            with MAINTENANCE.open("a", encoding="utf-8") as marker:
                marker.write(f"\n{datetime.now().isoformat()} job={name}: {reason}; operator recovery required\n")
                marker.flush()
                os.fsync(marker.fileno())
            break
        except OSError as error:
            # If both Docker verification and the safety gate fail, releasing the
            # lock could let another writer overlap the unknown container. Keep
            # exclusion until the marker can be persisted or an operator recovers.
            print(f"CRITICAL: cannot persist maintenance for {name}: {error}; "
                  "retaining deployment lock; operator recovery required", file=sys.stderr, flush=True)
            time.sleep(30)
    print(f"Maintenance active: {name}: {reason}; operator recovery required",
          file=sys.stderr, flush=True)


def job_logs(name: str) -> bool:
    try:
        # Inherit cron's output stream and retain the entire report, not just its tail.
        result = run(["docker", "logs", name], timeout=30, capture=False)
        if result.returncode:
            print(f"Unable to read job logs: {name} (exit={result.returncode})", file=sys.stderr, flush=True)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"Unable to finish reading job logs: {name}: {error}", file=sys.stderr, flush=True)
        return False


def execute_job(args: argparse.Namespace) -> int:
    name = f"limituplab-{args.job}-{uuid4().hex}"
    deadline = time.monotonic() + args.timeout_seconds
    launch_uncertain = True
    exit_code = 1
    confirmed_stopped = False
    print(f"Starting {args.job}: container={name} timeout={args.timeout_seconds}s", flush=True)
    try:
        result = run(command_for(args, name), timeout=min(60, args.timeout_seconds))
        if result.returncode:
            raise RuntimeError(f"Container launch failed ({result.returncode}): {result.stderr.strip()}")
        launch_uncertain = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise JobTimeout(f"Job exceeded {args.timeout_seconds}s: {name}")
            state = state_for(name, timeout=min(30, remaining))
            if state is None:
                raise RuntimeError(f"Container disappeared before its exit code was read: {name}")
            if stopped(state):
                exit_code = int(state["ExitCode"])
                confirmed_stopped = True
                break
            time.sleep(min(2, max(0, deadline - time.monotonic())))
    except (JobTimeout, subprocess.TimeoutExpired) as error:
        exit_code = 124
        print(f"Job timed out: {name}: {error}", file=sys.stderr, flush=True)
    except (Exception, KeyboardInterrupt) as error:
        print(f"Job failed: {name}: {error}", file=sys.stderr, flush=True)
    finally:
        # Ignore repeat termination requests while verifying cleanup. SIGKILL
        # cannot be intercepted; operator recovery is needed after a host crash.
        previous = {}
        for signum in TERMINATION_SIGNALS:
            previous[signum] = signal.signal(signum, signal.SIG_IGN)
        try:
            if not confirmed_stopped:
                try:
                    stop_and_confirm(name)
                    confirmed_stopped = True
                except Exception as error:
                    require_recovery(name, str(error))
                    exit_code = 1
            if launch_uncertain:
                # A timed-out create request can still be in flight at the daemon.
                # Keep the gate closed even when the first inventory shows no container.
                require_recovery(name, "Container launch was not acknowledged; late creation cannot be excluded")
                exit_code = 1
            logs_read = job_logs(name)
            if confirmed_stopped and logs_read:
                try:
                    result = run(["docker", "rm", name], timeout=30)
                    if result.returncode:
                        print(f"Stopped container could not be removed: {name}", file=sys.stderr, flush=True)
                except (OSError, subprocess.TimeoutExpired) as error:
                    print(f"Stopped container could not be removed: {name}: {error}", file=sys.stderr, flush=True)
            elif confirmed_stopped:
                print(f"Stopped container retained for log recovery: {name}", file=sys.stderr, flush=True)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    print(f"Finished {args.job}: container={name} exit={exit_code}", flush=True)
    return exit_code


def interrupted(signum, _frame) -> None:
    raise InterruptedError(f"Job interrupted by signal {signum}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    previous = {signum: signal.signal(signum, interrupted) for signum in TERMINATION_SIGNALS}
    try:
        with deployment_lock(timeout=7200):
            if MAINTENANCE.exists():
                raise RuntimeError("Maintenance active; job blocked until operator recovery")
            return execute_job(args)
    except (Exception, KeyboardInterrupt) as error:
        print(f"Production job failed: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
