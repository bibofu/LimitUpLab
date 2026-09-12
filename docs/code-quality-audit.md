# Agent 应用质量审查

## 2026-09-13：V1.4.0 标签部署失败与 SQLite 只读挂载修复

- GitHub Actions run `34704996201` 的 Windows/Ubuntu 验收通过，生产任务在镜像构建完成后失败。服务器 journal `v1.4.0-20260913T002448.json` 为 `failed_before_switch`，未进入维护、停服务、备份或迁移阶段；公网 `/health` 仍为 200。
- 根因：发布器用 `limituplab-data:/app/data:ro` 启动 schema 容器，SQLite 即使以 URI `mode=ro` 连接，仍需在卷内创建或访问 WAL/SHM 协调文件，因此报 `sqlite3.OperationalError: unable to open database file`。同样的错误也潜伏在后续备份容器。
- 生产隔离复现：数据目录与数据库为 `10001:10001`、容器用户为 UID 10001，直接读取文件成功；原始只读挂载连接失败，`immutable=1` 成功读出 schema version 12。后者可能忽略未 checkpoint WAL，故不作为修复。
- 修复：schema 与 backup 的 Docker 数据卷改为可写挂载，SQLite 源连接继续使用 `mode=ro`；增加命令级回归，防止重新引入 `:ro`。部署入口是 root 安装副本，发布前需审查并更新 `/usr/local/lib/limituplab/release.py`。
- 发布策略：不移动失败的 `v1.4.0`；修复验证后创建 `v1.4.1`，先安装受信发布器，再推送标签并观察自动部署和生产健康状态。
- 验证：部署专项首次在沙箱中 12 项通过、13 项因 Windows 临时目录 ACL setup error，收尾为 `WinError 5`，不计为通过；宿主隔离目录重跑 26/26 通过。统一验收最终为后端 **617 passed + 6 subtests passed**、0 失败/跳过、3 条依赖弃用警告，离线 Agent Dev **89/89**，前端 **15/15**，TypeScript/Vite 构建通过并保留 664.04 kB 单 chunk 警告。

## 2026-09-13：V1.4 文档与标签前阶段审查

- 范围：以当前生产源码和 `v1.3.2..HEAD` 为准，更新根目录、后端、LangChain/LangGraph、评测、代码阅读、面试及里程碑文档；不修改业务代码、评分、数据库或历史预测，不纳入 `artifacts/`、`backend/data/`、`tmp/` 和 `output/`。
- 架构结论：生产聊天是 LangChain 原生 tool calling 驱动的 LangGraph 有界 ReAct 图；旧 Planner/Capability/Tool Policy 补计划、答案模板、Task DAG 和 Complex Graph 仅在历史文档中保留演进记录。Requests Provider 仍不能运行生产 ReAct，文档已取消“聊天回退”承诺，但代码配置债 A36 保持未修。
- 文档修正：删除不存在的 `docs/Tasks.md`、`docs/需求.md` 引用；公开 Chat Eval Dev 数量按实际夹具与本次报告从错误的 120 统一为 89；V1.3 面试问答明确标为历史架构，避免与当前调用图混淆。
- 发布校验：第一次沙箱运行中，pytest 收尾因临时目录 ACL 报 `WinError 5`，Vite/esbuild 因 `spawn EPERM` 失败；同一入口在宿主权限下重跑全部通过。最终为后端 **616 passed + 6 subtests passed**、0 失败/跳过、3 条依赖弃用警告；离线 Agent Dev **89/89**；前端 **15/15**；TypeScript/Vite 构建通过，保留 664.04 kB 单 chunk 警告。
- 风险状态：未新增 P0。A37 类型化 `finish` 绕过、A38 评分规则未完整版本化和 BC-033/034 仍为 P1；A39-A42 及正式 Live/Holdout/Judge/成本门禁仍未完成。因此 `v1.4.0` 只标记架构与文档基线，不表示模型质量或评分有效性已全部验收。
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
- 状态：未修复；下一次 Agent 配置或 Provider 改动前处理。

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
- 状态：未修复；新增或修改工具时最容易触发。

### P2 / A41：旧 Query Understanding 仍作为公共 API 和评测口径存在，与生产 ReAct 语义形成双轨

