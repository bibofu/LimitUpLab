# Agent 应用质量审查

## 2026-09-13：生产 ReAct Provider 配置边界修复

- 范围：修复 P1/A36 中 Requests 文本 Provider 可被全局配置为生产 ReAct 后端的问题；不删除旧文本实现，不修改模型请求协议、市场工具、评分或前端。
- P1 已修（BC-040）：`configure_runtime_environment` 和共享 Provider 工厂现在只接受 `LIMITUPLAB_LLM_BACKEND=langchain`，因此错误配置在启动阶段失败，不再等到聊天循环内连续产生两次 provider error。旧 `OpenAIChatCompletionsProvider` 只能由明确的文本 消费者直接构造和注入。
- 纵深门禁：ReAct 入口按 `generate_messages` 是否被实现验证实际能力，不依赖 Provider 类名；显式注入 Requests Provider 也会在模型循环及网络请求前抛出 `NativeFunctionCallingUnavailable`。运行版本升级为 `react-runtime-v6`。
- 回归：配置、Requests 文本实现、LangChain 线协议、ReAct 与 lifecycle 定向首次 **66 passed**；加入 lifecycle 后的沙箱复跑为 66 passed / 6 setup errors，错误来自 pytest 临时目录 `WinError 5`，未计为通过。完整后端第一次宿主运行 **597 passed / 1 failed + 6 subtests passed**，失败暴露“已取消任务先做 Provider 检查”的门禁位置问题；将能力检查移到首次模型调用前后，最终宿主完整回归 **598 passed + 6 subtests passed**，0 failed、0 setup error、0 skipped，保留 3 条既有依赖弃用警告。
- 运行态：隔离设置 `LIMITUPLAB_LLM_BACKEND=requests` 后导入 `app.main`，进程在 `configure_runtime_environment` 明确抛出只支持 LangChain 的配置错误，退出码 1，未启动服务或发出模型请求。恢复正常配置并重启本地 8001 服务为 PID 25968；Cookie 隔离真实请求查询 2026-09-11 收盘涨停数量，返回 40 只、来源说明、`task_status=complete`、`generated_by=react-runtime-v6`，run 为 `run_b77d3877045e4f4094f24d263af1146c`；测试会话已通过 owner-scoped API 删除。

## 2026-09-13：工具契约单一真相源修复

- 范围：修复 P2/A40 的参数 Schema、执行签名、时态/集合 Catalog 三轨维护；覆盖全部 26 个业务工具，不修改市场口径、评分、数据管线或前端。
- P2 已修（BC-039）：`AgentToolSchema` 成为唯一公开契约，同时保存参数 Schema、时态、日期字段、集合字段、说明和显式 adapter。`catalog.py` 从该契约生成 strict Pydantic 输入模型与 LangChain `StructuredTool`；直接方法的默认值自动派生进类型化模型，模型 definitions、Policy 校验和实际调用共用同一对象，不再按工具名修补 required/schema。Python 方法签名只作为内部实现，启动时强制核对参数集合、必填项和基础类型；post-limit 与首板筛选的非直接调用通过契约 `adapter` 显式声明。
- 已知漂移已清除：`limit_up_events.result_mode` 不再对模型暴露后静默丢弃；`stock_kline.symbol` 在源契约中就是单字符串；`first_board_filter.trade_date` 和 `post_limit_path.symbol` 直接写入源契约；四个日期区间工具直接声明起止日期必填。旧的手写 `tool_schema.py` 校验器已删除。工具契约版本为 `agent-tools-v2`，运行版本为 `react-runtime-v5`。
- 回归：类型化契约、签名漂移、已知错配、直接工具、首板虚拟适配和 post-limit contract 适配均有固定测试；最终定向 **82 passed**，完整后端 **595 passed + 6 subtests passed**，0 failed、0 setup error、0 skipped，保留 3 条既有依赖弃用警告。
- 真实 HTTP：最终代码在本地 8001 服务 PID 26884 运行；Cookie 隔离会话查询 2026-09-11 收盘涨停数量，实际通过类型化契约调用 `market_event_pool(result_mode=count)` 与 `limit_up_events(closed_only=true,event_status=closed)`，两项均为 `result_state=ok`，后者输入不存在已移除的 `result_mode`。最终返回 40 只、`task_status=complete`、`generated_by=react-runtime-v5`，执行 trace 为 `tool_contract_version=agent-tools-v2`。最终验收 run 为 `run_35048b400cb34cfc9e4589365b19df6b`；测试会话已通过 owner-scoped API 精确删除。此前同代码第一次请求为 `partial` 且未交付数字，第二次相同请求完成；该模型终态波动不影响契约调用证据，但未被隐去或计作一次性稳定性通过。
- 失败记录：一次兼容测试因引用不存在的 `test_agent_tools.py` 而 0 项执行；另一次为 53 passed / 1 setup error，唯一错误是 Windows pytest 系统临时目录 `WinError 5`。两次均未计为通过，最终在宿主隔离 `--basetemp` 下完成全量验证。
- 边界：工具实现仍是普通 Python 方法，自定义 EvidenceStore、调用日志和最终门禁没有迁移到标准 `ToolNode`；本次只解决公开工具契约漂移，不顺带处理 A39 的 LangGraph persistence 设计债。

## 2026-09-13：多轮上下文二次截断修复

- 范围：修复会话记忆层最多交付 16 条、ReAct 层又截为 8 条的 P1；不修改记忆摘要刷新节奏、证据新鲜度边界、评分、数据管线或前端。
- P1 已修（BC-038）：`prepare_history` 不再维护第二套消息数量窗口，完整接收会话记忆层选出的有界上下文；仍保留同会话/非错误消息过滤和 12000 字符预算。`react_execution` trace 记录实际 `context_message_count`，运行版本升级为 `react-runtime-v4`。
- 回归：契约层固定完整 16 条及原顺序，运行时固定首轮 prompt 包含 system、16 条历史和当前问题；定向 **40 passed**。宿主隔离目录完整后端 **591 passed + 6 subtests passed**，0 failed、0 setup error、0 skipped，保留 LangGraph serializer 与 websockets 的 3 条既有弃用警告。沙箱内两次完整测试分别为 537 passed / 54 setup errors / 6 subtests passed，错误均为 pytest 临时目录 `WinError 5`，未计为通过；改在宿主隔离目录后重跑成功。
- 真实 HTTP：本地 8001 服务重启为 PID 22544。Cookie 隔离会话连续执行 7 轮明确 `clarify` 的非市场追问，`react_execution.context_message_count` 依次为 0、2、4、6、8、10、12，第 6、7 轮越过旧 8 条上限且仍复述第一轮标识词 `CTX-KEEP-16`；全部响应为 `react-runtime-v4`。两次验收创建的测试会话均已通过 owner-scoped API 精确删除。第一次措辞验收有 5 轮进入 `validation_failed`，错误态消息按契约被过滤，最大有效上下文仅 6，故没有将该次结果误作修复证据。
- 边界：字符预算仍可能按“最近消息优先、整条保留”舍弃超长旧消息，这是显式的 prompt 大小保护，不属于 16→8 的重复消息数裁剪。

## 2026-09-13：历史证据与本轮证据强制隔离

