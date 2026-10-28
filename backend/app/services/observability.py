"""SQL-backed operational summary for the hidden administrator dashboard."""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from app.database import connect, initialize_database
from app.observability_models import (
    ObservabilityBucket,
    ObservabilityReasonCount,
    ObservabilitySummary,
    ObservabilityTotals,
)
from app.services.review_time import SHANGHAI


MAX_WINDOW_DAYS = 90
NOTE_TEXT = (
    "使用量按匿名 cookie 去重，清除 cookie 视为新访客。",
    "token 仅统计测量完整的请求，不完整条数单独展示。",
    "成本为按明确单价计算的估算值，未配置价格时为 0。",
    "点赞率分母为已投票数；反馈覆盖率为投票数除以回答量。",
    "Judge 失败率在小样本下仅供线索，不作为发布门禁。",
    "回答量只统计已落库且包含正式回答的 run，排除拒绝请求。",
)


class ObservabilityService:
    """Aggregate traffic, feedback, review and judge facts for one period."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url

    def summary(self, days: int = 7) -> ObservabilitySummary:
        window = max(1, min(int(days), MAX_WINDOW_DAYS))
        now = datetime.now(timezone.utc)
        start = _day_start_utc(now) - timedelta(days=window - 1)
        end = now
        days_list = [
            (start.astimezone(SHANGHAI).date() + timedelta(days=index))
            for index in range(window)
        ]
        buckets = {
            item.isoformat(): ObservabilityBucket(date=item) for item in days_list
        }
        with closing(connect(self.database_url)) as db:
            initialize_database(db)
            usage = db.execute(_USAGE_BUCKETS, (start, end)).fetchall()
            audience = db.execute(_AUDIENCE_TOTALS, (start, end)).fetchone()
            runs = db.execute(_RUN_BUCKETS, (start, end)).fetchall()
            feedback = db.execute(_FEEDBACK_BUCKETS, (start, end)).fetchall()
            cases = db.execute(_CASE_BUCKETS, (start, end)).fetchall()
            reasons = db.execute(_REASON_COUNTS, (start, end)).fetchall()
            status_counts = db.execute(_CASE_STATUS_COUNTS, (start, end)).fetchall()
            source_counts = db.execute(_CASE_SOURCE_COUNTS, (start, end)).fetchall()
            job_status = db.execute(_JOB_STATUS_COUNTS, (start, end)).fetchall()
            job_kind = db.execute(_JOB_KIND_COUNTS, (start, end)).fetchall()
            judge = db.execute(_JUDGE_TOTALS, (start, end)).fetchone()
            latency = db.execute(_LATENCY_TOTALS, (start, end)).fetchone()
            mtta = db.execute(_MTTA, (start, end)).fetchone()
            mttr = db.execute(_MTTR, (start, end)).fetchone()

        _merge_usage(buckets, usage)
        _merge_runs(buckets, runs)
        _merge_feedback(buckets, feedback)
        _merge_cases(buckets, cases)
        bucket_list = [buckets[item.isoformat()] for item in days_list]
        totals = _totals(bucket_list, audience, judge, latency, mtta, mttr)
        status_map = {row["status"]: int(row["count"]) for row in status_counts}
        totals.case_count = sum(status_map.values())
        totals.open_case_count = sum(
            status_map.get(name, 0)
            for name in ("new", "confirmed", "fixing", "verifying")
        )
        totals.confirmed_case_count = sum(
            status_map.get(name, 0)
            for name in ("confirmed", "fixing", "verifying", "closed")
        )
        totals.closed_case_count = status_map.get("closed", 0)
        return ObservabilitySummary(
            days=window,
            period_started_at=start,
            generated_at=now,
            totals=totals,
            buckets=bucket_list,
            feedback_reasons=[
                ObservabilityReasonCount(reason=row["reason"], count=int(row["count"]))
                for row in reasons
            ],
            case_status_counts=status_map,
            case_source_counts={
                row["source"]: int(row["count"]) for row in source_counts
            },
            job_status_counts={
                row["status"]: int(row["count"]) for row in job_status
            },
            job_kind_counts={row["kind"]: int(row["count"]) for row in job_kind},
            notes=list(NOTE_TEXT),
            generated_by="observability-summary-v1",
        )


_USAGE_BUCKETS = """
SELECT
    (started_at::timestamptz AT TIME ZONE 'Asia/Shanghai')::date AS day,
    COUNT(DISTINCT CASE WHEN status != 'rejected'
        THEN COALESCE(run_id, usage_id) END) AS request_count,
    SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_count,
    SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error_count,
    SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected_count,
    SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running_count,
    COUNT(DISTINCT owner_id) AS distinct_owners,
    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
    COALESCE(SUM(total_tokens), 0) AS total_tokens,
    SUM(CASE WHEN token_usage_complete=1 THEN 1 ELSE 0 END) AS measured_request_count,
    SUM(CASE WHEN token_usage_complete=0 THEN 1 ELSE 0 END) AS incomplete_token_count,
    COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
