# Langfuse 观测接入

LLM 调用链的 trace 层，用于定位「这一轮为什么慢、走了哪个工具、token 花在哪」。
它**不替代** `agent_usage_events`（配额与成本账本）和 `agent_runs`（结果与回放）：

- 计费、限流、成本估算只认 `agent_usage_events`；
- Langfuse 只记录调用结构、输入输出、token 用量与错误；
- 两侧 token 必须相等，Langfuse 不单独计算成本。

## 开关

```env
LIMITUPLAB_LANGFUSE_ENABLED=false      # 总开关，默认关闭
LIMITUPLAB_LANGFUSE_SAMPLE_RATE=1.0    # 头部采样，0~1
LIMITUPLAB_LANGFUSE_ENVIRONMENT=local  # 环境标签，缺省回退 LIMITUPLAB_ENVIRONMENT
LANGFUSE_HOST=https://us.cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
```

真实 key 只写 `backend/.env`（已 gitignore），`.env.example` 只留占位符。
关闭或缺少任一凭据时，进程使用 `NullTracer`，不产生网络请求、不改变响应。

## 落点

| 层 | 文件 | 观测名称 |
| --- | --- | --- |
| 进程 | `app/main.py` lifespan | 初始化 / shutdown flush |
| 请求 | `app/routers/react_chat.py` | `agent_run`（AGENT） |
| 请求 | `app/routers/review_overview.py` | `agent_run`（AGENT） |
| 模型 | `app/services/langchain_provider.py` | `react_decision` / `grounded_answer`（GENERATION） |
| 工具 | `app/agents/runner/runner.py` | 工具名（TOOL） |

```text
agent_run                     input=用户问题, output={status, stop_reason}
├── react_decision           输入安全审查
├── react_decision           工具决策（tool_calls）
├── <tool>                   业务查询，input=参数, output=结果
└── react_decision           最终回答，usage=token
```

一次 run 的 trace id 由 `create_trace_id(seed=run_id)` 派生，**同一个 run 重试或恢复不会产生第二条 trace**。
trace 的 `session_id` 是聊天会话，`user_id` 是匿名 owner id，`environment` 来自配置。

## 关键约束

- **线程边界**：聊天和复盘总结都在独立线程执行，ContextVar / OTel context 不跨线程。
  因此 `tracer.run(...)` 必须在 worker 线程内部进入，不能在 HTTP handler 里。
- **fail-open**：客户端构造、span 创建、flush 全部吞异常并降级为 `NullTracer`；
  Langfuse 不可用只会少一条 trace，不会让回答失败。
- **flush**：SDK 后台批量导出，进程退出时 `shutdown()` 统一 flush；
  单次请求结束时不做同步 flush，避免拖慢租约释放。
- **反序列化安全**：输入统一截断为可 JSON 序列化的结构，超长 payload 截到 60k 字符并标注 `truncated`。

## 脱敏与体积

- `mask_trace_data` 在导出前递归替换 `api_key`、`authorization`、`cookie`、`secret` 等键，
  以及 `sk-…`、`pk-lf-…`、`Bearer …` 形态的值；
- 单个字段超过 6k 字符在 provider 侧截断，整体 payload 超过 60k 字符在 tracer 侧截断；
- 采样用 `sample_rate`，聊天链路建议 1.0，后台批处理可调低。

## 验证

```bash
pytest tests/test_agent_tracing.py tests/test_tracing_integration.py tests/test_agent_observability.py -q
```

真实验证：

1. 问三个可被工具直接回答的问题，确认 `task_status=complete`；
2. 响应里的 `trace_url` 与 `agent_runs.output_json.trace_url` 一致；
3. 用 `run_id` 反查 trace，observation 数量应为「1 个 AGENT + N 个 GENERATION + M 个 TOOL」；
4. 把三个 generation 的 `usage_details` 相加，与 `agent_usage_events` 的
   `prompt_tokens / completion_tokens / total_tokens` 完全一致。

排障注意：

- 读回 observation 必须带 `fields=`（如 `basic,io,model,usage`），否则 usage/io 为空；
- 入库有 30–60 秒延迟，刚跑完查不到是正常现象；
- 公开 API 有速率限制，短时间大量读回会返回 429。

## 回滚

把 `LIMITUPLAB_LANGFUSE_ENABLED` 设为 `false` 并重启即可，无需改代码；
如需彻底移除，删除 `app/services/agent_tracing.py` 的调用点与依赖即可，业务逻辑不依赖它。
