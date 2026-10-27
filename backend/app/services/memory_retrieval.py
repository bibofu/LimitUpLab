"""Owner-scoped, bounded long-term memory retrieval with optional local embeddings.

Lexical scoring stays authoritative for exact keys; pgvector cosine similarity
widens recall without overriding scope or validity filters. The result is
ephemeral model input. Only its revision/fingerprint may be stored in graph
state, never its values. Read a fresh snapshot before each model call.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
from typing import Any

from app.memory_terms import symbols_in_text, tools_in_text, topics_in_text

MEMORY_TYPES = frozenset({"preference", "research_context", "research_archive"})
VECTOR_MIN_SIMILARITY = 0.50
VECTOR_WEIGHT = 6
_LEXICAL_STOPWORDS = frozenset({"研究", "关注", "哪些", "什么", "怎么", "如何", "情况"})


@dataclass(frozen=True)
class VectorRankingPolicy:
    """Bounded contribution of cosine similarity to lexical retrieval scores."""

    min_similarity: float = VECTOR_MIN_SIMILARITY
    weight: int = VECTOR_WEIGHT


def infer_memory_scope_hint(query: str) -> dict[str, str] | None:
    """Infer one unambiguous scope dimension from the current user question."""

    tools = tools_in_text(query)
    hints = {}
    if len(tools) == 1:
        hints["tool_name"] = next(iter(tools))
    topics = topics_in_text(query)
    if len(topics) == 1:
        hints["topic"] = topics[0]
    symbols = symbols_in_text(query)
    if len(symbols) == 1:
        hints["symbol"] = next(iter(symbols))
    return hints or None


@dataclass(frozen=True)
class MemoryRetrievalResult:
    items: list[dict[str, Any]]
    diagnostics: dict[str, Any]
    owner_revision: int
    context_fingerprint: str
    dependencies: tuple[dict[str, Any], ...] = ()
    dependency_validator: Callable[[list[dict[str, Any]]], bool] | None = None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _archive_valid(item: dict) -> bool:
    metadata = item.get("metadata") or {}
    try:
        date.fromisoformat(str(metadata.get("as_of") or ""))
    except ValueError:
        return False
    return bool(item.get("source_run_id") and metadata.get("source_references")
                and metadata.get("evidence_references") and metadata.get("report_version"))


def _tokens(value: str) -> set[str]:
    """Cheap lexical tokens shared by Chinese bigrams and ASCII words."""

    text = value.casefold()
    words = set(re.findall(r"[a-z0-9]{2,}", text))
    chinese = re.findall(r"[一-鿿]", text)
    bigrams = {"".join(chinese[index:index + 2]) for index in range(len(chinese) - 1)}
    return (words | bigrams) - _LEXICAL_STOPWORDS


def _scope_matches(item: dict, hint: dict | None, query: str = "") -> tuple[bool, bool]:
    """Return (allowed, verified) for a scoped memory.

    Unknown scope dimensions stay eligible but unverified so a later tool
    selection can validate them instead of silently dropping useful memories.
    """

    scope_type = str(item.get("scope_type") or "global")
    if scope_type == "global":
        return True, True
    scope = item.get("scope") or {}
    if not isinstance(scope, dict):
        return False, False
    required = {
        "tool": "tool_name",
        "intent": "intent",
        "topic": "topic",
        "entity": "symbol",
        "condition": "condition",
    }.get(scope_type)
    if not required:
        return True, False
    expected = str(scope.get(required) or "").strip().casefold()
    if scope_type == "condition" and expected and expected in (query or "").casefold():
        return True, True
    if not hint or not hint.get(required):
        return True, False
    actual = str(hint.get(required) or "").strip().casefold()
    return (expected == actual), True


def _rank(item: dict, query: str, exact_keys: set | None, topics: set | None,
          vector_policy: VectorRankingPolicy = VectorRankingPolicy()) -> int:
    key = str(item.get("key") or "").casefold()
    item_topics = {str(topic).casefold() for topic in item.get("topics", [])}
    if exact_keys is not None:
        return 100 if key in exact_keys else 0
    query_tokens = _tokens(query)
    content_tokens = _tokens(str(item.get("value") or ""))
    content_matches = len(query_tokens & content_tokens)
    matches = sum(topic in query for topic in item_topics if topic)
    if topics:
        matches += len(item_topics & topics) * 2
    vector_score = 0
    distance = item.get("vector_distance")
    if isinstance(distance, (int, float)) and 0 <= distance <= 2:
        similarity = max(0.0, 1.0 - float(distance))
        if similarity >= vector_policy.min_similarity:
            vector_score = int(round(similarity * vector_policy.weight))
    if item.get("memory_type") == "preference":
        return 20 + matches + content_matches * 3 + vector_score
    if key and key in query:
        matches += 3
    if not item_topics and not content_matches and not vector_score:
        return 0
    return matches * 10 + content_matches * 3 + vector_score


def retrieve_memories(
    repository, *, owner_id: str, query: str,
    memory_types: tuple[str, ...] = ("preference", "research_context"),
    exact_keys: list[str] | None = None, topics: list[str] | None = None,
    allow_archives: bool = False, max_tokens: int = 1200,
    now: datetime | None = None, scope_hint: dict | None = None,
    vector_policy: VectorRankingPolicy | None = None,
) -> MemoryRetrievalResult:
    """Read current valid records, apply typed relevance, deduplicate and budget.

    Archive recall requires an explicit caller decision AND the archive type;
    ordinary market requests never receive archived observations by default.
    """
    from app.agents.context_builder import estimate_tokens

    if not owner_id or max_tokens < 0:
        raise ValueError("An owner and a non-negative memory budget are required")
    requested = tuple(dict.fromkeys(kind for kind in memory_types if kind in MEMORY_TYPES
                                   and (kind != "research_archive" or allow_archives)))
    ranking_policy = vector_policy or VectorRankingPolicy()
    clock = _datetime(now) if now is not None else None
    query_embedding = None
    vector_status = "disabled"
    from app.services.memory_embeddings import (
        MemoryEmbeddingUnavailable,
        embed_query,
        embeddings_enabled,
    )

    if embeddings_enabled():
        try:
            query_embedding = embed_query(query)
            vector_status = "ready"
        except MemoryEmbeddingUnavailable:
            vector_status = "unavailable"
    snapshot = repository.read_context(
        owner_id=owner_id, memory_types=requested,
        exact_keys=exact_keys, topics=topics, limit=100, now=clock,
        query_embedding=query_embedding,
    )
    clock = _datetime(snapshot.get("as_of")) or clock
    if clock is None:
        raise ValueError("Memory repository must provide its snapshot time")
    revision = int(snapshot.get("owner_revision", 0))
    keys = {str(key).casefold() for key in exact_keys} if exact_keys is not None else None
    requested_topics = {str(topic).casefold() for topic in topics} if topics else None
    candidates, rejected = [], 0
    for record in snapshot.get("items", []):
        item = copy.deepcopy(record)
        kind, expires = item.get("memory_type"), item.get("expires_at")
        expiration = _datetime(expires) if expires else None
        if (item.get("owner_id") != owner_id or kind not in requested or item.get("deleted_at")
                or (expires and (expiration is None or expiration <= clock))
                or (kind == "research_archive" and not _archive_valid(item))
                or not isinstance(item.get("value"), str) or not item.get("memory_id")):
            rejected += 1
            continue
        if item.get("status", "active") != "active":
            rejected += 1
            continue
        allowed, verified = _scope_matches(item, scope_hint, query)
        if not allowed:
            rejected += 1
            continue
        item_topics = {str(topic).casefold() for topic in item.get("topics", [])}
        if requested_topics and not item_topics.intersection(requested_topics):
            continue
        score = _rank(item, query.casefold(), keys, requested_topics, ranking_policy)
        if score:
            candidates.append((score, item, verified))
    candidates.sort(key=lambda pair: (pair[0], int(pair[1].get("revision", 0)),
                                     str(pair[1].get("updated_at") or ""), pair[1]["memory_id"]), reverse=True)
    exception_groups = {
        (item["memory_type"],
         str(item.get("canonical_key") or item.get("key") or "").casefold())
        for _, item, verified in candidates
        if verified and str(item.get("scope_type") or "global") != "global"
    }
    selected, seen_keys, seen_content, budget_omitted, used = [], set(), set(), 0, 0
    for _, item, verified in candidates:
        kind = item["memory_type"]
        group = (kind, str(item.get("canonical_key") or item.get("key") or "").casefold())
        scope_identity = str(item.get("scope_key") or "global") if group in exception_groups else "global"
        identity = (kind, str(item.get("key") or "").casefold(), scope_identity)
        metadata = item.get("metadata") or {}
        content_key = (kind, " ".join(item["value"].split()).casefold(), metadata.get("as_of"))
        if identity in seen_keys or content_key in seen_content:
            continue
        # Only explicitly sourced fields enter the prompt. Ownership, original
        # user commands and arbitrary repository metadata remain out of context.
        view = {"memory_id": item["memory_id"], "key": item.get("key"), "memory_type": kind,
                "value": item["value"], "topics": item.get("topics", []),
                "scope_type": item.get("scope_type", "global"),
                "scope": item.get("scope", {}),
                "scope_key": item.get("scope_key", "global"),
                "canonical_key": item.get("canonical_key"),
                "structured": item.get("structured", {}),
                "scope_verified": verified,
                "vector_similarity": (
                    round(max(0.0, 1.0 - float(item["vector_distance"])), 4)
                    if isinstance(item.get("vector_distance"), (int, float)) else None
                ),
                "revision": int(item.get("revision", 0)), "expires_at": expires,
                "source_run_id": item.get("source_run_id"), "current_market_evidence": False,
                "historical": kind == "research_archive"}
        if kind == "research_archive":
            view["archive_source"] = {key: copy.deepcopy(metadata[key]) for key in (
                "as_of", "source_references", "evidence_references", "report_version")}
        seen_keys.add(identity)
        seen_content.add(content_key)
        cost = estimate_tokens(view)
        if used + cost > max_tokens:
            budget_omitted += 1
            continue
        selected.append(view)
        used += cost
    exception_suppressed = 0
    verified_exception_groups = {
        (view["memory_type"],
         str(view.get("canonical_key") or view.get("key") or "").casefold())
        for view in selected
        if view.get("scope_verified") and view.get("scope_type") != "global"
    }
    if verified_exception_groups:
        filtered = [
            view for view in selected
            if not (
                view.get("scope_type") == "global"
                and (view["memory_type"],
                     str(view.get("canonical_key") or view.get("key") or "").casefold())
                in verified_exception_groups
            )
        ]
        exception_suppressed = len(selected) - len(filtered)
        if exception_suppressed:
            selected = filtered
            used = sum(estimate_tokens(view) for view in selected)
    # The active selection fingerprint changes after expiry even without a write
    # to owner_revision. Values are deliberately absent from this state marker.
    fingerprint = snapshot.get("context_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("Memory repository must provide the full owner snapshot fingerprint")
    dependencies = tuple({
        "memory_id": item["memory_id"],
        "effective_revision": int(item["revision"]),
        "scope_key": item.get("scope_key", "global"),
    } for item in selected)
    checker = getattr(repository, "dependencies_unchanged", None)
    validator = (
        (lambda deps: bool(checker(owner_id, deps)))
        if callable(checker) else None
    )
    return MemoryRetrievalResult(selected, {
        "memory_hit_count": len(selected), "long_term_memory_tokens": used,
        "memory_token_budget": max_tokens, "memory_candidate_count": len(candidates),
        "memory_budget_omitted_count": budget_omitted, "memory_rejected_count": rejected,
        "memory_exception_suppressed_count": exception_suppressed,
        "memory_vector_status": vector_status,
        "memory_vector_hit_count": sum(
            1 for item in selected if item.get("vector_similarity") is not None
        ),
        "memory_owner_revision": revision, "memory_context_fingerprint": fingerprint,
        "memory_dependency_count": len(dependencies),
        "memory_retrieval_mode": "typed_exact_topic_v1",
    }, revision, fingerprint, dependencies, validator)
