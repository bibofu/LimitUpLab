# Agent 评测资产

当前完成 M1 的类型、加载层、Frozen Registry 和单工具 Record-Replay，正式 Case、Runner 和
Evaluator 尚未实现。

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

下一步建立经审核的最小 World/Case 与确定性 Evaluator。现阶段 `AssertionSpec` 只保存
断言意图，尚不执行断言；Runner、完整 Live 分支、Fault Injection 和 Evaluator 类型在
对应实现步骤补齐，不能据此宣称已经有可运行的 Agent 评测。
