# LangChain + LangGraph ReAct 集成

> 当前版本：V1.4 / `react-runtime-v9` / `agent-tools-v2`
> 代码基线：以 `v1.4.0` 标签指向的提交为准

## 当前生产链路

聊天入口已经统一为一个有界 ReAct 循环，不再使用独立 Planner、Capability 路由、Tool Policy 补计划、Fast/Complex 分流或通用模板回答。

```text
POST /api/agents/chat 或 /chat/stream
  -> react_chat.start：owner/session/message_id 隔离、限流、幂等 run
  -> Input Security Reviewer：结构化语义判定，主动攻击拒绝、引用和审计允许
  -> agents.chat.answer_first_board_chat：唯一聊天入口
  -> react_runtime.runtime.GRAPH：Agent -> Policy -> Tools -> Observe -> Gate
  -> LangChain ChatOpenAI.bind_tools：保留 AIMessage / ToolMessage / tool_call_id
  -> StructuredTool / ToolGateway：类型化参数、profile、时态和调用预算校验
  -> AgentToolRegistry：执行结构化市场、评分、复盘和资讯工具
  -> EvidenceStore：保存完整结果，向模型返回有界预览和 evidence_id
  -> finish：提交 status、answer、evidence_ids、Claim Ledger 和 missing
  -> Compliance Critic：独立结构化投资合规判定
  -> Journal：原子持久化回答、run、消息和调用结果
  -> SSE completed / 只读 reconnect / cancel
```

模型每轮可以同时调用互相独立的工具；依赖上一步名单或状态的动作必须等待对应 ToolMessage。集合筛选、排序、交并差和聚合由受限 `compute_result` 完成，不允许模型执行任意 Python、SQL 或表达式。完整行不直接塞回上下文，需要时通过 `read_evidence` 分页读取。

## 组件职责

| 组件 | 当前职责 |
| --- | --- |
| `services/langchain_provider.py` | `ChatOpenAI.bind_tools`、消息调用、请求级 timeout/retry 配置、usage 归一 |
| `agents/react_runtime/runtime.py` | 唯一 StateGraph、预算、节点路由、工具观察和最终门禁 |
| `agents/tools.py` | 26 个工具的唯一公开契约：参数 Schema、时态、集合语义和显式适配模式 |
| `agents/react_runtime/catalog.py` | 从唯一契约生成严格 Pydantic 参数模型和 LangChain `StructuredTool`，启动时检查直接实现签名漂移 |
| `agents/react_runtime/tools.py` | 消费类型化工具，执行 profile、时态、身份解析、调用和证据接入 |
| `agents/react_runtime/evidence.py` | 请求级完整证据、预览、来源、缺失、截断和确定性计算 |
| `agents/react_runtime/contracts.py` | `finish`、`update_task`、`read_evidence`、`compute_result` 类型契约及预算 |
| `agents/react_runtime/compliance.py` | 独立语义合规 Critic；结构化返回交易指令、仓位、目标价、承诺和确定性预测违规 |
| `agents/react_runtime/lifecycle.py` | SQLite run journal、checkpoint、call replay record 和取消标记 |
| `routers/react_chat.py` | worker、同步等待、SSE、重连、取消和原子发布 |
| `services/session_memory.py` | owner/session 隔离的滚动摘要；只用于上下文，不作为市场证据 |

## 为什么使用自定义 StateGraph

LangGraph v1 对简单 Agent 推荐 `langchain.agents.create_agent`。本项目需要在每次工具调用前后保留额外金融边界：profile allowlist、历史时点能力、完整集合语义、证据裁剪、受限计算、调用幂等、取消和原子回答持久化，因此当前使用自定义 `StateGraph`。

这不表示现有实现已经是框架最佳实践。当前图没有直接采用 `MessagesState + ToolNode + LangGraph checkpointer/thread_id`；预算、证据和 durable journal 由项目代码维护。是否迁移应以能否完整保留上述金融语义为判断标准，而不是只追求 API 形式一致。相关技术债和优先级见 [代码质量审查](code-quality-audit.md)。

## 模型与配置

生产聊天必须使用：

```dotenv
LIMITUPLAB_LLM_ENABLED=true
LIMITUPLAB_LLM_BACKEND=langchain
LIMITUPLAB_LLM_BASE_URL=https://api.deepseek.com
LIMITUPLAB_LLM_MODEL=deepseek-v4-flash
LIMITUPLAB_LLM_NATIVE_FUNCTION_CALLING=true
LIMITUPLAB_LLM_MAX_ATTEMPTS=2
LIMITUPLAB_LLM_TIMEOUT_SECONDS=30
```