FROM agent_usage_events
WHERE started_at::timestamptz >= ? AND started_at::timestamptz < ?
GROUP BY day
ORDER BY day
"""

_AUDIENCE_TOTALS = """
SELECT COUNT(DISTINCT owner_id) AS distinct_owners,
       COUNT(DISTINCT session_id) AS distinct_sessions,
       SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running_count
FROM agent_usage_events
WHERE started_at::timestamptz >= ? AND started_at::timestamptz < ?
"""

_RUN_BUCKETS = """
SELECT
    (started_at::timestamptz AT TIME ZONE 'Asia/Shanghai')::date AS day,
    COUNT(*) AS answered_count,
    SUM(CASE WHEN response_json::jsonb->>'task_status'='partial' THEN 1 ELSE 0 END)
        AS partial_count,
    SUM(CASE WHEN response_json::jsonb->>'task_status'='error' THEN 1 ELSE 0 END)
        AS answer_error_count,
    SUM(CASE WHEN response_json::jsonb->>'task_status' IN ('clarify','refuse') THEN 1 ELSE 0 END)
        AS clarify_refuse_count
FROM react_runs
WHERE response_json IS NOT NULL
  AND started_at::timestamptz >= ? AND started_at::timestamptz < ?
GROUP BY day
ORDER BY day
"""

_FEEDBACK_BUCKETS = """
SELECT
    (updated_at AT TIME ZONE 'Asia/Shanghai')::date AS day,
    SUM(CASE WHEN vote='up' THEN 1 ELSE 0 END) AS up_count,
    SUM(CASE WHEN vote='down' THEN 1 ELSE 0 END) AS down_count
FROM answer_feedback
WHERE updated_at >= ? AND updated_at < ?
GROUP BY day
ORDER BY day
"""

_CASE_BUCKETS = """
SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS day,
       COUNT(*) AS case_count
FROM answer_review_cases
WHERE created_at >= ? AND created_at < ?
GROUP BY day
ORDER BY day
"""

_REASON_COUNTS = """
SELECT item AS reason, COUNT(*) AS count
FROM answer_feedback AS feedback,
     LATERAL jsonb_array_elements_text(feedback.reasons_json::jsonb) AS item
WHERE feedback.updated_at >= ? AND feedback.updated_at < ?
GROUP BY item
ORDER BY count DESC, reason ASC
"""

_CASE_STATUS_COUNTS = """
SELECT status, COUNT(*) AS count
FROM answer_review_cases
WHERE created_at >= ? AND created_at < ?
GROUP BY status
"""

_CASE_SOURCE_COUNTS = """
SELECT source, COUNT(*) AS count
FROM answer_review_cases
WHERE created_at >= ? AND created_at < ?
GROUP BY source
"""

_JOB_STATUS_COUNTS = """
SELECT status, COUNT(*) AS count
FROM answer_review_jobs
WHERE created_at >= ? AND created_at < ?
GROUP BY status
"""

_JOB_KIND_COUNTS = """
SELECT kind, COUNT(*) AS count
FROM answer_review_jobs
WHERE created_at >= ? AND created_at < ?
GROUP BY kind
"""

_JUDGE_TOTALS = """
SELECT COUNT(*) AS call_count,
       SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error_count,
       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
       COALESCE(SUM(total_tokens), 0) AS total_tokens,
       COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd,
       SUM(CASE WHEN pricing_configured=1 THEN 1 ELSE 0 END) AS priced_call_count,
       SUM(CASE WHEN token_usage_complete=0 THEN 1 ELSE 0 END)
           AS incomplete_token_count
FROM answer_review_judge_calls
WHERE created_at >= ? AND created_at < ?
"""

_LATENCY_TOTALS = """
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95
FROM agent_usage_events
WHERE duration_ms IS NOT NULL AND status != 'rejected'
  AND started_at::timestamptz >= ? AND started_at::timestamptz < ?
