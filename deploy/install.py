"""One-time root installation of reviewed release scripts and restricted SSH key.

Run from an uploaded, reviewed deploy/ directory. Never invoked by tag CI.
"""
import argparse
from datetime import datetime
import os
from pathlib import Path
import pwd
import shutil
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-key", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Installation requires root")
    public_key = args.public_key.read_text().strip()
    parts = public_key.split()
    if len(parts) != 3 or parts[0] != "ssh-ed25519" or parts[2] != "limituplab-actions-deploy":
        raise ValueError("Expected the dedicated deployment public key, not a personal key")
    user = pwd.getpwnam("ubuntu")
    source = Path(__file__).resolve().parent
    target = Path("/usr/local/lib/limituplab")
    state = Path("/var/lib/limituplab-deploy")
    target.mkdir(parents=True, exist_ok=True)
    target.chmod(0o755)
    for name in ("release.py", "job.py"):
        shutil.copyfile(source / name, target / name)
        (target / name).chmod(0o644)
        os.chown(target / name, 0, 0)
    for directory in (state, state / "releases"):
        directory.mkdir(exist_ok=True)
        directory.chmod(0o700)
        os.chown(directory, user.pw_uid, user.pw_gid)
    lock = Path("/var/lock/limituplab-release.lock")
    lock.touch(exist_ok=True)
    lock.chmod(0o660)
    os.chown(lock, user.pw_uid, user.pw_gid)

    nginx = Path("/etc/nginx/sites-available/limituplab")
    original = nginx.read_text()
    include = "    include /etc/nginx/snippets/limituplab-maintenance.conf;\n"
    if include not in original and original.count("proxy_pass http://127.0.0.1:8080;") != 1:
        raise RuntimeError("Unexpected Nginx layout; inspect manually before installation")
    backup = Path("/var/backups/limituplab-deploy-config") / datetime.now().strftime("%Y%m%dT%H%M%S")
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(nginx, backup / "nginx.conf")
    snippet = Path("/etc/nginx/snippets/limituplab-maintenance.conf")
    shutil.copyfile(source / "nginx/maintenance.conf", snippet)
    snippet.chmod(0o644)
    if include not in original:
        nginx.write_text(original.replace("    location / {", include + "\n    location / {", 1))
    try:
        subprocess.run(["nginx", "-t"], check=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=True)
    except Exception:
        shutil.copy2(backup / "nginx.conf", nginx)
        raise
    for name in ("limituplab-daily", "limituplab-backup"):
        destination = Path("/etc/cron.d") / name
        if destination.exists():
            shutil.copy2(destination, backup / name)
        shutil.copyfile(source / "cron" / name, destination)
        destination.chmod(0o644)

    authorized = Path(user.pw_dir) / ".ssh/authorized_keys"
    existing = authorized.read_text() if authorized.exists() else ""
    line = f'restrict,command="/usr/bin/python3 /usr/local/lib/limituplab/release.py" {public_key}'
    if "limituplab-actions-deploy" in existing and line not in existing.splitlines():
        raise RuntimeError("Another deployment key already exists; do not silently replace it")
    if line not in existing.splitlines():
        with authorized.open("a") as output:
            output.write(("\n" if existing and not existing.endswith("\n") else "") + line + "\n")
    authorized.chmod(0o600)
    os.chown(authorized, user.pw_uid, user.pw_gid)
    print(f"Deployment entry installed. Config backup: {backup}. No application containers restarted.")


if __name__ == "__main__":
    main()
