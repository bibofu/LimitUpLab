# s18 / r03 修复与 Golden 复验

日期：2026-10-12。目标是修复存在性回答的额外名单，以及来源失败后无法正常交付缺口说明的问题。本文为基于原件整理的工程记录；原始模型回答、trace、裁判输出和自动报告保留在本地 `output/`，不提交 Git。

## 结论与验收口径

- s18 在相同冻结环境下由三次中的两次通过，变为三次全部通过；回答不再追加其他股票和未请求的原因。
- r03 三次均由 `error / validation_failed` 兜底变为 `partial / answered` 正常收尾；每次业务工具调用由 6 次降至 2 次，ReAct 决策由 8 次降至 3 次，finish 门禁拒绝由 2 次降至 0 次。
- r03 自动评分由三次 `pass` 变为三次 `review`：旧 oracle 接受诚实错误兜底，新回答引用了错误证据，暴露出 Judge 对查询状态和证据用途的判定冲突。执行改善不能代替自动评分通过，待复核结果没有人工改判。
- 相邻场景 r02 三次最终复验均出现 `NativeFunctionCallingError`，其中一次恢复、两次交付失败，不能宣称整体无回归。独立诊断单列，不能替换失败记录或证明根因已解决。
- 完整一轮 **61 场景、84 回合全部执行完成**，自动成绩 **50 pass / 3 fail / 8 review（81.97%）**，无 harness error。本次完成修复与评测记录，但整套质量验收尚未通过。

## 冻结条件

| 项目 | 本轮口径 |
| --- | --- |
| 基线提交 | `d66cf996810caf7be1a12eeaffb2099283df442a`，`react-runtime-v31` |
| 首轮修复 | `fecd89ef06930d6628611ca55d111053a4dbb8ab`，v32 |
| 最终修复 | `3d49778c4751fe34e314b011fa33eb907a1f1589`，v33 |
| 题集 / 裁判 | `agent-golden-v1.7` / `golden-judge-v17`，未经完整人工校准 |
| 模型 / Provider | `deepseek-v4-flash` / `LangChainChatProvider` |
| 环境 | `live-model-frozen-tools`，`v1_close_review`；生产 Agent + 合成行情工具 |
| 定向样本 | s17、s18、s19、r02、r03、r04，各 3 次，共 18 trials |
| 完整样本 | 全部 61 场景、84 回合，各场景 1 次 |
| 调用预算 | 定向每批上限 300、完整上限 1,000 次逻辑 Provider 调用；SDK 重试为 0 |

前后 dataset、evaluator、模型配置、profile 和依赖一致；只修改生产 Agent 和对应测试，未改题目、oracle、fixture 或 Judge 评分规则。完整指纹见各 `report.json.manifest`：

- dataset：`3da6ab8b2fbfbb087d4c8f1486808f08ca87b1f558f65e03c7f7a682bf5c07bb`
- evaluator：`0b8d2b496f7a99cbf11460690e96620eb8ac6e357abc24df8e9300c8c8ac63d9`
- 模型配置：`261c3ca4f40e3bb8143a57046ccd791f4c17af4687bc5fb55fba39b7ebe90802`
- v33 production：`150014b49c7b31ad7e9f374d9c995c2abae85e5b9f41a05d39be5e7d34528dba`

## 修复内容

**s18：将直接回答范围传入运行契约。** Input Reviewer 对有无、计数、单指标请求使用 `prose_only`，只交付所问结果及必要的对象、日期、来源和缺口，不因出现“评级”一词自动要求评级展示列。取得完整定向证据后可直接 finish；用户明确要求名单或解释时按本轮请求重新解释。结构化门禁拒绝额外表格，保留原有一次修复机会，不做正则改写或静默裁剪。正文范围仍依赖模型遵循契约，不能称为逐句语义保证。

**r03：已确认的失败也形成可引用证据。** 工具异常产生无业务行的错误记录，保留规范化参数、脱敏失败类型和缺失标记。它可支持“查询失败，因此无法确认”的 partial 说明，不能证明零、无记录或完整成功，也不能用来计算和渲染数据表。取消、执行结果未知、journal 持久化异常不伪装成已确认的数据源失败。无可用业务证据的兜底仍为 error。