- 范围：修复历史 evidence 与本轮 evidence 可混合通过最终门禁、历史 evidence 可把本轮 requirement 标成 `satisfied` 的 P1；不修改评分、数据管线、预测快照或前端。实现提交：`9fdadd5`。
- P1 已修（BC-037）：证据 schema 升级为 `react-evidence-v3`，新增服务端维护的 `evidence_scope=current_run|conversation_history`。同会话历史恢复一律进入 context-only 域；任一历史父项会使派生结果继续属于历史域。EvidenceStore 的统一 `require_current` 边界同时保护 satisfied requirement 与全部最终状态的 evidence 引用，grounding 只接收本轮证据；需要复述旧事实时必须按原条件在本轮重新查询。运行版本升级为 `react-runtime-v3`。
- 回归：证据域、上下文、运行时与 HTTP 定向 **88 passed**；完整后端 **589 passed**，0 failed、0 setup error、0 skipped，保留 LangGraph serializer 与 websockets 的 3 条既有弃用警告。定向测试第一次为 83 passed / 1 failed，失败原因是成功修复分支的夹具答案声明了夹具未提供的名称与“10日”字段，grounding 正确拒绝；收窄为夹具实际提供的代码、日期和涨幅后重跑通过，未把该次失败计为成功。
- 真实 HTTP：Cookie 隔离会话 `chat_eefef3b208cd4538bdb29dd217964353`。第一轮查询 2026-09-11 涨停数量并完成；第二轮明确要求不重查、直接复用上一轮 evidence。第一次 answer check 以 `Final answers cannot use conversation-history evidence; refresh it in this run` 拒绝混合引用；模型随后重新调用 `market_event_pool`，最终只用 1 条 `current_run` 证据支持日期与 40 只计数，`task_status=complete`、`generated_by=react-runtime-v3`。第二轮 run：`run_990e8dde2f224f79b62b7d362f2fd439`。
- 边界：历史消息及 preview 仍供指代理解，EvidenceStore 仍在同一请求对象中保存两个 scope，但所有能发布或完成任务的出口共享同一强制边界。开放式定性/因果声明的语义接地仍属于 BC-033/034，不因本修复关闭。
- 检查：实现提交 204 行新增、27 行删除，低于单次 1000 行限制；提交前检查完整 diff、精确暂存和 `git diff --check`，未纳入用户的 `AGENTS.md` 修改、本地数据库、输出报告或 pytest 临时目录。

## 2026-09-13：ReAct 最终答案门禁绕过修复

- 范围：修复普通模型文本自动包装为 `complete`、无证据 requirement 自报 `satisfied`、无证据研究回答、仅历史证据完成新研究请求，以及显式实体/日期/指标/数值与引用证据错配。生产运行版本由 `react-runtime-v1` 升级为 `react-runtime-v2`；没有修改评分、数据管线或历史预测。
- P1 已修（BC-036）：普通文本不再成为隐式终态，只允许一次类型化 `finish` 修复；删除自然语言“可无证据完成”正则白名单，`complete` 和 `empty` 与用户措辞无关，一律要求 evidence ID，无证据场景只能返回 `partial`、`clarify` 或 `refuse`；已满足 requirement 必须绑定可用 evidence ID，且最终 `finish` 继续保留这些 ID；仅历史 reference 不能完成请求；生产 gate 对最终声明引用的证据执行已有关系接地校验，不允许错误股票、日期或数值通过。实现提交：`f3b72e0`、`b7812dc`。
- 真实 HTTP：新增 loopback FastAPI 回归，模型首轮直接输出“贵州茅台今天涨停，成交额100亿元”，API 不发布该文本，第二轮只交付 `partial` 与行情缺失说明；覆盖同步持久化后的最终响应，不以健康检查代替业务验收。
- 验证：最终门禁与 ReAct HTTP 定向 **28 passed**；最终完整后端 **582 passed**，0 failed、0 setup error、0 skipped。保留 LangGraph serializer 和 websockets 的 3 条既有弃用警告。前端未修改，未运行前端测试或构建。
- 剩余 P1：确定性 grounding 主要覆盖股票实体、日期、时间和带单位数值，尚不能证明“连续性弱”“因为复权”等开放式因果/定性解释，因此 BC-033/034 保持未修。会话上下文 16→8 空档、完整 memory payload 暴露和工具线程不可强制取消也不在本次范围。
- 检查：两次源码提交均检查完整 diff、精确暂存和 `git diff --check`，分别为 374 行新增/19 行删除、34 行新增/33 行删除，均低于单次 1000 行限制；未纳入本地数据库、输出报告、测试临时目录或其他未跟踪文件。

## 2026-09-13：V1.4.0 标签部署失败与 SQLite 只读挂载修复

- GitHub Actions run `34704996201` 的 Windows/Ubuntu 验收通过，生产任务在镜像构建完成后失败。服务器 journal `v1.4.0-20260913T002448.json` 为 `failed_before_switch`，未进入维护、停服务、备份或迁移阶段；公网 `/health` 仍为 200。
- 根因：发布器用 `limituplab-data:/app/data:ro` 启动 schema 容器，SQLite 即使以 URI `mode=ro` 连接，仍需在卷内创建或访问 WAL/SHM 协调文件，因此报 `sqlite3.OperationalError: unable to open database file`。同样的错误也潜伏在后续备份容器。
- 生产隔离复现：数据目录与数据库为 `10001:10001`、容器用户为 UID 10001，直接读取文件成功；原始只读挂载连接失败，`immutable=1` 成功读出 schema version 12。后者可能忽略未 checkpoint WAL，故不作为修复。
- 修复：schema 与 backup 的 Docker 数据卷改为可写挂载，SQLite 源连接继续使用 `mode=ro`；增加命令级回归，防止重新引入 `:ro`。部署入口是 root 安装副本，发布前需审查并更新 `/usr/local/lib/limituplab/release.py`。
- 发布策略：不移动失败的 `v1.4.0`；修复验证后创建 `v1.4.1`，先安装受信发布器，再推送标签并观察自动部署和生产健康状态。
- 验证：部署专项首次在沙箱中 12 项通过、13 项因 Windows 临时目录 ACL setup error，收尾为 `WinError 5`，不计为通过；宿主隔离目录重跑 26/26 通过。统一验收最终为后端 **617 passed + 6 subtests passed**、0 失败/跳过、3 条依赖弃用警告，前端 **15/15**，TypeScript/Vite 构建通过并保留 664.04 kB 单 chunk 警告。

## 2026-09-13：V1.4 文档与标签前阶段审查

