# 长期记忆 v2：基础契约层

更新日期：2026-10-27。

本文件记录长期记忆重构已经落地的边界。它不是完整 v2；向量检索和低风险自动激活仍按后续阶段推进。

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
- 迁移 `0011_memory_candidate_actions`：
  - 候选增加 `candidate_action=upsert|delete`；
  - DELETE 候选必须携带 active 目标，未找到目标时不生成候选；
  - 增加 `exception_to` 关系索引。
- 迁移 `0012_memory_auto_activation`：
  - 增加 `activation_kind=none|manual|safe_auto` 和 `activation_reason`；
  - 旧的非候选记忆回填为 manual；
  - 自动激活只由 `safe_auto` 策略写入。
- 迁移 `0013_candidate_ttl`：
  - 候选增加 `candidate_expires_at`；
  - 旧候选按 `created_at + 30 days` 回填；
  - 未确认候选默认 30 天过期，worker 每次运行时清理并清空正文。
- 自由文本偏好：
  - `preference` / `research_context` 支持 `preference:`、`tool_display:`、`research:` 前缀；
  - 保留 typed 设置（回答详略、展示格式、研究主题）的封闭值校验；
  - 交易指令、授权、目标价、收益承诺、密钥等继续拒绝。
- 作用域：
  - tool scope 使用公开工具 id，例如 `dragon_tiger_list`；
  - 中文常见工具名在服务端规范化为 canonical id；
  - entity aliases（`entity_id`/`stock_symbol`/`code`）统一为 `symbol`；
  - topic aliases（`research_topic`/`subject`）统一为 `topic`；
  - scope 不匹配的 scoped 记忆在检索时被排除；未知 scope 仍可作为未验证候选进入上下文。
- 检索：
  - owner、active、有效期、删除状态硬过滤；
  - 自由文本增加中文 bigram/ASCII 词法匹配；
  - Agent 实际检索会从当前问题推断唯一无歧义的 tool/topic/symbol scope hint；
  - 问题不含代码时，股票详情请求的 `symbol` / `page_context.symbol` 可作为兜底；
  - 问题明确包含其他代码时优先使用问题中的代码，不被页面标的覆盖；
  - 多个工具、多个主题或多个股票代码时不做猜测，保留未验证状态；
  - 返回 `scope`、`canonical_key`、`structured`、`scope_verified`；
  - 仍使用有界 token 预算。
- 作用域例外：
  - 同 `memory_type / canonical_key` 的 scoped 记忆与 global 记忆并存时，scoped 记忆写入 `exception_to` 关系；
  - 只有 scope hint 命中、`scope_verified=true` 时，检索才抑制 global 同 key 记忆；
  - condition 作用域只有当前问题包含其精确短语时才视为已验证；
  - scope 未验证或 scope 不匹配时保留 global 设置，避免未知工具下误删通用偏好；
  - 预算不足未选入 scoped 例外时，不抑制 global 设置。
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
  - 回答 system prompt 会明确告知服务端将渲染哪些工具/列，并要求正文不要重复完整表格、完整行或同义逐行名单；用户本轮明确要求完整名单时仍以用户要求为准；
  - 当前问题明确要求其他字段时覆盖长期展示偏好；
  - 短期引用优先使用展示块的 `ordered_symbols`，而不是从模型正文猜顺序；
  - 重复股票行保留展示顺序，仍可按“第二只”解析到同一 symbol；全量列举时再去重。