- 证据：`agents/query_contract.py` 仍有 923 行正则解析和 contract builder；当前生产 Agent 只从中导入日期锚点及几个 normalize helper，完整 `build_limit_up_query_contract` / `build_market_event_query_contract` 只由评测代码使用，却仍由 `agents/__init__.py` 公开导出。`docs/LangChain_Integration.md:18-35` 的流程图仍描述已经退役的 Planner/Policy/模板链路，并明确声称没有 LangGraph。
- 影响：离线 Query 指标可继续评价旧解析器而非真实 ReAct 决策；公共导出和过时文档让维护者误判生产调用链。
- 建议：把仍需共享的日期/枚举规范化拆成小型 contract 模块；把旧 parser 明确移到 eval/compat 命名空间或退役，对 ReAct 使用真实 decision/tool trace 评测。同步重写 LangChain 集成文档。
- 状态：未修复；属于上一轮旧链路退役后的收尾冗余。

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
- 方案与逐题run_id证据见 [通用任务运行时重构设计](agent-task-runtime-redesign.md)。以结构化任务契约、类型化计划、真实LLM Replan、参数直通、不可变证据、逐任务完成检查和无损兜底替换问法分支。
- 处理状态：仅完成审查及设计，所有实现问题保持开放。后续触发条件为实施工具适配层或新运行时，必须重跑旧十题、新组合盲测及真实HTTP/SSE。
- 验证：执行只读SQLite查询与源码核对；文档提交前检查完整diff和diff --check。没有运行新的模型评测或pytest，不宣称实现验收通过。

## 2026-09-10：Agent 主链路专项审查

- 基线提交：`2fc57c6`；关注当前实现，不对全部历史提交或整个数据流水线作完成审查声明。
- 范围：Planner、Capability/Query Contract、Tool Policy、工具执行、回答接地、会话上下文、评估门禁和 SSE 生命周期。
- 本次仅新增审查文档，不修改业务代码。用户已有 `AGENTS.md` 修改和本地数据不纳入提交。
- 结论：已有能力规划、受控工具、确定性计算、模板降级、滚动记忆和评估基础；主要缺口在契约解析正确性、关系级接地、上下文连续性与结果驱动的任务执行。
- 未发现足以认定 P0 的证据；以下区分隔离复现的缺陷与静态代码确认的能力边界。没有把旧 Bad Case 已修复内容重新列为当前问题。

### 验证结果与边界

- 定向 pytest：51 项通过、4 个子测试通过；0 失败、0 setup error、0 跳过。
- 测试文件：`test_answer_grounding.py`、`test_query_contract.py`、`test_agent_capability_contract.py`、`test_session_memory.py`、`test_agent_eval_runner.py`。
- 命令：在 `backend` 下执行 `.venv/Scripts/python.exe -m pytest tests/test_answer_grounding.py tests/test_query_contract.py tests/test_agent_capability_contract.py tests/test_session_memory.py tests/test_agent_eval_runner.py -q -p no:cacheprovider --basetemp=C:/Users/Administrator/Desktop/LimitUpLab/output/audit-20260910-pytest`。
- 另以 Python 直接调用当前函数完成下述三个隔离复现；股票和消息均为合成样本，不代表市场事实。
- 未运行完整后端、前端、真实模型或真实 HTTP 业务验收；本次没有修改运行中服务。不据此推断线上总体成功率或实际部署版本。
- 初始全目录搜索遇到旧临时测试目录 ACL 拒绝访问，后续直接读取本次范围内源码与测试；这些目录不在审查范围内。

### P1 / A01：接地评估能漏过股票之间的指标错配

- 证据：`backend/app/agents/answer_grounding.py:289` 的 `_verify_claim` 对数值遍历全局证据数字，按类别和值匹配，没有绑定同一句话的股票、日期与指标路径。
- 隔离复现：工具事实为甲股份评分 10、涨幅 2%，乙股份评分 90、涨幅 8%；答案交换两者评分和涨幅，`evaluate_answer_grounding` 仍返回 `passed=True`，六项声明均被判支持。正确答案也通过。
- 影响：真实存在的数字被安到错误实体上时，评估可以给出虚假的高接地率。日期与其他关系错配也需专项覆盖，但本次只复现了实体错配。
- 线上边界：该评估器的生产源码调用方是 `eval_runner.py` 与 `golden_eval.py`；`chat.py:940` 附近是专项名单/统计检查，尚无通用关系级接地门禁。
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

### P2 / A05：离线门禁不能证明真实模型下的成功率

- 证据：`scripts/check_project.py:40` 的共享门禁运行 core/product offline；`backend/app/agents/eval_runner.py:48` 的离线 Provider 强制走确定性降级。脚本自身正确声明此边界。
- 已有能力：`backend/scripts/run_agent_eval.py` 已提供 live-llm、改写、多轮、重复试验和 live-answer 开关，不能说项目没有真实模型评估。
- 缺口：当前默认发布门禁没有执行这些真实模型评估；离线通过不能证明能力选择、改写理解或自然语言回答稳定，A01 还会影响接地指标可信度。
- 建议：修复接地评分后，增加独立、可控预算的版本验收集；分开报告 Planner 正确率、Policy 补救率、最终任务成功率、模板降级率、延迟和成本。不必在每次普通提交上调用付费模型。
- 状态：未实施；风险为版本质量证据不足。后续触发条件：发布涉及 Planner/模型/提示词的版本前执行。修复提交：无。

### P2 / A06：SSE 缺少向执行线程传播的取消机制

