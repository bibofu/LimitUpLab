"""Verify declared row windows against runtime-owned computation provenance."""


def _ordered(rows, field, descending):
    if field is None:
        return True
    if any(not isinstance(row, dict) or row.get(field) is None for row in rows):
        return False
    try:
        if descending is None:
            return any(rows == sorted(rows, key=lambda row: row[field], reverse=direction)
                       for direction in (False, True))
        return rows == sorted(rows, key=lambda row: row[field], reverse=descending)
    except (TypeError, ValueError):
        return False


def _complete_population(store, key, visited):
    """A full immediate input is not a full population if an ancestor was cut."""
    if key in visited or key not in store.records:
        return False
    record = store.get(key)
    if (store.scope_of(record) != "current_run" or record["result_state"] == "error"
            or record.get("source_truncated")):
        return False
    step = store._selections.get(key)
    if step is None:
        # Records-only checkpoints cannot prove how a computed subset was made.
        return record["tool"] != "compute_result"
    if step["offset"] != 0 or len(record["rows"]) != step["total"]:
        return False
    parents = [step["source"]]
    if step["operation"] in {"intersection", "difference", "union"}:
        parents.extend(record["sources"])
    return all(_complete_population(store, parent, visited | {key}) for parent in set(parents))


def _scope(store, key, visited):
    if key in visited:
        return None
    visited = visited | {key}
    step = store._selections.get(key)
    if step is None:
        return None
    if step["operation"] != "select":
        return None
    source = store.get(step["source"])
    parent = _scope(store, step["source"], visited)
    result = {"offset": step["offset"], "capacity": step["limit"],
              "available": step["total"], "sort_by": step["sort_by"],
              "descending": step["descending"] if step["sort_by"] else None,
              "population": step["source"]}
    # A complete computed set is a valid new ranking population. A capped set
    # cannot be re-ranked/filtered and then presented as a global rank window.
    if _complete_population(store, step["source"], set()):
        if step["sort_by"] is None and parent:
            result.update(sort_by=parent["sort_by"], descending=parent["descending"])
        return result
    # Only an independently verified production prefix establishes global ranks
    # over a truncated raw collection. Sorting an arbitrary fragment cannot.
    prefix = store._ranked_prefixes.get(step["source"])
    if prefix and not step["filtered"] and step["offset"] + step["limit"] <= len(source["rows"]):
        if step["sort_by"] is None or (step["sort_by"] == prefix["sort_by"]
                                        and step["descending"] == prefix["descending"]):
            return {**result, **prefix}
    if parent is None:
        return None
    same_order = step["sort_by"] is None or (
        step["sort_by"] == parent["sort_by"] and step["descending"] == parent["descending"]
    )
    if not same_order:
        return None
    if parent.get("filtered"):
        # Once a window contains holes, later positional slicing uses different
        # indices. Keeping/filtering the entire remaining subset is still valid.
        return parent if step["offset"] == 0 and step["limit"] >= step["total"] else None
    if step["filtered"]:
        # Filtering all rows of an already selected window retains that window,
        # but cannot manufacture a smaller/different rank window.
        if step["offset"] == 0 and step["limit"] >= step["total"]:
            return {**parent, "filtered": step["total"] != len(source["rows"])}
        return None
    return {**parent, "offset": parent["offset"] + step["offset"],
            "capacity": min(step["limit"], max(0, len(source["rows"]) - step["offset"]))}


def row_selection_matches(store, key, *, offset, limit, sort_by=None, descending=None):
    record = store.get(key)
    if store.scope_of(record) != "current_run" or record["result_state"] == "error":
        return False
    rows = record["rows"]
    if len(rows) > limit:
        return False
    proof = _scope(store, key, set())
    if proof is None:
        # An uncut collection can satisfy a maximum row count, but cannot prove
        # a nonzero rank offset. Do not fall back for a capped computed result.
        if record["tool"] == "compute_result" and not _complete_population(store, key, set()):
            return False
        return offset == 0 and _ordered(rows, sort_by, descending)
    if proof["offset"] != offset:
        return False
    remaining = max(0, proof["available"] - offset)
    if proof["capacity"] != limit and not remaining <= min(proof["capacity"], limit):
        return False
    if sort_by is not None:
        if proof["sort_by"] is None:
            return _ordered(store.get(proof["population"])["rows"], sort_by, descending)
        return proof["sort_by"] == sort_by and (descending is None or proof["descending"] == descending)
    return True
