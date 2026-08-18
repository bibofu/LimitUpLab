# LimitUpLab 任务清单

本文件用于跟踪 LimitUpLab 从“涨停复盘看板”升级为“可解释首板评级 Agent 系统”的开发进度。
后续每完成一个阶段，都要同步更新本文件状态。

状态说明：

```text
[ ] 未开始
[~] 进行中
[x] 已完成
[!] 阻塞 / 需要决策
```

## 当前路线图

建议先做一个 2-3 周内可展示的求职 MVP，再继续补完整 Agent 闭环和网站化能力。

第一阶段目标：可展示 MVP

```text
首板候选池过滤
  -> 规则评分
  -> 评分 API
  -> 前端首板评级页
  -> 历史相似案例 Top-K
  -> Agent Chat 基础问答
  -> LLM 解释
```

第二阶段目标：完整竞争力版本

```text
Critic Agent
  -> Evaluation Agent
  -> agent_runs 运行记录
  -> 上下文管理
  -> 缓存与异步化预留
  -> 新闻/公告增强
  -> 项目包装
```

## Milestone 1：首板评分 Agent MVP

目标：系统可以基于真实涨停数据筛出当日首板，并给出可解释评分。

- `[x]` 实现首板候选池过滤。
  - 支持传入 `trade_date`，默认使用最新交易日。
  - 只保留 `board_height = 1`。
  - 只保留 `closed_limit = true`。
  - 排除 `ST / *ST / 退市风险警示` 股票。
  - 排除北交所股票。
  - 排除科创板股票。
  - 排除新股 / 次新股。
  - 排除成交额过小样本。
  - 缺失过滤字段时标记为 `data_missing`，并降低 `confidence`。

- `[x]` 实现首板候选 facts builder。
  - 从已有涨停事件和市场汇总中构建结构化事实。
  - 包含首封时间、炸板次数、封板次数、换手率、成交额、行业、市场环境、行业热度。
  - 不让 LLM 编造任何行情字段。

- `[x]` 实现规则评分引擎。
  - 输出 `score`、`rating`、`confidence`、`score_breakdown`、`reasons`、`risks`。
  - 明确区分 `score` 和 `confidence`。
  - 评分结果必须可解释、可测试、可复盘。

- `[x]` 完成首板评分扩展输入 v2。
  - 为当日合格首板候选缓存截至评分日的最近 60 日 K 线，并派生 5/20/60 日涨幅、距阶段高点、量比、波动率和均线结构。
  - 接入 CNInfo 上市日期，计算上市天数并用于新股/次新股过滤。
  - 流通市值优先使用龙虎榜明细真实值，缺失时使用成交额/换手率估算并记录来源。
  - 从个股 60 日 K 线按主板约 10%、创业板约 20% 的涨停阈值识别近 20/60 个交易日涨停次数，不依赖涨停池历史接口的保留期限。
  - 细化行业首板数、连板数、炸板数、板块高度、首封位次、昨日首板晋级率和市场首板封住率。
  - 接入东方财富龙虎榜和人气 Top100 时点快照；接口失败可降级并写入 `data_missing`，不编造数据。
  - 新增 `first_board_enrichment_snapshots` 时点快照表，评分版本升级为 `first-board-rule-v2-enriched`。
  - 2026-08-13 实测：32/32 候选具备 60 日 K 线、上市日期和流通市值，8 只命中龙虎榜，14 只进入人气 Top100。

- `[x]` 新增首板评级 API。
  - 建议接口：`GET /api/agents/first-board-ratings`。
  - 支持日期参数和最新日期 fallback。
  - 返回候选事实、评分拆解、理由和风险。

- `[x]` 增加后端测试。
  - 测试候选池过滤语义。
  - 测试评分区间和评级标签。
  - 测试缺失数据会降低置信度。
  - 测试输出不包含买入、卖出、目标价、仓位等投资建议表达。

验收标准：

