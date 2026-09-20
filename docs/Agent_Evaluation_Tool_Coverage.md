# Agent 评测工具覆盖清单

更新：2026-09-16。第一步交付：源码盘点与后续接入清单，未运行真实模型、未检查数据库样本是否齐备。

## 目标与当前基线

先完成所有本地工具的评测接入、通用判分、LLM Judge 和统一报告，再增加问题深度。
Local30 保留为回归集；三轮全绿不作为继续搭建框架的前置条件。

生产注册表共有26个业务工具：默认Profile开放24个，extended另开放
`remote_limit_up_pool`、`web_search`。默认Profile不等于纯本地执行。
本轮优先范围为下表16个具有本地研究路径的工具，其中3个已有Local30覆盖，13个待接入。
这是工具入口覆盖，不是参数分支全覆盖，也不是模型通过率。

当前Historical Live评测注册表只开放 `market_summary`、`limit_up_events`、`market_event_pool`，
且禁止远端跌停分支。生产工具已有Schema或pytest不意味着已有Agent行为评测。
最新Active Golden为 `output/agent-eval/golden/local30-current-v6/suite.json`，共30题；
同一最新资产的三轮正式稳定验收未完成。

## 本地优先工具：逐项接入清单

“已覆盖”仅表示已有实际Agent运行及正式判分；“待接入”表示缺少该工具的评测闭环。
下列代表问题是场景草案，不是已审核题目；日期、标的和预期值须先由真实数据预检确定。
所有待接入工具的本地样本充足性均为“待预检”。

| 工具 | 数据依赖与关键输入 | 当前覆盖 | 一个最小代表场景 | 确定性判分重点 |
| --- | --- | --- | --- | --- |
| `market_summary` | 本地事件；include_limit_down=false | 已覆盖本地分支 | 最新本地交易日涨停家数 | 实际日期、数量、禁止远端跌停调用 |
| `limit_up_events` | 本地事件；日期、市场、状态、板高 | 已覆盖部分组合 | 指定市场收盘封板名单 | 日期、代码名称、完整集合、状态 |
| `market_event_pool` | 本地涨停/炸板事件；日期、event_type、result_mode | 已覆盖本地分支 | 查询某市场涨停数量 | 筛选口径、count/list一致性、正常空集 |
| `daily_board_promotion` | 相邻日期事件与本地日K；days/end_date | 待接入 | 指定窗口每日晋级率 | 分母、晋级数、日期相邻性、缺失开盘数据 |
| `first_board_ratings` | 事件、评级Facts、预测快照；日期/标的 | 待接入 | 某日首板评级及缺失字段 | 分数、版本、置信度、live快照与重算来源 |
| `first_board_filter` | 评级结果；结构化筛选条件，Gateway适配器 | 待接入 | 按已定义条件筛选首板名单 | 条件交集、阈值边界、完整成员 |
| `stock_kline` | 本地日K优先；symbol/days/end_date；存在远端补数与写缓存 | 待接入，需隔离补数 | 指定股票历史窗口走势事实 | 截止日、价格、窗口、收益单位、缺失交易日 |
| `post_limit_screen` | 本地涨停后数据集；PostLimitQueryContract | 待接入 | 某截止日的涨停后形态筛选 | 锚点、条件、候选集合、可评价覆盖率 |
| `post_limit_path` | 本地事件/日K；contract及symbol | 待接入 | 某股涨停后的历史路径 | 锚点日期、路径顺序、截止日、缺失数据 |
| `post_limit_statistics` | 本地事件/日K；contract | 待接入 | 指定窗口的涨停后历史统计 | 完整样本分母、信号日数、统计值与版本 |
| `prediction_quality_audit` | 持久化预测、Outcome、策略；区间/版本/top_k | 待接入 | 某区间预测结果覆盖审计 | 去重数量、成熟Outcome覆盖率、版本与来源 |
| `rating_backtest` | 本地评分与Outcome；起止日/failure_limit | 待接入 | 历史评级分桶表现 | 样本数、成熟度、分桶、收益定义 |
| `first_board_critic` | 首板评级及Facts；标的/日期 | 待接入 | 解释某评级的反证和缺失项 | 标的、原始分数、反证、置信度与缺失字段 |
| `rating_evaluation` | 持久化预测与Outcome；区间/limit | 待接入 | 某区间预测复盘标签 | 标签、来源、可评价数、时间与收益口径 |
| `review_high_score_picks` | 历史预测、Outcome与事件；区间/阈值/top_per_day | 待接入 | 高分样本与市场晋级对照 | 样本选择、成功/失败/待定、对照分母 |
| `scoring_policy_status` | 本地策略注册表、优化记录；无参数 | 待接入 | 当前Champion和Challenger状态 | 版本、状态、是否激活、优化记录缺失 |

隔离接入时逐项核查默认仓库构造、隐式初始化、远端fallback和写入行为。
已知 `stock_kline` 数据不足时会调用采集器并upsert；评测需完整本地快照及受控数据依赖。
评级类工具涉及预测来源，不能把重算结果或未成熟Outcome当成真实发布预测及完整标签。

