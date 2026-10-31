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
WORKER_SERVICES = ("recommendation-refresh", "memory-extraction", "memory-embeddings", "review-worker")
LEGACY_WORKER_SERVICES = ("quality-worker", "quality-judge-worker")
SERVICES = ("backend", "frontend", *WORKER_SERVICES, *LEGACY_WORKER_SERVICES, "daily-update")
WORKER_START_TIMEOUT = 480
WORKER_STABLE_SECONDS = 30
WORKER_POLL_SECONDS = 5


def database_mode() -> str:
    """Read the explicit operator selection without reading connection secrets."""
    marker = STATE / "database.json"
    current = STATE / "current.json"
    selected = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
    active = json.loads(current.read_text(encoding="utf-8")) if current.exists() else {}
    mode = selected.get("engine")
    recorded = active.get("engine")
    if mode != "postgresql":
        raise RuntimeError("Production requires an explicit PostgreSQL database marker")
    if current.exists() and recorded != "postgresql":
        raise RuntimeError("Invalid recorded production database mode")
    if recorded is not None and recorded != mode:
        raise RuntimeError("Production database marker and current release disagree; operator recovery required")
    return mode


def compose_arguments(path: Path, mode: str) -> list[str]:
    """Keep releases and scheduled jobs on the same explicitly selected database."""
    if mode != "postgresql":
        raise RuntimeError("Only PostgreSQL production deployments are supported")
    args = ["docker", "compose", "--project-name", "limituplab", "--project-directory", str(path),
            "--env-file", str(REPO / ".env.production")]
    if not (REPO / ".env.postgresql").is_file():
        raise RuntimeError("PostgreSQL configuration is missing")
    if not (path / "docker-compose.postgresql.yml").is_file():
        raise RuntimeError("Release does not support the configured PostgreSQL deployment")
    args += ["--env-file", str(REPO / ".env.postgresql")]
    args += ["-f", str(path / "docker-compose.yml")]
    args += ["-f", str(path / "docker-compose.postgresql.yml")]
    return args