```text
后端能返回某个交易日的首板评级列表。
每只股票都有分数、评级、置信度、评分拆解、理由和风险。
测试通过。
```

## Milestone 2：前端首板智能评级页

目标：用户可以在页面上直接查看首板评级结果和个股详情。

- `[x]` 更新前端 API 类型和请求方法。

- `[x]` 新增“首板智能评级”视图。
  - 展示股票、行业、首封时间、炸板次数、换手率、成交额、评分、评级、置信度、主要理由、风险标签。
  - 页面要是实际可用的工作台，不做营销式 landing page。

- `[x]` 新增个股详情区域。
  - 展示评分拆解。
  - 展示原始 facts。
  - 展示理由和风险。
  - 预留历史相似案例、Explanation Agent、Critic Agent、Evaluation Agent 区域。

- `[x]` 完成前端构建验证。
  - 运行 `npm.cmd run build`。
  - 检查桌面和移动端布局是否可读。

验收标准：

```text
打开前端后，可以看到首板评级列表。
点击个股后，可以看到结构化详情。
页面没有明显布局错位。
```

## Milestone 3：历史相似案例 RAG

目标：把 RAG 做成结构化历史案例检索，而不是普通文本检索。本地需要持续维护可用的近期首板样本库；如果数据源无法一次性回补 60 个交易日，要从每日同步开始滚动沉淀。

- `[x]` 设计首板特征模型。
  - 候选表：`first_board_features`。
  - 包含相似度计算需要的结构化特征。
  - 尽量记录 `scoring_version` 和 `data_version`。

- `[x]` 实现历史样本特征构建。
  - 从历史涨停事件中抽取首板样本。
  - 生成首封时间区间、炸板次数、封板次数、成交额、换手率、市场环境、行业热度等特征。

- `[x]` 实现 SQL 召回。
  - 只召回历史日期，不包含目标日期和未来日期。
  - 只召回首板且收盘封住的样本。
  - 默认召回最近 60 个交易日，样本不足时扩展到 120 / 180 / 360。
  - 控制召回量在 300-500 条左右。

- `[x]` 实现加权相似度精排。
  - 对比首封时间、封板质量、换手率、成交额、市场环境、行业热度、近期 K 线结构。
  - 返回 Top-K 相似案例。
  - 每个案例必须说明相似原因和差异点。

- `[x]` 新增相似案例 API。
  - 建议接口：`GET /api/agents/first-board-similar-cases`。
  - 支持 `symbol`、`trade_date`、`limit` 参数。

- `[x]` 前端展示历史相似案例。
  - 展示相似股票、日期、相似度、共同点、差异点、后续表现。

- `[x]` 补齐相似案例首板后走势缓存。
  - 支持按目标股票回填 Top-K 相似案例后续 K 线。
  - 支持按当天首板候选池批量回填相似案例后续 K 线。
  - 单个行情源异常时跳过并继续处理后续样本。

验收标准：

```text
用户可以查看某只首板股票的 Top-K 历史相似案例。
相似案例不是黑盒结果，而是能解释为什么相似。
```

## Milestone 4：Agent Chat 基础版

目标：前端提供对话入口，用户可以围绕首板评级结果追问。

- `[x]` 新增前端 Agent Chat 入口。
  - 建议使用右侧抽屉或底部面板。
  - 支持当前日期、当前股票、当前页面上下文。

- `[x]` 新增 Chat API。
  - 建议接口：`POST /api/agents/chat`。
  - 请求包含 `session_id`、`message`、`trade_date`、可选 `symbol` 和页面上下文。

- `[x]` 实现问题意图识别和工具路由。
  - 今日首板总结。
  - 为什么某只股票评分高 / 低。
  - 某只股票有哪些历史相似案例。
  - 今日市场环境如何。
  - 某个行业热度如何。
  - 哪些股票高分但低置信度。

