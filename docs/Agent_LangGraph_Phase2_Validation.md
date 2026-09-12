# Agent LangGraph Phase 2 定向接通验收

验收时间：2026-09-12。架构名称：**Plan-and-Execute Fast Path + bounded observation-driven complex workflow**。本轮没有引入 LLM Replanner、ReAct、额外 Agent 或新 Judge。

## 1. Fixed issues

- Router 改为有优先级、required/optional/forbidden signals 的结构化 `ScenarioRule`；生产只开放 Phase 1 flagship 和四个 Phase 2 场景。`routing_decision` 保存 route、scenario、reason、reason codes 与 matched signals。
- Complex Answer 使用 scenario-specific contract 决定 fallback intent、回答指令、交集 flag、实体覆盖校验和数据边界，不再把所有场景硬编码成 Phase 1 intersection。
- `empty_news_fallback_v2` 删除默认 `300750`。目标只能来自原请求/显式字段/上下文并经当前 Registry 解析；缺失时不执行工具、不 Replan，并输出 `complex_graph_validation` failure trace。
- Replan fingerprint 包含 tool、规范化静态参数与 bindings/source references；执行级去重使用 resolved tool arguments。A/B 不同股票不冲突，同一成功的 A 调用会被拒绝。
- 每个 initial、dynamic downstream 和 replan step 仍保持 `resolve args → Tool Policy validate → execute`。
- Agent 版本更新为 `first-board-chat-langgraph-phase2-v20`；Live runner 更新为 v5，并在报告中分别保存 `graph_trace` 和完整 `full_trace`。

## 2. Production-routable scenarios

| Scenario | 入口信号 | Observation-driven 行为 |
| --- | --- | --- |
| `top_ratings_then_kline_v2` | 最高评分 + 逐只 + K 线 | 评分候选产生后 fan-out K 线 |
| `rating_dragon_tiger_branch_v2` | 最高评分 + 龙虎榜 + 空结果分支 | 先查龙虎榜；empty/partial 后补 K 线与新闻 |
| `empty_news_fallback_v2` | 明确股票 + 新闻 + 空结果 + K 线 | 新闻 empty/error 后补 K 线与个股动态 |
| `partial_stock_comparison_v2` | 两个代码 + K 线 + 失败继续 | 保留成功项，仅重试失败项一次 |

Phase 1 `hot_limit_up_rating_intersection_v1` 继续可路由。`rating_evidence_v2`、`highest_board_risk_v2`、`intersection_risk_v2` 仅保留内部实现，生产 Router 不暴露。

## 3. Live Eval result

Tool World 为 `chat-live-world-v2`，数据库与外部数据网络均关闭；Planner/Answer 使用已配置真实模型 `deepseek-v4-flash`，未启用 Judge。Complex targets 共 15 trials，全部通过；Fast Path controls 共 4 trials，全部通过。

| Case | Before frozen baseline | 本轮 | Replan |
| --- | ---: | ---: | ---: |
| `LIVE-REPLAN-006` Phase 1 flagship | 0/3 | 3/3 | 0 |
| `LIVE-REPLAN-002` conditional | 0/3 | 3/3 | 每次 2 |
| `LIVE-REPLAN-004` empty fallback | 0/3 | 3/3 | 每次 1 |
| `LIVE-RECOVERY-004` partial recovery | 既有 Phase 2 验收 3/3 | 3/3 | 每次 1 |
| `LIVE-REPLAN-001` Top-N downstream | 0/3 | 3/3 | 每次 1 |
| `LIVE-SIMPLE-001/002` | — | 2/2 | Fast Path |
| `LIVE-MULTI-001/002` | — | 2/2 | Fast Path |

Before 数据来自 fully frozen baseline `output/agent-live-eval/live-20260911T101042Z-9bd7ab47/summary.json`；partial recovery 使用接通前已有的专项 Phase 2 结果，不伪造不可比基线。本轮各报告位于 `output/agent-live-eval/phase2-connect-0912-*`。

## 4. True replan trace

`LIVE-REPLAN-002` 的完整报告保存在 `output/agent-live-eval/phase2-connect-0912-conditional-trace/live-20260912T054401Z-0ee93ff2/summary.json`。三个 trial 均记录同一真实链路：

```text
initial first_board_ratings
→ completion=false
→ replan #1: dragon_tiger_list
→ observed empty, completion=false
→ replan #2: stock_kline + stock_news
→ completion=true
```

报告中的 completion 序列为 `[false, false, true]`，两次 Replan validation 均为 success，新步骤数分别为 1 和 2；并非在 initial plan 中预先调用 fallback tools。

## 5. Metrics and answer checks

| Metric（15 个 Complex trials） | Result |
| --- | ---: |
| Task success | 15/15 |
| Observation-dependent task success | 12/12 eligible |
| Replan trigger rate | 80%（12/15；flagship 无需 Replan） |
| Replan success rate | 100%（12/12） |
| Unnecessary replan rate | 0% |
| Avg / max replans per complex task | 1.0 / 2 |
| Avg tool / LLM calls | 3.4 / 1.8 |
| Avg tokens | 4,280.67 |
| Latency p50 / p95 | 1,729 / 3,237 ms |

四个 Phase 2 target 的 12 个答案均通过原 Live contract；conditional、empty 与 partial 均有确定性 failure/uncertainty disclosure，12/12 未出现 Phase 1 “热股与涨停交集”串场。当前 Live runner 仍没有句子级 claim ledger，所以 `required_fact_coverage` 只在题目配置 assertion 时有数值，不能把 null 宣称为 Grounding 满分。

## 6. Tests and limitations

- 定向 Agent 回归：115 passed。
- 完整后端回归：731 passed，22 subtests passed；仅有 LangGraph 依赖的 pending-deprecation warning。
- 沙箱内第一次全量运行出现 42 个 `tmp_path` setup errors，原因是 Windows ACL；主机权限下使用独立 basetemp 复跑后全部通过。
- 当前仍是精确白名单和 deterministic completion/replan，不是通用 DAG 或 autonomous replanner；没有跨请求 checkpoint，也不处理未路由复杂场景。

## 7. Assessment

对现有 Live Eval 已暴露的四类核心 observation-dependent 问题，bounded deterministic replan 已足够：入口、动态参数、条件分支、partial recovery 与回答边界均得到稳定验证。当前没有证据表明必须继续实现 LLM Replanner。只有当线上人工确认的 bad case 持续落在结构化规则之外、且无法用少量安全 scenario 表达时，才应重新评估 LLM Replanner；在此之前增加它只会扩大不可预测计划与 Policy 风险。
