# LimitUpLab 短期记忆与长期记忆实现说明

更新日期：2026-10-28
适用代码：`memory_refactoring` 分支，当前 schema `0015_memory_embeddings`

本文描述系统当前真实落地的记忆实现，不是设计草案。重点回答：

1. 一次问答的“短期记忆”如何保存、压缩、裁剪并进入模型；
2. 长期记忆如何写入、自动学习、检索、失效和交付；
3. 两套记忆如何保持隔离，如何避免把记忆当作市场事实或交易授权；
4. 当前边界、运行方式、测试和已知风险。

相关但更偏演进的记录仍见 [Long_Term_Memory_v2.md](Long_Term_Memory_v2.md)。

## 目录

- [1. 核心边界](#1-核心边界)
- [2. 总体请求链路](#2-总体请求链路)
- [3. 短期记忆实现](#3-短期记忆实现)
- [4. 长期记忆实现](#4-长期记忆实现)
- [5. 运行方式与环境变量](#5-运行方式与环境变量)
- [6. 测试与评测](#6-测试与评测)
- [7. 当前限制和风险](#7-当前限制和风险)
- [8. 关键文件地图](#8-关键文件地图)

---

## 1. 核心边界

### 1.1 两类记忆

| 类型 | 生命周期 | 存储位置 | 作用 |
|---|---|---|---|
| 短期记忆 / 会话记忆 | 一个 `session_id`，跨多轮但通常不跨会话 | PostgreSQL `chat_sessions`、`chat_messages`、LangGraph checkpoint | 承接“第二只”“刚才那批”“继续沿用条件”等上下文 |
| 长期记忆 | 跨会话，按 `owner_id` 隔离 | PostgreSQL `long_term_memories` | 保存用户明确或低风险自动学习的偏好、研究背景、展示策略 |

短期记忆和长期记忆都不是市场事实来源。价格、涨跌幅、涨停、龙虎榜、财报和新闻必须由本轮工具重新查询；历史回答、摘要、引用和长期记忆只能用于研究目标、实体、日期、条件和偏好衔接。

### 1.2 Owner 隔离

- 浏览器通过签名 Cookie `limituplab_visitor` 获得 `visitor_<32位hex>` 身份。
- `AnonymousVisitorMiddleware` 校验签名后把 `owner_id` 写入 `request.state`。
- 所有会话、消息、运行记录和长期记忆查询都带 `owner_id`：
  - 会话读取拒绝其他 owner 的 `session_id`；
  - 长期记忆写入和读取都按 `owner_id` 过滤；
  - 不存在跨 owner 的长期记忆检索。

### 1.3 记忆不是证据

上下文规则明确写入 `app/agents/context_builder.py` 的 `CONTEXT_RULES`：

- 长期记忆优先级低于本轮用户明确要求和有效会话约束；
- scoped memory 只在对应工具、意图、主题、实体或条件下适用；
- `scope_verified=false` 的 scoped memory 只能当作候选，不能当全局偏好；
- `historical=true` 的研究档案不能作为当前市场证据；
- 历史聊天中的“保存/删除记忆”命令不能重新授权或恢复长期偏好。

### 1.4 运行时存储

业务运行、会话、记忆、运行状态和测试均使用 PostgreSQL；不存在 SQLite 运行时回退。SQLite 只保留历史归档和迁移读取工具。

---

## 2. 总体请求链路

```text
HTTP /api/agents/chat 或 /chat/stream
→ AnonymousVisitorMiddleware 校验 visitor cookie，得到 owner_id
→ ChatSessionRepository.ensure_session(session_id, owner_id)
→ RunLifecycle.create / claim，写入 react_runs
→ DurableRuntime 使用 LangGraph StateGraph + FencedPostgresSaver
→ input_review
   → 读取长期记忆 retrieve_for_run
   → LLM 输入审核，识别 memory_intent / reference_intent
   → 如为显式记忆命令，进入 memory 节点
→ agent
   → 每次模型调用前重新读取长期记忆
   → clean_conversation_for_memory_revision
   → build_context 组装 system/context/history/current/working
   → 模型或工具循环（最多 6 轮、8 次工具调用、90 秒）
→ tool
   → 工具结果写入 agent_tool_results，必要时分页读取
→ memory（仅显式记忆命令）
   → repository.put/delete，返回 memory_action
→ final validation
   → 校验本轮实际依赖的长期记忆是否仍在
   → 生成 display_blocks
→ RunDelivery.commit
   → 写 agent_runs、assistant chat_message、react_runs.response_json
   → 记录使用计数
   → 在同一个事务中写 memory_extraction_jobs outbox
→ SSE / HTTP 发布正式答案
```

---

## 3. 短期记忆实现

### 3.1 会话与消息持久化

表结构（基线和后续迁移）：

- `chat_sessions`
  - `session_id` 主键
  - `owner_id`
  - `title`
  - `created_at`、`updated_at`、`archived_at`
- `chat_messages`
  - `message_id` 主键
  - `session_id`
  - `role`：`user` / `assistant`
  - `content`
  - `status`：`success` / `error`
  - `run_id`
  - `metadata_json`
  - `created_at`
  - `legacy_order`
- `chat_session_memories`
  - 旧版滚动摘要表
  - 新实现只读它做一次性导入；新的摘要游标和摘要内容放在 LangGraph checkpoint，不再由新流程写入该表
- `react_runs`
  - `run_id`、`owner_id`、`session_id`
  - `request_json`、`request_hash`
  - `active`、`cancelled`
  - `thread_id`、`runtime_version`、`run_status`
  - `response_json`
  - `started_at`、`updated_at`、`finished_at`
  - `recovery_count`
- checkpoint 表
  - `checkpoints`
  - `checkpoint_blobs`
  - `checkpoint_writes`
  - 三者都以 `thread_id` 关联 `chat_sessions.session_id`，并随会话删除级联删除

关键行为：

- `ChatSessionRepository.append_message` 使用 `message_id` 做幂等写入：
  - 同一 `message_id` 重复写入不会生成第二条；
  - 消息 ID 已经属于其他会话时抛出 `SessionOwnershipError`；
  - 写入前再次校验会话 owner；
  - 用户消息会触发默认标题更新，标题最多 80 字符。
- `get_session` 默认只返回最近 100 条消息，上限 200 条；列表页返回会话摘要和消息数。
- 删除会话时：
  - 有活跃 `react_runs` 会先拒绝删除；
  - 删除 `chat_session_memories`、`chat_messages`、`agent_runs` 和 `chat_sessions`；
  - `react_runs` 及 checkpoint 表通过外键级联删除。
- 会话 API：
  - `POST /api/agents/chat/sessions`
  - `GET /api/agents/chat/sessions`
  - `GET /api/agents/chat/bootstrap`
  - `GET /api/agents/chat/sessions/{session_id}`
  - `PATCH /api/agents/chat/sessions/{session_id}`
  - `DELETE /api/agents/chat/sessions/{session_id}`

### 3.2 Durable Graph 与 checkpoint

实现位置：`app/agents/runner/durable_runtime.py`。

- 使用 LangGraph `StateGraph[RunnerState]`，节点为：
  - `input_review`
  - `agent`
  - `tool`
  - `memory`
  - `pause`
- 图编译时使用 `FencedPostgresSaver`，不是普通内存 checkpointer。
- checkpoint config：

```python
{
  "configurable": {
    "thread_id": session_id,
    "run_id": run_id,
    "owner_id": owner_id,
    "lease_token": ...,
    "fence": ...
  },
  "recursion_limit": 40
}
```

- `thread_id` 使用 `session_id`，因此同一会话的多个 run 共享 thread 状态；
- `run_id` 用于区分同一 thread 中的不同任务；
- checkpoint schema 版本要求 `checkpoint_migrations.MAX(v) == 9`，不匹配时启动失败；
- `RunnerState.schema_version = 1`，不一致时要求迁移后恢复；
- 运行租约由 `runtime_leases` 和执行 fencing 保护，避免多个进程同时提交同一 run。
- `react_runs` 的部分唯一索引 `react_active_session` 约束同一 owner + session 最多一个活跃 run。
- 暂停/恢复：
  - 暂停位置和模式记录在 `react_runs` 的 pause 字段；
  - 恢复前 `validate_resume` 校验 checkpoint id、pause mode 和 interrupt id；
  - 恢复时不重新运行已完成的业务节点；
  - 取消会写入终止状态，未交付的 checkpoint 不会变成正式答案。
- 恢复/重试时仍会重新读取长期记忆，并按依赖清除旧的偏好塑形文本。

`RunnerState` 的主要字段：

```text
schema_version, run_id, current_query, reference_date,
conversation, messages, pending,
memory_context_fingerprint, memory_dependencies,
memory_context_used, display_policies,
memory_action, memory_owner_revision,
answer, status, stop_reason, tool_calls, tool_messages,
model_rounds, tool_count, remaining_seconds, done
```

其中 `conversation` 是短期记忆的核心状态，包含：

- `recent_messages`：最近保留的完整轮次；
- `conversation_summary`：较早内容的滚动摘要；
- `research_context`：研究目标、股票代码、主题、日期范围、未解决问题；
- `active_constraints`：当前会话有效约束；
- `active_references`：上一轮已提交结果的结构化引用；
- `referents`：追问指针；
- `summary_cursor`、`summarized_message_count`、`summary_revision`；
- `last_committed_message_id`、`revision`；
- `summary_triggered`、`summary_deferred_reason`；
- `memory_context_fingerprint`、`memory_owner_revision`。

### 3.3 新会话开始时如何装载历史

`DurableRuntime._conversation`：

1. 从 checkpoint 取已有 `conversation`；
2. 如果这是新会话且 `chat_session_memories` 有旧记录，则只读导入一次：
   - `conversation_summary`
   - `active_constraints`
   - `research_context`
   - `imported_from`
3. 读取 `runner.history` 中已提交的用户/助手消息；
4. 如果旧摘要表有 `last_message_id`，只取该游标之后的消息；
5. 在摘要模型调用前，先调用 `clean_conversation_for_memory_revision`，删除受已变化长期记忆影响的 assistant 文本；
6. 调用 `compress_conversation` 追加已提交消息并触发滚动摘要；
7. 如果状态中存在上一轮 `run_id` 且该 run 已正式交付：
   - 从上一轮 response 构建 `active_references`；
   - 从引用推导 `active_constraints`；
   - 构建 `referents`；
8. 再次清理记忆影响的文本，返回新 conversation。

重点：

- 只有已正式交付的消息才进入下一轮；
- 未交付、失败、取消的 run 不会被当作历史；
- 记忆命令轮次不进入摘要；
- 使用过长期记忆的 assistant 文本不会进入摘要，避免偏好塑形的 prose 变成下一轮会话约束。

### 3.4 滚动摘要的触发和裁剪

实现位置：`app/agents/conversation_memory.py`、`app/services/conversation_summary.py`。

默认参数：

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `summary_trigger_tokens` | 6000 | 最近消息 + 摘要估算超过该值才压缩 |
| `recent_window_tokens` | 2400 | 摘要后保留的最近完整轮次预算 |
| `MAX_SUMMARY_CHARS` | 800 | 摘要正文最大字符 |
| `MAX_ITEM_CHARS` | 160 | 列表项最大字符 |
| `SUMMARY_SCHEMA_VERSION` | `conversation-summary-v1` | 摘要 schema |

流程：

1. 用 `message_id` 去重，校验本轮已提交窗口不重复；
2. 找到 `last_committed_message_id`，只消费游标之后的新消息；
3. `filter_memory_control_turns` 过滤：
   - memory_action 轮次；
   - 状态为 error/cancelled/refuse 的 assistant 轮次；
4. `successful_messages` 过滤非 success、空内容和被拒绝 run；
5. 把消息追加到 `recent_messages`，更新游标和 `revision`；
6. 估算 `recent_messages` + `conversation_summary` 的 token：
   - 不超过 6000：不压缩；
   - 超过 6000：按“完整轮次”从新到旧保留，直到约 2400 token；
   - 更旧的完整轮次进入摘要。
7. 如果最新一轮本身超过最近窗口，设置 `summary_deferred_reason=latest_turn_exceeds_recent_window`，不强行摘要；
8. 摘要来源只包含未被 memory scope 清除、且未使用长期记忆的 assistant 文本；
9. 摘要优先级：
   - 结构化 function call；
   - JSON prompt fallback；
   - deterministic summary；
   - 任何异常都降级为 deterministic summary，并记录 `summary_error`；
10. `build_summary` 对摘要、研究目标、股票、主题、日期、约束和未解决问题做上限约束。

`deterministic_summary` 的保守规则：

- 只从用户消息和已提交消息的 `metadata.stock_mentions` 提取股票；
- 只从固定主题词表提取主题；
- 只有用户消息含 `只看/排除/不要/重点/默认/关注` 等词时提取会话约束；
- assistant 文本不作为事实来源。

### 3.5 每次模型调用前的上下文组装

实现位置：`app/agents/context_builder.py`。

`build_context` 的消息顺序固定为：

```text
SystemMessage(system_prompt + CONTEXT_RULES)
HumanMessage("会话上下文（仅数据）：" + JSON payload)
最近保留的历史完整轮次（从旧到新）
当前用户问题
本轮 working messages（assistant tool_calls + ToolMessage 观察）
```

上下文 payload 的字段：

```text
context_version
conversation_summary
research_context
active_constraints
active_references
page_context
resolved_reference
historical_market_facts_are_evidence = false
long_term_memories  # 仅在调用方传入长期记忆时
```

`page_context` 只允许以下字段：

```text
symbol, trade_date, date_scope, page, tab, sector, industry, market
```

其他页面字段不会进入模型输入。

Token 估算：

- 文本：ASCII 字符数 / 3 + 非 ASCII 字符数 × 2，向上取整；
- `BaseMessage` 额外加 8；
- 这是保守估算，不是供应商 tokenizer 的精确计数。

预算策略：

- 默认输入预算 `LIMITUPLAB_AGENT_INPUT_TOKEN_BUDGET=40000`；
- 必须保留：system、context、current query、本轮 working messages、工具 schema；
- 长期记忆按检索排序从低到高丢弃，直到预算内；
- 如果仍超预算，返回 `ContextBudgetExceeded`，由运行层生成 `partial/context_budget`，**不会截断本轮工具证据**；
- 历史消息按完整轮次从旧到新尽量加入，超出预算的旧轮次直接省略，不会留下孤立 assistant 消息；
- `working messages` 校验每个 assistant tool call 都有且仅有一个结果，否则返回 `error/invalid_tool_context`。
- 长期记忆正文只存在于这次模型调用的临时输入；Graph checkpoint 保存的是本轮 `memory_context_fingerprint`、`memory_dependencies` 和当前-run 工作消息，不保存被选中的长期记忆正文。`_agent` 在模型返回后会剥离 `build_context` 生成的 system/context/history 消息，只保留 working messages。

### 3.6 引用、条件和页面上下文

- `resolve_reference` 解析：
  - `第一只/第二只/刚才那批/这批/上面那批` 等；
  - 已提交的 `active_references`；
  - 最近消息中的 referents；
  - 页面上下文。
- 解析结果分为：
  - `kind=entities`：可复用的已提交实体；
  - `kind=analysis`：沿用分析指标，但数值必须重新查询；
  - `kind=conditions`：沿用上次查询条件，不沿用旧名单；
  - `kind=none`。
- 无法唯一确定时返回 `clarify`，不猜测股票、顺序或日期。
- 会话约束只来自显式会话请求和已提交引用，不来自 assistant 文案。
- 当前用户明确条件覆盖页面条件和历史条件。

### 3.7 工具结果与结果分页

- 工具结果先做无损编码，再写入 `agent_tool_results`；
- 小结果直接进入上下文；
- 大结果在上下文中只保留摘要和 `result_ref`；
- 模型可以调用 `read_tool_result` 分页读取：
  - 单页预算 `PAGE_TOKEN_BUDGET=5000`；
  - 可指定字段和分页；
  - 不可变回执保证同一 run 中结果不被覆盖。
- 超预算时明确返回 `partial`，并保留完整证据，不用截断后的结果伪装完整。
- 当前-run 工具结果不可丢弃；历史工具结果不会原样进入后续轮次。

### 3.8 交付事务

实现位置：`app/services/agent_run_services.py` 的 `RunDelivery.commit`。

单事务内依次：

1. 读取并校验 `react_runs` owner/run；
2. 校验执行 lease/fence；
3. 拒绝 canceled、paused 等非法状态；
4. 校验 `memory_dependencies`：
   - 本轮实际使用的记忆被修改、删除或过期 → `partial/memory_changed`；
   - 无关记忆变化不阻断；
   - 旧 checkpoint 没有 dependencies 时，回退到 `memory_context_fingerprint`；
5. 如果本轮使用长期记忆且交付成功：
   - `record_usage_in_connection` 增加 `use_count`、`last_used_at`、`management_revision`；
   - 不改变内容 `revision`；
6. `AgentChatResponse.model_validate` 做最终公共文档校验；
7. 写 `agent_runs`；
8. 写 assistant `chat_messages`，其 `metadata_json` 是完整正式 response，包含：
   - `memory_dependencies`
   - `memory_context_used`
   - `memory_context_fingerprint`
   - `task_status`、`stop_reason` 等；
9. 如果是 memory_action 轮次：
   - 给对应的 user 消息 metadata 写入 `memory_scope=long_term`；
   - 防止这条用户命令以后被当作普通会话约束；
10. 更新 `react_runs.response_json`、`active=0`、`run_status`；
11. 在同一个事务中写 `memory_extraction_jobs` outbox；
12. 提交事务后 SSE/HTTP 才能发布正式答案。

`memory_context_used` 的精确含义：

- 当前代码在 Graph 中设置为 `bool(memory_hit_count)`；
- 它表示“本轮注入过长期记忆上下文”，不证明模型实际引用了每条记忆；
- usage 计数按实际注入的 dependencies 记录，不按模型语义判断。

### 3.9 记忆变化后的历史清理

实现位置：`app/services/memory_scope.py`。

- `filter_memory_control_turns`：
  - 过滤 memory_action 轮次；
  - 过滤 error/cancelled/refuse assistant 轮次；
  - 同一个 run 的 user 和 assistant 一起排除。
- `summary_source_messages`：
  - 额外排除使用过长期记忆的 assistant 文本；
  - 防止偏好塑形的回答变成会话摘要。
- `clean_conversation_for_memory_revision`：
  - 有 dependencies 的消息：只检查这些记忆是否仍匹配；
  - 没有 dependencies 的旧消息：回退到整个 fingerprint；
  - 被判定失效的 assistant 消息删除；
  - 如果确有删除，重建 deterministic summary；
  - 更新 `memory_owner_revision`、`memory_context_fingerprint`。
- `sanitize_working_messages_for_memory_revision`：
  - 恢复/重试时保留 tool call/result 证据；
  - 删除可能受旧偏好影响的 assistant 文本，但保留工具协议；
  - 依赖检查异常时按“已变化”处理，避免发布陈旧偏好塑形文本。

### 3.10 短期记忆的边界

- 短期记忆只服务当前会话，不是跨会话偏好；
- 摘要不保存价格、收益、评分、排名、新闻事实或工具原始输出；
- 历史回答、摘要和引用不能作为当前行情证据；
- 会话消息默认最多读取最近 100 条，摘要承担更早内容的连续性；
- 当前实现没有“无限历史全文检索”；
- 会话删除会永久删除消息、摘要、运行和 checkpoint，不会保留可恢复副本；
- 删除会话不会删除长期记忆；长期记忆只能通过显式删除、过期清扫或数据运维流程移除。

---

## 4. 长期记忆实现

### 4.1 数据模型

核心表：`long_term_memories`。

基线和迁移后的关键列：

```text
memory_id, owner_id, memory_type, memory_key,
value, topics_json, metadata_json,
revision, management_revision,
scope_type, scope_json, scope_key,
canonical_key, structured_json,
status, source_kind, source_quote, content_hash,
supersedes_memory_id, supersedes_revision,
candidate_action, candidate_expires_at,
activation_kind, activation_reason,
use_count, last_used_at,
embedding, embedding_model, embedding_source_hash, embedding_updated_at,
expires_at, deleted_at,
source_run_id, source_user_message,
created_at, updated_at
```

`memory_type`：

```text
preference          # 偏好
research_context    # 跨会话研究背景
research_archive    # 有来源的历史研究报告
```

`status`：

```text
candidate      # 待确认候选，不进入 active 检索
active         # 当前有效
superseded     # 被更新或替换
revoked        # 候选被拒绝
expired        # 已过期
deleted        # 已删除墓碑
```

`source_kind`：

```text
legacy      # 旧数据
explicit    # 用户显式保存/删除
auto        # 后台候选提取
import      # 迁移导入
```

`activation_kind`：

```text
none        # 未激活，通常为候选
manual      # 显式确认
safe_auto   # 低风险策略或 Judge 自动激活
```

其他表：

- `memory_relations`
  - `relation`：`supersedes`、`contradicts`、`exception_to`、`duplicate_of`、`merged_into`
  - 用于更新、删除、作用域例外和重复/合并关系
- `memory_owner_versions`
  - `owner_id` 主键
  - `revision`：owner 级有效版本
- `memory_operation_receipts`
  - `(owner_id, operation_id)` 主键
  - `request_hash`
  - `memory_id`、`operation`、`result_revision`
  - 用于幂等重放
- `memory_extraction_jobs`
  - 后台候选提取 outbox
  - `status`：`pending`、`processing`、`succeeded`、`skipped`、`failed`
  - `attempts`、`available_at`、`locked_by`、`locked_until`
  - 唯一约束 `(source_message_id, extractor_version)`
- `runtime_leases`
  - worker/运行 lease，如 `job:memory-extraction`、`job:memory-embeddings`
- `agent_tool_results`
  - 当前 run 的工具结果回执

关键索引：

- `memory_active_identity`：
  - `(owner_id, memory_type, memory_key, scope_key)`
  - `WHERE status='active' AND deleted_at IS NULL`
- `memory_candidate_open`：
  - `(owner_id, memory_type, scope_key, canonical_key)`
  - `WHERE canonical_key IS NOT NULL AND status='candidate' AND deleted_at IS NULL`
- `memory_owner_status`
- `memory_owner_scope`
- `memory_embedding_hnsw`：
  - HNSW `vector_cosine_ops`
  - `WHERE embedding IS NOT NULL AND status='active' AND deleted_at IS NULL`

### 4.2 身份、规范键和作用域

长期记忆身份由以下组合确定：

```text
owner_id + memory_type + canonical_key + scope_key
```

- `memory_key` 是用户可见/可查询的键；
- `canonical_key` 默认等于 `memory_key`；
- 作用域类型：

```text
global
tool        scope={"tool_name": "..."}
intent      scope={"intent": "..."}
topic       scope={"topic": "..."}
entity      scope={"symbol": "..."}
condition   scope={"condition": "..."}
```

- `scope_key` 由 `memory_scope_key` 生成：
  - global → `global`
  - 其他 → `type:<sorted compact json>`
  - 超过 120 字符 → `type:sha256:<hash>`
- 作用域别名会归一化：
  - entity：`entity_id` / `stock_symbol` / `code` → `symbol`
  - topic：`research_topic` / `subject` → `topic`
  - tool：`tool_name` / `tool_id` / `tool` → `tool_name`
  - 常见中文工具名 → 公开工具 id，例如 `龙虎榜` → `dragon_tiger_list`
- condition 作用域不参与语义匹配；只有当前问题逐字包含该条件短语时才视为 `scope_verified=true`。

三种版本号不要混淆：

| 版本 | 所在位置 | 何时变化 | 是否影响答案交付 |
|---|---|---|---|
| `revision` | 单条 `long_term_memories` | 内容、作用域、目标或状态发生有效变化；确认候选、删除、过期候选清理 | 是，依赖校验使用它 |
| `management_revision` | 单条 `long_term_memories` | 使用计数、向量回填等管理操作 | 否 |
| `owner_revision` | `memory_owner_versions` | 有效记忆内容变更、删除、确认候选等 | 通过 context fingerprint 间接影响旧兼容路径 |

`context_fingerprint` 只由当前 active 快照的 `(memory_id, revision)` 排序哈希生成，不包含正文、候选或使用计数。因此：

- 删除、更新、过期会改变 fingerprint；
- 单纯使用或回填向量不会改变 fingerprint；
- 依赖级交付优先使用 `memory_dependencies`，fingerprint 只作为旧 checkpoint 的兼容路径。

### 4.3 允许写入的封闭词表和自由文本

实现位置：`app/agents/memory_policy.py`。

封闭 typed 值：

| memory_type | key | 允许值 |
|---|---|---|
| `preference` | `response_style` | `concise` / `balanced` / `detailed` |
| `preference` | `output_format` | `paragraphs` / `bullets` / `table` |
| `research_context` | `research_topic` | `首板` / `连板` / `涨停` / `炸板` / `行业` / `市场情绪` |

自由文本前缀：

```text
preference:
tool_display:
research:
```

自由文本要求：

- `value` 非空；
- 不能包含 `买入`、`卖出`、`仓位`、`目标价`、`收益承诺`、`必涨`、`稳赚`、`ignore system`、`忽略系统`、`api key`、`api_key`、`密码` 等禁用词；
- `metadata`、`topics` 不能携带额外指令或事实；
- `research_archive` 必须来自已交付且已核验的 run；检索时还要求：
  - `metadata.as_of` 是合法日期；
  - `source_run_id` 存在；
  - `metadata.source_references` 非空；
  - `metadata.evidence_references` 非空；
  - `metadata.report_version` 存在。
  任一条件不满足时，archive 不会进入普通检索。

### 4.4 显式写入路径

```text
用户消息
→ input_review 的 LLM 审核
→ 识别 MemoryIntent（save/delete/list/unsupported）
→ prepare_memory_request 冻结：
   - expected_owner_revision
   - expected_revision
   - memory_id
   - restore_deleted
   - research_archive 的 source_run_id
→ Graph memory 节点
→ execute_memory_request
→ LongTermMemoryRepository.put / delete
→ 返回 memory_action 和新的 owner_revision
```

`put` 的关键行为：

- 显式记忆命令使用 `operation_id = run_id + ":memory"`；
- 校验 `MemoryWrite` 和 `validate_memory_write`；
- 校验 agent 命令的 `source_user_message` 必须等于当前用户消息；
- 校验 `operation_id` 幂等 receipt；
- 校验 `expected_owner_revision`（如果提供）；
- 校验 `expected_revision`（0 表示新建）；
- 对同一身份加 advisory lock；
- 显式写入会把同身份 pending candidate 标记 `superseded`；
- 相同内容不重复增加 revision；
- 内容变化写入 `revision+1`；
- 删除过的记忆必须显式 `restore_deleted=true` 才能恢复；
- 写入后建立/更新 `exception_to` 关系。

`delete` 的关键行为：

- 要求正数 `expected_revision`；
- 要求 owner/lease/control 仍有效；
- 先 supersede 同身份其他 pending candidate；
- 把目标改为 `status='deleted'`、`deleted_at` 写入当前时间；
- 清空 `value`、`topics`、`metadata`、`structured`、`source_quote`、`source_run_id`、`source_user_message`、`expires_at`；
- 保留身份字段、scope、canonical key、content hash 和版本历史；
- 记录幂等 receipt。

### 4.5 后台候选提取

实现位置：`app/services/memory_extraction.py`。

#### 入队

`RunDelivery.commit` 在同一个事务内调用：

```python
enqueue_memory_extraction_in_connection(...)
```

只有同时满足以下条件才入队：

- `LIMITUPLAB_MEMORY_AUTO_LEARN != off`；
- `task_status in {complete, partial, empty}`；
- 本轮没有 `memory_action`。

即：

- 正式交付的普通对话可以学习；
- 记忆管理命令不入队；
- 失败、取消、拒绝的回答不入队。

#### Worker

- 脚本：`backend/scripts/run_memory_extraction.py`
- 默认 interval 60 秒，batch 10，lease `job:memory-extraction` TTL 300 秒；
- `_claim` 使用 `FOR UPDATE SKIP LOCKED`，状态从 `pending` 或过期的 `processing` 领取；
- 任务 lease 默认 300 秒；
- 失败最多重试 3 次，延迟 `60 × attempts` 秒；
- 成功后写 `result_json`，包括：
  - candidate_ids
  - created_count / skipped_count / candidate_count
  - decisions：add/update/delete/refresh/noop/replay
  - auto_mode
  - auto_activated_ids
  - auto_shadow_eligible_ids
  - auto_decisions
  - judge_shadow_approved_ids

#### Extractor

- Extractor 版本：`memory-extractor-v2`
- 只读取用户 `chat_messages.content`，不读取 assistant 或工具输出；
- 把最多 50 条 active memories 作为 `existing_memories` 提供给模型；
- 输出最多 5 条候选；
- `MIN_CANDIDATE_CONFIDENCE=0.7`，低于该值或 `stability=other` 直接跳过；
- 候选字段：
  - `memory_type`
  - `candidate_action=upsert|delete`
  - `content_text`
  - `canonical_key`
  - `scope_type`、`scope`
  - `structured`
  - `confidence`
  - `stability`
  - `source_quote`
  - `reason`
- `source_quote` 必须逐字出现在用户消息中；
- 禁止交易、仓位、目标价、收益承诺、密钥和敏感个人信息。

### 4.6 Consolidator 决策

`LongTermMemoryRepository.consolidate_candidate` 在同一事务中比较：

- 当前 active 同身份记忆；
- 当前 pending candidate；
- 新候选内容。

决策：

| 决策 | 条件 |
|---|---|
| `noop` | active 与新候选等价；或候选重复且目标未变 |
| `refresh` | pending 候选内容相同，但目标 revision / target 变化 |
| `add` | 没有 active，也没有相同 pending |
| `update` | 有 active，内容不同，生成 UPDATE 候选并记录目标 revision |
| `delete` | 用户明确忘记/删除已有 active，生成 DELETE 候选 |
| `supersede` 关联 | 新候选替换旧 pending 时写 `memory_relations.supersedes` |

候选：

- 以 `status='candidate'` 保存；
- 不进入 active 检索；
- 不改变 owner 有效版本；
- `candidate_expires_at = now + TTL`；
- UPDATE/DELETE 候选携带 `supersedes_memory_id` 和 `supersedes_revision`。

### 4.7 Safe Auto 与可选 Judge

实现位置：`app/services/memory_auto_policy.py`、`app/services/memory_judge.py`。

默认模式：`safe_auto`。

`evaluate_safe_auto_candidate` 只允许低风险 ADD：

- 必须是 `upsert`；
- consolidation 决策必须是 `add`；
- 不能有已有 target；
- 不能替换旧 pending candidate；
- `memory_type` 必须是 `preference` 或 `research_context`；
- `confidence` 至少 0.85，display 和 global typed 要求至少 0.9；
- `stability` 必须是 `explicit_future` / `explicit_correction` / `conditional_scope`；
- `source_quote` 必须逐字出现；
- 内容不能命中禁用词；
- quote 必须包含持续表达，如 `以后/今后/后续/每次/默认/始终/一直/记住/改成/改为/always/default/from now on`；
- typed 值必须在封闭词表内；
- typed 设置不能带额外 `structured`；
- display 只允许 `structured.display_columns`，最多 8 列，列名必须在封闭字段表内；
- tool scope 的 `tool` 必须在 `SAFE_AUTO_DISPLAY_TOOLS` 白名单中；
- tool scope key 必须等于 `tool_display:<tool>`；
- global typed 不能同时出现工具或股票代码；
- tool typed 必须是单一工具且消息中只出现该工具；
- entity typed 必须是合法 A 股代码且消息中只出现该代码；
- condition typed 短语必须逐字出现、长度不超过 80，且消息中无工具/股票代码。

模式：

| 模式 | 行为 |
|---|---|
| `off` | 不提取、不自动激活 |
| `candidate` | 只生成候选 |
| `safe_auto` | 低风险 ADD 直接 active，其他候选 |
| `safe_auto_shadow` | 记录本可自动激活的候选，不写 active |
| `safe_auto_judge` | 确定性边界候选交给 Judge，approve+low risk 才激活 |
| `safe_auto_judge_shadow` | Judge 决策只记录，不写 active |

默认是 `safe_auto`；`safe_auto_judge` 和 `safe_auto_judge_shadow` 默认不启用。

Judge：

- 只能复核通过结构安全检查、但因 confidence/stability 被拒的 scoped typed/display 候选；
- 不能覆盖结构、作用域、目标或删除边界；
- 输出只能是 `approve/hold/reject` 和 `low/medium/high`；
- 只有 `approve + low` 才激活；
- 激活记录 `activation_kind=safe_auto`，`activation_reason` 带 `judge_auto:` 前缀。

### 4.8 候选 TTL 和主动过期

- 候选 TTL 默认 30 天，环境变量 `LIMITUPLAB_MEMORY_CANDIDATE_TTL_DAYS`；
- worker 每轮先执行：
  - `expire_candidates`：过期候选清空正文、结构化字段、来源引用，写 `status='expired'` 和 `deleted_at`；
  - `expire_active_memories`：过期 active 改为 `status='expired'`，不改变内容 `revision` 或 owner revision；
- `list_candidates` 隐藏已过期候选；
- 确认候选时清空 `candidate_expires_at`。

### 4.9 检索

实现位置：`app/services/memory_retrieval.py`。

调用入口：`app/agents/memory_commands.retrieve_for_run`。

默认：

```text
memory_types = ("preference", "research_context")
max_tokens = 1200
limit = 100 active rows
```

不默认检索 `research_archive`；只有显式 `allow_archives=True` 且请求类型包含 archive 时才返回。

#### 读取过滤

`LongTermMemoryRepository.read_context` 在 SQL 中过滤：

- `owner_id`
- `status='active'`
- `deleted_at IS NULL`
- `expires_at IS NULL OR expires_at > snapshot_now`
- 指定 `memory_type`
- 指定 `exact_keys`
- 指定 `topics`
- 最多 100 条，按 `updated_at DESC, memory_id` 排序

如果开启 embedding，同时计算：

```sql
CASE WHEN embedding IS NULL THEN NULL
     ELSE embedding <=> CAST(? AS public.vector)
END AS vector_distance
```

注意：当前是 **精确 cosine 计算**，不是 HNSW ANN 查询；HNSW 索引已建，但当前查询没有 `ORDER BY embedding <=>` 或 ANN 分页。

#### Scope hint

`infer_memory_scope_hint` 从当前问题推断：

- 唯一工具
- 唯一主题
- 唯一 A 股代码

`scope_hint_for_run` 额外规则：

- 问题中没有代码时，可用请求的 `symbol` 或 `page_context.symbol` 兜底；
- 问题中已有其他代码时，以问题代码为准，不被页面标的覆盖；
- 多个工具/主题/股票时不做猜测。

`_scope_matches`：

- global：允许，verified=true；
- scoped 且缺少对应 hint：允许但 verified=false；
- scoped 且 hint 匹配：允许且 verified=true；
- scoped 且 hint 不匹配：拒绝；
- condition：当前问题逐字包含条件短语时 verified=true。

#### 词法评分

`_tokens`：

- ASCII 单词/数字，长度 ≥ 2；
- 中文 bigram；
- 去掉 `研究`、`关注`、`哪些`、`什么`、`怎么`、`如何`、`情况`。

`_rank`：

- 如果调用方提供 `exact_keys`：
  - key 命中返回 100，否则 0；
- preference：

```text
20 + topic_matches + content_matches*3 + vector_score
```

- research_context：

```text
新增 key 在 query 中：matches += 3
如果无 topic、无 content、无 vector：返回 0
否则：matches*10 + content_matches*3 + vector_score
```

- `topics` 参数命中时，每个交集额外加 2。

#### 向量评分

- `VECTOR_MIN_SIMILARITY = 0.50`
- `VECTOR_WEIGHT = 6`
- 距离在 `[0,2]` 内，相似度 = `1 - distance`；
- 相似度 ≥ 0.50 时，`vector_score = round(similarity * 6)`；
- 低于阈值不加分；
- embedding 模型不可用时 `vector_status=unavailable`，仍执行纯词法检索。

#### 作用域例外

- 同 `memory_type + canonical_key` 的 scoped 记忆和 global 记忆并存时，写 `exception_to` 关系；
- 检索时，如果 scoped 记忆 `scope_verified=true`，它会抑制同组的 global 记忆；
- scope 未验证或未选中时，不抑制 global；
- 例如：
  - global `response_style=concise`
  - tool `stock_financials` `response_style=detailed`
  - 财报问题命中 tool scope → 只保留 detailed
  - K 线问题不命中该 tool scope → 保留 concise

#### 去重和预算

- 同一 `(type, key, scope_identity)` 去重；
- 同一内容 + 同一 `as_of` 去重；
- 逐条估算 token，超过 1200 预算的候选记录为 `budget_omitted`；
- 已选中的 scoped 例外再过滤 global；
- 返回 view 时只暴露允许字段：
  - memory_id、key、memory_type、value、topics
  - scope_type、scope、scope_key、canonical_key、structured
  - scope_verified、vector_similarity
  - revision、expires_at、source_run_id
  - current_market_evidence=false
  - historical（archive）
  - archive_source（archive）
- 不暴露 owner_id、原始用户命令、任意 metadata。

#### 检索诊断

`MemoryRetrievalResult.diagnostics` 包含：

```text
memory_hit_count
long_term_memory_tokens
memory_token_budget
memory_candidate_count
memory_budget_omitted_count
memory_rejected_count
memory_exception_suppressed_count
memory_vector_status       # ready / disabled / unavailable
memory_vector_hit_count
memory_owner_revision
memory_context_fingerprint
memory_dependency_count
memory_retrieval_mode      # 历史标签 typed_exact_topic_v1
```

#### 依赖和 fingerprint

- `context_fingerprint = sha256(sorted active (memory_id, revision))`；
- 每次读取同一 owner 的 active 快照生成；
- 长期记忆到期也会改变 fingerprint；
- 依赖为：

```json
{"memory_id": "...", "effective_revision": 3, "scope_key": "tool:..."}
```

- `dependencies_unchanged_in_connection` 只检查这些依赖的 status、deleted、expires、revision、scope_key；
- `management_revision`、候选和无关记忆变化不影响依赖校验。

### 4.10 Embedding 与回填

实现位置：`app/services/memory_embeddings.py`、`backend/scripts/run_memory_embeddings.py`。

- 模型：`BAAI/bge-small-zh-v1.5`
- 维度：512
- 运行时：`fastembed==0.9.1`
- 默认启用：`LIMITUPLAB_MEMORY_EMBEDDINGS=on`
- 默认缓存：
  - 本地：`backend/data/memory_embeddings`
  - Compose：`/app/data/memory_embeddings`
- 环境变量：
  - `LIMITUPLAB_MEMORY_EMBEDDING_MODEL`
  - `LIMITUPLAB_MEMORY_EMBEDDING_CACHE`

嵌入文本：

```text
key
value
scope JSON（非 global）
structured JSON
```

`embedding_source_hash` 是上述文本的 SHA-256。

回填逻辑：

- 只处理 active、未删除记忆；
- 如果：
  - 没有 embedding；
  - source hash 变化；
  - model 名变化；
  则重新嵌入；
- 每批默认 100；
- 保存向量只增加 `management_revision`，不改变内容 `revision`；
- worker 使用 `job:memory-embeddings` lease，TTL 300 秒；
- 启动脚本在无 live lease 且 embeddings 开启时自动拉起；
- Docker Compose 的 `memory-embeddings` 常驻服务负责生产运行。

失败行为：

- `MemoryEmbeddingUnavailable` 被 retrieval 捕获，`memory_vector_status=unavailable`；
- 退回词法检索，不影响聊天；
- 模型下载失败、缓存不可写或维度不符都不会伪造向量。

### 4.11 健康检查

脚本：`backend/scripts/check_memory_health.py --json`。

检查项：

- `duplicate_active_identity`
- `candidates_missing_ttl`
- `candidates_expired_pending`
- `active_expired_pending`
- `active_delete_candidate_action`
- `candidate_delete_missing_target`
- `legacy_missing_canonical`
- `orphan_memory_relations`
- `negative_use_count`
- `active_missing_embedding`
- `active_embedding_model_mismatch`
- `active_embedding_source_mismatch`
- `active_count`
- `candidate_count`

结构性问题计入 `critical_issues`；active 过期、候选过期和 embedding 缺口属于非致命告警，不改变 `ok` 的结构健康结论。

### 4.12 候选和运维 API

前端不展示记忆候选、确认、忽略或管理入口。

owner-scoped API：

```text
GET    /api/agents/chat/memories
GET    /api/agents/chat/memories/candidates
POST   /api/agents/chat/memories/candidates/{memory_id}/confirm
POST   /api/agents/chat/memories/candidates/{memory_id}/reject
GET    /api/agents/chat/memories/by-key/{memory_type}/{key}
GET    /api/agents/chat/memories/{memory_id}
PUT    /api/agents/chat/memories/{memory_type}/{key}
DELETE /api/agents/chat/memories/{memory_id}
```

CLI：

```text
python backend/scripts/review_memory_candidates.py --owner <owner> list|confirm|reject
python backend/scripts/report_memory_auto.py --days 7
python backend/scripts/evaluate_memory_auto.py --live
```

`reject_candidate` 会清空候选正文、结构化字段和来源引用，写入 `status='revoked'` 与 `deleted_at`；确认候选时使用 CAS 校验目标 revision，目标已变化时拒绝覆盖。

### 4.13 Display Blocks

实现位置：`app/services/memory_display.py`。

- 从 scoped `tool_display:<tool>` 偏好提取 `structured.display_columns`；
- 如果没有 structured，尝试从“只显示股票名称”等稳定表达推导列；
- 当前问题明确要求其他字段时，覆盖长期展示偏好；
- 服务端从完整工具 rows 构建 `display_blocks`：
  - kind=table
  - source_tool
  - columns
  - column_labels
  - rows
  - row_count
  - displayed_count
  - ordered_symbols
  - missing_columns
  - truncated
  - display_policy_id
- 回答 system prompt 会告知模型服务端将确定性渲染哪些列，要求正文不要重复完整表格、完整行或同义逐行名单；
- 前端 `AgentAnswerMarkdown`：
  - 有 display block 时移除 Markdown 表格；
  - 纯展示块行标签列表移除；
  - 含分析文字的列表保留。

---

## 5. 运行方式与环境变量

### 5.1 关键环境变量

| 变量 | 默认值 | 作用 |
|---|---|---|
| `LIMITUPLAB_DATABASE_URL` | 必填 | PostgreSQL 业务库 |
| `LIMITUPLAB_AGENT_INPUT_TOKEN_BUDGET` | 40000 | 单次模型输入预算 |
| `LIMITUPLAB_MEMORY_AUTO_LEARN` | `safe_auto` | 自动学习模式 |
| `LIMITUPLAB_MEMORY_CANDIDATE_TTL_DAYS` | 30 | 候选有效期 |
| `LIMITUPLAB_MEMORY_EMBEDDINGS` | `on` | 是否启用向量检索 |
| `LIMITUPLAB_MEMORY_EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | 本地 embedding 模型 |
| `LIMITUPLAB_MEMORY_EMBEDDING_CACHE` | `backend/data/memory_embeddings`（本地） | 模型缓存目录 |
| `DEEPSEEK_API_KEY` / `LIMITUPLAB_LLM_*` | 视部署 | 摘要、Extractor、Judge 和 Agent 的 LLM |

### 5.2 本地启动

`scripts/start_dev_detached.py`：

- 先检查 PostgreSQL；
- 启动 backend/frontend；
- 启动或复用 recommendation-refresh、memory-extraction、memory-embeddings；
- 用 PostgreSQL lease 判断 worker 是否已运行；
- embedding 开关关闭时不启动 embedding worker。

`scripts/start_local.cmd` 额外提供 Windows 一键启动，包含记忆提取和向量回填窗口。

### 5.3 Docker Compose

`docker-compose.yml` 中的常驻服务：

```text
backend
frontend
recommendation-refresh
memory-extraction
memory-embeddings
daily-update（jobs profile）
```

- `memory-extraction` 和 `memory-embeddings` 复用后端镜像；
- 两者都挂载 `limituplab-data:/app/data`；
- `memory-embeddings` 显式设置 `/app/data/memory_embeddings`；
- PostgreSQL overlay 为两个 worker 提供数据库环境，并等待 `postgres` 健康、`postgres-migrate` 完成；
- `backend/Dockerfile` 包含 `backend/evals`，容器内可运行检索评测脚本。

### 5.4 发布与回滚

`deploy/release.py`：

- `WORKER_SERVICES = recommendation-refresh, memory-extraction, memory-embeddings`；
- 发布时：
  - 停止所有 worker、frontend、backend；
  - 备份数据库；
  - 启动目标 backend/frontend；
  - probe 健康；
  - 启动并逐个检查三个 worker 的 `.State.Running` 和 `.State.Restarting`；
- 回滚时：
  - 停止目标 worker/backend/frontend；
  - 恢复上一版镜像；
  - 再启动并检查同一组 worker；
- 任一步 worker 不稳定，发布会失败并按记录回滚。

注意：本地 Compose 演练验证了隔离 pgvector 栈、迁移、worker 租约和向量回填；**生产标签发布与生产回滚演练尚未执行**。

---

## 6. 测试与评测

### 6.1 自动测试

关键测试文件：

```text
backend/tests/test_long_term_memory_golden.py
backend/tests/test_memory_retrieval.py
backend/tests/test_memory_embeddings.py
backend/tests/test_memory_extraction.py
backend/tests/test_memory_consolidation.py
backend/tests/test_memory_auto_golden.py
backend/tests/test_memory_judge.py
backend/tests/test_memory_display.py
backend/tests/test_memory_health.py
backend/tests/test_memory_retrieval_eval.py
backend/tests/test_local_startup.py
backend/tests/test_deployment.py
```

最近一次完整验证：

- 后端：`2049 passed, 1 skipped, 22 subtests passed`
- 前端：`243 passed`
- 前端生产构建通过

### 6.2 检索评测

数据集：

```text
backend/evals/memory_retrieval_cases.json
```

脚本：

```text
backend/scripts/evaluate_memory_retrieval.py
```

命令：

```powershell
cd backend
.venv\Scripts\python.exe scripts/evaluate_memory_retrieval.py
.venv\Scripts\python.exe scripts/evaluate_memory_retrieval.py --grid
```

当前 25 条合成查询结果：

| 模式 | exact | recall | MRR | 误注入 |
|---|---:|---:|---:|---:|
| 纯词法 | 20/25 | 0.783 | 0.609 | 0/23 |
| 混合 `sim>=0.50, weight=6` | 22/25 | 0.913 | 0.707 | 1/23 |

提升主要来自无直接词法重叠的研究主题改写。仍有 2 个语义查询未被 BGE-small 召回，说明模型区分能力是当前限制。

### 6.3 真实 Agent 验收

已验证：

- 三问 `complete`：
  - 2026-10-27 涨停池
  - 贵州茅台主营业务
  - 2026-10-27 龙虎榜
- “贵州茅台最近 K 线走势”为 `partial`，原因是 `stock_kline` 数据源缺少 `change_pct` 等字段，不是记忆链路；
- 保存 `tool_display:dragon_tiger_list` 后：
  - embedding worker 成功回填；
  - 新会话“展示最新的龙虎榜”返回单列 `name` 展示块，`row_count=87`；
  - 同 owner 询问贵州茅台主营业务时没有龙虎榜展示块；
- 保存 topic-scoped 研究记忆后：
  - 用无直接词法重叠的“打板后第二天还能不能继续走强看什么”提问；
  - 该记忆 `use_count=1`，证明语义召回在真实 Graph 中生效。

---

## 7. 当前限制和风险

1. **HNSW 还不是实际 ANN 路径**
   索引已创建，但当前检索仍是 owner active 集合内精确 cosine。记忆量增大后需要单独做 ANN 查询和过滤策略。

2. **BGE-small 语义区分有限**
   25 条评测中仍有 2 个语义查询未召回，并存在 1 个弱相关误注入。模型升级或 reranker 尚未验证。

3. **自动激活边界仍然保守**
   `safe_auto` 只覆盖低风险 ADD：tool_display、封闭 typed、tool/entity/condition typed 例外。UPDATE、DELETE、自由文本和未知作用域仍需人工确认。

4. **condition 不是语义匹配**
   condition scope 只有当前问题逐字包含短语时才验证，不做同义改写。

5. **候选没有前端 UI**
   这是产品选择：长期记忆对用户无感。候选只通过 owner-scoped API 和运维 CLI 处理。

6. **评测集偏小**
   25 条合成用例不足以代表真实匿名用户表达；需要扩充真实匿名查询、跨主题负例和更多改写。

7. **生产回滚未演练**
   Compose 服务、镜像 pin、worker 稳定性检查和隔离 pgvector 演练已完成，但真实生产标签发布/回滚仍需单独窗口。

8. **历史清理依赖元数据完整性**
   新消息保存 `memory_dependencies`；旧消息没有依赖时回退到整库 fingerprint，可能过度清理旧 assistant 文本。

9. **展示块去重是结构性的**
   前端移除 Markdown 表格和纯行标签列表；含分析文字或改写后的重复描述仍可能保留，未做语义级正文裁剪。

---

## 8. 关键文件地图

| 功能 | 文件 |
|---|---|
| 会话 CRUD | `app/repositories/chat_session_repository.py` |
| 会话 API | `app/routers/agents.py` |
| 旧会话摘要只读导入 | `app/repositories/chat_memory_repository.py` |
| Durable Graph / checkpoint | `app/agents/runner/durable_runtime.py` |
| Graph 状态字段 | `app/agents/runner/runner.py` |
| 上下文组装 / 预算 | `app/agents/context_builder.py` |
| 滚动摘要 | `app/agents/conversation_memory.py` |
| 摘要生成 / fallback | `app/services/conversation_summary.py` |
| 记忆 revision 清理 | `app/services/memory_scope.py` |
| 长期记忆模型 | `app/memory_models.py` |
| 长期记忆策略 | `app/agents/memory_policy.py` |
| 显式记忆命令 | `app/agents/memory_commands.py` |
| 长期记忆仓库 | `app/repositories/long_term_memory_repository.py` |
| 长期记忆检索 | `app/services/memory_retrieval.py` |
| 本地 embedding | `app/services/memory_embeddings.py` |
| 后台候选提取 | `app/services/memory_extraction.py` |
| safe_auto 策略 | `app/services/memory_auto_policy.py` |
| 可选 Judge | `app/services/memory_judge.py` |
| 展示块 | `app/services/memory_display.py` |
| 长期记忆 API | `app/routers/memories.py` |
| 交付事务 | `app/services/agent_run_services.py` |
| 迁移 | `backend/migrations/versions/0006_long_term_memory.py`、`0008_memory_scopes.py`、`0009_memory_extraction.py`、`0010_candidate_consolidation.py` 至 `0015_memory_embeddings.py` |
| 本地启动 | `scripts/start_dev_detached.py`、`scripts/start_local.cmd` |
| 生产 Compose | `docker-compose.yml`、`docker-compose.postgresql.yml` |
| 生产发布 | `deploy/release.py` |
| 检索评测 | `backend/scripts/evaluate_memory_retrieval.py`、`backend/evals/memory_retrieval_cases.json` |
| 记忆健康检查 | `backend/scripts/check_memory_health.py` |
| 前端展示去重 | `frontend/src/components/AgentAnswerMarkdown.tsx` |
