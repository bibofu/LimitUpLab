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
`release_eligible`保持false；现有Judge漏判问题和默认长证据预算策略并未在这次修改中解决。
CLI退出码：预检成功/全部诊断通过为0；有fail为1；无fail但有无法评分/待复核为2。

## 验证范围

真实Golden64资产预检：`output/agent-eval/golden64-unified-preflight-001`，64题均not_run，0模型调用。
脚本executor验证全部64题调度及19个Live路由，脚本成功不能当Agent正确率。
额外回归执行共享worker、旧合同提取+Judge、10个本地Live工具隔离场景，以及错误优先级和报告持久化。
