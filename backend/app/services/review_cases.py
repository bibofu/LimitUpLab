"""Aggregate review signals into cases and run the human review state machine."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.database import begin_write, connect, initialize_database
from app.review_models import (
    ReviewCaseDetail,
    ReviewCaseEvent,
    ReviewCaseSummary,
    ReviewCaseUpdate,
    ReviewDetectorReport,
    ReviewSignal,
)
from app.services.review_signals import severity_rank
from app.services.review_snapshot import load_run_snapshot


SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_QUESTION_PREVIEW = 120
MAX_EXPORT_ANSWER = 800
SOURCE_BY_KIND = {
    "downvote": "user_feedback",
    "deterministic": "deterministic",
    "judge": "judge",
    "sample": "sample",
    "run_error": "deterministic",
    "grounding": "deterministic",
    "manual": "manual",
}
SOURCE_PRIORITY = {
    "sample": 0,
    "manual": 1,
    "judge": 2,
    "deterministic": 3,
    "user_feedback": 4,
}
ALLOWED_TRANSITIONS = {
    "new": {"confirmed", "dismissed", "insufficient"},
    "confirmed": {"fixing", "dismissed", "insufficient"},
    "fixing": {"verifying", "confirmed"},
    "verifying": {"closed", "fixing"},
    "closed": set(),
    "dismissed": {"new"},
    "insufficient": {"new"},
}
EXPORTABLE_STATUSES = {"confirmed", "fixing", "verifying", "closed"}
EDITABLE_FIELDS = (
    "severity",
    "symptom",
    "root_cause",
    "fix_commit",
    "fix_version",
    "verification",
)


class ReviewCaseNotFound(KeyError):
    """The requested case does not exist."""


class ReviewCaseConflict(RuntimeError):
    """The submitted revision is older than the stored case."""


class ReviewCaseValidationError(ValueError):
    """The requested transition or required field is invalid."""


class ReviewCaseNotExportable(RuntimeError):
    """The case has not reached a reviewable state."""


class ReviewCaseService:
    """Own case aggregation, optimistic updates and badcase export."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url

    def aggregate_signals(self, limit: int = 50) -> int:
        """Attach unprocessed signals to one case per run."""

        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            signals = db.execute(
                """
                SELECT *
                FROM answer_review_signals
                WHERE processed_at IS NULL
                ORDER BY created_at, signal_id
                LIMIT ?
                FOR UPDATE SKIP LOCKED
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
            processed = 0
            for signal in signals:
                snapshot = load_run_snapshot(db, signal["run_id"])
                if snapshot is None:
                    db.execute(
                        "UPDATE answer_review_signals SET processed_at=clock_timestamp() WHERE signal_id=?",
                        (signal["signal_id"],),
                    )
                    processed += 1
                    continue
                case = db.execute(
                    "SELECT * FROM answer_review_cases WHERE run_id=?",
                    (signal["run_id"],),
                ).fetchone()
                if case is None:
                    case_id = "case_" + uuid4().hex
                    source = SOURCE_BY_KIND.get(signal["kind"], "manual")
                    severity = _case_severity(signal["severity"])
                    db.execute(
                        """
                        INSERT INTO answer_review_cases (
                            case_id, run_id, session_id, owner_id, status, source,
                            severity, intent, task_status, question, answer,
                            evidence_json, trace_url
                        )
                        VALUES (?, ?, ?, ?, 'new', ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            case_id,
                            signal["run_id"],
                            snapshot.session_id,
                            snapshot.owner_id,
                            source,
                            severity,
                            snapshot.intent,
                            snapshot.task_status,
                            snapshot.question,
                            snapshot.answer,
                            json.dumps(snapshot.evidence, ensure_ascii=False),
                            snapshot.trace_url,
                        ),
                    )
                else:
                    case_id = case["case_id"]
                    if case["status"] == "new":
                        source = _preferred_source(case["source"], signal["kind"])
                        severity = _max_severity(case["severity"], signal["severity"])
                        db.execute(
                            """
                            UPDATE answer_review_cases
                            SET source=?, severity=?, updated_at=clock_timestamp()
                            WHERE case_id=?
                            """,
                            (source, severity, case_id),
                        )
                    else:
                        db.execute(
                            "UPDATE answer_review_cases SET updated_at=clock_timestamp() WHERE case_id=?",
                            (case_id,),
                        )
                db.execute(
                    """
                    UPDATE answer_review_signals
                    SET case_id=?, processed_at=clock_timestamp()
                    WHERE signal_id=?
                    """,
                    (case_id, signal["signal_id"]),
                )
                processed += 1
            db.commit()
            return processed

    def list_cases(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        severity: str | None = None,
        limit: int = 50,
    ) -> list[ReviewCaseSummary]:
        """Return the filtered review queue ordered by most recent activity."""

        conditions: list[str] = []
        params: list[Any] = []
        for column, value in (("status", status), ("source", source), ("severity", severity)):
            if value:
                conditions.append(f"case_row.{column} = ?")
                params.append(value)
        where = " AND ".join(conditions) if conditions else "TRUE"
        params.append(max(1, min(limit, 200)))
        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            rows = db.execute(
                f"""
                SELECT case_row.*,
                    (SELECT COUNT(*) FROM answer_review_signals AS signal
                     WHERE signal.case_id = case_row.case_id) AS signal_count,
                    EXISTS(
                        SELECT 1 FROM answer_review_signals AS signal
                        WHERE signal.case_id = case_row.case_id
                          AND signal.created_at > COALESCE(
                              case_row.reviewed_at, case_row.created_at)
                    ) AS has_new_signal
                FROM answer_review_cases AS case_row
                WHERE {where}
                ORDER BY case_row.updated_at DESC, case_row.case_id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [_summary_from_row(row) for row in rows]

    def get_case(self, case_id: str) -> ReviewCaseDetail | None:
        """Return one case with its signals and audit history."""

        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            row = db.execute(
                """
                SELECT case_row.*,
                    (SELECT COUNT(*) FROM answer_review_signals AS signal
                     WHERE signal.case_id = case_row.case_id) AS signal_count,
                    EXISTS(
                        SELECT 1 FROM answer_review_signals AS signal
                        WHERE signal.case_id = case_row.case_id
                          AND signal.created_at > COALESCE(
                              case_row.reviewed_at, case_row.created_at)
                    ) AS has_new_signal
                FROM answer_review_cases AS case_row
                WHERE case_row.case_id = ?
                """,
                (case_id,),
            ).fetchone()
            if row is None:
                return None
            signal_rows = db.execute(
                """
                SELECT *
                FROM answer_review_signals
                WHERE case_id = ?
                ORDER BY created_at, signal_id
                """,
                (case_id,),
            ).fetchall()
            event_rows = db.execute(
                """
                SELECT *
                FROM answer_review_case_events
                WHERE case_id = ?
                ORDER BY created_at, event_id
                """,
                (case_id,),
            ).fetchall()
            report_rows = db.execute(
                """
                SELECT job_id, kind, detector_version, result_json, created_at
                FROM answer_review_jobs
                WHERE run_id = ? AND status = 'succeeded'
                ORDER BY created_at
                """,
                (row["run_id"],),
            ).fetchall() if row["run_id"] else []
        reports = [
            _report_from_row(item)
            for item in report_rows
            if _decode_object(item["result_json"]).get("report")
        ]
        return _detail_from_row(
            row,
            [_signal_from_row(item) for item in signal_rows],
            [_event_from_row(item) for item in event_rows],
            reports,
        )

    def update_case(
        self,
        case_id: str,
        payload: ReviewCaseUpdate,
        *,
        actor: str = "admin",
    ) -> ReviewCaseDetail:
        """Apply one audited update with an optimistic revision check."""

        note = payload.note.strip()
        if not note:
            raise ReviewCaseValidationError("审核说明不能为空")
        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            begin_write(db)
            row = db.execute(
                "SELECT * FROM answer_review_cases WHERE case_id=? FOR UPDATE",
                (case_id,),
            ).fetchone()
            if row is None:
                raise ReviewCaseNotFound(case_id)
            current = dict(row)
            if int(current["revision"]) != payload.revision:
                raise ReviewCaseConflict(
                    f"Case revision {current['revision']} is newer than {payload.revision}"
                )
            target_status = payload.status or current["status"]
            if target_status != current["status"] and target_status not in ALLOWED_TRANSITIONS.get(
                current["status"], set()
            ):
                raise ReviewCaseValidationError(
                    f"不允许从 {current['status']} 变为 {target_status}"
                )
            updates: dict[str, str] = {}
            for field in EDITABLE_FIELDS:
                value = getattr(payload, field)
                if value is not None:
                    updates[field] = value.strip()
            effective = {**current, **updates, "status": target_status}
            _validate_required(effective)
            assignments = [f"{field}=?" for field in updates]
            values: list[Any] = list(updates.values())
            assignments.extend([
                "status=?",
                "revision=revision+1",
                "updated_at=clock_timestamp()",
                "reviewed_at=clock_timestamp()",
            ])
            values.append(target_status)
            if target_status == "closed":
                assignments.append("closed_at=clock_timestamp()")
            values.append(case_id)
            db.execute(
                f"UPDATE answer_review_cases SET {', '.join(assignments)} WHERE case_id=?",
                tuple(values),
            )
            changed = {
                field: {"from": current.get(field), "to": value}
                for field, value in updates.items()
                if current.get(field) != value
            }
            if target_status != current["status"]:
                changed["status"] = {"from": current["status"], "to": target_status}
            db.execute(
                """
                INSERT INTO answer_review_case_events (
                    event_id, case_id, action, from_status, to_status,
                    changes_json, note, actor
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt_" + uuid4().hex,
                    case_id,
                    "status_changed" if target_status != current["status"] else "updated",
                    current["status"],
                    target_status,
                    json.dumps(changed, ensure_ascii=False),
                    note,
                    actor,
                ),
            )
            db.commit()
        detail = self.get_case(case_id)
        if detail is None:
            raise ReviewCaseNotFound(case_id)
        return detail

    def bulk_dismiss(
        self,
        case_ids: list[str],
        note: str,
        *,
        actor: str = "admin",
    ) -> dict[str, int]:
        """Dismiss only cases still awaiting review; skip every other status."""

        explanation = note.strip()
        if not explanation:
            raise ReviewCaseValidationError("审核说明不能为空")
        dismissed = 0
        skipped = 0
        for case_id in dict.fromkeys(case_ids):
            detail = self.get_case(case_id)
            if detail is None or detail.status != "new":
                skipped += 1
                continue
            try:
                self.update_case(
                    case_id,
                    ReviewCaseUpdate(
                        status="dismissed",
                        note=explanation,
                        revision=detail.revision,
                    ),
                    actor=actor,
                )
                dismissed += 1
            except (ReviewCaseConflict, ReviewCaseValidationError):
                skipped += 1
        return {"dismissed": dismissed, "skipped": skipped}

    def export_markdown(self, case_id: str) -> str:
        """Render one confirmed case in the existing badCase.md format."""

        case = self.get_case(case_id)
        if case is None:
            raise ReviewCaseNotFound(case_id)
        if case.status not in EXPORTABLE_STATUSES:
            raise ReviewCaseNotExportable(case.status)
        title = _one_line(case.symptom) or _one_line(case.question_preview) or case.case_id
        date = case.created_at.astimezone(SHANGHAI).date().isoformat()
        answer = _one_line(case.answer)[:MAX_EXPORT_ANSWER]
        lines = [
            f"## {date}：{title}",
            "",
            f"- 原表现：{_one_line(case.question)}；run_id={case.run_id or '已删除'}；"
            f"task_status={case.task_status or '未知'}；intent={case.intent or '未知'}。",
            f"- 回答摘要：{answer or '（空）'}",
            f"- 原因：{case.root_cause or '（待补充）'}",
            f"- 修复：{case.fix_commit or '（未填写提交号）'} / {case.fix_version or '（未填写版本）'}",
            f"- 验证：{case.verification or '（待补充）'}",
            f"- 证据：trace_url={case.trace_url or '未启用'}；来源=react_runs.response_json。",
            "",
            "> 由在线审核台导出，提交前需人工复核并补充真实复验结果。",
        ]
        return "\n".join(lines)


