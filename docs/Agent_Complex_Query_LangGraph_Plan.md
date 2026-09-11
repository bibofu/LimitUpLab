# LimitUpLab 复杂问题多步规划改造计划

## Phase 1 实施状态（2026-09-11）

Phase 1 已实施，当前采用 `Fast Path + allowlisted Complex Path`：

- 默认请求继续走原有 Plan-and-Execute；确定性 Router 仅在问题同时包含热股范围、涨停范围、集合筛选和评分意图时进入 Complex Path。
- 当前最小状态包含用户问题、消息、结构化步骤、当前步骤、实体集合、工具事实/trace、完成/失败步骤、工具/模型调用计数、最终答案和失败原因。
- 当前节点链路为 `validate_plan → execute_sources → observe_intersection → execute_rating → apply_policy → complete → answer`，不存在 Replan、Retry 或循环边。
- `ResultReference(source_step, entity_set, path, max_items)` 与 `ArgumentBinding` 负责动态参数绑定。交集由确定性代码按股票代码计算，`first_board_ratings.symbols` 直接读取 `S3` 的实体集合，不把 Observation 交给 LLM 猜测。
- 已支持的唯一 MVP 是“热股 Top10 ∩ 当日完整涨停池 → 仅对交集查询首板评分”。Top10 只约束热股来源，上游涨停池不因“首板评分”被误缩成首板或前 10；空集合、来源缺失、工具错误和绑定失败会明确标记失败并进入既有回答/降级机制。
- Capability Contract、Tool Policy、工具执行、事实组合、答案校验与投资合规继续复用现有实现；LangGraph 仅管理状态和步骤迁移。
- Trace 新增 `routing_decision`、`complex_graph_plan`、`complex_graph_step`，记录 dependency、resolved args、observation summary、step status，且 `replan_count=0`。

当前限制：其他动态依赖、条件分支、多轮实体集合、通用 DAG、Evidence Gap、bounded Replan、Retry、checkpoint 和 human approval 均未实现。进入 Phase 2 前，应先持续观察本 MVP 的真实失败样本和独立 Complex Path 基线。

## 1. 背景与目标

LimitUpLab 当前 Chat Agent 主要采用一次性链路：

```text
Query Understanding
    → Planner
    → Tool Execution
    → Tool Policy Repair
    → Grounded Answer
```

这条链路适合单能力问题和工具之间没有数据依赖的组合问题，例如：

- 查询指定日期的涨停名单；
- 查询一只股票的评分或近期新闻；
- 并行查询指数、板块、涨跌停和热门股后生成市场概览。

当问题包含动态实体集合、前后步骤依赖、工具异常后的补证据，或者需要根据中间结果调整计划时，一次性规划难以可靠完成。例如：

> 找出今天热门股中同时涨停的股票，再查询这些股票的走势和新闻，最后比较主要风险。

本次改造目标是在保留现有稳定快速路径的前提下，引入受控的复杂任务执行图：

```text
User Query
    → Query Understanding
    → Complexity Router
        ├─ Fast Path
        ├─ Complex Graph
        └─ Clarify
```

Complex Graph 支持 `Plan → Execute → Observe → Replan`，但必须受到确定性 Tool Policy、事实接地、调用预算和终止条件约束。

## 2. 非目标

本次改造不包括：

- 用 LangGraph 重写全部现有 Chat Agent；
- 把所有多工具问题都改成循环执行；
- 允许模型生成任意代码、SQL 或未注册工具；
- 引入多个自治 Agent 相互对话；
- 改变首板评分、Top10、D+1 至 D+5 等预测逻辑；
- 让 LLM 决定原始分数、投资结论或事实是否成立；
- 移除现有 Query Contract、Capability Contract、Tool Policy、工具注册表或 Grounding。

LangGraph 只承担复杂任务的状态管理和流程编排，现有确定性能力继续作为事实和安全边界。

