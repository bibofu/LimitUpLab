# 面试模拟题 — LimitUpLab 项目

> 假设你带着 LimitUpLab 去面试，我是面试官。以下是我会问的问题，
> 按我读代码后会专挑的软肋分类。每题都带「我想考察什么」，
> 你拿这文件当备考提纲。题越往后越难，最后几道是压力测试。
>
> 答不出没关系——答不出恰恰说明项目里有个你还没想透的点，
> 面试前把它想透，比答得快更重要。

---

## A. 核心定位与「研究 vs 交易」的张力

**A1.** 你 README 第一句写「不构成任何投资建议」，可你的核心产出叫
`AuctionFinalRecommendationsResponse`，字段叫 `final_score`、`final_rank`，
服务层注释写「Build the session's sole official Top10」，
`prediction_source` 填的是 `"live"`。「recommendations」「official」「live prediction」
全是最强的交易措辞，免责却只在 `chat.py:1500` 渲染分支里拼一句话。
一个不经过你渲染层、直接拿 JSON 的消费者（爬虫、未来加的纯 API、对接方），
根本看不到那句免责。**这不是名实不符吗？「研究工具」的定位和服务层的措辞，你打算怎么圆？**

> 考察：你有没有意识到产品措辞与免责边界的张力；以及免责应放在结构层而非渲染层。

**A2.** 既然是「研究工具」，为什么要做 09:25 集合竞价**终选**？
研究不需要实时性，收盘后复盘就够。你引入「盘前草稿 + 09:25 终选 + 不可变快照」
这套时间契约，到底是研究需要，还是产品演示需要？如果是后者，你敢不敢在面试里明说？

> 考察：研究 vs demo 的边界。敢承认「这也是产品展示」比硬撑「纯研究」更可信。

**A3.** 你说「不提供买入、卖出、仓位、目标价或收益承诺」。那 `final_score` 高的股票，
用户是不是更倾向于买？你产出的 Top10 排序，**事实上**就是一份买入优先级清单。
你在措辞上免责了，在功能上做了推荐的事。**你怎么向一个较真的面试官解释这个区别？**

> 考察：功能实质 vs 文字免责的辨析能力。

**A4.** 你的项目名叫 LimitUpLab——「实验室」。但你有 Docker 部署、Nginx、HTTPS、
备份 cron、限流、匿名会话。这是一个能上生产的产品。**「Lab」这个名字，
现在还诚实吗？还是它已经是一个披着 Lab 外衣的准交易系统？**

> 考察：项目自我认知是否跟演化同步。

---

## B. 统计与量化方法（最会被深挖的一块）

**B5.** 你的诊断模块用日期阻断 + Bonferroni + 符号翻转检验证明：14 个因子在 20 个
outcome-ready 交易日上**无信号**（`verdict=no_robust_signal`，OOS R²=0.014）。
但你在**同期**又写了三套新的手写评分规则：`first_board_discovery._recall_score`、
`auction_final_recommendations.score_auction_fact`、`recommendation_intelligence`。
**你在用严谨方法证明旧规则没用，然后用同样不严谨的方法写新规则。**
这两件事放在同一个项目里，你不觉得精神分裂吗？

> 考察：最核心的张力。诊断的严谨应该反哺到新规则上，而不是只用来给老规则写墓志铭。

**B6.** 20 个交易日。联合 Lasso 留一 OOS R² = 0.014，上一轮还是 0.034——**在恶化**。
你的 Bonferroni α 是 0.05/14 = 0.00357。20 天的符号翻转检验，功效有多低你算过吗？
你有没有可能**正是因为样本太小而没发现信号**，而不是真的没信号？
你的 caveats 写了「未发现信号 ≠ 已证噪声」，但这只是免责——**你做了什么去区分「功效不足」和「真无信号」？**

> 考察：统计功效（power）的概念；能不能区分「无证据」和「证据为否」。

**B7.** `sector_relay`（板块强度与接力）两轮评审都是最强单因子，方向**稳定为负**，
平均日 IC=−0.23，未校正 p=0.004。负方向 + 最接近显著 = **你的因子方向可能标反了**。
你做了什么去验证这个？如果它真的标反了，你的 14 因子评分体系有一个是反着用的，
你敢不敢在面试里说「我怀疑 sector_relay 方向是反的，但还没查」？

> 考察：敢不敢面对自己代码里可能的错误；负 IC 的解读。

**B8.** 你的 Lasso `alpha_fraction=0.1` 是怎么定的？换 0.05 或 0.2，保留因子数从 11 变几？
你做过 alpha 敏感性分析吗？如果「保留 11/14」这个结论换个 alpha 就崩，
你这篇诊断的结论还站得住吗？

> 考察：超参数的稳健性；有没有做扰动检验。

**B9.** 你用 Spearman IC 做横截面相关。但你的因子分来自手写点规则，
大量因子在同一天**取值相同**（caveats 自己写了「缺失 enrichment 时截断为常数」）。
Spearman 对常数列返回 None，你用 `SINGLE_FACTOR_MIN_SAMPLE` 跳过。
**你每天实际进入 IC 计算的样本，占当天总候选的比例是多少？**
如果一大半因子因为取值相同被跳过，你的「14 因子」其实每天只有几个在算，
你的「14 因子无信号」结论是不是被稀释了？

> 考察：数据质量对统计结论的污染；有效样本量。

**B10.** Bonferroni 校正假设 14 个检验是「family」。但你的 14 个因子**彼此相关**
（同一批股票，多个量价衍生）。Bonferroni 在相关检验上是**过度保守**的。
你选 Bonferroni 而不是 BH(Benjamini-Hochberg) 或有效检验数校正，
是因为保守更安全，还是因为没想过这层？

> 考察：多重检验校正的选择是否经过思考；保守 vs 恰当。

**B11.** 你的 bootstrap 保留率阈值定 0.7。`bootstrap_max_retention=0.995`——
几乎所有因子在 bootstrap 下都被保留。这说明你的 Lasso **几乎没有选择能力**，
alpha 太小，等于没正则。你看到 0.995 这个数字，第一反应是什么？
你有没有想过你的 Lasso 其实退化成了 OLS？

> 考察：能否读懂自己产出的数字；0.995 保留率是「太稳定」的红旗。

**B12.** 你说 OOS R² = 0.014。但你的 outcome 是「次日开盘到收盘」涨跌幅，
这是一个**均值接近零、噪声极大**的量。R²=0.014 在这种 target 上意味着什么？
你的模型解释了 1.4% 的方差，剩下的 98.6% 是噪声——**这个模型有任何实用价值吗？**
还是它唯一的诚实之处就是证明了自己没用？

> 考察：对低 R² 的诚实解读；「证伪工具」的价值辩护。

**B13.** 你跑诊断时 `random_seed=13`。换个 seed，p 值会变多少？
符号翻转 4096 次，你的 p 值精度上限是 1/4096 ≈ 0.00024。
你报 p=0.004 时，这是真精度还是你给了个超过精度的有效数字？

> 考察：置换检验的精度上限；随机性的可复现性。