def _case_severity(value: str) -> str:
    return "low" if value == "info" else value


def _preferred_source(current: str, kind: str) -> str:
    incoming = SOURCE_BY_KIND.get(kind, "manual")
    if SOURCE_PRIORITY.get(incoming, 0) > SOURCE_PRIORITY.get(current, 0):
        return incoming
    return current


def _max_severity(current: str, incoming: str) -> str:
    return current if severity_rank(current) >= severity_rank(incoming) else incoming


def _validate_required(case: dict[str, Any]) -> None:
    status = case["status"]
    if status == "confirmed" and not (case.get("symptom") or "").strip():
        raise ReviewCaseValidationError("确认问题前需填写症状")
    if status in {"verifying", "closed"}:
        if not (case.get("root_cause") or "").strip():
            raise ReviewCaseValidationError("进入待验证或关闭前需填写根因")
        if not ((case.get("fix_version") or "").strip() or (case.get("fix_commit") or "").strip()):
            raise ReviewCaseValidationError("进入待验证或关闭前需填写修复版本或提交号")
    if status == "closed" and not (case.get("verification") or "").strip():
        raise ReviewCaseValidationError("关闭前需填写验证说明")


def _summary_from_row(row) -> ReviewCaseSummary:
    return ReviewCaseSummary(
        case_id=row["case_id"],
        run_id=row["run_id"],
        session_id=row["session_id"],
        status=row["status"],
        source=row["source"],
        severity=row["severity"],
        intent=row["intent"],
        task_status=row["task_status"],
        question_preview=_one_line(row["question"])[:MAX_QUESTION_PREVIEW],
        signal_count=int(row["signal_count"] or 0),
        has_new_signal=bool(row["has_new_signal"]),
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
        reviewed_at=_datetime(row["reviewed_at"]) if row["reviewed_at"] else None,
        closed_at=_datetime(row["closed_at"]) if row["closed_at"] else None,
    )


