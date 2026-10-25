# Agent Runtime 与短期 Memory 首轮重构

记录日期：2026-10-25。本文描述 `memory_refactoring` 分支的首轮实现与验收边界，不表示已发布到生产。首轮覆盖持久化 ReAct、交付/恢复、短期记忆和上下文管理；跨会话长期记忆留到下一轮。

## 目标与职责

```text
FastAPI / SSE
  → RunRegistry：请求幂等、运行状态、owner/session
  → PostgreSQL Lease / Fencing：单一执行所有者
  → LangGraph：input_review → agent ↔ tool → END
      ├─ FencedPostgresSaver / AsyncPostgresSaver：唯一执行状态
      ├─ Context Builder：会话上下文、引用、工具消息和预算
      └─ ToolResultRepository：不可变工具结果回执
  → RunDelivery：正式答案、聊天消息、运行完成状态事务提交
  → completed：仅发布已提交结果

RunEventStore：独立保存进度与重连游标
Langfuse：记录运行、模型、工具与上下文诊断，不承担恢复状态
```

| 模块 | 当前职责 |
| --- | --- |
| `backend/app/agents/runner/durable_runtime.py` | 应用生命周期内编译 Graph；按 thread/run 恢复；新轮清空临时状态 |
| `backend/app/services/durable_runtime_host.py` | FastAPI lifespan 持有异步 Graph 与连接池；Windows 使用独立 SelectorEventLoop |
| `backend/app/services/durable_checkpointer.py` | 原生 PostgreSQL Saver，写事务内校验执行租约及 fencing |
| `backend/app/services/agent_run_services.py` | RunRegistry、RunCoordinator、RunEventStore、RunDelivery |
| `backend/app/agents/run_lifecycle.py` | `RunLifecycle` 薄门面与请求执行控制，不维护自研 checkpoint |
| `backend/app/repositories/tool_result_repository.py` | 按 run/call/参数签名持久化完整结果，恢复不读取可变缓存 |
| `backend/app/agents/context_builder.py` | 每次模型调用重建有限上下文，检查工具消息配对并输出预算诊断 |
| `backend/app/agents/conversation_memory.py` | Token 触发摘要、整轮近期窗口、消息游标与版本 |
| `backend/app/agents/structured_references.py` | 从已提交答案及真实工具行验证显示顺序，解析序号或要求澄清 |
| `backend/app/agents/tool_context.py` | 仅对模型视图做无损列式编码，原始证据不变 |
| `backend/app/services/agent_run_recovery.py` | 有界扫描中断任务，复用正常限流、计费和执行租约流程 |

旧 Journal 类及自定义 checkpoint 写入接口退出当前运行链路。历史 `react_runs.checkpoint_json`、`react_calls` 数据仍保留以便审计，不转换成 LangGraph checkpoint。业务运行表和事件表继续承担请求与交付职责，不是第二套执行状态。

## Graph State 与恢复边界

当前 `RunnerState` 顶层包含 `schema_version/run_id/current_query/conversation`、本轮 `messages/pending/tool_messages`、计数与剩余预算、答案/状态、上下文诊断。`conversation` 保存：

```text
recent_messages, conversation_summary, research_context
active_constraints, active_references
last_committed_message_id, summary_cursor
revision, summary_revision, summarized_message_count
summary_generation_mode, summary_triggered
```

`thread_id=session_id`，每次请求另有 `run_id`。同一会话活动任务由数据库唯一约束与租约保护；新轮显式重置工作消息、待执行工具、轮次计数、答案、引用解析结果和诊断。模型客户端、数据库连接、工具 registry、RunControl 通过 runtime context 注入，不写入 checkpoint。

默认 `durability="sync"`。一个工具调用占一个 super-step，多工具调用中已完成的调用无需在接管后重复。`aget_state()` 与 `aget_state_history()` 用于状态与历史检查；状态 schema 版本不兼容会停止恢复，而非猜测转换。

- 模型调用完成但 checkpoint 未提交时，恢复可能重新调用模型；不承诺 exactly-once 或零重复 token。
- 工具回执已保存但 checkpoint 写入失败时，恢复使用相同回执，不重新查询变化中的缓存。
- 只有 `started` 回执、没有完整结果时，返回 `UncertainExecution` 和显式缺口，不自动重放潜在副作用。
- Graph 已完成而正式交付失败时，读取已保存的 Graph 结果补交；数据库事务保证不会产生两条正式回答。
- 旧 worker 失去租约后不能再提交 checkpoint、工具回执或正式答案。取消会阻止后续工作；已发出的外部请求不保证立即撤回。
- SSE 断线不会取消服务器任务；GET 重连只读事件和正式结果。恢复由同一请求重试或 lifespan 恢复扫描触发，不由 GET 重放工具。

自动恢复默认每 30 秒扫描，单次最多 20 条、每 run 最多 3 次自动接管；缺少原请求准入身份的预留任务留给原 POST 重试。该机制不等同于无限自动重试或已验收的多实例吞吐能力。