- `[x]` 增加显式 Agent Plan 中间层。
  - 每次回答前先解析用户问题的 `intent`、`trade_date`、`symbol` 和筛选条件。
  - 生成计划调用的工具步骤，如 `first_board_ratings`、`first_board_filter`、`first_board_similar_cases`。
  - 将 `agent_plan` 作为工具轨迹返回前端，便于调试用户随机提问时 Agent 如何理解问题。
  - 为后续 LLM Tool Planner 和多工具复合问题拆解预留入口。

- `[x]` 支持第一版多工具复合问题拆解。
  - 可处理“某日首板里某行业/题材评分最高的是谁，有没有历史相似案例”。
  - 执行链路为：首板评级候选池 -> 行业/题材筛选 -> 选择目标股票 -> 历史相似案例检索。
  - 如果相似案例特征缓存缺失，明确说明缺失原因，不编造历史案例。

- `[x]` 增加首板板块问答能力。
  - 可处理“某日首板票主要板块有哪些”。
  - 先调用 `first_board_ratings` 获取结构化候选池，再聚合行业分布、平均评分和代表股票。
  - 回答统一走 LLM 生成入口，LLM 不可用时使用同一份 facts 的模板兜底。

- `[x]` 重构开放问答为统一工具事实生成通道。
  - 对市场环境、今日首板、板块分布、候选排序和未知首板相关问题，统一构建 `market_summary` + `first_board_ratings` facts 包。
  - LLM 负责基于 facts 生成最终回答，规则模板只作为 LLM 不可用时的兜底。
  - 保留语义 intent 便于前端和日志理解问题类型，但不再为每种问法写独立回答模板。
  - 补充“哪些候选评分靠前”测试，避免自然语言问法再次落入错误分支。

- `[x]` 增加 Conversation Router 入口层。
  - 先判断用户是在问能力说明、普通寒暄、越界问题、交易指令风险，还是股票工具问题。
  - `capability_intro`、`smalltalk`、`out_of_scope`、`unsafe_investment_advice` 不再触发股票数据工具。
  - 明确回答“你能做什么”，并说明当前 Agent 的工具边界。
  - 对直接交易类问题做安全降级，不输出交易指令、资金配比、价格预测或回报承诺。

- `[x]` 增加 LLM Planner 优先的工具调用主链路。
  - 正常链路调整为：用户问题 -> 拼接系统指令、工具描述和上下文 -> LLM 输出 JSON 工具计划 -> 后端执行工具 -> LLM 基于工具 facts 生成最终回答。
  - `intent` 降级为日志和审计标签，不再作为真实 LLM 可用时的主控制流。
  - 支持的工具计划包括 `market_summary`、`first_board_ratings`、`first_board_filter`、`first_board_similar_cases`。
  - LLM 不可用、未配置 key 或计划 JSON 解析失败时，自动回落到原有确定性规则链路，保证本地演示不崩。
  - 增加 Fake LLM 单测，验证 Agent 会先规划工具，再执行工具并生成回答。

- `[x]` 标准化 Agent 工具 schema 和执行轨迹。
  - 每个 Agent 工具统一声明 `name`、`description`、`args_schema`、`returns`。
  - Planner prompt 从工具 schema 自动生成，减少手写工具描述漂移。
  - 工具 trace 统一包含 `status`、`input`、`summary`、`output`、`error`。
  - 工具失败或模型请求未知工具时，不直接中断，改为记录 error trace 并交给 LLM 解释。
  - 前端对话区支持展开查看每次工具调用的输入和输出摘要。

- `[x]` 将散落的工具补偿规则重构为统一 Tool Policy Engine。
  - 新增声明式 `ToolRepairRule`，集中管理触发信号、目标工具、补偿原因和执行顺序。
  - `chat.py` 主链路只保留一次 `policy.reconcile(...)`，不再串联多个 `_ensure_*` 分支。
  - 对 Planner 直接回答进行统一事实接地检查，候选评分、相似案例、K 线、回测、Critic 和复盘问题必须经过对应工具。
  - 工具轨迹写入 `policy_repair` 元数据，前端审计可展示真实补偿规则及原因。
  - 增加独立 Policy 测试，覆盖规则互斥、缺失日期短路和空工具计划审计。

