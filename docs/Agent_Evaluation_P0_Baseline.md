# P0 当前基线（2026-09-20）

## 结论

`react-runtime-v20` 已建立一组可追溯的10题代表样本，但 P0 仍是“基线已建立、问题已暴露”，
不是发布通过：统一原始运行6题pass、1题fail、3题unscorable，判分覆盖率70%，
`release_eligible=false`。不得用后续补评覆盖原始结果，也不得把排除unscorable后的85.7%诊断通过率
解释为整体正确率。

当前修复后代码合同为 `react-runtime-v21`、`agent-tools-v2`、`react-evidence-v4`；Agent Prompt摘要为
`sha256:1f90197f68f7a939f00a09224878a69e911c968f280106c511de4f89e4fc1746`，
收紧维度独立性后的Judge Prompt摘要为
`sha256:eed57e94d46a5ff3227573182b02ddd82ee6cc664d9aa40ec1a0f6665da45635`。

## 原始10题真实运行

产物：`output/agent-eval/p0-v20-smoke-run-001`。该运行使用已授权的
`deepseek-v4-flash`，记录271,283 Token；因 `LH-033` 在模型调用前被数据漂移阻断，
用量完整性为false。

| 结果 | Case | 说明 |
| --- | --- | --- |
| pass | OFF-B001、OFF-B003、OFF-B024、OFF-035、OFF-038、LH-B005 | 统一入口三维诊断通过；仍非发布认证 |
| fail | OFF-B025 | 数据源错误已被回答识别，但Agent终态错误写成`empty`，保留为真实Agent failure |
| unscorable | LH-B003 | 无损编码后52,055字符，超过默认24,000字符Judge预算；原运行0次Judge调用 |
| unscorable | OFF-036 | Judge连续返回不存在的Evidence ID，类型校验拒绝该裁决 |
| unscorable | LH-033 | `HistoricalDataDrift`；缺少与旧Local30录制匹配的SQLite快照，0次模型调用 |

## 补充诊断

- Judge协议错误现在只对结构化协议异常最多重试一次，不重试网络、事实或Agent失败；提示明确禁止把
  Assertion ID或占位词当Evidence ID。`OFF-036`保存trace在新提示下一次完成，三维pass，1,617 Token，
  产物为 `output/agent-eval/p0-v20-off036-trace-review-003.json`。
- `LH-B003`在显式80,000字符上限下一次完成，三维pass，27,858 Token，产物为
  `output/agent-eval/p0-v20-lhb003-trace-review-80000-001.json`。这是单题显式扩容诊断，
  不代表默认预算应全局放宽。
- 两次补评合计记录29,475 Token。它们证明原两个unscorable可被诊断，不改写原始统一运行结果；
  Judge仍未完成充分人工校准，顶层保持`needs_review`。

## 零模型Live预检

`run-golden --dry-run`现在会通过生产Tool Gateway重放每个Historical Live的版本化观察，
而不只检查数据库文件存在。代表10题预检产物为
`output/agent-eval/p0-v20-live-preflight-001`：8个Offline合同有效、2个Snapshot Live兼容，
`LH-033`明确为`unscorable/data_failure`；模型调用0，CLI退出码2。

完整64题预检产物为 `output/agent-eval/golden64-v20-live-preflight-001`：54题可继续，
10题为`unscorable/data_failure`，正好是旧Local30的LH-001至LH-004及LH-031至LH-036；
45个Offline和9个新Snapshot Live均通过预检。模型调用0，CLI退出码2。

同一范围在v21的最终预检产物为 `output/agent-eval/golden64-v21-live-preflight-001`，结果仍为
54题可继续、10题旧Local30 Live数据阻断，0模型调用、CLI退出码2。对工作区104个SQLite/DB候选
逐个执行只读baseline回放：98个不具备所需业务结构，6个可读取但发生`HistoricalDataDrift`，0个匹配。
不能从World中的录制输出反向伪造数据库并冒充历史Live快照；恢复该项需要仓库外原始备份。

此前 `output/agent-eval/golden64-v15-contract-preflight-001` 的64题结果只证明资产摘要和代码合同兼容，
目录名是不可变历史名称，也没有检查Live数据内容。不能再把它表述为完整可运行预检通过。

## v21修复与复验

- `OFF-B025`根因是发布gate未检查`finish.status=empty`与引用证据`result_state=error`的冲突。
  v21要求empty至少引用一条真实empty证据，且不得引用error或partial；不解析问题或答案关键词。
  修复记录为 `BC-075`，原v20失败保留。
- v21真实复验 `output/agent-eval/p0-v21-offb025-recheck-001` 为1题pass、覆盖率100%，
  记录24,498 Token且完整，仍为诊断结果、`release_eligible=false`。
- 最终Judge提示复评该保存trace时Provider返回`RuntimeError`，产物为
  `output/agent-eval/p0-v21-offb025-trace-review-final-001.json`；确定性终态检查已pass，
  语义复评保持needs_review，不能用此前Judge结果替代最终提示结果。

## Judge校准结论

新增6个作者标注的P0边界样本，覆盖服务失败、真实空结果、隐瞒失败、编造计数、遗漏结果和无证据拒答。
离线协议与脚本模型测试通过，但这不是独立人工验收。两次真实校准产物分别为
`trace-p0-boundaries-v21-001`和`trace-p0-boundaries-v21-002`：两次均只有3/6完成，其余为Provider
`RuntimeError`；第二次完成项中2题完全匹配，遗漏结果题仍出现grounding abstain及boundary false fail。
因此Judge明确保持`calibrated=false`，不启动Golden64全量或稳定性面板。

代码回归最终为991 passed、10 skipped、3个第三方弃用warning、2个subtests passed。首次完整回归暴露的
Basic70可空`limit` schema兼容问题和旧终态测试ID冲突均已修复；相关108项定向回归随后通过，
最终完整回归无失败。

## P0剩余阻塞

1. 从仓库外找回旧Local30原始SQLite快照，或经人工审批将10题退役并以新快照录制的新版本Case替代。
2. 由独立人工审核者确认P0边界标签，并在Provider稳定后取得完整校准轮；不能靠自动重试筛选成功样本。
3. 上述两项完成后才能运行Golden64全量和同合同三轮Stability Panel；在此之前保持发布资格为false。
