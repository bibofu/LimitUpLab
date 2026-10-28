"""Append-only review signals from user votes and background detectors."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.database import begin_write, connect, initialize_database


FEEDBACK_DETECTOR = "answer_feedback"
FEEDBACK_DETECTOR_VERSION = "v1"
SIGNAL_VERSION = "review-signals-v1"
REASON_LABELS = {
    "data_error": "数据错误",
    "wrong_scope": "日期或对象不对",
    "missing_source": "缺少来源",
    "incomplete": "回答不完整",
    "display": "展示有问题",
    "other": "其他",
}
HIGH_REASONS = {"data_error", "wrong_scope"}
MEDIUM_REASONS = {"missing_source", "incomplete"}


def severity_rank(value: str) -> int:
    """Order severities for case escalation without changing human decisions."""

    return {"info": 0, "low": 1, "medium": 2, "high": 3}.get(value, 0)


class ReviewSignalService:
    """Write bounded evidence rows; callers decide whether to create a case."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url

    def record(
        self,
        *,
        run_id: str,
        kind: str,
        severity: str,
        detector: str,
        detector_version: str,
        source_ref: str,
        summary: str,
        details: dict[str, Any] | None = None,
        expected_owner_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> bool:
        """Insert one signal idempotently; a missing run is not an error."""

        created_at = (occurred_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            where = "run_id = ?"
            params: list[Any] = [run_id]
            if expected_owner_id is not None:
                where += " AND owner_id = ?"
                params.append(expected_owner_id)
            run = db.execute(
                f"SELECT session_id, owner_id FROM react_runs WHERE {where}",
                tuple(params),
            ).fetchone()
            if run is None:
                return False
            result = db.execute(
                """
                INSERT INTO answer_review_signals (
                    signal_id, run_id, session_id, owner_id, kind, severity,
                    detector, detector_version, source_ref, summary, details_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                (
                    "sig_" + uuid4().hex,
                    run_id,
                    run["session_id"],
                    run["owner_id"],
                    kind,
                    severity,
                    detector,
                    detector_version,
                    source_ref,
                    summary[:500],
                    json.dumps(details or {}, ensure_ascii=False),
                    created_at.isoformat(),
                ),
            )
            db.commit()
            return result.rowcount > 0

    def record_downvote(
        self,
        *,
        run_id: str,
        owner_id: str,
        reasons: list[str],
        comment: str,
        feedback_updated_at: datetime | str,
    ) -> bool:
        """Record one downvote exactly once per feedback revision."""

        unique_reasons = list(dict.fromkeys(reasons))[:5]
        labels = [REASON_LABELS.get(item, item) for item in unique_reasons]
        summary = "用户点踩：" + "、".join(labels) if labels else "用户点踩（未填原因）"
        return self.record(
            run_id=run_id,
            kind="downvote",
            severity=downvote_severity(unique_reasons),
            detector=FEEDBACK_DETECTOR,
            detector_version=FEEDBACK_DETECTOR_VERSION,
            source_ref=feedback_source_ref(feedback_updated_at),
            summary=summary,
            details={"reasons": unique_reasons, "comment": (comment or "")[:1000]},
            expected_owner_id=owner_id,
        )

    def backfill_downvotes(self, limit: int = 100) -> int:
        """Turn existing downvotes into signals once, including P1 data."""

        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            rows = db.execute(
                """
                SELECT feedback.run_id, feedback.owner_id, feedback.reasons_json,
                       feedback.comment, feedback.updated_at, run.session_id
                FROM answer_feedback AS feedback
                JOIN react_runs AS run ON run.run_id = feedback.run_id
                WHERE feedback.vote = 'down'
                ORDER BY feedback.updated_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
            existing = db.execute(
                """
                SELECT run_id, source_ref
                FROM answer_review_signals
                WHERE detector = ? AND detector_version = ?
                """,
                (FEEDBACK_DETECTOR, FEEDBACK_DETECTOR_VERSION),
            ).fetchall()
            seen = {(item["run_id"], item["source_ref"]) for item in existing}
            inserted = 0
            for row in rows:
                source_ref = feedback_source_ref(row["updated_at"])
                if (row["run_id"], source_ref) in seen:
                    continue
                reasons = _decode_reasons(row["reasons_json"])
                labels = [REASON_LABELS.get(item, item) for item in reasons]
                summary = "用户点踩：" + "、".join(labels) if labels else "用户点踩（未填原因）"
                db.execute(
                    """
                    INSERT INTO answer_review_signals (
                        signal_id, run_id, session_id, owner_id, kind, severity,
                        detector, detector_version, source_ref, summary, details_json, created_at
                    )
                    VALUES (?, ?, ?, ?, 'downvote', ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        "sig_" + uuid4().hex,
                        row["run_id"],
                        row["session_id"],
                        row["owner_id"],
                        downvote_severity(reasons),
                        FEEDBACK_DETECTOR,
                        FEEDBACK_DETECTOR_VERSION,
                        source_ref,
                        summary[:500],
                        json.dumps(
                            {"reasons": reasons, "comment": (row["comment"] or "")[:1000]},
                            ensure_ascii=False,
                        ),
                        _as_datetime(row["updated_at"]).isoformat(),
                    ),
                )
                inserted += 1
            db.commit()
            return inserted


def downvote_severity(reasons: list[str]) -> str:
    """Map explicit user reasons to the first-review priority."""

    values = set(reasons)
    if values & HIGH_REASONS:
        return "high"
    if values & MEDIUM_REASONS:
        return "medium"
    return "low"


def feedback_source_ref(value: datetime | str) -> str:
    """Keep one stable identity per stored feedback revision."""

    if isinstance(value, datetime):
        value = value.astimezone(timezone.utc).isoformat()
    return "feedback:" + str(value)


def _decode_reasons(value: str | None) -> list[str]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded][:5]


def _as_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)