- 证据：`backend/app/routers/agents.py:1091` 使用无界队列，`:1219` 同步等待队列，`:1228` 启动 daemon 线程；该路径未向模型/工具执行传入取消信号，也没有断连后停止任务的分支。
- 影响：客户端离开或断线后，已启动的工作仍可能继续占用额度和并发租约；这是静态代码风险，未进行网络断连或负载实验。
- 建议：增加请求生命周期取消信号、总截止时间及有界事件缓冲，并区分完成、失败、取消、超时状态。现有单次调用超时和限流不能等价替代取消传播。
- 状态：未实施；风险为资源浪费与恢复体验不足。后续触发条件：开放多人使用、长任务或停止生成交互前处理。修复提交：无。

### 建议顺序

先处理 A01—A03 的可复现正确性问题，再完善 A05 的版本验收证据；按产品任务需要引入 A04 的有限执行循环，并在长任务或多人场景前补 A06。当前缺陷不应通过单纯增加角色数量、接入框架或拉长提示词来衡量是否解决。

## 2026-09-10：评测资产清理与 Golden 入口收敛

### 范围与结果

- 按用户确认的清单删除 6 个非 Golden 用例 JSON、`backend/data` 中 5 个 Agent 历史评测 JSON、`output/validation` 中 51 个评测失败 JSON。删除前逐个验证绝对路径属于项目目录；没有递归删除目录。
- fixtures 中唯一 JSON 为 `agent_golden_dataset.json`；50 条用例内容未变，SHA256 清理前后均为 `B8C9362ADFDA08E499A65DF580E66475BB1528805B4BC3DE247933172D9C587C`。
- CLI 默认且仅支持 Golden；CI、评测接口和启用离线评测的系统健康检查使用同一数据源。接口保持原有字段，四层失败带层名展开，实际工具轨迹与答案预览取自同一次运行。
- 删除旧题库整库加载测试、失去消费者的加载器、序列化器和测试替身。保留独立构造输入的组件测试，没有把旧 JSON 复制成另一种题库。
- 没有修改 Agent 问答、评分和数据流水线逻辑；没有更改 Golden 判定标准。本次不处理 ReAct 或评测体系升级。
- 用户已有 `AGENTS.md` 修改、`testQuestion.md` 和 `interviewPre.md` 删除不纳入任务提交；业务数据、Markdown 和综合验证日志保留。验证产生的临时评测 JSON 在交付前清理。

### 验证

- 第一轮定向 pytest 遇到 Windows 临时目录 ACL，5 个 setup error，收尾也发生 PermissionError；不能算作通过。改用宿主权限及新的隔离目录后，17 项定向测试通过。
- 完整后端验收：608 项测试及 18 个子测试通过，0 失败、0 setup error、0 跳过。首次完整日志：`output/validation/20260910T133026Z-5f9a7845/pytest.log`；最终移除无消费者常量/测试替身后再次完整验证，同为 608 项及 18 个子测试通过，日志为 `output/golden-cleanup/final-pytest.log`。
- Golden 离线评测：清理前后均为 19/50 通过、31/50 失败，四层失败明细逐项对照完全一致；Planner 78%、Tool Execution 54%、Grounding 66%、Answer 80%。CLI 返回 1，因此整个共享验收门禁正确报告失败。
- 31 个既有失败 ID：G003、G005、G008、G009、G012、G013、G016、G017、G018、G019、G020、G021、G022、G023、G024、G025、G026、G029、G031、G034、G035、G037、G040、G041、G042、G044、G045、G046、G048、G049、G050。逐项原话和失败层明细见本地 `output/golden-cleanup/verification.md`，生成报告不提交。
- 真实 HTTP：独立 Uvicorn（PID 16556、端口 62099、隔离 SQLite）执行 `/api/agents/eval` 与 `/api/agents/system-health?run_offline_eval=true`，均 HTTP 200。评测返回 total=50、passed=19、failed=31，逐项错误与 CLI 基线一致；健康检查返回 total=50、failed=31、passed=false。
- 运行版本分别为 `agent-eval-panel-v2:agent-golden-v1` 和 `agent-system-health-v2-golden`；验证服务已停止。未调用真实模型，未修改现有运行服务。
- 本轮没有前端变更，未运行前端测试和构建。旧文件引用搜索及 `git diff --check` 纳入提交检查。

### 问题与处理状态

- P1：Golden 仍有 31 项既有失败，涉及能力/参数不符、证据缺失、拒答和答案契约。风险为研究问答质量不足，证据为清理前后同样的逐项结果。按本次范围暂不修复；下次 Agent 质量修复或发布前处理，不放宽金标绕过。
- P2：多题库、多入口引用与历史 JSON 报告混杂已完成清理。相关提交：`95aa49e`（入口、接口、CI 与文档）、`e3a50fe`（旧 core/product、V1 和契约题库清理）；本文所在提交完成剩余改写/多轮题库及无消费者代码清理。
- 上一节 A01—A06 的业务缺陷与架构限制维持原状态；此次仅完成 A05 中的数据源和默认入口收敛，不表示已建立真实模型验收门禁。

## 2026-09-11：Chat Eval V2 七层链路阶段性审查

