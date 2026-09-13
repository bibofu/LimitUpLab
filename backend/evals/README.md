# Agent 评测资产

当前完成 M1 的类型与加载层，正式 Case、真实录制、Runner 和 Evaluator 尚未实现。

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

下一步实现 Frozen Registry。现阶段 `AssertionSpec` 只保存受控的断言意图，尚不执行
断言；完整 Live 分支、Fault Injection、Recorded ToolResult/Evidence View 以及 Evaluator
专用类型在对应实现步骤补齐，不能据此宣称已经有可运行的 Agent 评测。
