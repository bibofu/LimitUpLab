# LangChain + LangGraph 集成

当前聊天链路是**LangGraph 持久化工具循环**，不再使用 `finish` 控制工具、evidence ID、`compute_result`、`read_evidence` 或输入任务编译器。Runtime 与短期 Memory 的状态契约、迁移和验证边界见 [首轮重构记录](Memory_Runtime_Refactoring.md)。

## 请求链路

```text
POST /api/agents/chat
POST /api/agents/chat/stream
  -> app/routers/react_chat.py：注册 run、绑定 owner/session、准入与执行租约
  -> app/agents/chat.py：唯一公开入口
  -> app/agents/runner/adapter.py：
       轻量输入安全及交易指令审查
       AgentRunner
       配置条件/全量语义复核模式（实际复核统一在 AgentRunner 内）
       映射为 AgentChatResponse
  -> app/agents/runner/durable_runtime.py：持久化 input_review -> agent <-> tool
  -> app/agents/runner/runner.py：模型调用、工具执行、预算与合规检查
  -> app/agents/runner/gateway.py：BusinessToolGateway 校验和执行业务工具
  -> app/agents/tools.py：工具实现与真实数据源
```

## 生产模块

| 模块 | 职责 |
| --- | --- |
| `app/agents/runner/durable_runtime.py` | 原生 checkpoint、恢复、线程状态与运行依赖注入 |
| `app/agents/runner/runner.py` | 模型/工具步骤、预算、取消、直接回答 |
| `app/agents/runner/gateway.py` | 业务工具 Schema、日期回显、单股票约束和执行 |
| `app/agents/runner/tool_view.py` | 把工具结果转成模型可读的 rows + field_definitions |
| `app/agents/runner/security.py` | 轻量输入安全审查，不解析任务/日期/交付合同 |
| `app/agents/runner/prompt.py` | 约 10 条规则的精简系统提示 |
| `app/agents/runner/guardrails.py` | 预算、状态映射和确定性合规守卫 |
| `app/agents/run_lifecycle.py` | RunLifecycle 薄门面、取消与执行所有者上下文 |
| `app/services/agent_run_services.py` | Run Registry、独立事件存储与事务化交付 |
| `app/agents/context_builder.py` | 每次调用的短期上下文、预算与消息合法性 |
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

完整结果持久化为不可变回执和公共 trace，不额外截取前 50 行。模型视图在更小时将 rows 无损转换为 columns/values，保留顺序、全部字段及明确的 null/缺失区别；字段定义和均线、回撤、筛选规则、覆盖率等元数据保留。仍超输入预算时明确 partial，不能悄悄丢行。工具层提供确定性统计，模型据此输出 Markdown 表格。

## 边界

- 日期：模型显式写入工具参数；工具返回 `data_as_of`，与请求不一致时拒绝。
- 数据缺失：`data_missing`、`source_truncated`、`truncated` 由工具显式返回。
- 财务口径：`stock_financials` 的 quarterly 主字段是年初至今累计，`*_single_quarter` 是服务端推算的单季值；上一季度缺失时不推算。`report_date` 是来源报告日期，不等同于原始公告日。该工具当前只在 `extended` profile 开放。
- 指数数据：`index_search` 负责名称/代码消歧，宽基指数保留 `resolved` 与同名 `variants`；`index_quote` 的 `days` 指最近 N 个有日线的交易日，`data_as_of` 可能早于请求截止日；`index_constituents` 只返回代码和名称，不含权重、行业和市值，不得自行补造。三个指数工具当前只在 `extended` profile 开放。
- 合规：关键词仅触发条件语义复核，不直接拒绝否定声明或历史引用；真实违规或复核失败不发布候选回答。可配置 `LIMITUPLAB_AGENT_COMPLIANCE=llm` 对未复核的其余回答也做语义复核。
- 输入安全：语义审查提示注入、权限绕过与直接交易指令；正常历史研究允许，拒绝发生在业务工具查询之前，不生成输出合同。
- 历史复盘：候选 start_date/end_date 与后续观察 as_of_date 分开；聊天工具默认观察到本地最新交易日，显式历史截止仍可复现。报告分别标注候选表现分类、特征分组和实际来源/阶段。
- 输出完整性：读取模型 finish_reason；length 仅允许一次同证据、禁止新增工具的精简重写，仍失败则明确未完成并返回 partial/error，不能把半句作为 complete。恢复仍受原有轮次和时限预算约束。
- 运行恢复：LangGraph 原生 checkpoint 是执行状态权威来源，`durability="sync"`；业务 Registry、事件与交付独立。新 durable run 在 worker 丢失后恢复未完成步骤，已保存工具回执跨进程复用；缺失回执明确报告不确定，不承诺 exactly-once。历史 `checkpoint_json` / `react_calls` 不导入原生 checkpoint，旧 run 不能视为已具备原生恢复点。
- 观测：LLM 调用、工具执行与 run 级 trace 见 [Langfuse 接入](Langfuse_Integration.md)。
- 评测：旧 Golden/quality 框架已退出；新增 40 条固定 Tool World Memory 行为测试及 PostgreSQL 恢复/交付测试。脚本模型通过不代表真实 LLM 任务质量通过，实际问答需另行验收。
