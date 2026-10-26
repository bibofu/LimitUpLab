# 长期记忆 v2：基础契约层

更新日期：2026-10-27。

本文件记录长期记忆重构已经落地的边界。它不是完整 v2；向量检索、候选删除和低风险自动激活仍按后续阶段推进。

## 已落地

- 迁移 `0008_memory_scopes`：
  - 记忆作用域：`global/tool/intent/topic/entity/condition`；
  - 自由文本、canonical key、结构化字段；
  - 状态、管理版本、来源类型、逐字来源引用、内容哈希；
  - `memory_relations` 关系表；
  - 旧记录按 active/expired/deleted 回填，不统一设为 active。
- 迁移 `0010_candidate_consolidation`：
  - 移除旧的 key-only 唯一约束，改为 active 记忆按 `(owner, type, key, scope)` 唯一；
  - 同一 key 的不同 scope 不再互相覆盖；
  - 候选增加 `supersedes_memory_id`、`supersedes_revision`，记录它要替换的确切目标版本。
- 自由文本偏好：
  - `preference` / `research_context` 支持 `preference:`、`tool_display:`、`research:` 前缀；
  - 保留 typed 设置（回答详略、展示格式、研究主题）的封闭值校验；
  - 交易指令、授权、目标价、收益承诺、密钥等继续拒绝。
- 作用域：
  - tool scope 使用公开工具 id，例如 `dragon_tiger_list`；
  - 中文常见工具名在服务端规范化为 canonical id；
  - scope 不匹配的 scoped 记忆在检索时被排除；未知 scope 仍可作为未验证候选进入上下文。
- 检索：
  - owner、active、有效期、删除状态硬过滤；
  - 自由文本增加中文 bigram/ASCII 词法匹配；
  - 返回 `scope`、`canonical_key`、`structured`、`scope_verified`；
  - 仍使用有界 token 预算。
- 依赖级交付失效：
  - 检索返回实际使用的 `(memory_id, effective_revision, scope_key)`；
  - Graph 状态和公开响应保存 `memory_dependencies`；
  - 交付只校验这组依赖，无关记忆变化不会阻止回答；
  - 被使用记忆修改/删除/过期仍返回 `memory_changed`；
  - `management_revision` 变化不影响交付；
  - 空依赖集不会被后续新增记忆误判失效。
- 对话清理按依赖判断：
  - assistant 消息保存生成时的 `memory_dependencies`；
  - 只有它实际使用过的记忆变化时才移除该 assistant 文本并重建摘要；
  - 无关记忆变化保留历史回答；
  - 旧消息没有依赖元数据时，才回退到全量 fingerprint；
  - 恢复执行时，working messages 也按当前依赖校验，而不是全量 owner 变化。
- 确定性展示块：
  - `tool_display` 记忆可从 `structured.display_columns` 或“只显示股票名称”等稳定表达推导展示列；
  - 服务端从完整工具 rows 生成 `display_blocks`，保留 `row_count`、`ordered_symbols`、缺失列和截断标记；
  - 前端确定性渲染展示块；
  - 展示块存在时，前端移除模型正文中的 Markdown 表格，避免同源表格重复；
  - 当前问题明确要求其他字段时覆盖长期展示偏好；
  - 短期引用优先使用展示块的 `ordered_symbols`，而不是从模型正文猜顺序。
