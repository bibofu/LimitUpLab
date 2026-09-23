# 评测套件入口

文件关系、三种执行路径和后续计划统一见[评测集导航](../../../docs/Agent_Evaluation_Guide.md)。

## 当前真实 Golden Dev（2026-09-23）

当前28题Active入口在本地`output/agent-eval/real-golden-admission-20260923-001/active-dev28-final/suite.json`。
仓库保留`real-dev28-active-index.json`准入索引、`real-dev30-design.json`题目设计、
`real-dev28-user-reviewed-answer-labels.json`逐题人审标签摘要。28题当前答案为20通过、8不通过；
答案失败不取消题目准入。Q09/Q23题目已认可，因历史新闻执行绑定阻断，暂不在可运行28题中。
Case/World录制及只读数据库均在被忽略的本地`output`，索引不是可运行数据包。
统一dry-run现有`public_limit_down`传递限制，14道Historical Live会在预检误报`unscorable`；
不将其记作Agent或题目失败。具体绑定和限制见`docs/Agent_Evaluation_Real_Golden.md`。

## 历史 Local30：20 Offline + 10 Historical Live

题目入口是 `local30.json`，不是新增一题就新增一个 Python 生成器。每题定义问题、能力标签、
日期和独立选择口径；通用构建器生成现有 CaseSpec、实际工具录制、标准事实、摘要和批量审核单。
该文件是可执行构建配方，不是原来的 40/48 非运行蓝图。

## 范围与状态

- 20 道 Offline、10 道 Historical Live；所有 Live 均为真实本地生产工具执行，非冻结返回。
- 业务范围限 `market_summary`、`limit_up_events`、本地 `market_event_pool`；远端跌停被显式禁用。
- Offline 仍通过 `execute_frozen_calls` 回放；真实参数路线未录制时为 fixture_failure，不能算 Agent 错。
- Live 启动时只读加载源数据，真实执行基线请求核对漂移；不提供生产 HTTP、外部接口或全 Profile 覆盖。
- 2026-09-08/10/11 是经过用户同意的数据窗口；9/13 周末锚定检验 latest_local。
  日历仅列已加载日期，**不能拿来计算连续 N 个交易日**。后续 Outcome 占位字段不作为事实标签。
- 当前配方为 suite v3 / case v5；题目是候选，不能把生成 30 份文件称作 30 道已审核 Active Golden。
  既有 OFF-010/LH-004 v1 Active 及其审批不变；修改后的题目不继承旧摘要审批。

## 判分与过程

集合题对照独立原始行口径和实际工具完整分区；检查日期、代码名称、完整成员，排序题另验顺序。
取数路径不是固定脚本：允许完整池取数后过滤、排序和读取分页。录制条数不代表题目数。
9/8 全量触板事件超过工具 100 条上限，全集由完整封板/未封板分区核对，不把截断页当作全集。

沿用轨迹/终态检查、Evidence 可见性与计算过程诊断、回答事实抽取。现支持有界 select
相等/数值范围过滤的独立复算，以及其结果的可见性；未知运算仍弃判。
股票名称仅允许空白显示差异，代码及其他字符不做模糊匹配。自动抽取仅为诊断；
遗漏的额外声明、语义歧义、未校准表述仍为 needs_review，不能据此自动批准题目或发布。
澄清/拒答题检验禁止业务工具调用和终态，回答语义按题内 rubric 人工复核，不做无关数字抽取。
本批不宣称覆盖多轮记忆、动态工具依赖、来源部分失败、外部服务或整套工具目录。

## 构建、运行、审核

在 backend 目录执行；输出目录必须是新目录，不覆盖旧运行：

```powershell
.venv/Scripts/python.exe -X utf8 -m app.agent_eval build-dataset --recipe evals/suites/local30.json --database data/limituplab.sqlite --output-dir ../output/agent-eval/datasets/local30-NEW
.venv/Scripts/python.exe -X utf8 -m app.agent_eval run-dataset --bundle ../output/agent-eval/datasets/local30-NEW --database data/limituplab.sqlite --output-dir ../output/agent-eval/runs/local30-NEW --allow-llm
```

`--case-ids OFF-010 LH-004` 可选择子集，不能选择不存在的 ID。运行逐题启动独立进程，
每次使用新 session/message ID；不共享生产限流租约、工具池或会话日志。
每题最多 16 次模型调用、240 秒；费用上限按用户授权不设限，仍记录真实 token。
批量执行不自动重试、也不以重跑覆盖失败；因数据漂移、录制缺口、模型服务等失败保留归因。

输出入口：

- `suite.json`：数量、相对路径、案例/基线摘要、审核状态。
- `REVIEW.md`：全部问题、考点、标准事实、终态和审核勾选项。
- `world.json` / `world-weekend.json`：共享录制，避免每份题目复制一套输入数据。
- `recordings/`：真实工具结果、来源摘要和 record/replay 证据。
- 运行目录每题保存 response、trajectory、process、facts、usage 等；总表为 `batch-results.json`。

先检查口径/标准事实/判分规则，再登记与具体摘要绑定的业务确认；技术诊断和人工批准是两回事。
真实数据、回答、录制和报告都在被忽略的 output 中，不提交 Git。

## 2026-09-14 实际交付入口

- 题库包：`output/agent-eval/datasets/local30-v4/suite.json`，含304条真实录制路线。
- 集中审核：同目录 `REVIEW.md`；30题问题和标准事实全部可见。
- 首轮：`output/agent-eval/runs/local30-001`，30次真实运行。
- 补录/改题复跑：`output/agent-eval/runs/local30-002`，8次真实运行；不覆盖首轮。
- 最终复核总表：`output/agent-eval/reviews/local30-001/README.md`。

38次运行共233次模型调用、1,546,832 Token。最终选用的30份回答均无执行不可评分项；
28道业务题核心事实诊断通过，OFF-035/036的实际终态均为complete，分别违反clarify/refuse。
原文确实在追问/拒绝，因此记录为**终态与内容不一致**，不宣称实际提供了交易指令。
这些是诊断结果，额外声明、语义遵循和抽取校准没有自动通过，不能输出“28/30质量通过率”。

复核命令不调用模型，逐题核对语义和时间锚点一致；旧回答不能用于改题，原执行失败也不能被抹掉：

```powershell
.venv/Scripts/python.exe -X utf8 -m app.agent_eval recheck-dataset --bundle ../output/agent-eval/datasets/local30-v4 --runs ../output/agent-eval/runs/local30-001 ../output/agent-eval/runs/local30-002 --output-dir ../output/agent-eval/reviews/local30-NEW
```

OFF-041/049及LH-033已改用9/8：全市场未封板37只、主板34只，可检测是否遗漏市场过滤。
OFF-050改为“主板+首板+开板后回封”，避免9/11“全市场至少2板”和“主板至少2板”事实集合相同。
旧题及旧运行保留，不将其记为额外题目。新增同类题仅编辑JSON和录制路线；新增判分能力才改通用代码。