## 3. 设计原则

### 3.1 依赖关系决定复杂度

工具数量不是复杂度标准。多个互不依赖的工具仍可一次规划并行执行；只有后续步骤依赖前一步结果，或者需要根据结果补救时，才进入 Complex Graph。

### 3.2 Fast Path 保持默认

现有快速路径经过较多业务规则和回归测试，应继续承担大多数请求。Complex Graph 作为旁路逐步接入，不在首版替代原链路。

### 3.3 Capability-first，禁止任意工具代码

Planner 和 Replanner 输出结构化能力、步骤和参数引用。服务端继续把 Capability 映射到允许的工具，模型不能直接生成可执行代码或绕过工具注册表。

### 3.4 Facts-first

计划、观察和回答分别保存。中间推理不能作为市场事实；最终答案中的股票、日期、指标和值必须能追溯到结构化工具事实。

### 3.5 有限循环

Replan 是异常或依赖驱动的补救机制，不是每执行一个工具都重新调用模型。图必须有步骤数、规划轮数、工具调用、token 和耗时上限。

## 4. 目标架构

```text
                         User Query
                              ↓
                    Query Understanding
                              ↓
                    Complexity Router
                   ↙          ↓          ↘
              Fast Path    Clarify    Complex Graph
                  ↓                       ↓
         Existing Planner             Initial Plan
                  ↓                       ↓
              Tool Policy          Plan Validation
                  ↓                       ↓
          Parallel Execution       Execute Ready Steps
                  ↓                       ↓
              Grounding               Observe
                  ↓                       ↓
               Answer             Evidence Update
                                          ↓
                                  Completion Check
                                    ↙           ↘
                               complete       incomplete
                                  ↓               ↓
                               Answer          Replan
                                                  ↓
                                           Plan Validation
```

Complex Graph 建议包含以下节点：

1. `understand_query`
2. `route_complexity`
3. `build_initial_plan`
4. `validate_plan`
5. `execute_ready_steps`
6. `observe_results`
7. `check_completion`
8. `replan`
9. `build_grounded_answer`
10. `final_safety_check`

`execute_ready_steps` 可以并行执行依赖已经满足的步骤，不应把所有工具机械串行化。

## 5. Complexity Router

### 5.1 路由输出契约

```python
class ComplexityDecision(BaseModel):
    route: Literal["fast_path", "complex_graph", "clarify"]
    reason_codes: list[Literal[
        "single_capability",
        "static_parallel_tools",
        "dependent_tool_arguments",
        "dynamic_entity_set",
        "conditional_follow_up",
        "multi_source_verification",
        "recoverable_tool_failure",
        "missing_required_constraint",
        "ambiguous_entity",
    ]]
    confidence: float
    estimated_steps: int
    estimated_tool_calls: int
```

Router 决策必须写入 trace，并同时保存原始输出和确定性修正后的最终路由。

### 5.2 确定性优先规则

以下情况优先进入 Fast Path：

- 单一 Capability；
- 多个工具互不依赖，可以一次性并行执行；
- 现有专用组合工具已经能直接返回结果；
- 问题可以由现有 Query Contract 和 Tool Policy 完整描述。

以下情况进入 Complex Graph：

- 后续工具参数来自前序工具结果；
- 需要先生成股票集合，再逐只或批量查询；
- 用户明确要求“先……再……然后……”且步骤存在数据依赖；
- 需要根据 empty、partial 或 error 决定替代步骤；
- 需要多来源交叉验证，并根据证据覆盖决定是否继续；
- 多轮上下文引用了之前产生的结果集合，需要在新任务中继续加工。

以下情况直接澄清：

- 股票、板块、日期等关键约束缺失且无法从会话上下文唯一恢复；
- 用户使用“它、那个、这些”等指代，但没有合法前置实体或集合；
- 两种解释会导致明显不同的工具、日期或市场范围。

