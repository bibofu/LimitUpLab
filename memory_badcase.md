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

## 2026-10-26：单只股票后用“它”追问，短期记忆无法回指

- 状态：**待修复**（本次只分析，未改代码）
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
