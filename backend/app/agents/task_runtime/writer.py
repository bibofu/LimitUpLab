"""Per-requirement answers with local repair and evidence-preserving fallback."""

import re

from pydantic import BaseModel, ConfigDict, Field

from .contracts import values_at


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_id: str
    path: str
    value: str | float | int


class AnswerBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    content: str
    claims: list[Claim] = Field(default_factory=list)
    evidence_steps: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: list[AnswerBlock]


def _unsafe(text):
    # Historical institution buy/sell facts do not match user-directed instructions.
    return bool(re.search(r"(?:建议|应该|可以|务必|立即|推荐)(?:你|您)?(?:现在|明天|逢低|择机)?(?:买入|卖出|加仓|减仓|建仓)|(?:目标价|建议仓位)\s*[:：]?\s*\d|保证.{0,8}(?:盈利|收益|上涨)", text))


def _fallback(requirement, state):
    import json
    from .runtime import _summary
    parts = []
    for record in state.get("records", {}).values():
        if requirement.id not in record["requirement_ids"]:
            continue
        if record["state"] in {"error", "skipped"}:
            parts.append("该项证据未取得或条件未满足，不能据此推断市场事实。")
            continue
        if record["state"] == "empty":
            parts.append("该项查询返回空结果。")
        elif record.get("payload"):
            # Preserve every task's successful facts, including selected/fan-out lists.
            rendered = json.dumps(_summary(record["payload"], 10), ensure_ascii=False, default=str)
            if not _unsafe(rendered):
                parts.append("已取得以下原始证据（较长列表以 truncated_items 标记截断；尚未完成解释）：\n```json\n" + rendered.replace("```", "｀｀｀") + "\n```")
            else:
                parts.append("来源内容未通过安全检查，不能直接展示。")
            if record["state"] == "partial":
                parts.append("其余实体或数据源未全部查询成功。")
        else:
            parts.append("已取得部分证据，但回答整理失败，尚未完成此项交付。")
    return "\n".join(dict.fromkeys(parts)) or "该项尚未取得充分证据，未完成。"


def compose(state, decide):
    if "plan" not in state:
        return "当前规划服务不可用，未执行查询，请稍后重试。"
    plan = state["plan"]
    if plan.behavior == "refuse":
        return "我可以提供有来源的研究事实和风险说明，但不能提供交易指令或收益承诺。"
    if plan.behavior in {"clarify", "answer"}:
        return plan.message if not _unsafe(plan.message) else "请明确需要查询的研究事实。"
    from .runtime import _summary
    try:
        result = decide("answer", Answer, {
            "requirements": [r.model_dump() for r in plan.requirements],
            "evidence": _summary(state["records"], 30),
            "missing": state["completion"].missing if state.get("completion") else {},
            "instruction": "Write Chinese research answer, ONE block for EVERY requirement ID. Use only supplied evidence; cite evidence_steps. Include all factual numeric claims with exact step_id/path/value from record.payload. Do not claim unavailable subresults. Distinguish inference from fact and don't infer causality/direction from correlation alone. Preserve exact requested scope, list size, dates. Explicitly disclose truncation/missing data. No trading instructions. No internal tool names. Historical institutional buys/sells are allowed. Return JSON only.",
        })
        blocks = {b.requirement_id: b for b in result.blocks}
    except Exception:
        blocks = {}
    sections = []
    for requirement in plan.requirements:
        block = blocks.get(requirement.id)
        valid = block is not None and bool(block.content.strip()) and not _unsafe(block.content)
        if valid:
            valid = bool(block.evidence_steps) and all(
                key in state["records"] and requirement.id in state["records"][key]["requirement_ids"]
                for key in block.evidence_steps
            )
        if valid:
            if re.search(r"\d", block.content) and not block.claims:
                valid = False
            for claim in block.claims:
                record = state["records"].get(claim.step_id)
                if record is None or claim.step_id not in block.evidence_steps:
                    valid = False
                    break
                try:
                    supported = claim.value in values_at(record["payload"], claim.path)
                except ValueError:
                    supported = False
                if not supported:
                    valid = False
                    break
        if not valid:
            state["stop"] = "partial"
        content = block.content if valid else _fallback(requirement, state)
        sections.append(f"### {requirement.description}\n\n{content}")
    if state.get("stop") != "complete":
        sections.append("部分要求尚未完成，以上仅包含已取得的证据；缺失不代表相关事实不存在。")
    return "\n\n".join(sections) + "\n\n不构成买卖建议。"