### 审查范围与提交边界

本次审查覆盖 `876e3ff` 至 `635f544` 的 Chat Eval V2 工作，共 21 个小提交；每个源码提交均少于 1000 行。变更按职责分组如下：

- 数据契约与资产：`876e3ff`、`daeae8c`、`67e455d`、`80ad72f`；涉及 `chat_eval_dataset.py`、Dev/工具 fixture、生成器和 V1 50 题迁移清单。
- 七层评估与关系接地：`790d3c0`、`34a7fcd`、`15f0757`、`695e7c1`、`8d60a12`；涉及 `chat_eval_v2.py`、`answer_grounding.py`、Judge 校准和变异测试。
- Query、Planner、Policy 与多轮 trace：`47074ef`、`8554767`、`72afa0e`、`fc04262`、`635f544`；涉及锚定日期、统一 Query View、raw/resolved Planner 分离和动态修复判定。
- Runner、报告、接口与门禁：`a271c1b`、`a546d4c`、`5c6a668`、`baffcff`、`71bce05`；涉及 offline/live/online-shadow、CLI、发布门槛、最近报告 API、12-case smoke 和旧四层 Golden 清理。
- Bad Case 修复与记录：`a780bf5`、`19a71c2`，并更新 BC-008、BC-010 至 BC-013 的回归证据。

未把用户已有的 `docs/V1.4_Design.md` 删除、未跟踪的 `backend/data/`、`tmp/`、本地数据库或 `output/agent-eval` 生成报告纳入提交。未发现重复评测入口、调试代码、真实密钥或 Holdout 内容进入版本库。

### 架构与数据正确性

- 七层报告逐层输出 `pass/fail/not_applicable`，不生成加权总分；Offline Planner 明确为 N/A，Live 保留 raw capability/tool calls，Policy 只为自身修复负责。
- 日期相对词在评测上下文中只读 `anchor_datetime`；Dev 与 Live 都使用同一个版本化工具 fixture，不访问当前数据库或网络。生产请求仍保持原有最新本地交易日语义。
- Query View 已覆盖日期、窗口、股票、板块、市场、板数、事件状态/类型、排序、Top-N、完整名单和上下文指代。审查时重新复现了旧 A02 的日期污染并完成修复。
- Grounding 以实体—日期—指标—值—来源关系判定，股票数值互换、日期互换、指标类别错误和无法解析的事实声明均不能默认通过。
- 120 Dev + 40 私有 Holdout 的分布由 loader 强制校验；全量选择还拒绝跨 split 重复对话。V1 的 G001–G050 均有明确 exact/reauthored/rejected 去向。
- `/api/agents/eval` 只读取原子发布的最近完成报告；完整评测由 CLI/CI 触发，系统健康只执行固定 12-case smoke。

### 验证结果

- 最终完整后端：657 项测试及 18 个子测试通过；0 失败、0 setup error、0 跳过。命令使用独立 `--basetemp`；此前沙箱内一次定向测试有 1 个 `tmp_path` setup PermissionError，改用批准的宿主测试目录后 27 项通过，未把环境错误计为通过。
- 前端：10 项逻辑测试通过；TypeScript/Vite 生产构建成功。构建仅报告现有单 chunk 大于 500 kB 的非阻断警告。
- V2 Offline Dev：120/120 case、15/15 Critical、七个可适用阶段均 100%；Planner raw accuracy 和 3/3 stable 正确为 N/A；Query 518/518 字段、required tool 131/131、参数 144/144、claim precision 与 evidence completeness 均为 100%，最大工具调用 4。
- 真实 HTTP（独立 Uvicorn、端口 62122、LLM 关闭）：`/api/agents/eval` 返回 120/120，未带管理员密钥返回 401；系统健康 smoke 为 12/12；正常首板查询、BC-013 empty/缺失披露、冻结报告中的 4/4 error case、多轮 `2026-05-15 + main_board` 继承和重仓问题拒答均通过。服务验证后已停止。
- BC-013 修复后的确定性降级和真实 Planner 替身都返回 `symbol_not_found`；Answer LLM 不会在目标实体缺失时继续生成。

### 问题分级与状态

- P0：未发现。
- P1：本次范围内发现的关系接地、相对日期/多轮 Query、Policy 修复归因、百分比分类、指定股票缺失和日期污染均已修复并有回归测试；当前无未处理 P1。
- P2：尚未执行真实供应商的 `160 × 3 + Judge` 发布跑批，因为仓库按设计不包含私有 Holdout、Judge 凭据、50 条人工双标校准集或批准的真实模型基线。缺失配置会返回 `configuration_error`，不能把本次 120-case Offline 通过解释为真实模型已达成熟门槛。后续触发条件：准备模型/Prompt 发布时注入私有资产，先冻结批准基线，再执行完整发布命令。
- P2：Online Shadow 的匿名化与只读 trace 路径已有测试，但本次没有可授权的 30–50 条真实线上样本供运行。后续触发条件：积累并人工确认真实问题后执行周度抽样，确认失败再回流 Dev。
- P2：上一节 A03 的生产会话摘要窗口空档不属于本轮评测实现，仍保持未修复；V2 多轮测试验证评测 Query View，不代表该生产 Memory 问题已经解决。后续修改会话记忆前优先处理。

