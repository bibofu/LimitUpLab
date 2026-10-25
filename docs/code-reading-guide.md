# 代码阅读指南

当前生产 Agent 使用应用生命周期内的单一 LangGraph 持久化工具循环。状态、迁移与验收边界见 [Runtime 与 Memory 重构说明](Memory_Runtime_Refactoring.md)；旧 `react_runtime`、自研执行 Journal 和独立同步 Graph 已删除。

## 推荐阅读顺序

1. `backend/app/agents/chat.py`：唯一公开聊天入口。
2. `backend/app/agents/runner/adapter.py`：Runner 依赖装配、合规模式与响应映射。
3. `backend/app/agents/runner/durable_runtime.py`：`input_review -> agent <-> tool`、记忆管理分支、原生流与恢复；`runner.py` 提供模型/工具节点、预算与取消行为。
4. `backend/app/agents/runner/gateway.py`：业务工具校验与执行。
5. `backend/app/agents/runner/tool_view.py`：工具结果与 `field_definitions`。
6. `backend/app/agents/runner/prompt.py`：系统提示。
7. `backend/app/agents/tool_output_contracts.py`：工具输出模型和字段字典。
8. `backend/app/agents/tool_catalog.py`：从契约生成 Pydantic 参数模型和 LangChain StructuredTool。
9. `backend/app/agents/tool_payload.py`：payload / collection 提取。
10. `backend/app/agents/run_lifecycle.py`：业务生命周期门面；`services/agent_run_services.py` 分担 Registry、EventStore、Delivery，执行状态仅由原生 Saver 保存。
11. `backend/app/agents/tools.py`：业务工具实现；按 profile 开放能力，运行时另提供 `read_tool_result` 读取已保存证据。
12. `backend/app/routers/react_chat.py`：HTTP/SSE、owner、幂等和 worker。
13. `backend/app/agents/context_builder.py`、`conversation_memory.py`、`structured_references.py`：上下文预算、滚动摘要和可核验引用；`services/conversation_summary.py` 只计算摘要。
14. `backend/app/repositories/long_term_memory_repository.py`、`services/memory_retrieval.py`：长期记忆隔离、版本、撤回与召回。
15. `backend/app/agents/result_paging.py`、`repositories/tool_result_repository.py`：不可变工具回执、内容哈希与分页视图。
16. `backend/scripts/evaluate_agent_runtime.py`：独立 PostgreSQL 中运行固定世界的恢复、交付和成本估算评测。

## 关键边界

- Graph host 缺失时新任务返回 503；旧未完成任务不能回退执行。已完成结果仍可幂等重放。
- 小工具结果无损编码；大结果先提供零行 manifest，再按哈希读取需要的行和字段。原始证据完整保存，预算不足明确 partial。
- 日期由模型显式传入，工具返回日期必须与请求一致。
- 输入审查处理安全边界、显式记忆管理和引用意图，不生成旧任务编译器的交付合同。
- 合规关键词仅触发语义复核，可选全量语义复核；不靠关键词覆盖最终答案。
- 新的 Memory Golden、Graph/交付恢复集成测试、消融和评测 CLI 已接入；固定脚本通过不能代替真实问答验收。
