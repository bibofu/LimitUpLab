"""Owner-scoped like/dislike feedback for completed Agent answers."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from app.database import begin_write, connect, initialize_database
from app.models import (
    AnswerFeedbackInput,
    AnswerFeedbackReasonCount,
    AnswerFeedbackRecord,
    AnswerFeedbackSummary,
)


MAX_REASONS = 5
FEEDBACK_WINDOW_MAX_DAYS = 90
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class FeedbackTargetNotFound(KeyError):
    """The run does not exist or does not belong to the caller."""


class FeedbackTargetIncomplete(RuntimeError):
    """The run has not produced a formal answer yet."""


class AnswerFeedbackRepository:
    """Store one current vote per answer without touching the chat path."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url

    def save(
        self,
        run_id: str,
        owner_id: str,
        payload: AnswerFeedbackInput,
    ) -> AnswerFeedbackRecord:
        """Create or replace the caller's verdict for one completed answer."""

        data = AnswerFeedbackInput.model_validate(payload)
        reasons = list(dict.fromkeys(data.reasons))[:MAX_REASONS]
        comment = _clean_comment(data.comment)
        stamp = datetime.now(timezone.utc).isoformat()
        with connect(self.database_url) as connection:
            initialize_database(connection)
            begin_write(connection)
            _require_answer(connection, run_id, owner_id)
            row = connection.execute(
                """
                INSERT INTO answer_feedback (
                    run_id, owner_id, vote, reasons_json, comment, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    vote = excluded.vote,
                    reasons_json = excluded.reasons_json,
                    comment = excluded.comment,
                    updated_at = excluded.updated_at
                RETURNING *
                """,
                (
                    run_id,
                    owner_id,
                    data.vote,
                    json.dumps(reasons, ensure_ascii=False),
                    comment,
                    stamp,
                    stamp,
                ),
            ).fetchone()
        return feedback_record_from_row(row)

    def get(self, run_id: str, owner_id: str) -> AnswerFeedbackRecord | None:
        """Return the caller's current verdict, or null when it has not voted."""

        with connect(self.database_url) as connection:
            initialize_database(connection)
            _require_answer(connection, run_id, owner_id)
            row = connection.execute(
                "SELECT * FROM answer_feedback WHERE run_id = ? AND owner_id = ?",
                (run_id, owner_id),
            ).fetchone()
        return feedback_record_from_row(row) if row is not None else None

    def delete(self, run_id: str, owner_id: str) -> bool:
        """Retract the caller's verdict while leaving the answer intact."""

        with connect(self.database_url) as connection:
            initialize_database(connection)
            begin_write(connection)
            _require_answer(connection, run_id, owner_id)
            result = connection.execute(
                "DELETE FROM answer_feedback WHERE run_id = ? AND owner_id = ?",
                (run_id, owner_id),
            )
            return result.rowcount > 0

    def summary(self, days: int = 7) -> AnswerFeedbackSummary:
        """Aggregate vote ratio, coverage and reasons for one period."""

        window = max(1, min(int(days), FEEDBACK_WINDOW_MAX_DAYS))
        start = datetime.now(timezone.utc) - timedelta(days=window)
        with connect(self.database_url) as connection:
            initialize_database(connection)
            vote_rows = connection.execute(
                """
                SELECT vote, COUNT(*) AS vote_count
                FROM answer_feedback
                WHERE updated_at >= ?
                GROUP BY vote
                """,
                (start,),
            ).fetchall()
            answered = connection.execute(
                """
                SELECT COUNT(*) AS answered_count
                FROM react_runs
                WHERE response_json IS NOT NULL AND started_at >= ?
                """,
                (start.isoformat(),),
            ).fetchone()
            reason_rows = connection.execute(
                """
                SELECT item AS reason, COUNT(*) AS reason_count
                FROM answer_feedback AS feedback,
                     LATERAL jsonb_array_elements_text(feedback.reasons_json::jsonb) AS item
                WHERE feedback.updated_at >= ?
                GROUP BY item
                ORDER BY reason_count DESC, reason ASC
                """,
                (start,),
            ).fetchall()
        counts = {row["vote"]: int(row["vote_count"]) for row in vote_rows}
        up_count = counts.get("up", 0)
        down_count = counts.get("down", 0)
        vote_count = up_count + down_count
        answered_count = int(answered["answered_count"] or 0)
        return AnswerFeedbackSummary(
            days=window,
            period_started_at=start,
            up_count=up_count,
            down_count=down_count,
            vote_count=vote_count,
            answered_count=answered_count,
            like_ratio=_ratio(up_count, vote_count),
            feedback_coverage=_ratio(vote_count, answered_count),
            down_per_100_answers=_ratio(down_count * 100, answered_count),
            reasons=[
                AnswerFeedbackReasonCount(reason=row["reason"], count=int(row["reason_count"]))
                for row in reason_rows
            ],
        )


def _require_answer(connection, run_id: str, owner_id: str) -> None:
    row = connection.execute(
        "SELECT response_json FROM react_runs WHERE run_id = ? AND owner_id = ?",
        (run_id, owner_id),
    ).fetchone()
    if row is None:
        raise FeedbackTargetNotFound(run_id)
    if not row["response_json"]:
        raise FeedbackTargetIncomplete(run_id)


def _clean_comment(value: str) -> str:
    return _CONTROL_CHARACTERS.sub("", value or "").strip()


def feedback_record_from_row(row) -> AnswerFeedbackRecord:
    return AnswerFeedbackRecord(
        run_id=row["run_id"],
        vote=row["vote"],
        reasons=json.loads(row["reasons_json"] or "[]"),
        comment=row["comment"] or "",
        created_at=_as_datetime(row["created_at"]),
        updated_at=_as_datetime(row["updated_at"]),
    )


def _as_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)
