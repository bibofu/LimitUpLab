# 记忆系统问题记录

本文件记录 LimitUpLab Agent 记忆系统（短期记忆、长期记忆、引用解析、上下文回指）中发现的真实问题、证据、根因和修复方案。通用 Agent 问答回归仍记录在 [badCase.md](badCase.md)；本文件不重复普通问答问题。

## 记录约定

每条记录包含：

- 状态：待分析 / 待修复 / 已修复 / 不修复
- 现象与影响范围
- 复现方式与会话/运行标识
- 证据（数据库、checkpoint、工具结果）
- 根因（带代码位置）
- 已排除项
- 解决方案与优先级
- 验证计划

## 修复汇总（2026-10-26）

三条记录按同一套引用模型修复，不再分别增加澄清特判：

- 新增按整轮构建的 `ConversationReferents`：支持单股票 payload、`rows[].symbol`、嵌套 `promoted_stocks`、多个单股票工具合并为集合、统计工具的 `analysis` 范围和 `date_scope`；不保存原始工具行。
- `ReferenceIntent` 增加 `analysis` 和 `date_override`；输入审查只接收有界的 `recent_referents`，不接收完整历史。
- 解析规则：唯一实体、明确整批、单一最近分析范围直接解析；只有真实多组名单/多日期冲突才澄清。
- 无工具结果的唯一历史股票生成 `verified=false` 指针，模型必须用本轮工具核验；`resolved_reference` 始终只是指针，不是行情证据。
- 单股票工具的 `symbol` 只描述对象，不再进入会话筛选条件。

最终验证：

- 后端全量：**1911 passed、22 subtests passed、1 skipped**（独立 PostgreSQL 临时 schema）。
- 真实 DeepSeek 三组两轮验收全部通过：
  - “宁德时代代码” → “它近期走势”：解析 300750（`verified=false`），实际调用 `stock_kline`；
  - 两只股票行业 → “他们的最新市值”：解析 300750/600519，不再误判多组名单；
  - 今天 1进2 → “那上一个交易日呢”：识别为 `analysis`，解析 2026-10-23 并调用 `daily_board_promotion`。
- 报告：`output/memory-referents/acceptance-20261026T110140Z/report.json`（Git 忽略目录）。
- 仍未解决：v1 工具集没有稳定的最新总市值数据源；两只股票的市值追问现在会明确回答“数据缺失”，不再误判为引用歧义。该工具能力需要单独评估。

## 2026-10-26：单只股票后用“它”追问，短期记忆无法回指

- 状态：**已修复**（2026-10-26）
- 影响范围：同一会话内的连续追问；单只股票、单只个股走势、单只新闻等“上一轮只有一个实体”的场景。

### 现象

同一会话先问“宁德时代的股票代码是多少”，再问“它近期走势怎么样”：

- 第一问 `complete`，回答“宁德时代（300750），深交所创业板。”；
- 第二问直接返回 `clarify / reference_ambiguous`：“请明确所指股票的代码或在上一份名单中的序号。”；
- 第二问没有调用任何工具，模型也没有被调用。

### 复现

用户实际会话：

- `chat_fb61d1c93c954f6ca1699a2cb57f8944`
  - 第一问：`run_dedcd9d370d247b488527d63bc6fa847`
  - 第二问：`run_8d3324e68ca940b29acc76709a91974b`

补充对照会话：

- `chat_535ef982b1af4fb3b317d6604a485b8a`
  - 第一问“请查询宁德时代最近5个交易日的走势……”实际调用了 `stock_kline`；
  - 第二问“它近期走势怎么样？”仍然 `clarify / reference_ambiguous`。

### 证据

用户会话第一问：

- `tool_calls=[]`
- `tool_results=[]`
- `references=[]`
- `stock_mentions=[]`
- checkpoint：`tool_count=0`、`model_rounds=1`、`reference_intent={kind:none}`

用户会话第二问 checkpoint：

```json
"reference_intent": {
  "kind": "entities",
  "quote": "它近期走势怎么样",
  "ordinal": null,
  "all_entities": false
},
"resolved_reference": {},
"recent_messages": [
  "宁德时代的股票代码是多少",
  "宁德时代（300750），深交所创业板。"
],
"context_diagnostics": {
  "history_turns_included": 1,
  "estimated_input_tokens": 19938,
  "input_token_budget": 40000,
  "reference_resolution": {
    "status": "clarify",
    "clarification": "请明确所指股票的代码或在上一份名单中的序号。"
  }
}
```