- 后台候选学习（candidate / safe_auto 模式）：
  - 正式交付事务写入 `memory_extraction_jobs` outbox，失败/取消/记忆管理轮不入队；
  - worker 使用 `FOR UPDATE SKIP LOCKED` 领取任务，校验 owner、来源消息、开关和已有记忆；
  - Memory Extractor 只读取用户消息，输出结构化候选；
  - 候选以 `status='candidate'` 保存，不进入 active 检索，不改变 owner 有效版本；
  - Consolidator 以 `(owner, type, canonical_key, scope)` 为身份：
    - active 行为等价（结构化字段与 scope 相同，或自由文本归一化后相同）时为 NOOP，不生成重复候选；
    - active 内容不同时生成 UPDATE 候选，并记录目标 `memory_id + revision`；
    - 同身份已有不同候选时，旧候选标记 superseded，新候选通过 `memory_relations.supersedes` 关联；
    - 同身份候选内容相同时只刷新目标版本，不重复插入；
    - 用户明确“忘记/删除/不要再记住”某条已有记忆时生成 DELETE 候选；没有 active 目标时为 NOOP；
  - 确认候选时在单事务内校验目标版本；目标被显式改动或已被占用时拒绝，不会覆盖更新的记忆；
  - 更新候选确认后旧 active 变为 superseded，新候选变为 active，并写 `supersedes` 关系；
  - 删除候选确认后目标写入 tombstone，候选本身也标记 deleted，并写 `supersedes` 关系；
  - 显式写入会先把同身份待确认候选标记 superseded，避免后续确认覆盖用户刚写入的内容；
  - `POST /api/agents/chat/memories/candidates/{id}/confirm` 显式激活；
  - `POST .../reject` 清空候选正文并标记 revoked；
  - `GET .../candidates` 列出待确认候选；
  - 前端不展示记忆候选、确认、忽略或管理入口；长期记忆只作为问答上下文使用；
  - 非自动候选只通过 owner-scoped API 供后台/运维流程处理，不进入用户界面；
  - 未确认候选默认 TTL 30 天，可通过 `LIMITUPLAB_MEMORY_CANDIDATE_TTL_DAYS` 调整；
  - worker 每次运行先清理过期候选，清空正文并标记 expired；
  - 过期候选从 `GET /candidates` 隐藏，确认时清空 `candidate_expires_at`；
  - `safe_auto` 只自动激活低风险 ADD：
    - `tool_display:*`；
    - 全局封闭 typed 设置 `response_style` / `output_format` / `research_topic`；
    - tool、entity（A 股代码）和精确短语 condition 作用域的 `response_style` / `output_format` 例外；
    - 无 active/pending 目标；
    - 默认 `confidence>=0.9` 且 `stability` 为明确未来/明确纠正；scoped typed 例外允许 `conditional_scope` 且 `confidence>=0.85`；
    - tool scope 与 key 一致；
    - `tool_display` 的 `structured` 只包含已知 `display_columns`；typed 设置 `structured` 必须为空；
    - `source_quote` 逐字出现在用户消息且包含持续表达；
    - UPDATE / DELETE / 自由文本 / 未知列 / 无目标冲突候选不自动激活；
  - 激活来源写入 `activation_kind=manual|safe_auto` 和 `activation_reason`；
  - `safe_auto_shadow` 只记录本可自动激活的候选，不写 active，用于校准；
  - worker 的 `result_json.auto_decisions` 记录每个候选的 outcome、policy reasons 和 risk，不保存记忆正文；
  - 影子/自动决策汇总：`python backend/scripts/report_memory_auto.py --days 7`，包含 judge_decisions / judge_risks；
  - 安全自动激活边界由 `tests/test_memory_auto_golden.py` 的固定用例覆盖；
  - live 校准：`python backend/scripts/evaluate_memory_auto.py --live`，输出 TP/TN/FP/FN 与逐案决策；
  - worker 命令：`python backend/scripts/run_memory_extraction.py --once`，或在本地启动脚本中自动拉起；
  - `LIMITUPLAB_MEMORY_AUTO_LEARN=off|candidate|safe_auto_shadow|safe_auto|safe_auto_judge|safe_auto_judge_shadow`；默认 `safe_auto`；
  - `safe_auto_judge` 仅对通过结构安全检查、但因置信度或条件稳定性被拒绝的 scoped typed/display 候选调用 Judge；
  - Judge 只能输出 approve/hold/reject，必须 `approve + low risk` 才生效，错误或非法输出一律 hold；
  - Judge 不能覆盖结构、作用域、目标或删除边界；激活记录 `activation_kind=safe_auto`，`activation_reason` 带 `judge_auto` 前缀；
  - `safe_auto_judge_shadow` 保留确定性 safe_auto 激活，只把边界 Judge 的批准/拒绝影子化，不让 Judge 写 active；
  - `result_json.auto_decisions` 记录 judge decision、risk、reason 和版本；
  - `judge_shadow_approved_ids` 单独记录影子批准候选；
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
  - 更新候选验收：已有展示偏好 + 新候选“显示代码和名称”，通过后台 API 确认后旧记忆 superseded、新记忆 active，`memory_relations` 写入 `supersedes`，候选列表清空；
  - 删除候选验收：对已有 `response_style` 生成 DELETE 候选，确认后目标与候选均进入 tombstone，active 列表为空；
  - 例外关系验收：global `response_style=concise` + tool `stock_financials` `response_style=detailed`，财报 scope 命中时只召回 detailed，并记录 `exception_to` 关系；
  - `safe_auto` 验收：真实 LLM 从“以后展示龙虎榜只显示股票名称”提取候选，在 `safe_auto` 模式下直接 active；`activation_kind=safe_auto`、候选队列为空、worker 结果记录自动激活 ID；
  - typed 自动激活验收：模型输出的 `structured.style/display_format` 与 dotted key 被规范化为 `response_style/output_format` 封闭值；全局“回答简洁”、tool-scoped“分析财报时详细”和 entity-scoped“分析600519时详细”均自动激活，scoped 版本分别写入 tool/symbol 作用域及 `exception_to`（存在 global 时）；condition 只有在短语逐字命中时自动激活，低置信度真实提取仍保持候选。
  - 运行时 scope 验收：真实 Graph 在“展示最新的龙虎榜”问题中把 scoped tool 记忆标记为 `scope_verified=true`，而不是只在单元测试中验证；
  - 请求上下文验收：股票详情追问“它近期走势怎么样”时，使用请求 `symbol` 作为 entity scope 兜底；问题内出现其他代码时以问题代码为准。
  - 重复榜单验收：三行展示块包含重复 symbol 时，`ordered_symbols` 保留重复顺序，`ordinal_resolvable=true`，reference 类型为 `display_block`。
  - candidate TTL 验收：候选写入 30 天 TTL，过期为 expired、正文清空、从候选 API 隐藏；确认后 active 的 `candidate_expires_at` 为空。
  - safe_auto 校准验收：10 条 live 用例覆盖 tool/global/tool-exception/entity/topic/临时/delete/行情/交易/condition，结果为 TP=5、TN=5、FP=0、FN=0。
  - Judge 验收：真实 DeepSeek 对 `confidence=0.75` 的 condition 候选返回 `approve + low risk`；单元测试验证 Judge 只能复核 scoped 边界，全局 typed 和结构不合格候选不会进入 Judge。

