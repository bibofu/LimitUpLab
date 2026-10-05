"""Read-only verification of historical reports against published SHA-256 values."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
# Published in Agent_Golden_Results_20260929_v14.md and
# Agent_Golden_Results_20261005_Criteria.md. Never derive expectations from files being audited.
PUBLISHED_REPORTS = {
    "baseline-v30-v14-20260929/report.json":
        "a36476d1318877ad69c5d267da73d9a8475843e0e3e41ba2283a901dbbcc3d05",
    "critical-v30-v14-3x-20260929/report.json":
        "2c15b01ca96ead67609966399e38a2c4e12a68e4064709eccdb8abfd1edde145",
    "judge-v12-criteria-round1-20260930.json":
        "779e8c14ec39b4749fa7f1cbe5b4cc38143c5b3571511ec0ba64b7a069f4e83d",
    "judge-v12-criteria-round2-20260930.json":
        "4e961856ca9b51bcad4955264626571411a014c275f0a433d0e7376e1a3a9b44",
}


def audit_artifacts(directory, expected=PUBLISHED_REPORTS):
    results = []
    for name, fingerprint in expected.items():
        item = {"file": name, "expected_sha256": fingerprint}
        try:
            item["actual_sha256"] = hashlib.sha256((Path(directory) / name).read_bytes()).hexdigest()
            item["status"] = "verified" if item["actual_sha256"] == fingerprint else "mismatch"
        except FileNotFoundError:
            item["status"] = "missing"
        except OSError as error:
            item.update(status="unreadable", error_type=type(error).__name__)
        results.append(item)
    counts = dict(Counter(item["status"] for item in results))
    return {"complete": bool(results) and all(item["status"] == "verified" for item in results),
            "counts": counts, "results": results,
            "scope": "Byte identity against published fingerprints only; no model calls, rescoring or recovery."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "output/golden")
    args = parser.parse_args(argv)
    report = audit_artifacts(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