补充对照会话第一问：

- `stock_kline` 调用成功，payload 顶层有 `"symbol": "300750"`；
- payload 的 `rows` 是 5 条 K 线，行内只有 `trade_date/open/close/high/low/...`，**没有 `symbol` 字段**；
- 该轮 `references=0`、`stock_mentions=[]`；
- 第二问 checkpoint 的 `active_references=null`，`history_turns_included=1`，`input_token_budget=40000`，`estimated_input_tokens=20676`。

### 根因

1. **单股票工具结果不会建立可回指引用。**
   [structured_references.py:142-160](backend/app/agents/structured_references.py#L142-L160) 只从 `payload.rows[].symbol` 提取股票；`stock_kline` 把股票代码放在 payload 顶层，K 线行内没有 `symbol`，因此 `identities` 为空，`active_references` 不会生成。

2. **`stock_mentions` 同样漏掉单股票工具。**
   [agent_presentation.py:33-40](backend/app/agent_presentation.py#L33-L40) 要求同一字典同时有 `symbol` 和 `name`，且 `name` 出现在回答里；`stock_kline` payload 顶层的 `name` 是工具名 `"stock_kline"`，不是股票名。结果 `stock_mentions=[]`，前端也没有可信股票链接。

3. **“它”被硬编码为必须澄清。**
   [reference_intent.py:39](backend/app/agents/reference_intent.py#L39) 明确要求“它/刚才那只”等未明确序号的单体填 `ordinal=null`、`all_entities=false`，并写明“程序将澄清”。[structured_references.py:193-194](backend/app/agents/structured_references.py#L193-L194) 在检查 `references` 之前就直接返回澄清，没有“上一份结果唯一”的例外。

4. **澄清在模型调用前短路，上一轮历史虽然存在但未被使用。**
   [durable_runtime.py:175-177](backend/app/agents/runner/durable_runtime.py#L175-L177) 在 `built.clarification` 存在时直接返回终态，不调用模型。即使不短路，[context_builder.py:30](backend/app/agents/context_builder.py#L30) 的上下文规则也要求“引用解析为 clarify 时先按澄清提示询问”。因此仅去掉短路不够，必须让解析结果为 `resolved` 或 `none`。

5. **第一问可能完全没有工具证据。**
   用户会话第一问 `tool_calls=[]`，模型凭自身知识回答 300750。`stock_kline` 的 schema 支持“代码或精确名称”（[tool_schemas_research.py:248-250](backend/app/agents/tool_schemas_research.py#L248-L250)），但当前没有轻量的股票代码/名称查询工具，也没有“股票身份必须工具核验”的硬规则。没有工具结果，就没有可建立引用的来源。

### 已排除项

- **不是上下文预算问题**：失败会话 `estimated_input_tokens=19938`，预算 `40000`；对照会话 `20676/40000`。
- **不是短期记忆未保存**：`recent_messages` 完整包含上一轮用户问题和回答，`history_turns_included=1`。
- **不是摘要/压缩问题**：第二轮尚未触发摘要压缩，失败发生在引用解析阶段。
- **不是长期记忆问题**：`memory_hit_count=0`，失败与长期记忆无关。

### 解决方案

按优先级：

1. **P1：让单股票工具产生实体引用。**
   - 在 `references_from_committed_result` 中，对 `stock_kline`、`stock_activity` 等单股票工具，允许从 `payload.symbol` 建立 `single_stock` 引用；
   - 工具输出补充 `stock_name`/`resolved_name`，不要把工具名放在 `name` 字段；
   - 同步更新 `stock_mentions` 提取逻辑，使单股票结果也能生成可信股票链接。

2. **P1：允许“唯一实体”的“它”解析。**
   - `resolve_reference` 在 `kind=entities`、`ordinal=null`、`all_entities=false` 时，如果最新已提交结果只有 **一个唯一股票**，解析为该股票；
   - 只有多个股票、多组名单、无法确认顺序或没有已提交结果时才澄清；
   - 同步修改 `REFERENCE_REVIEW_RULES` 的说明，避免规则与实现不一致。

3. **P2：保证股票身份有工具来源。**
   - 增加轻量 `stock_lookup`/`resolve_stock` 工具，或在提示词中要求“股票代码/名称等身份信息也必须用工具核验”；
   - 如果用户问题只是问代码，至少调用一次名称解析工具，产生可回指的 `single_stock` 引用。

4. **P3：补回归测试。**
   - 单股票工具（`stock_kline`）后问“它近期走势” → 应解析到 300750，并调用本轮工具；
   - 多股票名单后问“它” → 仍应澄清；
   - “第二只”现有行为不能被破坏；
   - 第一轮无工具时，要么强制工具查询，要么明确标记身份未核验，不能产生虚假引用；
   - 单股票工具结果的 `references`/`stock_mentions` 必须非空。

### 验证计划

修复后至少验证：

1. 同一会话“宁德时代最近5日走势” → “它近期走势怎么样”：第二问 `complete`，使用 300750，并实际调用 `stock_kline`；
2. 同一会话“首板名单前3只” → “它近期走势怎么样”：多个实体时仍返回澄清，不猜测；
3. 原有“第二只的代码和名称”回归通过；
4. 无工具的第一问不再被当作可信身份来源，或改为工具核验后才生成引用。

### 关联材料

- 证据文件：`output/ningde-analysis/session_messages.json`、`output/ningde-analysis/session_runs.json`、`output/ningde-analysis/checkpoint.json`（均在 Git 忽略目录）
- 相关实现：
  - [structured_references.py](backend/app/agents/structured_references.py)
  - [reference_intent.py](backend/app/agents/reference_intent.py)
  - [agent_presentation.py](backend/app/agent_presentation.py)
  - [durable_runtime.py](backend/app/agents/runner/durable_runtime.py)
  - [context_builder.py](backend/app/agents/context_builder.py)
  - [tool_schemas_research.py](backend/app/agents/tool_schemas_research.py)

---

## 2026-10-26：双股票后用“他们”追问，多工具引用聚合失败

- 状态：**已修复**（2026-10-26）
- 影响范围：一次回答涉及多只股票，且每只股票通过独立工具调用获取；随后用“他们/这两只”等整批指代追问。
- 同类问题：上一条记录的单股票“它”指代。

### 现象

同一会话先问“宁德时代和贵州茅台分别属于什么行业”，再问“他们的最新市值分别是多少”：

- 第一问 `partial`，实际调用两次 `stock_activity`，回答表格包含两只股票，`stock_mentions` 包含 300750 和 600519；
- 第二问直接返回 `clarify / reference_ambiguous`：“上一轮包含多组名单或未能确认展示顺序，请明确股票代码或名单。”；
- 第二问没有调用任何工具，模型也没有被调用。

### 复现

- 会话：`chat_fbe4075568b2437ba5d43dd06cc7f9ea`
- 第一问：`run_aabf6c7fb7c5438c879243774c698fc9`
- 第二问：`run_e8d99c3b62b341728c03dda63d334e41`

### 证据

第一问实际生成了 **两条各自只有一只股票的引用**：

```json
[
  {
    "reference_id": "run_aabf6c...:0:31da6132b75b",
    "reference_type": "tool_result",
    "source_tool": "stock_activity",
    "ordered_symbols": ["300750"],
    "source_symbols": ["300750"],
    "ordinal_resolvable": false
  },
  {
    "reference_id": "run_aabf6c...:1:6a731b92b800",
    "reference_type": "tool_result",
    "source_tool": "stock_activity",
    "ordered_symbols": ["600519"],
    "source_symbols": ["600519"],
    "ordinal_resolvable": false
  }
]
```

第二问 checkpoint：

```json
"reference_intent": {
  "kind": "entities",
  "quote": "他们",
  "ordinal": null,
  "all_entities": true
},
"resolved_reference": {},
"active_references": [
  {"ordered_symbols": ["300750"], "ordinal_resolvable": false},
  {"ordered_symbols": ["600519"], "ordinal_resolvable": false}
],
"context_diagnostics": {
  "history_turns_included": 1,
  "estimated_input_tokens": 21719,
  "input_token_budget": 40000,
  "reference_resolution": {
    "status": "clarify",
    "clarification": "上一轮包含多组名单或未能确认展示顺序，请明确股票代码或名单。"
  }
}
```

### 根因

1. **引用按单个工具结果生成。**
   [structured_references.py:142](backend/app/agents/structured_references.py#L142) 对每个 `tool_result` 分别建立引用；两次单股票 `stock_activity` 调用因此产生两条单符号引用。

2. **展示顺序按单条引用校验，无法覆盖整张两行表格。**
   [structured_references.py:161](backend/app/agents/structured_references.py#L161) 对每条引用只传入该引用自己的 `identities` 调用 `_displayed_order`。第一问的回答表格同时有 300750 和 600519，但在校验 300750 这条引用时，600519 行无法匹配任何符号，`_displayed_order` 返回 `None`；两条引用都变成 `tool_result`，`ordinal_resolvable=false`。

3. **`all_entities` 解析要求所有最新引用完全一致。**
   [structured_references.py:213-215](backend/app/agents/structured_references.py#L213-L215)：

   ```python
   distinct = {(tuple(item.get("ordered_symbols", [])), item.get("trade_date"))
               for item in latest}
   if len(distinct) != 1 or (ordinal is not None and not all(
           item.get("ordinal_resolvable") for item in latest)):
       return clarify("上一轮包含多组名单或未能确认展示顺序...")
   ```

   两条引用分别是 `(["300750"], null)` 和 `(["600519"], null)`，`len(distinct)==2`，于是被判定为“多组名单”，整批指代失败。**它们其实来自同一轮、同一种工具、同一张回答表格，只是被拆成了两次单股票调用。**

4. **澄清仍然在模型调用前短路。**
   [durable_runtime.py:175-177](backend/app/agents/runner/durable_runtime.py#L175-L177) 直接返回澄清；[context_builder.py:30](backend/app/agents/context_builder.py#L30) 的上下文规则也要求按澄清提示询问。上一轮历史已进入上下文（`history_turns_included=1`），但模型没有机会使用。

5. **第一问的工具结果本身是可信的。**
   与上一条“单股票 `stock_kline` 没有行级 symbol”不同，`stock_activity` 的 `rows` 同时包含 `symbol` 和 `name`，所以 `stock_mentions` 成功生成了两只股票。问题不在工具结果缺失，而在**多工具结果的引用聚合**。

6. **修复引用后仍可能缺少“最新总市值”数据源。**
   当前 v1 工具集没有稳定的最新总市值工具；`stock_activity` 只在可选的 `rating_context` 中提供 `float_market_cap`，而本次两只股票的 `rating_context` 都为空并记录了 `data_missing`。因此即使引用解析修好，第二问仍可能只能回答“数据缺失”，除非增加估值/市值工具或接入 hithink valuation 能力。

### 已排除项

- **不是上下文预算问题**：`estimated_input_tokens=21719`，预算 `40000`。
- **不是短期记忆未保存**：`recent_messages` 包含上一轮问答，`history_turns_included=1`。
- **不是股票提及提取问题**：`stock_mentions` 已包含 300750 和 600519。
- **不是长期记忆问题**：`memory_hit_count=0`。

### 解决方案

按优先级：

1. **P1：按整轮聚合单股票引用。**
   - 在 `references_from_committed_result` 中先汇总同一轮、同一工具、同一日期的单股票结果，再统一用回答表格校验展示顺序；
   - 或者新增 `single_stock` 引用类型，在解析 `all_entities` 时合并同一轮的单股票引用；
   - 合并后应得到类似 `ordered_symbols=["300750","600519"]` 的批次引用，而不是两条互不相认的单符号引用。

2. **P1：让 `all_entities` 支持“单股票引用集合”。**
   - 当最新一轮的所有引用都是单符号、来源工具/日期一致时，求并集并去重；
   - 明确合并后的顺序来源（回答表格顺序优先，其次工具调用顺序）；
   - 只有出现多个不同名单、不同日期、顺序冲突或无法确认时才澄清。

3. **P2：补齐“最新市值”数据源。**
   - 如果这是产品要支持的问题，增加估值/市值工具，或在 `stock_activity` 中稳定返回总市值/流通市值及其日期；
   - 否则在回答中明确 `data_missing`，不要用历史快照冒充最新市值。

4. **P3：补回归测试。**
   - 同一轮两次单股票 `stock_activity` → “他们”应解析出两只股票；
   - 两条不同名单/不同日期 → 仍应澄清；
   - “第一只/第二只”的顺序语义不能被合并逻辑破坏；
   - 单股票“它”场景（上一条记录）也要覆盖；
   - 市值问题要么调用真实数据工具，要么明确返回数据缺失。

### 验证计划

修复后至少验证：

1. 复现同样两问：第二问 `complete`，包含 300750 和 600519，或明确说明市值数据缺失；
2. checkpoint 中 `active_references` 形成可解析的批次引用，不再返回“上一轮包含多组名单”；
3. 多组不同名单/不同日期的回归仍返回澄清；
4. “第二只的代码和名称”与单股票“它”回归通过。

### 关联材料

- 证据文件：`output/ningde-analysis/session_messages_2.json`、`output/ningde-analysis/session_runs_2.json`（Git 忽略目录）
- 相关实现：
  - [structured_references.py](backend/app/agents/structured_references.py)
  - [durable_runtime.py](backend/app/agents/runner/durable_runtime.py)
  - [context_builder.py](backend/app/agents/context_builder.py)
  - [tools.py](backend/app/agents/tools.py)（`stock_activity` 的 `rating_context`）

---

## 2026-10-26：统计指标追问“那上一个交易日呢”，被误判为股票列表条件

- 状态：**已修复**（2026-10-26）
- 影响范围：上一轮是统计/聚合类工具（如 1进2 成功率、晋级率、市场统计），下一轮用“那上一个交易日呢/那昨天呢”等省略式追问同一指标、只换日期。
- 与前两条的区别：不是单股票“它”，也不是多股票“他们”；本次上一轮根本没有股票列表引用，追问的对象是**上一轮的分析指标 + 新日期**。

### 现象

同一会话先问“今天市场1进2成功率是多少，有多少只股票1进2成功了”，再问“那上一个交易日呢”：

- 第一问 `complete`，使用 `trading_calendar` 和 `daily_board_promotion`，正确回答 2026-10-26 的 1进2 为 8/68 = 11.76%；
- 第二问直接返回 `clarify / reference_ambiguous`：“尚无已提交的股票列表，请明确股票或先查询名单。”；
- 第二问没有调用任何工具，模型也没有被调用。

### 复现

- 会话：`chat_62462392562d48a5ae5760e5c9921273`
- 第一问：`run_3d1d2694883a4418aa3f53024a1ce25c`
- 第二问：`run_b61ebf3fe51641f387fce1fb9e7abd38`

### 证据

第一问工具结果：

- `trading_calendar`：`rows[0]` 包含 `previous_trade_date="2026-10-23"`，但没有 `symbol`；
- `daily_board_promotion`：`rows` 只有 1 行，行内没有 `symbol`；真正的股票列表在 `rows[0].promoted_stocks` 中（15 只），每只才有 `symbol/name`；
- 响应 `references=[]`、`active_references=null`；
- 但 `stock_mentions` 有 8 只——说明工具数据里确实有股票，只是引用提取没有看到嵌套列表。

第二问 checkpoint：

```json
"reference_intent": {
  "kind": "conditions",
  "quote": "那上一个交易日呢",
  "ordinal": null,
  "all_entities": false
},
"resolved_reference": {},
"active_references": null,
"context_diagnostics": {
  "history_turns_included": 1,
  "estimated_input_tokens": 20572,
  "input_token_budget": 40000,
  "reference_resolution": {
    "status": "clarify",
    "clarification": "尚无已提交的股票列表，请明确股票或先查询名单。"
  }
}
```

### 根因

1. **引用提取只看 `payload.rows[].symbol`。**
   [structured_references.py:142-160](backend/app/agents/structured_references.py#L142-L160) 对每个工具结果检查 `rows` 里的 `symbol`。`daily_board_promotion` 的行是统计汇总，股票在嵌套的 `promoted_stocks` 里；`trading_calendar` 则完全没有股票。两者都被跳过，`active_references` 为空。

2. **没有“查询范围引用”或“上一次分析”引用。**
   当前引用模型把“可回指对象”限定为股票列表。`conditions` 分支需要至少一条带 `query_scope` 的 `valid` 引用（[structured_references.py:195-200](backend/app/agents/structured_references.py#L195-L200)），但统计工具没有生成任何引用，于是直接落到“尚无已提交的股票列表”。

3. **“那上一个交易日呢”被分类为 `conditions`，但错误信息按股票列表语义解释。**
   [reference_intent.py:40](backend/app/agents/reference_intent.py#L40) 把“沿用上次查询条件再查询”归为 `conditions`。本次用户确实是“同一个指标、换一个日期”，分类本身不算错；错在解析器只支持股票列表的 `query_scope`，没有“上一次分析指标 + 日期覆盖”的表示。返回“尚无已提交的股票列表”对用户是答非所问。

4. **`daily_board_promotion` 的 `query_scope` 只有 `{days:1}`，并不包含实际使用的日期。**
   即使把 `{days:1}` 作为条件继承，也不能自动得到“上一个交易日 = 2026-10-23”。需要模型读取第一问 `trading_calendar` 的 `previous_trade_date` 并重新查询；但澄清在模型调用前短路，模型看不到这些历史。

5. **澄清在模型调用前短路。**
   [durable_runtime.py:175-177](backend/app/agents/runner/durable_runtime.py#L175-L177) 直接返回澄清；[context_builder.py:30](backend/app/agents/context_builder.py#L30) 的规则又要求按澄清提示询问。`recent_messages` 已包含上一轮的指标、8/68、`previous_trade_date=2026-10-23`，但模型没有机会使用。

6. **本质问题：引用系统缺少“分析型追问”这一层。**
   “那上一个交易日呢”不是股票实体回指，也不是股票列表条件继承；它是**同一分析指标的省略式追问 + 新日期**。当前 `entities/conditions/none` 三分类里没有合适的语义。

### 已排除项

- **不是上下文预算问题**：`estimated_input_tokens=20572`，预算 `40000`。
- **不是短期记忆未保存**：`history_turns_included=1`，`recent_messages` 包含上一轮完整问答。
- **不是股票提及提取问题**：`stock_mentions` 已提取 8 只晋级股票。
- **不是长期记忆问题**：`memory_hit_count=0`。
- **不是第一问数据错误**：第一问 8/68=11.76% 与工具 `daily_board_promotion` 返回一致。

### 解决方案

按优先级：

1. **P1：为统计/聚合工具保留可核验的查询范围。**
   - 即使工具结果没有股票 `symbol`，只要 payload 有合法 `query_scope`，也生成 `query_scope` 引用（`reference_type="query_scope"`）；
   - 或在 `conversation` 中单独保存 `last_tool_scope`，不要求同时存在股票列表；
   - `conditions` 分支应能使用这种引用，而不是要求股票列表。

2. **P1：增加“分析型追问”语义。**
   - 在 `ReferenceIntent` 中区分“沿用上次查询条件”和“沿用上次分析指标 + 新日期”；
   - 后者应携带上一轮工具、指标名称和日期覆盖信息，允许模型用 `trading_calendar.previous_trade_date` 解析“上一个交易日”，再调用同一工具；
   - 无法解析日期或指标时才澄清。

3. **P1：修正错误提示。**
   `conditions` 找不到任何可核验范围时，不应返回“尚无已提交的股票列表”；应返回“上一轮未保留可核验的查询条件，请重新说明指标或日期”，避免把统计追问误导成股票选择。

4. **P2：引用提取递归处理嵌套股票列表。**
   `daily_board_promotion.promoted_stocks` 这类嵌套列表可以递归提取，至少用于 `stock_mentions` 或可选的实体引用；但本问题的关键仍是查询范围，而不是股票列表。

5. **P3：补回归测试。**
   - 统计工具（1进2/晋级率）后问“那上一个交易日呢” → 应重新查询上一交易日并完整回答，不要求股票列表；
   - 同一指标换日期、换市场等明确覆盖条件；
   - 股票列表条件继承（“继续上次条件，改为降序”）仍正常；
   - 多组不同名单/不同日期的股票追问仍澄清；
   - 单股票“它”和多股票“他们”两条回归不退化。

### 验证计划

修复后至少验证：

1. 复现同样两问：第二问 `complete`，回答 2026-10-23 的 1进2 成功率与成功数量，或明确说明数据缺失；
2. 第二问 checkpoint 中 `reference_intent` 不再被当成股票列表条件，或 `resolved_reference` 能提供可核验的上一次分析范围；
3. 股票列表条件继承、单股票“它”、多股票“他们”回归通过；
4. 多组名单/不同日期仍能正确澄清。

### 关联材料

- 证据文件：`output/ningde-analysis/session_messages_3.json`、`output/ningde-analysis/session_runs_3.json`（Git 忽略目录）
- 相关实现：
  - [structured_references.py](backend/app/agents/structured_references.py)
  - [reference_intent.py](backend/app/agents/reference_intent.py)
  - [durable_runtime.py](backend/app/agents/runner/durable_runtime.py)
  - [context_builder.py](backend/app/agents/context_builder.py)
  - [tools.py](backend/app/agents/tools.py)（`daily_board_promotion` 的嵌套 `promoted_stocks`）
