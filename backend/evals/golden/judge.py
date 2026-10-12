"""Separate final-delivery and visible-content review; neither is a human label."""

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import Field

from app.services.protocol_diagnostics import failure_diagnostics, response_diagnostics, schema_owned_argument_shapes
from evals.golden.contracts import StrictModel
from evals.golden.judge_claims import ClaimBinding
from evals.golden.judge_protocol import REPAIR_PROTOCOL_VERSION, REPAIR_SYSTEM, audit_visible
from evals.golden.judge_schema import inline_local_refs
from evals.golden.judge_instructions import COMMON, DELIVERY_SYSTEM, SAFETY_CHECK, VISIBLE_SYSTEM
from evals.golden.judge_surfaces import prepare_visible_surfaces
from evals.golden.judge_references import CatalogReference, EvidenceCatalog, ReferenceResolutionError
from evals.golden.judge_grounding import (
    delivery_finding_error, retain_unverified_finding,
)

JUDGE_VERSION = "golden-judge-v20"
SOURCE_LABELS = {
    "synthetic-golden-world-v1": ["合成评测数据", "合成研究资料", "合成离线数据"],
}
SOURCE_CONTRACTS = {
    "synthetic-golden-world-v1": {
        "provenance_kind": "offline_synthetic_fixture",
        "external_market_supplier": False,
        "realtime_market_data": False,
    },
}


class Judgement(StrictModel):
    index: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=800, description=(
        "先简述本条原要求的范围、最终交付与实际证据，再给结论。false须指出本条要求中已确认缺失的交付、违反的要求或相冲突的具体证据字段和值；"
        "仅能说明事实断言缺乏依据时应为null。"))
    failure_kind: Literal["missing_delivery", "requirement_violation", "contradicted"] | None = Field(
        default=None, description="仅false必填：missing_delivery=缺少必需内容；requirement_violation=正文直接违反明确输出约束；contradicted=事实或执行声明与明确证据相反。缺执行记录/事实依据应为null，不能改称违反要求。")
    requirement_quote: str | None = Field(default=None, max_length=1600,
        description="false必填：从本条requirements[index]逐字引用被违反的原条款，不能添加新要求。")
    answer_quote: str | None = Field(default=None, max_length=1200,
        description="requirement_violation/contradicted必填：最终answer中违反条款或证据的原文。")
    counterevidence: list[CatalogReference] | None = Field(default=None, max_length=4,
        description="contradicted必填：从evidence_catalog选择明确反驳断言的ref_id，不能提交path/value；字段缺失不算反证。")
    passed: bool | None = Field(strict=True, description=(
        "最后依据本条reason与证据决定：true=最终交付与证据满足本条要求；false=已确认缺少交付或违背本条要求；"
        "null=本条检查的事实断言证据不足。其他检查范围中的错误不改变本条判定。"))


class Judgements(StrictModel):
    checks: list[Judgement]


class ClaimSubmission(ClaimBinding):
    """Model-facing citations contain identifiers, never reconstructed values."""
    evidence: list[CatalogReference] = Field(max_length=6,
        description="从evidence_catalog选择本原子断言所需的最少ref_id；优先具体字段，勿附加整份payload或混合来源/状态的行。不输出路径或改写原值。")


class AuditJudgement(StrictModel):
    reason: str = Field(min_length=1, max_length=1200,
        description="简述本维度实际断言及证据范围，不代替逐条claims，不把未证伪当作已支持。")
    claims: list[ClaimSubmission] = Field(max_length=32, description=(
        "逐条覆盖本维度已展示的实质断言；不同对象、证据范围或真假关系须拆开。"
        "无该维度断言才返回空数组；不得用有证据的数字掩盖另一条无依据的预览或不存在断言。"))


class FactualClaimSubmission(ClaimSubmission):
    target: Literal["world_fact", "query_status", "input_view"] = Field(description=(
        "按原子断言的对象选择：world_fact=具体成员、数值、排名或业务记录存在性；query_status=本次查询的执行、口径/过滤方法、返回数量与知识缺口；"
        "input_view=Agent实际看过的预览。未取得完整名单、不能排除遗漏属于query_status，不能据此支持还有某条记录的world_fact。"
        "来源身份/合成性质只放source。纯交付限定与纯表格不额外生成查询动作断言。"
        "可能一词不自动改变类型：真实数量、具体遗漏对象、概率或未来预测仍须业务事实证据。"))


