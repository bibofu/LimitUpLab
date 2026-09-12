"""Single-host, tag-pinned deployment. Install root-owned; execute as ubuntu.

Only the forced SSH command `deploy vX.Y.Z COMMIT_SHA` is accepted.
No production secrets are read by Python or printed in deployment logs.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, time as clock_time
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo

REPO = Path("/opt/LimitUpLab")
STATE = Path("/var/lib/limituplab-deploy")
LOCK = Path("/var/lock/limituplab-release.lock")
MAINTENANCE = REPO / ".maintenance"
SERVICES = ("backend", "frontend", "recommendation-refresh", "daily-update")


# Accept only the deployment command's exact tag/SHA format and return the validated pair.
def parse_request(command: str) -> tuple[str, str]:
    match = re.fullmatch(r"deploy (v[0-9]+\.[0-9]+\.[0-9]+) ([0-9a-f]{40})", command)
    if not match:
        raise ValueError("Only 'deploy vX.Y.Z <40-character commit SHA>' is accepted")
    return match.group(1), match.group(2)


# Reject deployment during the protected Shanghai market-opening interval.
def check_window(now: datetime | None = None) -> None:
    local = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(ZoneInfo("Asia/Shanghai"))
    if clock_time(8, 30) <= local.time() < clock_time(9, 35):
        raise RuntimeError("Deployment blocked during 08:30-09:35 Asia/Shanghai; rerun later")


# Hold the cross-process deployment/update/backup lock, waiting only up to the requested timeout.
@contextmanager
def deployment_lock(timeout: int = 0):
    import fcntl

    with LOCK.open("r+") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Deployment/daily-update/backup lock is busy")
                time.sleep(1)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# Write release metadata through a temporary sibling and replacement to avoid a partially written
# journal.
def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class Deployment:
    # Initialize Deployment with the supplied dependencies and per-instance state.
    def __init__(self, tag: str, sha: str):
        self.tag, self.sha = tag, sha
        self.record = {"tag": tag, "sha": sha, "started_at": datetime.now().isoformat()}
        self.journal = STATE / f"{tag}-{datetime.now():%Y%m%dT%H%M%S}.json"
        self.log = self.journal.with_suffix(".log")
        self.previous: dict = {}
        self.previous_schema: dict | None = None
        self.maintenance = False
        self.promoted = False

    # Update the in-memory release state and persist it to the deployment journal.
    def status(self, status: str, **details) -> None:
        self.record.update(status=status, **details)
        write_json(self.journal, self.record)

    # Execute a deployment subprocess with bounded runtime and logged stderr; optionally return
    # captured stdout.
    def run(self, *args: str, capture: bool = False, timeout: int = 600) -> str:
        env = {**os.environ, "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15"}
        with self.log.open("a", encoding="utf-8") as log:
            result = subprocess.run(args, cwd=REPO, env=env, text=True,
                                    stdout=subprocess.PIPE if capture else log,
                                    stderr=log, timeout=timeout, check=False)
        if result.returncode:
            raise RuntimeError(f"{args[0]} {args[1]} failed ({result.returncode}); see {self.log}")
        return result.stdout.strip() if capture else ""

    # Run a Git command through the deployment's logged subprocess boundary and return its output.
    def git(self, *args: str) -> str:
        return self.run("git", *args, capture=True)

    # Create or validate the release checkout and its production-environment link without
    # overwriting unexpected files.
    def worktree(self, sha: str) -> Path:
        path = STATE / "releases" / sha
        if not path.exists():
            self.git("worktree", "add", "--detach", str(path), sha)
        if self.run("git", "-C", str(path), "rev-parse", "HEAD", capture=True) != sha:
            raise RuntimeError("Release directory does not match requested SHA")
        if self.run("git", "-C", str(path), "status", "--porcelain", "--untracked-files=no", capture=True):
            raise RuntimeError("Release worktree has local tracked changes")
        env_path = path / ".env.production"
        if not env_path.is_symlink():
            if env_path.exists():
                raise RuntimeError("Release environment file exists; refusing to overwrite")
            env_path.symlink_to(REPO / ".env.production")
        if env_path.resolve() != (REPO / ".env.production").resolve():
            raise RuntimeError("Release environment symlink points outside production configuration")
        return path

    # Run Docker Compose against the selected release and its pinned image override.
    def compose(self, release: dict, *args: str, timeout: int = 600) -> str:
        path = Path(release["path"])
        override = STATE / "releases" / f"{release['sha']}-images.json"
        write_json(override, {"services": {
            service: {"image": release["frontend"] if service == "frontend" else release["backend"]}
            for service in SERVICES
        }})
        return self.run("docker", "compose", "--project-name", "limituplab",
                        "--project-directory", str(path), "--env-file", str(REPO / ".env.production"),
                        "-f", str(path / "docker-compose.yml"), "-f", str(override),
                        *args, timeout=timeout)

    # Validate the release source and prepare its images before entering the service-switch phase.
    def prepare(self) -> dict:
        if MAINTENANCE.exists():
            raise RuntimeError("Maintenance is already active; operator recovery required")
        if self.git("status", "--porcelain", "--untracked-files=no"):
            raise RuntimeError("Production checkout has tracked changes; refusing to overwrite")
        if self.git("branch", "--show-current") != "main":
            raise RuntimeError("Production checkout must remain on main")
        # Transient GitHub network failures must not interrupt a healthy service.
        for attempt in range(3):
            try:
                self.run("git", "-c", "http.lowSpeedLimit=1", "-c", "http.lowSpeedTime=30",
                         "fetch", "origin", "refs/heads/main:refs/remotes/origin/main",
                         f"refs/tags/{self.tag}:refs/tags/{self.tag}", timeout=120)
                break
            except (RuntimeError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
                time.sleep(5)
        if self.git("rev-parse", f"refs/tags/{self.tag}^{{commit}}") != self.sha:
            raise RuntimeError("Tag/SHA mismatch")
        self.git("merge-base", "--is-ancestor", self.sha, "origin/main")
        old_sha = self.git("rev-parse", "HEAD")
        self.git("merge-base", "--is-ancestor", old_sha, self.sha)
        self.previous = {"sha": old_sha, "path": str(self.worktree(old_sha))}
        for service in ("backend", "frontend"):
            image = self.run("docker", "inspect", f"limituplab-{service}-1", "--format", "{{.Image}}", capture=True)
            previous_tag = f"limituplab-{service}:rollback-{old_sha}"
            self.run("docker", "tag", image, previous_tag)
            self.previous[service] = previous_tag
        target = {"sha": self.sha, "path": str(self.worktree(self.sha)),
                  "backend": f"limituplab-backend:{self.sha}", "frontend": f"limituplab-frontend:{self.sha}"}
        self.status("building", previous=self.previous, target=target)
        self.compose(target, "build", "backend", "frontend", timeout=2400)
        return target

    # Wait for an existing daily-update container to finish before the service switch.
    def wait_for_jobs(self) -> None:
        deadline = time.monotonic() + 600
        while self.run("docker", "ps", "-q", "--filter", "label=com.docker.compose.project=limituplab",
                       "--filter", "label=com.docker.compose.service=daily-update", capture=True):
            if time.monotonic() >= deadline:
                raise RuntimeError("Existing daily-update is still running; no services were stopped")
            time.sleep(5)

    # Read the current database schema version and hash using a read-only container mount.
    def schema(self) -> dict:
        code = (
            "import sqlite3,json,hashlib; "
            "c=sqlite3.connect('file:/app/data/limituplab.sqlite?mode=ro',uri=True); "
            "rows=c.execute(\"SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name\").fetchall(); "
            "print(json.dumps({'version':c.execute('PRAGMA user_version').fetchone()[0],"
            "'hash':hashlib.sha256(json.dumps(rows).encode()).hexdigest()}))"
        )
        # Keep the SQLite connection read-only, but mount the volume writable so SQLite can
        # create/open its WAL shared-memory sidecar. A Docker read-only mount fails even when the
        # database file itself is readable.
        return json.loads(self.run("docker", "run", "--rm", "--network", "none",
                                  "-v", "limituplab-data:/app/data", self.previous["backend"],
                                  "python", "-c", code, capture=True))

    # Create and verify a deployment-specific database backup outside the daily retention set.
    def backup(self) -> None:
        # Deployment snapshots live outside the rolling daily backup retention set.
        directory = f"/backups/deployments/{self.journal.stem}"
        # backup_database.py opens the source with mode=ro; the writable Docker mount is required
        # only for SQLite's WAL/SHM coordination files.
        output = self.run("docker", "run", "--rm", "--network", "none",
                          "-v", "limituplab-data:/app/data", "-v", "/var/backups/limituplab:/backups",
                          self.previous["backend"], "python", "scripts/backup_database.py",
                          "--database", "/app/data/limituplab.sqlite", "--output-dir", directory,
                          "--retain-count", "1", capture=True)
        match = re.search(r"backup=(/backups/deployments/\S+\.sqlite)", output)
        if not match:
            raise RuntimeError("Backup command did not report a verified snapshot")
        self.status("backed_up", backup=match.group(1).replace("/backups/", "/var/backups/limituplab/", 1),
                    previous_schema=self.previous_schema)

    # Verify the switched service's HTTP routes and expected payload shapes before accepting the
    # release.
    def probe(self) -> None:
        for route in ("/health", "/", "/recommendations?strategy=relay",
                      "/recommendations?strategy=consolidation", "/recommendations?strategy=drawdown",
                      "/api/agents/recommendation-intelligence",
                      "/api/strategies/consolidation?strategy=consolidation",
                      "/api/strategies/consolidation?strategy=drawdown"):
            try:
                response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    "http://127.0.0.1:8080" + route, timeout=30)
            except urllib.error.HTTPError as error:
                # A new installation may have no snapshot yet; 500 is never an empty state.
                if route == "/api/agents/recommendation-intelligence" and error.code == 404:
                    continue
                raise
            with response:
                if response.status != 200:
                    raise RuntimeError(f"Health check failed: {route}")
                body = response.read()
                if route == "/health" and json.loads(body).get("status") != "ok":
                    raise RuntimeError("Backend health payload is not ok")
                if route.startswith("/api/") and not isinstance(json.loads(body), dict):
                    raise RuntimeError(f"Business endpoint did not return an object: {route}")

    # Point the local backend/frontend image tags at the selected release images.
    def restore_local_tags(self, release: dict) -> None:
        for service in ("backend", "frontend"):
            self.run("docker", "tag", release[service], f"limituplab-{service}:local")

    # Start the release's background worker after the foreground service transition.
    def start_worker(self, release: dict) -> None:
        self.compose(release, "up", "-d", "--no-build", "recommendation-refresh")
        # Check it remains alive, rather than only checking a successful docker start.
        time.sleep(10)
        state = json.loads(self.run("docker", "inspect", "limituplab-recommendation-refresh-1",
                                   "--format", "{{json .State}}", capture=True))
        if not state["Running"] or state["Restarting"]:
            raise RuntimeError("Recommendation refresh worker is not stable")

    # Attempt release recovery using the recorded previous state, retaining maintenance protection
    # when rollback is unsafe.
    def recover(self, target: dict) -> None:
        self.compose(target, "stop", "-t", "60", "recommendation-refresh", "backend", "frontend")
        if self.promoted or self.previous_schema is None or self.schema() != self.previous_schema:
            self.status("manual_recovery_required", reason="Release metadata advanced or database schema changed/unverified")
            return
        self.restore_local_tags(self.previous)
        self.compose(self.previous, "up", "-d", "--no-build", "--wait", "--wait-timeout", "180", "backend", "frontend")
        self.probe()
        self.start_worker(self.previous)
        MAINTENANCE.unlink()
        self.maintenance = False
        self.status("rolled_back")

    # Coordinate preparation, maintenance, backup, service switch, verification and recovery as
    # one journaled release.
    def execute(self) -> None:
        check_window()
        target = self.prepare()
        self.wait_for_jobs()
        check_window()  # Building/waiting can cross into the protected window.
        self.previous_schema = self.schema()
        MAINTENANCE.touch(exist_ok=False)
        self.maintenance = True
        self.status("maintenance")
        try:
            self.compose(self.previous, "stop", "-t", "60", "recommendation-refresh", "frontend", "backend")
            self.previous_schema = self.schema()
            self.backup()
            check_window()
            self.status("switching")
            self.compose(target, "up", "-d", "--no-build", "--wait", "--wait-timeout", "180", "backend", "frontend")
            self.probe()
            self.start_worker(target)
            self.restore_local_tags(target)
            self.git("merge", "--ff-only", self.sha)
            self.promoted = True
            write_json(STATE / "current.json", {**target, "tag": self.tag, "previous": self.previous,
                                                "journal": str(self.journal)})
            self.status("success")
            MAINTENANCE.unlink()
            self.maintenance = False
        except Exception:
            try:
                self.recover(target)
            except Exception:
                self.status("manual_recovery_required", reason="Recovery failed; keep maintenance and inspect log")
            raise


# Parse the restricted deployment request, hold the deployment lock and report the release result.
def main() -> int:
    tag, sha = parse_request(os.environ.get("SSH_ORIGINAL_COMMAND", " ".join(sys.argv[1:])))
    os.umask(0o077)
    # A disconnected SSH client must not interrupt a database migration.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    with deployment_lock(timeout=3600):
        deploy = Deployment(tag, sha)
        try:
            deploy.execute()
        except Exception as error:
            if not deploy.maintenance and deploy.record.get("status") != "rolled_back":
                deploy.status("failed_before_switch")
            print(f"Deployment failed: {error}; journal={deploy.journal}", file=sys.stderr)
            return 1
        print(f"Deployed {tag} {sha}; journal={deploy.journal}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
