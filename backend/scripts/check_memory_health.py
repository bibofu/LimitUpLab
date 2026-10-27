"""Read-only health check for long-term memory state after migrations."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import configure_runtime_environment
from app.database import connect_readonly, initialize_database
from app.services.memory_embeddings import (
    embedding_model_name,
    embedding_source_hash,
    memory_embedding_text,
)


def collect_health(db) -> dict:
    """Return counts for structural and lifecycle anomalies; never write."""

    checks = {}
    checks["duplicate_active_identity"] = db.execute("""SELECT count(*) AS count FROM (
        SELECT owner_id,memory_type,memory_key,scope_key FROM long_term_memories
        WHERE status='active' AND deleted_at IS NULL
        GROUP BY owner_id,memory_type,memory_key,scope_key HAVING count(*)>1
    ) AS duplicates""").fetchone()["count"]
    checks["candidates_missing_ttl"] = db.execute("""SELECT count(*) AS count
        FROM long_term_memories WHERE status='candidate' AND candidate_expires_at IS NULL""").fetchone()["count"]
    checks["candidates_expired_pending"] = db.execute("""SELECT count(*) AS count
        FROM long_term_memories WHERE status='candidate'
          AND candidate_expires_at IS NOT NULL AND candidate_expires_at<=clock_timestamp()""").fetchone()["count"]
    checks["active_expired_pending"] = db.execute("""SELECT count(*) AS count
        FROM long_term_memories WHERE status='active' AND deleted_at IS NULL
          AND expires_at IS NOT NULL AND expires_at<=clock_timestamp()""").fetchone()["count"]
    checks["active_delete_candidate_action"] = db.execute("""SELECT count(*) AS count
        FROM long_term_memories WHERE status='active' AND candidate_action='delete'""").fetchone()["count"]
    checks["candidate_delete_missing_target"] = db.execute("""SELECT count(*) AS count
        FROM long_term_memories WHERE status='candidate' AND candidate_action='delete'
          AND supersedes_memory_id IS NULL""").fetchone()["count"]
    checks["legacy_missing_canonical"] = db.execute("""SELECT count(*) AS count
        FROM long_term_memories WHERE canonical_key IS NULL""").fetchone()["count"]
    checks["orphan_memory_relations"] = db.execute("""SELECT count(*) AS count
        FROM memory_relations relation
        LEFT JOIN long_term_memories source ON source.memory_id=relation.from_memory_id
        LEFT JOIN long_term_memories target ON target.memory_id=relation.to_memory_id
        WHERE source.memory_id IS NULL OR target.memory_id IS NULL""").fetchone()["count"]
    checks["negative_use_count"] = db.execute("""SELECT count(*) AS count
        FROM long_term_memories WHERE use_count<0""").fetchone()["count"]
    embedding_rows = db.execute("""SELECT memory_key,value,scope_type,scope_json,structured_json,
            embedding IS NOT NULL AS has_embedding,embedding_model,embedding_source_hash
        FROM long_term_memories WHERE status='active' AND deleted_at IS NULL""").fetchall()
    model = embedding_model_name()
    checks["active_missing_embedding"] = sum(
        1 for row in embedding_rows if not row["has_embedding"]
    )
    checks["active_embedding_model_mismatch"] = sum(
        1 for row in embedding_rows
        if row["has_embedding"] and row["embedding_model"] != model
    )
    checks["active_embedding_source_mismatch"] = sum(
        1 for row in embedding_rows
        if row["has_embedding"] and row["embedding_source_hash"] != embedding_source_hash(
            memory_embedding_text(
                key=row["memory_key"], value=row["value"] or "",
                scope_type=row["scope_type"],
                scope=json.loads(row["scope_json"] or "{}"),
                structured=json.loads(row["structured_json"] or "{}"),
            )
        )
    )
    checks["active_count"] = db.execute("""SELECT count(*) AS count
        FROM long_term_memories WHERE status='active' AND deleted_at IS NULL""").fetchone()["count"]
    checks["candidate_count"] = db.execute("""SELECT count(*) AS count
        FROM long_term_memories WHERE status='candidate' AND deleted_at IS NULL""").fetchone()["count"]
    non_critical = {
        "active_count", "candidate_count", "active_expired_pending",
        "candidates_expired_pending", "active_missing_embedding",
        "active_embedding_model_mismatch", "active_embedding_source_mismatch",
    }
    critical = sum(value for name, value in checks.items() if name not in non_critical)
    return {
        "ok": critical == 0,
        "critical_issues": critical,
        "checks": checks,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit one JSON document")
    args = parser.parse_args(argv)
    configure_runtime_environment()
    with connect_readonly() as db:
        initialize_database(db)
        report = collect_health(db)
    report["checked_at"] = datetime.now(timezone.utc).isoformat()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("long-term memory health: " + ("OK" if report["ok"] else "ISSUES"))
        for name, value in report["checks"].items():
            print(f"  {name}: {value}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
