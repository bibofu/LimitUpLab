# Agent Runtime 与 Memory 重构

记录日期：2026-10-25。本文描述 `memory_refactoring` 分支的实现与验收边界，不表示已发布到远程生产环境。首轮覆盖持久化 ReAct、交付/恢复、短期记忆和上下文管理；第二轮增加显式、可撤回的跨会话长期记忆；第三轮清理旧执行与摘要写入链路，补充原生 Streaming、查询条件来源和大结果分页。

## 目标与职责

```text
FastAPI / SSE
  → RunRegistry：请求幂等、运行状态、owner/session
  → PostgreSQL Lease / Fencing：单一执行所有者
  → LangGraph：input_review → agent ↔ tool → END
      ├─ 显式记忆管理：input_review → memory → END
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
| `backend/app/services/conversation_summary.py` | 无数据库依赖的摘要生成与降级；不管理 owner、会话、游标或写入 |
| `backend/app/agents/structured_references.py` | 从已提交答案及真实工具行验证显示顺序，解析序号或要求澄清 |
| `backend/app/agents/tool_context.py` | 仅对模型视图做无损列式编码，原始证据不变 |
| `backend/app/agents/result_paging.py`、`services/agent_result_reader.py` | 不可变工具回执的 manifest、分页、字段投影及 owner/run 读取边界 |
| `backend/app/services/agent_stream.py` | 原生 Graph custom/updates 到公开进度事件的白名单映射 |
| `backend/app/services/agent_run_recovery.py` | 有界扫描中断任务，复用正常限流、计费和执行租约流程 |
| `backend/app/agents/memory_policy.py`、`memory_commands.py` | 有限类型策略、显式管理意图、独立 Graph 管理节点 |
| `backend/app/repositories/long_term_memory_repository.py` | owner 隔离、版本比较、操作回执、过期、删除墓碑和历史档案 |
| `backend/app/services/memory_retrieval.py`、`memory_scope.py` | 每次调用重新检索、相关性预算、旧上下文撤回隔离 |

旧 Journal 类及自定义 checkpoint 写入接口退出当前运行链路。历史 `react_runs.checkpoint_json`、`react_calls` 数据仍保留以便审计，不转换成 LangGraph checkpoint。业务运行表和事件表继续承担请求与交付职责，不是第二套执行状态。

第三轮删除 HTTP 的 legacy 执行回退和 direct-call Graph。运行时不可用返回 503，不创建新的 legacy run；旧未完成任务重试返回 409，需取消后以新 message_id 提交；旧已交付结果仍可读取。`services/session_memory.py` 的旧 prepare/refresh 写 API、`ChatMemoryRepository.save_memory`、按消息条数触发的配置及旧写入测试均已移除。旧 `chat_session_memories` 表保留，只通过只读连接在首次建立 Graph 会话时导入，之后不能覆盖 Graph 的摘要进度。`ChatSessionMemory` 仅作为旧记录读取 DTO，新摘要使用不含持久化身份的 `ConversationSummary`。

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

Graph 通过 `astream(stream_mode=["custom", "updates"], durability="sync")` 驱动。Stream Bridge 只公开允许的 status、tool_start、tool_end、warning 及固定状态文字，独立 Event Store 保留游标；不公开原始 Graph State、工具参数、模型中间文字或候选答案。传输层提供 error；completed 仍只能来自正式交付后的数据库响应，不能把某个 node 的 done 当成交付完成。旧 progress 事件保持读取兼容。

## 短期记忆和上下文

原始聊天历史继续保存在业务表。新会话状态读取已提交历史；已有旧摘要只在初次建立 Graph 会话时导入，并携带来源及摘要游标。取消、失败或尚未交付的结果不晋升为股票引用。摘要写回由同一 Graph 执行所有者控制；计算接口支持 `expected_revision`，缺失上次已提交游标时显式报错，避免旧窗口覆盖新进度。

每次模型调用组合系统规则、当前问题、页面信息、有效约束/引用、摘要、近期整轮对话和本轮工具观察。历史上下文作为数据输入，不作为新的系统授权。优先级为：当前明确要求、页面上下文、已提交引用、摘要。当前行情和统计事实必须重新取自本轮工具。

当前预算采用显式标注的估算：ASCII 每 3 字符约 1 token，非 ASCII 每字符约 2 tokens，并计入工具 schema 与消息开销；默认输入预算 24,000。这是保守预算方法，不是供应商计费 token。历史按整轮裁剪，不拆开 assistant tool calls 与对应 tool results；必需上下文或本轮结果仍超限时返回明确 partial，不默默删掉股票。

滚动摘要默认在会话估算超过 6,000 tokens 时触发，保留约 2,400 tokens 的完整近期轮次。摘要复用已有结构化生成与安全降级逻辑；LLM 不可用时降级状态可见。单个最新轮次过大时完整保留并记录延后原因。研究目标、股票、日期、未解问题和约束均进入 Context Builder，不再只加载 `constraints`。

有序引用保留 `source_run_id/trade_date/ordered_symbols/filters/sort_rules/result_hash`。只有答案中的单一 Markdown 表格或逐行名单能与工具股票行唯一匹配时，才支持“第二只”。未知中间行、重复实体、多组名单、越界序号或页面股票冲突都会要求澄清。普通 `stock_mentions` 仍用于展示链接，不承担排名语义。引用解析不建立整轮股票白名单，允许用户明确增加比较对象。

第三轮新增 `query_scope`：从已校验调用参数和工具实际归一化输入构造有效 arguments，并保留 explicit_arguments；它随不可变回执保存。引用的 filters 和 sort_rules 从这个来源产生，保留排序方向；旧结果缺少 scope 时明确标记 unknown，“继续按上次条件”会澄清，不从摘要猜参数。同名单、同日期但不同查询范围也不能默默合并。分页读取只是原回执视图，不生成第二份股票引用。

会话中另外保存带 `source_run_id/source_message_id/reference_id/tool/key/value` 的结构化条件。其 origin 为 committed_tool_scope、application 为 reference_followup：它描述上一查询范围，只在继续该查询或明确沿用条件时使用，不把模型工具参数或默认值冒称为用户偏好。摘要不能重写这些条件；长期记忆撤回时，仍被 active references 精确支撑的条件会保留，无法证明来源的旧自由文本仍保守重建。本轮明确要求优先。

工具列式编码把重复键名转成 `rows_table.columns/values`，保留全部行、顺序、字段值、来源、日期和完整字段定义；缺失字段单独记录索引，区别于明确返回的 `null`。只有编码后更小时才使用，恢复过程无需字段去重缓存。公共 trace 与不可变回执仍保留完整原始结构。

大结果在无损编码后仍超过 6,000 估算 tokens 时，模型收到明确的 manifest，包含 result_id、content_hash、总行数及可读取字段，而不是静默截短的股票列表。本轮 `read_tool_result` 只读取已保存回执，支持 offset/limit、字段投影和 metadata；每页最多 100 行并受 5,000 估算 tokens 约束，不拆开单行。next_offset/has_more、来源、字段定义、null/缺失语义和原始顺序均保留。业务工具只执行一次，页面超限或总上下文仍不足会显式返回错误/partial。分页元数据与提示规则要求模型说明未读部分，尚无对自然语言“完整名单”声明的全面语义证明。

外部只读入口为 `GET /api/agents/chat/runs/{run_id}/results/{result_id}`，支持 section、offset、limit、fields 和 expected_hash，服务端同时校验 owner、run 和完成回执，响应禁止缓存。GET 不创建回执、不重跑业务工具，也不提交答案。

## 第二轮：显式长期记忆

长期记忆独立存入 PostgreSQL repository，不把 LangGraph checkpoint 或聊天摘要当成跨会话偏好的权威来源。普通问答不会自动抽取、推断或保存偏好。现有输入安全审核的一次结构化模型调用增加可选 `MemoryIntent`；明确的保存、修改、删除或查询进入独立 `memory` 节点，业务工具没有记忆写入权限。当前请求与有效会话约束优先于持久偏好，记忆不授予工具权限，也不是当前市场证据。

| 类型 | 当前支持 | 约束 |
| --- | --- | --- |
| `preference` | `response_style`：concise/balanced/detailed；`output_format`：paragraphs/bullets/table | 只存枚举值，不接受附加指令或自由事实 |
| `research_context` | `research_topic`：首板、连板、涨停、炸板、行业、市场情绪 | 不存交易授权、预测、未经核实的新闻 |
| `research_archive` | 用户主动保存自己已正式交付且有来源的研究报告 | 服务端从原 run 构造正文、as_of、报告版本与证据快照，拒绝客户端伪造正文来源 |

可以分别提出：“请长期记住以后回答简洁”“把长期回答偏好改为详细”“查看我已保存的长期记忆”“删除已保存的回答详略偏好”。“这次用表格，只对本次生效”不写长期记忆。每次只执行一项管理请求；不支持的内容或混合业务请求会澄清。语义判断仍由模型完成，精确原文引用和类型校验只能约束输出，不能证明任意自然语言意图都识别正确。

档案示例：“把上一份有工具证据的研究报告保存为历史研究档案，名称为首板复盘”“查看历史研究档案首板复盘”。未给 source run 时，只在当前会话寻找最近有证据的已交付研究报告；有明确 source run 时仍校验 owner。档案不会默认进入普通研究调用，主动查看时明确标记历史日期，不能替代本轮行情查询。证据快照与档案一起保留，删除源会话不删除档案；档案本身可以单独删除。

每次业务模型调用从 runtime 依赖重新读取有效记忆，按 owner、类型、精确 key、主题、相关性、去重和估算 Token 预算处理，首版不使用 embedding。长期记忆默认预算 1,200 tokens，并受总输入预算约束；超限先移除低优先级整条记忆。检索内容只进入当次模型视图，checkpoint 保存 owner revision、有效集合 fingerprint 及是否实际使用的标记，不保存召回列表。普通模型回答本身可能提及偏好，因此还需下面的撤回规则。

### 撤回、恢复与交付一致性

读取用同一只读事务返回记录、owner revision、全 owner 有效集合 fingerprint 和数据库时钟。fingerprint 与本次 key/topic 过滤无关；到期即使没有新写入也改变有效集合。更新采用记录版本和可选 owner 版本比较，管理节点再使用运行租约 fencing。相同 operation ID 重试复用操作回执，并读取当前记录，不能用旧保存回执复活后来的删除；重新保存删除项必须显式允许恢复。

记忆管理的用户消息和助手响应带可信来源标记，整轮不进入普通模型历史或摘要。取消、失败和拒绝轮次同样按正式结局整体排除，覆盖记忆写入后、checkpoint 保存前被取消而缺少管理标记的情况；先判断整轮结局，再过滤消息状态，并保留原始历史游标。检测到集合变化后，清除旧自由摘要和无法证明作用域的约束，从仍有效的用户会话消息重建；带旧 fingerprint 的助手文本也会被排除。摘要模型调用之前先清理，本轮工作消息保留成对工具调用/结果但移除过时的模型文字。市场工具证据与已验证股票引用可以继续使用原有来源边界。

恢复最终 checkpoint、生成管理确认，以及正式交付事务都检查生成内容所属的记忆身份。若中途删除、更新或到期，旧候选答案变为 `partial / memory_changed`，要求重新查询，不发布已撤回内容。管理查询和写入确认绑定生成它们的同一事务快照，不能用后续新 fingerprint 给旧正文重新贴标。

这一策略会保守丢弃仅存在于旧自由摘要、尚无逐项来源的会话约束；第三轮的查询条件来源记录避免已验证范围随摘要一起丢失。删除长期记忆会擦除该记录的值和内容元数据并留下不含正文的墓碑/操作回执；它不追溯删除原始聊天、已正式交付答案、历史 checkpoint 或审计记录。旧记录继续可审计，但不作为新的偏好来源。当前 owner 来自签名 visitor cookie，同一 owner 可跨会话，尚无跨设备账号同步。

### 管理 API

均位于 `/api/agents/chat/memories`，由服务端确定当前 owner；查询响应使用 `Cache-Control: private, no-store`。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /` | 列出有效记忆，可按 `memory_type`、`key` 和 `limit` 过滤 |
| `GET /by-key/{memory_type}/{key}`、`GET /{memory_id}` | 查看自己的单条记录；可显式请求 inactive 状态 |
| `PUT /{memory_type}/{key}` | 新增/修改，必须带 `Idempotency-Key`，正文含 `source_user_message`、`value` 与 `expected_revision` |
| `DELETE /{memory_id}?expected_revision=N` | 删除，必须带 `Idempotency-Key`；版本冲突返回 409，其他 owner 记录返回 404 |

