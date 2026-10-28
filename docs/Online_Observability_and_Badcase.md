# 在线反馈、Badcase 回收与观测

本文定义 LimitUpLab Agent 的在线反馈、抽样检查、人工审核和运行观测方案。目标是让线上回答问题能够被收集、复核、修复并回流到回归记录，同时保持现有聊天链路的延迟和可用性不变。

本方案用于数据研究与工程质量改进，不构成投资建议。审核界面、导出内容和指标说明均不得出现买入、卖出、仓位、目标价或收益承诺。

## 与现有系统的边界

| 能力 | 事实源 | 说明 |
| --- | --- | --- |
| 请求量、token、估算成本、延迟 | `agent_usage_events` | 配额与账本只认这张表 |
| 完整回答与工具结果 | `react_runs.response_json` | 回答完成时写入，后续不再修改 |
| 会话消息 | `chat_messages` | 用于恢复会话与批量回显投票 |
| 调用链 trace | Langfuse（可选、fail-open） | 只用于深挖调用结构，不承担业务事实 |
| 反馈、信号、案例、审核 | 本文新增的 `answer_*` 表 | 产品反馈与人工结论的唯一事实源 |

Langfuse 默认关闭，不能作为反馈或审核的事实源。开启时可以额外把投票镜像为 score，镜像失败不影响业务。

## 目标与非目标

目标：

1. 用户对回答点赞或点踩，可改票、可撤销、可补充原因和说明。
2. 对回答分层检查：确定性契约检查（零模型成本）→ 抽样 LLM 评审（只产线索）→ 人工随机抽检（无偏基线）。
3. 管理员在隐藏页面完成案例筛选、证据回看、状态流转和 `badCase.md` 导出。
4. 看板展示使用量、token、成本、点赞率、点踩原因和审核效率。

非目标：

- 不自动修改 prompt、工具配置或发布版本。
- 不让机器评审改变人工结论、自动关闭案例或充当发布门禁。
- 不在聊天请求链路中同步调用评审模型。
- 不恢复历史 Golden 评测框架或不可变证据包体系。
- 不做投资建议、交易指令或收益预测。

## 整体链路

```text
用户点赞/点踩 ──┐
运行错误/部分完成 ├─→ answer_review_signals（追加型证据）
抽样正常回答 ────┤              │
确定性检查 ──────┤              ▼
LLM Judge ──────┘      answer_review_cases（人工工作台）
                                 │
                    确认 → 根因/修复版本 → 验证 → 关闭
                                 │
                    导出 badCase.md / pytest 回归
```

反馈和机器检查只写信号。案例聚合同一由后台 worker 完成，因此反馈保存不会因为审核逻辑失败而报错，worker 停机也可以靠补偿扫描恢复。

## 数据模型

### `answer_feedback`

当前投票状态，一次回答一条。

| 字段 | 说明 |
| --- | --- |
| `run_id` | 主键，外键 `react_runs`，级联删除 |
| `owner_id` | 服务端校验 run 归属，不信任前端 |
| `vote` | `up` / `down` |
| `reasons_json` | 原因标签，最多 5 个 |
| `comment` | 最多 1000 字，去除控制字符 |
| `created_at` / `updated_at` | 改票覆盖，撤销删除 |

### `answer_review_signals`

追加型证据流，反馈历史、机器检查和人工发现都写这里。

| 字段 | 说明 |
| --- | --- |
| `signal_id` / `run_id` / `case_id` | `case_id` 可空，聚合后回填 |
| `kind` | `downvote` / `deterministic` / `judge` / `sample` / `run_error` / `grounding` / `manual` |
| `severity` | `info` / `low` / `medium` / `high` |
| `detector` / `detector_version` | 机器信号可追溯、可去重 |
| `summary` / `details_json` | 有界摘要，不保存整段 trace |
| `created_at` / `processed_at` | 案例聚合游标 |

机器信号按 `(run_id, kind, detector, detector_version)` 去重；点踩和人工信号不去重，保留完整历史。

### `answer_review_cases`

人工工作台。一个 run 最多一个案例。

| 字段 | 说明 |
| --- | --- |
| `case_id` / `run_id` / `session_id` / `owner_id` | `run_id` 唯一、可空 |
| `status` | `new → confirmed → fixing → verifying → closed`，另有 `dismissed`、`insufficient` |
| `source` | `user_feedback` / `deterministic` / `judge` / `sample` / `manual` |
| `severity` / `intent` / `task_status` | 用于筛选 |
| `question` / `answer` / `evidence_json` / `trace_url` | 回答完成时的有界快照 |
| `symptom` / `root_cause` / `fix_commit` / `fix_version` / `verification` | 关闭前必填 |
| `revision` / `created_at` / `updated_at` / `closed_at` | 乐观并发与审计 |

