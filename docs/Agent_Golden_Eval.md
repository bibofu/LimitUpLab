# Agent Golden Dataset 与四层端到端评测

## 目标

Golden Dataset 用少量高质量真实问题持续约束 Agent 的完整行为链路。它不使用一个“最终答案是否像参考答案”的总分代替诊断，而是对同一次 Agent Run 分别评估：

1. Planner Eval：是否理解问题并识别正确业务能力。
2. Tool Execution Eval：必需工具是否真正执行，参数是否符合金标契约。
3. Grounding Eval：必需事实是否存在于成功工具输出，回答中的日期、数字和股票实体是否有证据。
4. Answer Eval：是否包含必要内容、避开禁止内容，并在有证据时不过度拒答、无证据时不编造。

只有四层都通过，一条 case 才通过。报告同时保留各层通过率和每类问题通过率，不能用 Answer 层的高分掩盖 Planner 或 Tool 层错误。

## 数据集契约

数据集位于 `backend/tests/fixtures/agent_golden_dataset.json`，当前版本为 `agent-golden-v1`。Loader 强制总量为 50～80 条、case id 唯一，并要求每条至少包含：

```json
{
  "question": "...",
  "expected_capabilities": [],
  "required_tools": [],
  "expected_parameters": {},
  "required_facts": [],
  "must_include": [],
  "must_not_include": [],
  "allow_refusal": false
}
```

辅助字段：

- `case_id`：稳定且唯一的金标编号。
- `category`：意图理解、参数抽取、工具选择、聚合、对比、多轮、上下文、缺数据、工具失败、防幻觉或 Safety。
- `conversation_id`：相同值的 case 共享生产格式的历史消息和 recent runs，用于多轮承接。
- `intent_hint`、`trade_date`、`symbol`：模拟页面传入的显式上下文。
- `simulate_tool_failure`：在测试专用工具注册表中让指定工具确定性抛错，用于复现新闻源、K 线源等故障；不改变生产默认执行。

`expected_parameters` 按工具名保存期望参数子集，允许实际 trace 增加版本、默认值等字段。`required_facts` 保存必须在成功工具结构化输出中出现的事实片段，不从最终答案反推。`must_include` 和 `must_not_include` 只负责用户可见答案契约。

## 执行

可重复的离线全量基线：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --suite golden --mode offline --summary-only
```

定向复测一个 case 或一类问题：

```powershell
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --suite golden --mode offline --case-filter G002
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --suite golden --mode offline --case-filter aggregation
```

真实模型端到端评测：

```powershell
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --suite golden --mode live-llm --live-answer --fail-on-failures
```

失败明细写入本地 `backend/data/agent_eval_failures.json`，该文件不提交。真实模型模式会产生外部调用和费用，应在模型、代理和预算明确时运行。

## 失败处理

- Planner 失败：优先改 Capability 描述、Planner prompt 或语义契约，不用答案模板掩盖。
- Tool Execution 失败：检查工具选择、Policy repair、Query Contract 和参数标准化。
- Grounding 失败：检查工具输出完整性、Facts 压缩、来源状态和答案事实校验。
- Answer 失败：检查最终模板或 Answer LLM 的完整性、表达、安全和拒答边界。

新增线上 Bad Case 时，先以用户原话增加或更新 Golden case，再在 `badCase.md` 记录根因和修复，最后只针对失败所在层修改。若业务口径变化，应递增数据集版本并在提交中说明迁移，不直接改旧期望来制造通过。

## 初始基线

`agent-golden-v1` 首次离线运行结果为 19/50 全层通过：Planner 78%、Tool Execution 54%、Grounding 66%、Answer 80%。这些失败是后续治理队列，不表示评测器失败；框架自身由 pytest 验证。初始阶段不把 Golden 全量加入普通 CI 的强制通过门禁，待真实问题逐层修复并形成稳定基线后，再启用 `--fail-on-failures`。
