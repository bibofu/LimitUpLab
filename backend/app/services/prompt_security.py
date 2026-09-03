"""Deterministic guards for direct prompt-injection attempts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class PromptInjectionAssessment:
    """High-confidence prompt-injection signals found in one user message."""

    detected: bool
    signals: tuple[str, ...] = ()


_SIGNAL_PATTERNS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "instruction_override",
        (
            re.compile(
                r"(?:忽略|无视|忘记|忘掉|抛弃|覆盖|绕过|跳过|取消|不要遵守)"
                r".{0,24}(?:系统|开发者|管理员|规则|指令|提示词|限制|安全策略)"
            ),
            re.compile(
                r"\b(?:ignore|disregard|forget|override|bypass|discard)\b"
                r".{0,80}\b(?:system|developer|instructions?|prompts?|polic(?:y|ies)|rules?)\b",
                re.IGNORECASE,
            ),
        ),
    ),
    (
        "prompt_disclosure",
        (
            re.compile(
                r"(?:输出|显示|打印|复述|泄露|透露|展示|给我看|告诉我)"
                r".{0,30}(?:系统提示词|系统消息|开发者消息|隐藏指令|内部指令|原始提示词|"
                r"工具.{0,6}(?:列表|定义|架构|schema))",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:reveal|print|repeat|dump|expose|display)\b"
                r".{0,100}\b(?:system\s+prompt|developer\s+message|hidden\s+instructions?|"
                r"internal\s+instructions?|original\s+prompt|tool\s+(?:schema|definitions?|list))\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:show\s+me|tell\s+me)\b.{0,100}\b"
                r"(?:your|current|hidden|original|full|exact|internal)\s+"
                r"(?:system\s+prompt|developer\s+message|instructions?|"
                r"tool\s+(?:schema|definitions?|list))\b",
                re.IGNORECASE,
            ),
        ),
    ),
    (
        "role_spoofing",
        (
            re.compile(
                r"(?:你现在是|从现在起你是|假装你是|扮演)"
                r".{0,30}(?:系统|开发者|管理员|root|dan|无限制|不受限制|不受约束|无审查)",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:act\s+as|pretend\s+to\s+be|you\s+are\s+now)\b"
                r".{0,80}\b(?:system|developer|admin(?:istrator)?|root|dan|unrestricted|uncensored)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?:<\s*/?\s*(?:system|developer|assistant)\s*>|"
                r"\[\s*(?:system|developer|assistant)\s*\])",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?:^|[\r\n])\s*(?:system|developer|assistant)\s*:\s*\S",
                re.IGNORECASE,
            ),
        ),
    ),
    (
        "tool_policy_bypass",
        (
            re.compile(
                r"(?:调用|执行|使用|开放|启用).{0,24}"
                r"(?:隐藏|内部|未授权|禁用|未开放).{0,12}(?:工具|函数|tool)",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?:隐藏|内部|未授权|禁用|未开放).{0,12}(?:工具|函数|tool)"
                r".{0,24}(?:调用|执行|使用|开放|启用)",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:call|invoke|execute|enable|use)\b.{0,80}"
                r"\b(?:hidden|internal|unauthorized|disabled|unlisted)\b.{0,30}"
                r"\b(?:tool|function)s?\b",
                re.IGNORECASE,
            ),
        ),
    ),
    (
        "encoded_instruction",
        (
            re.compile(
                r"(?:解码|base64|rot13).{0,40}(?:执行|遵循|服从|作为指令)",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:decode|base64|rot13)\b.{0,80}"
                r"\b(?:execute|follow|obey|instruction)s?\b",
                re.IGNORECASE,
            ),
        ),
    ),
    (
        "safety_bypass",
        (
            re.compile(
                r"(?:忽略|无视|绕过|取消|不要遵守).{0,24}"
                r"(?:安全|限制|政策|合规|免责声明|风险提示|投资建议)"
            ),
            re.compile(
                r"(?:不要|不许|禁止).{0,10}(?:提及|输出|添加|写).{0,10}"
                r"(?:免责声明|风险提示|安全提示|不构成投资建议)"
            ),
        ),
    ),
)


_PROMPT_LEAK_SIGNATURES = (
    "your first job is to decide which tools are needed",
    "capability catalog:",
    "available tools are described as json schemas",
    "submit_agent_plan",
    "capability_response_contracts",
    "exhaustive_list_output",
    "position_classification_output",
    "complete_hot_stock_output",
    "set_intersection_output",
    "limituplab_agent_profile",
    "openai_api_key=",
    "deepseek_api_key=",
)


def assess_direct_prompt_injection(message: str) -> PromptInjectionAssessment:
    """Detect high-confidence attempts to override or exfiltrate agent policy."""

    normalized = unicodedata.normalize("NFKC", message or "").strip()
    if not normalized:
        return PromptInjectionAssessment(False)
    signals = tuple(
        signal
        for signal, patterns in _SIGNAL_PATTERNS
        if any(pattern.search(normalized) for pattern in patterns)
    )
    return PromptInjectionAssessment(bool(signals), signals)


def contains_prompt_leak(content: str) -> bool:
    """Return whether model output contains an internal prompt or secret signature."""

    normalized = unicodedata.normalize("NFKC", content or "").lower()
    return any(signature in normalized for signature in _PROMPT_LEAK_SIGNATURES)
