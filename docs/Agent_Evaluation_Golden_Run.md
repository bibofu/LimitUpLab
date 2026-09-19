# Golden统一运行与报告

新增共享入口`python -m app.agent_eval run-golden`，支持Active套件的全量/选题执行。
复用现有子进程worker：旧合同保留原事实提取，新合同保留工具合同；显式开启Judge时两者都生成同一三维trace-review。
不修改Candidate验收入口，不覆盖Local30既有正式报告，不将未校准Judge包装成发布认证。

## 零模型预检

在backend目录执行，输出目录必须不存在：

```powershell
.venv/Scripts/python.exe -m app.agent_eval run-golden --suite ../output/agent-eval/golden/basic64-current-v1/suite.json --live-database ../output/agent-eval/basic70-live-readiness-003/source-snapshot.sqlite --output-dir ../output/agent-eval/golden64-preflight-next --dry-run
```

预检校验Active身份、Case/World摘要、ID选择、模式及数据库文件存在，不调用LLM。
实际运行前worker仍会验证Live基线漂移；预检成功不保证指定数据库与每题历史基线一致。
新增Live优先使用suite内固定数据库路径，`--live-database`仅补充原Local30缺少的数据库引用。

## 显式运行

在上述命令中去掉`--dry-run`，增加`--allow-llm --allow-judge`即可完整运行。
可添加`--case-ids OFF-B001 LH-B001`先跑代表题。`--workers`默认2且最多2；`--wall-seconds`默认每题90秒。
不加`--allow-judge`不产生额外Judge调用，未判分题不会伪装为pass。真正运行会消耗模型额度，本次实现验证没有执行该命令。

## 输出与判分

- `plan.json`：套件摘要、每题资产摘要、数据库路由及运行配置。
- 每题目录：沿用worker的完整证据、答案、用量及Judge记录。
- `<case>-result.json`：单题结果及时落盘；某题异常不阻止其他题执行。
- `report.json`和`REPORT.md`：统一结果、原因、覆盖率和记录Token。缺失Token明确标记不完整。

| 结果 | 含义 |
|---|---|
| pass | 已完成Judge的三个维度均pass，且无优先级更高的执行/合同错误；仅诊断通过 |
| fail | 确定性合同失败或已完成Judge指出回答失败；保留具体来源 |
| unscorable | 夹具/模型服务/执行异常、裁判协议错误或输入超限 |
| needs_review | 未启用Judge、缺失裁判结果或裁决不明确 |
| not_run | 仅预检，未执行Agent |

通过率分母仅为pass+fail，同时强制显示有效判分覆盖率，避免排除大量题后呈现虚高通过率。
`release_eligible`保持false；现有Judge漏判问题不因统一运行入口而解决。
CLI退出码：预检成功/全部诊断通过为0；有fail为1；无fail但有无法评分/待复核为2。

## 默认Judge预算策略（2026-09-19）

worker、统一Golden运行和保存trace复评默认尝试既有的可逆无损证据编码；只有包括解码说明后仍更短才采用。
不删行、不删字段、不自动提高预算、不自动重试。默认上限仍为24,000字符（不是Token或模型上下文上限）。
报告同时记录original_input_chars、input_chars、chars_saved、budget_decision、excess_chars和还原校验。
超预算不调用Judge；未开启Judge时仍提供预算预检，status保持disabled。

无需重跑Agent即可预检保存结果：

```powershell
.venv/Scripts/python.exe -m app.agent_eval review-trace --run-dir <已有运行目录> --output <新的报告.json>
```

显式添加`--allow-judge`才调用裁判；`--max-input-chars`可显式指定复评上限，`--no-compact-evidence`可禁用编码做对照。
旧报告不覆盖。无损编码不是语义摘要；无收益或保留键冲突时保持原证据。
运行时版本不兼容的历史trace仍拒绝评分，但可读取的证据会生成预算估算；不能把旧结果冒充当前版本验收。

历史长证据零模型预检：LH-B003从146,227降至76,593字符，LH-B006从96,981降至79,037字符，均通过无损还原校验。
两题仍超默认24,000预算，且历史运行时版本已不兼容，status=invalid_trace、budget_decision=exceeded、calls=0。
记录位于`output/agent-eval/default-budget-20260919-LH-B003-v2.json`及对应LH-B006文件，不代表重新补评通过。

## 验证范围

真实Golden64资产预检：`output/agent-eval/golden64-unified-preflight-001`，64题均not_run，0模型调用。
脚本executor验证全部64题调度及19个Live路由，脚本成功不能当Agent正确率。
额外回归执行共享worker、旧合同提取+Judge、10个本地Live工具隔离场景，以及错误优先级和报告持久化。
