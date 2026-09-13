# Agent 评测资产

当前完成 M1 的类型、加载层和 Frozen Registry，正式 Case、真实录制、Runner 和
Evaluator 尚未实现。

可执行模型位于 `app/agent_eval/models.py`，加载与引用校验位于 `loader.py`。
资产采用 UTF-8 JSON，可通过每个 Pydantic 模型的 `model_json_schema()` 导出规范。
设计文档中的 YAML 是可读示意；JSON 是当前实际支持的存储格式。

- `CaseSpec`：Profile、用户交付项、断言意图、终态及版本化 World 引用。
- `WorldSpec`：带时区的参考时间、独立的最新本地日期、明确覆盖范围的交易日历和录制。
- `RunManifest`：版本、模型、资产摘要及显式正值预算。
- `EvalResult`：失败与弃权不能被汇总成 Pass，未知 Token 保留为 null。

所有测试中的市场数据均为标明来源的合成协议样本，不是正式 Golden 或市场事实。
主 World 计划参考 2026-09-11 18:00 Asia/Shanghai，需核验真实数据后才能录制；
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

下一步是 Record-Replay 录制层。现阶段 `AssertionSpec` 只保存受控的断言意图，尚不执行
断言；完整 Live 分支、Fault Injection、Recorded ToolResult/Evidence View 以及 Evaluator
专用类型在对应实现步骤补齐，不能据此宣称已经有可运行的 Agent 评测。