## 其余10个工具：保留在总清单中

这些工具依赖远端或混合数据路径，当前没有Local30正式行为覆盖。
本地框架先为它们保留接入位置，后续采用真实录制回放或单独的外部验证任务。
远端缓存存在不等于已经具备可复现的本地评测环境。

| 工具 | 主要依赖/范围 |
| --- | --- |
| `market_index_trend` | 指数行情采集 |
| `sector_performance` | 板块行情与强弱数据服务 |
| `sector_stock_ranking` | 远端板块成分与行情、本地仓库混合 |
| `hot_stock_ranking` | 同花顺/东方财富热榜，可附行情 |
| `dragon_tiger_list` | 同花顺龙虎榜 |
| `finance_news` | 财经资讯检索 |
| `stock_news` | 个股资讯检索 |
| `stock_activity` | 个股异动数据 |
| `remote_limit_up_pool` | 远端涨停池，仅extended |
| `web_search` | 外部搜索，仅extended |

另有两个工具的外部分支：`market_summary(include_limit_down=true)` 与
`market_event_pool(event_type=limit_down)`，不计入当前本地分支覆盖。

## LLM-as-a-Judge 覆盖与缺口

| 维度 | 当前实现 | 下一项工作 |
| --- | --- | --- |
| 事实支持 | full_answer_judge检查候选回答与本轮证据；已有正反例校准 | 接入各类工具的证据，补统计/评级/时间序列校准 |
| 澄清/拒答 | semantic_acceptance提供两类rubric及正反例 | 保留现有验收，统一到Judge配置与报告 |
| 相关性与任务完整性 | 核心合同检查部分字段；通用语义维度尚未接入 | 增加遗漏要求、答非所问与不必要扩写的校准 |
| 数据缺失与不确定性 | 事实裁判允许明确缺失说明；尚无独立完整验收 | 校准“缺数据却下结论”和正确说明缺口 |
| 合规 | 拒答题有语义检查；生产合规Critic不是独立评测验收 | 为普通研究回答补独立合规评测与正反例 |

统一协议需返回维度、pass/fail/needs_review、理由及问题声明；记录提示版本、证据摘要、
调用与费用、协议错误。确定性失败不能被Judge改为通过。有效fail不能靠重复重判洗成pass。
现有正式评分器仅对NativeFunctionCallingError有限重试一次；其他异常与中断仍需统一报告处理。

## 后续拆分任务

### 当前优先级调整：统一trace复评

停止逐工具Runner扩建；以下原任务列表保留为历史拆分，不再依次执行。
以任务完成、事实接地、边界安全和执行效率为核心，工具覆盖仅作诊断。
已有工具只要产出兼容trace即可进入通用检查，不要求新增专用Runner。

```powershell
# 在backend目录；读取已有运行，不执行Agent，不调用模型
.venv/Scripts/python.exe -m app.agent_eval review-trace --run-dir <已有单题运行目录> --output <新报告.json>
# 需要实验性语义评分时显式追加 --allow-judge
```

输入为case.json和response.json，可选读取usage.json；输出父目录需存在，不覆盖旧报告。
默认语义维度not_run。开启Judge后合并一次调用，不逐维收费；仅结构化协议错误最多重试一次，
网络、事实、Agent失败和超预算不重试。
只传问题上下文、要求、答案及当前证据，去除完全重复的证据，不传全部执行决策和隐藏推理。
不删除证据字段或列表行；包括prompt和工具schema在内最多24000字符，可用
`--max-input-chars`调整至1000..100000。字符限额不是精确Token或费用承诺。
硬失败、坏trace、超预算不调Judge；失败不能被Judge覆盖。Judge引用未知证据或协议错误记为judge_error。
原运行Token可能包括答案提取，与新增Judge用量分开报告；缺失用量为null，不当作零。
暂未做结果缓存；重复加`--allow-judge`会再次付费，默认离线复评则不收费。
新合并Judge尚未校准，不能替代旧正式验收；总判定fail退出1，其余needs_review退出2。
下一步是小规模校准误杀与漏判，而不是逐工具补Runner。

合并Judge校准入口（不运行Agent）：

```powershell
.venv/Scripts/python.exe -m app.agent_eval calibrate-trace-judge --output-dir <新校准目录> --allow-judge
```

固定6个合成样例，与review-trace共用同一裁判调用和协议验证。保存样例、请求/响应、
逐项裁决和报告，绑定提示、样例摘要及模型；已有目录拒绝覆盖。最多6次调用、180秒总预算，
每次最多1800输出Token；预算在调用之间检查，不是精确账单上限。不自动重判或调整提示。
报告分别计数false_fail（预期pass却判fail）、false_pass（预期fail却判pass）及弃判/未评，
并给出分维度标签命中数。错误调用用量不完整时总Token为null，不计作零。
标签是合成作者标注，六例只能定位明显误判，不代表总体误判率；即使全部命中，
independent_acceptance和release_eligible仍为false，review-trace不会自动升级为已校准。

