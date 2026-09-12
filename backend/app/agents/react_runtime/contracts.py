"""Public control contracts. No executable plans or arbitrary expressions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VERSION = "react-runtime-v1"
MAX_MODEL_CALLS = 8
MAX_TOOL_CALLS = 8
MAX_CONTROL_CALLS = 16
MAX_CONCURRENCY = 3


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Requirement(StrictModel):
    id: str
    description: str
    source_text: str
    status: Literal["pending", "satisfied", "unavailable"] = "pending"
    evidence_ids: list[str] = Field(default_factory=list)


class UpdateTask(StrictModel):
    requirements: list[Requirement] = Field(min_length=1, max_length=20)


class Finish(StrictModel):
    status: Literal["complete", "partial", "empty", "clarify", "refuse"]
    answer: str = Field(min_length=1, max_length=16000)
    evidence_ids: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class ReadEvidence(StrictModel):
    evidence_id: str
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=30)


class Filter(StrictModel):
    field: str
    operator: Literal["eq", "ne", "gt", "ge", "lt", "le", "in"]
    value: str | int | float | bool | list[str]


class Compute(StrictModel):
    evidence_id: str
    operation: Literal["select", "intersection", "difference", "union", "aggregate"] = "select"
    other_id: str | None = None
    key: str = "symbol"
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    sort_by: str | None = None
    descending: bool = True
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    group_by: str | None = None
    metric: str | None = None
    aggregate: Literal["count", "sum", "mean", "min", "max"] = "count"


CONTROL_MODELS = {
    "update_task": (UpdateTask, "维护多项交付清单，不执行数据查询。不允许删除用户要求。"),
    "finish": (Finish, "提交最终中文回答、任务状态、证据ID和未完成要求。只能单独调用，等待数据返回后使用。"),
    "read_evidence": (ReadEvidence, "展开本次会话已返回的证据；支持offset分页，不能猜证据ID。"),
    "compute_result": (Compute, "对证据rows确定性筛选、排序、名次切片、交并差集或分组聚合。offset=3,limit=3取第4至6名。字段必须来自实际rows。"),
}
