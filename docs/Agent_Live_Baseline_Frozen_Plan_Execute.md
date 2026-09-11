# Fully Frozen Plan-and-Execute Live Baseline

## 1. Environment

- Run：`live-20260911T101042Z-9bd7ab47`
- Git：`4ab194f`
- Dataset：`agent-chat-live-eval-v1`，36 cases × 3 trials = 108 trials
- Agent：当前 `plan-and-execute`；不支持 observation-driven replan
- Model / provider：`deepseek-v4-flash` / `langchain-openai`
- Tool world：`chat-live-world-v2-fully-frozen-v2`，fixture `chat-live-world-v2`
- 冻结声明：database access=false，network access=false；冻结与 failure injection 测试 17/17 通过
- Judge：关闭；Judge 指标 N/A。Planner、Answer、Multi-turn 均为真实 LLM 调用。

## 2. Core Metrics

| 指标 | 结果 |
|---|---:|
| Overall trial success | 65/108（60.19%） |
| 3/3 stable cases | 21/36（58.33%） |
| Raw capability recall | 73.08% |
| Raw required-tool recall | 0% |
| Effective required-tool recall | 75.86% |
| Backend repair rate | 77.78% |
| 成功 trial 中依赖 backend repair | 53/65（81.54%） |
| Tool-argument dependency | 0/30（0%） |
| Required fact coverage | 6/6（100%） |
| Unsupported claim rate | N/A：无 sentence-level claim ledger |

Raw tool recall 为 0 是当前 capability-first trace 的真实值：Planner raw `tool_calls` 为空，最终工具主要由 capability resolve / Policy repair 产生，不能用 effective recall 代替 raw Planner 能力。

## 3. Category Results

| Category | Trial success | 3/3 stable cases |
|---|---:|---:|
| Simple | 16/18（88.89%） | 5/6（83.33%） |
| Multi-tool | 15/18（83.33%） | 5/6（83.33%） |
| Multi-turn | 15/18（83.33%） | 5/6（83.33%） |
| Recovery | 7/12（58.33%） | 2/4（50%） |
| Boundary | 12/12（100%） | 4/4（100%） |
| Replan category | 0/24（0%） | 0/8（0%） |
| Stress | 0/6（0%） | 0/2（0%） |

Simple 没达到参考的 90% trial / stable 门槛：个股新闻题只有 1/3，另两次工具成功但最终通用拒答。Multi-tool 达到 80% 参考线，但 `LIVE-MULTI-005` 0/3，Planner 把“新闻 + K线”缩成 `stock_activity`，遗漏两个必需能力和工具。该失败发生在任何 Replan 之前，是 pre-replan foundation issue。其余五个 Multi-tool case 均为 3/3；没有 extra-tool、grounding 或 cross-tool composition 失败。

## 4. Top Failure Causes

以下根因非互斥，同一失败可同时属于动态参数和 Observation dependency。

| Root cause | Failed trials | Affected cases | 代表性现象 |
|---|---:|---:|---|
| Tool argument | 30 | 10 | 下游 symbol/sector/query/symbols 缺失或错误；单项 failure injection 未命中 |
| Observation dependency | 27 | 9 | 已有候选、板块或交集 Observation，但没有形成后续动态参数 |
| Answer completeness | 16 | 6 | 工具成功后通用拒答，或未披露“风险/没有” |
| Planner / intent | 15 | 5 | 复杂问题未生成所需 capability 集合 |
| Tool selection | 15 | 5 | 因 capability 遗漏而没有执行必需工具 |

Grounding、过度调用、Provider failure 和 Eval/environment issue 均未检出。Required fact coverage 只覆盖当前 6 条显式 deterministic assertion，不能替代 unsupported-claim 检测。

## 5. Observation-dependent Analysis

当前 runtime 的 `replan_count` 始终为 0，所有 Replan/Stress case 都是 failure；没有 true observation-dependent、preplanned 或 deterministic-policy success。