**B14.** 你有一个 champion/challenger 评分策略治理，晋升闸是 60 个 outcome-ready 交易日。
你现在只有 20 天。**也就是说这个治理机制从建立到现在，一次晋升都没真正发生过。**
你写了一套从没跑通过的 governance，它现在的价值是什么？
你怎么证明它「设计上是对的」，而不仅仅是「看起来很专业」？

> 考察：governance 在空转时的价值辩护；设计 vs 验证。

---

## C. LLM Agent 工程

**C15.** 你的 Agent 是 Planner LLM 产出 JSON 工具调用计划，normalize 后 cap 在 6 次调用，
再过 14 条 Tool Policy 修复规则，再执行 17 个工具，最后 answer LLM 出 SSE 流式回答。
**这套链路里，哪一环是真正的瓶颈？**如果你只能优化一处，你优化哪？为什么？

> 考察：对自家系统性能瓶颈的认知；能不能说出 LLM 延迟 vs 工具延迟 vs 策略修复。

**C16.** 你 cap 在 6 次工具调用。如果一个用户问题需要 8 次调用才能答全，
你的系统会怎样？截断？降级？你测试过「调用数刚好被砍」时的回答质量退化吗？

> 考察：cap 的副作用；边界行为。

**C17.** 你的 14 条 Tool Policy 修复规则是「保证最低证据」的。给我举一条——
它修复的是什么 failure mode？如果这条规则**误触发**了（用户本来不需要那个证据，
规则硬塞进去），回答会怎样？你有没有规则误触发的测试用例？

> 考察：策略规则的假阳性；规则是否有「不该触发时触发」的测试。

**C18.** 你的 answer LLM 如果截断或幻觉，你有一个 post-hoc recompute 机制，
用确定性模板替换 LLM 输出。**触发条件是什么？你怎么判断「LLM 截断了」vs「LLM 答对了但不完整」？**
这个判断本身是启发式的还是可证明的？误判代价是什么？

> 考察：模板回退的触发逻辑；误判风险。

**C19.** 你用 DeepSeek 兼容的 Chat Completions。如果你的 LLM provider 明天挂了，
你的 `DisabledLLMProvider` 会 raise，整个 Agent 降级。**降级后用户看到的是什么？**
是报错、还是模板回答、还是空？你测试过 provider 不可用时用户体验吗？

> 考察：优雅降级；provider 故障的 UX。

**C20.** 你有 Explanation、Critic、Review、Evaluation 几个轻量 Agent 角色。
**Critic 和 Review 的区别是什么？**它们会互相否决吗？
如果 Critic 说「这个回答有问题」而 Review 说「没问题」，谁说了算？

> 考察：多角色职责划分的真实意义；冲突解决。

**C21.** 你的 `eval_runner` 之前有个 bug：`os.environ["LIMITUPLAB_FORCE_TEMPLATE_ANSWER"]`
在 try/finally 里逐用例改写，单 worker 下会污染并发的真实 chat 请求。你后来用
`contextvars.ContextVar` 修了。**给我讲讲 contextvars 的语义——为什么它能解决这个问题，
而 os.environ 不能？在 async 下的行为你确认过吗？**

> 考察：contextvars vs os.environ；async 下的正确性。

**C22.** 你有 11/11 离线 Agent eval 通过。这 11 个用例是怎么选的？
**它们覆盖了你 Agent 能处理的多少种问题类型？**有没有一类问题是「eval 全绿但线上会挂」的？
你怎么防止 eval 过拟合到这 11 个固定用例？

> 考察：eval 集的代表性；eval 绿 ≠ 线上稳。

---

## D. 系统设计与可扩展性

**D23.** 你用 SQLite。13 张表，无 ORM，手写 SQL，lazy init。
**SQLite 的 WAL 模式你开了，但并发写在多 worker 下还是会锁全库。**
你的部署是单 uvicorn worker。如果明天你要上 4 个 worker，你的数据层撑得住吗？
你已经做了哪些「并发加固」，哪些还欠？

> 考察：SQLite 的真实并发边界；单 worker 的隐含假设。

**D24.** 你有 17 个工具、14 条策略规则、6 次 cap、SSE 流式、多会话持久化。
整个 Agent 的状态在请求间是**有状态**的（会话历史、限流计数、usage 账本）。
**你是怎么在无状态 HTTP + SQLite 的组合下维持会话状态的？**
会话历史存哪？多大？长会话会不会把 SQLite 撑爆？

> 考察：会话状态设计；长会话的存储增长。

**D25.** 你的 `finalize_auction_recommendations` 在「已有当天快照」的分支里，
`get` 命中后**触发了一次 `_persist_relay_final_prediction(..., replace=True)`**——
一个读操作有写副作用，而且是 `replace=True` 覆盖。**你为什么要让一个 getter 写库？
这个幂等重放是故意的还是历史包袱？如果是故意的，为什么不在调用方显式做？**

> 考察：读函数写副作用的代码气味；最小惊讶原则。

**D26.** 你有 `SQLiteFirstBoardRepository`、`SQLiteFirstBoardDiscoveryRepository`、
`SQLiteAuctionFinalRepository`、`SQLiteRecommendationIntelligenceRepository`、
`SQLiteScoringPolicyRepository`、`SQLiteAgentUsageRepository`、`SQLiteReviewSnapshotRepository`
等一堆 repository，全共享同一个 SQLite 文件。**为什么不分库？**
多个 repository 写同一个文件，在 WAL 下并发表现你测过吗？

> 考察：单文件多表的并发；分库的 trade-off。

**D27.** 你的 `first_board_discovery` 用 `ThreadPoolExecutor(max_workers=8)` 并发拉 K 线，
`recommendation_intelligence` 也用 `ThreadPoolExecutor(max_workers=6)`。
**这些线程池的生命周期谁管？**每次请求都新建一个？有没有连接泄露或线程泄露的风险？

> 考察：线程池生命周期；资源泄露。

**D28.** 你的 API 有 `/api/agents/eval` 这个端点，暴露了离线 eval 的触发。
**这个端点在生产环境是开的还是关的？**如果开着，任何人都能触发一次完整 eval 跑批，
消耗 LLM token 和算力。你有没有对它加 admin key 守卫？

> 考察：内部端点的暴露面；admin 守卫的覆盖度。

---

## E. 数据工程与质量

**E29.** 你的数据源是 AKShare + 同花顺。**AKShare 的接口稳定性你评估过吗？**
如果 AKShare 明天改了字段名或限流，你的流水线会怎样？静默失败还是显式报错？
你有没有数据源的 schema 校验？

> 考察：外部依赖的健壮性；静默失败。

**E30.** 你说 D+1 到 D+5 回填。**今天是 9 月 1 日，你最近一次成功回填是哪天？**
如果过去 3 天的回填都静默失败了，你的诊断和复盘用的是**残缺的 outcome**，
你的「无信号」结论可能只是因为「一半股票没 outcome」。
你怎么监控回填是否真的在跑？

> 考察：数据管道的实际运行状态；静默失败对结论的污染。

