"""Bind evaluator observations to recorded fixture code, never today's oracle."""

import hashlib
import inspect
from pathlib import Path
import subprocess

from evals.golden.manifest import ROOT
from evals.golden.reporting import digest

WORLD_PATH = "backend/evals/golden/world.py"
REVIEWED_WORLD_SHA256 = "e300dd7d0145c5a01cf9125c2abf0df1e235b16425f1ffcbef18dcb01602b78f"


def _revision_sources(revision):
    git = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]
    names = subprocess.check_output([*git, "ls-tree", "-r", "--name-only", revision, "--", "backend/evals"],
                                    cwd=ROOT, stderr=subprocess.DEVNULL, text=True).splitlines()
    names = [name for name in names if name.startswith("backend/evals/") and name.endswith(".py")]
    queries = "".join(f"{revision}:{name}\n" for name in names).encode()
    raw = subprocess.run([*git, "cat-file", "--batch"], input=queries, cwd=ROOT, check=True,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    result, offset = {}, 0
    for name in names:
        end = raw.index(b"\n", offset)
        _, kind, size = raw[offset:end].split()
        if kind != b"blob":
            raise ValueError("Expected historical source blob")
        offset = end + 1
        result[name] = raw[offset:offset + int(size)]
        offset += int(size) + 1
    return result


def historical_fixture_binding(manifest):
    """Missing Git objects or changed evaluator fingerprints confer no semantics."""
    revision = manifest.get("git_commit")
    unknown = {"verified": False, "origin": "historical_manifest_commit", "source_sha256": None}
    if (not isinstance(revision, str) or len(revision) != 40
            or any(c not in "0123456789abcdef" for c in revision)):
        return unknown
    try:
        sources = _revision_sources(revision)
        hashes = {name: hashlib.sha256(content).hexdigest() for name, content in sources.items()}
        if digest(hashes) != manifest.get("evaluator_hash"):
            return {**unknown, "diagnostic": "evaluator_fingerprint_mismatch"}
        source_hash = hashes.get(WORLD_PATH)
        return {**unknown, "verified": source_hash == REVIEWED_WORLD_SHA256,
                "source_sha256": source_hash, "git_commit": revision,
                "evaluator_hash": manifest["evaluator_hash"]}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {**unknown, "diagnostic": "historical_source_unavailable"}


def current_fixture_binding(registry):
    if type(registry).__module__ != "evals.golden.world" or type(registry).__name__ != "FrozenRegistry":
        return {"verified": False, "origin": "current_registry", "source_sha256": None}
    source = Path(inspect.getfile(type(registry)))
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    return {"verified": source_hash == REVIEWED_WORLD_SHA256, "origin": "current_registry",
            "source_sha256": source_hash}


def fixture_semantics(observations, binding):
    if binding.get("verified") is not True or binding.get("source_sha256") != REVIEWED_WORLD_SHA256:
        return []
    results = []
    for index, item in enumerate(observations):
        args, output = item.get("input"), item.get("output")
        if (item.get("status") != "success" or not isinstance(args, dict) or not isinstance(output, dict)
                or output.get("synthetic") is not True or output.get("source") != "synthetic-golden-world-v1"):
            continue
        contract = None
        if (item["name"] == "market_event_pool" and args.get("event_type") == "limit_up"
                and output.get("event_type") == "limit_up" and args.get("result_mode") == "count"
                and output.get("result_mode") == "count" and output.get("items") == []
                and type(output.get("matched_count")) is int and output["matched_count"] >= 0):
            contract = {"event_status": "closed", "count_basis": "matching_fixture_event_rows",
                        "items_omitted_by_count_mode": True}
            if "market" in args and args["market"] is None and "query" in args and args["query"] is None:
                contract["market_scope"] = "all_fixture_market_segments"
        elif item["name"] == "stock_news" and isinstance(output.get("items"), list):
            contract = {"coverage_scope": "synthetic_fixture_only",
                        "external_news_completeness": "not_established",
                        "returned_items_count": len(output["items"])}
        if contract:
            results.append({"tool": item["name"], "observation_index": index,
                "fixture_source_sha256": binding["source_sha256"], "contract": contract})
    return results
