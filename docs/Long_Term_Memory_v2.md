# 长期记忆 v2：基础契约层

更新日期：2026-10-26。

本文件记录长期记忆重构第一阶段已经落地的边界。它不是完整 v2；向量检索、后台自动学习和确定性展示块仍按后续阶段推进。

## 已落地

- 迁移 `0008_memory_scopes`：
  - 记忆作用域：`global/tool/intent/topic/entity/condition`；
  - 自由文本、canonical key、结构化字段；
  - 状态、管理版本、来源类型、逐字来源引用、内容哈希；
  - `memory_relations` 关系表；
  - 旧记录按 active/expired/deleted 回填，不统一设为 active。
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
- 写入与删除：
  - 保留 owner revision、record revision、operation receipt、tombstone；
  - 删除清空正文、结构化字段、来源引用和 source quote，保留 scope/canonical identity、内容哈希和状态；
  - 交付前 fingerprint 校验保持不变。
- 真实验收：
  - 保存“以后展示龙虎榜只显示股票名称”；
  - 新会话列出该偏好；
  - 龙虎榜回答按偏好只展示股票名称；
  - 删除后不再召回；
  - 真实 DeepSeek 四问全部 `complete`。

## 尚未落地

以下能力明确未完成，不能对外宣称：

1. pgvector 语义检索与同义改写召回；
2. 后台 Memory Extractor / Judge / Consolidator 和 candidate inbox；
3. `effective_revision` 与 `management_revision` 的完整运行时失效分离；
4. 按实际依赖记忆版本失效，而不是整个 owner fingerprint；
5. 工具级展示偏好的确定性 `display_blocks` 与服务端投影；
6. 结构化展示顺序与短期引用顺序的统一；
7. 旧数据迁移到新表后的长期双读/回滚演练；
8. 记忆评测集与自动激活校准。

## 当前数据字段

`long_term_memories` 新增：

```text
scope_type, scope_json, scope_key,
canonical_key, structured_json,
status, management_revision,
source_kind, source_quote, content_hash
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

自动学习尚未接入；当前不会因为普通对话自动写入。

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

1. 补齐 effective/management 版本分离和依赖级失效；
2. 接入 pgvector 混合检索与中文评测；
3. 后台候选提取/合并 outbox；
4. 确定性展示块和引用顺序；
5. 记忆评测集、自动激活校准和迁移演练。
