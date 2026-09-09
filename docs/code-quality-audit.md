# 全量代码质量审查：冗余代码、超大文件与超大函数

日期：2026-09-09（Asia/Shanghai）。初次审查已完成基础扫描、重点复核及离线验证，报告提交为 `847ed36`。用户随后授权第一轮优化，A01、A09 已在 `0c3f645` 修复；最新状态、验证与新增待办见第 6 节。第 1–5 节及附录保留初次审查基线与历史证据。

起始提交：`9b44f7eb5b748dc203d1b9bb2976ffa8f2e627ef`。最终审查基线：`2c841b5c1829e47f39c8a8946f8d499ae8c7f2a3`。审查期间新增的 `2c841b5` 仅修改全局样式，已重新解析当前 CSS 并运行前端测试和构建，未覆盖该提交。

## 1. 结论与审查边界

确认 **1 项 P1、9 项 P2**。最需要优先解决的不是文件行数，而是 **Agent 普通执行与 Policy 补救重复实现，已经发生查询契约和实际执行不一致**。可以先清理的冗余主要在前端旧样式、无调用方的 API 包装及孤立类型。最值得拆分的是 Agent 编排/回答模板、推荐刷新、聊天路由生命周期及前端大组件。

未发现有充分证据可列为 P0 的问题。这里只表示本次审查的发现，不代表不存在其他缺陷。后文的结构问题按可维护性列为 P2，不把“超过行数阈值”本身等同于功能错误。

### 覆盖口径

- 初步的 232 文件、76,427 行统计仅包含选定代码扩展名。最终补齐 JSON fixtures、Dockerfile、Nginx、cron、示例配置等，覆盖 **256 个文件、78,068 行**，完整清单见附录。
- 其中后端应用 103 文件、后端测试及 JSON fixtures 84 文件、后端脚本 20 文件、前端 24 文件、部署 10 文件、根目录/通用脚本及配置 15 文件。
- 所有纳入范围的文件均已读取，完成规模/结构/重复候选基础扫描；Python、TS/TSX、JSON、CSS、PowerShell 进行相应语法解析。重点文件、引用候选、主要数据与 Agent 链路进一步人工复核。基础扫描不等于逐行证明所有行为正确。
- 排除 Markdown 正文作为代码统计对象、锁文件内容、SSH known_hosts 内容、依赖、缓存、构建输出、数据库及生成报告。另读取 `AGENTS.md`、`badCase.md` 等作为审查约束和回归依据。
- 不执行真实数据采集、线上部署、生产数据库操作或付费模型调用；未做线上 HTTP/浏览器验收。代码未修改，因此未重启运行中的服务。
- Git 仓库所有权检查通过命令级 `-c safe.directory=...` 处理，未更改全局 Git 配置。若干未跟踪测试缓存目录不可读，均不在审查范围；已跟踪审查文件没有读取阻塞。

### 扫描方法与限制

- 文件按物理行计数；Python 使用 AST 起止行，TS/TSX 使用 TypeScript AST。装饰器/注释可能不计入函数起始行，嵌套函数会同时计入父函数跨度和自身记录，不能将函数行数直接相加。
- 21 文件达到 800 行，8 文件达到 1,500 行；2,178 个 Python 函数/方法中 51 个达到 100 行、11 个达到 200 行；428 个 TS/TSX 函数节点中 12 个达到 100 行、4 个达到 200 行。TS 数量包含匿名回调。
- 分支提示统计 AST 条件、循环和异常节点，**不是标准圈复杂度**；大量声明式 SQL、模型字段、工具 schema 和测试样本单独判断。
- Python 函数体去除文档字符串后做精确 AST 比较；代码按连续 12 个有效行比较跨文件重复块；另以保留属性/常量、归一化局部名称的 AST 5-token 集合做近似比较（函数至少 20 行，Jaccard ≥0.70）。后者无命中，不代表没有语义重复。
- 名称只出现一次只是候选：FastAPI 装饰器、Pydantic validator、工具注册和模块导出均复核后再判断。TS 编译器的 `noUnusedLocals` 无法替代跨模块“导出无人使用”检查。
- 当前 CSS 共 807 个规则节点。210 个“选择器中的类名均无静态文本引用”候选全部核对；6 个来自动态类名，应保留。其余 204 个静态候选按功能族列在附录，不把候选数量直接等同于可删除行数或生产体积节省。

## 2. 确认问题

以下为初次审查时的状态：全部问题均为未修复，修复提交为“无”，当时用户限定仅提交报告。后续 A01、A09 的修复提交和其余问题状态以第 6 节为准；各项后续触发条件见整改批次。

### A01 — P1：Policy 补救遗漏跨日和分组参数，trace 却声称执行了新契约

**证据：** `backend/app/agents/tool_policy.py:986` 的 `_repair_limit_up_events` 在第 998–1009 行手动传入旧参数集合，遗漏 `recent_trade_days`、`group_by`；第 1013–1021 行又自行组装缺少窗口/分组信息的 facts。普通执行 `backend/app/agents/tool_execution/stocks.py:232` 之后完整传参并组装新 facts。工具 `backend/app/agents/tools.py:1787` 的窗口默认值是 1。

**隔离复现：** 两个交易日各放入一只农业题材合成样本，调用真实 `AgentToolRegistry`、`execute_tool_calls` 与公开 `AgentToolPolicyEngine.reconcile`，未使用真实数据库或网络。

| 问题 | 普通执行 | 空 execution 经 Policy 补救 |
| --- | --- | --- |
| 近期农业板块涨停过的股票有哪些 | 实际窗口 7；返回 2 只 | 实际窗口 1；只返回最新日 1 只；契约仍记录 7 |
| 近期哪些板块涨停的股票比较多 | 窗口 7，按 concept 聚合；农业 2 只 | 窗口 1，不分组，无 sector_summary；契约仍记录 7、concept |

补救 trace 的状态仍为 `success`。复现结果保存在本地 `output/code-audit-20260909/reproductions.json`。这证明补救接口存在缺陷，**没有宣称上述标准问法在当前主聊天入口必现**：`chat.py:733` 起及专门 fallback 会主动补齐部分标准问法，掩盖了路径差异。当其他调用方或语义变体进入补救路径时，旧实现仍然存在。

**影响：** 同一契约因执行入口不同产生不同股票集合；facts 与 trace 不能如实解释实际窗口，破坏可回放性。`chat.py:2447` 的旧 `_answer_limit_up_query` 也保留相同的旧参数投影，应在统一时一并核对可达场景。

**建议：** 先增加普通执行/Policy 补救的契约一致性用例；把契约到参数、结果到 facts 的转换收敛到一个无反向依赖的领域执行边界。Policy 只判断是否补救并标记 repair 原因，复用该边界。保留现有执行顺序、profile 限制、错误 trace、引用及空结果语义，不通过删掉补救功能消除重复。

**验收：** 上述两问，以及指定日期/近 10 日/空结果/同股跨日重复/题材多标签，分别走直接执行和补救，断言实际工具参数、契约、去重股票数、事件数与分组一致。随后运行全部 Agent 契约与离线评估。实际修复时按项目要求新增 Bad Case 记录。

### A02 — P2：已移除面板的 CSS 仍随应用打包

**证据：** 当前 `frontend/src/styles.css:671` 的 `.chat-tool-traces`、`:676` 的 `.agent-evidence-panel`、`:756` 的 `.planner-policy` 等，当前 TS/TSX 中无组件、字符串或动态类名消费者。该旧证据/trace 区域共 50 个候选规则；旧预测质量、Agent 评估和评分诊断面板也存在整组残留。`frontend/src/main.tsx` 仍整体引入此样式文件。

**影响：** 下线功能继续影响文件规模、样式搜索和全局维护；最终构建仍生成约 67.69 kB CSS，不能依赖 TS 的 tree-shaking 删除 CSS。该体积是全量产物，不是残留部分的体积估计。

**建议：** 按附录功能族移除已确认无消费者的选择器，先处理旧证据/trace、评估、预测质量、评分诊断面板，再将剩余样式按实际组件拆分，保持现有级联顺序。`chat-${item.role}`、`tag-${tone}`、`rating-${...}` 对应的 6 个候选保留。不同 media 下的同名同值规则不自动合并。

**验收：** 前端测试/构建，加首页、详情三种图表、聊天空态/消息/错误态、复盘、盘前两种形态在桌面和窄屏的页面回归；核对 JSX 与动态类名后再删除，不根据正则输出批量清空。

### A03 — P2：10 个前端 API 导出及孤立类型已没有调用方

**证据：** 全仓引用搜索确认以下函数只剩声明，前端也没有 namespace import、动态属性调用或对外包导出入口：

| `frontend/src/api.ts` 行号 | 无调用方函数 |
| --- | --- |
| 96 | fetchLimitUpEvents |
| 116 | fetchContinuationStats |
| 126 | fetchFailedRateStats |
| 130 | fetchPostPerformanceStats |
| 138 | fetchStockKLine |
| 164 | fetchStockPosition |
| 170 | fetchStockLatestClose |
| 261 | fetchDailyReviewSnapshots |
| 267 | fetchScoringErrorDiagnostic |
| 307 | sendAgentChatMessage |

`frontend/src/types.ts:493/533/799/872/906` 的 `PredictionQualityAuditResponse`、`AgentEvalReportResponse`、`AgentRunsResponse`、`AgentSystemHealthResponse`、`DailyPipelineStatusResponse` 在前端源码及测试中也只有声明。旧查询被详情组合接口及流式聊天替代后，导出及类型没有同步清理。

**影响：** 维护者容易继续使用已没有 UI 消费者的旧接口包装；废弃类型扩大前后端契约维护范围。未证明它们全部进入最终 JS，不以此宣称包体积会大幅降低。

**建议：** 删除这些前端导出，再逐层移除仅被它们引用的 imports/类型。保留仍有消费者的共享类型；本次证据只支持前端清理，不能据此删除对应后端公共 HTTP 路由。

**验收：** 全仓符号搜索、前端测试和生产构建；详情页继续请求组合接口，聊天继续走 SSE。

### A04 — P2：聊天编排、领域 fallback 和回答模板仍集中在巨型函数中

**证据：** `backend/app/agents/chat.py:306` 的入口 245 行，`:577` 的 LLM 工具链 456 行，混合规划、工具补救、完整性验证、降级、流式输出与 performance/trace 组装；`:870` 之后多处完整性校验重复赋值 answer/source/warnings。`backend/app/agents/chat_templates.py:64` 的 `_template_answer_from_tool_facts` 达 729 行，包含 129 个条件/循环/异常节点，按多个领域 facts 选择并渲染文本。`tools.py` 还同时容纳约 600 行工具 schema 和实际执行逻辑。

**影响：** 一个领域回答的修改必须穿过跨领域条件及先后顺序；加入新契约后容易只修普通分支而遗漏 fallback，A01 已给出实例。现有 `chat_plan_normalization`、`chat_answer_validation`、`tool_execution` 拆分有帮助，但未收敛所有路径。

**建议：** 先固定“多组 facts 同时存在”的选择优先级和完整性测试，再将模板拆成市场事件、板块、晋级、个股、评分复盘 renderer；主模板仅负责有序选择。随后拆出规划结果、工具执行、回答验证/降级、响应组装四个阶段，并让离线入口复用契约执行边界。最后分离工具 schema 和 registry 实现；不要再增加第二套领域执行器。

