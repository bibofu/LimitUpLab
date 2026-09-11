# Agent LangGraph Phase 1 小范围真实 A/B 验收

验收时间：2026-09-11。结论：**B — Phase 1 验收通过，可以进入 Phase 2 bounded Replan**。

## 1. Environment

| 项目 | Before Phase 1 | After Phase 1 |
|---|---|---|
| Commit | `4ab194fc89778b2e2d0bfb9cae405e3c670b96a3` | `6bcf6d6af59235868332eb4ea613f7a5f272390b` |
| Agent | `first-board-chat-policy-v17-capability-first-planner` | `first-board-chat-langgraph-phase1-v19` |
| Dataset | `agent-chat-live-eval-v1` | 相同 |
| Runner | `agent-live-eval-runner-v4` | 相同 |
| World | `chat-live-world-v2-fully-frozen-v2` / fixture `chat-live-world-v2` | 相同 |
| Model / provider | `deepseek-v4-flash` / `langchain-openai` | 相同 |
| Judge | 未启用 | 未启用 |

两次报告均声明 `fully_frozen=true`、`database_access=false`、`network_access=false`，并使用相同 evaluator 与 failure injection。Before 使用正式 36×3 baseline `live-20260911T101042Z-9bd7ab47`；After 报告为目标 `live-20260911T121135Z-5f2c7ad7`、Simple `live-20260911T121216Z-f6941abd`、Multi-tool `live-20260911T121243Z-23e0bca1`。模型无 Provider failure。

注意：正式 baseline 后、LangGraph 提交前还有基础 Agent 修复提交 `f6c99b1`，因此 Fast Path 对比是“正式 artifact vs 当前版本”，不是只切换 Graph 开关的严格隔离实验；目标动态参数的因果证据由 Graph 的 Observation 与实际下游参数逐 trial 对照确认。

## 2. Target Case

`LIVE-REPLAN-006`：热股 Top10 与今日涨停股求交集，只对交集查询首板评分。

| 指标 | Before | After |
|---|---:|---:|
| 通过 | 0/3 | **3/3** |
| Dynamic arg dependency | 0/3 | **3/3** |
| 平均工具调用 | 3.00 | 3.00 |
| 平均 LLM 调用 | 2.00 | 1.00 |
| 平均 tokens | 5,367.00 | 2,810.67 |
| 平均延迟 | 1,877.67 ms | 989.00 ms |
| p50 / 最大延迟 | 1,979 / 2,073 ms | 982 / 1,171 ms |
| 平均 backend repair（审计口径） | 3.00 | 3.00 |

Before 三次均因 `intersection -> first_board_ratings.symbols` 不成立而失败。After 三次均由 Planner 正确选择三个 capability，随后执行一次有状态 dependency-aware graph；没有 Replan、Retry 或 ReAct。

## 3. Dynamic Binding Validation

三个 trial 的冻结 Observation 相同：

- 热股：`301489, 300750, 600276, 600519, 001299, 601698, 600000, 000538, 002371, 688981`
- 涨停集合共 8 只。
- 同序交集：`301489, 300750, 600276, 001299, 601698, 000538, 002371, 688981`

| Trial | Rating symbols 等于真实交集 | 交集外 | 遗漏 | Missing source | 状态 |
|---:|---|---:|---:|---:|---|
| 1 | 是 | 0 | 0 | 0 | pass |
| 2 | 是 | 0 | 0 | 0 | pass |
| 3 | 是 | 0 | 0 | 0 | pass |

每次实际 trace 均为：

| Step | Capability / Tool | Dependency | Resolved args | Observation | Status |
|---|---|---|---|---|---|
| S1 | `popularity` / `hot_stock_ranking` | 无 | `period=day, limit=10, source=auto` | 冻结热股 Top10 返回 | completed |
| S2 | `limit_up_pool` / `limit_up_events` | 无 | `trade_date=2026-05-15, board_height=null, event_status=closed, limit=100` | 冻结涨停池返回 8 只 | completed |
| S3 | deterministic intersection | S1, S2 | `{}` | 生成 8 个 symbol | completed |
| S4 | `first_board_rating` / `first_board_ratings` | S3 | `symbols=[上述8个真实交集代码]` | 冻结评级候选返回 | completed |

S4 参数来自 S3 的结构化 `ResultReference`，不是 Prompt 回填或预先写死。实际评分调用没有交集外股票。最终回答走现有 facts、确定性模板、缺失披露和 safety boundary；evaluator task pass。该 case 没有配置 `required_answer_facts`，所以 `required_fact_coverage` 为 N/A，而不是失败。Judge 因仍需独立校准，本次未启用。