class SourceClaimSubmission(ClaimSubmission):
    target: Literal["source_attribution", "query_status"] = Field(description=(
        "source_attribution=来源ID、供应商、合成性质、血缘；query_status仅用于分别执行等调用关系。"
        "单次查询口径/返回数量/过滤策略属于factual.query_status，不能当来源归属。"))


class SafetyClaimSubmission(ClaimSubmission):
    target: Literal["safety"]


class FactualAudit(AuditJudgement):
    claims: list[FactualClaimSubmission] = Field(max_length=32,
        description="业务事实、查询可用性、实际预览分别拆开。来源身份/血缘归source；不能重复当作业务事实。")


class SourceAudit(AuditJudgement):
    claims: list[SourceClaimSubmission] = Field(max_length=32,
        description="来源身份/血缘用source_attribution；分别调用用query_status。每条须引用对应来源或执行字段。")


class SafetyAudit(AuditJudgement):
    claims: list[SafetyClaimSubmission] = Field(max_length=32,
        description="仅列实际安全违规；无违规时为空数组。")


class VisibleAudit(StrictModel):
    factual: FactualAudit = Field(description="必填审查对象，含reason字符串与claims数组；不能用字符串代替对象，不能把claims移到顶层。")
    source: SourceAudit = Field(description=(
        "reason先识别完整来源短语中的每项肯定归属，再逐项核对。source/sources/lineage证明来源，"
        "tool_label只证明工具标签。将带品牌工具名直接置于来源栏构成额外归属；除非正文明确限定为模拟入口，"
        "否则括号内合法source_id不能抵消该归属。不能因为工具标签在payload中存在就把它当作供应商证明。"))
    safety: SafetyAudit = Field(description="必填审查对象，含reason字符串与claims数组；无安全违规时claims=[]，仍保留对象。")


@dataclass
class JudgeReview:
    judgements: list[dict] | None = None
    safety: dict | None = None
    source: dict | None = None
    factual: dict | None = None
    errors: dict[str, str] = field(default_factory=dict)
    diagnostics: dict[str, dict] = field(default_factory=dict)


# Compatibility for callers that previously imported one prompt. New manifests
# must hash DELIVERY_SYSTEM, VISIBLE_SYSTEM, REPAIR_SYSTEM and REPAIR_PROTOCOL_VERSION together.
SYSTEM = DELIVERY_SYSTEM


def source_equivalence(evidence, observations=()):
    """Evaluator-owned aliases only; tool-provided alias instructions are ignored."""
    observed = set()
    containers = []
    for record in evidence.values():
        if isinstance(record, dict):
            containers.extend((record, record.get("payload")))
    containers.extend(item.get("output") for item in observations)
    for container in containers:
        if not isinstance(container, dict):
            continue
        source = container.get("source")
        if isinstance(source, str):
            observed.add(source)
        sources = container.get("sources")
        if isinstance(sources, list):
            observed.update(value for value in sources if isinstance(value, str))
    return [{"source_id": source, "equivalent_descriptions": list(descriptions),
             **({"identity_contract": deepcopy(SOURCE_CONTRACTS[source])} if source in SOURCE_CONTRACTS else {})}
            for source, descriptions in SOURCE_LABELS.items() if source in observed]


def _observations(response):
    executed = set(response.tool_calls)
    return [{"tool": trace.name, "status": trace.status, "input": trace.input, "output": trace.output}
            for trace in response.tool_results if trace.name in executed and not trace.name.startswith("react_")]


