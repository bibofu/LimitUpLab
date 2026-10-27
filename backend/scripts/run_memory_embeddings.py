"""Backfill local BGE embeddings for active long-term memories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import configure_runtime_environment
from app.repositories.long_term_memory_repository import LongTermMemoryRepository
from app.services.memory_embeddings import (
    embedding_model_name,
    embedding_source_hash,
    embeddings_enabled,
    embed_texts,
    memory_embedding_text,
)
from app.services.runtime_coordination import LeaseStore


def process_batch(repository, *, limit=100) -> int:
    rows = repository.embedding_backfill_rows(limit=limit)
    pending = []
    for row in rows:
        text = memory_embedding_text(
            key=row["memory_key"], value=row["value"] or "",
            scope_type=row["scope_type"],
            scope=json.loads(row["scope_json"] or "{}"),
            structured=json.loads(row["structured_json"] or "{}"),
        )
        source_hash = embedding_source_hash(text)
        if (row.get("has_embedding")
                and row.get("embedding_source_hash") == source_hash
                and row.get("embedding_model") == embedding_model_name()):
            continue
        pending.append((row, text, source_hash))
    if not pending:
        return 0
    vectors = embed_texts([item[1] for item in pending])
    model = embedding_model_name()
    updated = 0
    for (row, _, source_hash), vector in zip(pending, vectors):
        if repository.save_embedding(
            row["owner_id"], row["memory_id"], vector, model, source_hash,
        ):
            updated += 1
    return updated


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args(argv)
    configure_runtime_environment()
    if not embeddings_enabled():
        print(json.dumps({"embeddings": "disabled"}, ensure_ascii=False))
        return 0
    lease = LeaseStore().claim("job:memory-embeddings", ttl=300)
    if lease is None:
        print(json.dumps({"embeddings": "already_running"}, ensure_ascii=False))
        return 0
    lease.start_heartbeat()
    repository = LongTermMemoryRepository()
    total = 0
    try:
        while not lease.lost.is_set():
            updated = process_batch(repository, limit=max(1, min(args.limit, 500)))
            total += updated
            print(json.dumps({"updated": updated, "total": total}, ensure_ascii=False),
                  flush=True)
            if args.once:
                return 0
            sleep(max(2.0, args.interval) if updated else max(5.0, args.interval))
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        lease.release()


if __name__ == "__main__":
    raise SystemExit(main())
