"""Deterministic contract checks that need no model call."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.review_snapshot import RunSnapshot


CHECK_VERSION = "deterministic-v1"
DELIVERED_STATUSES = {"complete", "partial", "empty", "clarify", "refuse"}
MISSING_DISCLOSURE_TERMS = (
    "缺失", "未取到", "无法获取", "不可用", "不完整", "未披露", "失败", "没有提供",
)
COMPLIANCE_TERMS = (
    "买入", "卖出", "加仓", "减仓", "目标价", "满仓", "清仓", "梭哈",
    "建议持有", "止盈", "止损",
)
HISTORY_INTENTS = ("memory", "recap", "history", "session")
NUMERIC_CLAIM = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|万|亿|元|手|股)")
STOCK_CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")
VERDICT_ORDER = {"pass": 0, "unknown": 1, "fail": 2}


@dataclass(frozen=True)
class ReviewCheck:
    """One deterministic check result with the evidence used to decide it."""

    check: str
    verdict: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "verdict": self.verdict,
            "summary": self.summary,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class DeterministicReport:
    """Structured deterministic report stored with the review job."""

    overall: str
    checks: list[ReviewCheck]

    def as_dict(self) -> dict[str, Any]:
        return {
            "detector": "deterministic",
            "version": CHECK_VERSION,
            "overall": self.overall,
            "checks": [item.as_dict() for item in self.checks],
        }

    def failures(self) -> list[ReviewCheck]:
        return [item for item in self.checks if item.verdict == "fail"]


def run_deterministic_checks(snapshot: RunSnapshot) -> DeterministicReport:
    """Evaluate only contract facts that can be checked without semantics."""

    checks = [
        _delivery_status(snapshot),
        _answer_present(snapshot),
        _tool_evidence(snapshot),
        _partial_disclosure(snapshot),
        _tool_warnings(snapshot),
        _compliance_language(snapshot),
        _token_measurement(snapshot),
    ]
    overall = "pass"
    for item in checks:
        if VERDICT_ORDER[item.verdict] > VERDICT_ORDER[overall]:
            overall = item.verdict
    return DeterministicReport(overall=overall, checks=checks)


def _delivery_status(snapshot: RunSnapshot) -> ReviewCheck:
    status = snapshot.task_status
    if status in DELIVERED_STATUSES:
        return ReviewCheck("delivery_status", "pass", f"交付状态为 {status}")
    if status == "error":
        return ReviewCheck("delivery_status", "fail", "本轮运行以 error 结束")
    if status == "cancelled":
        return ReviewCheck("delivery_status", "unknown", "本轮运行被取消")
    return ReviewCheck("delivery_status", "unknown", "缺少可识别的交付状态")


def _answer_present(snapshot: RunSnapshot) -> ReviewCheck:
    if snapshot.answer.strip():
        return ReviewCheck("answer_present", "pass", "存在正式回答", {
            "answer_chars": len(snapshot.answer),
        })
    if snapshot.task_status in {"error", "cancelled"}:
        return ReviewCheck("answer_present", "unknown", "失败或取消的运行没有正式回答")
    return ReviewCheck("answer_present", "fail", "运行声称已交付但没有回答正文")


def _tool_evidence(snapshot: RunSnapshot) -> ReviewCheck:
    answer = snapshot.answer
    has_numeric_claim = bool(NUMERIC_CLAIM.search(answer) or STOCK_CODE.search(answer))
    if not has_numeric_claim:
        return ReviewCheck("tool_evidence", "pass", "回答没有需要本轮工具核验的数字断言")
    if snapshot.evidence.get("tool_calls"):
        return ReviewCheck("tool_evidence", "pass", "回答含数字且存在本轮工具调用", {
            "tool_calls": snapshot.evidence.get("tool_calls"),
        })
    if snapshot.task_status in {"clarify", "refuse", "error", "cancelled"}:
        return ReviewCheck("tool_evidence", "pass", "该终态不要求本轮数据工具")
    intent = (snapshot.intent or "").lower()
    if any(term in intent for term in HISTORY_INTENTS):
        return ReviewCheck("tool_evidence", "unknown", "回顾类回答可能引用历史证据")
    return ReviewCheck(
        "tool_evidence", "fail",
        "回答包含数字或股票代码，但本轮没有工具调用",
        {"intent": snapshot.intent, "task_status": snapshot.task_status},
    )


def _partial_disclosure(snapshot: RunSnapshot) -> ReviewCheck:
    if snapshot.task_status != "partial":
        return ReviewCheck("missing_data_disclosure", "pass", "非部分完成状态")
    if any(term in snapshot.answer for term in MISSING_DISCLOSURE_TERMS):
        return ReviewCheck("missing_data_disclosure", "pass", "部分完成回答披露了数据缺口")
    return ReviewCheck(
        "missing_data_disclosure", "fail",
        "部分完成回答没有披露缺失数据",
    )


def _tool_warnings(snapshot: RunSnapshot) -> ReviewCheck:
    warnings = snapshot.evidence.get("warnings") or []
    if not warnings:
        return ReviewCheck("tool_warnings", "pass", "没有工具警告")
    return ReviewCheck(
        "tool_warnings", "unknown",
        "存在工具警告，需要人工判断是否影响交付",
        {"warnings": warnings[:20]},
    )


def _compliance_language(snapshot: RunSnapshot) -> ReviewCheck:
    matches = [term for term in COMPLIANCE_TERMS if term in snapshot.answer]
    if not matches:
        return ReviewCheck("compliance_language", "pass", "未发现交易指令措辞")
    return ReviewCheck(
        "compliance_language", "unknown",
        "回答包含可能构成交易指令的措辞，需人工确认语境",
        {"terms": matches},
    )


def _token_measurement(snapshot: RunSnapshot) -> ReviewCheck:
    complete = snapshot.evidence.get("token_usage_complete")
    if complete is True:
        return ReviewCheck("token_measurement", "pass", "token 用量完整")
    return ReviewCheck(
        "token_measurement", "unknown",
        "缺少完整 token 用量，不能据此判断成本异常",
    )