配置入口只接受 `LIMITUPLAB_LLM_BACKEND=langchain`，其他值会在服务启动时失败，不会进入 ReAct 后连续产生 provider error。旧 `OpenAIChatCompletionsProvider` 类仍保留给明确的文本/Judge 消费者直接构造和依赖注入，但不再能通过全局环境配置成为生产聊天 Provider；ReAct 入口还会按是否真正实现 `generate_messages` 做第二层能力校验。关闭 LLM 或缺少 API Key 时，确定性数据、评分、回测和独立 Explanation 功能仍可运行；聊天任务不会通过旧通用模板伪装成功。

`AuditedChatOpenAI` 对兼容服务保留两项窄适配：将 SDK 的 `max_completion_tokens` 转回目标服务支持的 `max_tokens`；从流式 chunk 保存原始 usage。升级 `langchain-openai` 时必须运行线协议测试，不能只验证 import。

## 证据、状态与安全边界

- 工具结果区分 `ok`、`empty`、`partial`、`error`；空结果不是异常，缺失或截断不能冒充完整集合。
- 历史会话证据恢复后标记 `historical_reference=true`，不会因为再次计算自动变成当前事实。
- `finish` 必须给出真实任务状态和用户交付缺口；每条事实或推断通过类型化 Claim Ledger 绑定本轮 evidence ID、路径和实际标量值。
- 输入安全与最终投资合规分别使用独立强制 tool call 的结构化语义 Reviewer；失败时关闭，不通过补关键词正则扩展语义边界。
- 外部网页、新闻和历史消息均视为不可信内容，不能修改工具权限或系统边界。
- 系统只做研究解释，不输出买卖、仓位、目标价、收益承诺或确定性预测。
- Claim Ledger 让开放式定性和因果声明可追踪到明确证据字段；路径和值一致不等同于证明因果，相关语义质量仍需要离线 Judge 和真实 Bad Case 回归。

## 运行恢复

每次请求以 `owner_id + session_id + message_id` 建立 durable run。同一请求重复提交会复用已有运行；不同内容复用同一 message ID 会返回冲突。每个 tool call 以 `run_id + call_id + signature` 记录，已知成功结果可恢复，执行结果不确定的远端调用不会自动重发。

SSE GET 重连只读取事件和最终响应，不启动模型或工具。显式再次 POST 同一请求可以从保存的 checkpoint 恢复。取消是协作式的：不再启动新调用，但已经进入底层网络库的请求可能稍后结束。当前 worker 和锁是单进程设计，多 Uvicorn 进程部署前需要数据库租约或外部队列。

## 评测与验证

```powershell
# 完整离线项目验收
backend/.venv/Scripts/python.exe scripts/check_project.py

# 公开 89-case 冻结事实回放
cd backend
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --dataset dev --mode offline --trials 1 --summary-only

# 生产 ReAct + 真实模型 + 冻结工具世界
.\.venv\Scripts\python.exe scripts\run_agent_live_eval.py --case-id LIVE-SIMPLE-002 --trials 1
.\.venv\Scripts\python.exe scripts\run_agent_live_eval.py --trials 3 --judge
```

离线 Chat Eval V2 保留历史报告格式，但自然语言 Query Contract 与 Planner 阶段为 N/A；它只能证明冻结工具事实、Grounding 和答案契约。真实 ReAct runner 记录输入安全判定、原始模型决策、实际业务工具、Claim Ledger、合规判定、任务终态、模型轮数、token 和可选 Judge。公开集、单题 smoke 和浏览器抽查都不能替代私有 Holdout、稳定性与成本发布门槛。

V1.4 标记前完整后端回归为 616 项及 6 个子测试通过，0 失败/跳过；另有 3 条 LangGraph/websockets 依赖弃用警告。版本发布仍需运行标签工作流要求的 Windows/Linux 完整验收。

## 面试讲法

> LimitUpLab 的聊天主链路不是“Planner 生成整张计划再由规则补工具”，而是 LangChain 原生 tool calling 驱动的 LangGraph 有界 ReAct。模型每轮根据用户问题和已有 ToolMessage 决定下一步；后端在每次调用前校验工具权限、参数和历史时点能力，完整结果进入带来源与缺失标记的 EvidenceStore，模型通过 evidence ID 读取或确定性计算。运行过程用 SQLite journal 做幂等、恢复、取消和原子发布。LLM 负责语义决策与解释，评分、集合计算和审计统计仍由确定性代码完成。

同时需要诚实说明：工具公开契约已经收敛并派生为 LangChain `StructuredTool`，但 LangGraph 状态和 checkpoint 仍不是官方 checkpointer 范式，评分分箱仍有硬编码，开放式因果/定性回答也还缺少通用语义门禁。V1.4 的价值是持续收敛生产 ReAct 的执行债务，不是宣称 Agent 质量已经最终达标。

官方参考：[LangGraph v1](https://docs.langchain.com/oss/python/releases/langgraph-v1)、[LangChain Tools / ToolNode](https://docs.langchain.com/oss/python/langchain/tools)、[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。