`PUT` 可带 `expires_at`，更新必须使用刚读取的版本；archive 必须带 `source_run_id`。当前 UI 通过对话操作，尚无独立记忆设置面板。API 与 Agent 共用类型策略，不允许通过直接 HTTP 写入绕过枚举或证据校验。

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

第二轮迁移 `0006_long_term_memory` 在此基础上新增 `long_term_memories`、`memory_owner_versions`、`memory_operation_receipts`，不自动把旧聊天摘要变为长期偏好。部署沿用上述备份、升级和验证顺序，增加显式保存→重启→新会话召回→删除→再次查询，以及过期/回执重试的检查。2026-10-25 本机开发库已在备份验证后升级至 0006，升级前后均为 194 个会话、554 条消息、200 条 run；迁移报告为 `output/memory-refactoring/round2-migration.json`。备份不提交 Git，长期记忆迁移禁止破坏性 downgrade。

## 已完成验证

首轮已实际执行以下验证；这是首轮基线，不是第二轮最终全量结果：

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

第二轮已实际通过独立 PostgreSQL 的 owner 隔离、CAS/回执、删除墓碑、数据库到期时钟、档案来源与源会话删除、Context 失效、Graph 重启和 HTTP 管理测试；包含 44 条固定 Tool World 长期记忆 Golden。`test_memory_command_races.py` 另外验证列表读取后、写入回执返回前的并发更新/删除，以及摘要模型调用前旧摘要已清除；这些场景与 scope/durable LTM 联合运行 18 passed。