**验收：** 原始 Bad Case、完整名单/交集/多组 facts/缺失事实/Planner 失败/最终模型失败/流式与非流式返回的答案及 trace；全部 Agent 测试及 core/product 离线评估。

### A05 — P2：同步聊天与 SSE 路由复制整套会话和执行落库生命周期

**证据：** `backend/app/routers/agents.py:893–1026` 与 `:1030–1214` 分别实现 ensure_session、用户消息写入、上下文准备、调用 Agent、保存 run、写助手消息、计费结束和 lease 释放。内嵌 `run_agent` 自身又有 111 行。协议差异之外，成功/失败路径的大量业务步骤相同。

**影响：** 修改消息去重、owner 校验、错误落库或 usage 完成逻辑需要同步改两个入口，错误路径尤易产生行为分叉。这里确认的是重复与维护风险，没有据此宣称已复现计费或会话泄漏。

**建议：** 抽出共享的请求准备及执行/持久化生命周期，回调承载进度和 token；同步路由适配 JSON，SSE 路由适配队列和事件协议。集中处理 finish 与 release，保留两种传输语义及后台线程边界。

**验收：** 同一输入经两入口得到相同核心 response/run/messages；覆盖 session ownership、限流、模型异常、消息保存失败、finish 异常及 SSE 完成/错误事件。运行 session/security/rate-limit/observability 完整相关测试。

### A06 — P2：推荐刷新和日更编排承载过多阶段，仍有单策略分支残留

**证据：** `backend/app/services/recommendation_intelligence.py:93–531` 共 439 行，单个 `for candidate` 占第 256–492 行，同时做旧证据回退、收盘前后分割、龙虎榜/热度处理、分数修正和模型构建。`:494`、`:504` 两个 `for strategy in ("relay",)`，以及候选内部多次 `strategy == "relay"`，与 `:835` 起候选构建目前只产生 relay 的事实不相称。

日更脚本 `backend/scripts/update_daily_data.py:194–465` 的 272 行入口叠加采集、特征、评级、live/backtest 分流、回填和健康统计；`:547–768` 的回填函数 222 行。外层 `run_daily_close_loop.py:184–417` 再处理日历、锁、重试、推荐刷新和最终报告。这些入口均有实际调用，不能视为死代码。

**影响：** 时间截止、数据缺失和不可变快照的逻辑难以独立验证；新增策略时容易踩到只有 relay 分支初始化的局部变量。当前只有 relay 输入，未把潜在新策略问题列成现有崩溃。

**建议：** 先拆出纯候选评分/结果构建，明确输入证据和缺失字段；再拆证据采集与前值回退、窗口/冻结决策、排序、持久化。移除运行时无意义的单元素策略循环，但保留历史记录兼容模型。日更入口按采集、特征、预测快照、Outcome 回填、报告五阶段组织，外层继续负责锁和重试，不改变写入顺序或事务边界。

**验收：** 截止前后、重复刷新、缺失行情/新闻/热度、历史回填与 live 不混写、已冻结预测不被覆盖、重试幂等；完整 recommendation、daily pipeline、prediction snapshot、prediction time、Outcome 测试。

### A07 — P2：前端页面与数据状态集中在大组件，构建只有一个主要 JS 包

**证据：** `frontend/src/App.tsx:1148–1567` 的 StockDetail 为 420 行，在一个组件管理事件、评级、新闻、组合行情和两种分时请求及其状态。`AgentChatDock.tsx:99–603` 为 505 行，混合会话管理、localStorage、流式消息、计时与全部 UI。`MarketKLineChart.tsx:111–353` 的 effect 为 243 行，混合图表配置、series、数据、事件与释放。`ReviewDashboard.tsx` 的多个面板也有独立数据生命周期。

`App.tsx` 静态导入复盘、聊天和图表组件；最终构建主 JS 为 662.58 kB（gzip 207.90 kB），触发 >500 kB warning。这个事实证明集中打包，不是浏览器首屏性能实测。

**建议：** StockDetail 提取事件/新闻/行情 hooks 和图表模式展示；AgentChatDock 提取 session 管理、stream controller、消息列表与编辑器；图表提取配置/series 构建，保留唯一 create/remove 生命周期。路由页面再按实际入口使用 lazy import，保留现有路由 URL 和页面布局。不要为了行数再创建泛化的“万能请求 hook”。

**验收：** 股票快速切换不显示旧响应、日期变化更新数据、三图切换与卸载清理、会话切换/重命名/删除、SSE 成功失败；现有 helper 测试/构建之外补充组件行为回归。

### A08 — P2：models.py 除 110 个模型外还混入约 600 行 Agent 展示派生逻辑

**证据：** `backend/app/models.py` 共 2,496 行、110 个类；`:1703–2318` 包含 outcome 推断、股票名称抽取、tool policy audit、证据卡、展示标题与指标、建议追问等函数。`AgentChatResponse.populate_agent_ui_fields` 在 `:1635` 自动调用相关逻辑。

**影响：** 模型初始化同时承担展示规则；修改界面证据文字会进入后端共享模型的依赖面。不能把这 2,496 行全部当作必须拆分的纯 schema，也不能把 validator 当作未引用函数删除。

**建议：** 优先拆出展示/证据派生逻辑，并显式保留 response 构建时的派生行为；再按 market、rating/prediction、chat/observability 分组模型，通过稳定导出兼容现有导入。先解决依赖方向，避免模型与 presentation 互相导入。

**验收：** 相同输入的 model_dump、stock_mentions、evidence_cards、tool_policy_audit、suggested_questions、旧记录读取保持一致；运行 observability、session、tool outcome、模型使用方测试与前端构建。

### A09 — P2：评估报告版本仍硬编码 v2，实际评估的是 v5

**证据：** `backend/app/agents/query_contract_eval.py:99` 输出 `"version": "limit-up-query-v2"`；实际 `build_limit_up_query_contract(...).to_dict()["version"]` 是 `limit-up-query-v5`。本轮 core/product 日志和隔离复现均出现该不一致。

**影响：** 评测产物无法可靠说明验证了哪个契约版本，容易与 `badCase.md` 的旧进程问题混淆。这是报告口径错误，并非 36 项评测本身没有执行。

**建议：** 从唯一契约版本常量或实际 suite 结果生成报告版本；历史 fixture 名可保留。增加报告版本与实际结果版本一致的断言，空 suite 也使用明确的当前契约版本。

**验收：** core/product 的 query_contract.version 与实际结果一致；固定版本断言、空 suite 和现有 Query Contract 测试。

### A10 — P2：前端测试仅覆盖 3 个 helper 模块，未覆盖已记录的股票链接 Bad Case

**证据：** `frontend/package.json:10` 的测试命令只运行 `tests/*.test.ts`；当前 3 文件共 9 项，只验证 consolidation、intradayChart、relayRanking。`backend/tests/test_post_limit_research.py:134` 的链接测试只放 1 个候选并验证 stock_mentions，没有验证超出摘要上限的尾部股票，也未执行 `AgentAnswerMarkdown.tsx:24` 的实际链接渲染。

**影响：** BC-002 所要求的“多股票、尾部候选、点击进入正确详情”仍没有组件/浏览器自动回归。前端 9 项全通过不能用来证明大组件拆分、CSS 清理或 Markdown 链接没有回归。

**建议：** 重构前补充基于真实输出 shape 的多候选及重名/名称包含关系用例，验证 Markdown 文本、表格、已有链接/代码块不误改，尾部名称仍有正确站内链接；再添加组件路由点击验证。针对 A07 补充必要状态/异步交互测试，不镜像实现细节。

**验收：** 多候选输入下逐个检查站内 href 的 symbol/date/name，覆盖尾部、同名/包含关系、已有链接/代码块；测试命令纳入组件行为验证。

## 3. 可保留、低收益重复与待确认事项

| 对象 | 判断、原因与后续触发条件 |
| --- | --- |
| database.py `_apply_schema` 608 行 | 主要是有序 DDL；入口 `initialize_database:121` 已有版本判断、串行写事务和回滚。不是 608 行复杂业务分支。下次增加 schema migration 时按版本/表域组织，保持迁移顺序，禁止为了行数机械重排。 |
| first_board.py 816 行 | 评分已拆成多个短因子函数，最大函数 60 行左右。整体文件大但可回放边界相对清楚；暂不优先拆，新增独立策略族或出现因子耦合时再拆。 |
| first_board_repository.py 1,267 行 | 一类仓库管理 features/bars/outcomes/predictions/enrichment；预测落库大函数是高风险检查点，但并非死代码。下一次仓库变更可先拆 bar 与 snapshot 职责，保留兼容门面；不动历史快照数据。 |
| scoring_policy_optimizer.py 855 行、factor_signal_diagnostic.py 913 行 | 包含 walk-forward、统计、LASSO 等独立数值过程，大部分已函数化。`_ranks` 与 `_average_ranks` 算法相似但输入类型不同；调用方的 Spearman 计算在最小样本及退化返回语义上也不同，不直接合并。下一次统计口径修改时先固定差异测试。 |
| review_agent.py 1,136 行 | 规划、工具、复盘指标、模板共存但已有细分函数；暂排在主 chat 链路之后。改 Review 契约时拆 deterministic statistics 与报告生成，保留实际消费者。 |
| eval_runner.py 1,829 行、run_agent_eval.py main 265 行 | eval_runner 混合 fixture/result 类型、运行器和指标报告，宜在扩展评测维度时按 suite 拆分；`_check_product_turn` 200 行中相似检查对应不同评测维度，不能简单删除。 |
| 大测试文件 | test_agent_chat 1,891 行、test_agent_external_tools 1,231 行；长度主要来自多个独立用例及 fixture。后续随领域拆分测试类和共用数据工厂，保留所有断言，不以减少行数缩减覆盖。 |
| 两个 Repository 方法 | `first_board_repository.py:745` 的 list_feature_trade_dates_before、`recommendation_intelligence_repository.py:223` 的 list_changes 全仓仅有定义。列为待确认接口：当前无内部消费者，但清理前核对离线外部脚本/历史使用约定；尤其不能顺带删除 changes 审计表和写入。 |
| 日期解析精确重复 | helpers.py:117 的 `_parse_optional_date` 与 tool_policy.py:2215 的 `_parse_date` 函数体相同。随 A01 抽到契约基础层，不能让 handler/Policy 互相导入形成环。低收益，不单独扩大重构。 |
| 进程检查精确重复 | run_recommendation_refresh_loop.py:206 与 start_dev_detached.py:166 体相同；两脚本运行/部署入口不同。暂保留，下一次统一进程/锁工具时处理，不为 8–10 行引入跨部署路径耦合。 |
| 测试事件工厂精确重复 | test_evaluation_agent.py:34 与 test_rating_backtest.py:30 的 `_event` 各 29 行相同。后续两个 suite 数据口径一起调整时提取 fixture；当前不是错误，暂不独立重构。 |
| 日 K 线字段映射近似重复 | sync_first_board_history.py:190 和 update_daily_data.py:787 各自构造 StockDailyBar，另有 update_daily_data.py:902 附近映射。窗口目的不同，应保留，但字段/source 归一化可合并。都出现 `amount=bar.volume`，当前未做上游单位核验，也未证明下游数值错误；列为待核实数据语义，下一次 K 线管线变更前必须厘清，不能擅自用默认值替代。 |
| Docker Compose 公共服务属性/健康检查 | 多服务共享镜像、卷和环境，存在声明重复，但容器启动命令及健康策略不同。可在下次部署配置修改时使用扩展块；当前不因为重复几行就改部署基线。 |
| 兼容字段、历史读取、评分审计 | RecommendationIntelligenceItem 的 legacy validator、retire 脚本和历史推荐读取测试均有保留目的。未根据“旧/legacy”命名删除；只有历史迁移退出方案明确时再清理。 |

