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

每次只完成一个可审阅交付；每个任务先做无LLM验证，需要真实模型时限定场景与调用次数。
遇到新问题先形成可复现诊断，不自动启动多轮全量Local30。

1. 已完成：本清单，核对26个注册工具、16个本地优先入口及Judge缺口。
2. 下一任务：实现工具覆盖登记与无LLM预检入口。机器检查注册工具是否全部登记、状态是否明确；不执行业务工具，不增加题目。
3. 本地Runner隔离：先接入一个新增工具的本地快照执行，验证零外部取数及生产数据不变，再按小批扩展。
4. 通用确定性评测器：按标量/集合/序列/统计/评级来源分小任务接入，未知断言显式弃判。
5. Judge统一接口与校准：先协议、再逐维度校准，每次限定一个维度。
6. 最小代表题：按工具逐项补齐，每项默认一个主场景；共性边界复用共享样例。
7. 统一报告：分别报告工具覆盖、实际执行、确定性裁决、Judge裁决、基础设施失败与成本。

框架完成标准：16个本地优先入口均有可执行代表场景及判分链路；数据不足的工具明确列为阻塞，
不能算通过；Judge已接入并校准；一次有界冒烟能产出完整报告。问题深度与模型稳定性后续单独扩展。

## 源码核查入口

- `backend/app/agents/tools.py`：26个工具契约、Profile、实现与数据依赖。
- `backend/app/agents/react_runtime/tools.py`：包括first_board_filter的Gateway适配。
- `backend/app/agent_eval/core_batch.py`、`historical_live.py`：当前三工具本地执行范围。
- `backend/app/services/stock_kline.py`：缺失日K采集与缓存写入。
- `backend/app/agent_eval/full_answer_judge.py`、`semantic_acceptance.py`、`formal_report.py`：Judge边界与评分。