- `[x]` 增加每日数据更新流水线。
  - 新增 `scripts/update_daily_data.py`，串联涨停/炸板导入、近 60 个交易日首板特征同步、Top 候选相似案例后续 K 线回填。
  - 输出 JSON 健康报告，检查原始涨停数据、首板特征、Top 候选相似案例和 post bars 是否可用。
  - 支持 `--skip-import`、`--replace-date`、`--top-targets`、`--similar-limit`、`--max-kline-fetches` 等参数，便于本地调试和后续定时任务接入。
  - 补充单元测试，避免再次出现“只拉原始数据但忘记同步派生特征”的问题。

- `[x]` 增加 Agent 数据健康状态可见化。
  - 新增 `/api/agents/data-health`，返回最新交易日、原始涨停数据、首板特征、Top 候选相似案例和 post bars 覆盖情况。
  - 抽出 `app.services.data_health`，让 API 和每日更新脚本复用同一套健康检查逻辑。
  - 前端 Agent 工作台顶部展示“数据健康 / 部分可用 / 数据缺失”状态和关键数量。
  - `backend/README.md` 补充每日更新流水线命令和 data-health endpoint。

- `[x]` 增加评分回测与自我评价 MVP。
  - 新增 `app.services.rating_backtest`，按日期范围重算首板评分并对齐 `first_board_outcomes`。
  - 新增 `/api/agents/rating-backtest`，输出 A/B/C/D 分桶表现、失败样本和自我评价观察。
  - Agent 工具层新增 `rating_backtest`，用户可询问“最近首板评分准吗”“做个回测和自我评价”。
  - LLM Planner 漏调回测工具时，后端会根据问题自动补调用。
  - 前端首页新增“评分回测与自我评价”面板，展示评级分桶表现、走势覆盖率、回测观察和高分弱表现样本。
  - 补充回测服务和 Agent 工具调用测试。

- `[x]` 实现受控上下文管理第一版。
  - 保留最近 6-10 轮对话。
  - 保存当前 `trade_date` 和 `symbol`。
  - 工具大结果只保存引用或摘要。
  - 股票或日期指代不清时要求用户澄清。

- `[x]` 增强多轮指代解析第一版。
  - 从上一轮 `agent_plan`、`references` 和工具轨迹中恢复日期、筛选条件、命中股票列表和当前目标股票。
  - 支持追问“那里面评分最高的是谁”，复用上一轮行业/题材筛选池并选出最高评分候选。
  - 支持追问“它有没有历史相似案例”，复用上一轮选出的股票继续调用相似案例工具。
  - 当股票已识别但相似案例特征缓存缺失时，明确说明缓存缺失，不误报为股票不存在。

- `[x]` 新增个股 K 线 Agent 工具。
  - 支持按股票代码或本地股票名称查询最近 5-60 个交易日。
  - SQLite 缓存优先，历史 K 线补全，最新收盘快照兜底。
  - 输出数据截止日、涨跌幅、均线、量比、最大回撤和原始 OHLCV。
  - Planner 漏调时由后端补调用，LLM 不可脱离工具 facts 判断走势。

- `[x]` 增加问答安全边界。
  - 回答必须基于工具返回的 facts。
  - 不输出买入 / 卖出建议。
  - 不输出目标价、仓位和收益承诺。
  - 不编造新闻、资金流、基本面或未提供的数据。

验收标准：

```text
用户可以问：“总结今天首板涨停的股票”。
用户可以问：“为什么 A 股票评分高”。
用户可以问：“A 股票有没有历史相似股票”。
Agent 能调用对应工具并基于事实回答。
```

## Milestone 5：LLM Explanation Agent

目标：接入 LLM，但只让 LLM 做解释、总结和表达，不让 LLM 直接打分。