**E31.** 你的 `build_first_board_ratings` 有硬过滤：`board_height==1`、`closed`、
非 ST、非 BSE、非 STAR、上市≥120 天、成交额≥1 亿。**这些阈值是怎么定的？**
1 亿是拍脑袋的还是市场实证的？如果阈值偏紧，你把一些真有信号的股票过滤掉了，
你的诊断永远看不到它们——**幸存者偏差你怎么控制？**

> 考察：过滤阈值的依据；选择偏差。

**E32.** 你有 `three_day_open_to_close_pct` 作为备选 outcome measure，
诊断支持 `--outcome-measure three_day_open_to_close_pct`。
**你跑过三日口径吗？结论和次日口径一样吗？**如果三日有信号而次日没有，
你的「无信号」结论是不是口径选择的结果？

> 考察：多口径交叉验证；结论对口径的稳健性。

**E33.** 你的 K 线数据有 `_normalize_snapshot_volume`——当竞价快照的 volume
和 K 线 lots 差 20 倍以上时除以 100。**这是一个 hack 还是有依据？**
你怎么知道是「单位不同」而不是「数据错误」？误修正的代价是什么？

> 考察：数据清洗的启发式是否可解释；误清洗风险。

**E34.** 你的 `first_board_discovery._eligible_snapshot` 过滤条件里有
`change_pct < 9.5`（主板）或 `< 19.5`（创业板）。**这意味着你已经排除了当天涨停的股票——**
但你的项目叫「涨停板」研究。**用「没涨停的股票」去预测「次日是否涨停」，
你的正样本从哪来？你是在预测「涨停」还是「涨幅排序」？**

> 考察：discovery 的目标定义；首板挖掘的正负样本边界。

---

## F. 诚实性与 claim 验证

**F35.** 你的 README 写「314 项后端测试通过」。**你在面试时敢不敢现场 `pytest -q` 跑一遍？**
如果跑出来有 fail，你怎么解释？你的测试有没有「在我机器上能过」的环境依赖？

> 考察：claim 的可现场验证性；测试的环境依赖。

**F36.** 你说「评分 v3 工程实现已完成，影子验证中」。**影子验证的结果是什么？**
v3 比 v2 好吗？好多少？用哪个指标？如果 v3 没比 v2 好，你为什么说「已完成」？

> 考察：「完成」的定义；v3 的实证依据。

**F37.** 你的 `Tasks.md` / 文档里写了多少个「已完成」？**逐条数，哪些有数据支撑，哪些只是代码写完了？**
「代码写完」和「功能验证过」是两件事。你的文档区分这两者吗？

> 考察：完成度的诚实标注；文档的可信度。

**F38.** 你的诊断 caveats 写得很诚实——「未发现信号 ≠ 已证噪声」。
**但你的产品页/前端，有没有同等诚实的「当前 20 天、无信号」展示？**
如果用户打开前端只看到 Top10 排名，看不到「这套评分还没被证明有效」，
**你的免责是给用户看的，还是只是给自己看的？**

> 考察：诚实是否传递到用户触达的界面；免责的受众。

**F39.** 你在 `claudeAdvice.md` 里记录了 Claude 对你项目的批判性评审——包括
「言行张力」「magic numbers 无验证」「getter 写副作用」。**你把这些写进文档，
是想展示自我反省，还是你已经知道有问题但选择不修？**
「知道问题」和「修了问题」中间隔着什么？

> 考察：评审文档是展示还是行动；知行合一。

**F40.** 你的 `V1.1_Milestone.md` 第 5 节写「V1.2 优先继续做可验证，而不是继续堆功能」。
**但 V1.1 这段提交是 +12799 行功能堆叠。**你写了一句话说要转方向，
然后做了和这句话相反的事。**你打算怎么向面试官解释「文档说要 A，代码在做 B」？**

> 考察：方向承诺与执行的落差；最刺眼的一条。

---

## G. 代码细节与实现

**G41.** `_theme_matches_news` 里硬编码了 7 条别名映射（ai→人工智能、芯片→半导体……）。
**任何含 "ai" 这两个字母的英文标题都会匹配「AI 概念」。**
你测过误匹配率吗？你有没有一条「不该匹配但匹配了」的测试用例？

> 考察：硬编码规则的假阳性；测试覆盖。

**G42.** `score_auction_fact` 里竞价涨幅区间是 `1≤pct<5→10分`、`5≤pct<8→7分`、
`8≤pct<9.5→3分`、`≥9.5→0分`。**9.5 以上给 0 分的逻辑是什么？**
「接近一字涨停」就一定差吗？你这个判断有任何数据支撑，还是直觉？

> 考察：阈值规则的依据；9.5 这个数字从哪来。

**G43.** 你的 `_recall_score` 里 momentum = `max(0, 24 - |change_pct - 4| * 3)`。
**为什么是 4？为什么是 24？为什么是 3？** 这三个魔法数字，
你能给每个一个比「感觉」更强的理由吗？

> 考察：魔法数字的可解释性；能不能说出每个常量的来源。

**G44.** `AUCTION_BASE_WEIGHT = 0.8`——final_score = preauction_score * 0.8 + auction_score。
**为什么是 0.8 不是 0.5？**你的 auction_score 上限是 20，preauction_score 上限是多少？
这个权重让谁主导终选？你做过「换 0.5 / 0.65 / 0.9 看 Top10 变化」的敏感性分析吗？

> 考察：融合权重的依据；敏感性。

**G45.** 你的 `_rating_for_score` 在 auction 模块是 `≥85→A, ≥75→B, ≥65→C`，
而 `first_board` 的 `_rating` 是 `≥80→A, ≥65→B, ≥50→C`。**两套评级阈值不一样。**
同一个项目里，A/B/C 的含义不统一，这是有意的还是疏忽？

> 考察：跨模块的评级一致性；隐含的上下文。

**G46.** 你的 `confidence` 计算：`min(0.82, 0.52 + min(len(bars), 60)/300)`。
**上限 0.82、起点 0.52、斜率 1/300——这三个数字你调过吗？**
一个有 60 根 K 线的股票 confidence 是 0.72，一个有 6 根的是 0.54。
**你凭什么说「数据多的股票更可信」——在 20 天无信号的情况下？**

> 考察：confidence 的构造；无信号时给高置信度的矛盾。

---

## H. 测试与质量保证

**H47.** 你有 316 个测试。**其中有多少是「planted signal」的合成数据测试，多少是真实数据测试？**
如果 90% 的测试都在人造数据上绿，真实数据上无信号，**你的测试证明了什么？**
「测试覆盖了代码路径」和「验证了业务假设」是两件事——你的测试覆盖的是哪件？

> 考察：合成测试 vs 真实验证；测试证明了什么。

**H48.** 你的 `test_diagnostic_detects_planted_signal` 种了 12 天 × 10 只的完美单调信号，
断言 `mean_daily_ic > 0.5`。**这个测试只证明「有强信号时能检出」。
但你的真实场景是「弱信号 / 无信号」——你的测试有没有「弱信号该检出但漏了」的反例？**
只有正例没有反例的测试，给了你多少安全感？

