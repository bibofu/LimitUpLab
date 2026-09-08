"""Send an exact release request over pinned SSH; never print credentials."""
import os
from pathlib import Path
import subprocess
import tempfile

from release import parse_request


def main() -> None:
    tag = os.environ["RELEASE_TAG"]
    sha = os.environ["RELEASE_SHA"]
    parse_request(f"deploy {tag} {sha}")
    if not os.environ.get("DEPLOY_SSH_KEY", "").strip():
        raise RuntimeError("Configure the repository Actions secret DEPLOY_SSH_KEY before publishing a tag")
    root = Path(__file__).resolve().parents[1]
    # Resolve the annotated tag's commit rather than trusting the event's object SHA.
    resolved = subprocess.check_output(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"], text=True).strip()
    if resolved != sha:
        raise RuntimeError("Checked-out release does not match the tag")
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], check=True)
    with tempfile.TemporaryDirectory(prefix="limituplab-deploy-") as directory:
        key = Path(directory) / "key"
        key.write_text(os.environ["DEPLOY_SSH_KEY"].strip() + "\n", encoding="utf-8")
        key.chmod(0o600)
        result = subprocess.run([
            "ssh", "-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={root / 'deploy/production_known_hosts'}",
            "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=10",
            "ubuntu@118.193.34.72", f"deploy {tag} {sha}",
        ], check=False)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write(f"## Production deployment\n\nTag: `{tag}`\n\nCommit: `{sha}`\n\n"
                         f"Result: {'success' if result.returncode == 0 else 'failed; inspect deployment log'}\n")
    if result.returncode:
        raise RuntimeError(f"Production deployment returned {result.returncode}")


if __name__ == "__main__":
    main()