def assert_runtime_database_mode(mode: str) -> None:
    """Inspect only the backend's engine name; never return its connection URL."""
    code = ("import os; u=os.environ.get('LIMITUPLAB_DATABASE_URL','').strip(); "
            "print('postgresql' if u.startswith(('postgresql://','postgresql+psycopg://')) "
            "else 'invalid')")
    result = subprocess.run(["docker", "exec", "limituplab-backend-1", "python", "-c", code],
                            cwd=REPO, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=30, check=False)
    if result.returncode or result.stdout.strip() != mode:
        raise RuntimeError("Running backend database differs from deployment mode or cannot be verified")


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
        self.mode = "postgresql"
        self._service_names: dict[tuple[str, str], tuple[str, ...]] = {}

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

    def declared_services(self, release: dict) -> tuple[str, ...]:
        """Inspect service names only, before adding any image overrides."""
        path = Path(release["path"])
        mode = release.get("engine", self.mode)
        if mode != self.mode:
            raise RuntimeError("Automatic cross-database release or rollback is forbidden")
        key = (str(path), mode)
        if key not in self._service_names:
            output = self.run(*compose_arguments(path, mode), "--profile", "*",
                              "config", "--services", capture=True, timeout=30)
            names = tuple(dict.fromkeys(output.splitlines()))
            if not {"backend", "frontend", "postgres", "postgres-migrate"}.issubset(names):
                raise RuntimeError("Release is missing required PostgreSQL application services")
            self._service_names[key] = names
        return self._service_names[key]

    def worker_services(self, release: dict) -> tuple[str, ...]:
        declared = self.declared_services(release)
        return tuple(name for name in (*WORKER_SERVICES, *LEGACY_WORKER_SERVICES)
                     if name in declared)

    # Run Docker Compose against the selected release and its pinned image override.
    def compose(self, release: dict, *args: str, timeout: int = 600, capture: bool = False) -> str:
        path = Path(release["path"])
        mode = release.get("engine", self.mode)
        if mode != self.mode:
            raise RuntimeError("Automatic cross-database release or rollback is forbidden")
        override = STATE / "releases" / f"{release['sha']}-images.json"
        declared = self.declared_services(release)
        services = (name for name in (*SERVICES, "postgres-migrate") if name in declared)
        write_json(override, {"services": {
            service: {"image": release["frontend"] if service == "frontend" else release["backend"]}
            for service in services
        }})
        return self.run(*compose_arguments(path, mode), "-f", str(override),
                        *args, timeout=timeout, capture=capture)

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
        self.mode = database_mode()
        assert_runtime_database_mode(self.mode)
        self.record["engine"] = self.mode
        self.previous = {"sha": old_sha, "path": str(self.worktree(old_sha)), "engine": self.mode}
        compose_arguments(Path(self.previous["path"]), self.mode)
        for service in ("backend", "frontend"):
            image = self.run("docker", "inspect", f"limituplab-{service}-1", "--format", "{{.Image}}", capture=True)
            previous_tag = f"limituplab-{service}:rollback-{old_sha}"
            self.run("docker", "tag", image, previous_tag)
            self.previous[service] = previous_tag
        running = self.compose(self.previous, "ps", "--services", "--status", "running",
                               capture=True).splitlines()
        self.previous["running_workers"] = [name for name in self.worker_services(self.previous)
                                            if name in running]
        target = {"sha": self.sha, "path": str(self.worktree(self.sha)), "engine": self.mode,
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

    # Read the PostgreSQL schema version and hash without changing the database.
    def schema(self) -> dict:
        code = (
            "import os,json,hashlib,sqlalchemy as sa; "
            "from scripts.postgres_admin import connection_environment,client; "
            "u,e=connection_environment(os.environ['LIMITUPLAB_DATABASE_URL']); "
            "d=client(['pg_dump','--schema-only','--no-owner','--no-privileges'],e).stdout.decode('utf-8'); "
            "d='\\n'.join(line for line in d.splitlines() "
            "if not line.startswith(('\\\\restrict ','\\\\unrestrict '))); "
            "engine=sa.create_engine(u,hide_parameters=True); "
            "c=engine.connect(); "
            "v=list(c.execute(sa.text('SELECT version_num FROM alembic_version ORDER BY version_num')).scalars()); "
            "c.close();engine.dispose(); "
            "print(json.dumps({'engine':'postgresql','version':v,'hash':hashlib.sha256(d.encode()).hexdigest()}))"
        )
        return json.loads(self.compose(self.previous, "run", "--rm", "--no-deps", "backend",
                                       "python", "-c", code, capture=True))

    # Create and verify a deployment-specific database backup outside the daily retention set.
    def backup(self) -> None:
        # Deployment snapshots live outside the rolling daily backup retention set.
        directory = f"/backups/deployments/{self.journal.stem}"
        output = self.compose(self.previous, "run", "--rm", "--no-deps", "-v",
                              "/var/backups/limituplab:/backups", "backend", "python",
                              "scripts/postgres_admin.py", "backup", "--output", f"{directory}/postgresql.dump",
                              capture=True)
        result = json.loads(output)
        if (result.get("backup") != f"{directory}/postgresql.dump"
                or result.get("archive_readable") is not True or len(result.get("sha256", "")) != 64):
            raise RuntimeError("PostgreSQL backup did not report a verified archive")
        self.status("backed_up", backup=result["backup"].replace("/backups/", "/var/backups/limituplab/", 1),
                    backup_sha256=result["sha256"], previous_schema=self.previous_schema)

    # Verify the switched service's HTTP routes and expected payload shapes before accepting the
    # release.
    def probe(self) -> None:
        code = ("import urllib.request; "
                "print(urllib.request.urlopen('http://127.0.0.1:8001/ready',timeout=10).read().decode())")
        ready = json.loads(self.run("docker", "exec", "limituplab-backend-1", "python", "-c", code,
                                   capture=True, timeout=30))
        if ready.get("status") != "ok" or ready.get("database") != "postgresql":
            raise RuntimeError("Backend readiness did not confirm PostgreSQL")
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

    def worker_leases(self, workers: tuple[str, ...]) -> dict:
        """Read legacy lease evidence without exposing ownership tokens or changing leases."""
        names = tuple("job:" + name for name in workers)
        code = (
            "import hashlib,json\nfrom app.database import connect_readonly\n"
            f"names={names!r}\ndb=connect_readonly()\n"
            "now=float(db.execute('SELECT EXTRACT(EPOCH FROM clock_timestamp())').fetchone()[0])\n"
            "rows=db.execute('SELECT name,owner_token,fence,expires_at FROM runtime_leases WHERE name IN ('"
            "+','.join('?' for _ in names)+')',names).fetchall()\ndb.close()\n"
            "print(json.dumps({row['name'][4:]:{'owner':hashlib.sha256(row['owner_token'].encode()).hexdigest(),"
            "'fence':row['fence'],'expires_at':row['expires_at'],'live':row['expires_at']>now} for row in rows}))"
        )
        return json.loads(self.run("docker", "exec", "limituplab-backend-1", "python", "-c", code,
                                   capture=True, timeout=15))

    def worker_state(self, service: str) -> dict:
        """Inspect only lifecycle fields; never export container environment variables."""
        return json.loads(self.run(
            "docker", "inspect", f"limituplab-{service}-1", "--format",
            '{"id":{{json .Id}},"restarts":{{json .RestartCount}},"state":{{json .State}}}',
            capture=True, timeout=15,
        ))

    def worker_ready(self, container: str, service: str, started_at: str) -> dict:
        return json.loads(self.run(
            "docker", "exec", container, "python", "-m", "app.services.worker_lifecycle",
            "probe", service, started_at, capture=True, timeout=15,
        ))

    # Require continuous process stability and lease evidence, including during legacy rollback.
    def start_workers(self, release: dict) -> None:
        declared = self.worker_services(release)
        workers = tuple(name for name in declared
                        if "running_workers" not in release or name in release["running_workers"])
        if not workers:
            return
        modern = (Path(release["path"]) / "backend/app/services/worker_lifecycle.py").is_file()
        deadline = time.monotonic() + WORKER_START_TIMEOUT
        baseline = {}
        if not modern:
            while True:
                try:
                    baseline = self.worker_leases(workers)
                    break
                except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Legacy worker lease baseline could not be read") from error
                    time.sleep(min(WORKER_POLL_SECONDS, max(0, deadline - time.monotonic())))
        self.compose(release, "up", "-d", "--no-build", *workers)
        observed: dict[str, dict] = {}
        reasons = dict.fromkeys(workers, "not observed")
        while time.monotonic() < deadline:
            try:
                leases = self.worker_leases(workers) if not modern else {}
            except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
                observed.clear()
                reasons = dict.fromkeys(workers, f"legacy lease probe failed ({type(error).__name__})")
                time.sleep(min(WORKER_POLL_SECONDS, max(0, deadline - time.monotonic())))
                continue
            complete = True
            for service in workers:
                try:
                    snapshot = self.worker_state(service)
                    state = snapshot["state"]
                    identity = (snapshot["id"], state["StartedAt"], snapshot["restarts"])
                    if (not state["Running"] or state["Restarting"]
                            or state.get("Paused") or state.get("Dead")
                            or state.get("Health", {}).get("Status", "healthy") != "healthy"):
                        observed.pop(service, None)
                        reasons[service] = (f"container is not running continuously "
                                            f"(status={state.get('Status')}, restarts={snapshot['restarts']})")
                        complete = False
                        continue
                    current = observed.get(service)
                    if current is None or current["identity"] != identity:
                        current = observed[service] = {"identity": identity, "stable_since": None}
                    ready = False
                    if modern:
                        result = self.worker_ready(snapshot["id"], service, state["StartedAt"])
                        ready = result.get("ready") is True
                        reason = result.get("reason", "readiness not confirmed")
                    else:
                        lease = leases.get(service, {})
                        old = baseline.get(service, {})
                        owner = (lease.get("owner"), lease.get("fence"))
                        if (not lease.get("live") or owner == (old.get("owner"), old.get("fence"))):
                            current.pop("lease", None)
                            reason = "legacy lease has not changed owner since startup"
                        elif current.get("lease", {}).get("owner") != owner:
                            current["lease"] = {"owner": owner, "expires_at": lease["expires_at"]}
                            reason = "legacy new owner has not renewed its lease"
                        else:
                            ready = lease["expires_at"] > current["lease"]["expires_at"]
                            reason = "legacy lease renewal confirmed" if ready else "legacy lease renewal pending"
                    now = time.monotonic()
                    if ready:
                        if current["stable_since"] is None:
                            current["stable_since"] = now
                        stable = now - current["stable_since"]
                        reasons[service] = f"{reason}; stable for {stable:.0f}s"
                        complete = complete and stable >= WORKER_STABLE_SECONDS
                    else:
                        current["stable_since"] = None
                        reasons[service] = reason
                        complete = False
                except (RuntimeError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as error:
                    observed.pop(service, None)
                    reasons[service] = f"worker probe failed ({type(error).__name__})"
                    complete = False
            if complete:
                # A readiness subprocess can overlap a restart; recheck every sampled identity.
                for service in workers:
                    try:
                        snapshot = self.worker_state(service)
                        state = snapshot["state"]
                        identity = (snapshot["id"], state["StartedAt"], snapshot["restarts"])
                        if (identity != observed[service]["identity"] or not state["Running"]
                                or state["Restarting"] or state.get("Paused") or state.get("Dead")
                                or state.get("Health", {}).get("Status", "healthy") != "healthy"):
                            raise RuntimeError("container changed during readiness probe")
                    except (RuntimeError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
                        observed.pop(service, None)
                        reasons[service] = "container changed or could not be verified after readiness probe"
                        complete = False
            if complete and time.monotonic() < deadline:
                with self.log.open("a", encoding="utf-8") as output:
                    output.write(json.dumps({"worker_readiness": reasons,
                                             "mode": "ownership_probe" if modern else "legacy_renewal"}) + "\n")
                return
            time.sleep(min(WORKER_POLL_SECONDS, max(0, deadline - time.monotonic())))
        details = "; ".join(f"{name}: {reason}" for name, reason in reasons.items())
        raise RuntimeError(f"Workers did not become ready within {WORKER_START_TIMEOUT}s: {details}")

    # Attempt release recovery using the recorded previous state, retaining maintenance protection
    # when rollback is unsafe.
    def recover(self, target: dict) -> None:
        self.compose(target, "stop", "-t", "60", *self.worker_services(target), "backend", "frontend")
        if self.promoted or self.previous_schema is None or self.schema() != self.previous_schema:
            self.status("manual_recovery_required", reason="Release metadata advanced or database schema changed/unverified")
            return
        self.restore_local_tags(self.previous)
        self.compose(self.previous, "up", "-d", "--no-build", "--wait", "--wait-timeout", "180", "backend", "frontend")
        self.probe()
        self.start_workers(self.previous)
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
            self.compose(self.previous, "stop", "-t", "60", *self.worker_services(self.previous), "frontend", "backend")
            self.previous_schema = self.schema()
            self.backup()
            check_window()
            self.status("switching")
            self.compose(target, "up", "-d", "--no-build", "--wait", "--wait-timeout", "180", "backend", "frontend")
            self.probe()
            self.start_workers(target)
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
