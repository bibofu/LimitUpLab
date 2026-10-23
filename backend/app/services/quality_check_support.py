"""Small, pure helpers shared by deterministic evidence checks."""

from time import perf_counter

from app.quality_evaluation_models import CheckResult


EVIDENCE_PATH = "/checkpoint/runtime/evidence"


def budget(deadline):
    if deadline is not None and perf_counter() >= deadline:
        raise TimeoutError("Deterministic evaluation deadline exceeded")


def result(check_id, dimension, verdict, summary, evidence=None, *, reason=None):
    return CheckResult(check_id=check_id, dimension=dimension, verdict=verdict,
                       severity="low" if verdict == "unknown" else "medium",
                       summary=summary, evidence=evidence or {}, unknown_reason=reason)


def unknown(check_id, dimension, reason, evidence=None):
    return result(check_id, dimension, "unknown", "现有证据不足以完成此项检查。", evidence, reason=reason)


def gaps(bundle, *paths):
    """An omitted parent or descendant makes the dependent value incomplete."""
    manifest = bundle.get("manifest") or {}
    found = []
    for item in [*manifest.get("missing", []), *manifest.get("omissions", [])]:
        path = item.get("path", "")
        if isinstance(path, list):
            path = "/" + "/".join(map(str, path))
        if any(path == target or path.startswith(target + "/") or target.startswith(path + "/")
               for target in paths):
            found.append(item)
    return found


def ancestry(records, keys, deadline=None):
    """Return selected evidence plus compute parents; detect cycles without recursion."""
    found, visiting, complete = [], set(), set()
    pending = [(key, False) for key in reversed(keys)]
    while pending:
        budget(deadline)
        key, leaving = pending.pop()
        if leaving:
            visiting.remove(key)
            complete.add(key)
            continue
        if key in visiting:
            return found, "cyclic_evidence_lineage"
        if key in complete:
            continue
        found.append(key)
        visiting.add(key)
        pending.append((key, True))
        record = records.get(key)
        if not isinstance(record, dict):
            continue
        if record.get("tool") == "compute_result":
            payload = record.get("payload")
            operation = payload.get("operation") if isinstance(payload, dict) else None
            if not isinstance(operation, dict):
                return found, "missing_compute_operation"
            parents = [operation.get("evidence_id")]
            if operation.get("operation") in {"intersection", "difference", "union"}:
                parents.append(operation.get("other_id"))
            if any(not isinstance(parent, str) or not parent for parent in parents):
                return found, "missing_compute_parent_identity"
            pending.extend((parent, False) for parent in reversed(parents))
        elif record.get("tool") in {"compare_metrics", "evidence_view"}:
            parents = record.get("sources", [])
            if not parents or any(not isinstance(parent, str) or not parent for parent in parents):
                return found, "missing_derived_parent_identity"
            pending.extend((parent, False) for parent in reversed(parents))
    return found, None