> 考察：测试的正反例平衡；假阴性有没有被测。

**H49.** 你的 `test_backup_database` 之前在 Windows 上挂——`chmod(0o600)` 在 Windows 是 no-op。
**你修复的方式是加 POSIX 守卫。** 但这暴露了一个更深的问题：
**你的测试套件是在 Windows 上开发的，生产部署在 Linux。** 你有多少测试的 pass/fail
依赖平台？有没有「Windows 绿、Linux 红」或反向的隐患？

> 考察：开发-生产平台差异；测试的跨平台可信度。

**H50.** 你跑 `pytest` 全绿。**但你的测试有没有跑过真实 LLM？**
如果所有 Agent 测试都用 `DisabledLLMProvider` 或 mock，
**你的 LLM 链路在生产中真正跑通的证据是什么？**「测试绿」和「LLM 能用」之间差了什么？

> 考察：LLM 链路的真实集成测试；mock vs 真实。

**H51.** 你的 `code-review` / `simplify` 技能、`claudeAdvice.md` 评审、
Claude 作为「codex 批判者」的角色——**这些是开发辅助，还是项目本身的一部分？**
面试官问「这个项目哪些是你做的、哪些是 AI 做的」，你怎么答？
**如果答案是「大部分是 AI 写的」，那这个项目证明的是你的什么能力？**

> 考察：AI 辅助开发时代的作者性；你能不能说清自己的贡献。

---

## I. 部署与运维

**I52.** 你有 Docker、Nginx、HTTPS、持久化卷、备份 cron、logrotate。
**这些你实际部署过吗？在哪？**「写了 Dockerfile」和「在生产跑过」差多远？
你有没有一次「真的部署、真的被访问、真的出过问题、真的修过」的经历可讲？

> 考察：部署经验的真实性；有没有真生产故事。

**I53.** 你的备份 cron 是 `deploy/cron/limituplab-backup`。**备份频率？保留多久？
你恢复过一次吗？**「有备份」和「备份能恢复」是两件事。你做过恢复演练吗？

> 考察：备份的可恢复性；灾难恢复实操。

**I54.** 你有 Agent 限流：per-owner 8/分钟 60/天、per-IP 20/分钟、全局并发 4。
**这些数字怎么定的？** 60/天意味着一个用户一天最多问 60 次——
对一个「研究工具」够吗？你有没有遇到过真实用户被限流卡住？

> 考察：限流参数的依据；用户体验 vs 成本。

**I55.** 你的日志在哪？`auction_final.log`、`backend-lhb-filter.out.log` 这些
散在 `backend/` 下的日志文件，**生产环境下是 Docker volume 还是容器内？**
容器重建后日志还在吗？你有没有集中日志方案？

> 考察：日志的持久化；容器化下的日志策略。

---

## J. 安全

**J56.** 你的匿名会话用 HMAC 签名 cookie，生产强制 `LIMITUPLAB_SESSION_SECRET` ≥ 32 字节。
**但开发环境用硬编码的 `limituplab-local-development-secret-only`。**
如果有人把开发配置带到生产（忘了设 env），你的 `is_production_environment()`
靠 `LIMITUPLAB_ENVIRONMENT` 这个 env 来判断——**如果这个 env 没设，
生产会用开发密钥签名所有 cookie。**你有没有「生产启动时强制校验 env」的机制？

> 考察：环境隔离的安全边界；默认安全 vs 默认不安全。

**J57.** 你的 admin key 校验用 `hmac.compare_digest`——好。
**但 admin key 是从 env 读的，`require_admin_access` 每次都 `os.getenv`。**
如果运维在中途改了 env，老 worker 用旧 key、新 worker 用新 key——
**你怎么热更新 admin key？还是你接受重启才生效？**

> 考察：密钥轮转；热更新 vs 重启。

**J58.** 你的 SSE 流式回答通过 HTTP。`c5a2af9` 专门修了「preserve anonymous chat sessions over HTTP」。
**HTTP 下 SSE 长连接 + cookie + 限流——你的限流是在连接级还是消息级？**
一个用户开 4 个 SSE 长连接，是不是绕过了「全局并发 4」的限制？

> 考察：SSE 下的并发与限流；连接 vs 请求。

**J59.** 你的 Agent 会调用外部工具（AKShare、同花顺、财经新闻）。
**用户能通过 prompt 注入让 Agent 调用不该调用的工具吗？**
你的 Tool Policy 是修复「证据不足」，但它防不防「恶意调用」？
比如用户让 Agent 去拉一只特定股票然后输出到回答里——有信息泄露风险吗？

> 考察：prompt 注入；工具调用的越权。

---

## K. 前端

**K60.** 你的 `App.tsx` 是 3200 行单文件，无状态库、无代码分割、手写 SSE 解析。
`loadDashboard()` 用 `useEffect(() => void loadDashboard(), [])`，**没有 `AbortController`**。
**快速切换面板时，旧请求的 `setState` 在卸载后还会触发——你测过这个 race 吗？**
React 18 严格模式下 effect 跑两次，你的 `loadDashboard` 跑两次会怎样？

> 考察：React 并发安全；卸载后 setState。

**K61.** 你用 `lightweight-charts` 画 K 线。**这个库的性能上限你测过吗？**
100 只股票 × 60 根 K 线同时渲染，帧率多少？你的前端有没有「数据多了就卡」的隐患？

> 考察：图表库的性能边界；大数据量前端。

**K62.** 你的前端直接 `fetch` 后端，SSE 手写解析。**你没有用 EventSource API？**
为什么？手写 SSE 解析的 `data:` 分帧、`retry` 字段、`event:` 类型——你都处理了吗？
漏处理哪个会出什么问题？

> 考察：SSE 协议的完整实现；手写 vs 标准 API。

---

## L. 价值与「so what」

**L63.** 最根本的问题：**这个项目证明了什么？**
不是「我做了什么」——是你做完之后，**关于 A 股首板预测，我们知道了一件之前不知道的事吗？**
如果答案是「没有信号」，那这个项目的价值是「负面的」（证伪了一个假设）——
**一个证伪项目，值得作为面试主项目吗？**你怎么 sell 一个「结论是没用」的项目？

> 考察：项目的认识论价值；证伪作为成果的辩护。

**L64.** 你花了这么大功夫做了 Agent、评分、诊断、部署——**如果明天出了一套开源的
A 股首板预测工具，准确率明显更高，你的项目还有什么独特价值？**你的护城河是什么？
数据？Agent 工程？还是「我诚实地承认没有信号」这种稀缺态度？

> 考察：项目的不可替代性；诚实的竞争力。

**L65.** 你说这是「Agent 工程实践和求职展示」。**那从面试官角度，我该重点看哪一块？**
如果我招的是后端工程师，你的 Agent + SQLite + 限流有看头；
如果我招的是量化研究员，你的诊断方法论有看头，但「20 天无信号」让我犹豫；
如果我招的是全栈，前端 3200 行单文件是减分项。**你的项目在我这个岗位的 JD 下，
哪 30% 是亮点、哪 30% 是雷区？你自己标一下。**