固定 Tool World 消融采用相同九个输入场景与预算，分别关闭全部记忆、仅保留短期记忆、启用短期加长期记忆。对应上下文契约通过数为 4/9、4/9、9/9，报告记录召回数和估算输入 tokens；这衡量实体/约束/记忆是否以正确作用域抵达上下文，不是模型答题准确率或收益指标，也不能据此宣称长期记忆普遍提高任务成功率。复现时可设置 `LIMITUPLAB_MEMORY_ABLATION_REPORT` 到项目忽略目录后运行 `tests/test_long_term_memory_ablation.py`；本次报告为 `output/long-term-memory-ablation.json`。

第二轮真实 `deepseek-v4-flash` 经 FastAPI HTTP 路由完成两轮各 13 次问答，最后一轮在功能提交 `5600746` 对应代码上执行，13 次均为 HTTP 200、`task_status=complete`。覆盖保存偏好、应用重启后新会话查看、当前详细要求覆盖简洁偏好、修改、删除、否定/临时请求不写入、首板三问和跨会话主动查看历史档案；“第二只”再次返回 000559 万向钱潮。核对 owner 隔离与档案 as_of/source run，最终报告为 `output/memory-refactoring/round2-live.json`。这是实际模型冒烟验收，不替代稳定性、完整语义正确性或性能评测。

