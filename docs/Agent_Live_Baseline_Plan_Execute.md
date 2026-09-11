# Agent Live Baseline：Plan-and-Execute

## 1. Executive Summary

本报告记录提交 `b76e942` 上当前 Plan-and-Execute Agent 的一次原始 Live Behavioral Eval。执行于 2026-09-11，使用 `agent-chat-live-eval-v1` 全量 36 个 case，每题 3 次，共 108 个真实 Agent trial。过程中没有修改 Planner Prompt、Tool Policy、Capability Contract、Agent 主流程、case 或 evaluator，也没有因失败选择性重跑。

| 项目 | 值 |
|---|---|
| Agent architecture | Plan-and-Execute |
| Observation-driven replan | unsupported |
| Planner / Answer provider | LangChain OpenAI-compatible provider |
| Planner / Answer model | `deepseek-v4-flash` |
| Judge | 未配置，所有 Judge 指标为 N/A |
| Dataset | `agent-chat-live-eval-v1`，36 cases |
| Trials | 3/case，108 total |
| Tool environment | 部分冻结：`SAMPLE_EVENTS` + current registry/fallback providers |
| Raw report | `output/agent-live-eval/live-20260911T080804Z-f4288138/summary.json` |
| Enriched artifact | `output/agent-live-eval/live-20260911T080804Z-f4288138/live_baseline_plan_execute_20260911T080804Z-f4288138.json` |

独立 Judge 没有配置，因此本次 task success 只代表 deterministic/behavioral contract 是否通过，不能直接等价于用户可感知答案质量。实际抽查发现，部分通过 trial 的最终答案是“抱歉，该问题无法回答”，说明无 Judge 时的通过率存在明显乐观偏差。