> 考察：自我认知；按岗位重新定位项目。

**L66.** 你的 `V1.1_Milestone.md` 第 6 节写「它还没有解决准确预测市场这个困难问题，
但已经把预测发生的时间、输入证据、正式版本和后续评价组织成了可以持续验证的工程闭环」。
**这句话是你整个项目的卖点。但你只有 20 天数据——「持续验证」目前是 1/3 个验证周期。**
你的「工程闭环」是一个还没转完的环。**你打算在面试里怎么 present 一个
「设计很对、数据不够、结论是不知道」的项目？**这不是反问——这是你真要面对的表述难题。

> 考察：把「未完成」说成「进行中」而非「失败」的叙事能力。

---

## M. 未来与方向

**M67.** 你说 V1.2 要「为首板挖掘建立独立 Outcome」。**为什么 V1.1 发布时 discovery 的 Outcome 还没建？**
你做了一个没有结果追踪的预测系统，这和「发了推荐但不跟踪对错」有什么区别？
**你的「可持续验证」闭环，对 discovery 这一翼，现在是断的。你打算什么时候补？**

> 考察：闭环的完整性；discovery 的 outcome 缺口。

**M68.** 如果我给你 6 个月和 2 个工程师，你下一步做什么？**别给我 milestone 文档里的清单——
给我一个排序：如果只能做 3 件事，你做哪 3 件？** 你的排序依据是什么？
ROI 怎么算的？

> 考察：资源约束下的优先级；能不能砍。

**M69.** 你项目的真实瓶颈是「数据积累太慢，每天 1 天」。**有没有办法加速？**
接更多票源？买历史数据？用合成数据？**每个方案的代价和风险是什么？**
你为什么没在 V1.1 里做？

> 考察：数据瓶颈的破局思路；为什么没做。

**M70.** 最后一个：**如果你现在重新做这个项目，你会砍掉哪 40%？**
哪些东西是「做了但回看不值得」的？能说出要砍的东西，比能说出要加的东西，
更能证明你理解了这个项目。

> 考察：做减法的能力；对沉没成本的诚实。

---

## N. Agent 设计（AI 应用开发的核心，会被深挖）

> 这是你面试 AI 应用开发岗时最该准备好的一节。每个问题都锚在你的真实代码上：
> planner 产 strict JSON → 6 个 skill 从 markdown 加载 → 20 个 capability 契约做语义路由 →
> 18 条 repair 规则保底 → answer LLM 走 SSE → 截断时回退确定性模板。
> **没有用 MCP**（整个 backend grep 不到 mcp）——这本身是第一道题。

### N1. 整体架构与「为什么这么设计」

**N71.** 用三句话讲清楚你的 Agent 一次问答的完整数据流。然后告诉我：
**planner LLM 产出 JSON 工具计划，answer LLM 再流式生成回答——这是两次 LLM 调用。**
为什么要拆成两次？一次调用带 function-calling 不是更省延迟、更省 token？
你做了两次的理由，是「可解释性」（计划可审计）还是「能力分离」（规划 ≠ 生成）？
**这个拆分的代价你量过吗——p99 延迟多了多少？**

> 考察：能不能讲清自家数据流；两段式 planner-vs-generator 的 trade-off；
> 延迟/成本的量化意识。这是 AI 应用岗的开场必问。

**N72.** 你有一个 planner（产计划）、一个 answer LLM（产回答）、一个 Tool Policy Engine
（修计划）、一个 Query Contract（预解析）、6 个 skill、20 个 capability、18 条 repair 规则。
**这套东西加起来，是「Agent」还是「带 LLM 外壳的规则系统」？**
如果 Tool Policy 能在 LLM 漏调工具时硬塞进去，**LLM 在这里的「自主性」还剩多少？**
你管这个叫 agentic，还是 grounded-generation-with-guardrails？你怎么向面试官界定边界？

> 耟察：agent vs 规则系统的本质辨析；LLM 自主性被 guardrail 削到什么程度。
> 这是 AI 应用岗的高阶题——能不能诚实说出「这其实是个受约束的生成器」。

**N73.** 你的 planner 系统提示写 `Return only valid JSON. No markdown.`
**你没有用 provider 的原生 function-calling / tool-use API，而是让 LLM 自由产 JSON 再 `json.loads`。**
为什么？是 DeepSeek 兼容接口不支持 tool-use？还是你刻意要 provider 无关？
**自由 JSON 解析的失败率你测过吗？** LLM 吐了带 ```json 代码块、多了个尾逗号、
字段拼错了——你的容错在哪？`json.loads` 挂了你是重试、降级还是报错？

> 考察：原生 tool-calling vs prompt-to-JSON 的选型理由；
> JSON 解析容错；provider 能力边界。

**N74.** 你有 `PLANNER_DIRECT_ANSWER_INTENTS = {"capability_intro", "greeting", "smalltalk"}`
——这些 intent 直接走模板答案、跳过工具 grounding。**谁判定 intent？是 LLM 还是关键词？**
如果 LLM 把一个真问题误判成 smalltalk，用户拿到模板「我在。你可以直接问…」，
你有没有测过这个误判率？**intent 误判 = 用户被静默打发，你怎么发现？**

> 考察：意图分类的可靠性；静默降级的可观测性。

### N2. 工具调用流程

**N75.** 你的 `ensure_capability_tool_calls` 有 `max_calls` 上限。**一个 capability 可能要 4 个工具，
用户问题触发 2 个 capability——8 次调用需求撞你的上限，砍谁？砍后面还是砍证据？**
你测过「需求恰好超过上限」时回答质量怎么退化吗？被砍的工具如果是最关键的那个，
回答会不会变成无证据的幻觉？

> 耟察：调用上限的边界行为；砍调用时的优先级。

**N76.** `normalize_capabilities` 能从两条路推 capability：`skill_name` 一条，
`tool_calls` 反推一条（`TOOL_CAPABILITIES` 映射）。**两条路给了不同结果怎么办？**
LLM 选了 skill A 但 tool_calls 里放了 skill B 的工具——你信谁？
你现在 `normalize_capabilities` 先吃 skill_name 再补 tool_calls，**等于永远信 skill 不信 tool。
如果 skill 选错了呢？**这个「信 skill 不信 tool」的选择，你是有意的还是写顺手了？

> 耟察：多路路由信号的冲突解决；优先级是否经过思考。

**N77.** 你的 18 条 repair 规则，每条 `matches=lambda signals: signals.xxx`，
`repair=self._repair_xxx`。**规则的执行顺序你控制了吗？**`_rules()` 返回一个 tuple，
前面规则的 repair 可能为后面规则提供 facts（注释写了「earlier tools may provide facts for later rules」）。
**如果某条规则在它依赖的规则之前执行，会拿到空的 facts 然后 ValueError。**
你测过规则的执行顺序依赖吗？加一条新规则时，你怎么知道它该插在哪个位置？

> 耟察：规则系统的顺序依赖；隐式耦合；可维护性。

**N78.** `reconcile` 里每条规则套了 `try/except Exception`（`# noqa: BLE001`），
失败时 `_record_error` 把错误塞进 `facts[f"{rule.tool_name}_error"]`。
**然后呢？answer LLM 看到这个 `_error` 字段了吗？**如果一条关键工具 repair 失败，
answer LLM 拿不到证据却还在生成回答——**它会幻觉还是会说「拿不到数据」？**
你怎么保证「工具失败」传导到「回答降级」而不是「无证据瞎编」？