### 重复块候选处置

连续 12 有效行扫描产生 10 组跨文件对，重叠窗口按文件对归并，不能当成 10 个独立 bug：

| 文件对（起始行示例） | 结论 |
| --- | --- |
| test_evaluation_agent:26 / test_rating_backtest:22 | 上述共用 fixture 候选；29 个重叠窗口 |
| test_agent_v1_profile:373 / test_tool_policy:513 | 两个 profile/Policy 场景使用同样 mock 数据，断言目的不同；保留 |
| database:895 / test_first_board_features:112 | 旧 schema 与迁移测试输入相同是预期；保留 |
| eval_runner:680 / run_agent_question_bank:392 | 会话历史消息构造重复，可在下一次评测运行器拆分时提取；暂保留 |
| chat:2462 / tool_policy:999 | 旧参数投影重复；纳入 A01 |
| sync_first_board_history:201 / update_daily_data:808 | bar 字段映射重复，见单位/来源待核实项 |
| test_evaluation_agent:26 / test_factor_signal_diagnostic:154 | fixture 片段，后续可共用工厂 |
| test_factor_signal_diagnostic:154 / test_rating_backtest:22 | 同上，非生产代码重复 |
| test_scoring_error_diagnostic:41 / test_scoring_policy_optimizer:216 | feature 测试构造片段，保留独立验证意图 |
| tool_execution/ratings:28 / tool_execution/stocks:209 | 缺失交易日错误响应相似；随 A01 统一错误结构，保留各工具实际名称与错误语义 |

## 4. 验证结果与 Bad Case 覆盖

运行命令：`backend/.venv/Scripts/python.exe scripts/check_project.py --scope all`。使用 Python 3.13.14、pytest 9.1.1、Node 24.13.0，项目已有依赖；未安装/升级依赖。

| 验证项 | 宿主隔离验证结果 |
| --- | --- |
| 后端 pytest | **568 passed，21 subtests passed；0 failed、0 error、0 skipped**，pytest 汇总耗时 7.57 秒 |
| core 离线 Agent | **18/18**，unstable 0；确定性模板模式，LLM 调用 0 |
| product 离线 Agent | **10/10 场景、30/30 轮**；确定性模板模式，LLM 调用 0 |
| Query Contract | core 和 product 各执行同一 **36/36** 用例；不是 72 个独立用例；报告版本错误见 A09 |
| 前端 | **9/9**，失败/跳过均 0；当前基线重跑仍通过 |
| 生产构建 | TypeScript 和 Vite 成功；当前基线重跑仍通过。主 JS 662.58 kB，CSS 67.69 kB；1 类主 chunk 大小 warning |
| 静态语法 | Python 205 文件、TS/TSX 19 文件解析无错误；JSON fixtures/config、CSS 和两个 PowerShell 脚本解析无错误 |
| A01/A09 隔离复现 | 已复现现有实现不一致；这是额外诊断结果，不计入上面通过测试的数量 |

**环境错误如实记录：** 首次沙箱验证在 pytest 临时目录访问时触发 WinError 5，并在结束清理阶段中断，未产生可信的测试最终汇总；不能将其进度字符换算成通过/失败用例数量。该次 frontend-build 因 esbuild 子进程 `spawn EPERM` 阻塞；core、product、frontend-test 三个 gate 已通过。随后宿主环境使用新隔离输出目录重跑全部五个 gate 并通过，不将第一次两个失败 gate 描述为业务测试失败或静默抹除。

本地原始验证记录（均忽略提交）：

- 初次受阻：`output/validation/20260909T113849Z-bbf1a495/`。
- 完整重跑：`output/validation/20260909T114034Z-a4cd22a3/`，含 summary、pytest XML 和各 gate 日志。
- 最终样式提交的前端重跑：`output/validation/20260909T114750Z-ced97f0d/`。
- 扫描与复现：`output/code-audit-20260909/`。

### Bad Case 对照

| 记录 | 已覆盖证据 | 本轮仍有的验证边界 |
| --- | --- | --- |
| BC-001 回撤名单误走统计 | test_post_limit_research.py:110，契约从 statistics 校正到 high_drawdown screen | 原始问法契约已覆盖；未做浏览器与真实当前候选集合对照 |
| BC-002 股票名不能点击 | test_post_limit_research.py:134，trace→stock_mentions | 当前只 1 个样本，缺尾部/真实点击测试；A10 |
| BC-003 近期热门板块统计 | test_query_contract.py:75、test_agent_chat.py:1454，跨日去重/题材统计 | 普通/降级入口覆盖，补救入口存在 A01 |
| BC-004 智慧农业分类 | test_agent_chat.py:1491，错误 industry 与真实 concept 分离 | 未核对真实上游数据最新分类；这里只验证提供事实的使用方式 |
| BC-005 线上旧进程 | 现有部署/启动脚本及离线测试已检查相关入口 | 本轮不重启或访问线上；不能声称线上版本已验收；A09 会误导版本审计 |
| BC-006 农业名单误判空结果 | test_query_contract.py:106、test_agent_chat.py:1511，7 日、query 清理、去重 | 实时模型未测；Policy 补救遗漏同 A01 |
| BC-007 晋级开盘追问误判时间 | test_agent_chat.py:993、:1140、:1150，连续上下文、开盘高低及缺失 K 线 | 已使用隔离 SQLite 与确定性/模拟模型；未做真实模型/SSE 页面验收 |

本轮未修复 Agent 问答，因此没有向 `badCase.md` 写入“已修复”记录。A01 实施修复时必须补记并将上述补救路径加入回归。

## 5. 整改批次与完成条件

| 顺序 | 内容 | 风险与后续触发条件 |
| --- | --- | --- |
| 1 | A01 契约执行一致性；A09 报告版本 | 下一次 Agent 契约/工具变更或发布前优先处理。先锁定复现，再做最小统一；真实修复后进行业务 HTTP 验收 |
| 2 | A03 无消费者前端代码；A10 最小行为回归；A02 旧样式 | 下一次前端清理时实施；先建立链接/关键页面基线，再按族删除，保护新配色提交 |
| 3 | A04 Agent 阶段/模板、A05 路由生命周期、A08 模型展示逻辑 | 新增 Agent 能力前处理；一次只迁移一个边界，保持公共 schema/trace 与导入兼容 |
| 4 | A06 推荐与日更阶段、A07 大组件 | 下一次对应主流程迭代时处理；先纯函数后 I/O、先数据状态后展示；不混入快照迁移或筛选口径变化 |
| 延后 | SQL 声明、数值算法、测试工厂、低收益小重复 | 当前没有足够收益支持独立重构。按第 3 节触发条件复查；不能因暂缓而当作已解决 |

每个实际整改批次需单独提交，只纳入对应改动；检查完整 diff 与 `git diff --check`，按受影响链路运行完整测试。涉及评分/快照/管线时保留不可变预测、数据来源、缺失字段、截止时间与 live/backtest 区分。此报告不授权顺带更改这些行为。

## 6. 第一轮优化复核（2026-09-09）

授权：用户要求“先进行一次优化”。基于报告首批顺序处理 A01/A09，未扩大到 CSS 删除、大文件整体拆分或数据管线调整。代码、测试与 BC-008 提交：`0c3f6459a8add6d7282b9c73515d3d0e44d16589`（`fix(agent): share limit-up query execution across repair paths`）。本节是修改 Tool Policy 后的阶段性复核，不将初次全量扫描数字伪装成修改后的重新全扫结果。

### 状态与实现边界

| 项目 | 最新状态 | 证据与收益 |
| --- | --- | --- |
| A01 / P1 | 已修复，提交 `0c3f645` | `limit_up_execution.py:9` 统一契约参数、工具执行、trace 契约及完整 Facts；`tool_execution/stocks.py:232`、`tool_policy.py:999`、`chat.py:2462` 三入口复用，修复补救/旧降级的窗口、分组和证据遗漏 |
| A09 / P2 | 已修复，提交 `0c3f645` | `query_contract_eval.py:99` 读取 `QUERY_CONTRACT_VERSION`；非空和空评估套件均验证报告版本等于真实契约版本，当前为 v5 |
| A02–A08、A10 / P2 | 未修复，保留原整改批次 | 共 8 项；本轮优先处理已复现的执行一致性，后续按第 5 节触发条件处理 |
| R1-01 / P1 | 新发现、待修复 | 自然语言日期前缀混入题材关键词，详细复现及触发条件见下文 |

上述代码路径均相对于 `backend/app/agents/`。共享模块只依赖契约、工具与模型，不反向导入 Policy 或 dispatcher；删除 helpers 内已迁移的 `_event_fact`，全仓调用检索只剩共享模块内部消费者。Policy 的补救条件、profile 限制、错误 trace 与 repair 标记仍由 Policy 管理；普通入口日期缺失判断、执行顺序和引用保持原有职责。旧降级入口遇到跨日/分组契约时复用现有 Facts 回答模板，单日回答继续沿用原模板。

6 个应用文件合计新增 83 行、删除 103 行，净减少 **20 行**；三处手写参数/证据逻辑收敛为一个 57 行模块，而非简单搬移大文件。新增测试 116 行、BC-008 记录 25 行不计入业务代码缩减。Chat Agent 版本递增为 `first-board-chat-policy-v16-shared-limit-up-execution`；Query Contract 解析语义和 v5 版本未改，公共 API schema、数据库结构、评分/快照和采集行为未改。

### 验证结果与范围

| 检查 | 最终结果 | 证据/范围 |
| --- | --- | --- |
| 新增定向测试 | 10 passed；0 failed/error/skipped | `backend/tests/test_limit_up_execution.py`：6 个直接执行/公开补救一致性参数用例、旧降级、异常/禁用边界、2 个版本报告用例 |
| 全部后端 pytest | 578 passed、21 subtests passed；0 failed/error/skipped | `backend/.venv/Scripts/python.exe scripts/check_project.py --scope backend`，pytest 6.18 秒，使用门禁隔离数据库 |
| core 离线 Agent 评估 | 18/18 passed | 确定性模板，未调用真实 LLM |
| product 离线评估 | 10/10 场景、30/30 轮 passed | 无失败场景或轮次 |
| Query Contract | 36/36 passed | core/product 报告均显示 v5 |
| 真实回环 HTTP 验收 | 4/4 HTTP 200，内容断言通过 | 独立 Uvicorn、临时端口、隔离 SQLite，2 次普通 `/api/agents/chat`、2 次注入空执行后经 Policy 的 `/api/agents/chat/stream` |
| 前端测试/构建 | 本轮未运行 | 无前端变更；初次审查的 9 项测试及构建结果只作为历史结果，不算本轮验证 |
| 生产/付费模型/浏览器验收 | 未覆盖 | 未重启生产服务、未操作生产数据、未调用付费模型；隔离合成数据 HTTP 验证不等于线上验收 |