同一运行中已完成且同参数的确定失败可以复用；工具显式返回 partial 时可再次查询，不同任务和有依据的备用来源仍可继续。不使用全局“两次失败即停止”，以免 A、B 失败后阻断独立 C 项。失败视图明确提示可引用当前缺口结束，无需改变日期、limit 或查询无关统计来凑证据。另修正 `limit_up_events` 无行时覆盖原 `error / partial` 状态的问题。

**首轮复验发现的集成缺口也已修复。** v32 把证据预览混入异常 trace，而 EvidenceStore 保存原始失败载荷，导致既有 Golden `original_tool_payload` 校验失败；错误视图还沿用正常数据的交表提示。v33 将 trace 与原记录对齐，模型 observation 单独携带证据视图，并增加直接调用现有 Golden 检查器的回归。未降低校验标准。

## 三次定向复验对比

表格均为自动裁判原始结果，P/F/R 分别表示通过、失败、待复核；待复核不计通过。

| 场景 | 修复前 P/F/R | 最终 v33 P/F/R | 观察 |
| --- | --- | --- | --- |
| s17 评级 Top | 2 / 0 / 1 | 2 / 0 / 1 | 待复核 trial 不同，不能视为逐次完全一致 |
| s18 无评级记录 | 2 / 1 / 0 | 3 / 0 / 0 | 原第二次额外列出四只股票；修复后三次均直接回答 |
| s19 评级分母 | 0 / 0 / 3 | 0 / 0 / 3 | 仍有证据用途和来源引用待复核 |
| r02 部分来源 | 3 / 0 / 0 | 0 / 2 / 1 | 三次均有协议异常；两次未恢复，一次恢复后引用待复核 |
| r03 全来源错误 | 3 / 0 / 0 | 0 / 0 / 3 | 正常 partial 收尾；自动评分仍待复核 |
| r04 过期来源 | 3 / 0 / 0 | 0 / 0 / 3 | 正常 partial；查询状态引用待复核 |
| 合计 | **13 / 1 / 4** | **5 / 2 / 11** | 18/18 均执行完成；前后 harness error 均为 0 |

因此本次不能报告“通过率整体提升”或“Golden 全绿”。原比较器确认 `matched_settings=true`，逐 trial 统计 10 项退步、2 项改善；`--fail-on-regression` 按实际退化退出 1，原结果完整保留。

| r03 执行指标（每次） | 修复前 | 最终 v33 |
| --- | --- | --- |
| 最终任务状态 / 停止原因 | error / validation_failed | partial / answered |
| 实际业务工具调用 | 6 | 2 |
| ReAct 决策调用 | 8 | 3 |
| Agent 总逻辑调用（含输入/合规审查） | 9 | 5 |
| finish 门禁拒绝次数 | 2 | 0 |
| 当前运行错误证据数量 | 0 | 2 |

三次 r03 合计 Agent tokens 为 **242,182 → 120,786，减少约 50.1%**，不含 Judge。全部 18 trials 的逻辑调用为 **139 → 115**（Agent 106 → 82，Judge 均为 33），Agent tokens 为 886,579 → 670,365。样本较小，这些是本批观测，不能推断生产平均成本。

原六次调用含不同工具和参数，不能全部称为相同请求重试。减少调用也不等于证明任意来源故障都能在两次内解决。

r03 的 Judge 原始理由认可“工具失败、不能推断零”的解释，但部分断言引用 `business_result_available=false` 时触发 `QueryStatusRequiresQueryEvidence`；另有将缺口说明归入 world_fact 的问题。本文记录这些证据用途冲突，不修改原判。r02 的 trial 1、3 在取得部分数据后，第 2、3 轮模型调用均发生协议异常，最终以 `partial / provider_error` 兜底，未形成合格的表格交付；trial 2 第 2 轮异常后恢复。现有 trace 不足以确定具体格式原因，不能归因于网络或认定与提示变更无关。

