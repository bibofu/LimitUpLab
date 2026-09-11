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

没有用 paraphrase 凑数。Multi-tool 与 Replan 的边界是：后续工具及参数是否必须读取前一步 Observation 才能确定。

## 4. Schema 与 evaluator

Live schema 使用当前 trace 可验证的字段：`required_capabilities`、required/optional/forbidden tools、`conditional_tools`、`tool_dependencies`、`tool_arg_dependencies`、result states、answer facts/claims 和调用预算。没有引入 `required_behaviors` runtime abstraction。

`tool_arg_dependencies` 从 source trace 的受限 JSONPath（`$.a[*].b`）读取运行时值，再与 source 之后 target trace 的真实参数做 `same_set`、`subset`、`member` 或 `equals` 比较。因此 evaluator 不会预先写死动态股票或板块。

`conditional_tools.when` 读取真实 Tool Outcome。只有本次 Observation 的状态命中 condition，分支工具才成为 required；evaluator 不提前选择分支。

错误由 `InjectedToolRegistry` 在 eval 环境注入。用户问题仍是正常业务问题；代理只包装当前 registry，不修改 Planner、Policy、执行器或答案链路。默认基础世界使用固定的 `SAMPLE_EVENTS`；依赖本地/外部 provider 的工具仍可能随环境变化，这是 V1 的已知限制，后续应将所有 typed tool outputs 固化成统一 `chat-live-world-v1` snapshot。

## 5. 指标

报告记录每个 trial 的 Planner output、Tool trace、Policy repair、最终答案、失败原因、token 与 latency，并汇总：

- Task Success Rate、Planner Accuracy、Required Tool Recall
- Multi-turn Success Rate、Failure Recovery Rate、Replan Success Rate
- Unnecessary Replan Rate（当前固定为 0）
- Avg Tool Calls、Avg LLM Calls、Avg Tokens、P50/P95 Latency
- 各 category success rate 与多次运行 stable rate

Grounding/fact correctness 由确定性 answer fact assertions 负责；语义 Judge 不得补金融事实。当前 V1 尚未建立通用 claim extractor，因此报告不伪造全局 `Unsupported Claim Rate` 或 `Grounding Accuracy`；这两项应在逐句 claim ledger 可用后加入。

## 6. LLM Judge

`--judge` 使用独立配置的模型，并且只看到问题、期望行为、真实工具事实和答案。Rubric 每项 0～2：完整性、清晰度、相关性、风险解释、任务解决度。总分至少 8/10，且任一项不能为 0。

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

本次只建立 suite，没有执行 36×3 的付费 baseline。首次基线必须记录 model、配置、代码 commit 与数据快照；不能把单次 sample 当发布结果。预期当前版本在 simple 和一部分 multi-tool/multi-turn 上可通过，而真正 observation-dependent case 会暴露 `replan_count=0` 的能力缺口。

## 8. LangGraph bounded Replan A/B

后续改造必须保持同一数据集、同一模型配置、同一 world、同一 trials 与 Judge。比较当前 Plan-and-Execute 和 LangGraph bounded Replan 的 Task Success、Replan Success、Failure Recovery、稳定率、工具/模型调用数、token 与 P95 latency。

只有当目标 case 成功率提高、无关 replan 没有增加、Grounding/安全不退化，并且成本与时延在批准预算内，才说明复杂任务能力真正提升。
