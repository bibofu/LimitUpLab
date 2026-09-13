# Agent 评测资产

当前完成 M1 的类型、加载层、Frozen Registry、单工具 Record-Replay、候选资产导出，
以及轨迹/终态检查、审核抽取接口与窄范围 Fact Verifier。单题隔离 Runner 和真实 LLM
声明抽取已接通并实际运行；正式题库、经校准的自动抽取器和完整 Fact Evaluator 尚未完成。

可执行模型位于 `app/agent_eval/models.py`，加载与引用校验位于 `loader.py`。
资产采用 UTF-8 JSON，可通过每个 Pydantic 模型的 `model_json_schema()` 导出规范。
设计文档中的 YAML 是可读示意；JSON 是当前实际支持的存储格式。

- `CaseSpec`：Profile、用户交付项、断言意图、终态及版本化 World 引用。
- `WorldSpec`：带时区的参考时间、独立的最新本地日期、明确覆盖范围的交易日历和录制。
- `RunManifest`：版本、模型、资产摘要及显式预算。费用上限可显式为 null，表示已授权费用不限；
  不等于用零代替未知费用，也不取消调用次数、Token 与时长边界。
- `EvalResult`：失败与弃权不能被汇总成 Pass，未知 Token 保留为 null。

所有测试中的市场数据均为标明来源的合成协议样本，不是正式 Golden 或市场事实。
主 World 计划参考 2026-09-11 18:00 Asia/Shanghai，已完成单工具本地数据录制核验；
周末协议测试使用 9 月 13 日作为参考日期、9 月 11 日作为最新本地日期。

执行类型层测试（仓库根目录）：

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_agent_eval_assets.py -q
```

`FrozenAgentToolRegistry` 通过生产 Gateway 的 `execute_frozen_calls` 协议接入：

```python
registry = FrozenAgentToolRegistry(world)
with registry.anchored():
    response = run(request, registry, provider)
