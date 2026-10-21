"""Manage only this checkout's explicitly configured local PostgreSQL cluster."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import tempfile

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import load_local_env
from sqlalchemy.engine import make_url


@dataclass(frozen=True)
class LocalCluster:
    bin_dir: Path
    data_dir: Path
    port: int

    @property
    def pg_ctl(self) -> Path:
        return self.bin_dir / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")


def project_path(value: str, root: Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def local_cluster(url, env, *, root: Path = ROOT) -> LocalCluster | None:
    """Require explicit paths; never manage an external database by hostname alone."""
    raw_bin = env.get("LIMITUPLAB_LOCAL_PG_BIN", "").strip()
    raw_data = env.get("LIMITUPLAB_LOCAL_PG_DATA", "").strip()
    if not raw_bin and not raw_data:
        return None
    if not raw_bin or not raw_data:
        raise RuntimeError("Both LIMITUPLAB_LOCAL_PG_BIN and LIMITUPLAB_LOCAL_PG_DATA are required")
    if url.host not in {"127.0.0.1", "localhost"} or url.query:
        raise RuntimeError("Managed PostgreSQL requires a loopback URL without query overrides")
    allowed = (root / ".local" / "postgresql").resolve()
    bin_dir, data_dir = project_path(raw_bin, root), project_path(raw_data, root)
    if not data_dir.is_relative_to(allowed) or data_dir == allowed:
        raise RuntimeError("Managed data directory must be inside this project's .local/postgresql")
    if not bin_dir.is_relative_to(allowed):
        raise RuntimeError("Managed binaries must be inside this project's .local/postgresql")
    cluster = LocalCluster(bin_dir, data_dir, url.port or 5432)
    if not cluster.pg_ctl.is_file() or not (data_dir / "PG_VERSION").is_file():
        raise RuntimeError("Local PostgreSQL binaries or initialized cluster are missing")
    return cluster


def run_ctl(cluster: LocalCluster, *args: str) -> subprocess.CompletedProcess:
    """Hide server helpers and keep credentials out of argv and diagnostics."""
    # Windows postmaster descendants can inherit pipe handles. Waiting for pipe
    # EOF would then block until the server exits, despite pg_ctl already exiting.
    with tempfile.TemporaryFile(dir=cluster.data_dir.parent) as output:
        result = subprocess.run(
            [str(cluster.pg_ctl), "-D", str(cluster.data_dir), *args],
            stdin=subprocess.DEVNULL, stdout=output, stderr=output, timeout=40,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output.seek(0)
        result.stdout = output.read(65536).decode("utf-8", errors="replace")
        return result


def validate_running_cluster(cluster: LocalCluster) -> None:
    """Match the running postmaster to the declared project directory and endpoint."""
    try:
        fields = (cluster.data_dir / "postmaster.pid").read_text(encoding="utf-8").splitlines()
        same_data = Path(fields[1]).resolve() == cluster.data_dir
        same_port = int(fields[3]) == cluster.port
        loopback = fields[5].strip() == "127.0.0.1"
    except (OSError, IndexError, ValueError):
        raise RuntimeError("Cannot verify local PostgreSQL process identity") from None
    if not same_data or not same_port or not loopback:
        raise RuntimeError("Running PostgreSQL does not match the configured local cluster")


def verify_database(url) -> None:
    """Check authentication, database identity and schema without creating anything."""
    from sqlalchemy import create_engine
    from app.storage.schema import assert_schema_current

    engine = create_engine(
        url.set(drivername="postgresql+psycopg"), hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    try:
        with engine.connect() as connection:
            database = connection.exec_driver_sql("SELECT current_database()").scalar_one()
            if database != url.database:
                raise RuntimeError("Unexpected database identity")
            assert_schema_current(connection)
    except Exception:
        raise RuntimeError("Configured PostgreSQL is not ready; check credentials, database and migrations") from None
    finally:
        engine.dispose()


def manage(action: str, env=None, *, root: Path = ROOT) -> str:
    env = os.environ if env is None else env
    raw_url = env.get("LIMITUPLAB_DATABASE_URL", "").strip()
    if not raw_url:
        raise RuntimeError("LIMITUPLAB_DATABASE_URL is required; configure PostgreSQL before startup")
    try:
        url = make_url(raw_url)
    except Exception:
        raise RuntimeError("Invalid LIMITUPLAB_DATABASE_URL") from None
    if url.drivername not in {"postgresql", "postgresql+psycopg"} or not url.database:
        raise RuntimeError("LIMITUPLAB_DATABASE_URL must select a PostgreSQL database")
    cluster = local_cluster(url, env, root=root)
    if cluster is None:
        if action == "stop":
            raise RuntimeError("Refusing to stop PostgreSQL without explicit local cluster paths")
        verify_database(url)
        return "PostgreSQL ready (externally managed)"

    result = run_ctl(cluster, "status")
    if result.returncode not in {0, 3}:
        raise RuntimeError("Cannot read local PostgreSQL status")
    running = result.returncode == 0
    if running:
        validate_running_cluster(cluster)
    if action == "stop":
        if running and run_ctl(cluster, "-m", "fast", "-w", "-t", "30", "stop").returncode:
            raise RuntimeError("Local PostgreSQL did not stop; inspect its log")
        return "Local PostgreSQL stopped"
    if not running:
        if action == "status":
            raise RuntimeError("Local PostgreSQL is stopped")
        log = cluster.data_dir.parent / "postgresql.log"
        result = run_ctl(cluster, "-l", str(log), "-o", f"-h 127.0.0.1 -p {cluster.port}",
                         "-w", "-t", "30", "start")
        if result.returncode:
            raise RuntimeError("Local PostgreSQL did not start; inspect .local/postgresql/postgresql.log")
        validate_running_cluster(cluster)
    verify_database(url)
    return f"Local PostgreSQL ready on 127.0.0.1:{cluster.port}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("ensure", "status", "stop"))
    args = parser.parse_args()
    load_local_env()
    try:
        print(manage(args.action))
        return 0
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        # Only our sanitized RuntimeErrors are rendered, never driver credentials.
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"Database startup check failed: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
