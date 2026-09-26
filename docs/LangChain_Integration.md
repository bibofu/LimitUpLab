# LangChain + LangGraph ReAct 集成

> 当前源码标识：`react-runtime-v26` / `agent-tools-v2`
> 本文描述工作树实现；V1.4 标签的历史发布范围见 [阶段里程碑](V1.4_Milestone.md)。

## 当前生产链路

聊天入口已经统一为一个有界 ReAct 循环，不再使用独立 Planner、Capability 路由、Tool Policy 补计划、Fast/Complex 分流或通用模板回答。

```text
POST /api/agents/chat 或 /chat/stream
  -> react_chat.start：owner/session/message_id 隔离、限流、幂等 run
  -> agents.chat.answer_first_board_chat：唯一聊天入口
  -> react_runtime.runtime.run：Input Reviewer 判定安全、请求类型、追问范围和明确日期
  -> react_runtime.runtime.GRAPH：Agent -> Policy -> Tools -> Observe -> Gate
  -> LangChain ChatOpenAI.bind_tools：保留 AIMessage / ToolMessage / tool_call_id
  -> StructuredTool / ToolGateway：类型化参数、profile、时态和调用预算校验
  -> AgentToolRegistry：执行结构化市场、评分、复盘和资讯工具
  -> EvidenceStore：保存完整结果，向模型返回有界预览和 evidence_id
  -> finish：提交 status、answer、可选 table、evidence_ids 和 missing
  -> 原生 answer chunks：持久化待校验草稿，SSE 实时传给前端
  -> Answer Gate：本轮证据与状态检查、服务端表格渲染
  -> Compliance Critic：独立结构化投资合规判定
  -> Journal：原子持久化最终回答、run 和消息
  -> SSE completed / 只读 reconnect / cancel
```

模型每轮可以同时调用互相独立的工具；依赖上一步名单或状态的动作必须等待对应 ToolMessage。集合筛选、排序、交并差和聚合由受限 `compute_result` 完成，不允许模型执行任意 Python、SQL 或表达式。完整行保存在 EvidenceStore：深入分析可通过 `read_evidence` 分页读取，名单交付则直接引用 `finish.table`，无需模型逐行重写或读完所有分页。

## 组件职责

| 组件 | 当前职责 |
| --- | --- |
| `services/langchain_provider.py` | `ChatOpenAI.bind_tools`、原生消息流、完整工具参数校验、请求级 timeout/retry 配置、usage 归一 |
| `agents/react_runtime/runtime.py` | 唯一 StateGraph、预算、节点路由、工具观察和最终门禁 |
| `agents/tools.py` | 26 个工具的唯一公开契约：参数 Schema、时态、集合语义和显式适配模式 |
| `agents/react_runtime/catalog.py` | 从唯一契约生成严格 Pydantic 参数模型和 LangChain `StructuredTool`，启动时检查直接实现签名漂移 |
| `agents/react_runtime/tools.py` | 消费类型化工具，执行 profile、时态、身份解析、调用和证据接入 |
| `agents/react_runtime/evidence.py` | 请求级完整证据、预览、来源、缺失、截断和确定性计算 |
| `agents/react_runtime/contracts.py` | `finish`、`update_task`、`read_evidence`、`compute_result` 类型契约及预算 |
| `agents/react_runtime/answer_stream.py` | 增量解析单个 `finish` 调用的顶层 `answer`，不展示其他参数或并行调用 |
| `agents/react_runtime/answer_delivery.py` | 管理待校验正文、revision、撤回和表格占位符缓冲 |
| `agents/react_runtime/rendering.py` | 从本轮证据渲染单张表格，检查字段、行数、截断与范围 |
| `agents/react_runtime/compliance.py` | 独立语义合规 Critic；结构化返回交易指令、仓位、目标价、承诺和确定性预测违规 |
| `agents/react_runtime/lifecycle.py` | SQLite run journal、checkpoint、call replay record 和取消标记 |
| `routers/react_chat.py` | worker、同步等待、SSE、重连、取消和原子发布 |
| `services/session_memory.py` | owner/session 隔离的滚动摘要；只用于上下文，不作为市场证据 |

## 为什么使用自定义 StateGraph

本项目需要在每次工具调用前后保留 profile allowlist、历史时点能力、完整集合语义、证据裁剪、受限计算、调用幂等、取消和原子回答持久化，因此当前使用自定义 `StateGraph`。

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

配置入口只接受 `LIMITUPLAB_LLM_BACKEND=langchain`，其他值会在服务启动时失败，不会进入 ReAct 后连续产生 provider error。旧 `OpenAIChatCompletionsProvider` 类仍保留给明确的文本消费者直接构造和依赖注入，但不再能通过全局环境配置成为生产聊天 Provider；ReAct 入口还会按是否真正实现 `generate_messages` 做第二层能力校验。关闭 LLM 或缺少 API Key 时，确定性数据、评分、回测和独立 Explanation 功能仍可运行；聊天任务不会通过旧通用模板伪装成功。

`AuditedChatOpenAI` 对兼容服务保留两项窄适配：将 SDK 的 `max_completion_tokens` 转回目标服务支持的 `max_tokens`；从流式 chunk 保存原始 usage。升级 `langchain-openai` 时必须运行线协议测试，不能只验证 import。

## 证据、状态与安全边界

