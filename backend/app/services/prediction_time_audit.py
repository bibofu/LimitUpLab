"""Non-destructive time audit annotations for predictions and saved reviews."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, time, timezone
from types import SimpleNamespace

from app.services.prediction_time import CN_TZ, assess_prediction_time


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def audit_prediction_times(connection: sqlite3.Connection, *, apply: bool = False) -> dict:
    """Annotate original artifacts; never rewrite predictions or saved reports."""
    connection.row_factory = sqlite3.Row
    batches = defaultdict(list)
    for row in connection.execute("SELECT * FROM agent_predictions"):
        item = dict(row)
        key = (item["trade_date"], item["prediction_source"], item["scoring_version"], item["created_at"])
        batches[key].append(item)
    annotations = []
    summaries = []
    invalid_identities = set()
    for key, rows in sorted(batches.items()):
        first = rows[0]
        model = SimpleNamespace(
            prediction_source=first["prediction_source"], scoring_version=first["scoring_version"],
            trade_date=datetime.fromisoformat(first["trade_date"]).date(),
            data_as_of=datetime.fromisoformat(first["data_as_of"]).date(),
            created_at=datetime.fromisoformat(first["created_at"]),
            prediction_provenance=json.loads(first.get("provenance_json") or "{}"),
        )
        verdict = assess_prediction_time(model)
        summary = {"trade_date": key[0], "source": key[1], "scoring_version": key[2],
                   "created_at": key[3], "count": len(rows), **verdict.to_dict()}
        summaries.append(summary)
        original = json.dumps(sorted(rows, key=lambda x: x["prediction_id"]), sort_keys=True)
        annotations.append(("prediction_batch", json.dumps(key), content_hash(original), summary))
        if not verdict.research_eligible:
            invalid_identities.update((r["trade_date"], r["symbol"], r["score"], r["data_as_of"]) for r in rows)
    affected_reviews = []
    for row in connection.execute("SELECT as_of_date, report_json FROM daily_review_snapshots"):
        report = json.loads(row["report_json"])
        affected = [p for p in report.get("reviewed_picks", []) if
                    (p["trade_date"], p["symbol"], p["score"], p["data_as_of"]) in invalid_identities]
        verdict = {"status": "excluded" if affected else "legacy_report",
                   "affected_prediction_count": len(affected),
                   "affected_base_dates": sorted({p["trade_date"] for p in affected})}
        annotations.append(("review", row["as_of_date"], content_hash(row["report_json"]), verdict))
        if affected:
            affected_reviews.append({"as_of_date": row["as_of_date"], **verdict})
    finals = []
    for row in connection.execute("SELECT * FROM recommendation_prediction_finals"):
        captured = datetime.fromisoformat(row["finalized_at"])
        valid = captured.tzinfo is not None
        local = captured.astimezone(CN_TZ)
        valid = valid and local.date().isoformat() == row["target_trade_date"] and time(9) <= local.time() < time(9, 30)
        verdict = {"status": "time_window_only" if valid else "excluded",
                   "target_trade_date": row["target_trade_date"], "finalized_at": row["finalized_at"],
                   "reason": "input_provenance_requires_verification" if valid else "outside_preopen_window"}
        finals.append(verdict)
        annotations.append(("recommendation_final", row["target_trade_date"], content_hash(row["response_json"]), verdict))
    if apply:
        now = datetime.now(timezone.utc).isoformat()
        connection.executemany("""
            INSERT OR IGNORE INTO prediction_time_audits
            (artifact_type, artifact_key, content_hash, verdict_json, audited_at)
            VALUES (?, ?, ?, ?, ?)
        """, [(kind, key, digest, json.dumps(verdict, ensure_ascii=False), now)
              for kind, key, digest, verdict in annotations])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "applied": apply,
        "row_count": sum(s["count"] for s in summaries),
        "cohort_rows": dict(Counter({cohort: sum(s["count"] for s in summaries if s["cohort"] == cohort)
                                      for cohort in {s["cohort"] for s in summaries}})),
        "batches": summaries, "affected_reviews": affected_reviews, "finals": finals,
    }
