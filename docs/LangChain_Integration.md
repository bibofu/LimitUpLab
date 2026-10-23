# LangChain + LangGraph 集成

当前生产聊天链路是**轻量 LangGraph 工具循环**，不再使用 `finish`、evidence ID、`compute_result`、`read_evidence` 或输入任务编译器。

## 请求链路

```text
POST /api/agents/chat
POST /api/agents/chat/stream
  -> app/routers/react_chat.py：创建 durable run、绑定 owner/session、写入 journal
  -> app/agents/chat.py：唯一公开入口
  -> app/agents/runner/adapter.py：
       轻量输入安全审查
       AgentRunner
       可选语义合规复核
       映射为 AgentChatResponse
  -> app/agents/runner/runner.py：LangGraph agent -> tools -> agent
  -> app/agents/runner/gateway.py：BusinessToolGateway 校验和执行业务工具
  -> app/agents/tools.py：工具实现与真实数据源
```

## 生产模块

| 模块 | 职责 |
| --- | --- |
| `app/agents/runner/runner.py` | 两节点 LangGraph 循环、预算、取消、直接回答 |
| `app/agents/runner/gateway.py` | 业务工具 Schema、日期回显、单股票约束和执行 |
| `app/agents/runner/tool_view.py` | 把工具结果转成模型可读的 rows + field_definitions |
| `app/agents/runner/security.py` | 轻量输入安全审查，不解析任务/日期/交付合同 |
| `app/agents/runner/prompt.py` | 约 10 条规则的精简系统提示 |
| `app/agents/runner/guardrails.py` | 预算、状态映射和确定性合规守卫 |
| `app/agents/run_lifecycle.py` | durable run journal、取消和租约 |
| `app/agents/tool_output_contracts.py` | 工具输出模型、字段字典生成 |
| `app/agents/tool_catalog.py` | 从工具契约生成 Pydantic 参数模型和 LangChain StructuredTool |
| `app/agents/tool_payload.py` | 工具 payload / collection 提取 |

## 工具结果

模型直接看到：

```json
{
  "status": "ok",
  "data_as_of": "2026-10-23",
  "source": "local-limit-up-events",
  "rows": [],
  "row_count": 12,
  "truncated": false,
  "field_definitions": {
    "rows": {"board_height": "连板高度，1为首板"},
    "metadata": {"matched_count": "过滤后命中事件数"}
  },
  "data_missing": []
}
```

完整结果直接进入 ToolMessage，不再裁剪成 evidence 预览。模型基于 rows 直接筛选、排序、汇总和输出 Markdown 表格。

## 边界

- 日期：模型显式写入工具参数；工具返回 `data_as_of`，与请求不一致时拒绝。
- 数据缺失：`data_missing`、`source_truncated`、`truncated` 由工具显式返回。
- 合规：默认确定性模式守卫；可配置 `LIMITUPLAB_AGENT_COMPLIANCE=llm` 增加语义复核。
- 输入安全：只拒绝提示注入/权限绕过，不生成输出合同。
- 运行恢复：journal 仍负责幂等、取消、租约和最终答案原子保存；SSE 进度和最终结果保留。
- 评测：Golden/quality 评测框架已删除，后续评测集重新设计。