## 2026-09-11：Live Behavioral Eval 工具世界冻结审查

### 范围与结论

- 审查 `chat_live_eval_runner.py`、共享工具 dispatcher、Tool Policy reconcile 扩展点、36-case Live 数据契约与相关测试。生产工具实现、数据库、数据流水线和用户问答语义未修改。
- Live 环境由 `sample-events-plus-current-registry-v1` 升级为 `chat-live-world-v2-fully-frozen-v2`。默认 runner 不再创建生产 `AgentToolRegistry`；真实 Planner/Answer 保留，初始调用和 Policy repair 统一读取专用 `chat-live-world-v2`。
- `chat-live-world-v2` 明确标记为人工策展的行为评测世界，而非真实历史行情重建；加载器会验证 24 个 V1 工具覆盖、统一锚定日期、实体/板块关系、事件汇总与 TopN 题目所需数据量。
- failure injection 在冻结层按工具和可选参数匹配；未定义或未启用工具只返回可审计 error，不允许 fallback 到 SQLite、DuckDB 或网络 provider。
- 报告新增冻结声明：fixture snapshot、`fully_frozen`、database/network access。历史部分冻结 baseline 与新环境不可直接比较，必须重新冻结 36×3 基线。

### 验证与问题分级

- 定向与共享 Agent 回归：108 项测试及 12 个子测试通过。完整后端在宿主隔离目录中为 688 项测试及 18 个子测试通过，0 失败、0 setup error、0 跳过；此前一次沙箱运行有 1 个 `tmp_path` ACL setup error（其余 106 项通过），未计为成功。另有端到端替身验证真实生产编排只接收到 `2026-05-15` fixture facts。
- P0：未发现。
- P1：最终 36×1 预检通过 22/36，Critical 仅 6/14；8 个 Replan 和 2 个 Stress 全部失败。主要证据是下游工具参数没有绑定上游 Observation、复杂入口未形成有效 capability 集合，以及按实体失败注入因调用缺少实体参数而未触发。修复这些 Agent 行为前不得冻结 36×3 正式基线。
- P2：冻结 payload 是专用、一致的行为评测世界，不是真实 2026-05-15 行情重建。Judge 尚未启用，sentence-level claim ledger 仍缺失；后续触发条件为 36×1 Critical 和 Replan 明显恢复，再生成新的 36×3 Judge 校准样本。

## 2026-09-11：基础 Agent Planner/Tool Selection 修复审查

### 范围与架构一致性

- 审查真实冻结基线中更早发生的 Planner/Tool-selection 失败，修改 capability 描述与 prompt、plan normalization、Tool Policy signal、事实模板和对应测试；没有修改 Live Golden、冻结工具 fixture、evaluator 或通过门槛。
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
- P1：观察值驱动参数绑定仍未实现。`first_board_ratings → dragon_tiger_list.query`、行业 TopN → 成分股、交集集合 → 逐股 K线等动态依赖需要 observation-driven execution 或有界 Replan；本次不以预执行全部可选工具或放宽 Golden 掩盖。下一阶段应先实现结构化引用/集合展开，再重跑 8 个 Replan 与 2 个 Stress。
- P2：真实模型结果目前是修复用例的单次验收，不代表新的 36×3 稳定性基线；完成动态参数绑定后再执行全量基线与 Judge 校准。

## 2026-09-11：LangGraph Phase 1 动态依赖路径审查

### 范围与架构一致性

- 源码提交 `b7eed3b` 新增独立 `complex_graph` 包、确定性 Router、最小 State、结构化结果引用和唯一白名单图；`2a51635` 将完全结构化的交集评分答案改为既有确定性模板，避免无必要的 Answer LLM。
- Fast Path 保持默认且未迁移；只有热股、涨停、集合筛选、评分四类信号同时出现才进入 Complex Path。LangGraph 不实现业务工具、集合事实、Policy、Grounding 或安全规则，只编排现有组件。
- 下游 `first_board_ratings.symbols` 只接受前序实体集合引用，最多 20 个六位股票代码；生产工具与完全冻结工具适配器都按相同参数过滤候选。Planner 的无关 capability 不会扩大白名单图的工具范围。
- 白名单计划把“热股 Top10”和“首板评分”的词面约束限定在各自步骤：热股取 10 条，涨停来源取完整池（非仅首板、上限 100），避免通用 Query Contract 将两个修饰语错误传播到涨停来源。
- Phase 1 没有 Replan、Retry、critic、checkpoint 或循环边。dependency 缺失、空集合、执行错误和参数绑定失败会确定性失败并进入答案/降级路径。

### 验证与 A/B

