"""Run existing production jobs under the same lock as a release switch."""
import subprocess
import sys

from release import MAINTENANCE, REPO, deployment_lock


def main() -> int:
    commands = {
        "daily-preview": ["docker", "compose", "--env-file", ".env.production", "--profile", "jobs",
                          "run", "--rm", "daily-update", "python", "scripts/run_daily_close_loop.py",
                          "--trigger", "scheduled", "--phase", "preview"],
        "daily-update": ["docker", "compose", "--env-file", ".env.production", "--profile", "jobs",
                         "run", "--rm", "daily-update"],
        "backup": ["docker", "run", "--rm", "-v", "limituplab-data:/app/data",
                   "-v", "/var/backups/limituplab:/backups", "limituplab-backend:local",
                   "python", "scripts/backup_database.py", "--database", "/app/data/limituplab.sqlite",
                   "--output-dir", "/backups", "--retain-count", "14"],
    }
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise ValueError("Expected daily-preview, daily-update or backup")
    with deployment_lock(timeout=7200):
        if MAINTENANCE.exists():
            raise RuntimeError("Maintenance active; job blocked until operator recovery")
        return subprocess.run(commands[sys.argv[1]], cwd=REPO, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