本地未提交证据：`output/validation/20260909T121502Z-95c07e25/{summary.json,pytest.log,eval-core.log,eval-product.log}`；`output/optimization-round1/http-results.json` 及同目录 `http_acceptance.py`。HTTP 两个固定问题与 BC-008 一致，合成数据含 2 只股票、3 条跨日事件；普通和补救路径均断言实际窗口=契约窗口=7、股票数=2、事件数=3，板块问法有 concept 分组和题材汇总，响应版本为 v16。补救场景仅替换 Planner/前置工具执行为离线 fixture，真实 Policy、registry、数据库和 HTTP/SSE 序列化仍执行，验收服务完成后关闭。

测试过程也保留失败信息：旧代码首次运行新用例为 9 failed/1 passed；统一执行后曾为 1 failed/9 passed，剩余失败暴露 R1-01。为明确本轮只验证执行投影，日期用例改为同一问题加结构化 `request.trade_date`，随后 10 项通过；**没有宣称自然语言日期前缀已修复**。新增测试覆盖显式 10 日、结构化日期、空结果、同股跨日重复、多标签和行业聚合，错误工具及禁用工具均保留既有边界。

### R1-01 — P1：日期前缀导致题材查询错误空结果（待修复）

**证据：** `backend/app/agents/query_contract.py:647` 的 `extract_topic_query` 未先移除日期，`:653` 的正则允许数字进入题材词；`:677` 的 `_clean_topic_candidate` 仅从开头清理时间修饰词。对原话 `2026-09-08 近期农业板块涨停过的股票有哪些` 只读调用 `build_limit_up_query_contract`，输出 `trade_date=2026-09-08`、`recent_trade_days=7`，但 `query=08近期农业`。有农业数据的隔离用例因此返回 0，直接执行与补救都受影响。解析文件本轮没有修改，是既有问题。

**风险与暂缓原因：** 日期成功提取却污染了题材关键词，可能把有数据误报为无结果。该问题属于独立的自然语言契约解析边界；本轮先完成已复现的执行投影统一，避免将解析规则变更与路径收敛混在同一修复。风险仍然存在，未以回归全绿关闭。

**后续触发与建议：** 下一轮优先修复，且应在下一次 Agent 发布或 Query Contract 变更前处理。先将上述完整原话加入契约和答案回归，再在题材提取前识别/屏蔽完整日期片段，保护包含数字的真实题材名称，不采用任意删除数字。核对日期前置/后置、中文日期、显式窗口、数字题材及 Planner 参数优先级；复验直接执行、Policy 补救和 HTTP 原话，并按语义变更递增契约版本。

代码提交前已检查所有 8 个文件的完整 diff 和 `git diff --check`/暂存 diff 检查；只提交应用代码、测试和 BC-008。本记录单独提交；本地数据库、生成报告及无关文件均未纳入。

下列附录是初次审查基线的覆盖与规模证据；“基础完成”表示该文件已进入扫描和结构检查，并非没有任何潜在问题。

## 7. 专项审查：Agent 提示词体积与职责边界（2026-09-09）

审查基线：`232c627`。本节仅审查并记录问题，未修改提示词、Planner 输出契约或运行逻辑。审查覆盖 `backend/app/agents/chat_prompts.py`、`capability_contract.py`、`tools.py`、`chat.py`、`session_memory.py`、`llm_provider.py`，以及 2026-09-09 当天本地 `agent_usage_events` 中有 Planner 调用的 29 条运行记录。

### 量化结果

当前运行 profile 为 `v1_close_review`，Planner 每次可见 24 个工具和 23 个 Capability。以下字符数由生产构建函数直接生成；字符数是 Python `len(str)`，不是 token 数。

| 组成 | 字符数 | 占 Planner system 比例 | 说明 |
| --- | ---: | ---: | --- |
| Planner system prompt | 21,842 | 100.0% | 原生 function-call 模式 |
| 固定规则（移除两个目录后的剩余部分） | 10,015 | 45.9% | 角色、安全、上下文、逐领域路由规则 |
| 工具目录 | 8,364 | 38.3% | 24 个工具的描述、参数类型、枚举和必填项 |
| Capability 目录 | 3,463 | 15.9% | 23 个能力的描述、示例和所需证据 |
| 原生函数调用契约 | 2,308 | — | API payload 中另行发送，不属于 system message |
| Planner JSON 降级 system prompt | 22,032 | — | 比原生模式多 190 字符的输出 schema 说明 |
| Answer 基础 system prompt | 4,788 | — | 未计入按请求追加的 Capability 回答约束 |
| 全部 Capability 回答约束 | 1,343 | — | 实际只追加当前请求涉及的部分 |

工具目录中最大的单项为 `limit_up_events` 1,170 字符、`post_limit_screen` 960 字符、`post_limit_statistics` 735 字符。它们既在目录中描述参数和用途，又在固定 Planner 规则及 Capability 目录中重复描述选择边界。

真实运行记录显示，固定模板之外的动态上下文也不可忽略：

| 指标 | 样本数 | 最小 | 中位数 | P90 | 最大 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Planner 总输入字符 | 29 | 24,616 | 26,619 | 27,947 | 27,963 |
| Answer 总输入字符（仅发生 Answer LLM 调用） | 18 | 5,381 | 11,062 | 17,801 | 20,373 |
| API 返回 prompt tokens | 25 | 7,348 | 8,557 | 13,643 | 14,794 |

`prompt_tokens` 是一次业务运行内所有 LLM 调用的合计，不能当作 system prompt 的精确 token 数；4 条 token usage 不完整的运行未纳入该行。29 条运行中有 11 条只调用 Planner，随后由确定性模板回答。Planner 总输入已包含 system prompt、动态 user JSON 和 2,308 字符的原生函数契约。

### QP-01 — P1：Capability 与工具调用是重复的规划输出，四处路由描述形成冲突面

**证据：** `chat_prompts.py:97–148` 以自然语言逐领域指定 Capability/工具；同一请求边界又出现在 `capability_contract.py` 的 Capability 描述、`tools.py` 的工具 schema，以及 `_planner_function_parameters` 的 Capability/工具枚举。Planner 被要求同时输出 `capabilities` 和 `tool_calls`，但 `ensure_capability_tool_calls` 已能根据 Capability 确定性补齐所需工具和默认参数，Query Contract 也会再次以用户原文覆盖 Planner 参数。

近期 `3d8bfd4`、`16b0dee`、`3709f63`、`85d0ceb` 分别围绕板块涨停、晋级日开盘、多日股票集合和错误日程提示补规则或补救，说明当前可靠性依赖“Prompt 提示 + Planner 结果 + 后端修正”三方保持一致。新增一句 Prompt 能缓解已知问法，但继续扩大重复规则和冲突面。

**影响：** 每次普通问题都支付完整工具目录和重复枚举成本；模型可能给出 Capability 正确但 tool call 错误、或参数与 Query Contract 冲突的计划，后端再修复，trace 难以区分模型决策与确定性契约。该问题同时影响成本、延迟、可维护性和问答稳定性，列为 P1。

**建议：** 将 Planner 收敛为 Capability-first 契约，只输出 Capability、上下文模式、安全类型和少量无法由用户原文确定的语义槽位。Capability 到工具及默认参数完全由 `CapabilityToolRequirement` 映射，日期、窗口、板数、行业查询、排序和数量继续由 Query Contract 从用户原文编译。移除 Planner system 中的完整工具目录和 LLM 输出中的原始 `tool_calls`；后端执行 trace 仍记录最终工具与参数。不要用新增一套散落正则代替 Planner，而要复用现有 Query Contract 和 Capability Contract。

**目标与验收：** Planner system 不超过 10,000 字符，system 加原生函数契约的固定部分不超过 12,000 字符；Capability 契约、最终工具、Query Contract 和 trace 可回放一致。运行完整 Capability/Query Contract/Tool Policy/Agent Chat 测试，使用 `badCase.md` 全部真实问法做回归，并对多意图、同义改写及两轮指代执行真实 HTTP 验收。

### QP-02 — P2：Planner 固定规则混入大量 Answer 展示职责

**证据：** Planner 规则不仅决定证据能力，还要求热门股回答注明采集时间、新闻逐条保留来源和 URL、市场综述使用哪些展示维度、禁止某些回答措辞等；相同要求已经存在于 `_tool_answer_system_prompt` 和 Capability 的 `answer_guidance`。Planner 本应只产出计划，这些展示约束不会改善工具参数的确定性。

**影响：** Answer 格式或披露口径变化时需同步修改 Planner、Answer prompt、Capability guidance 和确定性模板。展示规则进入 Planner 还会稀释真正重要的能力区分与上下文继承规则。

**建议：** Planner 仅保留会改变 Capability 选择的对比规则，例如“板块近期涨停统计”与“全市场板块行情”的区别。来源披露、表格列、完整名单、免责声明和指标解释移到按请求加载的 `answer_guidance` 或确定性 renderer；安全边界保留在 Planner 与 Answer 的短公共核心中。

### QP-03 — P2：原始会话历史和 Session Memory 在 Planner 与 Answer 中重复发送

**证据：** `_tool_planner_user_prompt` 与 `_tool_answer_user_prompt` 都包含 `conversation_history` 和 `session_memory`。历史最多取 8 条、每条 400 字符；Planner 另有结构化 `recent_context`，Answer 已获得解析后的问题、计划和工具事实。真实 Answer 总输入中位数 11,062 字符、最大 20,373 字符，说明动态事实与重复上下文在部分回答中已经超过 Answer 基础 system prompt。

**影响：** 同一轮对话文本被发送两次；旧助手回答虽被标记为“不可信且不可复用证据”，仍占用上下文并可能干扰最终事实总结。长工具事实与历史叠加时更容易触发模型上下文压力。

**建议：** Planner 保留结构化 Session Memory、最近必要用户话语和 `recent_context`；Answer 默认只接收当前用户问题、已解析的实体/日期/上下文来源和工具事实。只有确需保留用户表达约束的 Capability 才附加压缩后的约束，不再发送旧助手回答全文。必须用“一进二名单 → 这些票开盘如何”等多轮 Bad Case 证明指代没有退化。

### QP-04 — P2：缺少提示词预算与组成级可观测性，增长只能事后发现

**证据：** 当前测试只断言 `planner_prompt_chars`、`answer_prompt_chars` 大于零，或检查目录包含/排除某工具；没有 system、工具目录、Capability 目录、函数契约和动态上下文的独立预算。运行表只保存 Planner/Answer 合计字符数，无法直接判断增长来自固定规则、schema、历史还是工具 facts。

**影响：** 每次为 Bad Case 增加规则都可能静默推高全量请求成本；只有查看数据库总量才能发现变化，也无法为 PR 提供明确回归门槛。

**建议：** 增加纯构建测试和分项指标：`planner_system_chars`、`tool_catalog_chars`、`capability_catalog_chars`、`function_contract_chars`、`conversation_context_chars`、`answer_facts_chars`。为固定部分设置上限，动态部分设置截断和告警而非简单测试失败；在使用量聚合中观察 P50/P90，并在重构前后比较首 token 延迟、总耗时和 token 用量。

### 建议实施顺序与当前状态

