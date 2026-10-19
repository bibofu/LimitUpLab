"""Ensure the selected database is ready before the Windows scheduled close job."""

from pathlib import Path
import subprocess
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Forward help without starting a service. Operational invocations first
    # ensure the explicitly managed project cluster, or check an external DB.
    if not any(value in {"-h", "--help"} for value in arguments):
        ready = subprocess.run(
            [sys.executable, str(BACKEND_ROOT / "scripts/local_postgresql.py"), "ensure"],
            cwd=BACKEND_ROOT, check=False,
        )
        if ready.returncode:
            return ready.returncode
    completed = subprocess.run(
        [sys.executable, str(BACKEND_ROOT / "scripts/run_daily_close_loop.py"), *arguments],
        cwd=BACKEND_ROOT, check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