- 工具结果区分 `ok`、`empty`、`partial`、`error`；空结果不是异常，缺失或截断不能冒充完整集合。
- 默认不向 ReAct 重放历史原文或答案证据。仅在 Input Reviewer 判定为 `follow_up` 时，`context.py` 提取历史实体名称/代码及上一轮实际工具参数用于消歧，所有事实仍需本轮重新查询。
- Memory 默认只传偏好约束；追问时再传研究目标、股票、题材、日期范围和未解问题，均不能作为事实或自动继承的任务。EvidenceStore 的历史域兼容检查仍保留，历史引用及其派生结果不能进入最终引用。
- 工具由原生 tool call 选择。Input Reviewer 的 `requested_date` 成为 `required_date`，只补齐调用中缺省的单日期字段或 `requested_as_of`，再按工具能力及实际返回日期校验；不会补造额外查询。
- 最终回答必须单独调用 `finish`，给出真实状态和用户交付缺口。研究请求的 `complete / partial / empty` 必须引用本轮证据；`empty` 需要真实空结果，工具失败或部分证据不能伪装为空。
- 名单通过 `finish.table` 指定证据和字段，并在正文放置一次 `{{evidence_table}}`。服务端输出实际返回的行，最多一张表、12 列、1000 行；无表时不允许残留占位符。源数据截断且无完整排名范围证明时，不能把该表声明为 `complete`。
- Claim Ledger 已退役，旧 checkpoint 的 `claims` 字段仅兼容忽略。当前 gate 不对每句正文做标量绑定校验；证据引用和表格约束不能证明开放式解释或因果关系。
- 输入安全与最终投资合规分别使用独立强制 tool call 的结构化语义 Reviewer；失败时关闭，不通过补关键词正则扩展语义边界。
- 外部网页、新闻和历史消息均视为不可信内容，不能修改工具权限或系统边界。
- 系统只做研究解释，不输出买卖、仓位、目标价、收益承诺或确定性预测。

## 正文流式交付

`LangChainChatProvider.generate_messages` 使用原生 `AIMessageChunk`。运行时只解码单个 `finish` 调用的顶层 `answer`，完整 JSON 参数在流结束后才允许执行；普通文本、工具参数和思维过程不直接展示。

| SSE 事件 | 语义 |
| --- | --- |
| `accepted` | 返回 `run_id` 和会话标识 |
| `progress` | 更新准备、规划、查询、校验等阶段 |
| `answer_start` | 开始一个递增 revision，标记 `provisional=true` |
| `answer_delta` | 按 Unicode 码点 offset 追加正文 |
| `answer_reset` | 撤回当前草稿，等待新的 revision 或最终结果 |
| `completed` | 返回已原子持久化的权威响应，替换临时正文 |

表格占位符及其后正文先缓冲，待完整声明通过确定性渲染后接入草稿，避免暴露占位符或打乱顺序。正文显示时仍待最终投资合规校验；校验失败会撤回并给模型一次修复机会，取消、超时或调用失败也会清理草稿。前端 `agentAnswerBuffer.ts` 校验偏移和重复片段，并按动画帧合并显示。

## 运行恢复

每次请求以 `owner_id + session_id + message_id` 建立 durable run。同一请求重复提交会复用已有运行；不同内容复用同一 message ID 会返回冲突。每个 tool call 以 `run_id + call_id + signature` 记录，已知成功结果可恢复，执行结果不确定的远端调用不会自动重发。

SSE GET 重连只读取事件和最终响应，不启动模型或工具。运行中从 `after` 游标继续读持久化事件；已完成的运行只返回最终回答，不重播被撤回草稿。前端断线后自动尝试一次 GET 重连。显式再次 POST 同一请求可以从保存的 checkpoint 恢复，清理旧草稿并延续 revision 编号。

取消是协作式的：不再启动新调用，但已经进入底层网络库的请求可能稍后结束。当前 worker 和锁是单进程设计，多 Uvicorn 进程部署前需要数据库租约或外部队列。

## 验证

```powershell
# 完整离线项目验收
backend/.venv/Scripts/python.exe scripts/check_project.py
```

项目验收命令验证代码、前端和构建，不会自动调用真实模型；当次数量、退出码和日志保存在 `output/validation/<运行标识>/`，不使用历史标签的测试数代表当前验收。

流式协议重点参考后端的 `test_langchain_provider.py`、`test_react_answer_stream.py`、`test_react_live_answer.py`、`test_react_http.py`，以及前端的聊天传输和答案缓冲测试。旧聊天评测框架已退役，离线契约回归不等同于真实模型质量验收；预测 Outcome 的 Evaluation Agent 仍保留。

## 面试讲法

> LimitUpLab 的聊天主链路不是“Planner 生成整张计划再由规则补工具”，而是 LangChain 原生 tool calling 驱动的 LangGraph 有界 ReAct。模型每轮根据用户问题和已有 ToolMessage 决定下一步；后端在每次调用前校验工具权限、参数和历史时点能力，完整结果进入带来源与缺失标记的 EvidenceStore，模型通过 evidence ID 读取或确定性计算。运行过程用 SQLite journal 做幂等、恢复、取消和原子发布。LLM 负责语义决策与解释，评分、集合计算和审计统计仍由确定性代码完成。

同时需要诚实说明：工具公开契约已经收敛并派生为 LangChain `StructuredTool`，但 LangGraph 状态和 checkpoint 仍不是官方 checkpointer 范式，评分分箱仍有硬编码，开放式因果/定性回答也还缺少通用语义门禁。V1.4 的价值是持续收敛生产 ReAct 的执行债务，不是宣称 Agent 质量已经最终达标。

官方参考：[LangGraph v1](https://docs.langchain.com/oss/python/releases/langgraph-v1)、[LangChain Tools / ToolNode](https://docs.langchain.com/oss/python/langchain/tools)、[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。