输出限制定向校准使用 `--suite output_constraints`，固定4个合成样例、最多4次调用；
默认 `--suite core` 仍为6例。前者使用相同答案在严格/普通要求下作对照，并保护必要缺失说明，
只验证该判分边界，不替代core回归。当前提示区分显式输出约束与写作风格：
纯格式排版不失败，违反明确输出范围记task_completion失败，不自动污染其他维度。
提示改变后必须按prompt_digest区分校准产物，旧提示的通过不能作为新提示验收。

可选证据精简：为 `review-trace` 追加 `--compact-evidence`（无需开启Judge即可离线预检）。
只把同字段列表的重复字段名提到columns，逐行values仍包含全部值；不做语义筛选。
每次编码均验证还原摘要，记录净字符变化；无收益或保留键冲突时回退原格式。
启用编码时额外解码说明计入输入预算与提示摘要；字段不同的行、重复行及null不会被丢弃。
目前默认关闭：数据还原等价不代表Judge行为等价，未完成配对模型验证前不能将其作为默认优化，
也不能把字符减少比例直接报告为Token节省比例。

每次只完成一个可审阅交付；每个任务先做无LLM验证，需要真实模型时限定场景与调用次数。
遇到新问题先形成可复现诊断，不自动启动多轮全量Local30。

1. 已完成：本清单，核对26个注册工具、16个本地优先入口及Judge缺口。
2. 已完成：工具覆盖登记与无LLM预检入口。机器检查注册工具漏登、未知工具、重复登记及状态是否合法；不执行业务工具，不增加题目。
3. 首个本地Runner隔离已完成：`daily_board_promotion`。下一任务先补原生列表输出的录制/回放适配，再按小批扩展；目前不算正式题目或判分覆盖。
4. 通用确定性评测器：按标量/集合/序列/统计/评级来源分小任务接入，未知断言显式弃判。
5. Judge统一接口与校准：先协议、再逐维度校准，每次限定一个维度。
6. 最小代表题：按工具逐项补齐，每项默认一个主场景；共性边界复用共享样例。
7. 统一报告：分别报告工具覆盖、实际执行、确定性裁决、Judge裁决、基础设施失败与成本。

框架完成标准：16个本地优先入口均有可执行代表场景及判分链路；数据不足的工具明确列为阻塞，
不能算通过；Judge已接入并校准；一次有界冒烟能产出完整报告。问题深度与模型稳定性后续单独扩展。

## 无LLM登记预检

在 `backend` 目录运行：

```powershell
.venv/Scripts/python.exe -m app.agent_eval preflight-tool-coverage
```

默认读取 `backend/evals/tool_coverage.json`，与生产 `TOOL_SCHEMAS` 对照。
可用 `--registry <path>` 检查其他登记表；`--output <path>` 可保存JSON报告，
父目录需已存在，已有文件不会被覆盖。

退出码0仅表示登记合法且无遗漏，2表示登记无效；不代表工具执行、质量或发布验收通过。
当前登记为本地优先3项partial、13项planned，外部依赖10项deferred。
合法状态还有blocked；partial必须声明非空coverage_refs，其余状态不声明覆盖引用。
引用仅为登记声明，本命令不验证对应测试资产，也不检查本地数据是否充足。
报告明确保留 `quality_verified=false`、`coverage_refs_verified=false`、
`release_eligible=false` 和 `data_readiness=not_checked`。

## 源码核查入口

### 首个隔离Runner

在 `backend` 目录执行（路径和日期需替换为已核实的本地数据窗口）：

```powershell
.venv/Scripts/python.exe -m app.agent_eval record-local-promotion --database <local.sqlite> --start-date 2026-09-10 --anchor 2026-09-11T18:00:00+08:00 --days 5 --output <new-report.json>
```

输出父目录需存在，已有文件不会覆盖。只读事务读取指定窗口内事件及日K，关闭数据库后，
通过生产Gateway在内存快照上执行；不初始化表、不远端补数、不调用模型。
窗口最多366天，结果天数1到60。起始日需包含计算首个晋级日所需的前一交易日。
无事件或表缺失直接报错；仅一天等无法计算的窗口输出insufficient并退出2。
有统计项退出0仅表示执行完成，不表示数据完整或答案质量通过；缺失日K沿用生产工具的缺失字段。
读取窗口不能证明交易日完整，也不能证明历史数据未被修订。

产物为 `local-tool-execution-v1`，保留原生列表payload、trace、状态、输入行摘要和校验和，
不是现有dict-only CaptureArtifact或Golden；隐私状态为unreviewed，不提交生成产物。
当前验证仅用合成测试库，正式判分与Judge尚未接入，覆盖登记保持planned。

- `backend/app/agents/tools.py`：26个工具契约、Profile、实现与数据依赖。
- `backend/app/agents/react_runtime/tools.py`：包括first_board_filter的Gateway适配。
- `backend/app/agent_eval/core_batch.py`、`historical_live.py`：当前三工具本地执行范围。
- `backend/app/services/stock_kline.py`：缺失日K采集与缓存写入。
- `backend/app/agent_eval/full_answer_judge.py`、`semantic_acceptance.py`、`formal_report.py`：Judge边界与评分。