- `[x]` 增加 LLM provider 抽象。
  - provider、model、api key 通过配置管理。
  - 支持 LLM disabled 模式，失败时使用模板化解释。

- `[x]` 设计 Explanation Agent prompt。
  - 输入包括 facts、score breakdown、similar cases、market context、risks。
  - 输出包括摘要、支持因素、风险因素、置信度解释、数据限制。

- `[x]` 实现结构化输出校验。
  - 校验字段完整性。
  - 校验不能出现投资建议表达。
  - 校验回答必须引用已提供事实。

- `[~]` 在前端详情页展示 Agent 解释。

- `[x]` 增加 Explanation Agent 测试。

验收标准：

```text
LLM 解释能说明评分依据、风险和历史相似案例。
LLM 不直接决定 score。
LLM 不输出买卖建议。
```

## Milestone 6：Critic Agent

目标：让系统具备自我质疑能力，避免评分结论过度乐观。

- `[x]` 实现 Critic Agent。
  - 检查是否过度乐观。
  - 检查是否缺失关键数据。
  - 检查是否有反向市场证据。
  - 检查是否越过投资建议边界。
  - 新增 `/api/agents/first-board-critic`，按股票和交易日输出结构化复核结果。
  - Agent 工具层新增 `first_board_critic`，支持用户询问“评分靠谱吗”“帮我反驳一下高分理由”。
  - LLM Planner 漏调 Critic 工具时，后端会根据问题自动补调用。

- `[x]` 实现置信度调整逻辑。
  - Critic 可以降低 confidence。
  - Critic 不直接重写评分规则。
  - 输出原始置信度、建议置信度、confidence delta 和 `supportive / cautious / fragile` verdict。

- `[x]` 前端展示 Critic warnings。
  - 个股详情页新增“Critic 复核”面板。
  - 展示支持证据、反向证据、缺失数据、复盘问题和数据边界提示。

验收标准：

```text
每个重点候选都能看到主评分结论和 Critic 质疑。
Critic 输出能解释为什么降低或不降低置信度。
```

## Milestone 7：Evaluation Agent 自我复盘

目标：让 Agent 对自己的历史评级结果进行事后评价，形成闭环。

- `[x]` 设计预测和结果存储。
  - 新增 `agent_predictions`，保存每日首板评分快照。
  - 复用现有 `first_board_outcomes` 作为后续走势结果表。
  - Evaluation 输出暂不落独立表，先由服务实时生成，后续可扩展为 `agent_evaluations`。

- `[x]` 保存每日评级结果。
  - 保存 score、rating、confidence、reasons、risks、scoring_version、facts snapshot、data_as_of 和 prediction_source。
  - 预测快照按 `live / historical_backtest` 分开保存，同一评分版本允许同时保留两类不可变记录。
  - Evaluation 接口只读取已保存预测，不再因查询复盘接口而自动制造历史预测。
  - 每日数据更新阶段保存最新交易日 live Top10；缺失的历史 Top10 只能标记为 historical_backtest。

- `[x]` 保证每日 Top10 后续走势缓存。
  - 每次数据更新优先校验最近 6 个交易日 Top10 的“首板日 + 后续 5 个交易日”K 线。
  - 对远端空结果和临时失败执行重试，并把未补齐样本保留到下一次更新继续处理。
  - 前端固定展示首板至 D+5 六个槽位，区分“待收盘”和“缺缓存”。
  - 修正腾讯历史行情 `end_date` 排他边界，确保复盘截止日 K 线能够入库并补齐存量 Top10。

- `[x]` 回填或计算次日结果。
  - 是否晋级二板。
  - 次日开盘涨跌幅。
  - 次日最高涨跌幅。
  - 次日收盘涨跌幅。
  - 最大回撤。
  - 新增以次日开盘为介入基准的开盘到最高、最低、收盘收益，以及三日收益和最大回撤。
  - 分离 `next_day_ready` 与 `three_day_ready`，次日评价不再被三日缓存状态阻塞。
  - MVP 阶段复用每日数据流水线维护的 `first_board_outcomes`。

