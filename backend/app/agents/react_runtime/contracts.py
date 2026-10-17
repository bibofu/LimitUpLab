"""Public control contracts. No executable plans or arbitrary expressions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VERSION = "react-runtime-v40"
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


class EvidenceColumn(StrictModel):
    field: str = Field(min_length=1, description="Exact scalar field in evidence rows; no expressions")
    label: str = Field(min_length=1, max_length=40, description="User-readable Chinese column title, never a raw field name")


class EvidenceTable(StrictModel):
    evidence_id: str
    columns: list[EvidenceColumn] = Field(min_length=1, max_length=12)


class Finish(StrictModel):
    # Older checkpoints may still contain the retired Claim Ledger. Ignore it so
    # in-flight runs can finish after upgrading without exposing it in the schema.
    model_config = ConfigDict(extra="ignore")

    status: Literal["complete", "partial", "empty", "clarify", "refuse"] = Field(
        description="User-deliverable status, not a copy of the tool result state. complete: all allowed research was answered, including a supported no-record existence answer or a count of zero. empty: the user requested matching records/a list, and a successful, complete query found none. partial: an allowed deliverable remains unresolved; unavailable, stale or incomplete evidence cannot prove absence. clarify: required user input is missing. refuse: only disallowed advice was requested."
    )
    answer: str = Field(min_length=1, max_length=16000,
        description="Candidate Chinese Markdown, published only after validation and persistence. Serialize as a JSON string exactly once: after JSON decoding paragraph breaks must be actual newlines, not literal backslash-n characters. Keep intentional backslashes in code or paths unchanged.")
    table: EvidenceTable | None = Field(default=None, description="At most ONE evidence table for the entire answer, only when delivering a list. Render all RETURNED rows, not necessarily the full source universe: check source_truncated and any proven rank scope. With table, put {{evidence_table}} exactly once on its own unindented line outside code fences, with blank lines separating surrounding prose; without table, no placeholder. Never transcribe rows or omit a user-required list. Select/filter/sort with compute_result first.")
    evidence_ids: list[str] = Field(default_factory=list, description="Current-run evidence IDs. Error evidence can support only a truthful partial failure explanation, never market values, a count of zero or absence. Do not query unrelated data merely to obtain an ID.")
    missing: list[str] = Field(default_factory=list, description="A JSON ARRAY of quoted strings, never bare prose, a date/number or a single string. Use [] for no gaps, otherwise one quoted string per gap inside square brackets. Only specific unmet ALLOWED research deliverables; never blank items. Explicitly refused trading advice is not missing research: when the allowed research is delivered, use complete with an empty list. partial requires a real allowed-research gap. Pure trading-advice refusal uses refuse. Irrelevant missing source fields are caveats, not unfinished tasks.")


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
    operation: Literal["select", "intersection", "difference", "union", "distinct", "aggregate"] = "select"
    other_id: str | None = None
    key: str = "symbol"
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    sort_by: str | None = None
    descending: bool = True
    offset: int = Field(default=0, ge=0)
    limit: int | None = Field(default=None, ge=1, le=100,
        description="Omit or null to retain the full computed evidence. Set a limit only for an explicit requested row/rank window; evidence previews are independently paginated.")
    group_by: str | None = None
    metric: str | None = None
    aggregate: Literal["count", "sum", "mean", "min", "max"] = "count"


CONTROL_MODELS = {
    "update_task": (UpdateTask, "维护多项交付清单，不执行数据查询。不允许删除用户要求。"),
    "finish": (Finish, "提交最终中文回答、任务状态、可选证据ID和未完成要求；只能单独调用，等待数据返回后使用。"),
    "read_evidence": (ReadEvidence, "展开本次会话已返回的证据；支持offset分页，不能猜证据ID。"),
    "compute_result": (Compute, "对证据rows确定性筛选、排序、名次切片、交并差集或分组聚合。省略limit或设null保留完整计算结果，预览分页不裁剪证据；只为明确数量或排名窗口设置limit，offset=3,limit=3取第4至6名。字段必须来自实际rows。集合按key去重，重复成员保留左侧首条记录，不合并两侧指标；来源缺失、截断及历史引用标记沿计算链保留。"),
}
