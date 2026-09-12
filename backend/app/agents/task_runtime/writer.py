"""Per-requirement answers with local repair and evidence-preserving fallback."""

import json
import re

from pydantic import BaseModel, ConfigDict, Field

from .contracts import evidence_values


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


ANSWER_INSTRUCTION = """Write a concise Chinese research answer, ONE block per requirement ID.
Use only supplied evidence and cite evidence_steps. Every factual numeric claim other
than a date, count, rank, or window copied from the original requirement needs
exact step_id/path/value from record.payload; paths use items.0.metric notation.
Never guess a field name: copy the actual key. Do not include the date or symbol as
a claim unless that value actually exists at the cited path. Claims must have scalar
values, not objects. Include names/codes for stock lists. State empty/missing/truncated
data explicitly. Distinguish inference from observation and correlation from causation.
No trading instructions. Historical institutional buys/sells are allowed. Return JSON.
"""


def fact_catalog(records, limit=300):
    """Give the model copyable source paths, not an invitation to guess JSON layout."""
    facts = []
    def walk(step_id, value, path):
        if len(facts) >= limit:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                walk(step_id, item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(step_id, item, f"{path}.{index}" if path else str(index))
        elif value is not None and not isinstance(value, bool):
            facts.append({"step_id": step_id, "path": path, "value": value})
    for key, record in records.items():
        walk(key, record["payload"], "")
    return {"claims": facts, "possibly_truncated": len(facts) >= limit}


def _validation_errors(block, requirement, state):
    errors = []
    if block is None or not block.content.strip() or _unsafe(block.content):
        return ["missing, empty, or unsafe content"]
    if not block.evidence_steps or not all(key in state["records"] and requirement.id in state["records"][key]["requirement_ids"] for key in block.evidence_steps):
        errors.append("evidence step is missing or belongs to another requirement")
    for claim in block.claims:
        record = state["records"].get(claim.step_id)
        if record is None or claim.step_id not in block.evidence_steps:
            errors.append(f"claim step is not cited: {claim.step_id}")
            continue
        try:
            if claim.value not in evidence_values(record, claim.path):
                errors.append(f"unsupported claim: {claim.step_id}.{claim.path}={claim.value}")
        except ValueError:
            errors.append(f"invalid claim path: {claim.step_id}.{claim.path}")
    content_numbers = set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", re.sub(r"(?m)^\s*\d+[.)、]\s*", "", block.content)))
    claim_numbers = {
        number for claim in block.claims
        for number in re.findall(r"\d+(?:\.\d+)?", str(claim.value))
    }
    scope_numbers = set(re.findall(
        r"\d+(?:\.\d+)?",
        requirement.source_text + " " + requirement.description + " "
        + json.dumps(requirement.constraints, ensure_ascii=False, default=str),
    ))
    if not content_numbers <= claim_numbers | scope_numbers:
        errors.append(f"undeclared numeric tokens: {sorted(content_numbers - claim_numbers - scope_numbers)}")
    return errors


def _valid(block, requirement, state):
    return not _validation_errors(block, requirement, state)


def _unsafe(text):
    # Historical institution buy/sell facts do not match user-directed instructions.
    return bool(re.search(r"(?:建议|应该|可以|务必|立即|推荐)(?:你|您)?(?:现在|明天|逢低|择机)?(?:买入|卖出|加仓|减仓|建仓)|(?:目标价|建议仓位)\s*[:：]?\s*\d|保证.{0,8}(?:盈利|收益|上涨)", text))


def _fallback(requirement, state):
    from .runtime import _summary
    parts = []
    for record in state.get("records", {}).values():
        if requirement.id not in record["requirement_ids"]:
            continue
        if record["state"] in {"error", "skipped"}:
            parts.append("该项证据未取得或条件未满足，不能据此推断市场事实。")
            if not record.get("payload"):
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
        if plan.behavior == "answer" and re.search(r"\d|涨停|行情|评分|龙虎榜|走势|新闻", plan.message):
            return "该问题需要工具证据后才能回答；当前未执行查询。"
        return plan.message if not _unsafe(plan.message) else "请明确需要查询的研究事实。"
    from .runtime import _summary
    try:
        result = Answer.model_validate(state["answer_draft"]) if state.get("answer_draft") is not None else decide("answer", Answer, {
            "requirements": [r.model_dump() for r in plan.requirements],
            "evidence": _summary(state["records"], 30),
            "missing": state["completion"].missing if state.get("completion") else {},
            "instruction": ANSWER_INSTRUCTION,
        })
        blocks = {b.requirement_id: b for b in result.blocks}
    except Exception:
        blocks = {}
    validation = {
        r.id: _validation_errors(blocks.get(r.id), r, state)
        for r in plan.requirements
    }
    invalid = [r for r in plan.requirements if validation[r.id]]
    state["answer_validation"] = {"initial": validation}
    # One bounded local repair; never replace already verified task paragraphs.
    if invalid and state.get("answer_draft") is not None:
        try:
            repaired = decide("answer_repair", Answer, {
                "requirements": [r.model_dump() for r in invalid],
                "evidence": _summary({key: record for key, record in state["records"].items() if any(r.id in record["requirement_ids"] for r in invalid)}, 30),
                "invalid_blocks": [blocks[r.id].model_dump() for r in invalid if r.id in blocks],
                "validation_errors": {r.id: validation[r.id] for r in invalid},
                "instruction": ANSWER_INSTRUCTION + " Repair only listed blocks: evidence references or values were invalid, missing, or unsafe. Use exact paths and values from payload, not the record wrapper.",
            })
            for block in repaired.blocks:
                if block.requirement_id in {r.id for r in invalid}:
                    blocks[block.requirement_id] = block
        except Exception:
            pass
    state["answer_validation"]["final"] = {
        r.id: _validation_errors(blocks.get(r.id), r, state)
        for r in plan.requirements
    }
    sections = []
    for requirement in plan.requirements:
        block = blocks.get(requirement.id)
        valid = _valid(block, requirement, state)
        if not valid:
            state["stop"] = "partial"
        content = block.content if valid else _fallback(requirement, state)
        sections.append(f"### {requirement.description}\n\n{content}")
    if state.get("stop") != "complete":
        sections.append("部分要求尚未完成，以上仅包含已取得的证据；缺失不代表相关事实不存在。")
    return "\n\n".join(sections) + "\n\n不构成买卖建议。"