"""

_MTTA = """
SELECT AVG(EXTRACT(EPOCH FROM (reviewed_at - created_at))) AS seconds
FROM answer_review_cases
WHERE reviewed_at IS NOT NULL AND created_at >= ? AND created_at < ?
"""

_MTTR = """
SELECT AVG(EXTRACT(EPOCH FROM (closed_at - created_at))) AS seconds
FROM answer_review_cases
WHERE closed_at IS NOT NULL AND created_at >= ? AND created_at < ?
"""


def _merge_usage(buckets, rows) -> None:
    for row in rows:
        bucket = _bucket(buckets, row["day"])
        if bucket is None:
            continue
        bucket.request_count = int(row["request_count"] or 0)
        bucket.success_count = int(row["success_count"] or 0)
        bucket.error_count = int(row["error_count"] or 0)
        bucket.rejected_count = int(row["rejected_count"] or 0)
        bucket.distinct_owners = int(row["distinct_owners"] or 0)
        bucket.prompt_tokens = int(row["prompt_tokens"] or 0)
        bucket.completion_tokens = int(row["completion_tokens"] or 0)
        bucket.total_tokens = int(row["total_tokens"] or 0)
        bucket.measured_request_count = int(row["measured_request_count"] or 0)
        bucket.incomplete_token_count = int(row["incomplete_token_count"] or 0)
        bucket.estimated_cost_usd = round(float(row["estimated_cost_usd"] or 0), 8)


def _merge_runs(buckets, rows) -> None:
    for row in rows:
        bucket = _bucket(buckets, row["day"])
        if bucket is None:
            continue
        bucket.answered_count = int(row["answered_count"] or 0)
        bucket.partial_count = int(row["partial_count"] or 0)
        bucket.answer_error_count = int(row["answer_error_count"] or 0)
        bucket.clarify_refuse_count = int(row["clarify_refuse_count"] or 0)


def _merge_feedback(buckets, rows) -> None:
    for row in rows:
        bucket = _bucket(buckets, row["day"])
        if bucket is None:
            continue
        bucket.up_count = int(row["up_count"] or 0)
        bucket.down_count = int(row["down_count"] or 0)


def _merge_cases(buckets, rows) -> None:
    for row in rows:
        bucket = _bucket(buckets, row["day"])
        if bucket is None:
            continue
        bucket.case_count = int(row["case_count"] or 0)


def _bucket(buckets, value):
    key = value.isoformat() if isinstance(value, date) else str(value)
    return buckets.get(key)


def _totals(buckets, audience, judge, latency, mtta, mttr) -> ObservabilityTotals:
    def total(attribute: str) -> int:
        return sum(int(getattr(item, attribute) or 0) for item in buckets)

    def total_float(attribute: str) -> float:
        return round(sum(float(getattr(item, attribute) or 0) for item in buckets), 8)

    up_count = total("up_count")
    down_count = total("down_count")
    vote_count = up_count + down_count
    answered_count = total("answered_count")
    totals = ObservabilityTotals(
        request_count=total("request_count"),
        success_count=total("success_count"),
        error_count=total("error_count"),
        rejected_count=total("rejected_count"),
        running_count=int(_row_value(audience, "running_count")),
        distinct_owners=int(_row_value(audience, "distinct_owners")),
        distinct_sessions=int(_row_value(audience, "distinct_sessions")),
        answered_count=answered_count,
        partial_count=total("partial_count"),
        answer_error_count=total("answer_error_count"),
        clarify_refuse_count=total("clarify_refuse_count"),
        prompt_tokens=total("prompt_tokens"),
        completion_tokens=total("completion_tokens"),
        total_tokens=total("total_tokens"),
        measured_request_count=total("measured_request_count"),
        incomplete_token_count=total("incomplete_token_count"),
        estimated_cost_usd=total_float("estimated_cost_usd"),
        up_count=up_count,
        down_count=down_count,
        vote_count=vote_count,
        like_ratio=_ratio(up_count, vote_count),
        feedback_coverage=_ratio(vote_count, answered_count),
        down_per_100_answers=_ratio(down_count * 100, answered_count),
    )
    totals.judge_call_count = int(_row_value(judge, "call_count"))
    totals.judge_succeeded_count = int(_row_value(judge, "succeeded_count"))
    totals.judge_error_count = int(_row_value(judge, "error_count"))
    totals.judge_prompt_tokens = int(_row_value(judge, "prompt_tokens"))
    totals.judge_completion_tokens = int(_row_value(judge, "completion_tokens"))
    totals.judge_total_tokens = int(_row_value(judge, "total_tokens"))
    totals.judge_estimated_cost_usd = round(
        float(_row_value(judge, "estimated_cost_usd")), 8,
    )
    totals.judge_priced_call_count = int(_row_value(judge, "priced_call_count"))
    totals.judge_incomplete_token_count = int(
        _row_value(judge, "incomplete_token_count"),
    )
    p50 = _row_value(latency, "p50", None)
    p95 = _row_value(latency, "p95", None)
    totals.p50_duration_ms = round(float(p50), 1) if p50 is not None else None
    totals.p95_duration_ms = round(float(p95), 1) if p95 is not None else None
    mtta_value = _row_value(mtta, "seconds", None)
    mttr_value = _row_value(mttr, "seconds", None)
    totals.mtta_seconds = (
        round(float(mtta_value), 1) if mtta_value is not None else None
    )
    totals.mttr_seconds = (
        round(float(mttr_value), 1) if mttr_value is not None else None
    )
    return totals


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _row_value(row, key: str, default=0):
    if row is None:
        return default
    value = row[key]
    return default if value is None else value


def _day_start_utc(now: datetime) -> datetime:
    return now.astimezone(SHANGHAI).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).astimezone(timezone.utc)