> 耟察：工具失败如何传导到生成；失败后的幻觉风险；
> 错误是否进入 LLM 上下文。这是 N2 里最尖锐的一题。

**N79.** 你的 `ToolResult`（`tools.py:106`）每个工具返回。**工具本身内部的失败呢？**
`tools.py` 里有多处 `except Exception: # noqa: BLE001`（785/974/1240/1734）——
宽口径吞异常。一个工具内部拉数据失败，返回的是空 list 还是 raise？
**raise 的会被上层 policy 的 try/except 吞成 `_error` 字段；不 raise 的返回空数据——
answer LLM 分得清「真没数据」和「拉数据失败了所以没数据」吗？**

> 耟察：工具内部错误语义；空数据 vs 失败数据的歧义。

### N3. MCP 与 Skill

**N80.** **你没有用 MCP。**整个 backend grep 不到一个 `mcp` 字样。
2025-2026 年做 Agent 不上 MCP，你的理由是什么？是「MCP 是给工具复用/跨进程的，
我工具都在同进程不需要」？还是「MCP 当时还不稳」？还是**根本没考虑过**？
如果面试官问「为什么不用 MCP 接你的 17 个工具」，你能给一个比「没必要」更强的理由吗？
反过来——**如果明天要让你这些工具能被别的 Agent 框架（Claude Code、Cursor）复用，
你现在这套自定义 capability 契约能平迁到 MCP 吗？迁移成本多大？**

> 耟察：MCP 选型的有意识决策；自定义工具协议 vs 标准协议的可迁移性。
> AI 应用岗 2026 年必问之一。N80 讲的是**内部工具层**——下面 N80b 讲的是
> **外部数据源层**，是同一主题的另一半，且比 N80 更硬。

**N80b.** N80 说的是内部 17 个工具不用 MCP。但你还有一条**外部数据源**的接入：
同花顺金融数据（`hithink_finance_collector.py`）——你用的是 `subprocess.run` 调
`hithink-finance.cmd` CLI（`:580` 的 `_invoke`、`:648` 的 `_find_executable`、
Windows 上还套了 `cmd.exe /d /s /c`），不是 MCP，也不是直接调同花顺 HTTP API。
**你另一条数据路 AKShare 是 in-process Python `import akshare`（27 个文件），
同花顺却是 out-of-process CLI——为什么两种集成模式？而且同花顺是付费 API（更稳），
AKShare 是爬虫（更抖），你把更稳的放进程外、更抖的放进程内，这个对称性怎么解释？**

> **参考答案**（这题带答案，因为代码已经能撑住标准答案——别背，讲 trade-off）：
>
> 三个层次，从浅到深：
>
> 1. **MCP 解决的是「工具跨 host 共享」**——同一个 MCP server 接给 Claude Desktop、
>    Cursor、我的 agent。我的同花顺数据**只被我自己 backend 里的 agent 消费**，
>    没有跨应用复用场景。套 MCP = 付一笔协议税（JSON-RPC over stdio + handshake +
>    `tools/list` + `tools/call`），换来的「跨应用共享」收益是零。
> 2. **CLI 给我进程隔离 = 免费的故障边界**。`:597` 的 `TimeoutExpired` 被接住、
>    标 `retryable=True`——CLI hang 了是干净的失败、可重试；in-process SDK hang 的是
>    我的事件循环。**进程边界 = 故障边界**，这是 CLI 选择里最硬的一条。
> 3. **CLI 是 vendor 原生接口**，返回稳定 JSON envelope（`_parse_envelope` 接的
>    `{ok, data, error:{code, message, retryable}}` 是同花顺自己定的契约）。auth、分页、
>    envelope 归一化 vendor 已经做了。直接调 HTTP = 把 vendor 在 CLI 里做的事在
>    backend 重写一遍，纯增 moving parts。
>
> **对称性问题的正确接法**——**别答「因为同花顺更抖所以要隔离」**（会反噬：AKShare
> 爬虫更抖，逻辑反了）。正确答案是：**我沿用每个数据源的原生接口形状**——AKShare
> 原生是 pip 库所以 in-process，同花顺 iFinD 原生是 CLI 可执行所以 out-of-process。
> 不是我在「CLI vs in-process」之间选，是**尊重 vendor 边界**。硬把任一方包成另一种，
> 都是我自己加的层、没收益。
>
> **能加分的补充**（可选，展示自省）：「真要说，更抖的 AKShare 反而更该考虑进程隔离——
> 这是我标的下一个改进点，不是现状就最优，是尊重 vendor 接口形状。」——把弱点变 roadmap。
>
> **会被继续戳的点**（提前想好）：
> - **(a) MCP 是行业收敛方向**——接法：MCP 解决的是「工具暴露给别人」，我的需求是
>   「把数据接进自己 agent」，category error；需求变成「让 Claude Desktop 也能用我的
>   首板评分工具」时上 MCP 才对——那是另一个决策。
> - **(b) 每次 `_invoke` spawn 进程的延迟**——`collect_full_market_snapshot` 分页循环里
>   每页一次 spawn。接法：日终复盘非热路径，分页 `--limit/--offset` 已用（`:308-310`），
>   不为不存在的延迟提前优化。
> - **(c) 传输层 vs 路由层是正交的两层**——MCP 给的是 transport + discovery，我的
>   `capability_contract` 给的是 routing + policy，不是替代关系。我不需要 MCP 的
>   discovery，因为我已经在更高层做了更细的语义路由。

> 考察：CLI vs MCP vs in-process 的选型判断；「进程边界 = 故障边界」；
> 「尊重 vendor 接口形状」比「统一架构」更重要的工程判断；
> 以及能不能把「两种集成模式的不对称」解释成合理而非随意——
> 尤其是接住「更稳的放进程外、更抖的放进程内」这个对称性质问。
> N80（内部工具不用 MCP）+ N80b（外部数据源用 CLI）= 集成策略全貌。

**N81.** 你有 6 个 skill，从 `SKILL.md` markdown 加载（YAML frontmatter + markdown 正文）：
`finance-news`、`stock-news`、`first-board-rating`、`limit-up-pool`、`market-environment`、`popularity`。
每个 skill 声明 `required-tools` + `examples` + `default-tool-arguments` + 正文 instructions。
**这套设计跟 OpenAI Custom GPT 的 instructions、Claude Code 的 skill 系统、
LangChain 的 Tool 概念——你的 skill 比它们多了什么、少了什么？**
你为什么要自己造一套 markdown skill 而不是直接把工具列进 system prompt？

> 耟察：skill 抽象的设计自觉；与业界方案的对标；造轮子的理由。

