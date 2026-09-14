# 本地 Golden 资产入口

2026-09-14首批完成：1道Offline、1道受限Historical Live。两题仅覆盖最高连板高度、
完整并列成员及本轮证据支持，不代表整套Agent评测或全回答裁判已完成。

| Case | 模式 | 本地资产目录 |
| --- | --- | --- |
| OFF-010 v1 | Offline | output/agent-eval/golden/OFF-010-v1 |
| LH-004 v1 | Historical Live | output/agent-eval/golden/LH-004-v1 |

每份含case.json（status=active）、world.json或baseline.json、business-approval.json、
technical-acceptance.json、manifest.json。真实数据不通过Git分发，因此新机器只有此入口
而没有可直接运行的数据包；迁移需另行复制经过审核的本地资产并核对摘要。

业务审批绑定候选Case与Baseline摘要；技术验收绑定审批、抽取提示版本及校准结果。
晋升时重新核对这些绑定，禁止旧批准用于修改后的题目，任何缺失路线或失败校准阻止晋升。
active只改变资产生命周期，不重写原来的候选/回答成绩，也不伪造人工抽取审核。

验收路线：直接highest_only查询（limit=30/100），完整封板池（limit=100）后取最高。
Offline走真实ToolGateway冻结契约；Historical Live以只读数据执行生产方法，不进行冻结匹配。
尚未录制的其他合法Offline路线仍归fixture_failure/unscorable，不当作Agent失败。

抽取校准每题6条：原文、换序、表格、错高度、缺成员、重复成员。标签由已批准事实
确定性构造，模型只看到答案文本与行号，不看到标准标签。错误文本必须按原样抽取，
不能“修正”为正确答案。LH-004仅一名成员，换序不增加独立表达覆盖。此小样本校准
不证明任意语言表达可靠，模型/提示变化须重新验收。

执行（backend目录）：

```powershell
.venv/Scripts/python.exe -m app.agent_eval run-offline --case ../output/agent-eval/golden/OFF-010-v1/case.json --world ../output/agent-eval/golden/OFF-010-v1/world.json --output-dir ../output/agent-eval/runs/golden-OFF-010-NEW --allow-llm
.venv/Scripts/python.exe -m app.agent_eval run-live-historical --case ../output/agent-eval/golden/LH-004-v1/case.json --baseline ../output/agent-eval/golden/LH-004-v1/baseline.json --database data/limituplab.sqlite --output-dir ../output/agent-eval/runs/golden-LH-004-NEW --allow-llm
```

新运行仍输出实际Agent终态、过程诊断和事实诊断。自动抽取没有独立审核的自由表述、
金额/行业等额外声明继续needs_review；不可为了让Golden运行“全绿”隐藏这些项。
两题本身的active与整个系统发布门禁是不同概念，manifest的release_eligible=false。