- 范围：以当前生产源码和 `v1.3.2..HEAD` 为准，更新根目录、后端、LangChain/LangGraph、代码阅读、面试及里程碑文档；不修改业务代码、评分、数据库或历史预测，不纳入 `artifacts/`、`backend/data/`、`tmp/` 和 `output/`。
- 架构结论：生产聊天是 LangChain 原生 tool calling 驱动的 LangGraph 有界 ReAct 图；旧 Planner/Capability/Tool Policy 补计划、答案模板、Task DAG 和 Complex Graph 仅在历史文档中保留演进记录。Requests Provider 仍不能运行生产 ReAct，文档已取消“聊天回退”承诺，但代码配置债 A36 保持未修。
- 文档修正：删除不存在的 `docs/Tasks.md`、`docs/需求.md` 引用；V1.3 面试问答明确标为历史架构，避免与当前调用图混淆。
- 发布校验：第一次沙箱运行中，pytest 收尾因临时目录 ACL 报 `WinError 5`，Vite/esbuild 因 `spawn EPERM` 失败；同一入口在宿主权限下重跑全部通过。最终为后端 **616 passed + 6 subtests passed**、0 失败/跳过、3 条依赖弃用警告；前端 **15/15**；TypeScript/Vite 构建通过，保留 664.04 kB 单 chunk 警告。
- 风险状态：未新增 P0。A37 类型化 `finish` 绕过、A38 评分规则未完整版本化和 BC-033/034 仍为 P1；A39-A42 仍未完成。因此 `v1.4.0` 只标记架构与文档基线，不表示模型质量或评分有效性已全部验收。
- 检查：本地 Markdown 相对链接扫描通过；提交前继续执行完整 diff、`git diff --check` 与精确暂存检查。标签只在本地创建，不推送、不触发部署。

## 2026-09-12：当前 ReAct 范式、冗余与硬编码专项审查

### 范围与结论

- 基线：`4095758`。本次只审查当前生产 ReAct、模型适配、工具契约、评分规则和共享时间口径，不修改业务实现，不触碰未跟踪的 `artifacts/`、`backend/data/`、`tmp/`。
- 当前实现确实是原生工具调用驱动的 `StateGraph` 循环，不应简单判定为“没有 LangGraph”或“必须改用已弃用的 `create_react_agent`”。LangGraph v1 官方推荐简单 Agent 使用 `langchain.agents.create_agent`；需要精细工具控制的自定义图使用 `MessagesState`、`ToolNode` 和 checkpointer。当前项目选择自定义图有金融 Policy、证据存储和幂等需求，方向合理，但复制了较多框架职责且出现下述契约漂移。
- P0：未发现。P1：3 项未修。P2：4 项未修。此前 BC-033/034/035、正式真实模型质量/成本门槛等问题仍沿用上一节状态，本节不重复改写。

### P1 / A36：文档与配置承诺的 Requests 回退后端已不能运行 ReAct

- 证据：`backend/app/services/llm_provider.py:489-520` 仍接受 `LIMITUPLAB_LLM_BACKEND=requests` 并返回 `OpenAIChatCompletionsProvider`；`backend/README.md:61-63`、`backend/.env.example:17-18` 和 `docs/LangChain_Integration.md:50` 仍称其为可重启切换的显式回退。生产 ReAct 唯一调用 `generate_messages`（`react_runtime/runtime.py:121`），但 Requests Provider 没有覆盖该方法，只继承 `LLMProvider.generate_messages` 的 `NativeFunctionCallingUnavailable`。
- 隔离复现：构造不联网的 Requests Provider 进入当前 `run()`，连续两轮 provider error 后得到 `task_status=error`、`stop_reason=provider_error`；没有发出网络请求。
- 影响：配置表面合法、文档明确支持，实际会令所有聊天失败；同时保留两套模型协议增加测试和维护面。
- 建议：二选一。若坚持纯 LangChain/LangGraph，删除 Requests 配置分支、回退文档及仅剩消费者；若确需灾备，给它实现并测试完整的消息/多工具调用协议，而不是旧的单次 function-call 接口。
- 状态：已于 2026-09-13 按 BC-040 修复；生产配置仅允许 LangChain，Requests 仅保留为显式文本依赖，ReAct 入口另有能力门禁。

### P1 / A37：普通模型文本绕过类型化 `finish` 与证据完成门禁

- 证据：系统提示在 `react_runtime/runtime.py:39` 要求最终必须单独调用 `finish`，但 `:130-134` 在模型无 tool call 时自行构造 `status=complete`、引用全部证据并把 `missing` 置空；随后 `gate()` 只校验这些自动填入的 ID 是否存在，不能证明答案声明与证据、缺口或任务清单一致。
- 隔离复现：模型对“查询市场事实”首轮直接返回无工具普通文本，当前运行结果为 `task_status=complete`、`stop_reason=answered`，且没有任何业务工具证据。
- 影响：模型偶发不调用工具时，事实优先、真实状态和缺失披露均可被绕过；也解释了 BC-033/034 一类关系/口径错误为何能通过现有 answer gate。
- 建议：无 tool call 只在明确非事实型回答或拒答场景接受；其余情况生成可观察的校验反馈并强制类型化终态。更标准的实现可使用 `create_agent(response_format=...)`，自定义图则应把结构化终态作为唯一出口，并增加 plain-response、错 evidence ID、错实体/指标、漏 requirement 回归。
- 状态：未修复；应优先于继续扩工具或提示词调优。

### P1 / A38：评分策略只版本化权重，分箱、过滤和置信度规则仍散落在代码中

- 证据：`services/scoring_policy.py:53-77` 的 Policy 只保存 14 个 factor weight；实际分数分箱、中性分和上限写在 `agents/first_board.py:274-696`，过滤条件写在 `:142-186`，置信度扣分写在 `:699-727`。同一 `0.55/0.35` 炸板率、`1/18` 换手率、`9:45` 首封、Top5/20 人气等阈值又在 reasons/risks（`:730-803`）和 `services/first_board_critic.py:64-149` 重复。
- 影响：数据库中的 policy/version 不能独立重放完整评分逻辑；修改任一分箱但漏改解释或 Critic 会产生“分数、理由、质疑口径不一致”。硬过滤 `MIN_AMOUNT=5000万` 与 enrichment 的 `MIN_AMOUNT=1亿` 也属于不同阶段的同名常量，名称不足以表达业务差异。
- 建议：建立版本化 `ScoringRuleSet`，包含 eligibility、bins、neutral score、confidence penalties、rating bands 和展示标签；评分、reason/risk、Critic 从同一规则对象产出。历史快照保留完整规则摘要或不可变 rule-set hash，不只保存权重。
- 状态：未修复；属于评分核心高风险项，实施前需先冻结现有输出并做回放对照。

### P2 / A39：LangGraph 目前更像调度外壳，核心状态和持久化在图外重复实现

- 证据：`react_runtime/runtime.py:49-55` 的 Graph State 只保存消息和少量路由字段；预算、证据、缓存、requirements、trace、deadline 和最终状态都在可变 `Run` 对象（`:62-99`）。图以 `configurable.run` 注入进程对象（`:307-312`），`compile()` 没有 checkpointer（`:324-337`），另由 `lifecycle.py` 自建 SQLite checkpoint/call journal。
- 官方范式差异：官方 persistence 以 checkpointer + `thread_id` 在 super-step 保存 Graph State；自定义工具工作流通常用 `MessagesState`/message reducer 与 `ToolNode`。当前手工实现不是功能错误，且已有调用幂等保护，但框架无法直接检查/恢复完整状态，节点保存顺序、序列化、并发、错误配对都由项目自行维护。
- 建议：不要为了“像框架”机械重写。先修 A36/A37；之后做小型 spike，对比“现有 Policy Gateway + 标准 BaseTool/ToolNode + SQLite checkpointer”是否能保留证据裁剪、单独 finish、调用幂等和取消语义，再决定迁移。
- 状态：设计债务，未修复。

### P2 / A40：工具定义存在三份真相源，未使用 LangChain 的类型化工具契约

