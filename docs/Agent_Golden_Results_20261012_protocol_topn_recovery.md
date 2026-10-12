# r02 / s13 / r10 修复与 Golden 复验

日期：2026-10-12。本文记录原生工具调用协议、TopN 数量契约和模型失败后的收尾修复。原始模型回答、trace、裁判输出与自动报告只保存在本地 `output/`，不提交 Git。

## 结论与验收口径

- 同配置九场景各三次，两批均完成 **27 trials、30 回合**，无 harness error、无覆盖重跑。自动成绩由 **20 pass / 4 fail / 3 review → 22 pass / 0 fail / 5 review**，通过率 **74.07% → 81.48%**。
- s13 从 **0/3 → 3/3** 通过，最早封板 Top2 不再交付三行；r10 从 **2/3 → 3/3** 通过，均正常以 `partial / answered` 保留截断说明。
- r02 本次修复前已为 3/3 通过，修复后为 2 pass / 1 review；待复核来自缺口说明的引用类型争议。它不能被改写成成功率提升或“完全修复”的证明。
- Agent 协议异常调用 **3 → 0**；修复后未触发格式错误修复反馈或运行时部分恢复。这两条路径由离线失败注入验证，本轮真实模型只证明正常提交改善，不能宣称实际验证了线上异常恢复。
- 修复后五个待复核项分别为：r02 一项引用类型争议，r03 两项查询状态引用争议，r03 与 s14 各一次 Judge 协议失败。原始判定保留；整体质量验收仍未通过。

## 冻结条件

| 项目 | 本轮口径 |
| --- | --- |
| 修复前提交 | `7f666adb0152dc7c1fdab32af01468317a86e1b5`，`react-runtime-v33` |
| 修复后提交 | `b91df45678b8ae85a11aa3c3ac83a9b5f643d125`，`react-runtime-v34` |
| 题集 / 裁判 | `agent-golden-v1.7` / `golden-judge-v17`，尚未完成人工校准 |
| 模型 / Provider | `deepseek-v4-flash` / `LangChainChatProvider` |
| 工具环境 | `live-model-frozen-tools`，`v1_close_review`，生产 Agent + 冻结合成行情 |
| 样本 | s12、s13、s14、s16、m04、r01、r02、r03、r10，各 3 次；每批 27 trials、30 回合 |
| 调用预算 | 每批 350 次逻辑 Provider 调用；SDK 重试为 0，ReAct deadline 120 秒 |

本次为相关场景复验，不是新一轮全部 61 场景评测。上轮完整成绩仍见 [s18 / r03 修复与完整基线](Agent_Golden_Results_20261012_s18_r03.md)。三次重复用于观察本轮行为，不能估计稳定线上成功率。

两批 dataset、evaluator、模型设置、profile、deadline 和依赖版本均一致。既有比较器返回 `matched_settings=true`，识别4项改善与2项 pass→review；`--fail-on-regression` 退出码为1，没有抹去退化项。生产代码 hash 按本次修复发生变化。

| SHA-256 项目 | 值 |
| --- | --- |
| 九场景 dataset | `1c19d4b6904a0e1de48f77f21454dcb6ffeb483f51dcfda0ad85f68592aabe1f` |
| evaluator | `0b8d2b496f7a99cbf11460690e96620eb8ac6e357abc24df8e9300c8c8ac63d9` |
| 模型设置 | `261c3ca4f40e3bb8143a57046ccd791f4c17af4687bc5fb55fba39b7ebe90802` |
| 修复前 production | `150014b49c7b31ad7e9f374d9c995c2abae85e5b9f41a05d39be5e7d34528dba` |
| 修复后 production | `9b70f9008458777722592aed7521592e4775e7ce5082450bb2baecd4f3aac268` |

## 真实模型前后对比

