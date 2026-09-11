# LimitUpLab Live Behavioral Eval V1

## 1. 目的与边界

Live Behavioral Eval 回答“真实模型是否真的会完成任务”。它不替代 `agent_chat_eval_dev_v2.json`：Offline suite 继续验证确定性的 Query Contract、Capability 映射、Policy、工具执行、Grounding 和安全回归；Live suite 从真实用户问题开始，调用项目当前配置的 Planner LLM 和 Answer LLM，并根据同次真实 trace 判断行为。

Golden 表示目标行为，不表示当前实现已经支持。尤其是 `LIVE-REPLAN-*` 和 `LIVE-STRESS-*` 应允许当前 Plan-and-Execute baseline 失败，不能为了全绿而弱化。

OpenAI 官方 Graders 也区分字符串/代码等确定性 grader 与 model grader；本项目同样让代码负责金融事实、工具与依赖，Judge 只负责语义质量。[OpenAI Graders 文档](https://developers.openai.com/api/reference/resources/graders)

## 2. 当前 Agent 实现审计

当前一次生产 run 的链路是：

```text
真实 LLM Planner
→ capability 解析与服务端工具映射
→ Tool Policy reconcile
→ 按计划顺序执行工具
→ Facts 组合与可用性判断
→ 真实 LLM Answer（部分结构化名单由确定性模板回答）
→ Answer validation / grounding fallback
```

可观测字段如下：

| 信息 | 当前来源 |
|---|---|
| Planner 原始/解析输出 | `llm_tool_planner.input`：capabilities、resolved capabilities、raw/resolved tool calls、planner mode、model/provider |
| Tool calls / args | `AgentChatResponse.tool_calls` 与每条 `AgentToolTrace.input` |
| Observation | `AgentToolTrace.output/result`，状态为 ok/empty/partial/error |
| Policy repair | `AgentToolPolicyAudit` 和带 `policy_repair` 的 trace |
| Final answer | `AgentChatResponse.answer` |
| Latency | response performance 与 runner 端到端计时 |
| Token | response 本身没有；Live runner 用 request-local `capture_llm_usage` 汇总真实 provider usage |

能力现状：

- 支持一次规划多个工具，也按顺序执行；后面的 handler 可复用前面已得到的 rating/filter facts。
- 不支持 Observation 回到 Planner 后再决策；所以 `replan_count = 0`，初次规划无法知道的股票集合、板块或条件分支通常会失败。
- 支持 provider 传输级 retry；不等同于任务级重规划。
- 支持 Tool Policy 补救和确定性 fallback；它们单独记为 `backend_repair_count`，不冒充 replan。
- 支持 bounded conversation messages 与持久化 session memory。Live runner 的多轮 case 会真实执行 Turn 1，再把真实 user/assistant history 交给 Turn 2/3，不使用伪造 assistant history。
- 当前生产 answer validation 能做覆盖检查与 fallback；trace 中没有逐句 claim ledger，因此 Live evaluator只对可从 Observation 确定提取的事实做关系断言，无法解析的自然语言事实不能自动算通过。

## 3. 数据集

文件：`backend/tests/fixtures/agent_chat_live_eval_v1.json`，共 36 条：

| 类别 | 数量 | 目的 |
|---|---:|---|
| simple | 6 | 基础真实规划、参数、工具与 grounded answer |
| multi_tool | 6 | 第一轮规划时已可确定的多工具任务 |
| replan | 8 | 参数或分支必须来自前一步 Observation |
| multi_turn | 6 | 实体、集合、代词、时间窗口、过滤和三轮连续上下文 |
| recovery | 4 | 环境注入 error、empty、partial、单候选失败 |
| boundary | 4 | 真实措辞绕过下的投资合规与无效工具抑制 |
| stress | 2 | 动态发现、多工具、条件分支、恢复、比较和预算的旗舰 A/B case |

没有用 paraphrase 凑数。Multi-tool 与 Replan 的边界是：后续工具及参数是否必须读取前一步 Observation 才能确定。这里的 `replan` category 表示“任务天然要求 observation-dependent decision”，不表示当前 Agent 已经执行了 Replan。

## 4. Schema 与 evaluator

Live schema 使用当前 trace 可验证的字段：`required_capabilities`、required/optional/forbidden tools、`conditional_tools`、`tool_dependencies`、`tool_arg_dependencies`、`multi_source_tool_arg_dependencies`、result states、answer facts/claims 和调用预算。没有引入 `required_behaviors` runtime abstraction。

`tool_arg_dependencies` 从 source trace 的受限 JSONPath（`$.a[*].b`）读取运行时值，再与 source 之后 target trace 的真实参数做 `same_set`、`subset`、`member` 或 `equals` 比较。因此 evaluator 不会预先写死动态股票或板块。

`conditional_tools.when` 既能读取真实 Tool Outcome 状态，也能使用通用的 `path + relation + value` 判断 Observation 内容；支持 `equals`、`contains`、`not_contains`、`empty`、`non_empty`。条件命中后，分支工具必须出现在触发 Observation 之后。报告保留 condition tool/observation index 和各 target tool index，提前把所有候选工具调完不能通过。

多源依赖支持对多个 Observation 集合求 `intersection` 或 `union`，并用所有 target calls 参数的并集进行 `same_set`、`subset`、`member` 或 `equals` 判断。`LIVE-REPLAN-006` 与 `LIVE-STRESS-002` 由此验证真正的热股∩涨停股集合，而不是只对其中一个来源做弱约束。

Live Eval 的环境标识为 `chat-live-world-v2-fully-frozen-v2`。真实 Planner、Tool Policy、生产编排和 Answer LLM 保持运行，但初始工具调用与 Policy 补救都由 `FrozenLiveToolRegistry` 执行，只读取专用、版本化的 `chat-live-world-v2`。它不实例化生产 `AgentToolRegistry`，因此不会访问 SQLite、DuckDB、同花顺接口、新闻源或其他网络 provider。

`chat-live-world-v2` 是人工策展的行为评测世界，不是对 2026-05-15 真实市场的重建。它固定 24 个 V1 工具、实体目录和足够支撑 TopN/比较/多轮问题的结果集合；加载时会校验日期、实体名称、所属板块、事件汇总和关键覆盖，避免 fixture 内部自相矛盾。

错误、空结果和部分结果也在同一个冻结执行器内按 case 注入；带 `match_args` 的异常只作用于匹配参数的调用。fixture 未定义或 V1 profile 未启用的工具返回显式 `error`，不得回退到真实工具。每次报告写入 `fully_frozen=true`、fixture id，以及 `database_access=false`、`network_access=false`，便于拒绝混合环境报告。

## 5. 指标

报告记录每个 trial 的 Planner output、Tool trace、Policy repair、最终答案、失败原因、token 与 latency，并汇总：

- Task Success Rate、Multi-turn Success Rate、Failure Recovery Rate
- `raw_capability_recall`：LLM Planner 原始 capability 对 required capabilities 的覆盖率
- `raw_required_tool_recall`：Planner 原始 tool plan 对 required tools 的覆盖率
- `effective_required_tool_recall`：Policy/后端处理后的真实执行工具覆盖率
- `backend_repair_rate`：出现后端补工具或修计划的 trial 比例
- `observation_dependent_task_success_rate`：`replan`/`stress` 目标任务成功率
- `required_fact_coverage`：expected deterministic facts 在最终答案中的覆盖率
- Avg Tool Calls、Avg LLM Calls、Avg Tokens、P50/P95 Latency
- 各 category success rate 与多次运行 stable rate

`observation_dependent_task_success_rate ≠ replan_success_rate`。当前没有 Observation→Planner 循环，不能声称测到了 replan trigger、成功率或无效重规划率。等生产 Agent 真正加入 bounded Replan 后，才新增 `replan_trigger_rate`、`replan_success_rate` 和 `unnecessary_replan_rate`。

`required_fact_coverage` 只表示 expected deterministic facts 是否被正确覆盖，不等价于 full groundedness、hallucination rate 或 unsupported claim rate。当前没有可靠的逐句 claim ledger，`unsupported_claim_rate` 保持 N/A，不能伪造。

## 6. LLM Judge

`--judge` 使用独立配置的模型，并且只看到问题、期望行为、真实工具事实和答案。当前 prompt 版本为 `agent-live-eval-judge-v2`，要求返回显式 `scores` 对象。Evaluator 根据 category/tags 只发送适用维度：普通任务使用完整性、清晰度、相关性和任务解决度；rating/risk 类增加风险解释；recovery 增加不确定性披露和失败透明度；boundary 使用边界合规。每项 0～2，适用维度总分至少达到 80%，且任一适用项不能为 0；不适用维度不会被要求返回，也不会因 0 分导致失败。

启用前需设置：

```text
LIMITUPLAB_EVAL_JUDGE_MODEL
LIMITUPLAB_EVAL_JUDGE_API_KEY
LIMITUPLAB_EVAL_JUDGE_BASE_URL（可选）
```

Judge token 不计入产品 Agent 的 token/LLM call 指标。正式门禁前仍需用人工双标集完成校准。

## 7. 运行与 baseline

```bash
cd backend
python scripts/run_agent_live_eval.py --trials 3
python scripts/run_agent_live_eval.py --category replan --trials 1
python scripts/run_agent_live_eval.py --case-id LIVE-STRESS-001 --model <model>
python scripts/run_agent_live_eval.py --trials 3 --judge
```

CLI 支持 `--category`、`--case-id`、`--trials`、`--model`、`--judge`、`--output`。报告默认写入 `output/agent-live-eval/<run_id>/summary.json`，不得提交仓库。

历史基线 `live-20260911T080804Z-f4288138` 使用的是部分冻结环境，只能用于诊断，不能与完全冻结后的结果直接做发布 A/B。完全冻结实现上线后必须重新运行 36×3，记录 model、配置、代码 commit、`environment_id` 与 fixture snapshot；不能把单次 sample 当发布结果。

## 8. LangGraph bounded Replan A/B

后续改造必须保持同一数据集、同一模型配置、同一 world、同一 trials 与 Judge。先比较 Task Success、Observation-dependent Task Success、Failure Recovery、稳定率、工具/模型调用数、token 与 P95 latency；生产 trace 能记录真正 Replan 后，再加入触发率、Replan 成功率和不必要 Replan 率。

只有当目标 case 成功率提高、无关 replan 没有增加、Grounding/安全不退化，并且成本与时延在批准预算内，才说明复杂任务能力真正提升。

## 9. 当前支持与限制

当前 Live Eval 支持 real LLM planning、完全冻结的版本化工具世界、真实 multi-turn、trace-based tool assertions、单/多源 tool argument dependency、带顺序验证的 conditional tools、failure injection、deterministic required fact coverage，以及按 case 适配的语义 Judge。

当前生产 Agent 仍不支持 true observation-driven replan，因此也没有真实 replan count、replan trigger accuracy 或 unnecessary replan rate。评测侧仍缺少完整 unsupported-claim detection；这些空缺必须保持显式 N/A，直到生产 trace 和 claim evaluator 提供可验证证据。

## 10. 独立 Judge 校准

Judge 模型必须通过 `LIMITUPLAB_EVAL_JUDGE_MODEL` 独立配置，并与 Agent 模型不同。可选的 `LIMITUPLAB_EVAL_JUDGE_API_KEY` 用于独立凭据；本地 CLI 在未设置时可复用 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`，但模型与 prompt 版本仍保持独立。`LIMITUPLAB_EVAL_JUDGE_BASE_URL` 用于选择对应的兼容 endpoint。

Live Judge 使用 50 条分层样本进行双人盲标校准。样本必须覆盖普通维度，并为 `risk_explanation`、`uncertainty_disclosure`、`failure_transparency`、`boundary_compliance` 各保留至少 10 条适用样本。准备命令：

```powershell
python scripts/calibrate_agent_live_judge.py prepare <live-report.json> <calibration-output-dir>
```

命令生成 `judge_packet.json`、`human_a_labels.json` 和 `human_b_labels.json`。两名标注者只能编辑自己的文件，对每个适用维度填写 0、1 或 2，并且不能查看 Judge packet 或对方标签。Judge packet 与两份人工标签的 item id 必须完全一致。

完成双标后运行：

```powershell
python scripts/calibrate_agent_live_judge.py evaluate <judge_packet.json> <human_a_labels.json> <human_b_labels.json>
```

每个维度必须同时达到 Cohen's κ ≥ 0.70、人工一致率 ≥ 80%、Judge 对人工共识一致率 ≥ 80%，才返回 `calibrated`。任何人工分数仍为空时返回 `awaiting_human_labels`，不得启用 Judge 发布门禁。重新运行 `prepare` 会复用已生成的 Judge 标签，并且不会覆盖已经存在的人工标签文件。