### 5.3 路由降级与升级

- Router 不确定时默认 Fast Path，避免普通问题承担复杂图成本。
- Fast Path 执行后如果出现可识别的证据缺口，可以在预算允许时升级一次到 Complex Graph。
- Complex Graph 不得降级为无事实的自由回答；预算耗尽时只能基于已有事实回答并披露缺口。

## 6. Complex Agent State

建议新增独立状态模型，不直接在节点之间传递任意 `dict[str, Any]`：

```python
class ComplexAgentState(TypedDict):
    request_id: str
    session_id: str
    query_view: dict
    route_decision: dict
    goal: str

    plan_version: int
    steps: list[PlanStep]
    completed_step_ids: list[str]
    failed_step_ids: list[str]
    pending_step_ids: list[str]

    entity_sets: dict[str, list[EntityRef]]
    tool_facts: list[ToolFact]
    evidence_claims: list[EvidenceClaim]
    evidence_gaps: list[EvidenceGap]
    tool_errors: list[ToolError]

    iteration: int
    tool_call_count: int
    retry_count: int
    planner_tokens: int
    answer_tokens: int
    elapsed_ms: int

    completion_status: str
    stop_reason: str | None
    answer: str | None
```

关键字段只能追加或由专门节点更新，避免 Replanner 覆盖历史事实和执行记录。

## 7. Plan 与步骤契约

```python
class PlanStep(BaseModel):
    step_id: str
    capability: str
    depends_on: list[str]
    tool_name: str | None
    arguments: dict
    argument_bindings: list[ArgumentBinding]
    output_alias: str | None
    success_condition: SuccessCondition
    on_empty: Literal["continue", "replan", "stop_with_disclosure"]
    on_partial: Literal["continue", "replan", "stop_with_disclosure"]
    on_error: Literal["retry", "replan", "stop_with_disclosure"]
```

动态参数通过显式绑定表达：

```json
{
  "target_argument": "symbols",
  "source_step_id": "S1",
  "source_path": "entity_sets.intersection",
  "max_items": 5
}
```

禁止使用自由文本指令，例如“把上一步的票传给下一个工具”。执行器必须验证绑定来源存在、类型正确、数量受限。

## 8. Plan Validation 与 Tool Policy

每次 Initial Plan 或 Replan 后都先运行确定性校验：

- Capability 是否属于当前 profile；
- 工具是否已注册、已启用且属于该 Capability；
- 参数是否符合工具 Schema；
- 步骤依赖是否存在环；
- 动态参数引用是否指向已完成或合法待执行步骤；
- 是否重复执行相同工具和相同参数；
- 预计调用量是否超过预算；
- 是否包含投资建议、收益承诺或其他越界目标；
- 新步骤是否能够覆盖当前 Evidence Gap。

Policy 可以修复确定性、低风险错误，例如补充必需工具或规范参数；不能替模型猜测股票、日期和板块。所有修复必须记录：

```text
raw_plan
policy_repairs
validated_plan
repair_reasons
rejected_steps
```

## 9. Execute、Observe 与 Replan

### 9.1 Execute

- 只执行依赖已满足的 ready steps；
- 独立步骤并行执行；
- 相同工具和参数使用请求内去重；
- 每个结果统一标记 `ok|empty|partial|error`；
- 工具异常保留结构化错误，不转换为虚构事实；
- 动态股票集合设置数量上限，首版建议最多 5 只。

### 9.2 Observe

Observe 节点只做确定性归一化：

- 提取实体、日期、指标、值和来源路径；
- 更新命名实体集合；
- 标记已满足 Evidence；
- 生成新的 Evidence Gap；
- 判断错误是否可重试；
- 不生成用户可见结论。

### 9.3 Replan 触发条件

只在以下情况调用 Replanner：