| 顺序 | 内容 | 风险 | 状态 |
| --- | --- | --- | --- |
| 1 | 先增加分项字符指标、预算测试和重构前基线 | 低 | 已完成：`a8f2dba` |
| 2 | 从 Planner 移走纯 Answer 展示规则，保持现有输出契约 | 中 | 已完成：`a8f2dba` |
| 3 | 将 Planner 改为 Capability-first，由契约确定性映射工具和参数 | 高 | 已完成：`a8f2dba` |
| 4 | 精简 Planner/Answer 的重复历史上下文并做多轮真实回归 | 中 | 已完成：`a8f2dba` |

本专项未发现 P0。建议先完成第 1 步，再以第 2、3 步为一个可回滚的核心变更批次；不能只删除文字后以现有单问测试通过作为完成依据。目标是减少重复决策面，而不是单纯追求更短的 Prompt。

### 本次验证

- 提示词构建实测：上述字符数均由当前生产函数和 `v1_close_review` profile 直接生成。
- 运行记录：只读统计本地 2026-09-09 当天 29 条有 Planner 输入的使用记录；未修改数据库。
- 定向测试：`test_agent_capability_contract.py`、`test_agent_v1_profile.py`、`test_agent_chat.py` 共 **81 passed、3 subtests passed、1 warning**。warning 为 pytest 无法写入仓库根目录 `.pytest_cache`，不是测试跳过或业务失败。
- 未运行付费 Planner 评估、完整后端 pytest、前端测试或构建；本次只新增审查文档，不改变运行逻辑。

### 实施结果（2026-09-09）

提交 `a8f2dba` 完成 Capability-first Planner 重构。原生函数契约不再允许 LLM 输出 `tool_calls`，Capability 目录也不再向 Planner 暴露 `required_evidence`；服务端继续通过 `CapabilityToolRequirement` 注入工具，并从用户原话确定性编译日期、窗口、板数、板块、排序和数量。为保持兼容，JSON 降级解析仍可接收旧 Provider 的额外工具调用，但生产原生契约只要求 Capability、上下文模式和安全类型。Chat Agent 版本递增为 `first-board-chat-policy-v17-capability-first-planner`，Planner 契约为 `capability-first-v2`。

QP-01、QP-02、QP-03 已关闭。QP-04 的固定输入预算与 Planner trace 分项已落地：`planner_system_chars`、`capability_catalog_chars`、`embedded_tool_catalog_chars`、`function_contract_chars`、`fixed_input_chars` 和 `planner_user_chars` 均可检查；动态 Answer facts 的 P50/P90 聚合仍作为后续可观测性增强，不影响本次正确性关闭。

重构后的固定输入实测如下：

| 组成 | 重构前 | 重构后 | 变化 |
| --- | ---: | ---: | ---: |
| Planner system prompt | 21,842 | 5,627 | -74.2% |
| 原生函数调用契约 | 2,308 | 1,584 | -31.4% |
| system + 原生契约固定输入 | 24,150 | 7,211 | -70.1% |
| Planner 内嵌完整工具目录 | 8,364 | 0 | -100% |

验证结果：

- 定向 Agent 回归：73 passed，无失败、跳过或 warning。
- 完整后端：580 passed、21 subtests passed，无失败或跳过。沙箱内首次从 backend 根目录运行时，被遗留无权限目录阻断收集并产生 9 个 collection error；随后限定正式 tests 仍被 Windows `tmp_path` ACL 阻断。最终在沙箱外使用同一工作区和解释器完成上述完整通过结果，不能把前两次环境错误记为业务测试通过。
- 前端：9 passed；生产构建成功。Vite 仍报告既有的单 chunk 超过 500 kB 警告，本次未修改前端产物边界。
- 真实 HTTP：重启 8001 后，原始两轮“一进二名单 → 这些票晋级日高开还是低开”均使用 `daily_board_promotion(days=2)`，第二轮保留 Markdown 表格；“近期农业板块涨停过的股票有哪些”“近期哪些板块涨停的股票比较多”“近期涨停后回撤比较多的股票有哪些”分别命中 `limit_up_pool`、`limit_up_pool`、`post_limit_screening`。响应版本、最终工具和确定性参数均与 trace 一致。

## 附录 A：全部超大文件

| 文件 | 行数 | 复核结论 |
| --- | ---: | --- |
| `frontend/src/styles.css` | 5025 | A02：先清残留，再按组件组织，保护级联 |
| `backend/app/agents/chat.py` | 3298 | A01/A04：执行统一，分离编排与领域 fallback |
| `backend/app/agents/tools.py` | 2726 | A01/A04：schema/registry 与领域查询分离 |
| `backend/app/models.py` | 2496 | A08：先分离展示派生，字段声明不机械切割 |
| `backend/app/agents/tool_policy.py` | 2255 | A01/A04：补救复用领域执行；规则声明保留 |
| `frontend/src/App.tsx` | 1967 | A07：页面/hooks/图表状态拆分 |
| `backend/tests/test_agent_chat.py` | 1891 | 按领域拆测试组织，保留回归断言 |
| `backend/app/agents/eval_runner.py` | 1829 | 后续按 suite/指标/报告拆，保留不同评测维度 |
| `backend/app/agents/chat_templates.py` | 1347 | A04：有序分派 + 领域 renderer |
| `backend/app/routers/agents.py` | 1314 | A05：共享生命周期，JSON/SSE 仅作传输适配 |
| `backend/app/services/recommendation_intelligence.py` | 1273 | A06：证据/评分/时点/排序/持久化分阶段 |
| `backend/app/repositories/first_board_repository.py` | 1267 | 下次变更拆 bar/snapshot 仓库，保持门面 |
| `backend/tests/test_agent_external_tools.py` | 1231 | 保留独立 mock 场景，后续按工具域拆测试 |
| `backend/app/agents/review_agent.py` | 1136 | 已有短函数；后续分离统计与报告生成 |
| `backend/app/database.py` | 1039 | DDL/迁移有序且事务化；不因行数机械重排 |
| `backend/scripts/update_daily_data.py` | 958 | A06：按五个数据阶段组织，保护快照与回填 |
| `frontend/src/types.ts` | 955 | A03：先清孤立类型，剩余按领域分组 |
| `backend/app/services/factor_signal_diagnostic.py` | 913 | 数值算法已函数化；后续按诊断/计算分组 |
| `frontend/src/components/ReviewDashboard.tsx` | 859 | A07：按有独立状态的面板拆分 |
| `backend/app/services/scoring_policy_optimizer.py` | 855 | 已分评价/比较/优化；下次核心修改再拆 |
| `backend/app/agents/first_board.py` | 816 | 多数为短评分函数；保持可回放边界，暂保留 |

## 附录 B：全部 ≥100 行函数/组件

下表列出全部 63 个节点，包括嵌套回调；处置是审查建议，不代表本轮进行了重构。

