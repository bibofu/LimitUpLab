"""Evaluate long-term memory retrieval quality against a local BGE model.

The dataset is synthetic. It measures contract behavior and recall, not live LLM
answer quality. All retrieval uses the same production ranking function; the
only substitutions are an in-memory snapshot and precomputed memory vectors.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import configure_runtime_environment
from app.memory_models import memory_scope_key
from app.services import memory_embeddings
from app.services.memory_embeddings import (
    embed_texts,
    embedding_model_name,
    embeddings_enabled,
    memory_embedding_text,
)
from app.services.memory_retrieval import (
    VECTOR_MIN_SIMILARITY,
    VECTOR_WEIGHT,
    VectorRankingPolicy,
    retrieve_memories,
)

DEFAULT_DATASET = Path(__file__).resolve().parents[1] / "evals" / "memory_retrieval_cases.json"
AS_OF = "2026-10-28T00:00:00+00:00"


def load_cases(path: Path = DEFAULT_DATASET) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("Unsupported memory retrieval evaluation dataset version")
    return data


def _scope_key(item: dict) -> str:
    return memory_scope_key(
        str(item.get("scope_type") or "global"),
        item.get("scope") or {},
    )


def build_pool_items(pool: list[dict], *, owner: str, foreign_owner: str) -> list[dict]:
    items = []
    for record in pool:
        item = {
            "owner_id": foreign_owner if record.get("owner") == "foreign" else owner,
            "memory_type": record["memory_type"],
            "key": record["key"],
            "value": record["value"],
            "topics": [],
            "metadata": {},
            "revision": 1,
            "source_run_id": None,
            "expires_at": None,
            "deleted_at": None,
            "status": "active",
            "updated_at": AS_OF,
            "scope_type": "global",
            "scope": {},
            "canonical_key": record["key"],
            "structured": {},
            **record,
        }
        item["scope_key"] = _scope_key(item)
        items.append(item)
    return items


def build_memory_vectors(items: list[dict], *, embedder=embed_texts) -> dict[str, list[float]]:
    vectors = {}
    for item in items:
        text = memory_embedding_text(
            key=item["key"], value=item.get("value") or "",
            scope_type=item.get("scope_type") or "global",
            scope=item.get("scope") or {},
            structured=item.get("structured") or {},
        )
        vectors[item["memory_id"]] = embedder([text])[0]
    return vectors


def _distance(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 1.0
    return max(0.0, min(2.0, 1.0 - dot / (left_norm * right_norm)))


class SnapshotRepository:
    """In-memory equivalent of read_context's visibility and filtering rules."""

    def __init__(self, items: list[dict], vectors: dict[str, list[float]]):
        self.items = [deepcopy(item) for item in items]
        self.vectors = vectors

    def read_context(self, owner_id, *, memory_types=None, exact_keys=None, topics=None,
                     limit=100, now=None, query_embedding=None):
        timestamp = AS_OF
        selected = [item for item in self.items
                    if item["owner_id"] == owner_id
                    and item.get("status", "active") == "active"
                    and not item.get("deleted_at")
                    and (not item.get("expires_at") or item["expires_at"] > timestamp)
                    and (not memory_types or item["memory_type"] in memory_types)]
        if exact_keys is not None:
            keys = set(exact_keys)
            selected = [item for item in selected if item["key"] in keys]
        if topics:
            topic_set = set(topics)
            selected = [item for item in selected if topic_set.intersection(item.get("topics", []))]
        selected.sort(key=lambda item: (str(item.get("updated_at") or ""), item["memory_id"]), reverse=True)
        selected = selected[:limit]
        for item in selected:
            vector = self.vectors.get(item["memory_id"])
            item["vector_distance"] = (
                _distance(query_embedding, vector)
                if query_embedding is not None and vector is not None else None
            )
        identity = [[item["memory_id"], item["revision"]] for item in selected]
        return {
            "items": selected,
            "owner_revision": 1,
            "context_fingerprint": sha256(
                json.dumps(identity, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
            "as_of": timestamp,
        }

    def dependencies_unchanged(self, owner_id, dependencies, now=None):
        return True


def evaluate_case(repository, owner: str, case: dict, policy: VectorRankingPolicy | None,
                  *, use_vectors: bool) -> dict:
    with patch.object(memory_embeddings, "embeddings_enabled", return_value=use_vectors):
        result = retrieve_memories(
            repository,
            owner_id=owner,
            query=case["query"],
            scope_hint=case.get("scope_hint"),
            vector_policy=policy,
            max_tokens=2000,
        )
    selected = [item["memory_id"] for item in result.items]
    expected = list(case.get("expect") or [])
    forbidden = list(case.get("forbid") or [])
    expected_hits = [memory_id for memory_id in expected if memory_id in selected]
    forbidden_hits = [memory_id for memory_id in forbidden if memory_id in selected]
    first_rank = next((index + 1 for index, memory_id in enumerate(selected)
                       if memory_id in expected), None)
    return {
        "id": case["id"],
        "selected": selected,
        "expected_hits": expected_hits,
        "forbidden_hits": forbidden_hits,
        "has_forbid": bool(forbidden),
        "expected_recall": len(expected_hits) / len(expected) if expected else 1.0,
        "first_rank": first_rank,
        "exact": set(expected_hits) == set(expected) and not forbidden_hits,
        "tokens": result.diagnostics["long_term_memory_tokens"],
        "vector_status": result.diagnostics["memory_vector_status"],
    }


def aggregate(rows: list[dict]) -> dict:
    expected_rows = [row for row in rows if row["expected_hits"] or row["expected_recall"] < 1.0]
    positive_rows = [row for row in rows if row["first_rank"] is not None or row["expected_recall"] < 1.0]
    return {
        "cases": len(rows),
        "exact_success": sum(row["exact"] for row in rows),
        "mean_recall": (
            sum(row["expected_recall"] for row in expected_rows) / len(expected_rows)
            if expected_rows else 1.0
        ),
        "mrr": (
            sum(1.0 / row["first_rank"] for row in positive_rows if row["first_rank"])
            / len(positive_rows)
            if positive_rows else 1.0
        ),
        "forbidden_checked_cases": sum(row["has_forbid"] for row in rows),
        "forbidden_hit_cases": sum(1 for row in rows if row["forbidden_hits"]),
        "forbidden_hits": sum(len(row["forbidden_hits"]) for row in rows),
        "mean_selected": sum(len(row["selected"]) for row in rows) / len(rows),
        "mean_tokens": sum(row["tokens"] for row in rows) / len(rows),
    }


def policy_label(policy: VectorRankingPolicy | None, use_vectors: bool) -> str:
    if not use_vectors:
        return "lexical"
    return f"hybrid(sim>={policy.min_similarity:.2f},weight={policy.weight})"


def run(data: dict, *, policies: list[VectorRankingPolicy],
        include_lexical: bool = True, details: bool = False) -> list[dict]:
    owner = data["owner"]
    foreign_owner = data["foreign_owner"]
    pools = {
        name: build_pool_items(records, owner=owner, foreign_owner=foreign_owner)
        for name, records in data["pools"].items()
    }
    all_items = [item for items in pools.values() for item in items]
    vectors = build_memory_vectors(all_items)
    repositories = {name: SnapshotRepository(items, vectors) for name, items in pools.items()}
    report = []
    settings = ([None] if include_lexical else []) + policies
    for policy in settings:
        use_vectors = policy is not None
        rows = [
            evaluate_case(repositories[case["pool"]], owner, case, policy,
                          use_vectors=use_vectors)
            for case in data["cases"]
        ]
        report.append({
            "mode": policy_label(policy, use_vectors),
            "policy": None if policy is None else {
                "min_similarity": policy.min_similarity,
                "weight": policy.weight,
            },
            "metrics": aggregate(rows),
            "cases": rows if details else None,
        })
    return report


def _print_report(report: list[dict]) -> None:
    for entry in report:
        metrics = entry["metrics"]
        print(
            f"{entry['mode']:<38} exact={metrics['exact_success']}/{metrics['cases']} "
            f"recall={metrics['mean_recall']:.3f} mrr={metrics['mrr']:.3f} "
            f"false_inject={metrics['forbidden_hit_cases']}/{metrics['forbidden_checked_cases']} "
            f"tokens={metrics['mean_tokens']:.0f}"
        )
        if entry.get("cases"):
            for row in entry["cases"]:
                if not row["exact"] or row["forbidden_hits"]:
                    print(json.dumps(row, ensure_ascii=False))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--grid", action="store_true", help="Scan thresholds and weights.")
    parser.add_argument("--threshold", type=float, action="append")
    parser.add_argument("--weight", type=int, action="append")
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        data = load_cases(args.dataset)
    except (OSError, ValueError) as error:
        print(f"Dataset error: {error}", file=sys.stderr)
        return 2

    if args.grid:
        thresholds = args.threshold or [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
        weights = args.weight or [6, 12, 18]
        policies = [VectorRankingPolicy(threshold, weight)
                    for threshold in thresholds for weight in weights]
    else:
        thresholds = args.threshold or [VECTOR_MIN_SIMILARITY]
        weights = args.weight or [VECTOR_WEIGHT]
        policies = [VectorRankingPolicy(threshold, weight)
                    for threshold in thresholds for weight in weights]

    configure_runtime_environment()
    if not embeddings_enabled():
        print("Embeddings are disabled; set LIMITUPLAB_MEMORY_EMBEDDINGS=on.",
              file=sys.stderr)
        return 1
    print(f"Embedding model: {embedding_model_name()}", file=sys.stderr)
    report = run(data, policies=policies, include_lexical=True,
                 details=args.details)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