## 短期记忆和上下文

原始聊天历史继续保存在业务表。新会话状态读取已提交历史；已有旧摘要只在初次建立 Graph 会话时导入，并携带来源及摘要游标。取消、失败或尚未交付的结果不晋升为股票引用。摘要写回由同一 Graph 执行所有者控制；计算接口支持 `expected_revision`，缺失上次已提交游标时显式报错，避免旧窗口覆盖新进度。

每次模型调用组合系统规则、当前问题、页面信息、有效约束/引用、摘要、近期整轮对话和本轮工具观察。历史上下文作为数据输入，不作为新的系统授权。优先级为：当前明确要求、页面上下文、已提交引用、摘要。当前行情和统计事实必须重新取自本轮工具。

当前预算采用显式标注的估算：ASCII 每 3 字符约 1 token，非 ASCII 每字符约 2 tokens，并计入工具 schema 与消息开销；默认输入预算 24,000。这是保守预算方法，不是供应商计费 token。历史按整轮裁剪，不拆开 assistant tool calls 与对应 tool results；必需上下文或本轮结果仍超限时返回明确 partial，不默默删掉股票。

滚动摘要默认在会话估算超过 6,000 tokens 时触发，保留约 2,400 tokens 的完整近期轮次。摘要复用已有结构化生成与安全降级逻辑；LLM 不可用时降级状态可见。单个最新轮次过大时完整保留并记录延后原因。研究目标、股票、日期、未解问题和约束均进入 Context Builder，不再只加载 `constraints`。

有序引用保留 `source_run_id/trade_date/ordered_symbols/filters/sort_rules/result_hash`。只有答案中的单一 Markdown 表格或逐行名单能与工具股票行唯一匹配时，才支持“第二只”。未知中间行、重复实体、多组名单、越界序号或页面股票冲突都会要求澄清。普通 `stock_mentions` 仍用于展示链接，不承担排名语义。引用解析不建立整轮股票白名单，允许用户明确增加比较对象。

工具列式编码把重复键名转成 `rows_table.columns/values`，保留全部行、顺序、字段值、来源、日期和完整字段定义；缺失字段单独记录索引，区别于明确返回的 `null`。只有编码后更小时才使用，恢复过程无需字段去重缓存。公共 trace 与不可变回执仍保留完整原始结构。

## 数据库与部署迁移

依赖锁定 `langgraph==1.0.5`、`langgraph-checkpoint==3.0.1`、`langgraph-checkpoint-postgres==3.0.5`、`psycopg-pool==3.3.3`。`0005_durable_runtime` 在现有 PostgreSQL 中增加运行状态字段、`agent_tool_results` 和原生 `checkpoints/checkpoint_blobs/checkpoint_writes/checkpoint_migrations`。原生 DDL 固化自 Saver 3.0.5 的迁移 0–9，运行时不会调用 `setup()`。

升级其他 Saver 版本必须先对照其 DDL 与 API，增加 Alembic 迁移并运行恢复回归；不能只更新 pip 依赖。`FencedPostgresSaver` 适配了上游 cursor 事务边界，这也是升级时必须重点复核的耦合点。

部署顺序：

1. 停止接受新聊天任务，等待旧 worker 完成；无法完成的旧任务由用户取消，保留消息与运行记录。旧 run 没有原生 checkpoint，不能承诺从中途升级恢复。
2. 保存并验证 PostgreSQL 备份；在独立恢复库演练升级。不要手工清空 checkpoint 或把旧 `checkpoint_json` 导入新表。
3. 安装锁定依赖，通过已有管理入口运行 Alembic，然后启动应用。未升级的 schema 会使启动检查失败。
4. 验证旧会话读取、连续两轮问答、同 message_id 重试、SSE 重连、取消、重启接管，以及删除会话后的 checkpoint 级联清理。
5. 保留迁移前备份与历史兼容数据；本次迁移禁止破坏性 downgrade，回滚需恢复经验证的备份并使用匹配的旧代码。

从 `backend` 目录执行，连接信息由受保护的 `.env` 或环境变量提供：

```powershell
.venv/Scripts/python.exe scripts/postgres_admin.py backup --output output/backups/pre-memory-runtime.dump
.venv/Scripts/python.exe scripts/postgres_admin.py verify-backup --archive output/backups/pre-memory-runtime.dump
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe scripts/postgres_admin.py upgrade
.venv/Scripts/python.exe scripts/postgres_admin.py status
```

备份文件使用新名称，不提交数据库、报告或凭据。自动化测试在独立 PostgreSQL 测试库及临时 schema 执行。

2026-10-25 已实际升级本机 `127.0.0.1:55433` 的开发业务库至 `0005_durable_runtime`。升级前确认无有效 Agent 执行租约，使用 `pg_dump` 生成备份、校验 SHA-256 与归档可读性；升级后原有 192 个会话、542 条消息计数均未变化，再创建独立验收会话。备份位于忽略目录 `output/memory-refactoring/backups/`。未发布或迁移远程生产环境。