- 证据：26 个工具的 JSON Schema 手写在 `agents/tools.py:136-811`，执行签名在同文件 `AgentToolRegistry` 方法中，时态/集合契约又手写在 `react_runtime/catalog.py:18-45`；`catalog.schemas()` 再用 `inspect.signature` 和名称特判修补 required/schema（`:48-75`）。扫描已发现 `limit_up_events.result_mode` 只存在于 Schema、执行前再被丢弃，三个 post-limit 工具还依赖专用 adapter 将扁平 Schema 转成 `PostLimitQueryContract`。
- 现有保护：`test_every_existing_tool_has_reviewed_contract` 能保证名称集合相同，但不能保证参数类型、默认值、描述、adapter 和执行签名持续一致。
- 建议：以 Pydantic args model + LangChain `BaseTool`/`StructuredTool` 为单一契约源；额外的金融时态和集合能力作为 tool metadata，由 Policy middleware/gateway 消费。保留自定义 ToolNode 也不需要保留三套 Schema。
- 状态：已于 BC-039 修复。参数、时态、集合与 adapter 收敛到 `AgentToolSchema`，并派生 strict Pydantic model / LangChain `StructuredTool`；实现签名漂移在启动期失败，不再运行时按名称修补。

### P2 / A42：共享时间与风险口径仍有跨模块魔法值

- 证据：收盘后数据就绪时间 `15:30` 分散于 `services/system_health.py`、`consolidation.py`、`post_limit.py`、`prediction_time.py`；上海时区也以 `SHANGHAI`、`SHANGHAI_TZ`、`CN_TZ`、`_SHANGHAI` 和固定 `UTC+8` 多次定义。`LARGE_LOSS_THRESHOLD_PCT=-3.0` 在 prediction audit 与 optimizer 重复定义。
- 影响：修改数据就绪窗口、时区实现或大亏口径时容易出现页面、健康检查、研究统计和晋级策略不一致，违反新增业务窗口优先抽成共享常量/Query Contract 的项目约定。
- 建议：增加共享 `MarketClockContract` 和审计阈值契约，名称区分交易收盘 `15:00` 与数据可用 `15:30`；消费者只引用契约，不各自复制。
- 状态：未修复。

### 验证结果与边界

