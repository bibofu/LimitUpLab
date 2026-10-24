# 代码阅读指南

当前生产 Agent 链路是轻量 LangGraph 工具循环；旧 `react_runtime`、EvidenceStore、`finish`、`compute_result` 和 Golden/quality 评测代码已删除。

## 推荐阅读顺序

1. `backend/app/agents/chat.py`：唯一公开聊天入口。
2. `backend/app/agents/runner/adapter.py`：输入安全、Runner 调用、合规和响应映射。
3. `backend/app/agents/runner/runner.py`：LangGraph `agent -> tools -> agent` 循环、预算和取消。
4. `backend/app/agents/runner/gateway.py`：业务工具校验与执行。
5. `backend/app/agents/runner/tool_view.py`：工具结果与 `field_definitions`。
6. `backend/app/agents/runner/prompt.py`：系统提示。
7. `backend/app/agents/tool_output_contracts.py`：工具输出模型和字段字典。
8. `backend/app/agents/tool_catalog.py`：从契约生成 Pydantic 参数模型和 LangChain StructuredTool。
9. `backend/app/agents/tool_payload.py`：payload / collection 提取。
10. `backend/app/agents/run_lifecycle.py`：durable run journal、取消和租约。
11. `backend/app/agents/tools.py`：25 个业务工具实现（默认 v1_close_review 开放 24 个，extended 开放全部 25 个）。
12. `backend/app/routers/react_chat.py`：HTTP/SSE、owner、幂等和 worker。

## 关键边界

- 生产只依赖轻量 Runner；不存在生产可切换的 legacy runtime。
- 工具结果直接进入模型上下文，不再有 evidence ID / 预览分页 / compute DSL。
- 日期由模型显式传入，工具返回日期必须与请求一致。
- 输入审查是安全分类，不再生成 `TaskInterpretation` / `OutputContract` / `DateScope`。
- 合规默认确定性守卫，可选语义复核。
- 评测框架已删除，后续重新设计评测集。