| 场景 | 修复前（3次） | 修复后（3次） | 观察 |
| --- | --- | --- | --- |
| s12 成交额 TopN | 3 pass | 3 pass | 相邻排序场景保持通过 |
| s13 最早封板 Top2 | 3 fail | 3 pass | 最终3行变为2行；第三次实际触发行数门禁后修正 |
| s14 最低换手 Top2 | 3 pass | 2 pass / 1 review | 第三次表格正确，Judge 协议失败 |
| s16 排名窗口 | 3 pass | 3 pass | 本批指定窗口保持通过 |
| m04 追问改变数量 | 3 pass | 3 pass | 每次两回合，2只改为3只均通过 |
| r01 有效空结果 | 3 pass | 3 pass | 空结果状态正确，但业务查询次数增加，见成本说明 |
| r02 部分来源缺失 | 3 pass | 2 pass / 1 review | 执行正常；一次完整性限定被 Judge 归入市场事实 |
| r03 来源失败 | 3 review | 3 review | 均正常 partial；两次引用类型争议、一次 Judge 协议失败 |
| r10 来源截断 | 2 pass / 1 fail | 3 pass | 均交付两行并明确截断，未将匹配总数冒充已返回名单 |

关键场景的三次值依次对应 trial 1/2/3。Agent 逻辑调用包含输入审核、ReAct 与合规审核；业务查询不包含 `read_evidence`、`compute_result` 等控制工具。

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| s13 最终表格行数 | 3 / 3 / 3 | 2 / 2 / 2 |
| s13 业务查询次数 | 1 / 1 / 1 | 1 / 1 / 1 |
| s13 ReAct 调用次数 | 2 / 2 / 2 | 3 / 3 / 4 |
| s13 Agent 逻辑调用次数 | 4 / 4 / 4 | 5 / 5 / 6 |
| s13 门禁拒绝次数 | 0 / 0 / 0 | 0 / 0 / 1 |
| r02 业务查询 / ReAct / Agent 调用 | 每次1 / 2 / 4 | 每次1 / 2 / 4 |
| r02 最终状态 | 三次 partial / answered | 三次 partial / answered |
| r10 最终表格行数 | 2 / 2 / 未交表 | 2 / 2 / 2 |
| r10 业务查询次数 | 1 / 1 / 1 | 1 / 1 / 1 |
| r10 ReAct 调用次数 | 3 / 4 / 4 | 2 / 3 / 2 |
| r10 Agent 逻辑调用次数 | 5 / 6 / 5 | 4 / 5 / 4 |
| r10 协议失败次数 | 0 / 1 / 2 | 0 / 0 / 0 |
| r10 最终 stop_reason | answered / answered / provider_error | answered / answered / answered |

s13 第三次是门禁效果的直接证据：模型仍尝试交付原始3行，门禁明确拒绝；随后用原证据执行 `compute_result(limit=2)`，最终交付正确2行，业务查询仍只有1次。这比仅看三次 pass 更能说明确定性检查的作用。修复前 r10 第二次虽然发生一次协议错误，仍恢复成功；第三次累计两次协议失败后丢失表格。修复后没有再触发这类异常，不能把正常结束归功于未被执行的 fallback。

### 待复核项和成本

- r02 trial 1：最终和草稿的完整性限定被 Judge 归为 `world_fact`，却引用 `data_missing/result_state`，触发 `AvailabilityIsNotWorldFact`。同类说明在 trial 3 被归为 `query_status` 并通过；三次股票名单、数量及 partial 状态均与证据匹配。此为引用角色待校准问题，保留 review，不人工加分。
- r03 trial 2/3：说明查询未返回数据时使用的引用包含 `business_result_available=false`，被规则判为 `QueryStatusRequiresQueryEvidence`；生产均以两次业务查询、三次 ReAct 正常 `partial / answered`，没有把查询失败说成市场零记录。
- r03 trial 1、s14 trial 3：Judge 的 `submit_golden_visible_audit` 返回非法原生调用。前者 `finish_reason=length`，后者为重复调用中含非法 JSON；原始 wire 与转换结果一致。它们不属于 Agent 协议错误，也不能据此给可见事实、来源或安全维度补记通过。

