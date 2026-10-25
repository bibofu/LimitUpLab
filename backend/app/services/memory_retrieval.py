"""Owner-scoped, bounded long-term memory retrieval without embeddings.

The result is ephemeral model input. Only its revision/fingerprint may be stored
in graph state, never its values. Read a fresh snapshot before each model call.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

MEMORY_TYPES = frozenset({"preference", "research_context", "research_archive"})


@dataclass(frozen=True)
class MemoryRetrievalResult:
    items: list[dict[str, Any]]
    diagnostics: dict[str, Any]
    owner_revision: int
    context_fingerprint: str


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


def _rank(item: dict, query: str, exact_keys: set | None, topics: set | None) -> int:
    key = str(item.get("key") or "").casefold()
    item_topics = {str(topic).casefold() for topic in item.get("topics", [])}
    if exact_keys is not None:
        return 100 if key in exact_keys else 0
    matches = sum(topic in query for topic in item_topics if topic)
    if topics:
        matches += len(item_topics & topics) * 2
    if item.get("memory_type") == "preference":
        return 20 + matches
    if key and key in query:
        matches += 3
    if not item_topics:
        return 1
    return matches * 10


def retrieve_memories(
    repository, *, owner_id: str, query: str,
    memory_types: tuple[str, ...] = ("preference", "research_context"),
    exact_keys: list[str] | None = None, topics: list[str] | None = None,
    allow_archives: bool = False, max_tokens: int = 1200,
    now: datetime | None = None,
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
    clock = _datetime(now or datetime.now(timezone.utc))
    snapshot = repository.read_context(owner_id=owner_id, memory_types=requested,
                                       exact_keys=exact_keys, topics=topics, limit=100, now=clock)
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
        item_topics = {str(topic).casefold() for topic in item.get("topics", [])}
        if requested_topics and not item_topics.intersection(requested_topics):
            continue
        score = _rank(item, query.casefold(), keys, requested_topics)
        if score:
            candidates.append((score, item))
    candidates.sort(key=lambda pair: (pair[0], int(pair[1].get("revision", 0)),
                                     str(pair[1].get("updated_at") or ""), pair[1]["memory_id"]), reverse=True)
    selected, seen_keys, seen_content, budget_omitted, used = [], set(), set(), 0, 0
    for _, item in candidates:
        kind = item["memory_type"]
        identity = (kind, str(item.get("key") or "").casefold())
        metadata = item.get("metadata") or {}
        content_key = (kind, " ".join(item["value"].split()).casefold(), metadata.get("as_of"))
        if identity in seen_keys or content_key in seen_content:
            continue
        # Only explicitly sourced fields enter the prompt. Ownership, original
        # user commands and arbitrary repository metadata remain out of context.
        view = {"memory_id": item["memory_id"], "key": item.get("key"), "memory_type": kind,
                "value": item["value"], "topics": item.get("topics", []),
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
    # The active selection fingerprint changes after expiry even without a write
    # to owner_revision. Values are deliberately absent from this state marker.
    fingerprint = snapshot.get("context_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("Memory repository must provide the full owner snapshot fingerprint")
    return MemoryRetrievalResult(selected, {
        "memory_hit_count": len(selected), "long_term_memory_tokens": used,
        "memory_token_budget": max_tokens, "memory_candidate_count": len(candidates),
        "memory_budget_omitted_count": budget_omitted, "memory_rejected_count": rejected,
        "memory_owner_revision": revision, "memory_context_fingerprint": fingerprint,
        "memory_retrieval_mode": "typed_exact_topic_v1",
    }, revision, fingerprint)
