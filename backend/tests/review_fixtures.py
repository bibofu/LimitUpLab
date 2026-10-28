"""Shared fixtures for review queue tests."""

import json

from app.agents.run_lifecycle import RunLifecycle
from app.database import connect
from app.models import AgentChatRequest, AnswerFeedbackInput
from app.repositories import AnswerFeedbackRepository, ChatSessionRepository
from app.review_models import ReviewCaseUpdate
from app.services.review_cases import ReviewCaseService
from app.services.review_signals import ReviewSignalService


def owner(suffix: str) -> str:
    return "visitor_" + suffix * 32


def completed_run(
    owner_id: str,
    session_id: str,
    *,
    question: str = "市场最高标是哪个",
    answer: str = "研究回答",
    task_status: str = "complete",
) -> str:
    ChatSessionRepository().create_session(session_id=session_id, owner_id=owner_id)
    row, _ = RunLifecycle().create(
        AgentChatRequest(
            session_id=session_id,
            message_id=f"question-{session_id}",
            message=question,
        ),
        owner_id,
    )
    response = {
        "answer": answer,
        "task_status": task_status,
        "intent": "market_summary",
        "tool_calls": ["market_summary", "limit_up_events"],
        "warnings": [],
        "trace_url": f"https://trace.example/{row['run_id']}",
    }
    with connect() as connection:
        connection.execute(
            """
            UPDATE react_runs
            SET response_json=?, active=0, run_status='completed'
            WHERE run_id=?
            """,
            (json.dumps(response, ensure_ascii=False), row["run_id"]),
        )
    return row["run_id"]


def downvote(
    owner_id: str,
    run_id: str,
    reasons: list[str] | None = None,
    comment: str = "日期不对",
) -> None:
    chosen = reasons or ["data_error"]
    record = AnswerFeedbackRepository().save(
        run_id,
        owner_id,
        AnswerFeedbackInput(vote="down", reasons=chosen, comment=comment),
    )
    ReviewSignalService().record_downvote(
        run_id=run_id,
        owner_id=owner_id,
        reasons=chosen,
        comment=comment,
        feedback_updated_at=record.updated_at,
    )


def advance_to_closed(service: ReviewCaseService, case_id: str) -> None:
    detail = service.update_case(
        case_id,
        ReviewCaseUpdate(
            status="confirmed",
            symptom="日期范围错误",
            note="确认问题",
            revision=1,
        ),
    )
    detail = service.update_case(
        case_id,
        ReviewCaseUpdate(status="fixing", note="开始修复", revision=detail.revision),
    )
    detail = service.update_case(
        case_id,
        ReviewCaseUpdate(
            status="verifying",
            root_cause="事件日期未取最近交易日",
            fix_version="v1.2",
            note="修复完成待验证",
            revision=detail.revision,
        ),
    )
    service.update_case(
        case_id,
        ReviewCaseUpdate(
            status="closed",
            verification="真实复验通过",
            note="关闭案例",
            revision=detail.revision,
        ),
    )
