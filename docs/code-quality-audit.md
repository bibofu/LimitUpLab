# Agent 应用质量审查

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