## 已完成验证

截至本记录，已实际执行：

| 验证集 | 结果 | 证据边界 |
| --- | --- | --- |
| 最终功能提交 `1434c72` 后端全量 | 1619 passed、22 subtests passed，0 failed、0 skipped | 独立 PostgreSQL；线程异常视为失败；130.87 秒；仅有 LangChain 弃用警告 |
| 前端测试与生产构建 | 224 tests passed；tsc + Vite build 成功 | 存在既有 bundle 超过 500 kB 的非阻断提示 |
| Context Builder、摘要、有序引用、无损编码、Memory Golden 联合测试 | 87 passed | 47 条模块测试与 40 条固定 Tool World 场景 |
| 生产 Durable Graph、原生 Saver、Memory Golden 联合测试 | 61 passed，1 warning | 9 条 Graph、12 条 Saver、同一组 40 条 Golden；不可与上一行直接相加 |
| 真实 Python 子进程终止后接管 | 已通过 Saver 集成测试 | 已提交 super-step 恢复、旧 fence 写入拒绝 |
| Graph 完成后交付事务故障 | 已通过 Graph 集成测试 | 恢复后无需再次调用模型/工具，正式答案只保存一次 |
| 多工具中断与下一轮隔离 | 已通过 Graph 集成测试 | 只续未完成调用，计数/待执行调用/诊断不污染下一轮 |

上述模块测试均包含在最终全量中，不相加计数。最终后端日志/JUnit 位于忽略目录 `output/validation/20261025T063821Z-464da1ba/`；前端报告位于 `output/validation/20261025T062857Z-d190227a/`。应用 lifespan、同步 worker 桥、原生 Saver、SSE 重连、host 重启与真实关闭时的中断保留均有固定模型的端到端集成测试。

复现（先将 `LIMITUPLAB_TEST_DATABASE_URL` 设为独立测试库，禁止指向业务库）：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_context_builder.py tests/test_conversation_memory.py tests/test_structured_references.py tests/test_tool_context.py tests/test_memory_behavioral_golden.py -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_durable_agent.py tests/test_durable_checkpointer.py tests/test_memory_behavioral_golden.py -q -p no:cacheprovider
```

固定样本经本地 `cl100k_base` 分词器实际计数：

| 样本 | 原始 JSON tokens | 无损编码 tokens | 减少 |
| --- | ---: | ---: | ---: |
| 68 只股票 | 5,516 | 3,612 | 34.5% |
| 60 条 K 线 | 4,636 | 2,972 | 35.9% |

两类样本都验证了 JSON 往返还原、全部行、顺序和 `null`/字段缺失的区别。这是编码尺寸测量，不是生产端到端成本、延迟或模型回答质量提升。Golden 采用脚本模型和固定工具事实，不能代替真实 LLM 验收。

用户明确授权外部模型验收后，通过真实 FastAPI lifespan/HTTP 路由、开发 PostgreSQL 与已配置的 `deepseek-v4-flash` 完成两轮三问；最后一轮使用最终功能代码，三问全部 HTTP 200、`task_status=complete`：

1. “什么是首板”：概念解释完整结束。
2. “2026-10-23 首板名单按代码升序列出前三只”：实际工具行与回答均为 000156 华数传媒、000559 万向钱潮、000700 模塑科技。
3. “第二只的代码和名称”：准确返回 000559 万向钱潮，没有把历史行情当作当前事实。

另外只读核对了 checkpoint 中的有序引用、来源 run、解析结果、每个 run 的唯一正式消息，以及完成后 GET 重连答复一致。三问延迟仅为单次冒烟观测，不作为 P50/P95 或版本性能对比。原始对话报告存于忽略目录 `output/memory-refactoring/`，不提交 Git。

## 明确未完成的范围

- 长期 Memory 的显式保存/更新/删除、跨会话检索、过期和消融评测尚未实现；当前匿名 visitor 也不等同于跨设备账号。
- 没有对模型自然语言回答做全面的语义事实证明；保留工具 Schema、数据缺口、输入安全、合规复核与输出长度检查，开放式解释仍需审阅证据。
- 尚无生产全量 P50/P95、数据库写放大、任务成功率和真实 token 成本对比，不据代码结构宣称性能提升。
- 大结果先做无损编码；仍超预算时显式 partial。按需分页/展开工具不在首轮范围。
- 多实例压测、跨版本 Graph State 自动迁移、长期 checkpoint 保留策略和人机审批恢复需要后续单独验收。

设计依据：[LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)、[Checkpointers 与恢复边界](https://docs.langchain.com/oss/python/langgraph/checkpointers)、[Memory 与消息裁剪](https://docs.langchain.com/oss/python/langgraph/add-memory)。实际 API 与恢复边界以锁定版本源码及集成测试为准。