## 4. Fast Path Regression

固定选择 ID 排序前三个 Simple 和前三个 Multi-tool，各运行一次；所有 Router 均为 `fast / fast_path_default`，没有 `complex_graph_plan`。

| Case | Before | After | 工具选择 | 参数对比 |
|---|---:|---:|---|---|
| SIMPLE-001 | 3/3 | 1/1 | 相同，4 tools | 相同 |
| SIMPLE-002 | 3/3 | 1/1 | 相同，1 tool | 相同 |
| SIMPLE-003 | 1/3 | 1/1 | 相同，`stock_news` | 相同 |
| MULTI-001 | 3/3 | 1/1 | 相同，4 tools | 相同 |
| MULTI-002 | 3/3 | 1/1 | 相同，3 tools | 当前 K 线参数显式为 `symbol=300750, days=20`；baseline 为空参数，这是 `f6c99b1` 的基础 Agent 修复，不是 Graph 路由变化 |
| MULTI-003 | 3/3 | 1/1 | 相同，2 tools | 相同 |

Simple 当前 3/3，baseline 同三题合计 7/9；Multi-tool 当前 3/3，baseline 9/9。样本 trial 数不同，只能说明未观察到明显回归，不能据此宣称稳定性提升。

| 子集 | Before tools / LLM / tokens / latency | After tools / LLM / tokens / latency |
|---|---|---|
| Simple 001–003 | 2.00 / 2.00 / 4,940.67 / 2,552.11 ms | 2.00 / 2.00 / 5,100.00 / 2,757.67 ms |
| Multi 001–003 | 3.00 / 2.00 / 5,656.11 / 3,333.33 ms | 3.00 / 2.00 / 5,733.67 / 2,929.00 ms |

Simple token/延迟分别约 +3.2%/+8.1%，Multi token 约 +1.4%、延迟约 -12.1%；单 trial 波动范围内，没有额外 Graph、工具或 LLM 调用。

## 5. Router

- `LIVE-SIMPLE-001..003`：`fast / fast_path_default`
- `LIVE-MULTI-001..003`：`fast / fast_path_default`
- `LIVE-REPLAN-006`：`complex / [dynamic_entity_set, dependent_tool_arguments]`

本次 7/7 路由符合预期；没有观察到普通 case 被误送入 Graph。

## 6. Efficiency

目标任务成功率从 0% 到 100%，工具调用未增加。由于结构化列表直接使用确定性答案模板，LLM 调用从 2 降至 1，token 降约 47.6%，平均延迟降约 47.3%。Phase 1 为动态绑定引入的 Graph 编排成本没有失控；本次样本甚至更低，但不把单次小样本当作通用性能结论。

## 7. Known Issues

1. **Tool Policy 时机（Phase 2 prerequisite）**：执行前 `validate_plan` 检查 capability→tool 授权和 registry enablement，dispatcher 也执行工具白名单；S4 前还验证 dependency 与 ResultReference。完整 `AgentToolPolicyEngine.reconcile` 位于 S4 执行之后，因此当前仍是 `policy_validation_before_execution=partial`、`policy_reconcile_after_execution=true`。Phase 2 应在每个动态步骤执行前应用完整参数/预算 Policy，而不是只依赖计划白名单。
2. 报告里的 `backend_repair_count=3` 是 raw Planner 无 tool calls 与 Graph 最终三工具之间的审计差异；并不表示事后 reconcile 又执行了三次工具。后续报告应区分 graph compilation 与 policy repair。
3. Live runner 的 `tool_trace` 会过滤 `complex_graph_step` 控制 trace。本报告根据同次实际 source/rating trace 和固定 Graph 状态还原逐步字段；Phase 2 前应让原始 Graph trace 作为独立诊断字段持久化，同时继续从业务工具统计中排除。
4. 目前只验证一个白名单动态依赖场景；不能外推为其他 7 个 Replan 或 2 个 Stress case 已解决。
5. 当前没有 LLM Replan：准确链路是 `Initial Plan → Execute → Observe → deterministic downstream binding → Answer`。

## 8. Decision

**B — Phase 1 验收通过，可以进入 Phase 2 bounded Replan。**

进入 Phase 2 前必须先落实：逐步骤执行前完整 Policy 校验；区分 Graph 编译与 Policy repair 指标；持久化原始 Graph step trace；为 Phase 2 设定严格的 replan/tool/token/latency 上限和无进展终止条件。不要把本次结果描述为 Replan 或 ReAct 成功。