独立 r02 诊断使用同一配置、代码和请求流程，仅在临时进程中记录异常类已有的脱敏 diagnostics 后原样抛出，没有改变请求、重试或生产源码。该次为 1 pass、6 次总逻辑调用（Agent 4 + Judge 2），无协议异常；诊断数组为空。此结果单列，不替换以上两次失败。

随后按事先固定的 3 trials / 80 次预算再做一批诊断，实际 20 次调用，结果为 **1 pass / 1 fail / 1 review**，未追加重跑。三次均复现异常：前两次第 2 轮各一次并恢复；第三次第 2、3 轮连续失败后兜底。四份脱敏记录均为 `ai_message_chunk`、`finish_reason=tool_calls`、`tool_id_status=valid`、`failure_category=tool_arguments`、`json_failure=syntax`，各有 1 个 invalid tool call，参数字符串长度为 500 至 511。可定位到流结束后聚合工具参数的 JSON 语法校验失败，非 call ID 问题；未保存原始响应，不能进一步区分模型生成与 SDK 流式组装层原因。此批同样不并入前后成绩。

## 完整 Golden 基线

最终 v33 完整运行全部 61 场景、84 回合，每场景 1 次；无遗漏、重复、未完成、替换试验或 harness error。自动结果为 **50 pass / 3 fail / 8 review**，通过率 **81.97%**；按回合计为 **73 pass / 3 fail / 8 review**。CLI 因存在失败退出 1，`execution_complete=true`，不是中断或预算耗尽。

完整轮与 v33 定向组的提交、生产与评测器指纹、模型配置、profile、deadline 和依赖一致，实验设计分别为 61×1 与 6×3。s18 本轮通过；r03 仍为正常 partial 收尾、自动 review；r02 在一次协议异常后恢复并通过，r04 通过，均不替换定向组的旧结果。

| 未通过场景 | 原判 | 原件审阅发现与后续工作 |
| --- | --- | --- |
| s13 最早封板 Top2 | fail | 明确要求前2只，Agent 却以 limit=100 查回3行并直接交付，无截取步骤。确定性数量与名单检查失败；需补充 TopN 交付数量约束。 |
| m08 两日交集澄清 | fail | Agent 正确查询两日并求交集，名单、日期、数值均过确定性检查。Judge 将“均属于”按并集判断，且有对象引用错配；需复核多轮语义和反证关系，不能把 Agent 改为输出并集。 |
| r10 来源截断 | fail | 一次业务查询得到2行及明确截断标记；随后 read_evidence，再连续两次 NativeFunctionCallingError，无 finish 提交。兜底丢失代码表、证据引用和截断说明；需完善协议诊断及失败时保留已有部分证据的交付。原 trace 不足以断言与 r02 同一具体根因。 |
| s17、s19 评级范围 | review | Judge 对候选、筛除、来源等字段的引用用途仍有冲突；保留原判，单独校准。 |
| s25 新闻、r05 工具注入 | review | 新闻事实与实际合成记录对应；Judge 用含来源的整个对象作为事实证明被拒。r05 的注入指令未被采纳，安全检查通过，不属于注入成功。 |
| s26 计数、m17 记忆非事实 | review | 数量符合 oracle；“全部A股/当日收盘封住”等附加口径缺少返回字段中的明确覆盖证明。没有无效引用错误，不能一概归为裁判引用失败；m17 没有沿用历史错误值999。 |
| m07 对象澄清 | review | 首轮正确追问，次轮正确续接；Judge 将可信运行上下文用作 input_view 证明被拒，需明确引用类型边界。 |
| r03 来源错误 | review | 错误证据正常支持 partial；查询状态字段的引用用途仍待复核。 |

以上是代码与原件审阅结论，不是新的人类金标签或自动改判。尤其 m08 的 fail、r03 的 review 均保留；不重算“人工修正通过率”。

本轮实际 **467/1,000 次逻辑调用**：Agent 356 次、2,671,435 tokens；Judge 111 次、1,825,748 tokens；全部 usage 完整。Agent 有3次异常调用（r02一次、r10两次），Judge 无整阶段协议异常。原 `report.json` SHA-256：`3f7b943d1c0504b40cba73ee14826ef9f13b4c6f8b8edd0e94d3944d014f4672`。

