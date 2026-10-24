# LangChain + LangGraph 集成

当前生产聊天链路是**轻量 LangGraph 工具循环**，不再使用 `finish`、evidence ID、`compute_result`、`read_evidence` 或输入任务编译器。

## 请求链路

```text
POST /api/agents/chat
POST /api/agents/chat/stream
  -> app/routers/react_chat.py：创建 durable run、绑定 owner/session、写入 journal
  -> app/agents/chat.py：唯一公开入口
  -> app/agents/runner/adapter.py：
       轻量输入安全及交易指令审查
       AgentRunner
       配置条件/全量语义复核模式（实际复核统一在 AgentRunner 内）
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
| `app/services/agent_tracing.py` | 可选 Langfuse trace，fail-open，不参与业务判断 |

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

完整结果直接进入 ToolMessage，Runner 不再额外截取前 50 行。集合只保留一份 rows，均线、回撤、筛选规则、覆盖率等元数据同步保留；非集合报告保持完整结构。来源截断与关键缺口会传播为 partial。工具层提供带峰谷日期的收盘回撤与高低价回撤、候选统计，模型优先引用确定性结果并输出 Markdown 表格。

## 边界

- 日期：模型显式写入工具参数；工具返回 `data_as_of`，与请求不一致时拒绝。
- 数据缺失：`data_missing`、`source_truncated`、`truncated` 由工具显式返回。
- 财务口径：`stock_financials` 的 quarterly 主字段是年初至今累计，`*_single_quarter` 是服务端推算的单季值；上一季度缺失时不推算。`report_date` 是来源报告日期，不等同于原始公告日。该工具当前只在 `extended` profile 开放。
- 指数数据：`index_search` 负责名称/代码消歧，宽基指数保留 `resolved` 与同名 `variants`；`index_quote` 的 `days` 指最近 N 个有日线的交易日，`data_as_of` 可能早于请求截止日；`index_constituents` 只返回代码和名称，不含权重、行业和市值，不得自行补造。三个指数工具当前只在 `extended` profile 开放。
- 合规：关键词仅触发条件语义复核，不直接拒绝否定声明或历史引用；真实违规或复核失败不发布候选回答。可配置 `LIMITUPLAB_AGENT_COMPLIANCE=llm` 对未复核的其余回答也做语义复核。
- 输入安全：语义审查提示注入、权限绕过与直接交易指令；正常历史研究允许，拒绝发生在业务工具查询之前，不生成输出合同。
- 历史复盘：候选 start_date/end_date 与后续观察 as_of_date 分开；聊天工具默认观察到本地最新交易日，显式历史截止仍可复现。报告分别标注候选表现分类、特征分组和实际来源/阶段。
- 输出完整性：读取模型 finish_reason；length 仅允许一次同证据、禁止新增工具的精简重写，仍失败则明确未完成并返回 partial/error，不能把半句作为 complete。恢复仍受原有轮次和时限预算约束。
- 运行恢复：journal 负责幂等、取消、租约和最终答案原子保存；SSE 进度和最终结果可重连读取。
  worker 丢失后重新提交同一 `message_id` 会复用 run 记录，但从第一轮重跑，工具结果不跨进程复用；
  `checkpoint_json` / `react_calls` 当前没有写入方，不构成断点续跑。
- 观测：LLM 调用、工具执行与 run 级 trace 见 [Langfuse 接入](Langfuse_Integration.md)。
- 评测：Golden/quality 评测框架已删除，后续评测集重新设计。