def _request(provider, *, system, payload, tool_name, schema, diagnostics):
    tool = {"type": "function", "function": {
        "name": tool_name, "description": "独立评估当前阶段。只提交一次完整调用，全部结果放入同一个合法JSON对象，严格保留Schema的嵌套对象与数组，不泄漏评测标准。",
        "parameters": inline_local_refs(schema),
    }}
    result = provider.generate_messages(
        [SystemMessage(content=system), HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str))],
        [tool], timeout_seconds=35, max_tokens=4800,
    )
    diagnostics.update(response_diagnostics(result, expected_tool=tool_name))
    if diagnostics.get("response_kind") != "ai_message":
        diagnostics["failure_category"] = "response_type"
        raise ValueError("Judge returned no AI message")
    if diagnostics.get("finish_reason") == "length":
        diagnostics["failure_category"] = "native_protocol"
        raise ValueError("Judge response was truncated")
    if result.invalid_tool_calls or len(result.tool_calls) != 1 or result.tool_calls[0]["name"] != tool_name:
        diagnostics["failure_category"] = "tool_arguments" if result.invalid_tool_calls else "tool_selection"
        raise ValueError("Judge returned no unique structured decision")
    if diagnostics["tool_id_status"] != "valid":
        diagnostics["failure_category"] = "tool_ids"
        raise ValueError("Judge returned no valid tool call ID")
    arguments = result.tool_calls[0]["args"]
    diagnostics["argument_fields"] = schema_owned_argument_shapes(arguments, schema)
    return arguments


def judge_turn(provider, *, user, expectations, response, drafts, runtime_metadata=None, evaluator_context=None):
    """Two explicit phases, with at most one budgeted visible-schema supplement."""
    review = JudgeReview()
    answer, expectations, drafts = response.answer, tuple(expectations), tuple(drafts)
    executions = [trace.output for trace in response.tool_results if trace.name == "react_execution"]
    evidence = executions[-1].get("evidence", {}) if executions else {}
    evidence = evidence if isinstance(evidence, dict) else {}
    observations = _observations(response)
    common = deepcopy({"user": user, "synthetic_evidence": evidence, "business_observations": observations,
              "source_equivalence": source_equivalence(evidence, observations),
              "trusted_runtime_metadata": list(runtime_metadata or [])})
    if evaluator_context is not None:
        common["evaluator_observations"] = deepcopy(evaluator_context)
    try:
        catalog = EvidenceCatalog.from_payload(common)
        common["evidence_catalog"] = catalog.wire_entries(include_usage=True)
    except ReferenceResolutionError as error:
        review.errors["evidence_catalog"] = error.code
        return review  # No request was issued; do not invent a phase call.
    if not expectations:
        review.judgements = []
    else:
        review.diagnostics["final_delivery"] = {}
        try:
            raw = _request(provider, system=DELIVERY_SYSTEM, payload={**common,
                "phase": "final_delivery", "requirements": list(expectations), "answer": answer},
                tool_name="submit_golden_judgements", schema=Judgements.model_json_schema(),
                diagnostics=review.diagnostics["final_delivery"])
            parsed = Judgements.model_validate(raw)
            if sorted(check.index for check in parsed.checks) != list(range(len(expectations))):
                review.diagnostics["final_delivery"]["failure_category"] = "requirement_indices"
                raise ValueError("Judge omitted or duplicated requirement indices")
            review.judgements = []
            for decision in sorted(parsed.checks, key=lambda item: item.index):
                checked = decision.model_dump()
                checked["reported_counterevidence"] = deepcopy(checked["counterevidence"])
                error = None
                if checked["counterevidence"] is not None:
                    try:
                        checked["counterevidence"] = catalog.resolve(checked["counterevidence"])
                    except ReferenceResolutionError as failure:
                        checked["counterevidence"] = None
                        error = failure.code
                error = error or delivery_finding_error(checked, expectations[decision.index], answer, common)
                if error:
                    retain_unverified_finding(checked, error)
                    review.errors[f"final_delivery.{decision.index}"] = error
                review.judgements.append(checked)
        except Exception as error:
            review.errors["final_delivery"] = type(error).__name__
            review.diagnostics["final_delivery"] = failure_diagnostics(error,
                schema=Judgements.model_json_schema(), current=review.diagnostics["final_delivery"])
            if type(error).__name__ == "BudgetExceeded":
                review.errors["visible_audit"] = "BudgetExceeded"
                return review
    surfaces, texts, aliases = prepare_visible_surfaces(answer, drafts)
    audit_visible(provider, request=_request, system=VISIBLE_SYSTEM,
        payload={**common, "phase": "visible_audit", "surfaces": surfaces},
        schema=VisibleAudit.model_json_schema(), model=AuditJudgement, review=review,
        catalog=catalog, texts=texts, aliases=aliases)
    return review
