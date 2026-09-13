# Agent 评测资产

当前完成 M1 的类型、加载层、Frozen Registry、单工具 Record-Replay、候选资产导出，
以及轨迹/终态的部分确定性检查。正式题库、真实模型 Runner、Fact Evaluator 尚未实现。

可执行模型位于 `app/agent_eval/models.py`，加载与引用校验位于 `loader.py`。
资产采用 UTF-8 JSON，可通过每个 Pydantic 模型的 `model_json_schema()` 导出规范。
设计文档中的 YAML 是可读示意；JSON 是当前实际支持的存储格式。

- `CaseSpec`：Profile、用户交付项、断言意图、终态及版本化 World 引用。
- `WorldSpec`：带时区的参考时间、独立的最新本地日期、明确覆盖范围的交易日历和录制。
- `RunManifest`：版本、模型、资产摘要及显式正值预算。
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

下一步是 Answer Fact 的声明抽取/验证接口与反误杀校准样本；人工复核候选后才能晋升。
Runner、完整 Live 分支、Fault Injection 和完整发布判定在对应步骤补齐。