| 文件:起始行 / 名称 | 跨度 | 处置 |
| --- | ---: | --- |
| `backend/app/agents/chat_templates.py:64` / `_template_answer_from_tool_facts` | 729 | A04：领域 renderer，明确 facts 优先级 |
| `backend/app/database.py:151` / `_apply_schema` | 608 | DDL/兼容迁移；保留顺序及事务，版本变更时再组织 |
| `frontend/src/components/AgentChatDock.tsx:99` / `AgentChatDock` | 505 | A07：按数据生命周期与展示职责拆；回调保留统一 cleanup |
| `backend/app/agents/chat.py:577` / `_answer_with_llm_tool_agent` | 456 | A04：规划/执行/上下文/回答分阶段，保留统一契约 |
| `backend/app/services/recommendation_intelligence.py:93` / `refresh_recommendation_intelligence` | 439 | A06：纯计算先拆；冻结落库保持边界 |
| `frontend/src/App.tsx:1148` / `StockDetail` | 420 | A07：按数据生命周期与展示职责拆；回调保留统一 cleanup |
| `frontend/src/components/MarketKLineChart.tsx:49` / `MarketKLineChart` | 360 | A07：按数据生命周期与展示职责拆；回调保留统一 cleanup |
| `backend/scripts/update_daily_data.py:194` / `run_daily_update` | 272 | A06：采集/预测/回填/报告阶段化；外层锁与重试保留 |
| `backend/scripts/run_agent_eval.py:44` / `main` | 265 | 后续按运行器、fixture、指标报告拆；不同检查维度保留 |
| `backend/app/agents/chat.py:306` / `answer_first_board_chat` | 245 | A04：规划/执行/上下文/回答分阶段，保留统一契约 |
| `frontend/src/components/MarketKLineChart.tsx:111` / `<callback>` | 243 | A07：按数据生命周期与展示职责拆；回调保留统一 cleanup |
| `backend/scripts/run_daily_close_loop.py:184` / `execute_daily_close_loop` | 234 | A06：采集/预测/回填/报告阶段化；外层锁与重试保留 |
| `backend/scripts/update_daily_data.py:547` / `backfill_recent_daily_top_candidate_bars` | 222 | A06：采集/预测/回填/报告阶段化；外层锁与重试保留 |
| `backend/app/agents/tools.py:1776` / `limit_up_events` | 210 | A01/A04：抽领域查询与 facts 映射，registry 保留入口 |
| `backend/app/agents/eval_runner.py:1296` / `_check_product_turn` | 200 | 后续按运行器、fixture、指标报告拆；不同检查维度保留 |
| `backend/app/agents/tool_policy.py:431` / `_rules` | 197 | 规则/信号声明；与执行拆分，不机械按行拆 |
| `backend/scripts/run_agent_question_bank.py:249` / `main` | 194 | 后续按运行器、fixture、指标报告拆；不同检查维度保留 |
| `backend/app/agents/chat.py:1035` / `_generate_llm_query_plan` | 191 | A04：规划/执行/上下文/回答分阶段，保留统一契约 |
| `backend/app/agents/tool_policy.py:80` / `from_message` | 188 | 规则/信号声明；与执行拆分，不机械按行拆 |
| `backend/app/routers/agents.py:1030` / `stream_first_board_agent_chat` | 185 | A05：共享请求与落库生命周期 |
| `frontend/src/components/ReviewDashboard.tsx:351` / `HighScoreReviewPanel` | 176 | A07：按数据生命周期与展示职责拆；回调保留统一 cleanup |
| `frontend/src/components/ReviewDashboard.tsx:55` / `DragonTigerReviewPanel` | 173 | A07：按数据生命周期与展示职责拆；回调保留统一 cleanup |
| `backend/app/services/scoring_policy_optimizer.py:55` / `optimize_scoring_policy` | 170 | 数值/样本流程已有子函数；下一次统计口径改动时分阶段 |
| `frontend/src/App.tsx:155` / `App` | 170 | A07：按数据生命周期与展示职责拆；回调保留统一 cleanup |
| `backend/app/services/prediction_quality_audit.py:30` / `build_prediction_quality_audit` | 167 | 诊断报告编排；按覆盖/统计/结论拆，保留缺失数据语义 |
| `backend/app/agents/eval_runner.py:611` / `run_agent_product_eval_suite` | 158 | 后续按运行器、fixture、指标报告拆；不同检查维度保留 |
| `backend/app/services/data_health.py:19` / `build_agent_data_health` | 154 | 诊断报告编排；按覆盖/统计/结论拆，保留缺失数据语义 |
| `backend/tests/test_tool_policy.py:340` / `test_market_environment_repairs_all_required_evidence_groups` | 151 | 测试场景/样本；保留断言，后续提取领域 fixture |
| `frontend/src/App.tsx:1662` / `FirstBoardRatingDetail` | 148 | A07：按数据生命周期与展示职责拆；回调保留统一 cleanup |
| `backend/tests/test_agent_chat.py:993` / `test_promotion_opening_followup_reuses_previous_days_and_kline` | 146 | 测试场景/样本；保留断言，后续提取领域 fixture |
| `frontend/src/components/ReviewDashboard.tsx:531` / `DailyTopReview` | 144 | A07：按数据生命周期与展示职责拆；回调保留统一 cleanup |
| `backend/app/services/sector_performance.py:41` / `build_sector_performance` | 142 | 查询、缺失/回退与结果组装；下次数据源变更再拆 |
| `backend/app/services/scoring_error_diagnostic.py:39` / `build_scoring_error_diagnostic` | 141 | 诊断报告编排；按覆盖/统计/结论拆，保留缺失数据语义 |
| `backend/app/services/sector_stock_ranking.py:33` / `build_sector_stock_ranking` | 140 | 查询、缺失/回退与结果组装；下次数据源变更再拆 |
| `backend/app/repositories/first_board_repository.py:565` / `_insert_live_prediction_batch` | 138 | 保持不可变快照校验与写事务；下次仓库变更再分解 |
| `backend/app/routers/agents.py:893` / `chat_with_first_board_agent` | 134 | A05：共享请求与落库生命周期 |
| `backend/scripts/update_daily_data.py:823` / `backfill_recent_post_limit_bars` | 132 | A06：采集/预测/回填/报告阶段化；外层锁与重试保留 |
| `backend/app/agents/eval_runner.py:478` / `run_agent_conversation_planner_eval_suite` | 131 | 后续按运行器、fixture、指标报告拆；不同检查维度保留 |
| `backend/app/agents/eval_runner.py:907` / `_run_agent_planner_eval_case` | 128 | 后续按运行器、fixture、指标报告拆；不同检查维度保留 |
| `backend/app/services/factor_signal_diagnostic.py:358` / `build_date_blocked_lasso_summary` | 128 | 数值/样本流程已有子函数；下一次统计口径改动时分阶段 |
| `backend/app/services/outcome_completeness.py:19` / `build_top10_outcome_completeness` | 125 | 诊断报告编排；按覆盖/统计/结论拆，保留缺失数据语义 |
| `frontend/src/App.tsx:654` / `RecommendationNewsBoard` | 125 | A07：按数据生命周期与展示职责拆；回调保留统一 cleanup |
| `backend/app/agents/tools.py:1653` / `market_event_pool` | 122 | A01/A04：抽领域查询与 facts 映射，registry 保留入口 |
| `frontend/src/components/ReviewDashboard.tsx:229` / `DailyBoardPromotionPanel` | 121 | A07：按数据生命周期与展示职责拆；回调保留统一 cleanup |
| `backend/scripts/research_limit_up_paths.py:236` / `analyze` | 119 | 研究分析脚本；后续按输入/指标/报告拆，保持可复现 |
| `backend/app/services/analysis.py:189` / `calculate_daily_board_promotion` | 118 | 晋级分组统计；按样本准备/分组/结果拆，保持确定性口径 |
| `backend/app/agents/query_contract.py:225` / `build_limit_up_query_contract` | 117 | 契约集中编译有价值；先解决 A01 多入口参数分叉 |
| `backend/app/agents/chat_prompts.py:39` / `_tool_planner_system_prompt` | 112 | 以声明式提示词为主；模板增长时分领域组织 |
| `frontend/src/App.tsx:1035` / `StockTable` | 112 | A07：按数据生命周期与展示职责拆；回调保留统一 cleanup |
| `backend/app/routers/agents.py:1082` / `run_agent` | 111 | A05：共享请求与落库生命周期 |
| `backend/app/agents/tools.py:1467` / `stock_activity` | 110 | A01/A04：抽领域查询与 facts 映射，registry 保留入口 |
| `backend/app/services/scoring_policy_optimizer.py:227` / `evaluate_scoring_policy` | 110 | 数值/样本流程已有子函数；下一次统计口径改动时分阶段 |
| `backend/scripts/sync_first_board_history.py:24` / `main` | 110 | CLI 编排保留；共用 bar 映射前先核对来源单位 |
| `backend/app/agents/eval_runner.py:1544` / `_check_response` | 109 | 后续按运行器、fixture、指标报告拆；不同检查维度保留 |
| `backend/app/services/recommendation_intelligence.py:726` / `_persist_final_relay_snapshot` | 107 | A06：纯计算先拆；冻结落库保持边界 |
| `backend/scripts/validate_consolidation_evidence.py:84` / `analyze` | 107 | 研究分析脚本；后续按输入/指标/报告拆，保持可复现 |
| `backend/app/agents/chat.py:2069` / `_merge_context_from_run` | 103 | A04：规划/执行/上下文/回答分阶段，保留统一契约 |
| `backend/tests/test_review_agent.py:71` / `test_feature_comparison_describes_success_and_failure_groups` | 103 | 测试场景/样本；保留断言，后续提取领域 fixture |
| `backend/app/database.py:918` / `_ensure_agent_live_prediction_snapshots_schema` | 102 | DDL/兼容迁移；保留顺序及事务，版本变更时再组织 |
| `backend/app/services/first_board_features.py:85` / `build_first_board_outcome` | 102 | Outcome 推导；按窗口与标签阶段拆，保持 D+1/D+3/D+5 语义 |
| `backend/app/services/post_limit.py:153` / `build_post_limit_statistics` | 102 | 统计流程；保留既有 shape/契约共享，后续拆聚合阶段 |
| `backend/app/services/stock_position.py:185` / `_score_regimes` | 100 | 评分分支有明确域；规则调整时分函数，不机械分文件 |
| `backend/scripts/research_pullback_consolidation.py:121` / `analyze` | 100 | 研究分析脚本；后续按输入/指标/报告拆，保持可复现 |

## 附录 C：CSS 静态候选完整分组

所有起始行对应最终基线 styles.css。每个候选均完成静态类名搜索和动态类名排除；保留项不是死代码。无引用候选仍需在实际删除前完成 A02 的页面回归。

| 功能族 | 规则数 | 起始行（逐项） | 处置 |
| --- | ---: | --- | --- |
| 旧 Agent 证据与 trace 面板 | 50 | 671, 676, 682, 693, 698, 703, 708, 718, 725, 730, 737, 750, 756, 766, 775, 781, 789, 793, 797, 801, 805, 814, 821, 830, 837, 842, 851, 856, 861, 867, 873, 882, 887, 894, 904, 911, 918, 922, 929, 933, 941, 945, 952, 957, 963, 972, 977, 981, 987, 992 | 无静态消费者；按 A02 清理前做页面验证 |
| 动态类名：保留 | 6 | 1169, 1175, 2890, 2895, 2900, 4153 | 动态生成，保留 |
| 旧 Agent 评估面板 | 26 | 1310, 2369, 2380, 2385, 2396, 2402, 2413, 2418, 2422, 2427, 2433, 2442, 2448, 2453, 2458, 2463, 2469, 2478, 2483, 2490, 2497, 2506, 2511, 2517, 2529, 2539 | 无静态消费者；按 A02 清理前做页面验证 |
| 旧复盘结论面板 | 18 | 1382, 1389, 1397, 1402, 1410, 1415, 1433, 1440, 1447, 1451, 1456, 1461, 1467, 1471, 4848, 4852, 4857, 4863 | 无静态消费者；按 A02 清理前做页面验证 |
| 旧评分诊断面板 | 26 | 1527, 1533, 1541, 1545, 1550, 1556, 1561, 1570, 1574, 1580, 1587, 1594, 1600, 1616, 1625, 1629, 1636, 1646, 1650, 1660, 1666, 1671, 1681, 4872, 4878, 4884 | 无静态消费者；按 A02 清理前做页面验证 |
| 其余无静态引用 | 1 | 2063 | 无静态消费者；按 A02 清理前做页面验证 |
| 旧预测质量面板 | 61 | 2070, 2076, 2080, 2087, 2093, 2103, 2110, 2115, 2125, 2130, 2137, 2143, 2149, 2156, 2164, 2168, 2174, 2180, 2187, 2192, 2197, 2202, 2208, 2214, 2221, 2232, 2239, 2245, 2250, 2255, 2262, 2267, 2273, 2282, 2289, 2294, 2299, 2304, 2309, 2318, 2324, 2329, 2334, 2342, 2350, 2357, 2363, 4560, 4564, 4568, 4572, 4915, 4921, 4925, 4929, 4933, 4937, 4942, 4946, 4951, 4955 | 无静态消费者；按 A02 清理前做页面验证 |
| 旧首页指数与题材面板 | 16 | 3153, 3168, 3173, 3180, 3185, 3190, 4205, 4209, 4219, 4224, 4230, 4237, 4244, 4250, 4260, 4265 | 无静态消费者；按 A02 清理前做页面验证 |
| 旧低位证据面板 | 6 | 3979, 3985, 3992, 4000, 4012, 4020 | 无静态消费者；按 A02 清理前做页面验证 |

三个相同 selector/声明候选分别为 `.market-snapshot-indices article:first-child`（286、4655）、`article:last-child`（294、4659）、`.agent-chat-context`（4746、4965）。它们处于不同响应式上下文；不依据文本相同自动删除。

## 附录 D：完整覆盖清单

所有 256 文件基础扫描完成。F 为函数/方法/回调节点数，Max 为最大节点跨度；无相应 AST 统计项记为 —，不是解析失败。专项编号指向已确认问题或重点结构复核；“基础”不等于无缺陷。