| 成本与调用 | 修复前 | 修复后 |
| --- | ---: | ---: |
| Agent 逻辑调用 / 失败 | 149 / 3 | 155 / 0 |
| Judge 逻辑调用 / 失败 | 42 / 0 | 42 / 2 |
| 总逻辑调用（预算350） | 191 | 197 |
| Agent tokens | 1,285,187 | 1,396,435 |
| Judge tokens | 850,496 | 884,513 |
| 总 tokens | 2,135,683 | 2,280,948 |

全部调用的 usage 完整。两批合计388次逻辑调用、4,416,631 tokens。费用未按未经配置的单价推算。数量修复需要确定性选取和偶发修复，整批成本没有下降；r01 业务查询由4/3/3次增至5/4/5次，仍存在空结果后的额外探测。报告中的 P50/P95 包含 Judge 和多回合执行，不作为用户响应延迟。

## 根因与实现

### r02：严格保留协议校验，给模型有效修复反馈

历史 r02 曾多次触发 `NativeFunctionCallingError`；本次修复前 r02 三次均通过，因此不能宣称复现了相同的历史失败率。本次实际捕获的三个协议异常均来自 r10：`finish.missing` 直接使用未加引号的日期文本，缺少 JSON 数组括号和字符串引号；`answer` 字符串合法。原始 wire 参数与 SDK 转换后的 chunk 拼接结果逐字一致，失败位置一致，未发现 SDK 拼接破坏。历史 r02 没有逐字符原件，不能认定其错误字段必然相同。

明确 `missing` 是字符串数组、无缺口时为 `[]`。首次原生协议错误后的既有修复机会会收到格式反馈，保留已取得证据，不通过重新查询业务修复格式。Provider 继续严格拒绝非法 JSON；不补引号、不使用正则改写参数、不增加隐式重试。生产 trace 仅记录白名单结构诊断，排除原始参数、异常文本与凭据。

原件由临时进程内观察器记录：只捕获失败 tool-call 参数及 chunk，不读取请求头或密钥；已用流式/非流式、合法/非法四种离线组合核对请求、回调、结果和异常保持不变。两批使用相同观察器；观察器和原件留在忽略目录，未修改生产 Provider 或评测器。

### s13：把用户数量要求纳入交付契约

修复前用户要求最早封板的 2 只股票，业务工具实际返回 3 行，模型直接引用全部 3 行交付。工具 `limit` 与用户最终展示上限是两个不同约束。

Input Reviewer 输出本轮 `row_selection`，保留明确数量或排名窗口的原意、`limit` 和 `offset`。最终门禁读取 EvidenceStore 完整行数，超出上限就拒绝，让模型在已有证据上通过 `compute_result` 排序与选取，再交付。服务端不静默截表，也不放宽来源截断检查；完整集合本来不足 N 时可如实展示，不能补造行。

行数门禁只证明上限，不证明排序、偏移和成员正确。语义识别仍依赖 Input Reviewer；当前追问覆盖旧数量、改为全部或只问计数，以及旧 checkpoint 兼容均有离线回归，真实语义效果以本轮 Golden 为准。

### r10：保留已取得数据，同时如实标记失败

累计两次模型调用失败触发 `provider_error` 时，旧兜底丢失代码表和具体来源截断说明。新恢复路径仅接受本轮唯一可用数据集、明确且已解析的展示字段、未被标记为过期的证据、没有待澄清输入的表格任务；沿用 `data_fresh` 标志，不额外校验 TTL。任何 `row_selection` 均不自动交表，避免把尚未核验的 TopN/排名范围当成已完成。

恢复答案经过现有证据渲染、交付校验及独立合规审核；审核前后检查取消与 deadline。它明确保留 `partial / provider_error`，说明范围与完整性尚未全部核验，并保留来源截断和缺失提示。失败审核后不会再通过通用兜底回显被拒绝的单元格。

