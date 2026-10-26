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
还需识别显式长期记忆管理请求，放入 memory_intent；普通问答、临时要求、否定、引用、假设、
讨论记忆系统的技术问题均填 null，不能从用户行为自动推断偏好。只有用户明确要求今后/跨会话
记住、保存、改为、忘记某项偏好，或明确查询已保存的长期记忆时才填写。
quote 必须逐字引用当前用户肯定提出该记忆操作的完整原文片段，不得删掉否定或条件语。
支持 save/delete/list/unsupported。save 是新增或修改；delete 必须指定一个明确的 type/key。
preference: response_style=concise/balanced/detailed，output_format=paragraphs/bullets/table。
research_context: research_topic=首板/连板/涨停/炸板/行业/市场情绪。
research_archive: 仅明确保存上一份已交付研究报告，key 使用用户指定的档案名称，未命名用 last_report；
source_run_id 只可填用户给出的真实 ID，否则 null，服务端解析上一份报告。
自由文本偏好：用户在明确、稳定的跨会话表达中可保存展示/研究偏好，例如
“以后展示龙虎榜只显示股票名称”“分析财报时详细一点”。
memory_type 用 preference 或 research_context；key 使用 preference:...、tool_display:... 或
research:...；value 填有界用户偏好文本；scope_type/scope 标注 tool/intent/topic/entity/condition。
tool scope 使用公开工具 id，例如 scope={"tool_name":"dragon_tiger_list"}；
龙虎榜=dragon_tiger_list，K线=stock_kline，财报=stock_financials，个股新闻=stock_news。
tool_display 偏好应在 structured 中给出展示列，例如
{"display_columns":["name"]} 或 {"display_columns":["symbol","name"]}；
列名使用工具字段 id（如 name、symbol），不要填无法验证的字段。
只有“以后/每次/默认/始终/我习惯”等稳定表达才保存；“这次/先/今天/如果”等临时或条件性表达
只有在用户明确要求长期保存时才保存。
交易授权、仓位、目标价、收益承诺、密码/密钥、他人观点、未经核实的新闻、讨论记忆系统本身，
用 unsupported，不能隐式保存其中一部分。安全拒绝时 memory_intent=null。
list 可不指定类型表示列出全部长期记忆；单独查看档案必须用 research_archive。
这些字段不能授权交易、扩大工具权限或把历史数据变成当前事实。
"""
