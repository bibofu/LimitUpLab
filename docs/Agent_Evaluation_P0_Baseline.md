# P0 当前基线（2026-09-20）

## 结论

当前 `react-runtime-v20` 已建立一组可追溯的10题代表样本，但 P0 仍是“基线已建立、问题已暴露”，
不是发布通过：统一原始运行6题pass、1题fail、3题unscorable，判分覆盖率70%，
`release_eligible=false`。不得用后续补评覆盖原始结果，也不得把排除unscorable后的85.7%诊断通过率
解释为整体正确率。

当前代码合同为 `react-runtime-v20`、`agent-tools-v2`、`react-evidence-v4`；Agent Prompt摘要为
`sha256:1f90197f68f7a939f00a09224878a69e911c968f280106c511de4f89e4fc1746`，
收紧Evidence ID协议后的Judge Prompt摘要为
`sha256:48e1c4f592c3fa16fcb7b99160a0bb02a8da4982781345db20e4c0d7c349018d`。

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

此前 `output/agent-eval/golden64-v15-contract-preflight-001` 的64题结果只证明资产摘要和代码合同兼容，
目录名是不可变历史名称，也没有检查Live数据内容。不能再把它表述为完整可运行预检通过。

## P0 后续动作

1. 找回或重建与旧Local30 baseline完全匹配的只读SQLite快照；未找到前，不运行旧10道Live全量模型评测。
2. 单独修复 `OFF-B025` 的错误终态，并按约定在 `badCase.md` 留档后回归；基线文档保留修复前失败。
3. 对Judge新提示补充人工标注校准，明确长证据的分档预算策略，再启动Golden64当前版本全量运行。
4. 全量完成后同一合同连续运行三轮Stability Panel；在此之前保持发布资格为false。