```

- 使用生产工具目录和参数 Schema，默认 Profile 24 个业务工具，extended 26 个。
- 只回放精确有效参数；省略参数与显式生产默认值等价，只有 Schema 允许的 null 可以省略。
- 未录制参数组合抛 `FrozenFixtureError`，绝不退回真实工具或伪造 empty。
- 录制保存已经过 `payload_of()` 的标准化对象；Gateway 冻结分支不再二次转换。
- 结果状态、来源错误和名单按录制回放，每次返回独立副本；调用尝试保留在 `attempts`。
- `events` 只提供最新本地日期元数据，用于现有 Gateway/Runtime 日期上下文，并不伪造市场事件。
- `anchored()` 设置参考日期并在退出时恢复；实际 Runtime 工具线程的 ContextVar 传播有测试。
- 交易日历当前只做范围校验；不会宣称已冻结所有底层服务时钟或实现交易日解析器。

## Record-Replay

`recorder.py` 通过生产 ToolGateway 执行一次工具，保存原始 ToolResult、标准化完整
payload、模型可见 Evidence View、原始调用参数和工具规范化后的 input，以及来源清单。
全量 payload 不会被八行预览替代；回放比较仅忽略证据 ID 和获取时间两个运行标识。
来源错误保留在 outcome 中，不为回放方便擅自添加到原 payload。

从 backend 目录执行（不调用 LLM）：

```powershell
.venv/Scripts/python.exe -m app.agent_eval record-local-summary --database data/limituplab.sqlite --anchor 2026-09-11T18:00:00+08:00 --output ../output/agent-eval/recordings/market-summary-20260911.json
.venv/Scripts/python.exe -m app.agent_eval validate-capture ../output/agent-eval/recordings/market-summary-20260911.json
```

- 当前只读入口只开放真实 `market_summary(include_limit_down=False)`，不初始化生产
  服务、不联网取跌停数据、不读写生产会话；SQLite 使用 `mode=ro` 并显式关闭连接。
- 精确读取锚点对应的上海本地日期；缺数据直接报错，不回退 sample 或其他日期。
- SHA-256 校验录制正文；三个结构指纹覆盖原始结果、payload、view 的所有已观察行
  结构。写入前和加载时重新校验，文件存在则拒绝覆盖。校验和不是防伪签名。
- `verify_replay` 比较 payload、状态、来源错误、freshness、稳定 view 和结构；只有全部
  一致才保存。`validate-capture` 仅检查文件完整性，不等于回放通过或事实正确。
- 结构指纹是观测快照，不是完整工具输出 Schema：空集合不能证明行结构；发现生产工具
  漂移还需重录并比较指纹，旧录制自校验不能证明新生产实现仍兼容。
- 产物保存在被忽略的 `output/`，标记 `privacy_status=unreviewed`。工具原始输出可能
  包含敏感信息，不自动上传或提交；正式晋升前必须审核/脱敏并重新生成校验摘要。
- 当前来源清单记录日期、行数和所选行摘要，不复制数据库路径或数据库。该快照来自
  当前本地库，不能保证不存在历史修订；单日观测日历只服务此验证，不冒充完整交易日历。

已实录核验：2026-09-11 本地 58 条事件、40 条封板，七项回放检查通过。该记录不是
正式 Golden，也不代表真实 Agent 回答通过。

## 候选 World/Case 与部分确定性检查

从已验证录制导出一个待审核候选（在 backend 目录执行）：

```powershell
.venv/Scripts/python.exe -m app.agent_eval prepare-summary-candidate ../output/agent-eval/recordings/market-summary-20260911.json --output-dir ../output/agent-eval/candidates/local-summary
```

目录包含 `case.json`、`world.json` 和 `review.json`。导出前重新检查录制及回放一致性，
要求本地数据日期等于锚点日期、涨停数量合法、实际工具输入确认不取远端跌停数据。
目录存在则拒绝覆盖；来源录制摘要和 Case/World 摘要写入 review 清单。
这些文件只保留在本地 output，不自动进入正式资产目录、Git 或 active 状态。
写盘中断产生的不完整目录应另选新目录重试，不把不完整文件当作已审核资产。

候选问法：查询最新本地交易日的涨停家数并标明日期，不查跌停。预期事实从录制取值，
仍须人工核验源数据、交付项拆解和 complete 口径；跌停字段缺失并非本题 missing。
单日 World 不能覆盖“上一交易日/最近 N 个交易日”。当前只生成候选，不完成晋升审批。

`evaluators.py` 读取真实 runtime 的 `react_decision`、`react_policy`、`react_execution`
和 `react_answer_check`，不读取 UI 的工具列表猜测行为：

| 检查 | 当前判定规则 |
| --- | --- |
| trajectory / tool_required | target 为业务工具名；至少一次调用被 Policy allow/reuse。只证明选择被接受，不证明有充分证据或回答正确 |
| trajectory / tool_forbidden | target 为业务工具名；任意尝试都 fail，包括 Policy 拒绝的尝试 |
| trajectory / argument_equals | target 为 `tool.parameter`；使用生产参数 Schema 补默认值，每次尝试的该参数都必须符合 expected，不允许最后一次正确掩盖前面的错误 |
| `$terminal` | 实际返回状态必须在 Case.allowed_status 中，错误 complete 显式 fail |
| `$missing` | 当前只确定判定预期和最终 missing 都为空；非空自然语言的交付项映射一律 needs_review，暂不计算 Recall |
| 其他 evaluator/kind | needs_review，包括 fact、compute、evidence、safety 和动态依赖，不能默认 pass |

原始决策参数用于判断 `requested_as_of`，避免工具规范化 input 丢掉日期参数。
工具输出已成功不等于答案事实已交付；未调 `update_task` 不等于遗漏用户交付项。
缺失/不兼容/不完整 trace、错误的断言工具名或参数配置进入 needs_review，而非 Agent fail。
`$` 开头的 assertion ID 保留给内部检查。Profile 必须由执行方明确传入，不从工具列表推测。

检查一份已经保存的 `AgentChatResponse`（不产生任何模型调用）：

```powershell
.venv/Scripts/python.exe -m app.agent_eval check-response --case ../output/agent-eval/candidates/local-summary/case.json --response ../output/agent-eval/response.json --profile v1_close_review
```

报告 scope 固定为 `trajectory_terminal`，release_eligible 始终 false。任一确定失败则
整体 fail，否则任一弃权则 needs_review，其余才是此范围的 pass。退出码分别为 1、2、0。
这不是完整 EvalResult 或发布 Gate，尚不做故障根因归属、回答声明覆盖率或模型质量汇总。
包含 Fact 断言的当前候选，即使轨迹/终态全部通过，也必须保留 needs_review。

测试通过脚本 provider 驱动真实 runtime 和 Frozen Registry，安全审查也是显式测试替身；
每次使用新 session/message ID，禁止网络及数据库访问。这只能验证评测基础设施，不能
证明真实模型或生产安全审查通过。默认 `check_project.py` 保持无密钥验收。

## 审核抽取接口与 Summary Fact Verifier

`facts.py` 把声明清单、声明抽取、单位归一化和证据核验分开。本节的审核型核验入口不
自行调用 LLM，真实自动抽取见下一节。`Extraction` 是独立标注文件，必须绑定**实际返回答案**的 `digest(answer)`
及逐字匹配的原文起止位置；不能给模型预填 expected_facts 再当作其回答声明。

- `inventory`：独立审核的全部原子声明清单，每项包含 id、start/end、quote，及 numeric /
  relational 标签；包含答案主动补充的事实，不只是题目要求的事实。
- `claims`：抽取出来的实体、日期、指标、数字 token、单位和 certainty，与 inventory ID
  一一关联。中文省略、并列、否定等语义由抽取/审核方确认，Verifier 不猜测。
- `inventory_complete`、`inventory_reviewers`、`extraction_reviewers`：两项审核分别记录。
  P0 各至少两个不同审核者，其他级别至少一个。代码不会自动填写真实审核记录；这些字段
  是审计元数据而非身份认证，正式晋升仍须外部核实，不得仅填写名字绕过审核。
- `origin=contract_test` 仅用于合成契约校准，不能据此声称真实样本经过人工复核。

报告给出 Claim / Numeric / Entity-Date-Metric Coverage：分母分别为审核清单中全部 /
numeric / relational 声明，分子为关联抽取记录数。它们衡量**清单覆盖**，不是事实通过率、
抽取准确率或中文理解准确率。没有分母时为 null；零抽取或漏抽会保留 needs_review。
清单本身是否漏标仍依赖独立审核，后续须用完整双标校准集测量这一误差。

当前证据适配器只支持 `market_summary` 的涨停、首板、连板、未回封、跌停家数：

1. 从版本匹配的 World 录制建立事实集合，不把手写 expected 当作唯一真值。
2. 从 `react_execution.evidence` 取得本轮完整 Evidence，排除历史和不可用记录。
3. 从 `react_observe` 读取模型实际看到的 metadata/rows；用生产 EvidenceStore 重建视图
   交叉检查，记录支持路径。视图/完整数据/World 漂移先 needs_review，不误判为 Agent 编造。
4. 逐条核验已审核声明，包括用户没有要求但回答主动添加的事实。世界里正确、当前没有
   查询或没有可见证据的声明不能通过。实体/日期/指标/数值错误与单位维度错误显式 fail。
5. 再检查 required facts 是否交付。expected 与 World 矛盾是标注问题，进入 needs_review。

数字采用 Decimal，保留精度并区分数量、金额、百分比和百分点；支持显式的万/亿换算，
不删除逗号或乱拼中文数字。不确定、约数、未知单位/指标进入 needs_review，当前不擅自
设定“约”的容差。金额等单位的归一化测试不代表对应业务事实适配器已实现。
当前未覆盖个股、窗口、集合派生、因果、缺失披露或完整 Compute Evidence 来源闭包。

```powershell
.venv/Scripts/python.exe -m app.agent_eval check-summary-facts --case ../output/agent-eval/candidates/local-summary/case.json --world ../output/agent-eval/candidates/local-summary/world.json --response ../output/agent-eval/response.json --extraction ../output/agent-eval/extraction.json
```

省略 extraction 会明确 needs_review，不能自动补出声明。报告 scope 为
`reviewed_summary_numeric_claims`，release_eligible 恒 false，退出码 pass/fail/review 为
0/1/2。这是独立的窄范围报告，`check-response` 的 Fact 断言仍不会被自动替换成通过。

`tests/test_agent_eval_facts.py` 提供版本控制的 Verifier/Normalizer 校准种子：正常空格、
表格、单位变体、约数、否定不确定、错误实体/日期/数量、额外声明、无工具接地、历史
Evidence、可见路径漂移和漏抽。测试中的声明来自显式合成标注，不是自动抽取器产物；
因此尚未测得自动抽取 FP/FN，也不能替代设计中的 120～180 条双人复核校准集。

## 单题真实 LLM 闭环（实验性）

```powershell
.venv/Scripts/python.exe -m app.agent_eval run-offline --case ../output/agent-eval/candidates/local-summary-step4/case.json --world ../output/agent-eval/candidates/local-summary-step4/world.json --output-dir ../output/agent-eval/runs/new-run --allow-llm --wall-seconds 240
```

这个命令会实际调用已配置的外部 LLM，必须确认目的地及数据外发授权。输入包括问题、
Agent 系统提示、工具定义、冻结业务证据和生成答案；密钥仅用于认证，不放进提示或报告。
默认 `check_project.py` 不调用此入口，仍强制关闭 LLM。

- `runner.py` 创建一个独立子进程，`worker.py` 只支持单轮 Offline 的 candidate/active Case。
  该进程有自己的 runtime TOOL_POOL，直接运行 runtime，不走生产 HTTP 限流租约和 journal；
  显式清空 CURRENT_CONTROL，并禁止 sqlite3.connect。每次创建新 session/message/run ID。
- 工具仍全部经过 Frozen Registry，不会因为没录制就回退真实工具。输入安全和回答合规
  审查保留真实 Provider，不用测试替身；Agent 规划、审查、抽取全部计入调用账本。
- 当前每次只运行 1 题，最多 16 次模型调用、200 万输入 Token、10 万输出 Token，默认
  240 秒。费用不限（null）；时长范围 1～900 秒，父进程另留 15 秒启动/清理宽限。
  Token 以实际返回 usage 计量，调用后记账、下一次调用前熔断；不是事前精确 Token 保证。
  usage 不完整时保留未知，总调用数和时限仍有效，不将未知 Token 记为真实零消耗。
- 无测试通过导向的自动重跑。子进程超时被终止，已产生的逐次请求/响应保留；不完整运行
  返回 unscorable。输出目录存在就拒绝覆盖，避免覆盖失败样本。
- 保存 manifest、输入资产副本及摘要、请求身份、源代码摘要、每次 Provider 请求/响应、
  完整 AgentChatResponse、Frozen 匹配记录、抽取、分项核验、统一 EvalResult 和 usage。
  数据只保存在被忽略的 output，privacy_status 为 unreviewed；不提交或自动上传报告。
  没有价格配置时 estimated_cost_usd=null，不猜价格或将其写成0。

`extractor.py` 使用真实模型和结构化工具调用遍历回答全文。它只收到答案和抽取规则，
不接收 expected facts、World 或工具返回，避免把真值抄成答案声明。原文引用必须逐字
存在，宿主确定偏移；重复片段通过 occurrence 定位，伪造引用直接报抽取错误并保留原输出。
非数字、未知语义及不能解析的声明也必须留下项目，不能直接丢弃。

自动抽取产生 `origin=model`、`inventory_complete=false`，不填写任何人工审核者。
诊断 FactReport 使用 `provisional_summary_numeric_claims`，单条结果只代表在该抽取假设
下的核验；独立 Claim/Numeric/Relation Coverage 全部为 null，不能拿模型自报清单当分母。
必须建立独立标注校准集后，才能评价抽取漏报率及决定哪些结果可以自动裁决。

统一结果替换轨迹报告中的 Fact 占位断言，并保留逐声明诊断。已知录制匹配失败优先标记
fixture_failure；运行停止于 Provider 错误标 provider_failure；抽取协议失败标 evaluator_failure。
预算/Worker 异常单独留原因。没有这些阻断时，确定的轨迹/终态失败可以判 Agent fail；
未校准抽取的 pass/fail 仍不能独立决定整题通过或失败。release_eligible 始终 false。
当前为有限归因规则，不代表设计中的完整根因分析已完成。

### 首次真实运行记录

2026-09-13，经用户确认使用 `api.deepseek.com` / `deepseek-v4-flash`，首次单题运行完成：

- 本地目录：`output/agent-eval/runs/real-summary-001`，原始记录保持不变。
- 5 次真实调用：输入安全 1 次、Agent 决策 2 次、回答合规 1 次、声明抽取 1 次。
- 23,841 Token（输入 22,317，输出 1,524），Worker 内约 8.69 秒，未触及预算。
- Agent 返回 complete，答出 2026-09-11 涨停40家；工具选择、禁止远端跌停参数、终态和
  空 missing 的已实现检查通过，所要求的数量/日期关系在自动抽取假设下与证据相符。
- 自动清单列出11项、结构化数值声明8项；不能把8/11当作独立校准覆盖率。附加的连板
  高度、比率、行业等尚未全面核验，自动抽取也未经校准，统一结果为 needs_review。
- 这次暴露的是覆盖范围和校准缺口，不应通过删去额外声明、放宽断言或改用脚本答案来
  伪装通过。报告本身不是市场结论、模型稳定性结果或发布基线。

下一步应围绕本次真实答案建立独立标注、扩充事实适配器和反误杀校准，再扩大 World/Case。
批量 Runner、Live、Stability、Judge 和完整发布判定仍待建设。

## 批量出题蓝图

已新增 `blueprints/core40_live48.json`：40道Offline问题、30道Historical Live、12道
Current Invariant、6道External Canary，均包含交付项、校验意图及数据/录制需求。
蓝图不是可执行Case，也没有增加active题目数；既有市场汇总候选对应OFF-001，不重复计数。
`blueprint-coverage` 根据生产目录生成逐工具/逐Profile矩阵，明确列出extended的24项
正常路径计划缺口和未显式规划参数。详见 `blueprints/README.md` 的录制顺序与物化要求。
