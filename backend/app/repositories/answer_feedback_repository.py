"""Owner-scoped like/dislike feedback for one persisted Agent answer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.database import begin_write, connect, get_database_url


class FeedbackInput(BaseModel):
    """One user's verdict on a completed Agent answer."""

    model_config = ConfigDict(extra="forbid")

    vote: Literal["up", "down"]
    reasons: list[str] = Field(default_factory=list, max_length=10)
    comment: str = Field(default="", max_length=1000)


class AnswerFeedbackRepository:
    """Store feedback separately from the retired quality-evaluation system."""

    def __init__(self, database_url: str | None = None):
        self.database_url = get_database_url(database_url)

    def save(self, run_id: str, owner_id: str, payload: FeedbackInput) -> dict:
        data = FeedbackInput.model_validate(payload).model_dump()
        reasons = [item.strip()[:80] for item in data["reasons"] if item.strip()]
        stamp = datetime.now(timezone.utc).isoformat()
        with connect(self.database_url) as db:
            begin_write(db)
            owned = db.execute(
                "SELECT 1 FROM react_runs WHERE run_id=? AND owner_id=?",
                (run_id, owner_id),
            ).fetchone()
            if owned is None:
                raise KeyError("Run not found")
            db.execute(
                """INSERT INTO answer_feedback
                   (run_id,owner_id,vote,reasons_json,comment,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(run_id) DO UPDATE SET
                     vote=excluded.vote, reasons_json=excluded.reasons_json,
                     comment=excluded.comment, updated_at=excluded.updated_at""",
                (run_id, owner_id, data["vote"], json.dumps(reasons, ensure_ascii=False),
                 data["comment"], stamp, stamp),
            )
            row = db.execute("SELECT * FROM answer_feedback WHERE run_id=?", (run_id,)).fetchone()
            return self._decoded(row)

    def get(self, run_id: str, owner_id: str) -> dict | None:
        with connect(self.database_url) as db:
            owned = db.execute(
                "SELECT 1 FROM react_runs WHERE run_id=? AND owner_id=?",
                (run_id, owner_id),
            ).fetchone()
            if owned is None:
                raise KeyError("Run not found")
            row = db.execute("SELECT * FROM answer_feedback WHERE run_id=?", (run_id,)).fetchone()
            return self._decoded(row) if row else None

    @staticmethod
    def _decoded(row) -> dict:
        result = dict(row)
        result["reasons"] = json.loads(result.pop("reasons_json") or "[]")
        return result