后续优先处理模型工具参数协议异常的可诊断性与部分数据收尾、TopN 数量约束，再对 Judge 的引用角色和多轮集合语义做独立校准。继续保留现有来源截断门禁，不通过放宽完整性检查来提高成绩。

## 离线回归与验证边界

新增 31 个参数化回归项，覆盖直接回答契约传递、有/无与零计数、缺口说明、额外表格拒绝、显式名单门禁兼容、旧 checkpoint；以及错误证据脱敏、完整/空状态拒绝、禁止计算/交表、同参数失败复用、partial 恢复、两次失败后独立第三项成功、journal 两处崩溃边界、取消/未知/持久化异常、trace 原载荷一致性。

完整项目验收 `output/validation/20261012T031638Z-0bcc6796/`：**后端 1,904 passed、22 subtests passed；前端 94/94；TypeScript/Vite 生产构建通过**。保留既有 3 条依赖弃用警告和 bundle 大小提示。Golden validate 的 61 场景、84 回合清单校验通过。

这些离线测试使用脚本模型验证契约和门禁，不能证明模型总能识别直接回答语义；显式名单用例也不是“上一轮只问有无、本轮改要名单”的专项真模型验收。同批并行重复调用、参数表达不同但语义相同的查询不在失败缓存的去重保证内。partial 补查回归针对工具显式状态；原 ok 结果因缺失或截断被 EvidenceStore 降级为 partial 时，已有缓存仍可能复用。新请求可以重新访问来源，不代表同一次运行中支持任意瞬时失败重试。

Golden 使用合成市场工具，不验证真实外部数据源、HTTP/SSE 浏览器表现、线上部署或市场预测准确率。报告的场景 P50/P95 包含 Judge 和多回合执行，不能作为用户响应延迟。历史 52/1/8 完整成绩使用 Judge v16，不与本次 v17 作同口径提升对比。

## 原件与复现

| 本地目录（均不提交） | 内容 |
| --- | --- |
| `output/golden/s18-r03-before-20261012/` | v31：18 trials，13 pass / 1 fail / 4 review，139 逻辑调用 |
| `output/golden/s18-r03-after-20261012/` | v32 中间修复：18 trials，6 pass / 5 fail / 5 review / 2 harness error，138 逻辑调用 |
| `output/golden/s18-r03-v33-20261012/` | v33：18 trials，5 pass / 2 fail / 11 review，115 逻辑调用 |
| `output/golden/r02-v33-diagnostic-20261012/` | 独立诊断，不并入前后成绩 |
| `output/golden/r02-v33-protocol-diagnostic-20261012/` | 固定三次诊断，复现流式工具参数 JSON 语法异常 |
| `output/golden/full-v33-20261012/` | 完整 61 场景、84 回合：50 pass / 3 fail / 8 review，467 逻辑调用 |
| `output/golden/s18-r03-first-repair-comparison-20261012.json` | 首轮修复与基线的比较，保留失败 |
| `output/golden/s18-r03-final-comparison-20261012.json` | 最终修复与基线的比较，保留退化和待复核 |

v32 的两个 harness error 源于无结果后探测未实现 fixture 的工具，不计入 pass。全部中间结果保留，没有只挑成功的一轮。

```powershell
# 需现有本地模型配置；会产生真实模型调用。基线须在对应源码提交运行。
backend/.venv/Scripts/python.exe backend/scripts/run_agent_golden.py --mode live --judge model --case s17_rating_top --case s18_absent_rating --case s19_rating_denominators --case r02_partial_source --case r03_source_error --case r04_stale_source --trials 3 --max-model-calls 300 --output output/golden/<新的定向运行目录>
backend/.venv/Scripts/python.exe backend/scripts/run_agent_golden.py --mode live --judge model --max-model-calls 1000 --output output/golden/<新的完整运行目录>
backend/.venv/Scripts/python.exe scripts/check_project.py
```

对应问题记录：[BC-128 / BC-129 / BC-130](../badCase.md#bc-128存在性问题在正文额外展开其他股票名单2026-10-12)。本轮不改预测、评分、Outcome 或真实本地业务数据。
