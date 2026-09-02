"""Start local backend and frontend dev servers on Windows without PowerShell Start-Process."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import argparse
import json
import socket
import winreg
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
BACKEND_LOG = BACKEND / "dev_backend.log"
BACKEND_ERR = BACKEND / "dev_backend.err.log"
FRONTEND_LOG = FRONTEND / "dev_frontend.log"
FRONTEND_ERR = FRONTEND / "dev_frontend.err.log"
REFRESH_LOG = BACKEND / "recommendation_refresh.log"
REFRESH_ERR = BACKEND / "recommendation_refresh.err.log"
REFRESH_LOCK = BACKEND / "data" / "recommendation_refresh.lock"


def main() -> int:
    """Start both services and print a compact status report."""

    parser = argparse.ArgumentParser(description="Start LimitUpLab local dev services.")
    parser.add_argument(
        "--skip-data-check",
        action="store_true",
        help="Skip startup data freshness check and AKShare update attempt.",
    )
    args = parser.parse_args()

    backend_python = BACKEND / ".venv" / "Scripts" / "python.exe"
    if not backend_python.exists():
        print(f"Backend venv python not found: {backend_python}", file=sys.stderr)
        return 1

    env = build_env()
    if not args.skip_data_check:
        check_result = run_data_check(backend_python, env)
        if check_result != 0:
            print("Data freshness check failed; continuing startup. See backend/data/dev_check_report.json.")

    backend_status = probe("http://127.0.0.1:8001/health", timeout=1)
    frontend_status = probe("http://127.0.0.1:5173/", timeout=1)

    if not backend_status:
        spawn_detached(
            [
                str(backend_python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8001",
            ],
            cwd=BACKEND,
            stdout_path=BACKEND_LOG,
            stderr_path=BACKEND_ERR,
            env=env,
        )

    if not frontend_status:
        spawn_detached(
            [
                "cmd.exe",
                "/c",
                "npm.cmd",
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                "5173",
            ],
            cwd=FRONTEND,
            stdout_path=FRONTEND_LOG,
            stderr_path=FRONTEND_ERR,
            env=env,
        )

    if not refresh_worker_running():
        spawn_detached(
            [
                str(backend_python),
                "scripts/run_recommendation_refresh_loop.py",
            ],
            cwd=BACKEND,
            stdout_path=REFRESH_LOG,
            stderr_path=REFRESH_ERR,
            env=env,
        )

    backend_ok = wait_for("http://127.0.0.1:8001/health", seconds=12)
    frontend_ok = wait_for("http://127.0.0.1:5173/", seconds=12)

    print(f"Backend  http://127.0.0.1:8001  {'OK' if backend_ok else 'FAILED'}")
    print(f"Frontend http://127.0.0.1:5173  {'OK' if frontend_ok else 'FAILED'}")
    print(f"Backend log:  {BACKEND_LOG}")
    print(f"Frontend log: {FRONTEND_LOG}")
    print(f"Recommendation refresh log: {REFRESH_LOG}")
    return 0 if backend_ok and frontend_ok else 1


def run_data_check(backend_python: Path, env: dict[str, str]) -> int:
    """Refresh expected local data before dev services start."""

    print("Checking latest local data before startup...")
    result = subprocess.run(
        [
            str(backend_python),
            "scripts/dev_check.py",
            "--ensure-data",
            "--skip-eval",
        ],
        cwd=str(BACKEND),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    return result.returncode


def refresh_worker_running() -> bool:
    """Treat a recently touched lock as an active half-hour worker."""

    return worker_lock_active(REFRESH_LOCK, stale_after_seconds=90 * 60)


def worker_lock_active(lock_path: Path, *, stale_after_seconds: int) -> bool:
    """Return whether a worker lock still looks active."""

    if not lock_path.exists():
        return False
    try:
        age_seconds = time.time() - lock_path.stat().st_mtime
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid"))
    except OSError:
        return False
    except (ValueError, TypeError, json.JSONDecodeError):
        pid = None
    active = pid is not None and process_exists(pid)
    if not active or age_seconds > stale_after_seconds:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def process_exists(pid: int) -> bool:
    """Check that the PID recorded by the lock still exists."""

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def build_env() -> dict[str, str]:
    """Build a clean process environment and avoid Path/PATH duplicate failures."""

    env: dict[str, str] = {}
    for key, value in os.environ.items():
        normalized = "PATH" if key.lower() == "path" else key
        if normalized not in env:
            env[normalized] = value

    machine_path = os.environ.get("PATH") or os.environ.get("Path") or ""
    env["PATH"] = machine_path
    env["PYTHONPATH"] = str(BACKEND)
    api_key = env.get("DEEPSEEK_API_KEY") or read_windows_env("DEEPSEEK_API_KEY")
    if api_key:
        env["DEEPSEEK_API_KEY"] = api_key
        env["LIMITUPLAB_LLM_ENABLED"] = "true"
        env["LIMITUPLAB_LLM_BASE_URL"] = "https://api.deepseek.com"
        env["LIMITUPLAB_LLM_MODEL"] = "deepseek-v4-flash"
        env.setdefault("LIMITUPLAB_LLM_TIMEOUT_SECONDS", "15")
    else:
        env.setdefault("LIMITUPLAB_LLM_ENABLED", "false")

    proxy_names = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    for proxy_name in proxy_names:
        env.pop(proxy_name, None)
    proxy = env.get("LIMITUPLAB_PROXY_URL") or auto_detect_local_proxy()
    if proxy:
        for proxy_name in proxy_names:
            env[proxy_name] = proxy
    return env


def auto_detect_local_proxy() -> str:
    """Use the known local proxy when it is listening."""

    for port in (17891, 7890, 10809, 1080):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return f"http://127.0.0.1:{port}"
        except OSError:
            continue
    return ""


def read_windows_env(name: str) -> str:
    """Read a user or machine environment variable from the Windows registry."""

    for hive, subkey in (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    ):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, name)
                return str(value).strip()
        except OSError:
            continue
    return ""


def spawn_detached(
    args: list[str],
    *,
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    env: dict[str, str],
) -> None:
    """Spawn a detached Windows process and return immediately."""

    creationflags = 0
    if os.name == "nt":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    stdout = stdout_path.open("w", encoding="utf-8", errors="replace")
    stderr = stderr_path.open("w", encoding="utf-8", errors="replace")
    subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        creationflags=creationflags,
        close_fds=True,
    )


def wait_for(url: str, seconds: int) -> bool:
    """Poll a local URL for a few seconds."""

    deadline = time.time() + seconds
    while time.time() < deadline:
        if probe(url, timeout=1):
            return True
        time.sleep(0.5)
    return False


def probe(url: str, timeout: float) -> bool:
    """Return whether a local URL responds with a 2xx/3xx status."""

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