def _detail_from_row(
    row,
    signals: list[ReviewSignal],
    events: list[ReviewCaseEvent],
    reports: list[ReviewDetectorReport],
) -> ReviewCaseDetail:
    return ReviewCaseDetail(
        **_summary_from_row(row).model_dump(),
        question=row["question"],
        answer=row["answer"],
        evidence=_decode_object(row["evidence_json"]),
        trace_url=row["trace_url"],
        symptom=row["symptom"],
        root_cause=row["root_cause"],
        fix_commit=row["fix_commit"],
        fix_version=row["fix_version"],
        verification=row["verification"],
        revision=int(row["revision"]),
        signals=signals,
        events=events,
        reports=reports,
    )


def _signal_from_row(row) -> ReviewSignal:
    return ReviewSignal(
        signal_id=row["signal_id"],
        run_id=row["run_id"],
        case_id=row["case_id"],
        kind=row["kind"],
        severity=row["severity"],
        detector=row["detector"],
        detector_version=row["detector_version"],
        source_ref=row["source_ref"],
        summary=row["summary"],
        details=_decode_object(row["details_json"]),
        created_at=_datetime(row["created_at"]),
        processed_at=_datetime(row["processed_at"]) if row["processed_at"] else None,
    )


def _event_from_row(row) -> ReviewCaseEvent:
    return ReviewCaseEvent(
        event_id=row["event_id"],
        action=row["action"],
        from_status=row["from_status"],
        to_status=row["to_status"],
        changes=_decode_object(row["changes_json"]),
        note=row["note"],
        actor=row["actor"],
        created_at=_datetime(row["created_at"]),
    )


def _report_from_row(row) -> ReviewDetectorReport:
    report = _decode_object(row["result_json"]).get("report")
    return ReviewDetectorReport(
        job_id=row["job_id"],
        kind=row["kind"],
        detector_version=row["detector_version"],
        created_at=_datetime(row["created_at"]),
        report=report if isinstance(report, dict) else {},
    )


def _decode_object(value: str | None) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _one_line(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def _datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)