- 完整后端第一次从 backend 根目录运行，47 个历史临时目录因 Windows ACL 在收集阶段报错；显式限定 `tests/` 后沙箱临时目录仍出现 setup error 和收尾 PermissionError，均不计为通过。
- 宿主权限、隔离 basetemp 最终结果：**616 passed，6 subtests passed，0 failed，0 setup error，0 skipped，3 warnings**，耗时 12.65 秒。警告为 LangGraph serializer 默认值待变更以及 websockets 两项弃用。
- 另做两个无网络隔离复现：A36 得到 provider_error；A37 得到无证据 complete。当前测试集全绿说明这两个边界尚无回归覆盖，不代表结论不存在。
- 未运行真实模型、前端构建或真实 HTTP；本次没有修改运行中服务。审查只给出问题和建议，不实施重构。
- 官方依据：[LangGraph v1 说明](https://docs.langchain.com/oss/python/releases/langgraph-v1)、[LangChain Tools / ToolNode](https://docs.langchain.com/oss/python/langchain/tools)、[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。

## 2026-09-12：十题真实复杂问答与通用运行时重构前审查

- 基线：`1302726`。只读核对十次真实页面请求的持久化 trace 与 Router、Planner、Policy、执行适配器、完成检查、安全校验和兜底模板；本次未修改业务代码。
- 十题均走 fast，未进入 LangGraph Replan；实际涉及21种业务工具。原页面行为判定为8题失败、2题部分通过，不等于完整数值Grounding验收。
- P1／未修复：问法白名单限制动态规划；能力列表不表达独立子任务与依赖；后端互斥规则删除合法任务；适配器重解析和默认值覆盖明确参数；facts按工具名合并无法保证多次调用隔离。
- P1／未修复：局部答案校验触发整答单工具模板，丢失其他已获得事实；安全禁词将历史机构买卖事实与建议混同。第6题的实际warning确认安全降级，第3/8题确认完整性降级。
- P2／未修复：Query Understanding在回答完成后附加，与执行参数可能不一致；旧记录未保存足够原始答案信息，不能确认每次空回答/固定拒答的生成原因。
- 修正先前判断：第5题K线实际参数为12日，不能判为默认窗口错误；第2题排名和K线工具均被选择，但动态实体未绑定，不能仅归因于漏选工具。
- 当时方案与逐题 run_id 证据已随旧设计文档退役；相关实现历史仍可从对应 Git 提交追溯。
- 处理状态：仅完成审查及设计，所有实现问题保持开放。后续触发条件为实施工具适配层或新运行时，必须重跑旧十题、新组合盲测及真实HTTP/SSE。
- 验证：执行只读SQLite查询与源码核对；文档提交前检查完整diff和diff --check。没有运行新的模型请求或 pytest，不宣称实现验收通过。

## 2026-09-10：Agent 主链路专项审查

- 基线提交：`2fc57c6`；关注当前实现，不对全部历史提交或整个数据流水线作完成审查声明。
- 范围：Planner、Capability/Query Contract、Tool Policy、工具执行、回答接地、会话上下文、评估门禁和 SSE 生命周期。
- 本次仅新增审查文档，不修改业务代码。用户已有 `AGENTS.md` 修改和本地数据不纳入提交。
- 结论：已有能力规划、受控工具、确定性计算、模板降级、滚动记忆和评估基础；主要缺口在契约解析正确性、关系级接地、上下文连续性与结果驱动的任务执行。
- 未发现足以认定 P0 的证据；以下区分隔离复现的缺陷与静态代码确认的能力边界。没有把旧 Bad Case 已修复内容重新列为当前问题。

### 验证结果与边界

- 定向 pytest：51 项通过、4 个子测试通过；0 失败、0 setup error、0 跳过。
- 另以 Python 直接调用当前函数完成下述三个隔离复现；股票和消息均为合成样本，不代表市场事实。
- 未运行完整后端、前端、真实模型或真实 HTTP 业务验收；本次没有修改运行中服务。不据此推断线上总体成功率或实际部署版本。
- 初始全目录搜索遇到旧临时测试目录 ACL 拒绝访问，后续直接读取本次范围内源码与测试；这些目录不在审查范围内。

### P1 / A01：接地评估能漏过股票之间的指标错配

- 证据：`backend/app/agents/answer_grounding.py:289` 的 `_verify_claim` 对数值遍历全局证据数字，按类别和值匹配，没有绑定同一句话的股票、日期与指标路径。
- 隔离复现：工具事实为甲股份评分 10、涨幅 2%，乙股份评分 90、涨幅 8%；答案交换两者评分和涨幅，`evaluate_answer_grounding` 仍返回 `passed=True`，六项声明均被判支持。正确答案也通过。
- 影响：真实存在的数字被安到错误实体上时，评估可以给出虚假的高接地率。日期与其他关系错配也需专项覆盖，但本次只复现了实体错配。
- 建议：对关键声明校验 `(实体, 日期, 指标, 值, 来源)`，加入数值交换、日期交换和同值不同指标的反例；优先覆盖业务关键事实，勿直接把现有全局值匹配作为线上门禁。
- 状态：已于 2026-09-10 修复。数值与时间声明现在绑定同一答案子句中的股票/日期，并要求证据路径属于兼容记录；新增股票指标互换和同股日期互换反例。定向测试 14 项通过。修复提交：`15f0757`；Bad Case：BC-010。

### P1 / A02：日期前缀污染题材查询，造成错误空结果

- 证据：`backend/app/agents/query_contract.py:652` 的主题正则会捕获日期末尾数字，`_clean_topic_candidate` 没有清理完整日期残片。
- 隔离复现：`近期农业板块涨停过的股票有哪些` 得到 `query=农业`、7 日窗口；`2026-09-08 近期农业板块涨停过的股票有哪些` 得到正确日期与 7 日窗口，但 `query=08近期农业`。
- 影响：合法日期表达改变候选集合，可能把解析失败呈现为没有数据。此项也见于既有 BC-008 的未修复备注，本次确认当前基线仍存在。
- 建议：先解析并剥离日期/时间范围，再解析题材；对日期前后置、自然语言改写与条件组合做契约测试。事实统计仍由确定性工具负责。
- 状态：已于 2026-09-11 修复。主题解析先剥离完整或简写日期，再提取板块；原失败问法现得到 `trade_date=2026-09-08`、`query=农业` 和 7 日窗口。定向测试 29 项及 4 个子测试通过；完整后端 657 项及 18 个子测试通过。修复提交：`635f544`；Bad Case：BC-008。

### P1 / A03：摘要窗口与 Planner 窗口不一致，出现上下文空档

- 证据：`backend/app/services/session_memory.py:85` 首次摘要需至少 8 条消息离开最近 8 条窗口；`:128` 无摘要时最多保留 16 条；`backend/app/agents/chat.py:2106` 却再次只取最近 8 条。
- 隔离复现：12 条成功消息且无摘要，`select_session_context_messages` 返回 12 条，`_build_session_context` 只将第 5 至第 12 条放进 Planner 历史，前 4 条不在任何摘要中。
- 影响：早期研究限制和追问背景可能暂时不可见。专门的 run 字段可以恢复部分股票/窗口信息，但不能替代任意用户约束。
- 建议：以同一个上下文预算管理“已摘要区间 + 未摘要尾部”，不要再次按独立固定长度截断；补充首次摘要前、刷新间隔内和摘要失败时的约束保留测试。
- 状态：未修复；本次授权为审查。风险为多轮丢约束；后续触发条件：下一次会话记忆或多轮能力改动前优先处理。修复提交：无。

### P2 / A04：任务执行是固定批次，复杂依赖缺少通用表达

- 证据：`backend/app/agents/chat.py:598` 先规划一次，`:788` 执行一次工具批次，`:797` 执行确定性 Policy 补救，然后组织答案；`tool_execution/__init__.py:60` 按既定顺序调度。
- Planner 契约 `chat_prompts.py:132` 输出能力及上下文模式，没有步骤依赖、结果引用、待澄清字段或完成条件；`capability_contract.py:388` 主要按工具名合并能力所需调用。
- 能力边界：已有跨工具交集、顺序复用 Facts 和专门的晋级开盘工具；因此不能说“不支持多工具”。但“先筛一批股票，再按结果批量查新闻，缺失后换来源，再比较解释”等新任务依赖手工增加专门流程。
- 建议：保留列表/统计的确定性快路径，对确有依赖的研究任务增加有步数、时间和成本上限的执行循环，显式保存结果集合引用、证据缺口及停止/澄清条件。
- 状态：设计限制，未实施；风险为组合能力扩展成本高。后续触发条件：扩展新的跨工具、多股票研究任务时处理。修复提交：无。

### P2 / A06：SSE 缺少向执行线程传播的取消机制

- 证据：`backend/app/routers/agents.py:1091` 使用无界队列，`:1219` 同步等待队列，`:1228` 启动 daemon 线程；该路径未向模型/工具执行传入取消信号，也没有断连后停止任务的分支。
- 影响：客户端离开或断线后，已启动的工作仍可能继续占用额度和并发租约；这是静态代码风险，未进行网络断连或负载实验。
- 建议：增加请求生命周期取消信号、总截止时间及有界事件缓冲，并区分完成、失败、取消、超时状态。现有单次调用超时和限流不能等价替代取消传播。
- 状态：未实施；风险为资源浪费与恢复体验不足。后续触发条件：开放多人使用、长任务或停止生成交互前处理。修复提交：无。

### 建议顺序

先处理 A01—A03 的可复现正确性问题；按产品任务需要引入 A04 的有限执行循环，并在长任务或多人场景前补 A06。当前缺陷不应通过单纯增加角色数量、接入框架或拉长提示词来衡量是否解决。

## 2026-09-11：基础 Agent Planner/Tool Selection 修复审查

### 范围与架构一致性

- 审查真实冻结基线中更早发生的 Planner/Tool-selection 失败，修改 capability 描述与 prompt、plan normalization、Tool Policy signal、事实模板和对应测试。
- 复合任务中的“新闻”现在先判断股票作用域，避免 V1 已支持的 `stock_news` 被误判为 `web_research` 并在 Planner 前拒绝。显式“K线+新闻”在 Planner 契约、normalization 和 Policy 三层保持相同语义。
- Capability-first 仍由后端负责编译显式参数。多个六位股票代码按原顺序展开为独立工具调用，窗口由共享提取器生成；执行器既有逐项异常隔离保证单项失败后继续执行。
- Answer LLM 只有在已有可用工具证据且确定性模板确实可回答时，固定笼统拒答才会被替换。安全拒答、无可用证据和真正越界请求不受影响。
- Agent 版本升级为 `first-board-chat-policy-v18-explicit-evidence`，Planner contract 升级为 `capability-first-v3`。Bad Case 新增 BC-017 至 BC-020。

### 验证

- 定向回归最终为 108 项测试及 16 个子测试通过；覆盖复合新闻分类、宽泛 activity 纠偏、多股票参数展开、empty 过度拒答、critic 风险/冻结 K线模板及既有板块成分股路由。
- 完整后端为 696 项测试及 22 个子测试通过，0 失败、0 setup error、0 跳过。首次未限定 `tests` 的运行因仓库中 20 个既有 ACL 目录在收集阶段报 PermissionError，未计为通过；随后以宿主权限和隔离 `--basetemp` 完成有效全量运行。
- 真实模型、完全冻结工具世界单次验收：`LIVE-MULTI-005`、`LIVE-RECOVERY-002`、`LIVE-RECOVERY-004`、`LIVE-MTURN-003` 均 1/1 通过；无 Provider failure。`LIVE-REPLAN-002` 已从 Planner 前拒绝恢复为 raw capability recall 100%、effective required tool recall 100%，但仍因上游评分结果没有绑定到 `dragon_tiger_list.query` 而失败。
- 真实 HTTP：临时 Uvicorn 端口 62150 对原始 BC-018 问法返回 HTTP 200；trace 为 `stock_trend + stock_news`、实际执行 `stock_kline + stock_news`，回答包含 10 日 K线和 7 日新闻，运行版本为 v18。验收服务已停止。

### 问题分级与状态

- P0：未发现。
- P1：复合个股新闻提前拒绝、显式证据折叠、明确多股票参数缺失、empty/风险过度拒答已修复。
- P1：观察值驱动参数绑定仍未实现。`first_board_ratings → dragon_tiger_list.query`、行业 TopN → 成分股、交集集合 → 逐股 K线等动态依赖需要 observation-driven execution 或有界 Replan。下一阶段应先实现结构化引用/集合展开。

## 2026-09-11：LangGraph Phase 1 动态依赖路径审查

### 范围与架构一致性

- 源码提交 `b7eed3b` 新增独立 `complex_graph` 包、确定性 Router、最小 State、结构化结果引用和唯一白名单图；`2a51635` 将完全结构化的交集评分答案改为既有确定性模板，避免无必要的 Answer LLM。
- Fast Path 保持默认且未迁移；只有热股、涨停、集合筛选、评分四类信号同时出现才进入 Complex Path。LangGraph 不实现业务工具、集合事实、Policy、Grounding 或安全规则，只编排现有组件。
- 下游 `first_board_ratings.symbols` 只接受前序实体集合引用，最多 20 个六位股票代码；生产工具与完全冻结工具适配器都按相同参数过滤候选。Planner 的无关 capability 不会扩大白名单图的工具范围。
- 白名单计划把“热股 Top10”和“首板评分”的词面约束限定在各自步骤：热股取 10 条，涨停来源取完整池（非仅首板、上限 100），避免通用 Query Contract 将两个修饰语错误传播到涨停来源。
- Phase 1 没有 Replan、Retry、critic、checkpoint 或循环边。dependency 缺失、空集合、执行错误和参数绑定失败会确定性失败并进入答案/降级路径。

### 验证与 A/B

- 新增 8 项 Router、动态绑定、Graph、Policy/工具白名单和 Fast Path 测试；Agent 定向回归 114 项通过。
- 完整后端首轮为 703/704 通过、22 个子测试通过；唯一失败是旧 mock 严格期望未传 `symbols=None`。改为只在动态集合存在时传参后，定向 14 项通过；补充 Graph 控制 trace 与执行器参数归一化测试后，最终完整后端为 706 项及 22 个子测试通过，0 失败、0 setup error、0 跳过。
- 真实模型完全冻结小样本：Simple 6/6、Multi-tool 6/6，均未进入 Complex Graph；目标 `LIVE-REPLAN-006` 从旧基线 0/3 提升到 3/3，raw capability recall 与 effective tool recall 均为 100%，Provider failure 为 0。
- 目标调用严格为 `hot_stock_ranking → limit_up_events → first_board_ratings`，评分参数是前两项实际 Observation 的同序交集。工具调用保持 3；模板优化后模型调用从 2 降为 1，平均 token 从 5,367 降至 2,809，p50/p95 延迟为 1,057/1,325 ms。
- 真实 HTTP/SSE：重启本地 Uvicorn 后，简单指数请求返回 `route=fast`；复杂原始问法依次返回兼容的 `progress`、`answer_delta`、`completed` 事件。生产工具结果交集为风华高科（000636），评分工具 trace 的 `symbols=["000636"]`，答案明确披露该股不在当前评级候选池。Graph 控制 trace 已从业务工具、Policy repair 和用户证据卡统计中排除。

### 问题分级与状态

- P0：未发现。
- P1：`LIVE-REPLAN-006` 的多来源集合绑定已修复；Fast Path 小样本未发现回归。
- P2：当前 Router 和 Graph 只覆盖一个白名单场景，另外 7 个 Replan 与 2 个 Stress case 未声称修复；在新增第二个经过真实 Bad Case 驱动的场景前，不建议立即引入通用 bounded Replan。
# 2026-09-12：通用任务运行时第一批实现审查

- 范围：task_runtime 新包、Chat 入口开关、控制 trace 分类；未修改业务评分、行情数据和题库。
- 证据：新运行时9项测试通过；完整后端宿主回归748项、22个子测试通过，无失败/跳过，1项依赖弃用警告。沙箱首轮临时目录 ACL 及清理异常中断，未产生可信最终计数，不计通过。
- P1 已修正待真实复测：字段路径语法导致整答异常；成功 fan-out 证据被预算异常覆盖；遗漏任务被 Completion 误判完成。
- P1 未完成：语义安全与完整事实关系校验、简单题成本门禁、deadline/取消/恢复、完整HTTP和稳定性验收。`LIMITUPLAB_AGENT_RUNTIME=task` 仅迁移验证，默认 legacy，禁止按此结果发布为高可用。
- P2：当前顺序执行独立步骤，尚未并发；摘要有截断标记，但尚无按需证据展开协议。

## 2026-09-12：任务运行时第二批收敛审查

- 增加独立步骤Schema、通用select/filter/sort/TopN/predicate、payload/selected显式引用、批量参数类型适配、工具公开输出契约、逐项答案修复和数值声明校验；没有按股票名或问题句式新增场景。
- 冻结简单题6×3为17/18（94.44%），稳定率83.33%，Provider失败率5.56%；复杂Replan 8×3为15/24（62.5%），3/3稳定3/8，effective required tool recall 97.62%。均按原门禁判定，未通过发布。
- P1：复杂题仍有过度补查/无效Replan与预算失败；对返回粒度不足增加 `can_recover=false` 终止语义，需新一轮真实模型验收。Provider结构化调用失败未自动重试，符合真实失败计数，但稳定性未达标。
- 真实HTTP：双日期同工具及empty隔离均达到complete，2次模型/2次工具；SSE交易指令请求1次模型、0工具并正确拒答。旧服务8000/8001未替换，测试端口已关闭。
- 最终完整后端回归在第二批中间版本为753项、22子测试通过；后续定向37项通过。最新完整回归在沙箱为715通过、42个setup error，均为pytest临时目录Windows ACL，不能记为全量通过；宿主重跑受账户工具使用额度阻塞。

## 2026-09-12：复杂冻结复测后的契约审查

- `6f4eea3` 上真实复杂8×3仅10/24通过，稳定3/8；Provider失败为0，排除接口波动为主因。主要失败为未触发条件分支被本地覆盖规则重新判缺、Planner `source_text`轻微改写使整图失败、绑定错误被模型误判不可恢复。
- P1 已修待真实复测：条件判定写入ExecutionRecord；未触发互斥分支作为可审计观测；绑定/依赖契约错误强制保留一次受限恢复机会；`source_text`回退到原始问题；回答数字区分用户范围与市场事实。
- 以上改动为通用执行/证据语义，没有新增股票名或问题句式分支；定向43项通过。尚未取得修改后的8×3报告，发布状态仍为不通过，默认legacy。

## 2026-09-12：任务运行时收尾审查

- 最新正式复杂8×3报告 `live-20260912T081721Z-a6ee40f5`：18/24通过、稳定4/8、Provider失败0、能力与工具召回100%、无效Replan 0；平均约40951 token、p95 23058ms。相比10/24改善，但稳定性和成本不达门槛。
- P1 已修：Completion可在同一次观测判断中提出最小patch，独立Replan仅作兜底；两类patch共享签名去重；谓词字段不存在不再等价于false；派生选择错误不再覆盖成功工具事实；原生结构化输出失败可显式JSON重试且仍计入Provider失败。
- P0 已修待真实复验：真实HTTP发现2026-05-15问题混入2026-09当前热榜/新闻。工具Schema现声明历史能力/快照日期，生产执行前阻止`snapshot_only`工具跨日期调用；用户要求停止后未再跑Live/HTTP。
- 测试：定向48项通过；改时间门禁前Agent回归237项通过，完整后端767项及22子测试通过。时间门禁后的全量回归未重复执行；默认仍为legacy。

## 2026-09-12：ReAct B 最小闭环实施审查（未完成 B/C/D 验收）

- 范围：标准 AI/ToolMessage 模型适配、单一 StateGraph、逐调用 Policy/Observe、完整证据与计算引用、顶层 task_status，默认入口改为 react，legacy/task 仅显式回滚。不自动降级旧链路。
- 定向测试 6 通过，0 失败/跳过，1 条 LangGraph serializer 弃用预告；按用户要求未跑大规模测试。
- 页面验证真实模型/生产数据：首次动态名单+行情任务失败（BC-027）；修正分页/签名/保留收尾后已有可读结果，但仍有不充分的口径解释和部分事实判断错误。仅证明标准循环实际工作，不能宣称复杂任务质量达标。
- P1 待后续提交：工具历史/输出契约覆盖、受控调用超时及底层重试、事实关系和未验证声明门禁、上下文证据继承。
- P1 待 D：checkpoint、幂等/重连/取消/清理、前端业务终态；当前仅 HTTP response/trace 有 task_status，运行表 success 仍仅表示执行返回。
- 本提交作为实施中间点，不满足发布门槛；本地后端显式 react，用于后续验证。

### ReAct C 契约接入进展

- 全部 26 个既有工具列入显式时间/集合覆盖表 `react_runtime/catalog.py`；未知工具缺契约时注册失败，不再按参数名称猜测历史能力。
- ReAct 调度不依赖 task DAG adapter 或 capability 修复；补齐原虚拟 first_board_filter 的直接执行、原 Python 必填日期及空 symbols 防扩张检查。
- 历史证据仅从 Router 提供的同会话 assistant metadata 恢复，并标注 historical_reference；不作为当前结果缓存。截断全集不能通过交并差集证明全量结论。
- 定向 16 通过、1 条依赖弃用预告；首次测试 1 失败因新增测试缺 created_at，补齐 fixture 后通过。尚未完成全部真实工具/多轮验收；用户要求跳过上一条回答的重复验证，下一阶段集中验证新增运行流程。

### 2026-09-12：ReAct C 集合证据链续作审查

- 范围：基于 `ba5db9b`，只修改 evidence、计算工具说明、新增证据回归测试及计划/Bad Case/本记录；现有未提交 runtime、lifecycle、Router、Provider 和运行时测试改动保留，不纳入本次提交。对应提交以 `fix: preserve ReAct computation evidence lineage` 标题定位。
- P1 已修（本提交）：派生历史结果被标为当前事实（BC-028）；缺失或截断的空结果参与完整差集、连续计算丢失缺失原因（BC-029）；重复/空主键污染集合与计数（BC-030）。证据版本递增为 v2，保留来源证据 ID；显式排名切片不冒充来源缺页。
- 测试：ReAct 定向 59 通过、0 失败/跳过；默认 react 完整后端 733 通过、94 失败、22 子测试通过、0 setup error/跳过；显式 legacy 完整后端 827 通过、22 子测试通过、0 失败/setup error/跳过。宿主完整回归均有 1 条 LangGraph serializer 弃用预告。首次沙箱全量因 Windows 临时目录 ACL 导致 setup/收尾异常，未取得可信完整汇总，不计为通过；首次定向另有 pytest cache ACL 警告，最终定向关闭缓存后无此警告。
- HTTP：独立随机本地端口、冻结模型和数据、真实 Uvicorn/HTTP/StateGraph。取热榜→distinct→count→finish，HTTP 200、task_status partial、1 次业务工具与 2 次计算；具体缺页原因保留。验证服务已关闭；没有替换现有服务，没有验证生产 Router/真实模型/市场来源，不能称为 Live 验收。
- P1 未完成：94 项默认入口失败暴露旧测试仍断言 Planner intent/agent_plan 或仅提供旧 generate 协议；显式 legacy 复跑全部通过支持协议不匹配判断。下一批迁移测试适配时处理，保持原任务覆盖和阈值；当前不得宣称默认 ReAct 全量回归通过。
- P1 发布风险：默认 react 已在前序提交启用，早于计划 E 的真实质量门槛。本轮未擅自改回部署入口，也未继续扩大工具覆盖；切换/发布前必须完成 B 真实四类场景、稳定性及 C 多轮验收。
- P1 待 D：此处仅保留历史/缺失证据标签，最终回答是否误用历史事实仍须关系门禁；运行恢复/幂等等工作区代码尚未纳入本轮验收，后续接续该工作时独立检查并提交。
- 审查结论：本次为有界数据正确性修复，不改变评分、预测快照、市场日期或用户筛选口径；不触发大规模重构与发布。完整 diff 与空白检查在提交前执行。

### 2026-09-12：ReAct D 后端运行可靠性

- 提交范围：`feat: persist bounded ReAct runs and reconnect safely`。接续工作区已有 lifecycle/react_chat/runtime/Provider 草稿，补齐原子收尾、取消中断任务、完成轮询竞争、事务内删除检查和新链路测试。未修改评分/数据流水线/legacy 编排。
- P1 已修（本提交，BC-031）：最终运行与聊天记录分次落库、取消中断任务仍占用会话、检查与删除之间存在竞争。SQLite 故障注入证明事务回滚；恢复测试在工具完成但 checkpoint 尚未更新处中断，恢复后只执行一次业务工具、两次模型决策；不确定调用不自动重发。
- 测试：ReAct、Provider、会话/运行持久化和限流完整相关集合 99 通过，0 失败/setup error/跳过，3 条 LangGraph/websockets 依赖弃用警告。首次 87 项为 85 通过、2 失败，原因是新增测试响应夹具漏填 generated_by；补齐后通过。没有跑 legacy Agent 全量。
- HTTP：3 项真实 loopback HTTP + 生产路由，独立临时 SQLite/冻结模型和数据；覆盖幂等冲突 409、owner 隔离 404、断线后继续、读取重连不执行、活动会话删除 409、取消任务、completed partial 与会话级联删除。测试服务关闭，不替换已有进程；认证通过测试依赖注入 owner，真实 cookie 签名不在本次新增验收范围。
- Provider：generate_messages 使用请求独立 SDK client，3 秒 timeout 和禁止隐式重试经 mock HTTP 503 验证；共享模型配置不变、失败计入 usage。底层市场来源内部重试仍待独立盘点。
- P1 后续触发：多进程部署前须补数据库执行租约/跨进程 worker 接管；当前只支持单进程 SQLite。真实模型/冻结 Live、输出事实关系门禁和成本门槛仍未完成，不能据此发布为已全面验收。
- P2：恢复要求客户端保留 message_id；不确定远端调用只披露失败，不保证远端恰好一次。取消不强杀线程，已启动的底层请求可能在收尾后完成。证据/checkpoint 随会话删除，独立保留期清理策略待后续部署阶段。

### 2026-09-12：ReAct D 工作台接入

- 后端对应 `bc4f9fd`；本批提交 `feat: expose ReAct task status cancellation and reconnect`，只改前端 API/传输、工作台、类型、样式与回归/文档。
- P1 已修（本提交，BC-032）：统一 HTTP completed/success 掩盖业务 partial，断线重试另建 message_id，未检查草稿提前显示。前端展示真实 task_status；自动重连只执行一次 GET 并带 last cursor，服务端 error 不触发补执行；当前页面手动重试复用原请求参数。取消入口复用同 run_id，只有 validated completed 进入答案区。
- 清理：删除前端关键词 inferChatIntent、answer_delta 类型/消费及原 SSE 解析副本，后端负责语义决策；旧历史消息没有 task_status 时不补造“已完成”标签。
- 验证：前端全量 15 通过，0 失败/跳过；生产构建通过，仍有 Vite 单 chunk 超过 500 kB 提醒。首次构建因 Object.hasOwn 与现有编译 target 不兼容失败，已使用兼容写法；随后沙箱 esbuild spawn EPERM，宿主构建成功。没有执行浏览器视觉验收或真实模型 Live，不将冻结传输测试视为模型能力提升。
- P2 后续：整页刷新后的活动 run 自动发现及持久化恢复入口尚未接入；需要在新增刷新恢复场景时补测试。状态/取消/重连为 D 阶段本批交付，输出关系门禁、批量源调用计量和真实质量门槛仍待完成。

### 2026-09-12：重启后五题真实浏览器验收

- 基线 `a567a93`；后端8001 PID22000（22:00:47）、前端5173 PID21844（22:00:48）。Windows computer-use曾因URL识别两次终止，本次改用Chrome浏览器控制通道实际新建4个会话、提交5题并读取回答，第5题沿用第4题会话。没有HTTP代发、没有修改实现或跑legacy测试。
- 模型与生产来源实际执行，每题1次：Q1动态炸板Top2→新闻条件分支14.261s/3模型/3业务工具；Q2双日期二板集合9.393s/3/2；Q3首板评分第4–6名→10日K线14.936s/7/4；Q4双股票20日K线+龙虎榜15.935s/5/4；Q5缩对象/改日期/改窗口6.781s/3/1。
- 5题执行均结束、服务均complete、回答门禁均passed；不等于5题质量全部通过。Q1主要交付正确，002297新闻出现自然TLS失败并回退stale部分结果，缺口已披露；Q2集合正确但有错误附加推断（BC-033）；Q3名次和首末涨跌数字正确；Q4数据/分支正确但收益口径解释错误（BC-034）；Q5实际唯一调用的对象/日期/10日窗口正确，页面链接日期错误（BC-035）。三项均待修，后续D输出门禁与链接接地工作触发修复。
- P1：答案门禁未识别BC-033/034。应优先修关系/口径核验，不能据这次全部complete切换发布状态。
- P2：Q3原生usage累计208067 token、7次模型调用，接近模型轮数上限；其余四题总token分别32961/35565/72619/43617。只为这5次请求观察，不构成稳定性或正式成本统计。后续上下文收敛需保持任务约束，不通过降级题目降低成本。
- P2：最大回撤工具按日内high/low算并包含同一根日线，不能推出真实日内峰谷先后；需明确与收盘序列回撤的区别。Q1正文仍展示TLS/curl技术错误，可在输出清洗阶段改善。
- 边界：Q1/Q4的empty补查未自然触发，完全来源失败的隔离未覆盖；只观察到Q1部分来源失败。核对依据为保存的工具结果、bar首末独立计算及页面DOM/截图，不是全部市场来源独立真实性鉴证。
- 完整原问、答案、调用及JSON保留在本地 `output/react-ui-validation-20260912/`，生成报告不提交。浏览器保留最后一题页面，新增会话均保留。本次提交只包含审查/Bad Case记录。

### 2026-09-12：旧执行链路退役阶段审查

- 授权与边界：用户明确要求先删除旧流程，调整原 E 阶段先验收后退役的顺序。本轮不修改评分、数据源、Query Contract 口径、预测快照或数据库迁移，不宣称通过质量发布门槛。回滚方式改为部署已知 Git 提交，不保留运行时切回 legacy/task 的入口。
- 审查范围：`08206b9..5a9e88d`，30 个文件，105 行新增、6922 行删除，净删 6817 行。逐提交检查变更范围与 diff/check；十个提交变更总行数分别为 955、892、506、684、800、472、811、852、697、330，均不超过 1000。没有提交本地数据、生成报告或无关工作区文件。
- `081eccb`：统一聊天入口，删除旧同步/SSE 执行与旧环境开关；`28faa6e`、`f80eae5`：迁出安全检查，删除 Task DAG/PlanPatch/绑定/Writer 及专属测试；`bc3ab45`、`d82ccde`、`b8991d8`：删除场景图测试及完整场景图包；`39ce59c`、`b9ee538`、`694ac8e`：删除不可达旧工具循环、意图计划和回答模板；`5a9e88d`：移除无用导入/关键词常量，解耦通用 Schema 校验。
- 架构核对：ReAct 不再引用 task_runtime、complex_graph 或旧 Policy 的 Schema 私有方法；旧两包不存在 Python 源码消费者，前端无对应代码引用。`chat.py` 3809→681 行；剩余定义按生产入口追溯。保留历史运行 trace 兼容展示，不能因执行器删除破坏旧会话读取。
- 最终测试：ReAct Runtime/Evidence/Contracts/Lifecycle/HTTP、LangChain Provider 和共享 ToolOutcome 共 103 通过、0 失败/跳过，3 条依赖弃用警告。HTTP 参数化覆盖遗留 react/legacy/task 环境值，均走 ReAct。全后端仅 collect-only：772 项成功收集，未执行 legacy 全量。前端源码未改，本次未重复前端测试/构建。
- 中间失败如实记录：第一次沙箱测试出现 9 个临时目录相关 setup error，pytest 清理又报 WinError 5，未形成正常汇总；宿主重跑原集合 91 通过。拆分删除时曾有 1 个 `_AgentPlan` 注解导致的收集错误，修正提交边界后通过，后续该类型连同全部调用者删除。一次 `diff --check` 报末尾空行已清理；最终无空白错误。局部沙箱测试另出现 pytest cache 写权限警告。
- 运行态：已核实并重启原 8001 后端父子进程 20772/22000，新监听 PID24752。真实 HTTP run `run_0e28660ce084445ab331053c80ffd4ed`，generated_by=react-runtime-v1、task_status=complete；查询 2026-09-11 主板收盘二板，仅实际调用 limit_up_events，返回 000823/002201/002912/600876 共4只，与工具完整结果一致。同 message_id 重发响应完全相同，未重复执行工具。本地原问/完整响应保存在 `output/react-retirement-20260912/http.json`，不提交。
- P0：本轮检查未发现新增的跨用户访问或数据破坏证据；此结论限于已执行的隔离/幂等/清理回归，不替代全面安全审计。
- P1 未修：BC-033/034 的事实关系解释错误仍存在；本轮删除不改变这一状态。继续 D 输出接地工作时优先处理。
- P2 未修：BC-035 日期链接、上下文成本及刷新恢复等既有问题本次未处理。回滚操作依赖版本部署；不再承诺单环境开关恢复旧流程。