## 尚未落地

以下能力明确未完成，不能对外宣称：

1. pgvector 语义检索与同义改写召回；
2. 自动激活覆盖 `tool_display`、全局封闭 typed，以及 tool/entity/精确 condition 作用域的 `response_style/output_format` 例外；`safe_auto_judge` 是可选边界复核；condition 仍不支持语义匹配；scope hint 仍是词法推断，名称同义词不一定识别；
3. 非自动候选没有前端入口，只能由运维 API 处理；TTL 已清理过期候选；
4. `management_revision` 的写入方（候选、使用统计、embedding 回填）尚未接入；
5. 展示块已移除 Markdown 表格并增加 prompt 去重约束，但模型正文中的列表或普通文字仍可能重复；尚未做硬性正文裁剪；
6. 重复股票行已保留展示顺序并支持序号引用；但同一 symbol 的多个行无法区分具体是哪一条记录；
7. 旧数据迁移到新表后的长期双读/回滚演练；
8. 记忆评测集与自动激活校准。

## 当前数据字段

`long_term_memories` 新增：

```text
scope_type, scope_json, scope_key,
canonical_key, structured_json,
status, management_revision,
source_kind, source_quote, content_hash,
supersedes_memory_id, supersedes_revision,
candidate_action, activation_kind, activation_reason,
candidate_expires_at
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
→ Extractor 输出 upsert / delete 候选
→ Consolidator 对比 active / pending candidate
→ add / update / delete / noop / supersede
→ safe_auto 策略
   → 低风险 tool_display / 封闭 typed ADD / tool-scoped typed 例外：直接 active，activation_kind=safe_auto
   → UPDATE / DELETE / 其他：status='candidate'
→ 非自动候选由后台/运维 API 处理，前端不展示
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

1. 运行 `safe_auto_judge_shadow` 校准，评估边界 Judge 的误放行率；
2. 增加后台候选运维接口；
3. 接入 pgvector 混合检索与中文评测；
4. `management_revision` 写入方；
5. 抑制展示块与正文列表/文字的重复，并解析重复股票榜单的序号；
6. 记忆评测集、自动激活校准和迁移演练。