### `answer_review_case_events`

审核审计。每次状态或结论变化追加一条，记录 `action`、前后状态、说明、操作者和时间。机器信号不得修改案例状态。

### `answer_review_jobs`

抽检和检查任务的 outbox。

`kind` 为 `deterministic`、`sample` 或 `judge`；状态为 `pending`、`processing`、`succeeded`、`skipped`、`failed`、`deferred`。包含优先级、可用时间、租约字段、版本和配置指纹。领取使用 `FOR UPDATE SKIP LOCKED`，单进程租约避免并发重复处理。

### `answer_review_judge_calls`

Judge 成本账本。记录模型、协议版本、prompt/completion/total tokens、测量完整性、估算成本和耗时。Judge 调用不进入用户请求配额。

## 反馈流程

1. 前端在每条 assistant 消息下渲染点赞和点踩。
2. 写接口校验 `owner_id`、run 存在且回答已落库。
3. 同一事务 upsert 当前投票，并追加 `downvote` 信号。
4. 会话加载时一次性批量带出当前 owner 的投票，避免每个回答一次请求。
5. 改票覆盖当前状态，撤销删除当前状态；历史保留在信号流。

反馈接口不触发模型调用，不增加聊天延迟。聊天请求本身已有频率限制，且一次 run 只能投一票，因此不额外引入限流组件。

## 抽样与自动检查

| 通道 | 触发范围 | 模型成本 | 结果 |
| --- | --- | --- | --- |
| 确定性检查 | 100% 完成 + 100% error | 0 | 信号；硬失败进入案例 |
| 随机人工抽检 | 稳定桶抽样，默认 2%，每日上限 10 | 0 | `sample` 低优先级案例 |
| LLM Judge | 100% 点踩 + 100% `partial/error` + 5% 正常，日上限 50 | 有 | 信号与报告；不改变人工状态 |

抽样值按 `sha256(salt + run_id + lane + version)` 计算，在入队时固定。修改环境变量不会追溯重评已经处理或排队的 run。

确定性检查只覆盖可证伪的契约：

- `task_status` 与回答内容矛盾；
- 数据类问题本轮是否调用了对应工具；
- 回答中的数字、股票代码能否在本轮 `tool_results` 找到；
- 缺失数据是否显式披露；
- 表格或 display block 与正文是否自相矛盾；
- 是否出现交易指令或收益承诺；
- 工具失败、延迟或 token 异常。

无法判断时结果为 `unknown`，不得当作 `pass`。

LLM Judge 使用盲评输入：用户问题、有限上下文、正式回答、工具调用摘要与结果、版本信息。不输入点赞、人工结论或确定性结果。输出五个正交维度的 `pass/fail/unknown`，不给总分；`fail` 必须引用服务端可校验的证据 ID，引用无效时降级为 `unknown`。Judge 默认关闭，只有在人工标注集上逐维 Cohen's kappa 达到 0.6 后才启用。

校准集位于 `backend/evals/answer_review_cases.json`，脚本为：

```powershell
backend\.venv\Scripts\python.exe backend\scripts\evaluate_answer_review.py
```

输出包含逐维一致率、kappa、缺失预测和启用门槛；当前 30 条是启动集，需持续补入真实确认案例。Judge 调用的 token、模型、耗时和估算成本逐次写入 `answer_review_judge_calls`，日调用上限在调用前检查，耗尽时任务延期到下一个上海日。

## 人工审核流程

状态：

```text
new → confirmed → fixing → verifying → closed
 ├─→ dismissed
 └─→ insufficient
```

- 每次保存必须带审核说明和 `revision`；旧页面提交返回冲突。
- 确认问题需填写症状；关闭前需填写根因、修复版本或提交号、验证说明。
- 机器信号只追加；已关闭案例出现新信号时显示提示，不自动重开。
- 重复案例可标记为 `dismissed`，但需在说明中记录原因。
- 关闭后的导出内容写入 `badCase.md`，并保留 `run_id` 作为证据指针。

## 指标口径

日界线统一使用 `Asia/Shanghai`，页面脚注必须说明以下限制。