**N82.** `loader.py` 的 `_parse_frontmatter` 自己手写了一个极简 YAML 解析器
（只认 `indent in {0, 2}`、`:` 分隔、`#` 注释）。**为什么不用 `pyyaml`？**
是为了零依赖，还是 YAML 子集就够了？**如果有人在一个 SKILL.md 里写了 4 空格缩进或
嵌套三层，你的解析器会 `SkillLoadError` 还是静默吃掉？** skill 加载失败时
整个 Agent 启动会挂还是会降级？

> 耟察：依赖 vs 自己写的边界；解析器健壮性；加载失败的可观察性。

**N83.** skill 的 `required-tools` 和 capability 的 `required_tools`——**这是两套「工具需求」声明。**
skill 声明一次，capability 声明一次，`SKILL_CAPABILITIES` 再把 skill 映射到 capability。
**三层映射，你维护的时候怎么保证一致？**加一个工具时要改几个地方？
你有没有测过「skill 要求工具 A，但 capability 没声明 A」这种不一致？

> 耟察：多层声明的同步成本；配置漂移；一致性测试。

### N4. 语义路由与双路分类

**N84.** 你有两条路由：**LLM 推 capability（语义）+ `looks_like_*` 关键词兜底（词法）。**
`QuestionSignals.from_message` 里 `use_lexical_fallback = not capability_set`——
**只有 LLM 没给出 capability 时才走词法。** 那问题来了：既然 LLM 路由已经覆盖了，
为什么还要留一整套 `looks_like_finance_news_question` / `looks_like_stock_kline_question`
这种关键词分类器？**是给 LLM 挂掉时的兜底，还是给 eval 跑离线时省 token 用的？**
如果是兜底，你测过「LLM 给了错的 capability，词法兜底没机会纠正」这种情况吗？

> 耟察：双路由的存在理由；fallback 还是竞争；漏触发。

**N85.** `looks_like_*` 系列有 20 个函数，全是中文关键词匹配 + 正则。
`looks_like_finance_news_question` 要 `新闻/快讯/资讯/消息` 之一 + `最新/今天/近期` 之一 +
非 `个股/公司/板块`。**这套关键词阈值你是怎么调的？手测？还是跑过标注集？**
「最近这个股票有什么新闻」——既有「新闻」又有「最近」，但没有「个股」——
按你的逻辑会走 `finance_news`（全市场新闻）而不是 `stock_news`（个股新闻）。
**这是 bug 还是 feature？** 你有没有一个「路由正确率」的离线评测？

> 耟察：关键词路由的调参方法；边界 case；路由评测。
> `190cd41 harden agent semantic routing evals` 说明你做了路由评测——讲讲它。

**N86.** 你的语义路由评测（commit `190cd41`）——**它评测的是「capability 推断对不对」
还是「最终回答好不好」？**多少个用例？正负例怎么选？如果路由评测全绿
但用户实际问法没覆盖到，你怎么发现泛化缺口？**你有没有一个「线上真实问题」回流
去补评测集的闭环？** 还是评测集是开发时手写的固定 N 条、再没动过？

> 耟察：评测集的来源与演进；线上回流；泛化。
> 这是 AI 应用岗区分「做过 demo」和「做过产品」的分水岭。

**N87.** `capability_contract.py` 里 20 个 capability，每个 `planner_payload()` 只暴露
`name` + `description` + `required_evidence`（工具名）给 LLM。**你把工具的
`default_arguments` 藏起来了，只给 LLM 看「需要哪些工具」不给「怎么调」。**
这是为了减少 LLM 出错？**但 `ensure_capability_tool_calls` 又会把这些默认参数硬塞进去。**
等于「LLM 选能力，参数我来定」。**这种「LLM 选菜单、后厨定做法」的设计，
你管它叫 agent 还是叫 dispatcher？** 它和让 LLM 完全控制 tool + args 的区别，
你在面试里怎么讲？

> 耟察：LLM 控制权的边界；dispatcher 模式；agent 语义的诚实界定。

### N5. Tool Policy：guardrail 还是手铐

**N88.** 你的 Tool Policy 的核心承诺是「保证最低证据」——LLM 漏调的工具，policy 硬塞。
**但反过来说：LLM 主动多调、调对了的工具，policy 会不会因为「不在 capability 的
required_tools 里」就把它们丢掉？** 看你的 `ensure_capability_tool_calls`——
它把 required 排前面，其余 `normalized` 追加在后面，最后 `[:max_calls]` 截断。
**所以 LLM 自己加的、policy 不认的工具，排队排在最后、最容易被截掉。**
这是有意的（policy 优先级 > LLM）还是副作用？如果 LLM 发现了一个 policy 没预料到的
有用工具，它会被你的系统优先砍掉——**你管这叫 guardrail 还是叫抑制了 agent 的探索？**

> 耟察：guardrail 与 agent 自主性的真实张力；优先级的副作用。
> 这是 N5 里最能体现「想过 agent 哲学」的一题。

**N89.** `requires_grounding` 拒绝 direct-answer 模式——如果问题需要本地证据但 LLM
想直接答，policy 否决。**这条「否决」是怎么传导给 LLM 的？** 是改 prompt、
还是直接覆盖计划、还是抛异常走模板？**当 policy 否决了 LLM 的判断，
LLM 知道自己被否决了吗？**还是它以为自己赢了、其实是后端偷偷改了它的计划？

> 耟察：否决的可见性；LLM 是否知道自己被 override；
> 影响可解释性。

**N90.** `_repair_*` 方法里到处是 `del request, signals, context_symbol`——
**因为你不用这些参数但函数签名要凑齐。** 18 个 repair 方法，参数签名全一样
`(request, signals, execution, context_symbol)`，但大部分 `del` 掉一半。
**这是不是一个信号——你的 repair 抽象其实不需要统一签名？** 用 `ToolRepairRule`
的 `RepairAction` 类型强行统一，代价是每个方法开头一排 `del`。你考虑过
让 repair 只拿它需要的参数吗？

> 耟察：代码抽象的过度统一；签名设计；是不是为了统一而统一。

### N6. 模板回退与生成质量控制

**N91.** 你的 `_FORCE_TEMPLATE_ANSWER_OVERRIDE` 是个 `ContextVar`，配 `template_answer_override`
contextmanager 和 `os.getenv` fallback。**触发条件是什么——什么时候你会强制用
确定性模板替换 LLM 流式回答？** eval 时强制？LLM 截断时？provider 挂时？
**「LLM 截断」你具体怎么检测？** SSE 流提前结束？内容里没有预期字段？
**检测截断的逻辑是启发式还是可证明的？误判代价是什么——把一个好回答替换成模板，
用户体验怎样？**

> 耟察：模板回退的触发；截断检测；误判的 UX 代价。
> 这题上一轮 C18 问过，这里深挖「检测逻辑」本身。