- 必需 Evidence 仍缺失；
- 工具返回 partial 或可恢复 error；
- 新实体集合决定了后续调用；
- 原步骤参数因空集合或类型不符而无法执行；
- Completion Check 认为任务未完成且仍有合法补救路径。

成功且后续步骤已经明确时直接继续执行，不进行无意义 Replan。

Replanner 输入只包含目标、当前结构化计划、事实摘要、Evidence Gap、错误和剩余预算，不重复注入全部原始工具 JSON 和完整历史对话。

## 10. Completion Check 与停止条件

Completion Check 以确定性规则为主：

- 用户要求的子任务是否都有完成、缺失或失败状态；
- 必需步骤是否执行；
- 关键实体集合是否覆盖；
- 关键 Evidence 是否完整；
- empty、partial 和 error 是否准备好显式披露；
- 是否存在没有来源却准备写入答案的事实性声明；
- 是否达到预算或不可恢复错误。

建议首版硬限制：

| 项目 | 上限 |
|---|---:|
| Initial Plan + Replan | 3 轮 |
| 计划步骤 | 6 个 |
| 总工具调用 | 8 次 |
| 单工具相同参数调用 | 1 次 |
| 单步骤重试 | 1 次 |
| 动态股票集合 | 5 只 |

此外设置总耗时和产品 token 上限。达到任何上限后停止执行，回答已有事实并说明哪些部分未完成。

## 11. Grounding 与答案生成

Complex Graph 最终仍使用统一 Grounding 和 Answer Contract：

- Evidence 使用 `(entity, date, metric, value, source_path)`；
- 动态集合的来源步骤必须可追溯；
- 不同股票之间不能借用数值；
- 不同日期、指标和来源不能错绑；
- 无法解析或无法证明的事实声明进入 `unscored_claims`；
- 工具 empty、partial、error 必须在答案中披露；
- 不输出买卖、仓位、目标价、收益承诺或确定性预测。

答案模型接收经过裁剪的结构化事实和缺失项，不接收可让其自行补充市场数字的开放式背景。

## 12. SSE、持久化与 Trace

### 12.1 SSE 事件

建议新增或扩展以下事件：

```text
route_decided
plan_created
plan_repaired
step_started
step_completed
observation_updated
replan_started
completion_checked
answer_delta
completed
failed
cancelled
```

前端默认展示简洁进度，详细 Plan、工具参数、修复原因和 Evidence 放在可展开 Trace 中。

### 12.2 持久化

首版可以只保存最终完整 trace，不要求跨进程恢复。稳定后再增加 LangGraph checkpoint：

- 只保存结构化状态和引用，不重复保存大体积原始响应；
- checkpoint 与 `agent_run_id`、`session_id` 关联；
- 恢复前重新校验工具版本、计划版本和预算；
- 已完成工具调用不得因为恢复而重复执行。

### 12.3 取消

复杂任务必须支持请求取消和总截止时间。客户端断开后应停止尚未开始的节点，并尽可能取消正在进行的模型或工具请求。

## 13. 与现有代码的衔接方案

建议新增独立模块，避免继续扩大主 Chat 文件：

```text
backend/app/agents/complex_graph/
├── state.py
├── router.py
├── plan_models.py
├── planner.py
├── policy.py
├── executor.py
├── observer.py
├── completion.py
├── answer.py
├── graph.py
└── trace.py
```

现有组件复用关系：

| 现有组件 | 改造方式 |
|---|---|
| Query Understanding | 直接复用，补充复杂度信号 |
| Capability Contract | 作为 Plan Step 的能力白名单 |
| Tool Registry | 作为唯一工具入口 |
| Tool Execution | 提供单步/批次执行适配层 |
| Tool Policy | 抽取为可在每一轮调用的无状态校验 |
| Grounding | 汇总多步骤 Evidence 后复用 |
| Answer Contract | 保持最终确定性门禁 |
| Agent Run Repository | 扩展保存 route、plan version 和 node trace |
| SSE | 增加复杂图进度事件，保持最终 completed 兼容 |