- 新增 8 项 Router、动态绑定、Graph、Policy/工具白名单和 Fast Path 测试；Agent 定向回归 114 项通过，评测契约/runner 回归在宿主隔离目录 65 项通过。沙箱内评测测试曾有 4 个 `tmp_path` setup error，原因是 Windows pytest 临时目录 ACL，未计为通过。
- 完整后端首轮为 703/704 通过、22 个子测试通过；唯一失败是旧 mock 严格期望未传 `symbols=None`。改为只在动态集合存在时传参后，定向 14 项通过；补充 Graph 控制 trace 与执行器参数归一化测试后，最终完整后端为 706 项及 22 个子测试通过，0 失败、0 setup error、0 跳过。
- 真实模型完全冻结小样本：Simple 6/6、Multi-tool 6/6，均未进入 Complex Graph；目标 `LIVE-REPLAN-006` 从旧基线 0/3 提升到 3/3，raw capability recall 与 effective tool recall 均为 100%，Provider failure 为 0。
- 目标调用严格为 `hot_stock_ranking → limit_up_events → first_board_ratings`，评分参数是前两项实际 Observation 的同序交集。工具调用保持 3；模板优化后模型调用从 2 降为 1，平均 token 从 5,367 降至 2,809，p50/p95 延迟为 1,057/1,325 ms。
- 真实 HTTP/SSE：重启本地 Uvicorn 后，简单指数请求返回 `route=fast`；复杂原始问法依次返回兼容的 `progress`、`answer_delta`、`completed` 事件。生产工具结果交集为风华高科（000636），评分工具 trace 的 `symbols=["000636"]`，答案明确披露该股不在当前评级候选池。Graph 控制 trace 已从业务工具、Policy repair 和用户证据卡统计中排除。

### 问题分级与状态

- P0：未发现。
- P1：`LIVE-REPLAN-006` 的多来源集合绑定已修复；Fast Path 小样本未发现回归。
- P2：当前 Router 和 Graph 只覆盖一个白名单场景，另外 7 个 Replan 与 2 个 Stress case 未声称修复；在新增第二个经过真实 Bad Case 驱动的场景前，不建议立即引入通用 bounded Replan。
- P2：Complex Path 尚未建立 36×3 全量稳定性与 Judge 基线；本轮按计划只执行小范围 A/B，生成报告留在本地 `output/agent-live-eval/`，不提交仓库。
# 2026-09-12：通用任务运行时第一批实现审查

- 范围：task_runtime 新包、Chat 入口开关、控制 trace 分类、Live Eval 读取原始 task_plan；未修改业务评分、行情数据和题库。
- 证据：新运行时9项测试通过；完整后端宿主回归748项、22个子测试通过，无失败/跳过，1项依赖弃用警告。沙箱首轮临时目录 ACL 及清理异常中断，未产生可信最终计数，不计通过。
- 后续 Live Eval trace 适配定向28项通过。真实模型简单6×1初验0/6：原始 capability 与工具 recall 100%，全部超过旧2次LLM预算，3题另有引用异常。
- P1 已修正待真实复测：字段路径语法导致整答异常；成功 fan-out 证据被预算异常覆盖；遗漏任务被 Completion 误判完成。
- P1 未完成：语义安全与完整事实关系校验、简单题成本门禁、deadline/取消/恢复、完整HTTP和稳定性验收。`LIMITUPLAB_AGENT_RUNTIME=task` 仅迁移验证，默认 legacy，禁止按此结果发布为高可用。
- P2：当前顺序执行独立步骤，尚未并发；摘要有截断标记，但尚无按需证据展开协议。后续实现不得放宽原有评测阈值来获得通过。

## 2026-09-12：任务运行时第二批收敛审查

- 增加独立步骤Schema、通用select/filter/sort/TopN/predicate、payload/selected显式引用、批量参数类型适配、工具公开输出契约、逐项答案修复和数值声明校验；没有按股票名或问题句式新增场景。
- 冻结简单题6×3为17/18（94.44%），稳定率83.33%，Provider失败率5.56%；复杂Replan 8×3为15/24（62.5%），3/3稳定3/8，effective required tool recall 97.62%。均按原门禁判定，未通过发布。
- P1：复杂题仍有过度补查/无效Replan与预算失败；对返回粒度不足增加 `can_recover=false` 终止语义，需新一轮真实模型验收。Provider结构化调用失败未自动重试，符合真实失败计数，但稳定性未达标。
- P1：Live Evaluator此前未把 task runtime计为Graph，已纳入编译/Replan统计；冻结 limit_up_events 参数语义已补齐。报告是本地产物，不提交。
- 真实HTTP：双日期同工具及empty隔离均达到complete，2次模型/2次工具；SSE交易指令请求1次模型、0工具并正确拒答。旧服务8000/8001未替换，测试端口已关闭。
- 最终完整后端回归在第二批中间版本为753项、22子测试通过；后续定向37项通过。最新完整回归在沙箱为715通过、42个setup error，均为pytest临时目录Windows ACL，不能记为全量通过；宿主重跑受账户工具使用额度阻塞。