- `[x]` 实现 Evaluation Agent。
  - 分类 `success`、`partial`、`miss`、`avoid_success`、`false_negative`。
  - 成功标签只由“次日开盘到收盘收益”决定；晋级二板和盘中冲高作为独立事实，不再混入成功标签。
  - 生成经验教训和评分规则调整建议。
  - 只给建议，不自动修改评分规则。
  - 新增 `/api/agents/rating-evaluation`，输出预测数量、outcome 覆盖、标签分布、样本评价和复盘摘要。
  - Agent 工具层新增 `rating_evaluation`，用户可询问“复盘一下最近哪些评分错了”“昨天评分最高的票表现怎么样”。
  - LLM Planner 漏调 Evaluation 工具时，后端会根据问题自动补调用。

- `[x]` 前端展示复盘结果。
  - 首页新增“Evaluation Agent 预测复盘”面板。
  - 展示预测快照数、Outcome 覆盖率、误判/漏判数量、标签分布。
  - 展示重点复盘样本，包括 lesson 和 scoring suggestion。
  - 前端明确展示实时预测/历史回测来源，并优先展示次日开盘介入后的收益和回撤。

- `[x]` 完成受约束评分策略自我改进 MVP。
  - 将 12 个评分因子权重从代码常量抽成可版本化 `ScoringPolicy`，默认 Champion 精确兼容现有 `first-board-rule-v2-enriched` 结果。
  - 新增 SQLite Champion/Challenger 策略注册表与优化运行记录，预测快照继续绑定具体评分版本。
  - 按交易日顺序切分训练、验证、测试集：训练段只估计因子与次日开盘到收盘收益的相关方向，验证段选择受限候选，测试段只做最终晋级判断。
  - 单次权重相对变化不超过 12%，晋级同时检查独立交易日数、Top10 样本数、综合目标、正收益率和三日最大不利波动。
  - 优化默认运行在影子模式，只注册 Challenger；即使通过门槛也必须显式请求启用，未通过门槛时禁止晋级。
  - 新增评分策略查询/优化 API 和 `scoring_policy_status` Agent 工具，可回答当前 Champion、最近 Challenger、样本外表现和未晋级原因。
  - 2026-08-19 本地首次运行：11 个结果完整交易日按 6/2/3 切分，Challenger 测试段综合目标增量为 0，未通过门槛且未启用。

- `[~]` 训练与验证评分 v3。
  - 建立至少 60 个交易日的可用训练样本，按日期执行 walk-forward 验证。
  - 分别建模次日晋级、次日开盘到收盘上涨和大跌风险。
  - 对比 Top10 与全部候选、旧规则 baseline，只有样本外指标改善后才允许升级线上评分版本。
  - 当前阻塞在样本覆盖：只有 11 个结果完整交易日，需通过每日滚动沉淀或可靠历史回补扩展至至少 60 日。

验收标准：

```text
系统能回答：“昨天评分最高的首板表现怎么样”。
系统能总结哪些评分因子可能有效，哪些可能误导。
```

## Milestone 8：网站化运行架构

目标：让项目具备真实网站的并发、缓存、追踪和降级设计。

- `[x]` 新增 `agent_runs` 运行记录。
  - 记录 run type、status、input hash、input JSON、output JSON、error message、started_at、finished_at。

- `[x]` 新增 Agent 运行可观测性 MVP。
  - 后端提供 `/api/agents/runs` 查询最近运行记录。
  - 前端聊天工作台展示最近运行状态、耗时、工具链路、warning/error。
  - 保留单轮回答下方的详细 tool trace，方便演示 Agent 如何按需使用工具和结构化数据。

