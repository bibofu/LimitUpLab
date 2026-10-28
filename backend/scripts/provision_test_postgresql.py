"""Manage a disposable loopback PostgreSQL cluster for the CI test matrix."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CLUSTER = ROOT / "output" / "ci-postgresql"


def executable(bin_dir: Path, name: str) -> str:
    path = bin_dir / (name + (".exe" if os.name == "nt" else ""))
    if not path.is_file():
        raise RuntimeError(f"Required PostgreSQL client is missing: {name}")
    return str(path)


def run(bin_dir: Path, name: str, *arguments: str) -> None:
    subprocess.run([executable(bin_dir, name), *arguments], check=True, timeout=90,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop"))
    parser.add_argument("--bin-dir", type=Path)
    args = parser.parse_args()
    # This helper has no switch for an application cluster or external directory.
    if CLUSTER.resolve() != CLUSTER.absolute():
        raise RuntimeError("CI PostgreSQL directory must not contain symlink components")
    state_path = CLUSTER / "cluster.json"
    data_dir = CLUSTER / "data"
    if args.action == "stop":
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            run(Path(state["bin_dir"]), "pg_ctl", "-D", str(data_dir), "-m", "fast", "-w", "stop")
        return
    if args.bin_dir is None:
        parser.error("--bin-dir is required to initialize the disposable cluster")
    github_env, github_path = os.environ.get("GITHUB_ENV"), os.environ.get("GITHUB_PATH")
    if not github_env or not github_path:
        raise RuntimeError("This helper requires CI GITHUB_ENV and GITHUB_PATH before creating a cluster")
    if CLUSTER.exists():
        raise RuntimeError("CI PostgreSQL directory already exists; use a clean checkout")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    CLUSTER.mkdir(parents=True)
    bin_dir = args.bin_dir.resolve()
    run(bin_dir, "initdb", "-D", str(data_dir), "-U", "limituplab_test", "-A", "trust",
        "--encoding=UTF8", "--no-locale")
    with (data_dir / "postgresql.conf").open("a", encoding="utf-8") as config:
        # Distribution builds may default to a shared socket directory that the
        # CI user cannot write. All clients use loopback TCP; empty is also the
        # normal Windows socket setting, so no platform-specific path is needed.
        config.write(f"\nlisten_addresses = '127.0.0.1'\nport = {port}\nunix_socket_directories = ''\n")
    log_path = CLUSTER / "postgresql.log"
    try:
        run(bin_dir, "pg_ctl", "-D", str(data_dir), "-l", str(log_path), "-w", "start")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("Disposable PostgreSQL startup failed; cluster log follows:", file=sys.stderr, flush=True)
        try:
            print(log_path.read_text(encoding="utf-8", errors="replace"), file=sys.stderr, flush=True)
        except OSError as error:
            print(f"Cluster log unavailable ({type(error).__name__})", file=sys.stderr, flush=True)
        raise
    state_path.write_text(json.dumps({"bin_dir": str(bin_dir)}), encoding="utf-8")
    run(bin_dir, "createdb", "-h", "127.0.0.1", "-p", str(port), "-U", "limituplab_test", "limituplab_test")
    # Fail at provisioning, before exporting the URL, if the selected server lacks pgvector.
    run(bin_dir, "psql", "-X", "-h", "127.0.0.1", "-p", str(port), "-U", "limituplab_test",
        "-d", "limituplab_test", "-v", "ON_ERROR_STOP=1", "-c",
        "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    url = f"postgresql+psycopg://limituplab_test@127.0.0.1:{port}/limituplab_test"
    with Path(github_env).open("a", encoding="utf-8") as environment:
        environment.write(f"LIMITUPLAB_TEST_DATABASE_URL={url}\n")
    with Path(github_path).open("a", encoding="utf-8") as environment:
        environment.write(str(bin_dir) + "\n")


if __name__ == "__main__":
    main()