| Case | 成绩 | 首次计划产生的工具链 | 主要失败阶段 |
|---|---:|---|---|
| REPLAN-001 | 0/3 | ratings → K-line | Top-N symbols 未绑定到 K-line |
| REPLAN-002 | 0/3 | 无 | Planner/capability、工具选择、候选参数 |
| REPLAN-003 | 0/3 | sector → ranking | sector Observation 未绑定到 ranking |
| REPLAN-004 | 0/3 | 无 | Planner 未选择 empty 后的 news fallback |
| REPLAN-005 | 0/3 | ratings → critic → K-line | candidate symbol 未绑定下游工具 |
| REPLAN-006 | 0/3 | hot → limit-up → ratings | intersection 未绑定 ratings symbols |
| REPLAN-007 | 0/3 | limit-up → dragon-tiger → K-line | candidate 未绑定 query / symbol |
| REPLAN-008 | 0/3 | sector → ranking | branch sector 未绑定 ranking |
| STRESS-001 | 0/3 | 无 | Planner/工具选择先失败，另有动态参数和答案缺口 |
| STRESS-002 | 0/3 | 无 | Planner/工具选择先失败，另有交集和答案缺口 |

30 条显式动态参数断言全部失败：Top-N 0/3、sector→ranking 0/6、previous candidate set→downstream 0/9、intersection/mixed dependencies 0/12。当前 Golden 没有为 Multi-turn 建立逐参数断言，因此 multi-turn entity→tool args 记为 N/A，不用 case pass 冒充参数正确率。

按 case 主因看，6/10（REPLAN-001/003/005/006/007/008）已经取得上游 Observation 并执行下游工具，主要缺口是 Observation→新决策/动态参数；4/10（REPLAN-002/004、STRESS-001/002）先发生 Planner / Tool selection 基础失败。后四者不能只靠增加 Replan 解决。

## 6. Multi-turn / Recovery

Multi-turn：entity carry-over 3/3、previous result-set 3/3、date modification 3/3、multi-turn filtering 3/3、3-turn continuity 3/3；pronoun 场景 0/3。Pronoun case 的 capability 和工具均已执行，失败发生在 Answer：三次都通用拒答而未回答风险，因此不归因于 Memory 或 Query Contract。

Recovery：error 3/3、partial 3/3、empty 1/3、single-item failure 0/3。Empty 的两次失败是工具正确返回 empty 后最终拒答，没有把 empty 编造成负面事实；single-item failure 中调用缺少股票参数，导致按 `300750` 的 failure injection 未命中，并且没有继续完成 `600000`。未观察到明确 missing-data hallucination，但 unsupported claim rate 仍为 N/A。

## 7. Efficiency

| 指标 | 结果 |
|---|---:|
| Avg / p95 tool calls | 1.70 / 4 |
| Avg LLM calls | 2.00 |
| Avg / p95 tokens | 4,764.24 / 9,441 |
| Avg latency | 2,337 ms |
| p50 / p95 latency | 2,073 / 4,902 ms |

Judge 未启用，其 token 和 latency 均为 N/A。Multi-turn 尤其 3-turn continuity 成本明显更高；本次未出现超过 8 次工具预算或 efficiency/over-calling failure。

## 8. Recommendation

Q1：完全冻结后的当前真实 baseline 是 65/108（60.19%），3/3 stable case 21/36（58.33%）。

Q2：Simple 88.89%、稳定 83.33%，尚不足以作为完全可靠 Fast Path；Multi-tool 83.33%、稳定 83.33%，总体达到参考线，但仍有一个 0/3 的基础意图失败。可以保留现有 Fast Path 方向，但不能宣称已经稳定。

Q3：Observation-dependent / Stress 的 10 个 case 中，6 个主要因缺少 Observation→新决策→动态参数；4 个先被 Planner / Tool selection 基础问题阻断。另有 Answer completeness 和单项恢复问题。

Q4：选择 **A**。下一步先修基础 Planner capability 覆盖、显式工具参数、empty/风险回答完成度和单项失败继续执行；修复并复跑同一 baseline 后，再只对 Complex Path 引入 bounded Replan。现在直接进入 LangGraph 会把 4 个基础失败与 6 个真正动态依赖失败混在一起。

## Future Eval Improvements

- 为 Multi-turn entity carry-over 增加逐参数可观测断言。
- 在已有 sentence-level claim ledger 后再启用 unsupported-claim 指标。
- Judge 完成人工校准后另行运行；不阻塞本 deterministic behavioral baseline。