- `[x]` 新增结果缓存机制 MVP。
  - 新增 `agent_cache` SQLite 表和 `SQLiteAgentCacheRepository`。
  - 缓存 key 包含接口参数和本地行情事件签名，避免数据更新后继续读取旧结果。
  - 先缓存低风险结构化结果：首板评分、评分回测、Evaluation Agent。
  - 暂不缓存 LLM 对话文本，避免多轮上下文和页面上下文串味。

- `[x]` 完成 Agent 问答延迟优化第一版。
  - DeepSeek `deepseek-v4-flash` 的 Planner 和事实回答默认关闭 thinking，避免结构化任务把时间和 token 消耗在长 reasoning 上。
  - 同一请求复用 HTTP Session 和 HTTPS 连接，Planner / Answer 分别限制为 320 / 480 tokens。
  - Planner 工具 schema 只保留工具名、用途、参数类型和必填项；最终 facts 使用前 5 只详细、后 5 只摘要的紧凑结构。
  - API 返回 Planner、工具、Answer 和总耗时，以及两段 prompt 字符数。
  - 前端等待期间显示动态计时和“规划工具 / 查询数据 / 生成结论”，完成后展示分段耗时。
  - 2026-08-13 实测：“金开新能怎么样”从 15.9 秒降至约 4.4 秒；“总结今天首板”从 7.1 秒降至约 5.2 秒，均保留完整 LLM 工具调用链。

- `[ ]` 扩展结果缓存机制。
  - 缓存首板总结、评级结果、市场环境、行业热度、相似案例、LLM 解释。
  - 数据版本或评分版本变化时失效。

- `[x]` 完成 Agent SSE 流式回答 MVP。
  - 保留同步 `/api/agents/chat`，新增 `/api/agents/chat/stream` 流式接口。
  - 按 `planning -> tools -> answering -> answer_delta -> completed` 推送执行阶段、LLM 文本增量和完整响应。
  - DeepSeek 最终回答使用原生 `stream=true`，前端通过 Fetch ReadableStream 逐段解析 SSE 并实时追加文本。
  - 完成事件继续携带 run id、工具 trace、证据卡、Planner/Answer 耗时，并在服务端持久化完整 Agent run。
  - 后端修复首板数据问题被 Planner 直接回答的情况，强制先查 `first_board_ratings` 再流式生成事实回答。
  - 2026-08-13 实测：阶段事件约 0.04 秒到达，首个回答分片约 1.91 秒到达，完整回答约 4.05 秒，共 337 个文本增量。

- `[ ]` 扩展异步执行路径。
  - 为超长复盘和批量评测任务增加后台 Worker。
  - 支持客户端断开后取消上游 LLM 请求，并补充单用户并发上限。

- `[x]` 新增本地启动健康检查闭环。
  - 新增 `app.services.system_health` 和 `/api/agents/system-health`。
  - 检查本地最新数据日期、预期数据日期、数据是否新鲜、Agent data health、LLM 配置、代理异常、离线 eval 状态。
  - 新增 `backend/scripts/dev_check.py --ensure-data`，启动前可自动尝试更新最新收盘数据。
  - 新增 `scripts/start_local.cmd`，Windows 一键执行数据新鲜度检查后启动后端和前端。
  - 前端 Agent 工作台新增系统状态条，展示数据日期、数据新鲜度、LLM 状态和 eval 状态。

## Milestone 9：Agent 评测和回归体系

目标：让 Agent 的工具调用、回答事实和安全边界可以被持续验证，而不是靠人工随手试问。

- `[x]` 新增 Agent 问答评测集 MVP。
  - 新增 `tests/fixtures/agent_eval_cases.json`。
  - 覆盖能力说明、开盘时间、今日首板、指定日期、评分靠前、单股解释、历史相似案例、市场情绪、连板查询、缺失日期、越界交易问题。

- `[x]` 新增 deterministic eval runner。
  - 新增 `app.agents.eval_runner`，使用离线 LLM provider 强制走稳定兜底链路。
  - 检查 expected intent、required tools、forbidden tools、answer_contains、warnings、投资建议禁词。
  - 新增 `scripts/run_agent_eval.py`，可命令行输出评测报告。

