"""Check judge citations, without pretending that a valid citation proves a claim."""

from typing import Any

from pydantic import Field, StrictInt, StrictStr

from evals.golden.contracts import StrictModel


class EvidenceReference(StrictModel):
    path: list[StrictStr | StrictInt] = Field(min_length=3, max_length=24, description=(
        "当前断言的证据在本阶段输入中的路径。首项只能为synthetic_evidence、business_observations、"
        "trusted_runtime_metadata或source_equivalence，后续依次为对象键或数组下标；不能引用回答、理由或缺失字段。"))
    value: Any = Field(description="该路径实际存在的完整JSON值，保留原类型；支持或反驳断言取决于该字段的语义和证据范围。")


def _same_json(left, right):
    # Python's True == 1 must not turn a mismatched citation into a valid one.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def valid_counterevidence(references, payload):
    """Existence and value checks only; logical relevance still needs calibration."""
    if not references:
        return False
    roots = {"synthetic_evidence", "business_observations", "trusted_runtime_metadata", "source_equivalence"}
    for reference in references:
        path = reference["path"]
        if path[0] not in roots:
            return False
        value = payload
        for part in path:
            if isinstance(value, dict) and type(part) is str and part in value:
                value = value[part]
            elif isinstance(value, list) and type(part) is int and 0 <= part < len(value):
                value = value[part]
            else:
                return False
        if not _same_json(value, reference["value"]):
            return False
    return True


def contains_quote(text, quote):
    return isinstance(quote, str) and bool(quote.strip()) and quote in text


def delivery_finding_error(check, requirement, answer, payload):
    if check["passed"] is not False:
        return None
    if not contains_quote(requirement, check["requirement_quote"]):
        return "InvalidRequirementLocation"
    kind = check["failure_kind"]
    if kind is None:
        return "MissingFailureKind"
    if kind != "missing_delivery" and not contains_quote(answer, check["answer_quote"]):
        return "InvalidFindingLocation"
    if kind == "contradicted" and not valid_counterevidence(check["counterevidence"], payload):
        return "InvalidCounterevidence"
    return None


def retain_unverified_finding(check, error):
    """Keep the model's negative decision auditable; an invalid negative is not a pass."""
    check.update(passed=None, reported_passed=False, reported_reason=check["reason"],
                 validation_error=error, reason=f"Judge negative finding failed citation validation: {error}")