**N92.** 模板回答和 LLM 回答混在同一个 SSE 流里给前端——**前端分得清吗？**
用户看到一段回答，他不知道这是 LLM 生成的还是模板拼的。**你的回答里有没有
一个字段标记「本回答为模板回退」？** 如果没有，你的 eval 统计里
「模板回答」和「真 LLM 回答」是分开计的吗？混在一起统计会让你的「回答质量」指标
被模板拉高/拉低——**你意识到这个混淆了吗？**

> 耟察：模板 vs LLM 的可观测性；指标混淆；诚实度量。

**N93.** 你有 Explanation、Critic、Review、Evaluation 四个角色。**Critic 和 Tool Policy 的区别是什么？**
Tool Policy 在调用前保证证据；Critic 在生成后质疑可靠性。**两者会不会重复劳动——
Policy 已经塞够了证据，Critic 又来一遍？** 如果 Critic 说「证据不足」但 Policy
明明已经修过了，谁说了算？**你的 Critic 是另一个 LLM 调用吗？那又是一轮延迟和成本。**
四个角色 = 四次 LLM？还是共享一次？

> 耟察：多角色的职责去重；LLM 调用次数与成本；
> Critic 的独立价值。

### N7. Prompt 工程与成本

**N94.** 你的 planner system prompt（`_tool_planner_system_prompt`）把 tool schema、
skill schema、capability contract、agent profile 全拼进去。**这个 prompt 多少 token？**
20 个 capability 的 description + 6 个 skill + 17 个工具 schema——
**每次请求都全量发？你做了 prompt caching 吗？** DeepSeek 兼容接口支持
prefix caching，你的 system prompt 是 stable prefix 吗？如果不做 caching，
每个用户问题都付一遍这个 prompt 的 input token，**你算过月成本吗？**

> 耟察：prompt token 预算；prefix caching；成本量化。
> AI 应用岗必问的成本题。

**N95.** 你的 `capability_schema_prompt` 只把「工具可用的 capability」暴露给 LLM——
`if all(req.name in allowed_tool_names ...)`。**不同 profile 暴露不同 capability。**
那 LLM 看到的能力集是动态的。**你有没有测过「换 profile 后 LLM 路由准确率变化」？**
如果一个 capability 因为某工具被禁用而从 prompt 消失，但用户问的就是那个能力，
LLM 会判成 `out_of_scope`——**你测过这种「能力消失导致的误判」吗？**

> 耟察：动态能力集的路由稳定性；禁用工具的连锁影响。

**N96.** 你的 safety：`unsafe_investment_advice` intent + `SAFETY_BOUNDARY` 常量 +
`TEXT["unsafe"]` 模板。**「不安全投资建议」这个 intent 谁判定——LLM 还是关键词？**
`KEYWORDS` 里没有 `unsafe` 条目，看起来是 LLM 判。**那 LLM 把一个安全的「这只股票评分理由」
误判成 `unsafe_investment_advice`，用户拿到「我不能给出直接交易指令」——
你测过这个假阳性吗？** 安全误判的代价是「拒绝回答真问题」，比漏判更伤体验。

> 耟察：安全分类的假阳性；over-refusal；UX 代价。
> over-refusal 是 2026 年 LLM 应用面试的热门题。

**N97.** 你的 answer LLM 走 SSE 流式。**流式过程中，如果生成到一半 Tool Policy
才发现证据不够（比如某个工具异步返回慢），你能中断已发的流吗？**
还是前半段已经发给用户了、后半段收不回？**你的 grounding 检查是在
生成前（planner 阶段）还是生成中（流式阶段）？** 如果只在 planner 阶段，
那流式开始后就没有防护了——**你怎么防「流式过程中幻觉」？**

> 耟察：流式生成的中途防护；grounding 时机；
> 已发送内容的不可撤回性。

### N8. Agent 可观测性与可调试性

**N98.** 你的 `agent_plan` trace 把 intent、trade_date、tool_steps 全记录了。
**这个 trace 给用户看吗？** 如果用户能看到「Agent 调了哪些工具、为什么」，
这是可解释性的卖点；如果只给开发者看，那是调试日志。**你的产品定位是哪个？**
如果是给用户的可解释性，trace 里的 `policy_repair` 标记——用户看到
「这条工具是 policy 替你补的，不是 Agent 自己想的」会怎么想？

> 耟察：trace 的受众；可解释性 vs 暴露实现细节；
> policy_repair 暴露给用户是否合适。

**N99.** 你有 11/11 离线 eval。**这些 eval 跑的是真 LLM 还是 mock？**
如果是 mock（`DisabledLLMProvider` 或固定 payload），你的 eval 只测了
「路由 + 模板」这条确定性链路，没测「真 LLM 在真 prompt 下答得好不好」。
**那「LLM 真的能答好用户问题」这件事，你的证据是什么？** 11/11 绿，
绿的是哪一层——路由？工具调用？还是生成质量？

> 耟察：eval 用 mock vs 真 LLM；eval 绿证明的是什么层。
> 这题和 H50 同源，但聚焦在「eval 测的是不是 LLM」。

**N100.** 最后一题，也是 AI 应用开发岗的「能不能落地」题：
**你的 Agent 在真用户身上跑过吗？不是你自己测——是有人用它问了真问题、
你看过他们的问法分布吗？** 如果没有真用户，你的 `looks_like_*` 关键词、
capability description、skill examples 全是凭自己想象写的。
**一个没见过真用户的 Agent 系统，它的路由准确率你凭什么相信？**
这题答「没跑过真用户」不丢人——丢的是「没跑过还相信它准」。

> 耟察：真用户数据 vs 想象；路由准确率的证据基础；
> AI 应用岗区分「demo 工程师」和「能上线的人」。

---

## 附录：面试官视角说明

这份题的设计逻辑——我作为面试官，读完你的代码后会专挑的软肋：

1. **言行张力**（A/F40）：文档说研究、代码做推荐；文档说转验证、代码在堆功能。
   这是最容易被 15 分钟读完代码就抓住的矛盾。
2. **统计诚实 vs 功能膨胀**（B5/F40）：你用严谨方法证伪了旧规则，却用不严谨方法
   写新规则。同一个人在同一项目里同时严谨和草率，是最值得追问的。
3. **样本不足的辩护**（B6/B14）：20 天，governance 从未触发。你怎么辩护一个
   「设计对但没跑过」的系统？
4. **「完成」的定义**（F36/F37）：代码写完 ≠ 验证过。文档里多少「已完成」
   只是代码层面？
5. **AI 作者性**（H51）：大部分代码 AI 写的——你证明的是什么能力？
   这题在 2026 年的面试里一定会被问到。
6. **Agent 设计的哲学边界**（N72/N80/N87/N88）：不用 MCP、自定义 skill 系统、
   LLM 选菜单后厨定做法、guardrail 优先级会砍掉 LLM 自主探索——
   你的「agent」到底是 agent 还是带 LLM 外壳的规则系统？能诚实界定这条边界，
   是 AI 应用开发岗区分「做过 Agent demo」和「想清楚过 agent 架构」的分水岭。

最难的不是答对，是答得**诚实**——面试官能分辨「背了答案」和「想过问题」。
拿这 100 题逐条想过一遍，比背 10 个「标准答案」有用。