- `[x]` 扩展真实 LLM 评测 MVP。
  - `scripts/run_agent_eval.py --mode live-llm` 会调用当前配置的 LLM provider。
  - live 模式记录 planner 原始工具、最终工具、后端修复工具、trace、warning 和回答预览。
  - 失败样本和后端修复样本输出到 `backend/data/agent_eval_failures.json`，该文件不进入 git。
  - 默认 live 模式不因模型抽样失败返回非零退出码；需要严格检查时使用 `--fail-on-failures`。
  - 当前 DeepSeek 抽样结果：11 个 case 中 7 个通过，4 个进入失败复盘，主要问题是能力说明未出现产品名、日期格式表达差异、评分解释时 planner 只选 Critic、缺失日期问题直接回答未调用数据可用性工具。

- `[ ]` 增加前端/文档化评测报告。
  - 展示通过率、失败 case、工具调用准确率、越界拦截情况。

- `[x]` 扩展 Agent 通用涨停事件查询能力。
  - 新增 `limit_up_events` 工具，支持按日期、板数、连板、炸板、行业/题材关键词过滤。
  - 修复“涨停 + 行业/板块”被误判为首板筛选的问题。
  - 增加“今天二连板有哪些”“今天涨停里医药相关有哪些”的回归测试。

- `[ ]` 增加超时、重试和失败降级。
  - LLM 不可用时展示规则评分和模板化解释。
  - 相似案例不可用时展示结构化评分并降低置信度。
  - 工具失败时明确报错，不生成虚假答案。

- `[ ]` 增加成本和速率控制。
  - 限制单用户 / 单 session 并发 Agent run 数量。
  - 缓存重复请求。
  - 列表页不批量生成长 LLM 文本。

验收标准：

```text
Agent 执行可追踪、可复用、可失败降级。
项目架构能解释如何支撑多用户网站化场景。
```

## Milestone 9：新闻 / 公告增强

目标：把新闻和公告作为解释增强，不作为第一版评分主因。

- `[ ]` 调研稳定的数据源。
  - 优先公告。
  - 新闻作为题材解释。
  - 研报作为可选背景，不作为短线评分主因。

- `[ ]` 新增公告 / 新闻检索工具。
  - 用于解释题材催化和风险提示。
  - 不直接决定 score。

- `[ ]` 前端展示来源和引用。

验收标准：

```text
Agent 可以说明某只股票可能受哪些公告或新闻影响。
回答中必须展示来源，不允许编造新闻。
```

## Milestone 10：求职项目包装

目标：把项目包装成能被面试官快速理解的 Agent 工程作品。

- `[ ]` 更新 README。
  - 写清楚新定位：可解释首板评级 Agent 系统。
  - 写清楚 facts-first 架构。
  - 写清楚不是投资建议。
  - 增加架构图。

- `[ ]` 增加演示流程。
  - 数据导入。
  - 首板评级。
  - 历史相似案例。
  - Agent Chat。
  - Critic 和 Evaluation 示例。

- `[ ]` 增加截图或短演示视频。

- `[ ]` 增加架构说明文档。
  - Tool calling。
  - 上下文控制。
  - 轻量 Multi-Agent。
  - 自我评价闭环。
  - 网站化运行架构。

验收标准：

```text
项目首页、README 和演示流程能让面试官在 3-5 分钟内理解亮点。
可以讲清楚为什么这不是普通股票看板，也不是简单 LLM 套壳。
```

## 暂缓事项

- `[ ]` 用户账号和权限系统。
- `[ ]` 正式部署配置。
- `[ ]` SQLite 迁移到 PostgreSQL。
- `[ ]` 完整异步 Worker 栈。
- `[ ]` 更复杂的因子研究和回测系统。
- `[ ]` 数据导入状态管理页面。






