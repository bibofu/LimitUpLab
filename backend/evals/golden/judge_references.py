"""Server-owned, snapshot-scoped references; valid locations do not prove claims.

Only four evidence roots enter the catalog. IDs bind the entire canonical snapshot
and an existing path, so equal snapshots share IDs and changed snapshots do not.
No model-supplied path or value is accepted. Limits fail explicitly, never truncate.
"""

from base64 import urlsafe_b64encode
from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Mapping

from pydantic import Field, StrictStr

from evals.golden.contracts import StrictModel


ROOTS = ("synthetic_evidence", "business_observations", "trusted_runtime_metadata", "source_equivalence")


class CatalogReference(StrictModel):
    ref_id: StrictStr = Field(min_length=1, max_length=64,
        description="从本阶段 evidence_catalog 选择实际存在的 ref_id；不要提交路径、原值或自行构造 ID。")


class ReferenceResolutionError(ValueError):
    def __init__(self, code, *, index=None, reported_refs=None):
        super().__init__(code)  # Never put candidate content in diagnostic messages.
        self.code, self.index, self.reported_refs = code, index, reported_refs


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _reference_id(snapshot_digest, path):
    # 96 bits, with collision detection below; fixed-length IDs keep wire overhead bounded.
    return urlsafe_b64encode(hashlib.sha256(snapshot_digest + b"\0" + _json_bytes(path)).digest()[:12]).decode("ascii")


def _wire_entries(paths):
    groups = {}
    for ref_id, path in paths.items():
        prefix = path[:-1]
        group = groups.setdefault(prefix, {"prefix": list(prefix), "entries": []})
        group["entries"].append({"ref_id": ref_id, "key": path[-1]})
    return list(groups.values())


@dataclass(frozen=True, slots=True)
class EvidenceCatalog:
    _snapshot: bytes
    _paths: Mapping[str, tuple[str | int, ...]]

    @classmethod
    def from_payload(cls, payload, *, max_entries=8192, max_depth=24, max_bytes=2 * 1024 * 1024):
        """Freeze JSON roots; bound indexed paths, traversal, snapshot and wire bytes."""
        if type(payload) is not dict:
            raise ReferenceResolutionError("CatalogInvalidPayload")
        if (type(max_entries) is not int or max_entries < 1 or type(max_depth) is not int
                or not 3 <= max_depth <= 24 or type(max_bytes) is not int or max_bytes < 1):
            raise ValueError("Invalid evidence catalog limits")
        paths, active, visited = [], set(), 0

        def visit(value, path):
            nonlocal visited
            visited += 1
            if len(path) > max_depth or visited > max_entries * 4 + 16:
                raise ReferenceResolutionError("CatalogLimitExceeded")
            if len(path) >= 3:
                paths.append(tuple(path))
                if len(paths) > max_entries:
                    raise ReferenceResolutionError("CatalogLimitExceeded")
            if type(value) in (dict, list):
                if id(value) in active:
                    raise ReferenceResolutionError("CatalogInvalidPayload")
                active.add(id(value))
                if type(value) is dict:
                    if any(type(key) is not str for key in value):
                        raise ReferenceResolutionError("CatalogInvalidPayload")
                    children = ((key, value[key]) for key in sorted(value))
                else:
                    children = enumerate(value)
                for key, child in children:
                    visit(child, [*path, key])
                active.remove(id(value))
            elif type(value) not in (str, int, float, bool, type(None)) or type(value) is float and not math.isfinite(value):
                raise ReferenceResolutionError("CatalogInvalidPayload")

        selected = {root: payload[root] for root in ROOTS if root in payload}
        for root, value in selected.items():
            visit(value, [root])
        try:
            snapshot = _json_bytes(selected)
        except (UnicodeError, ValueError, TypeError, RecursionError) as error:
            raise ReferenceResolutionError("CatalogInvalidPayload") from error
        if len(snapshot) > max_bytes:
            raise ReferenceResolutionError("CatalogLimitExceeded")
        snapshot_digest = hashlib.sha256(snapshot).digest()
        references = {}
        for path in paths:
            ref_id = _reference_id(snapshot_digest, path)
            if ref_id in references:
                raise ReferenceResolutionError("CatalogReferenceCollision")
            references[ref_id] = path
        # Bound grouped wire bytes incrementally, including repeated long prefixes.
        wire_bytes = 0
        for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(_wire_entries(references)):
            wire_bytes += len(chunk.encode("utf-8"))
            if wire_bytes > max_bytes:
                raise ReferenceResolutionError("CatalogLimitExceeded")
        return cls(snapshot, MappingProxyType(references))

    def wire_entries(self):
        """Each group's prefix + [entry.key] is its exact existing evidence path.

        Grouped parents avoid repeating long paths; every container/leaf remains
        addressable. The model selects only ref_id, never key or prefix.
        """
        return _wire_entries(self._paths)

    def resolve(self, references):
        """Return fresh exact values, or one stable error retaining JSON wire refs.

        Successful callers retain their reported refs alongside resolved refs in the
        report. Non-JSON Python objects have no safe wire representation and retain None.
        """
        try:
            reported = json.loads(_json_bytes(references))
        except (UnicodeError, ValueError, TypeError, RecursionError):
            reported = None
        if type(references) is not list:
            raise ReferenceResolutionError("InvalidEvidenceReferenceList", reported_refs=reported)
        paths = []
        for index, reference in enumerate(references):
            if (type(reference) is not dict or set(reference) != {"ref_id"}
                    or type(reference["ref_id"]) is not str or not 1 <= len(reference["ref_id"]) <= 64):
                raise ReferenceResolutionError("InvalidEvidenceReference", index=index, reported_refs=reported)
            path = self._paths.get(reference["ref_id"])
            if path is None:
                raise ReferenceResolutionError("UnknownEvidenceReference", index=index, reported_refs=reported)
            paths.append(path)
        # Each returned value is independent, including repeated or overlapping refs.
        snapshot = json.loads(self._snapshot)
        result = []
        for path in paths:
            value = snapshot
            for part in path:
                value = value[part]
            result.append({"path": list(path), "value": json.loads(_json_bytes(value))})
        return result
