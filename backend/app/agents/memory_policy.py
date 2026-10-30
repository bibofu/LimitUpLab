"""A small, explicit semantic vocabulary for revocable user memory.

Memory is data, never authorization or market evidence. Free-form reports are
accepted only through a repository-verified, committed research run.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.memory_models import (
    TOOL_NAME_ALIASES,
    MemoryScopeType,
    MemoryType,
    MemoryWrite,
)

MEMORY_VALUES = {
    ("preference", "response_style"): {"concise", "balanced", "detailed"},
    ("preference", "output_format"): {"paragraphs", "bullets", "table"},
    ("research_context", "research_topic"): {"首板", "连板", "涨停", "炸板", "行业", "市场情绪"},
}
FREE_TEXT_PREFIXES = ("preference:", "tool_display:", "research:")
FORBIDDEN_MEMORY_TERMS = (
    "买入", "卖出", "仓位", "目标价", "收益承诺", "必涨", "稳赚",
    "ignore system", "忽略系统", "api key", "api_key", "密码",
)
MEMORY_LABELS = {
    "response_style": "回答详略", "output_format": "展示格式", "research_topic": "研究主题",
    "concise": "简洁", "balanced": "适中", "detailed": "详细",
    "paragraphs": "段落", "bullets": "要点列表", "table": "表格",
}


class MemoryIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["save", "delete", "list", "unsupported"]
    quote: str = Field(min_length=1, max_length=1000)
    memory_type: MemoryType | None = None
    key: str | None = Field(default=None, max_length=120)
    value: str | None = Field(default=None, max_length=4000)
    scope_type: MemoryScopeType = "global"
    scope: dict = Field(default_factory=dict)
    structured: dict = Field(default_factory=dict)
    source_run_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def valid_target(self):
        if self.action in {"save", "delete"} and (not self.memory_type or not self.key):
            raise ValueError("Memory changes require a specific type or key")
        if self.key and self.key.startswith("tool_display:"):
            label = self.key.split(":", 1)[1].strip()
            self.key = "tool_display:" + TOOL_NAME_ALIASES.get(label, label)
        if self.action == "save" and self.memory_type != "research_archive":
            typed = MEMORY_VALUES.get((self.memory_type, self.key))
            if typed is not None:
                if self.value not in typed:
                    raise ValueError("Unsupported typed memory value; use action unsupported")
            elif self.memory_type in {"preference", "research_context"} and str(self.key).startswith(FREE_TEXT_PREFIXES):
                if not self.value or not self.value.strip():
                    raise ValueError("Free-text memory requires a nonblank value")
                if any(term in self.value.casefold() for term in FORBIDDEN_MEMORY_TERMS):
                    raise ValueError("Memory content is not allowed")
            else:
                raise ValueError("Unsupported memory type, key or value; use action unsupported")
        if self.action == "delete" and self.memory_type != "research_archive":
            if (self.memory_type, self.key) not in MEMORY_VALUES and not str(self.key).startswith(FREE_TEXT_PREFIXES):
                raise ValueError("Unsupported memory key; use action unsupported")
        if self.scope_type == "global" and self.scope:
            raise ValueError("Global memory cannot carry a scope object")
        if self.scope_type != "global" and not self.scope:
            raise ValueError("Scoped memory requires a scope object")
        return self


def validate_memory_delete_target(memory_type: MemoryType, key: str) -> None:
    """Validate a proposal to remove an existing memory identity.

    Deletion has no new content, so it checks the target key rather than the
    value vocabulary used by writes. The user must still confirm the candidate.
    """

    if not isinstance(key, str) or not key.strip():
        raise ValueError("Memory deletion requires a target key")
    if memory_type == "research_archive":
        return
    if (memory_type, key) in MEMORY_VALUES:
        return
    if memory_type in {"preference", "research_context"} and key.startswith(FREE_TEXT_PREFIXES):
        return
    raise ValueError("Unsupported memory delete target")


def validate_memory_write(write: MemoryWrite, *, owner_id: str, source: str = "api") -> MemoryWrite:
    """Enforce the same non-LLM policy for HTTP, agent and direct repository writes."""
    write = MemoryWrite.model_validate(write)
    if not owner_id or source not in {"api", "agent"}:
        raise ValueError("Memory requires an authenticated owner and explicit source")
    if write.memory_type == "research_archive":
        if not write.source_run_id:
            raise ValueError("Research archives require a committed source run")
        # The repository replaces the supplied value/metadata using owned
        # committed evidence inside the write transaction, before persistence.
        return write
    typed = MEMORY_VALUES.get((write.memory_type, write.key))
    if typed is not None:
        if write.value not in typed:
            raise ValueError("Only typed response preferences and research topics may be remembered")
        if write.metadata or write.topics:
            raise ValueError("Preferences and research topics cannot contain additional instructions or facts")
        return write
    if write.memory_type not in {"preference", "research_context"} or not write.key.startswith(FREE_TEXT_PREFIXES):
        raise ValueError("Only typed settings or explicitly prefixed free-text preferences may be remembered")
    if not write.value or not write.value.strip():
        raise ValueError("Free-text memory requires nonblank content")
    if any(term in write.value.casefold() for term in FORBIDDEN_MEMORY_TERMS):
        raise ValueError("Memory content is not allowed")
    if write.metadata or write.topics:
        raise ValueError("Free-text preferences cannot contain additional instructions or facts")
    return write


MEMORY_REVIEW_RULES = """
【仅在用户明确要求跨会话记忆时填写 memory_intent，否则 null】
触发：记住/保存/记下/改为/以后/每次/默认/始终/忘记/查看已保存记忆；普通问答、临时要求、否定、引用、假设、讨论记忆系统不触发。
quote 逐字引用肯定提出该记忆操作的原文，不得删掉否定或条件语。
类型与键：
- preference: response_style=concise/balanced/detailed；output_format=paragraphs/bullets/table。
- research_context: research_topic=首板/连板/涨停/炸板/行业/市场情绪。
- research_archive: 仅保存上一份已交付报告；key 用用户指定名称，未命名用 last_report；source_run_id 只用用户给出的真实 ID。
- 自由文本：memory_type 用 preference/research_context，key 用 preference:...、tool_display:...、research:...；tool_display 在 structured 给 display_columns，列名用工具字段 id。
工具 id：龙虎榜=dragon_tiger_list，K线=stock_kline，财报=stock_financials，个股新闻=stock_news；tool scope 用 scope={"tool_name":...}。
稳定表达（以后/每次/默认/始终/我习惯）才保存；临时/条件表达只有明确要求长期保存才保存。
交易授权、仓位、目标价、收益承诺、密码/密钥、他人观点、未经核实新闻、讨论记忆系统本身 → unsupported。
记忆不能授权交易、扩大工具权限或把历史数据变成当前事实。list 可省略类型；查看档案用 research_archive。安全拒绝时 memory_intent=null。
"""