主入口只负责：构建 Query View、调用 Router、选择 Fast Path 或 Complex Graph、统一返回 `AgentChatResponse`。

## 14. 分阶段实施

### Phase 0：契约与基线

- 冻结现有 Fast Path 的 Dev/Live Eval 基线；
- 明确复杂问题标签和首批场景；
- 定义 Router、State、PlanStep、EvidenceGap Schema；
- 不引入循环执行。

完成标准：Schema、序列化、状态更新和预算规则都有单元测试。

### Phase 1：Router 与 Graph 骨架

- 引入 LangGraph；
- 实现 `route → plan → validate → execute → observe → complete → answer`；
- 暂不启用 Replan；
- 只支持一个两跳场景；
- 未命中允许场景时回到 Fast Path。

首个建议场景：热门股与涨停股求交集，再查询交集股票的走势和新闻。

完成标准：Fast Path 行为不变；两跳场景能传递动态股票集合；工具调用不超过预算。

### Phase 2：受控 Replan

- 增加 Evidence Gap；
- 增加 partial/error/empty 分支；
- 增加一次工具重试和最多两次 Replan；
- 加入 Completion Check、重复调用拦截和预算终止；
- Trace 展示每次计划版本和修复。

完成标准：补救成功、不可恢复失败、预算耗尽和部分回答均可稳定复现。

### Phase 3：多轮、SSE 与持久化

- 支持跨轮实体集合引用；
- 完善 Graph 节点 SSE；
- 增加取消和截止时间；
- 评估是否启用 checkpoint 与恢复；
- 扩展更多复杂 Capability 组合。

完成标准：刷新、断开、取消和恢复不会产生重复工具调用或丢失事实来源。

### Phase 4：灰度与扩面

- Router 先以 shadow 方式运行，只记录建议路由；
- 人工分析 false positive 和 false negative；
- 小比例启用 Complex Graph；
- 达到 Eval 和运行指标后逐步增加复杂场景。

## 15. Eval V2 扩展

保持现有七层指标，不生成新的单一总分。Router 作为 Query Understanding 与 Planner 之间的可诊断子项，Complex Graph 的多轮执行继续归入 Planner、Policy、Execution、Grounding 和 Efficiency。

新增指标：

- `route_accuracy`
- `over_routing_rate`
- `under_routing_rate`
- `plan_step_precision/recall`
- `dependency_accuracy`
- `dynamic_binding_accuracy`
- `completion_decision_accuracy`
- `replan_needed_rate`
- `replan_success_rate`
- `unnecessary_replan_rate`
- `duplicate_tool_call_rate`
- `loop_budget_violation_rate`
- `partial_completion_disclosure_rate`

新增 Dev case 类型：

1. 多工具但相互独立，必须继续走 Fast Path；
2. 两步和三步依赖任务，必须走 Complex Graph；
3. 动态集合为空，不得继续无效调用；
4. 动态集合超过上限，必须裁剪并披露；
5. 工具 partial/error 后允许补救；
6. 不可恢复错误后停止并披露；
7. Replanner 建议重复调用时被 Policy 拒绝；
8. 预算耗尽时停止；
9. 多轮实体、日期、窗口和集合指代；
10. 股票、日期和指标跨步骤错绑的 Grounding 变异测试。

发布门禁除现有指标外，建议增加：

- Critical 复杂 case 100% 通过；
- Router 总体准确率不低于 95%；
- Fast Path 过度路由率不高于 3%；
- 依赖关系和动态参数绑定关键字段 100% 正确；
- 重复工具调用率和预算违规率为 0；
- 复杂任务关键事实 Grounding precision 为 100%；
- Complex Graph p95 延迟和 token 必须有单独批准基线，不能和 Fast Path 混算。

## 16. 测试计划

### 单元测试