第二轮最终验收（2026-10-25）：

| 验证集 | 最终结果 | 报告与边界 |
| --- | --- | --- |
| 后端全量，功能版本 `5600746` | 1711 passed、22 subtests passed，0 failed、0 skipped | 独立 PostgreSQL；线程异常视为失败；165.99 秒；`output/validation/20261025T070946Z-867a6cc0/` |
| 前端测试与生产构建 | 224 passed；tsc + Vite build 成功 | `output/validation/20261025T070315Z-8f8f02b8/` |
| 最终真实模型问答 | 13 次均完整交付 | 新建独立验收会话，保留当前请求覆盖、跨会话及市场名单核对记录 |
| 正式交付与档案审计 | 13 个 run 各有且仅有 1 条正式 assistant 消息；删除值清空；档案正文匹配已提交源报告；证据 SHA-256 一致 | `output/memory-refactoring/round2-audit.json`；所有测试会话均有原生 checkpoint |

全部模块测试包含在后端全量数字中，不重复相加。最后的取消修复增加 8 项测试，包括实际 PostgreSQL Graph 的写入前/写入后故障再取消边界。后端只保留既有 LangChain 弃用提示，前端保留既有大 bundle 非阻断提示。原始问答、测试日志、备份与消融输出均位于 Git 忽略目录，未提交凭据、数据库或生成报告。