恢复使用独立 `react_fallback_delivery` trace，记录 `origin=runtime`；不伪造模型 `finish` 或 `answered`。现有 Golden 若遇到这种兜底，仍可能因缺少正常模型交付判失败；本轮没有修改裁判来掩盖异常。

## 离线验证

新增 48 个参数化回归项：4 项 Provider 线协议、26 项数量契约与运行时修复、18 项协议恢复与失败边界。覆盖真实形状的非法 `missing`、流式与非流式严格拒绝、脱敏诊断、已有证据复用、超行拒绝后确定性选取、较小完整集合、来源截断、追问覆盖、旧 checkpoint、歧义/历史/过期证据、排名窗口、取消、超时及合规拒绝。

完整验收 `output/validation/20261012T040332Z-851f2196/`：**后端 1,952 passed、22 subtests passed，前端 94/94，TypeScript/Vite 生产构建通过**。保留既有 3 条依赖弃用警告与 bundle 大小提示。代码提交增删共 672 行，符合单次不超过 1,000 行的约定。

离线脚本模型测试证明契约、门禁和失败分支行为，不等于模型总能理解用户语义。Golden 使用合成行情，不验证真实外部数据源、浏览器 HTTP/SSE 表现、生产部署或预测准确率。

## 原件与复现

| 本地路径（不提交） | 内容 |
| --- | --- |
| `output/golden/r02-s13-r10-before-20261012/` | 修复前报告、30回合证据、诊断计划和3份生产协议失败原件 |
| `output/golden/r02-s13-r10-after-v34-20261012/` | 修复后报告、30回合证据、诊断计划和2份 Judge 协议失败原件 |
| `output/golden/r02-s13-r10-after-v34-20261012/metrics-comparison.json` | 逐回合行数、调用、错误、门禁、状态与 token 对照 |
| `output/golden/r02-s13-r10-v34-comparison-20261012.json` | 既有比较器输出，保留4项改善、2项退化 |
| `output/diagnostics/native_protocol_capture_20261012.py` | 两批共用的失败参数观察器，诊断计划保留运行参数 |
| `output/validation/20261012T040332Z-851f2196/` | 完整离线验收、日志与 JUnit |

修复前 `report.json` SHA-256：`3206373acc23e3839934180fa203d9beb5404c7d5716adb29a7e4a9a355a7f85`。修复后：`3f0de87c19564a84c5564bd25b4917429853a363d6c5c519f5703ad1fef18293`。

正式评测入口如下。需现有本地模型配置，会产生真实调用；基线应在对应源码提交运行，使用新目录避免覆盖原件。两批实际运行还套用了上述只观察参数的临时包装器，`capture_errors` 均为空。

```powershell
backend/.venv/Scripts/python.exe backend/scripts/run_agent_golden.py --mode live --judge model --profile v1_close_review --case s12_amount_top --case s13_earliest_seal --case s14_low_turnover --case s16_rank_window --case m04_change_limit --case r01_valid_empty --case r02_partial_source --case r03_source_error --case r10_source_truncation --trials 3 --max-model-calls 350 --output output/golden/<新的运行目录>
backend/.venv/Scripts/python.exe backend/scripts/compare_agent_golden.py output/golden/r02-s13-r10-before-20261012/report.json output/golden/r02-s13-r10-after-v34-20261012/report.json --fail-on-regression --output output/golden/<新的比较结果.json>
backend/.venv/Scripts/python.exe scripts/check_project.py
```

对应问题记录为 [BC-131 / BC-132 / BC-133](../badCase.md)。后续优先独立校准 Judge 的协议和引用类型，再扩大排名/追问语义与错误恢复样本；r01 额外探测也应纳入效率评估。本轮没有改动评分、预测、Outcome 或真实本地业务数据，未部署。
