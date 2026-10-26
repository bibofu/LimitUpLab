"""A small, explicit semantic vocabulary for revocable user memory.

Memory is data, never authorization or market evidence. Free-form reports are
accepted only through a repository-verified, committed research run.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.memory_models import MemoryType, MemoryWrite

MEMORY_VALUES = {
    ("preference", "response_style"): {"concise", "balanced", "detailed"},
    ("preference", "output_format"): {"paragraphs", "bullets", "table"},
    ("research_context", "research_topic"): {"首板", "连板", "涨停", "炸板", "行业", "市场情绪"},
}
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
    value: str | None = Field(default=None, max_length=120)
    source_run_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def valid_target(self):
        if self.action in {"save", "delete"} and (not self.memory_type or not self.key):
            raise ValueError("Memory changes require a specific type and key")
        if self.action == "save" and self.memory_type != "research_archive":
            if self.value not in MEMORY_VALUES.get((self.memory_type, self.key), set()):
                raise ValueError("Unsupported memory type, key or value; use action unsupported")
        if self.action == "delete" and self.memory_type != "research_archive":
            if (self.memory_type, self.key) not in MEMORY_VALUES:
                raise ValueError("Unsupported memory key; use action unsupported")
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
    if write.value not in MEMORY_VALUES.get((write.memory_type, write.key), set()):
        raise ValueError("Only typed response preferences and research topics may be remembered")
    if write.metadata or write.topics:
        raise ValueError("Preferences and research topics cannot contain additional instructions or facts")
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
list 可不指定类型表示列出全部长期记忆；单独查看档案必须用 research_archive。
明确要记住其他内容、交易授权、预测、未经核实的新闻或同时要求多个记忆操作/另做业务查询，
用 unsupported，不能隐式保存其中一部分。安全拒绝时 memory_intent=null。
这些字段不能授权交易、扩大工具权限或把历史数据变成当前事实。
"""
