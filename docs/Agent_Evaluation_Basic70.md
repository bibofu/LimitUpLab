# Basic70：50 Offline + 20 Live

## 目标和真实起点

最新：`output/agent-eval/basic70-runnable-001/suite.json`已合并50 Offline + 20 Live可运行资产。
新增10 Live已有标准Case/baseline及共享worker接入，10题真实工具＋脚本模型全链路通过；
不是10题真实模型质量通过。Active仍为20 Offline + 10 Live，新增40题仍待验收。
worker默认生成trace-review，显式--allow-judge才追加至多一次三维评判。下一步做有界验收，
不再逐工具扩建执行框架。以下保留此前起点与建设记录。

更新：已实现30个新增Offline候选，见 `backend/evals/suites/basic70-expansion.json`。
`output/agent-eval/basic70-candidates-003/suite.json`汇总50 Offline资产（20 Active+30 Candidate）
和10个既有Live，工具资产并集26/26。下表保留Active Golden口径，新候选不计入已批准数量。
8道候选已修正生产字段/口径并升v2；10个新增Live场景已在数据库副本完成实工具预检，
产物为 `output/agent-eval/basic70-live-readiness-003/report.json`。尚需接入Live worker、
标准Case断言和有界验收，不能将预检录制计入已可执行或已批准Live数。

目标是70个有明确判定依据的Golden场景，合计覆盖全部26个注册业务工具，
不是26份专用Runner，也不是70个只有问句的占位符。

2026-09-16逐项读取 `output/agent-eval/golden/local30-current-v6/suite.json` 及所引用资产：

| 项目 | 当前 | 目标 | 缺口 |
| --- | --- | --- | --- |
| Active Offline | 20 | 50 | 30 |
| Active Historical Live | 10 | 20 Live | 10 |
| Active题目数 | 30 | 70 | 40 |
| Golden证据涉及业务工具 | 3 | 26 | 23 |

目前三个工具是market_summary、limit_up_events、market_event_pool，仅部分参数/分支。
数量完成率42.9%不代表质量完成率；工具入口覆盖约11.5%，且不是所有工具参数覆盖。
这些active标记不代表最新runtime、新Judge已完成全套验收。不能把合成Judge校准例、
额外测试函数、daily_board_promotion执行入口或候选蓝图计入Golden数。

Live沿用项目现有Historical Live口径：真实工具执行，日期及数据基线固定。
涉及外部服务时明确依赖和日期；如果仅回放录制结果，必须归Offline。
20个Live不要求每个工具各一题，全部26工具的最低覆盖由Offline+Live并集满足。
不可将远端不可用记成通过；基线漂移、依赖失败与Agent答错分别报告。

## 数量分配：不再继续堆同类查询

保留并核验原20 Offline、10 Live。新增30 Offline分为23个缺口工具代表场景及7个共性场景。
以下为待实现分配，不是已创建或已验收的case。

| 新增Offline组 | 工具/能力 | 题数 |
| --- | --- | --- |
| 本地统计与时序 | daily_board_promotion、stock_kline、post_limit_screen、post_limit_path、post_limit_statistics | 5 |
| 评级与解释 | first_board_ratings、first_board_filter、first_board_critic | 3 |
| 评估与策略 | prediction_quality_audit、rating_backtest、rating_evaluation、review_high_score_picks、scoring_policy_status | 5 |
| 行情与板块外部依赖 | market_index_trend、sector_performance、sector_stock_ranking、hot_stock_ranking、dragon_tiger_list | 5 |
| 新闻与其他外部依赖 | finance_news、stock_news、stock_activity、remote_limit_up_pool、web_search | 5 |
| 跨工具与边界 | 两工具交叉核对、日期/范围一致性、空结果、关键字段缺失、工具错误后合理降级、来源注入防护、显式输出约束 | 7 |
| 合计新增 | | 30 |

23个代表场景优先一个明确任务、最小完整证据和少量断言；复杂工具只验证一个主路径，
不穷举参数。remote_limit_up_pool和web_search使用extended profile，不改变默认产品工具权限。
外部工具Offline可用明确标记的合成合同夹具或可追溯录制，不伪称真实在线执行。

新增10 Live本轮选用本地可执行场景：晋级统计1、K线1、涨停后筛选/路径/统计3、
评级/过滤/质疑3、审计/策略2。远端场景暂由Offline覆盖；外部Live留待后续扩充。
此调整优先完成本地依赖验证，不宣称远端工具已有Live覆盖。组合题按真实任务需要组合，
不是强制模型调用固定工具顺序；预检无数据时明确阻塞，不为了凑数改写为通过。

## Golden准入：少而准确

每题必须具备：

1. 用户问题、mode/profile、少量必须满足与禁止事项；不能强制唯一措辞或工具路线。
2. Offline完整录制/合同夹具，或Live可执行依赖和日期/来源基线；记录缺失信息。
3. 独立可复核的关键事实断言（名单/数字/时间范围/来源）和期望终态。
4. trace确定性检查及按需Judge；明确哪些断言尚不支持，未判不计作pass。
5. 经审阅的候选到Golden提升记录，不因生成成功或一次模型全绿自动晋升。

目前主要框架缺口仍包括：通用列表/时序/评级事实断言与现有结果协议兼容、
外部依赖Live接入、候选审阅提升、70题的批量汇总。统一trace入口能读工具证据，
不等于这些业务事实断言已经完成。保留旧正式评分器，不宣称实验Judge已替代它。

## 执行顺序与成本

1. 下一批先实现5个本地统计/时序Offline候选及对应小范围断言；
   优先统一录制/回放格式适配，不再逐工具建Runner。候选不计Golden。
2. 按上述组批量补剩余25 Offline，每批约5题，新增公共适配只在确有必要时做。
3. 按预检可用性分两批补10 Live，远端调用单独控制预算；无依赖的题报告阻塞。
4. 按组审阅并提升资产，最后核验50/20及26工具并集，执行一次有界验收；不追三轮全绿。

开发期间仅运行改动相关题，默认先零LLM检查。每题至多一次语义裁判、无自动重判，
确定性已完整覆盖的场景可跳过Judge；不能在断言未完成时声称已完整覆盖。
运行结果留存后复评，不为修改报告重复运行Agent。正式全量需先预估预算，再执行。

压缩目前默认关闭：2026-09-16四次合成配对调用中，负例压缩输出发生协议矛盾，
不能因字符减少而启用。基础70题建设不再被该优化阻塞。