## 原始方案对照

状态含义：**Implemented** 表示当前代码有实现和相应测试；**Explicit optional** 表示原方案明确允许按需采用或“如适用”，本轮未启用；**Deferred** 表示尚未完成，不能用相近的测试指标替代。最终全量和真实模型验收以各轮记录为准。

| 原方案条目 | 状态 | 当前实现与边界 |
| --- | --- | --- |
| ReAct 动态工具循环、应用生命周期 Graph 与依赖注入 | Implemented | 单一 durable Graph；客户端/连接/registry 不进入 State，无额外 Planner 或多 Agent 框架 |
| Conversation / Run / Control 状态隔离 | Implemented | conversation、每轮工作消息/计数/预算、run/schema/status 分开；新 run 重置临时字段 |
| AsyncPostgresSaver 唯一执行状态、同步 checkpoint | Implemented | 原生 Saver 加事务内 fencing；不宣称 exactly-once |
| Node/模型/工具中断、崩溃、重启、取消、旧 worker 拒写 | Implemented | 固定模型、真实 PostgreSQL 和子进程终止场景；不确定工具结果不自动重放 |
| 等待人工审批后恢复 | Explicit optional | 原文为“如适用”；当前业务没有审批节点 |
| Journal 职责迁移、请求幂等、同 Thread 并发 | Implemented | Registry/Coordinator/EventStore/Delivery 分工；自定义 checkpoint 与 legacy 执行回退退出 |
| 正式交付事务、幂等补交、先提交再 completed | Implemented | Graph 和业务提交分开；交付失败可恢复且只产生一条正式回答 |
| 原生 Streaming 与 SSE 游标重连 | Implemented | custom/updates 白名单映射，status/tool_start/tool_end/warning/error/completed；GET 不执行任务 |
| 统一 Context Builder、预算、优先级、完整工具消息组 | Implemented | 每次业务模型调用重建视图；估算 tokens，不把计费精度或任意语义冲突检查冒充已实现 |
| 股票序号/批次引用与真实查询条件继承 | Implemented | 展示顺序经真实行验证；query_scope 支撑条件和排序；关键歧义或旧 scope 缺失时澄清 |
| Recent Window + Rolling Summary + Structured Context | Implemented | Token 触发、消息游标/版本；纯摘要计算；查询条件独立于自然语言摘要 |
| 当前行情证据隔离、Schema/输入/合规/输出校验 | Implemented | 当前数据来自本轮工具；记忆与档案不自动成为当前事实；没有全面语义事实证明 |
| 无损工具压缩与按需展开 | Implemented | 小结果列式编码，大结果 manifest + 不可变回执分页/字段投影；超预算显式说明 |
| 独立长期 Store、显式保存/更新/查询/删除/过期 | Implemented | PostgreSQL 自有 repository，owner/CAS/回执/墓碑/来源与撤回处理 |
| User Preference、Research Context、Research Archive 首版 | Implemented | 支持有限回答格式/详略、六种主题、有已交付来源的历史档案，类型共用策略校验 |
| 研究主题之外的长期关注板块、任意跨会话筛选条件示例 | Deferred | 未扩大本轮有限枚举；本轮有来源的会话查询条件不等于跨会话保存条件 |
| 自动抽取所有长期记忆 | Explicit optional | 原文明示首版不要求；只有明确用户管理请求写入 |
| pgvector/复杂语义检索、外部向量库或知识图谱 | Explicit optional | 原文要求出现实际复杂需求后再评估；当前精确 key/类型/主题/排序/预算检索 |
| 记忆过滤、去重、冲突、删除后旧 checkpoint 失效 | Implemented | 每次读取有效集合身份；最终状态与交付事务复核；旧管理轮不进入摘要 |
| Context/Memory/LLM/Tool/Checkpoint/Final Delivery Tracing | Implemented | 复用现有 tracer，增加阶段和关联版本/计数/时间；trace 故障有可观测记录，不作为恢复数据库 |
| Persistence、Delivery、Tool-use 回归与 Memory Golden | Implemented | 真实 PostgreSQL 集成；40 条短期与 44 条长期固定世界 Golden，以及新增来源/分页/流事件回归 |
| No / Short / Short+Long Memory 消融 | Implemented | 相同固定世界和预算的上下文契约评测；不得把契约通过率称为真实 LLM 答题提升 |
| 可复现运行评测入口与当前版本指标 | Implemented | `scripts/evaluate_agent_runtime.py` 使用独立 PostgreSQL 随机 schema 和固定模型；原始报告单独保存 |
| 重构前后真实模型任务成功率、成本与生产 P50/P95 比较 | Deferred | 真实问答仅冒烟；没有匹配旧版本/真实流量的受控性能基线，不据固定脚本宣称质量或性能提升 |
| Alembic、备份、旧会话/摘要/未完成 run 迁移策略 | Implemented | 0005/0006 与已记录开发库备份；旧摘要只读一次导入，旧执行状态不强转原生 checkpoint |
| 删除旧实现、配置、无效测试并更新前后端协议文档 | Implemented | 旧 summary writer/direct Graph/字段去重缓存退出；保留历史 schema/data，非静默丢弃 |