| 文件 | 行数 | F / Max | 复核 |
| --- | ---: | --- | --- |
| `.dockerignore` | 18 | — | 基础 |
| `.env.production.example` | 62 | — | 基础 |
| `.gitattributes` | 13 | — | 基础 |
| `.github/workflows/validate.yml` | 81 | — | 离线门禁/部署入口 |
| `.gitignore` | 48 | — | 基础 |
| `backend/.env.example` | 72 | — | 基础 |
| `backend/Dockerfile` | 49 | — | 基础 |
| `backend/app/__init__.py` | 6 | — | 基础 |
| `backend/app/agent_output_sanitizer.py` | 131 | 5 / 20 | 基础 |
| `backend/app/agents/__init__.py` | 19 | — | 基础 |
| `backend/app/agents/answer_grounding.py` | 421 | 15 / 69 | 基础 |
| `backend/app/agents/capability_contract.py` | 457 | 7 / 45 | 基础 |
| `backend/app/agents/chat.py` | 3298 | 80 / 456 | A01/A04：执行统一，分离编排与领域 fallback |
| `backend/app/agents/chat_answer_validation.py` | 374 | 20 / 58 | 基础 |
| `backend/app/agents/chat_plan_normalization.py` | 198 | 6 / 41 | 基础 |
| `backend/app/agents/chat_prompts.py` | 385 | 5 / 112 | 基础 |
| `backend/app/agents/chat_templates.py` | 1347 | 17 / 729 | A04：有序分派 + 领域 renderer |
| `backend/app/agents/eval_runner.py` | 1829 | 38 / 200 | 后续按 suite/指标/报告拆，保留不同评测维度 |
| `backend/app/agents/explanation.py` | 138 | 6 / 28 | 基础 |
| `backend/app/agents/first_board.py` | 816 | 28 / 60 | 多数为短评分函数；保持可回放边界，暂保留 |
| `backend/app/agents/query_contract.py` | 730 | 33 / 117 | A01/A09 契约 |
| `backend/app/agents/query_contract_eval.py` | 112 | 4 / 37 | A09 |
| `backend/app/agents/review_agent.py` | 1136 | 43 / 93 | 已有短函数；后续分离统计与报告生成 |
| `backend/app/agents/tool_execution/__init__.py` | 93 | 1 / 39 | 基础 |
| `backend/app/agents/tool_execution/context.py` | 20 | — | 基础 |
| `backend/app/agents/tool_execution/helpers.py` | 424 | 24 / 61 | 基础 |
| `backend/app/agents/tool_execution/market.py` | 355 | 8 / 56 | A01 领域对照 |
| `backend/app/agents/tool_execution/post_limit.py` | 73 | 5 / 17 | 基础 |
| `backend/app/agents/tool_execution/ratings.py` | 126 | 3 / 42 | 基础 |
| `backend/app/agents/tool_execution/review.py` | 193 | 5 / 44 | 基础 |
| `backend/app/agents/tool_execution/stocks.py` | 313 | 7 / 72 | A01 |
| `backend/app/agents/tool_policy.py` | 2255 | 80 / 197 | A01/A04：补救复用领域执行；规则声明保留 |
| `backend/app/agents/tools.py` | 2726 | 42 / 210 | A01/A04：schema/registry 与领域查询分离 |
| `backend/app/collectors/__init__.py` | 89 | — | 基础 |
| `backend/app/collectors/akshare_limit_up_collector.py` | 184 | 7 / 51 | 基础 |
| `backend/app/collectors/first_board_enrichment_collector.py` | 305 | 13 / 40 | 基础 |
| `backend/app/collectors/hithink_finance_collector.py` | 756 | 28 / 61 | 基础 |
| `backend/app/collectors/limit_down_collector.py` | 79 | 4 / 25 | 基础 |
| `backend/app/collectors/market_index_collector.py` | 373 | 13 / 67 | 基础 |
| `backend/app/collectors/network.py` | 35 | 1 / 23 | 基础 |
| `backend/app/collectors/sector_collector.py` | 371 | 21 / 42 | 基础 |
| `backend/app/collectors/stock_kline_collector.py` | 351 | 14 / 43 | 基础 |
| `backend/app/collectors/trading_calendar_collector.py` | 31 | 2 / 13 | 基础 |
| `backend/app/config.py` | 241 | 17 / 25 | 基础 |
| `backend/app/consolidation_models.py` | 55 | — | 基础 |
| `backend/app/database.py` | 1039 | 20 / 608 | DDL/迁移有序且事务化；不因行数机械重排 |
| `backend/app/main.py` | 48 | 1 / 2 | 基础 |
| `backend/app/models.py` | 2496 | 21 / 96 | A08：先分离展示派生，字段声明不机械切割 |
| `backend/app/post_limit_query_contract.py` | 455 | 31 / 81 | 基础 |
| `backend/app/repositories/__init__.py` | 41 | — | 基础 |
| `backend/app/repositories/agent_cache_repository.py` | 114 | 6 / 41 | 基础 |
| `backend/app/repositories/agent_run_repository.py` | 154 | 6 / 44 | 基础 |
| `backend/app/repositories/agent_usage_repository.py` | 198 | 10 / 59 | 基础 |
| `backend/app/repositories/chat_memory_repository.py` | 141 | 5 / 52 | 基础 |
| `backend/app/repositories/chat_session_repository.py` | 382 | 12 / 66 | 基础 |
| `backend/app/repositories/consolidation_repository.py` | 57 | 1 / 42 | 基础 |
| `backend/app/repositories/daily_pipeline_repository.py` | 120 | 6 / 28 | 基础 |
| `backend/app/repositories/first_board_repository.py` | 1267 | 34 / 138 | 下次变更拆 bar/snapshot 仓库，保持门面 |
| `backend/app/repositories/limit_up_repository.py` | 241 | 11 / 61 | 基础 |
| `backend/app/repositories/post_limit_repository.py` | 66 | 1 / 45 | 基础 |
| `backend/app/repositories/recommendation_intelligence_repository.py` | 305 | 9 / 66 | 接口引用候选 |
| `backend/app/repositories/review_snapshot_repository.py` | 137 | 7 / 25 | 基础 |
| `backend/app/repositories/scoring_policy_repository.py` | 268 | 12 / 40 | 基础 |
| `backend/app/repositories/stock_news_repository.py` | 174 | 6 / 44 | 基础 |
| `backend/app/routers/__init__.py` | 1 | — | 基础 |
| `backend/app/routers/agents.py` | 1314 | 41 / 185 | A05：共享生命周期，JSON/SSE 仅作传输适配 |
| `backend/app/routers/analysis.py` | 56 | 4 / 11 | 基础 |
| `backend/app/routers/consolidation.py` | 18 | 1 / 6 | 基础 |
| `backend/app/routers/limit_up.py` | 57 | 5 / 9 | 基础 |
| `backend/app/routers/market.py` | 235 | 6 / 31 | 基础 |
| `backend/app/routers/stocks.py` | 236 | 8 / 36 | 基础 |
| `backend/app/security.py` | 230 | 16 / 33 | 基础 |
| `backend/app/services/__init__.py` | 1 | — | 基础 |
| `backend/app/services/agent_rate_limit.py` | 253 | 13 / 80 | 基础 |
| `backend/app/services/analysis.py` | 422 | 14 / 118 | 基础 |
| `backend/app/services/consolidation.py` | 175 | 5 / 65 | 基础 |
| `backend/app/services/daily_bar_source.py` | 12 | 1 / 9 | 基础 |
| `backend/app/services/daily_review.py` | 122 | 3 / 46 | 基础 |
| `backend/app/services/data_health.py` | 187 | 2 / 154 | 基础 |
| `backend/app/services/dragon_tiger_review.py` | 225 | 8 / 66 | 基础 |
| `backend/app/services/evaluation_agent.py` | 410 | 10 / 80 | 基础 |
| `backend/app/services/factor_signal_diagnostic.py` | 913 | 23 / 128 | 数值算法已函数化；后续按诊断/计算分组 |
| `backend/app/services/finance_news.py` | 412 | 12 / 76 | 基础 |
| `backend/app/services/first_board_critic.py` | 169 | 7 / 45 | 基础 |
| `backend/app/services/first_board_enrichment.py` | 389 | 10 / 81 | 基础 |
| `backend/app/services/first_board_features.py` | 251 | 9 / 102 | 基础 |
| `backend/app/services/limit_up_reason.py` | 79 | 5 / 21 | 基础 |
| `backend/app/services/llm_provider.py` | 603 | 23 / 88 | 基础 |
| `backend/app/services/outcome_completeness.py` | 210 | 4 / 125 | 基础 |
| `backend/app/services/post_limit.py` | 710 | 31 / 102 | 基础 |
| `backend/app/services/prediction_quality_audit.py` | 538 | 11 / 167 | 基础 |
| `backend/app/services/prediction_time.py` | 139 | 6 / 49 | 基础 |
| `backend/app/services/prediction_time_audit.py` | 83 | 2 / 65 | 基础 |
| `backend/app/services/promotion_labels.py` | 48 | 2 / 25 | 基础 |
| `backend/app/services/prompt_security.py` | 166 | 2 / 12 | 基础 |
| `backend/app/services/rating_backtest.py` | 249 | 7 / 60 | 基础 |
| `backend/app/services/recommendation_intelligence.py` | 1273 | 26 / 439 | A06：证据/评分/时点/排序/持久化分阶段 |
| `backend/app/services/relay_universe.py` | 10 | 1 / 4 | 基础 |
| `backend/app/services/sample_data.py` | 248 | — | 基础 |
| `backend/app/services/scoring_error_diagnostic.py` | 375 | 11 / 141 | 基础 |
| `backend/app/services/scoring_policy.py` | 134 | 4 / 19 | 基础 |
| `backend/app/services/scoring_policy_optimizer.py` | 855 | 23 / 170 | 已分评价/比较/优化；下次核心修改再拆 |
| `backend/app/services/sector_performance.py` | 324 | 8 / 142 | 基础 |
| `backend/app/services/sector_stock_ranking.py` | 297 | 11 / 140 | 基础 |
| `backend/app/services/session_memory.py` | 495 | 19 / 61 | 基础 |
| `backend/app/services/stock_kline.py` | 514 | 20 / 72 | 基础 |
| `backend/app/services/stock_news.py` | 270 | 11 / 91 | 基础 |
| `backend/app/services/stock_position.py` | 397 | 14 / 100 | 基础 |
| `backend/app/services/system_health.py` | 176 | 5 / 87 | 基础 |
| `backend/app/services/web_search.py` | 255 | 9 / 55 | 基础 |
| `backend/requirements-dev.txt` | 2 | — | 基础 |
| `backend/requirements.txt` | 6 | — | 基础 |
| `backend/scripts/audit_prediction_times.py` | 39 | 1 / 18 | 基础 |
| `backend/scripts/backup_database.py` | 73 | 4 / 24 | 基础 |
| `backend/scripts/dev_check.py` | 127 | 3 / 77 | 基础 |
| `backend/scripts/export_consolidation_cases.py` | 86 | 4 / 28 | 基础 |
| `backend/scripts/import_limit_up_from_akshare.py` | 51 | 1 / 33 | 基础 |
| `backend/scripts/import_sample_data.py` | 23 | 1 / 6 | 基础 |
| `backend/scripts/research_limit_up_paths.py` | 363 | 12 / 119 | 基础 |
| `backend/scripts/research_pattern_positive_rates.py` | 130 | 5 / 62 | 基础 |
| `backend/scripts/research_pullback_consolidation.py` | 228 | 9 / 100 | 基础 |
| `backend/scripts/retire_first_board_discovery.py` | 79 | 4 / 24 | 基础 |
| `backend/scripts/run_agent_eval.py` | 324 | 2 / 265 | 基础 |
| `backend/scripts/run_agent_question_bank.py` | 446 | 9 / 194 | 基础 |
| `backend/scripts/run_daily_close_loop.py` | 613 | 13 / 234 | A06 |
| `backend/scripts/run_factor_signal_diagnostic.py` | 181 | 4 / 67 | 基础 |
| `backend/scripts/run_recommendation_refresh_loop.py` | 230 | 12 / 45 | 基础 |
| `backend/scripts/start_backend.cmd` | 3 | — | 基础 |
| `backend/scripts/start_backend.ps1` | 83 | — | 基础 |
| `backend/scripts/sync_first_board_history.py` | 228 | 4 / 110 | bar 映射重复 |
| `backend/scripts/update_daily_data.py` | 958 | 9 / 272 | A06：按五个数据阶段组织，保护快照与回填 |
| `backend/scripts/validate_consolidation_evidence.py` | 198 | 9 / 107 | 基础 |
| `backend/tests/fixtures/agent_conversation_eval_scenarios.json` | 214 | — | 基础 |
| `backend/tests/fixtures/agent_eval_cases.json` | 179 | — | 基础 |
| `backend/tests/fixtures/agent_paraphrase_eval_cases.json` | 307 | — | 基础 |
| `backend/tests/fixtures/agent_product_eval_scenarios.json` | 270 | — | 基础 |
| `backend/tests/fixtures/agent_v1_scope_cases.json` | 16 | — | 基础 |
| `backend/tests/fixtures/query_contract_v2_cases.json` | 192 | — | 基础 |
| `backend/tests/test_agent_cache_repository.py` | 55 | 4 / 14 | 基础 |
| `backend/tests/test_agent_capability_contract.py` | 413 | 23 / 34 | 基础 |
| `backend/tests/test_agent_chat.py` | 1891 | 77 / 146 | 按领域拆测试组织，保留回归断言 |
| `backend/tests/test_agent_eval_runner.py` | 281 | 12 / 54 | 基础 |
| `backend/tests/test_agent_external_tools.py` | 1231 | 38 / 62 | 保留独立 mock 场景，后续按工具域拆测试 |
| `backend/tests/test_agent_observability.py` | 48 | 1 / 36 | 基础 |
| `backend/tests/test_agent_output_sanitizer.py` | 150 | 10 / 29 | 基础 |
| `backend/tests/test_agent_question_bank_runner.py` | 79 | 2 / 44 | 基础 |
| `backend/tests/test_agent_rate_limit.py` | 147 | 8 / 53 | 基础 |
| `backend/tests/test_agent_run_repository.py` | 59 | 1 / 46 | 基础 |
| `backend/tests/test_agent_tool_outcome.py` | 100 | 5 / 20 | 基础 |
| `backend/tests/test_agent_usage_repository.py` | 78 | 1 / 64 | 基础 |
| `backend/tests/test_agent_v1_profile.py` | 460 | 19 / 46 | 基础 |
| `backend/tests/test_akshare_limit_up_collector.py` | 122 | 6 / 37 | 基础 |
| `backend/tests/test_analysis.py` | 186 | 13 / 61 | 基础 |
| `backend/tests/test_anonymous_session_security.py` | 226 | 10 / 92 | 基础 |
| `backend/tests/test_answer_grounding.py` | 105 | 5 / 33 | 基础 |
| `backend/tests/test_backup_database.py` | 47 | 2 / 18 | 基础 |
| `backend/tests/test_chat_session_api.py` | 114 | 1 / 83 | 基础 |
| `backend/tests/test_chat_session_repository.py` | 171 | 3 / 84 | 基础 |
| `backend/tests/test_config.py` | 165 | 11 / 26 | 基础 |
| `backend/tests/test_consolidation_case_export.py` | 21 | 2 / 7 | 基础 |
| `backend/tests/test_consolidation_evidence_validation.py` | 42 | 5 / 11 | 基础 |
| `backend/tests/test_consolidation_strategy.py` | 273 | 20 / 21 | 基础 |
| `backend/tests/test_daily_close_loop.py` | 410 | 29 / 49 | 基础 |
| `backend/tests/test_daily_update_pipeline.py` | 567 | 21 / 95 | 基础 |
| `backend/tests/test_data_health.py` | 96 | 6 / 30 | 基础 |
| `backend/tests/test_database_concurrency.py` | 149 | 8 / 39 | 基础 |
| `backend/tests/test_deployment.py` | 194 | 20 / 29 | 基础 |
| `backend/tests/test_dragon_tiger_review.py` | 182 | 5 / 48 | 基础 |
| `backend/tests/test_evaluation_agent.py` | 236 | 8 / 55 | 基础 |
| `backend/tests/test_explanation_agent.py` | 66 | 6 / 13 | 基础 |
| `backend/tests/test_factor_signal_diagnostic.py` | 347 | 17 / 70 | 基础 |
| `backend/tests/test_finance_news.py` | 224 | 11 / 65 | 基础 |
| `backend/tests/test_first_board_agent.py` | 247 | 11 / 31 | 基础 |
| `backend/tests/test_first_board_enrichment.py` | 133 | 3 / 75 | 基础 |
| `backend/tests/test_first_board_features.py` | 308 | 9 / 75 | 基础 |
| `backend/tests/test_hithink_finance_collector.py` | 383 | 13 / 53 | 基础 |
| `backend/tests/test_legacy_recommendation_reads.py` | 73 | 5 / 19 | 基础 |
| `backend/tests/test_limit_down_collector.py` | 45 | 3 / 19 | 基础 |
| `backend/tests/test_limit_up_path_research.py` | 89 | 10 / 13 | 基础 |
| `backend/tests/test_limit_up_reason.py` | 83 | 3 / 31 | 基础 |
| `backend/tests/test_limit_up_repository.py` | 96 | 6 / 12 | 基础 |
| `backend/tests/test_llm_provider.py` | 290 | 23 / 40 | 基础 |
| `backend/tests/test_market_index_collector.py` | 105 | 5 / 24 | 基础 |
| `backend/tests/test_market_summary_routes.py` | 45 | 1 / 33 | 基础 |
| `backend/tests/test_outcome_completeness.py` | 202 | 8 / 47 | 基础 |
| `backend/tests/test_pattern_positive_rates.py` | 28 | 3 / 7 | 基础 |
| `backend/tests/test_popularity_collector.py` | 36 | 1 / 24 | 基础 |
| `backend/tests/test_post_limit_research.py` | 563 | 30 / 43 | BC-001/002，A10 |
| `backend/tests/test_prediction_quality_audit.py` | 167 | 6 / 49 | 基础 |
| `backend/tests/test_prediction_snapshot_contract.py` | 316 | 12 / 66 | 基础 |
| `backend/tests/test_prediction_time.py` | 182 | 17 / 25 | 基础 |
| `backend/tests/test_project_checks.py` | 38 | 3 / 10 | 基础 |
| `backend/tests/test_prompt_security.py` | 56 | 4 / 13 | 基础 |
| `backend/tests/test_pullback_consolidation_research.py` | 59 | 6 / 9 | 基础 |
| `backend/tests/test_query_contract.py` | 171 | 15 / 17 | BC-003/006 |
| `backend/tests/test_rating_backtest.py` | 132 | 6 / 37 | 基础 |
| `backend/tests/test_rating_bands.py` | 71 | 1 / 46 | 基础 |
| `backend/tests/test_recommendation_intelligence.py` | 195 | 7 / 38 | 基础 |
| `backend/tests/test_recommendation_refresh_loop.py` | 97 | 6 / 28 | 基础 |
| `backend/tests/test_relay_universe.py` | 12 | 1 / 6 | 基础 |
| `backend/tests/test_retire_first_board_discovery.py` | 53 | 1 / 47 | 基础 |
| `backend/tests/test_review_agent.py` | 235 | 6 / 103 | 基础 |
| `backend/tests/test_review_snapshot_repository.py` | 163 | 6 / 63 | 基础 |
| `backend/tests/test_scoring_error_diagnostic.py` | 114 | 3 / 60 | 基础 |
| `backend/tests/test_scoring_policy_optimizer.py` | 289 | 8 / 60 | 基础 |
| `backend/tests/test_sector_performance.py` | 173 | 6 / 43 | 基础 |
| `backend/tests/test_sector_stock_ranking.py` | 153 | 9 / 36 | 基础 |
| `backend/tests/test_session_memory.py` | 354 | 16 / 45 | 基础 |
| `backend/tests/test_stock_kline_collector.py` | 193 | 13 / 41 | 基础 |
| `backend/tests/test_stock_kline_service.py` | 511 | 23 / 71 | 基础 |
| `backend/tests/test_stock_news.py` | 211 | 11 / 52 | 基础 |
| `backend/tests/test_stock_position.py` | 91 | 7 / 18 | 基础 |
| `backend/tests/test_system_health.py` | 35 | 3 / 7 | 基础 |
| `backend/tests/test_tool_execution.py` | 97 | 10 / 15 | 基础 |
| `backend/tests/test_tool_policy.py` | 630 | 28 / 151 | A01 覆盖差异 |
| `backend/tests/test_web_search.py` | 34 | 3 / 23 | 基础 |
| `deploy/ci_deploy.py` | 42 | 1 / 29 | 基础 |
| `deploy/cron/limituplab-backup` | 5 | — | 基础 |
| `deploy/cron/limituplab-daily` | 6 | — | 基础 |
| `deploy/install.py` | 83 | 1 / 66 | 基础 |
| `deploy/job.py` | 26 | 1 / 15 | 基础 |
| `deploy/logrotate/limituplab` | 10 | — | 基础 |
| `deploy/nginx/app.conf` | 74 | — | 基础 |
| `deploy/nginx/limituplab-site.conf.example` | 17 | — | 基础 |
| `deploy/nginx/maintenance.conf` | 4 | — | 基础 |
| `deploy/release.py` | 296 | 20 / 34 | 基础 |
| `docker-compose.yml` | 98 | — | 基础 |
| `frontend/Dockerfile` | 21 | — | 基础 |
| `frontend/index.html` | 12 | — | 基础 |
| `frontend/package.json` | 30 | — | A10 |
| `frontend/src/App.tsx` | 1967 | 145 / 420 | A07：页面/hooks/图表状态拆分 |
| `frontend/src/api.ts` | 403 | 39 / 71 | A03/A10 |
| `frontend/src/components/AgentAnswerMarkdown.tsx` | 128 | 9 / 35 | A10 |
| `frontend/src/components/AgentChatDock.tsx` | 648 | 57 / 505 | A07 |
| `frontend/src/components/ConsolidationPanel.tsx` | 108 | 15 / 94 | 基础 |
| `frontend/src/components/MarketKLineChart.tsx` | 581 | 51 / 360 | A07 |
| `frontend/src/components/Panel.tsx` | 23 | 1 / 14 | 基础 |
| `frontend/src/components/ReviewDashboard.tsx` | 859 | 61 / 176 | A07：按有独立状态的面板拆分 |
| `frontend/src/consolidation.ts` | 116 | 7 / 16 | 基础 |
| `frontend/src/dashboardFormatters.ts` | 45 | 8 / 10 | 基础 |
| `frontend/src/intradayChart.ts` | 37 | 4 / 17 | 基础 |
| `frontend/src/main.tsx` | 14 | — | 基础 |
| `frontend/src/relayRanking.ts` | 68 | 9 / 21 | 基础 |
| `frontend/src/styles.css` | 5025 | — | A02：先清残留，再按组件组织，保护级联 |
| `frontend/src/types.ts` | 955 | — | A03：先清孤立类型，剩余按领域分组 |
| `frontend/src/vite-env.d.ts` | 1 | — | 基础 |
| `frontend/tests/consolidation.test.ts` | 76 | 9 / 18 | 基础 |
| `frontend/tests/intradayChart.test.ts` | 46 | 3 / 39 | 基础 |
| `frontend/tests/relayRanking.test.ts` | 87 | 10 / 30 | 基础 |
| `frontend/tsconfig.json` | 23 | — | 基础 |
| `frontend/vite.config.ts` | 14 | — | 基础 |
| `pytest.ini` | 3 | — | 基础 |
| `scripts/check_project.py` | 127 | 3 / 34 | 验证入口/隔离环境 |
| `scripts/daily_close_loop_task.ps1` | 77 | — | 基础 |
| `scripts/start_dev_detached.py` | 294 | 11 / 84 | 基础 |
| `scripts/start_local.cmd` | 50 | — | 基础 |