- 后台候选学习（candidate 模式）：
  - 正式交付事务写入 `memory_extraction_jobs` outbox，失败/取消/记忆管理轮不入队；
  - worker 使用 `FOR UPDATE SKIP LOCKED` 领取任务，校验 owner、来源消息、开关和已有记忆；
  - Memory Extractor 只读取用户消息，输出结构化候选；
  - 候选以 `status='candidate'` 保存，不进入 active 检索，不改变 owner 有效版本；
  - Consolidator 以 `(owner, type, canonical_key, scope)` 为身份：
    - active 行为等价（结构化字段与 scope 相同，或自由文本归一化后相同）时为 NOOP，不生成重复候选；
    - active 内容不同时生成 UPDATE 候选，并记录目标 `memory_id + revision`；
    - 同身份已有不同候选时，旧候选标记 superseded，新候选通过 `memory_relations.supersedes` 关联；
    - 同身份候选内容相同时只刷新目标版本，不重复插入；
  - 确认候选时在单事务内校验目标版本；目标被显式改动或已被占用时拒绝，不会覆盖更新的记忆；
  - 更新候选确认后旧 active 变为 superseded，新候选变为 active，并写 `supersedes` 关系；
  - 显式写入会先把同身份待确认候选标记 superseded，避免后续确认覆盖用户刚写入的内容；
  - `POST /api/agents/chat/memories/candidates/{id}/confirm` 显式激活；
  - `POST .../reject` 清空候选正文并标记 revoked；
  - `GET .../candidates` 列出待确认候选；
  - 会话面板提供“记忆候选”工作台：显示作用域、候选正文和逐字来源；更新候选标记“确认后替换现有记忆”；确认或忽略后更新列表，操作失败保留候选并给出可重试提示；
  - worker 命令：`python backend/scripts/run_memory_extraction.py --once`；
  - `LIMITUPLAB_MEMORY_AUTO_LEARN=off|candidate`；`safe_auto` 尚未实现。
- 写入与删除：
  - 保留 owner revision、record revision、operation receipt、tombstone；
  - 删除清空正文、结构化字段、来源引用和 source quote，保留 scope/canonical identity、内容哈希和状态；
  - 交付前 fingerprint 校验仅作为旧 checkpoint/旧响应的兼容路径。
- 真实验收：
  - 保存“以后展示龙虎榜只显示股票名称”；
  - 新会话列出该偏好；
  - 龙虎榜回答按偏好只展示股票名称；
  - 删除后不再召回；
  - 真实 DeepSeek 四问全部 `complete`；
  - 更新候选验收：已有展示偏好 + 新候选“显示代码和名称”，工作台显示“确认后替换现有记忆”；确认后旧记忆 superseded、新记忆 active，`memory_relations` 写入 `supersedes`，列表不再有候选。

## 尚未落地

以下能力明确未完成，不能对外宣称：

1. pgvector 语义检索与同义改写召回；
2. 候选 DELETE 动作与“例外于某条记忆”的提取/审批尚未实现；
3. `safe_auto` 自动激活尚未实现；当前候选必须显式确认；
4. `management_revision` 的写入方（候选、使用统计、embedding 回填）尚未接入；
5. 展示块目前只抑制 Markdown 表格；模型正文中的列表或普通文字仍可能与展示块重复；
6. 只有 `ordered_symbols` 唯一时才能用展示块解析“第二只”；重复股票的榜单仍不可按序号引用；
7. 旧数据迁移到新表后的长期双读/回滚演练；
8. 记忆评测集与自动激活校准。

## 当前数据字段

`long_term_memories` 新增：

```text
scope_type, scope_json, scope_key,
canonical_key, structured_json,
status, management_revision,
source_kind, source_quote, content_hash,
supersedes_memory_id, supersedes_revision
```

`revision` 继续作为有效内容版本；`management_revision` 预留给候选、使用统计和 embedding 回填，当前不会影响 fingerprint。

## 写入路径

```text
用户显式记忆命令
→ input_review 输出 MemoryIntent
→ 服务端规范化 tool scope/key
→ validate_memory_write
→ long_term_memories（active）
```

普通对话自动学习走独立候选路径：

```text
正式回答提交
→ memory_extraction_jobs（outbox）
→ worker 读取用户消息
→ Extractor 输出候选
→ Consolidator 对比 active / pending candidate
→ add / update / noop / supersede
→ status='candidate'（显式确认后才 active）
```

## 检索路径

```text
owner + active + 未过期 + 未删除
→ 作用域可验证/待验证
→ 自由文本词法匹配 + typed 偏好
→ token 预算
→ 上下文
```

`scope_verified=false` 表示当前工具/意图尚未确定，模型只能把它当作候选，不能当作全局偏好。

## 下一步

1. 候选 DELETE 提取、例外关系和批量合并审批；
2. 接入 pgvector 混合检索与中文评测；
3. `management_revision` 写入方、`safe_auto` 低风险自动激活；
4. 抑制展示块与正文列表/文字的重复，并解析重复股票榜单的序号；
5. 记忆评测集、自动激活校准和迁移演练。