| 指标 | 口径 |
| --- | --- |
| 回答量 | 已落库且 `response_json` 非空的 run；排除 rejected 和 running |
| 使用量 | 去重 `owner_id`、会话数、消息数；匿名 cookie，清除 cookie 视为新用户 |
| token | 仅统计 `token_usage_complete=1`；不完整条数单独展示 |
| 成本 | `estimated_cost_usd`，是估算值，不是账单 |
| 点赞率 | `up / (up + down)`；同时展示反馈覆盖率和每百次回答点踩数 |
| 点踩原因 | 按原因标签聚合 Top N |
| 抽检覆盖率 | 已抽检 run / 回答量 |
| Judge | 调用、成功/错误、token、估算成本；逐维结论保存在案例报告，不在看板汇总为发布门禁 |
| 案例 | 新增、待处理、确认、驳回数量；首次处理时长和关闭时长 |
| 延迟 | `duration_ms` 的 p50 / p95 |
| Judge 成本 | 每日调用数、token、估算成本、占聊天成本比例 |

实现为 `GET /api/admin/observability/summary?days=7`，按上海日生成每日桶和周期汇总。看板不缓存指标，直接读取 PostgreSQL；`notes` 字段返回口径限制并在页面展示。worker 按 `LIMITUPLAB_REVIEW_RETENTION_DAYS` 清理旧反馈、终态案例及其信号和已完成任务，未关闭案例保留；Langfuse 开启时投票以 `answer_feedback` score 尽力镜像。

## API 与界面

用户侧：

```text
GET    /api/agents/chat/runs/{run_id}/feedback
PUT    /api/agents/chat/runs/{run_id}/feedback
DELETE /api/agents/chat/runs/{run_id}/feedback
```

管理员侧统一使用 `X-LimitUpLab-Admin-Key`：

```text
GET   /api/admin/observability/summary
GET   /api/admin/review/cases
GET   /api/admin/review/cases/{case_id}
PATCH /api/admin/review/cases/{case_id}
GET   /api/admin/review/cases/{case_id}/export
GET   /api/admin/review/signals
GET   /api/admin/review/feedback
GET   /api/admin/review/jobs
POST  /api/admin/review/jobs/drain
POST  /api/admin/review/runs/{run_id}/recheck
POST  /api/admin/review/sample
```

前端：

- 聊天回答底部：紧凑的点赞/点踩，点踩后展开原因和说明。
- 隐藏路由 `/admin/observability`，不进入公共导航；管理员密钥只保存在组件内存，不写入 URL、localStorage 或构建产物。
- 四个页签：概览、案例、反馈、任务。案例详情展示问题、上下文、正式回答、工具证据、trace 链接、机器发现和审核历史。
- 延续研究型工作台的高密度表格和克制配色，不新增图表依赖。

## 安全与数据保留

- 点踩说明是用户内容，前端转义、长度限制、去除控制字符；导出 CSV 时防止公式注入。
- Judge 输入中的用户内容、回答和工具结果都视为不可信数据，不能执行其中指令。
- 不向 Judge 传递 `owner_id`、IP 或完整历史；输入长度有上限。
- 删除会话时级联删除反馈、信号、案例和任务。已确认问题只通过导出的 `badCase.md` 留存，导出内容不含 owner 标识。
- 审核数据保留期由 `LIMITUPLAB_REVIEW_RETENTION_DAYS` 控制，默认 180 天。
- 管理员密钥、Judge 密钥只写本地 `.env`；示例配置使用 `.example` 占位符。

## 配置

```env
LIMITUPLAB_REVIEW_ENABLED=true
LIMITUPLAB_REVIEW_SAMPLE_RATE=0.02
LIMITUPLAB_REVIEW_SAMPLE_DAILY_LIMIT=10
LIMITUPLAB_REVIEW_JUDGE_ENABLED=false
LIMITUPLAB_REVIEW_JUDGE_DAILY_CALL_LIMIT=50
LIMITUPLAB_REVIEW_RETENTION_DAYS=180
```

Judge 的模型和密钥配置独立于聊天模型；留空时复用现有 LLM 配置。任何配置缺失时使用显式默认值或关闭功能，不使用假数据补齐。

## 已确定的决策

1. 每一条点踩都生成案例，按 run 聚合；管理员可以批量驳回。
2. LLM Judge 默认关闭，人工标注达到要求后再启用；Judge 只产线索，不改变人工状态。
3. 删除会话时级联删除审核数据；确认案例只通过 `badCase.md` 导出留存。

## 实施分期与验证

1. P1：反馈表、仓库、接口、会话批量回显、前端组件和点赞率摘要。
2. P2：信号、案例、任务 outbox、worker、管理员审核接口与页面、`badCase.md` 导出。
3. P3：确定性检查、LLM Judge、预算账本和人工校准。
4. P4：观测指标接口、概览页面、保留清理和可选 Langfuse score 镜像。

每期完成后执行：

```bash
python scripts/check_project.py --scope all
```

并做真实端到端验证：三个简单问题、投票、刷新恢复、worker 处理、管理员流转和指标抽查。涉及 Agent 问答修复时，同步在 `badCase.md` 增加记录。