## 2026-09-12：复杂冻结复测后的契约审查

- `6f4eea3` 上真实复杂8×3仅10/24通过，稳定3/8；Provider失败为0，排除接口波动为主因。主要失败为未触发条件分支被本地覆盖规则重新判缺、Planner `source_text`轻微改写使整图失败、绑定错误被模型误判不可恢复，以及Evaluator不识别全量empty的集合反证。
- P1 已修待真实复测：条件判定写入ExecutionRecord；未触发互斥分支作为可审计观测；绑定/依赖契约错误强制保留一次受限恢复机会；`source_text`回退到原始问题；回答数字区分用户范围与市场事实；Evaluator只在无过滤且结果明确empty时接受全集反证。
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
- P1 未完成：94 项默认入口失败暴露旧测试仍断言 Planner intent/agent_plan 或仅提供旧 generate 协议；显式 legacy 复跑全部通过支持协议不匹配判断。下一批迁移评测/测试适配时处理，保持原任务覆盖和阈值；当前不得宣称默认 ReAct 全量回归通过。
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
- 架构核对：ReAct 不再引用 task_runtime、complex_graph 或旧 Policy 的 Schema 私有方法；旧两包不存在 Python 源码消费者，前端无对应代码引用。`chat.py` 3809→681 行；剩余定义按生产入口与现存评测调用追溯。保留历史运行 trace 兼容展示，不能因执行器删除破坏旧会话读取。
- 最终测试：ReAct Runtime/Evidence/Contracts/Lifecycle/HTTP、LangChain Provider 和共享 ToolOutcome 共 103 通过、0 失败/跳过，3 条依赖弃用警告。HTTP 参数化覆盖遗留 react/legacy/task 环境值，均走 ReAct。全后端仅 collect-only：772 项成功收集，未执行 legacy 全量。前端源码未改，本次未重复前端测试/构建。
- 中间失败如实记录：第一次沙箱测试出现 9 个临时目录相关 setup error，pytest 清理又报 WinError 5，未形成正常汇总；宿主重跑原集合 91 通过。拆分删除时曾有 1 个 `_AgentPlan` 注解导致的收集错误，修正提交边界后通过，后续该类型连同全部调用者删除。一次 `diff --check` 报末尾空行已清理；最终无空白错误。局部沙箱测试另出现 pytest cache 写权限警告。
- 运行态：已核实并重启原 8001 后端父子进程 20772/22000，新监听 PID24752。真实 HTTP run `run_0e28660ce084445ab331053c80ffd4ed`，generated_by=react-runtime-v1、task_status=complete；查询 2026-09-11 主板收盘二板，仅实际调用 limit_up_events，返回 000823/002201/002912/600876 共4只，与工具完整结果一致。同 message_id 重发响应完全相同，未重复执行工具。本地原问/完整响应保存在 `output/react-retirement-20260912/http.json`，不提交。
- P0：本轮检查未发现新增的跨用户访问或数据破坏证据；此结论限于已执行的隔离/幂等/清理回归，不替代全面安全审计。
- P1 未修：BC-033/034 的事实关系解释错误、既有正式 Live/Holdout/稳定性验收缺口仍存在；本轮删除不改变这一状态。继续 D 输出接地工作时优先处理。
- P2 未修：旧评测器仍消费 plan_agent_query、旧 Prompt/参数归一化/Policy 和模板；部分旧协议测试尚未迁移。暂留原因是仍有显式评测/共享消费者，不是供聊天回退。下一批清理以评测器迁移和依赖收敛为触发条件，不能将旧 Planner 成绩冒充 ReAct 能力。template_answer_override 对新聊天链路已无效，相关评测模式需随评测器一并退役。
- P2 未修：BC-035 日期链接、上下文成本及刷新恢复等既有问题本次未处理。回滚操作依赖版本部署；不再承诺单环境开关恢复旧流程。

### 2026-09-12：旧评测依赖清理阶段检查（一）

- 用户要求继续退役 Planner/Prompt/Policy 评测依赖。`0d5b5e3` 删除 Chat Eval V2 的 Planner-only Live 执行，CLI 只允许 offline/online-shadow；生产模型评测统一使用 `run_agent_live_eval.py`。历史报告、冻结世界、Dev/Holdout 样本及其阈值不修改。
- `6d67934`～`61692b0` 分小提交删除旧问答、Planner、Policy、模板协议测试与无人使用的模型夹具。保留同文件的数据/能力目录/报告读取/会话测试，43 项通过。旧模板与原始数据断言共存时只移除模板断言，例如 Post Limit 缺失事件窗口检查仍保留。
- 本次 Live 适配变更：移除 Frozen Registry 对旧 Policy 的类型与 repair 方法依赖，使用 ReAct 原始 decision/execution trace；控制 trace 不算业务工具，失败/取消不能通过验收，多轮传递完整已完成响应 metadata。原始工具与实际工具召回分别计算；capability 标签明确由原始工具集合推导，不再当作独立 Planner 意图识别成绩。旧 Replan/Graph Compilation 指标退役，冻结样本中的历史类别和 max_replans 字段保留原样但不作为 ReAct 独立 Replanner 指标。
- Live/回放定向最终 28 通过、0 失败/跳过、1 条依赖弃用警告；包括原生消息两轮决策、控制 trace 计数、单实体失败保留另一实体、错误运行不通过。此前沙箱一次运行 27 通过、1 个报告临时目录 setup error，宿主重跑已通过。以上模型输出为夹具，不是新真实模型质量结论。
- 阶段依赖扫描：移除这些消费者后，旧 eval_runner/chat Planner/Prompt/Policy/执行器可继续物理删除。当前检查无新增数据写入或评分变更；删除尚在进行，后续须完成全后端回归、实际进程重启及 HTTP/CLI 验证后记录最终结果。