OpenAI Graders 将确定性检查与 model grader 作为不同 grader 类型；本报告也保持这两个层次分离，不用确定性 required fact coverage 冒充完整语义质量。[OpenAI Graders](https://developers.openai.com/api/reference/resources/graders)

## 2. Overall Results

| 指标 | 结果 |
|---|---:|
| Total cases / trials | 36 / 108 |
| Passed trials | 55 |
| Task success rate | 50.93% |
| Stable case rate（3/3） | 50.00%（18/36） |
| 至少 2/3 case rate | 50.00%（18/36） |
| 恰好 1/3 case rate | 2.78%（1/36） |
| 0/3 case rate | 47.22%（17/36） |
| Provider failure rate | 0.00% |

唯一不稳定 case 是 `LIVE-MULTI-005`（1/3）。没有 2/3 case；其余 case 都是稳定通过或稳定失败，表明本次主要问题不是随机波动，而是结构性的规划、参数、上下文和恢复能力缺口。

## 3. Results by Category

下表的 success rate 按全部 trial 计算，而不是只取每题第一次运行。

| Category | Cases | Trials | Success | Stable | Avg tools | Avg LLM | Avg tokens | Avg latency | P95 latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| simple | 6 | 18 | 100.00% | 100.00% | 1.67 | 1.83 | 4,686.94 | 3,076.67 ms | 5,352 ms |
| multi_tool | 6 | 18 | 55.56% | 50.00% | 2.44 | 2.17 | 6,860.44 | 6,139.89 ms | 16,738 ms |
| replan | 8 | 24 | 12.50% | 12.50% | 1.83 | 1.38 | 3,349.46 | 3,333.08 ms | 13,283 ms |
| multi_turn | 6 | 18 | 50.00% | 50.00% | 2.72 | 3.50 | 8,712.00 | 4,869.00 ms | 6,731 ms |
| recovery | 4 | 12 | 25.00% | 25.00% | 1.75 | 1.50 | 4,275.83 | 5,219.92 ms | 16,494 ms |
| boundary | 4 | 12 | 100.00% | 100.00% | 0.00 | 1.00 | 2,692.42 | 1,162.83 ms | 1,525 ms |
| stress | 2 | 6 | 0.00% | 0.00% | 0.00 | 0.00 | 0.00 | 0.00 ms | 0 ms |

Simple 和 boundary 的确定性契约表现稳定，但 simple 中存在通过却没有实际回答的样本；没有 Judge 时不能据此断言自然语言质量已达标。Multi-tool 只有 55.56%，也不足以称为稳定。

Stress 的 0 token、0 LLM call、0 tool call 不是“高效失败”，而是请求没有进入有效 Planner/执行链路。它揭示的是复杂请求在初始路由/规划入口即失败。

## 4. Planner Quality

| Category | Raw capability recall | Raw required tool recall | Effective required tool recall | Backend repair rate |
|---|---:|---:|---:|---:|
| overall | 74.36% | 0.00% | 77.01% | 77.78% |
| simple | 100.00% | 0.00% | 100.00% | 100.00% |
| multi_tool | 87.88% | 0.00% | 89.74% | 100.00% |
| replan | 78.57% | 0.00% | 78.57% | 75.00% |
| multi_turn | 100.00% | 0.00% | 100.00% | 100.00% |
| recovery | 100.00% | 0.00% | 100.00% | 100.00% |
| boundary | N/A | N/A | N/A | 0.00% |
| stress | 0.00% | 0.00% | 0.00% | 0.00% |

Planner 使用 `capability-first-v2`：原始 `tool_calls` 字段存在但始终为空，工具出现在 `resolved_tool_calls`，并由后端 Policy 形成最终调用。因此 raw required tool recall 的 0% 是当前 trace 契约的真实结果，但不应被误读为 Planner 完全没有能力；它说明当前不能从 raw tool plan 证明模型独立完成了工具规划。

108 个 trial 中有 84 个发生 backend repair，共记录 181 个 repair operation。Repair 最多的 case 包括：

- `LIVE-MTURN-006`：13 次 operation。
- `LIVE-MULTI-001`、`LIVE-RECOVERY-003`、`LIVE-SIMPLE-001`：各 12 次。
- `LIVE-MTURN-002`：10 次。
- `LIVE-REPLAN-006`、`LIVE-REPLAN-007`：各 9 次。

Raw 与 effective 的差异说明成功高度依赖后端工具补全；但 effective recall 仍只有 77.01%，所以 Policy 没有覆盖复杂规划缺失。

## 5. Observation-dependent Analysis

`replan + stress` 共 10 cases、30 trials，只通过 3 次：

`observation_dependent_task_success_rate = 10.00%`

该指标不是 `replan_success_rate`。所有 trial 的 `replan_count` 均为 0，真正的 observation-driven success 为 0/30。

| Case | Trials | 主要失败阶段 | 判断 |
|---|---|---|---|
| LIVE-REPLAN-001 | 0/3 | 动态候选未绑定到 `stock_kline.symbol` | tool argument dependency failure |
| LIVE-REPLAN-002 | 0/3 | capability/tool 均缺失，随后依赖失败 | initial planning failure |
| LIVE-REPLAN-003 | 0/3 | 前序行业结果未绑定到排名工具参数 | tool argument dependency failure |
| LIVE-REPLAN-004 | 0/3 | 初始新闻 capability/tool 缺失 | initial planning failure |
| LIVE-REPLAN-005 | 0/3 | 首板候选未绑定到 critic 参数 | tool argument dependency failure |
| LIVE-REPLAN-006 | 0/3 | 热股∩涨停集合未传给评分工具 | dynamic candidate discovery failure |
| LIVE-REPLAN-007 | 0/3 | 条件 fallback 顺序正确，但龙虎榜 query 未绑定前序股票 | partial branch success, argument failure |
| LIVE-REPLAN-008 | 3/3 | 条件未命中，但静态计划仍调用了 optional branch tool | preplanned-success / over-calling |
| LIVE-STRESS-001 | 0/3 | 没有 Planner trace、LLM 或工具调用 | pre-planner / initial planning failure |
| LIVE-STRESS-002 | 0/3 | 没有 Planner trace、LLM 或工具调用 | pre-planner / initial planning failure |

`LIVE-REPLAN-008` 不能视为 observation-driven success：Planner 在首次计划就包含 `sector_performance` 和 `sector_stock_ranking`；三个 trial 的内容条件均未匹配，但 optional branch 仍被调用。它是典型 `preplanned-success`。

主要证据支持“部分任务需要 Observation → bounded Replan → New Tool Calls”：动态候选参数、集合交集、empty/partial 后的 fallback 无法只靠静态空参数工具序列可靠完成。但 Stress 和若干 Replan case 在进入观察循环之前已经失败，单独加入 Replan 不能解决入口路由和 capability 识别问题。

## 6. Multi-turn Analysis

Multi-turn 总成功率为 50.00%（9/18），稳定 case 为 3/6。

| 能力 / Case | 结果 | 失败层 |
|---|---:|---|
| Entity carry-over / LIVE-MTURN-001 | 0/3 | `stock_news.symbol=None`，context/tool args |
| Previous result set / LIVE-MTURN-002 | 3/3 | PASS |
| Pronoun resolution / LIVE-MTURN-003 | 0/3 | 后续工具有执行，但最终答案缺少风险说明 |
| Date modification / LIVE-MTURN-004 | 3/3 | PASS |
| Previous-set filtering / LIVE-MTURN-005 | 3/3 | PASS |
| Three-turn continuity / LIVE-MTURN-006 | 0/3 | 工具链不稳定，最终答案缺少风险说明 |

当前 memory/context 并非整体失效：集合与日期修改稳定通过；实体继承和三轮连续性仍是明确弱项。

## 7. Recovery Analysis

`recovery_success_rate = 25.00%`（3/12）。

| Recovery type | Case | 结果 | 观察 |
|---|---|---:|---|
| error | LIVE-RECOVERY-001 | 3/3 | 明确表示“无法”，没有把错误包装成事实 |
| empty | LIVE-RECOVERY-002 | 0/3 | 输出通用“无法回答”，没有明确披露 empty |
| partial | LIVE-RECOVERY-003 | 0/3 | 注入前底层工具已变成 error，未得到预期 partial；回答虽披露缺失，但契约失败 |
| single candidate error | LIVE-RECOVERY-004 | 0/3 | 首个候选失败后没有继续完成 `600000` 的可完成部分 |

Recovery 最大问题是缺少基于 Observation 的继续执行，以及 empty/error/partial 状态表达没有稳定区分。`LIVE-RECOVERY-003` 还受到部分冻结环境、错误工具参数和外部 provider 失败影响。

## 8. Grounding and Answer Quality

| 指标 | 结果 |
|---|---:|
| Required fact coverage | 100.00%（6/6 assertions） |
| Unsupported claim rate | N/A |
| Judge pass rate | N/A |
| Judge average score by dimension | N/A |

Required fact coverage 只验证少量 expected deterministic facts 是否出现在答案中，不是完整 groundedness 或 hallucination rate。只有 6 个 assertion，覆盖范围很窄。

Judge 未配置，所以 completeness、clarity、relevance、task resolution、risk explanation、failure transparency、uncertainty disclosure、boundary compliance 均为 N/A。抽查显示 `LIVE-SIMPLE-001`、`LIVE-SIMPLE-004`、`LIVE-MULTI-004` 等存在 deterministic PASS 但回答是通用拒答的情况，未来正式发布门禁不能缺少已校准 Judge 或更强 Answer Contract。

## 9. Efficiency

| 指标 | 结果 |
|---|---:|
| Avg / P95 tool calls | 1.74 / 4 |
| Avg LLM calls | 1.83 |
| Avg / P95 tokens | 4,895.14 / 11,912 |
| Avg latency | 3,797.47 ms |
| P50 / P95 latency | 2,500 / 15,737 ms |
| Total LLM calls | 198 |
| Total tool calls | 188 |
| Total tokens | 528,675 |

96/108 trial 有完整 token usage；12 个没有模型调用，因此 token/model 字段为空。当前 capture 只提供 Agent 总 token，无法可靠拆分 Planner 与 Answer token；Judge 未运行，Judge token/cost 为 N/A，且没有混入 Agent 指标。

## 10. Failure Root Causes

以下 count 为受到该根因影响的 trial 数；同一 trial 可进入多个根因，因此不能相加为总失败数。

| Root cause | Failed trials | Affected cases | Representative example |
|---|---:|---:|---|
| Answer completeness | 27 | 9 | 缺少风险、实体、empty/partial 披露或可完成候选 |
| Tool argument | 18 | 6 | 动态股票/行业未从前序 Observation 绑定到后续参数 |
| Planner / intent | 14 | 5 | 缺少 required capabilities，Stress 没有进入 Planner |
| Tool selection | 14 | 5 | required tools 未执行 |
| Observation dependency | 6 | 2 | 多来源交集没有传入目标工具 |
| Efficiency / budget | 3 | 1 | `LIVE-MULTI-006` 使用 4 次 LLM，超过 2 次预算 |
| Tool failure recovery | 3 | 1 | 预期 partial，实际底层工具 error |

最大瓶颈不是单一模块：动态参数/Observation 依赖是 Replan case 的核心瓶颈；复杂意图入口、后端修复依赖、答案完成度和 multi-turn 实体延续同样显著。

### Top failing cases

以下 17 个 case 均为 0/3：

- `LIVE-MULTI-002`、`LIVE-MULTI-006`
- `LIVE-REPLAN-001` 至 `LIVE-REPLAN-007`
- `LIVE-MTURN-001`、`LIVE-MTURN-003`、`LIVE-MTURN-006`
- `LIVE-RECOVERY-002`、`LIVE-RECOVERY-003`、`LIVE-RECOVERY-004`
- `LIVE-STRESS-001`、`LIVE-STRESS-002`

### Most unstable cases

- `LIVE-MULTI-005`：1/3。
- 2/3：无。

## 11. Current Architecture and Baseline Limitations

- 当前没有 Observation→Planner 循环；`replan_count=0` 是事实，不应生成 replan success 指标。
- Capability-first Planner 的 raw `tool_calls` 为空，工具几乎全部由后端 resolve/repair，限制了 raw tool planning 指标的解释力。
- 工具世界并未完全冻结。运行中观察到本地 hithink-finance DuckDB 文件占用、上游 429、代理连接错误及现有 fallback；新闻/热榜还返回了 2026-09-11 的当前数据，而 case anchor 是 2026-05-15。这降低了未来跨版本 A/B 的可比性。
- 后续修复：`agent-live-eval-runner-v4` 已改为专用的 `chat-live-world-v2-fully-frozen-v2`，初始调用与 Policy repair 均禁止访问数据库和网络，并在加载时验证评测世界内部一致性。由于评测环境发生实质变化，本页旧基线保留为历史诊断，不作为新环境的对照基线；需先执行 36×1 预检，再重新执行 36×3。
- Trace 能记录总 token 和 latency，但不能拆分 Planner/Answer token；无 LLM 调用的 trial token completeness 为 false。
- Judge 未配置，deterministic PASS 会漏掉通用拒答、解释质量和答案相关性问题。
- Unsupported-claim evaluator 尚不存在，无法报告 hallucination rate。

## 12. Recommendation

### Q1：simple / multi-tool 是否足够稳定？

Simple 的 deterministic contract 为 18/18，boundary 为 12/12；但抽查发现 simple 存在无效通用回答，且 Judge 缺失，因此只能说工具契约稳定，不能说答案质量稳定。Multi-tool 为 10/18、稳定 case 3/6，明确不够稳定。

### Q2：observation-dependent / stress 的主要失败原因是什么？

核心是动态候选、行业和集合没有绑定到后续工具参数；其次是复杂请求没有进入有效 Planner/工具链。唯一通过的 Replan case 是首次静态计划提前调用 optional tool 的 `preplanned-success`，不是真实 Replan。

### Q3：是否必须通过 Observation → Replan → New Tool Calls 解决？

动态参数、交集和失败后 fallback 需要这一循环，bounded Replan 有明确价值；但 Stress 入口失败、raw planning/Policy 边界、multi-turn entity carry-over、答案完成度以及未完全冻结的工具环境，不会因为引入 LangGraph 自动消失。

结论选择 **C：当前问题主要不在 Replan，应先修 Planner/Tool/Memory**。建议顺序是：先冻结完整 tool world、校准并启用 Judge、修复复杂意图入口和上下文参数传递；随后只对已经获得有效 Observation、且确实缺少动态参数或 fallback 的节点做局部 bounded Replan A/B。现在直接全面迁移 LangGraph，会把多种失败原因混在一起，难以证明收益来自 Replan。

## 13. Fully Frozen V2 36×1 Precheck

2026-09-11 在 `agent-live-eval-runner-v4`、`chat-live-world-v2-fully-frozen-v2` 和 `deepseek-v4-flash` 上执行 36 case × 1 trial，未启用 Judge。正式预检报告为 `live-20260911T095301Z-a7a323a8`；工具层声明 `database_access=false`、`network_access=false`。

- 通过 22/36，task success / pass@1 为 61.11%；Provider failure 为 0。
- Simple 6/6、Boundary 4/4，说明基础事实题和安全边界在冻结世界中可用。
- Multi-tool 4/6、Multi-turn 5/6、Recovery 3/4；仍有复杂能力遗漏、风险披露遗漏和按实体注入失败未触发。
- Replan 0/8、Stress 0/2；observation-dependent task success 为 0。六个 Replan case 虽执行了上下游工具，但下游参数没有绑定上游 Observation；其余复杂入口没有形成有效能力集合。
- raw capability recall 73.08%，effective required tool recall 75.86%，backend repair rate 77.78%。Capability-first Planner 的 raw tool calls 仍为空，因此 raw required tool recall 0 只描述当前架构边界，不等价于最终工具完全不可用。
- required fact coverage 100%；平均工具调用 1.72，平均 LLM 调用 2.0，平均 token 4761.25，p50/p95 latency 为 2267/4529 ms。
- Critical 通过 6/14，仅 42.86%，当前不能升级为 36×3 正式基线，也不应开始新 Judge 校准。

在预检前，指数和个股新闻两个 case 暴露出冻结 payload 缺少生产回答契约字段。补齐窗口、回撤、涨跌日、抓取时间、缓存状态和资讯元数据后，两题单独复跑均通过；环境因此升级到 fully-frozen-v2，旧的 v1 预检报告作废，不用于拼接成绩。

下一步优先修复 Observation→下游参数绑定，以及复杂请求的 capability 入口；随后重跑 36×1。只有 Critical 明显恢复且 Replan 不再系统性为 0，才执行 36×3 并生成新的 Judge 校准样本。