当前开发者还可以独立评估跨设备账号、设置面板、生产多实例压测和保留策略；这些不是已经交付的能力。跨设备账号与设置面板也不是原始方案明确要求的交付项。

## 明确未完成的范围

- 长期记忆仅覆盖上述有限类型；自动偏好抽取、向量/语义检索、自由事实记忆、跨设备账号与独立设置面板尚未实现。
- 没有对模型自然语言回答做全面的语义事实证明；保留工具 Schema、数据缺口、输入安全、合规复核与输出长度检查，开放式解释仍需审阅证据。
- 尚无生产全量 P50/P95、数据库写放大、任务成功率和真实 token 成本对比，不据代码结构宣称性能提升。
- 大结果已支持分页/展开；超过单行或总上下文预算仍会显式失败/partial，不承诺任意规模名单都能在一次问答中完整展开。
- 多实例压测、跨版本 Graph State 自动迁移、长期 checkpoint 保留策略和人机审批恢复需要后续单独验收。

设计依据：[LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)、[Checkpointers 与恢复边界](https://docs.langchain.com/oss/python/langgraph/checkpointers)、[Memory 与消息裁剪](https://docs.langchain.com/oss/python/langgraph/add-memory)、[PostgreSQL 事务隔离](https://www.postgresql.org/docs/current/transaction-iso.html)。采用 thread 内状态与跨会话用户存储分离、同快照读取和交付时复核；项目的类型策略、撤回和操作回执由自己的 PostgreSQL repository 实现，不宣称由通用 Store 自动提供。实际 API 与恢复边界以锁定版本源码及集成测试为准。