### 2026-09-12：旧评测依赖清理阶段检查（二）

- `c88c352`～`44a8166` 删除 chat Planner、模板开关、旧 eval_runner、Prompt、参数归一化、回答验证/模板及 Policy 主体。题库脚本保留 ReAct 真实执行，保存完整响应供多轮恢复，拒绝将新结果续写进旧模板/Planner 报告。
- 删除旧 Policy 的后续批次曾被自动审批拦截，理由为旧执行器仍导入它、消费者边界未证实。随后扫描全部受 Git 跟踪 Python 文件：旧组件外部导入为零，8 处引用均在同批退役组件内部；全后端 621 项成功收集。未绕过审批，先运行完整测试建立服务可用证据后再申请继续。
- 全后端首次 620 通过、1 失败、6 子测试通过；唯一失败为新并发验收测试假定返回固定顺序。按股票/结果配对排序比较后，全后端 621 通过、0 失败/跳过、6 子测试通过，3 条依赖弃用警告。该修正不放宽实体/数量/错误隔离要求，只移除线程完成顺序假设。
- 暂未改动数据口径、评分、预测快照和生产 ReAct 工具行为。还需删除组件内部残留执行器源码，并在最终删除状态复验。BC-033～035 和正式真实模型质量/成本门槛仍保持未完成。

### 2026-09-12：旧评测依赖清理最终记录

- `9a5e877` 完成 Policy 分类器退役及并发测试顺序修正；`d4aa793`、`b50c279` 删除全部旧 Policy/工具执行包。最后清除 capability/tools 中仅供旧 Planner 使用的 Prompt 序列化、能力推导和自动补工具函数；仍使用的能力标签目录、Query Contract、共享数据服务及历史报告兼容保留。
- 最终扫描受 Git 跟踪的 app/tests/scripts Python：已退役模块导入、plan_agent_query、template_answer_override、capability_schema_prompt、ensure_capability_tool_calls、planner_dump 引用均为零。文档同步移除旧 Live 命令及旧调用链说明；没有修改冻结金标、发布阈值或私有 Holdout。
- 完整删除状态先运行全后端 621 通过；再清除共享目录中的旧函数和对应 5 项专属测试后，定向 35 通过，最终全后端 **616 通过、6 子测试通过、0 失败/跳过、3 条依赖弃用警告**（13.06 秒）。警告来自 LangGraph 序列化默认值及 websockets 弃用。前端源码未变，本次未重复前端测试或构建。
- 运行态：核实后停止旧后端 9776/24752，重启后监听 PID17428。真实 HTTP run `run_deba895acce24805a6cc37de6476c130` 完成；2026-09-11 主板收盘二板返回 000823/002201/002912/600876 共4只，业务取数为 limit_up_events，另使用 compute_result 按代码排序。同 message_id 重复请求响应完全一致。本地原问与完整响应保存在 `output/react-eval-retirement-20260912/http.json`，不提交。
- Live CLI 曾被自动审批拦截，要求核实外发数据。只读确认 LIVE-SIMPLE-002 是公开指数问题，工具世界明确为人工冻结夹具、不访问生产数据库、私有 Holdout 或真实会话；提交这些证据后原命令获准。单题单次通过，1 次业务工具、2 次模型调用、17709 token、3643 ms，Judge 未运行。用户随后明确不再跑 Live，已停止扩展；此记录仅证明 CLI 可运行，不代表全量质量或稳定性通过。
- P0：本轮已执行检查未发现新增数据破坏或隔离回归；不替代全面安全审计。P1 未修：BC-033/034 与正式质量/Holdout 门槛仍待后续输出接地工作。P2：上一轮“旧评测消费 Planner/Prompt/Policy”已解决；BC-035、成本、页面刷新恢复等既有问题保持未修，按原计划触发。没有借清理改动评分或不可变预测记录。
- 所有源码提交按不超过 1000 行拆分，逐批检查 diff/check；本次末批连同文档也低于该上限。生成报告、本地数据库、运行日志及其他未跟踪数据未提交。前两次阶段检查保留了中间失败和审批处理证据，最终结果以本节为准。
