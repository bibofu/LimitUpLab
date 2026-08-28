"""SQLite accounting for public Agent usage and estimated LLM cost."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.database import connect, initialize_database
from app.models import AgentUsageRecord, AgentUsageSummary


SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


class SQLiteAgentUsageRepository:
    """Persist one accounting record per accepted Agent request."""

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path

    def start(self, record: AgentUsageRecord) -> None:
        """Insert an accepted request before expensive work starts."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO agent_usage_events (
                    usage_id, run_id, session_id, owner_id, ip_hash, status,
                    model, llm_call_count, failed_llm_call_count,
                    prompt_tokens, completion_tokens, total_tokens,
                    token_usage_complete, planner_prompt_chars,
                    answer_prompt_chars, answer_chars, estimated_cost_usd,
                    started_at, finished_at, duration_ms, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _record_tuple(record),
            )
            connection.commit()
        finally:
            connection.close()

    def finish(self, record: AgentUsageRecord) -> None:
        """Finalize one request with measured LLM and latency data."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                UPDATE agent_usage_events SET
                    run_id = ?, status = ?, model = ?, llm_call_count = ?,
                    failed_llm_call_count = ?, prompt_tokens = ?,
                    completion_tokens = ?, total_tokens = ?,
                    token_usage_complete = ?, planner_prompt_chars = ?,
                    answer_prompt_chars = ?, answer_chars = ?,
                    estimated_cost_usd = ?, finished_at = ?, duration_ms = ?,
                    error_message = ?
                WHERE usage_id = ?
                """,
                (
                    record.run_id,
                    record.status,
                    record.model,
                    record.llm_call_count,
                    record.failed_llm_call_count,
                    record.prompt_tokens,
                    record.completion_tokens,
                    record.total_tokens,
                    int(record.token_usage_complete),
                    record.planner_prompt_chars,
                    record.answer_prompt_chars,
                    record.answer_chars,
                    record.estimated_cost_usd,
                    record.finished_at.isoformat() if record.finished_at else None,
                    record.duration_ms,
                    record.error_message,
                    record.usage_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def record_rejection(self, record: AgentUsageRecord) -> None:
        """Persist a denied request without consuming its daily accepted quota."""

        self.start(record)

    def owner_today(self, owner_id: str) -> AgentUsageSummary:
        """Return today's accepted usage for one anonymous visitor."""

        start = _shanghai_day_start_utc()
        return self._summary(start, owner_id=owner_id)

    def summary(self, days: int = 1) -> AgentUsageSummary:
        """Return aggregate usage for an administrator-selected period."""

        start = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        return self._summary(start)

    def _summary(
        self,
        start: datetime,
        *,
        owner_id: str | None = None,
    ) -> AgentUsageSummary:
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            where = "started_at >= ?"
            params: list[object] = [start.isoformat()]
            if owner_id is not None:
                where += " AND owner_id = ?"
                params.append(owner_id)
            row = connection.execute(
                f"""
                SELECT
                    SUM(CASE WHEN status != 'rejected' THEN 1 ELSE 0 END)
                        AS request_count,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_count,
                    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_count,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count,
                    COALESCE(SUM(llm_call_count), 0) AS llm_call_count,
                    COALESCE(SUM(failed_llm_call_count), 0) AS failed_llm_call_count,
                    SUM(prompt_tokens) AS prompt_tokens,
                    SUM(completion_tokens) AS completion_tokens,
                    SUM(total_tokens) AS total_tokens,
                    SUM(CASE WHEN token_usage_complete = 1 THEN 1 ELSE 0 END)
                        AS token_measured_request_count,
                    COALESCE(SUM(planner_prompt_chars), 0) AS planner_prompt_chars,
                    COALESCE(SUM(answer_prompt_chars), 0) AS answer_prompt_chars,
                    COALESCE(SUM(answer_chars), 0) AS answer_chars,
                    COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
                FROM agent_usage_events
                WHERE {where}
                """,
                tuple(params),
            ).fetchone()
        finally:
            connection.close()
        return AgentUsageSummary(
            period_started_at=start,
            request_count=int(row["request_count"] or 0),
            success_count=int(row["success_count"] or 0),
            error_count=int(row["error_count"] or 0),
            running_count=int(row["running_count"] or 0),
            rejected_count=int(row["rejected_count"] or 0),
            llm_call_count=int(row["llm_call_count"] or 0),
            failed_llm_call_count=int(row["failed_llm_call_count"] or 0),
            prompt_tokens=_optional_int(row["prompt_tokens"]),
            completion_tokens=_optional_int(row["completion_tokens"]),
            total_tokens=_optional_int(row["total_tokens"]),
            token_measured_request_count=int(row["token_measured_request_count"] or 0),
            planner_prompt_chars=int(row["planner_prompt_chars"] or 0),
            answer_prompt_chars=int(row["answer_prompt_chars"] or 0),
            answer_chars=int(row["answer_chars"] or 0),
            estimated_cost_usd=float(row["estimated_cost_usd"] or 0),
        )


def _record_tuple(record: AgentUsageRecord) -> tuple[object, ...]:
    return (
        record.usage_id,
        record.run_id,
        record.session_id,
        record.owner_id,
        record.ip_hash,
        record.status,
        record.model,
        record.llm_call_count,
        record.failed_llm_call_count,
        record.prompt_tokens,
        record.completion_tokens,
        record.total_tokens,
        int(record.token_usage_complete),
        record.planner_prompt_chars,
        record.answer_prompt_chars,
        record.answer_chars,
        record.estimated_cost_usd,
        record.started_at.isoformat(),
        record.finished_at.isoformat() if record.finished_at else None,
        record.duration_ms,
        record.error_message,
    )


def _shanghai_day_start_utc() -> datetime:
    local_now = datetime.now(SHANGHAI_TIMEZONE)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc)


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None