- Router 规则、置信度和确定性覆盖；
- Plan DAG 环检测；
- 动态参数绑定类型和数量限制；
- ready step 选择和并行分组；
- 同工具同参数去重；
- Evidence Gap 生成和消除；
- Completion Check；
- 重试、Replan 和预算终止；
- Graph State 序列化与恢复兼容。

### 集成测试

- Fast Path 回归保持不变；
- 两跳动态集合；
- 三步筛选、补充信息和比较；
- empty、partial、error；
- Provider 失败；
- 多轮集合引用；
- 取消和超时；
- SSE 事件顺序与最终响应一致。

### 真实 HTTP 验收

- 简单问题仍走 Fast Path；
- 静态多工具问题不被过度路由；
- 复杂问题产生多版本计划和完整 trace；
- 工具失败后按策略补救或停止；
- 达到预算后返回部分事实及缺失披露；
- 最终答案和 trace 中的事实、来源及调用数一致。

## 17. 主要风险与应对

### 风险一：复杂路径吞掉普通请求

应对：Fast Path 默认、Router shadow、过度路由指标和白名单场景灰度。

### 风险二：调用次数和延迟失控

应对：计划前估算、逐轮预算、并行 ready steps、动态集合裁剪和硬停止条件。

### 风险三：Replan 重复或循环

应对：计划版本、调用指纹去重、Evidence Gap 必须减少、最多三轮规划。

### 风险四：工具结果结构不统一

应对：先建立 ToolFact、EntitySet 和 ToolError 适配层，不让 Replanner直接读取任意工具对象。

### 风险五：事实跨步骤错绑

应对：关系级 Evidence、来源路径、实体集合 lineage 和变异测试。

### 风险六：主 Chat 链路回归面过大

应对：独立 `complex_graph` 包、主入口最小路由改动、Fast Path 完整回归、逐场景启用。

### 风险七：框架接入掩盖业务边界

应对：LangGraph 只负责编排；Policy、工具注册、事实计算、Grounding 和 Safety 继续由项目确定性代码负责。

## 18. 工作量判断

| 范围 | 复杂度 | 说明 |
|---|---|---|
| Router + Schema + Graph 骨架 | 中 | 不含真正动态执行和 Replan |
| 单个两跳场景 | 中高 | 需要实体集合、参数绑定和 Grounding |
| 通用 Replan + 预算 + 错误恢复 | 高 | 涉及 Policy、执行、状态和评测协同 |
| 多轮 + SSE + 取消 + checkpoint | 高 | 涉及运行生命周期和持久化 |
| 全 Capability 迁移 | 很高且不建议一次完成 | 回归范围大，容易破坏 Fast Path |

真正的重难点不是 LangGraph API，而是状态契约、动态参数传递、逐步 Policy、Completion Check、关系级 Grounding 和稳定性评测。

## 19. 最终验收标准

改造达到可发布状态需要同时满足：

- 现有 Fast Path Eval 不低于冻结基线；
- 简单问题不产生额外模型或工具循环；
- 复杂路径可以展示原始计划、Policy 修复、每步结果、Evidence Gap 和 Replan 原因；
- 任何执行路径都不超过工具、规划、token 和耗时预算；
- empty、partial、error 和预算耗尽均有明确用户披露；
- 最终关键事实全部可追溯到工具 Evidence；
- 安全违规为 0；
- 真实 HTTP 与 SSE 验收通过；
- Complex Graph 单独建立并批准延迟、token、稳定性和 Provider failure 基线。

## 20. 推荐实施决策

首轮只实现 Phase 0 和 Phase 1，并只开放一个两跳依赖场景。不要立即把全部 Chat Agent 或所有多工具问题迁入 LangGraph。

在首个场景通过关系 Grounding、预算、SSE 和 Eval 门禁后，再引入 Replan。这样既能验证 LangGraph 对项目的实际价值，也能避免框架迁移先于业务契约成熟。
