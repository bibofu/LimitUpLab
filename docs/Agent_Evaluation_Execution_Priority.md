# Agent 评测近期执行优先级

> 2026-09-22 起执行。原则：停止扩建 Eval framework，优先增加真正经过人工复核的题目与真实 Agent 运行证据。

## 统一口径

当前 Golden64 表示题目资产和判分合同曾获准进入套件，不等于64题都完成当前运行时下的人工答案复核。
后续报告必须分别写出：

- `contract-admitted`：题目与合同已准入的资产库存；
- `reviewed Active`：问题、World、预期行为、真实回答和最终裁决均完成逐题复核；
- `Dev`：允许反复运行和定位问题；
- `Private Holdout`：不用于逐题调提示，只在阶段验收时运行。

不新增新的评测框架或平行 Runner 来表达这些口径；先用清单和现有资产状态管理，保留历史产物不覆盖。

## P0-A：扩充 reviewed Active

目标先达到40题，其中30题作为 Active Dev，10题作为 Private Holdout。当前10题代表运行只作为首批审阅材料，
不能按一次 Judge 结果自动晋升。

40题应覆盖以下能力；一题可以覆盖多个能力，但每类至少包含独立主场景和失败/边界场景：

| 能力 | 最低目标 | 重点检查 |
| --- | ---: | --- |
| simple factual | 6 | 对象、日期、指标、数值、来源 |
| dynamic two-step | 5 | 第二步必须依赖第一步 Observation |
| conditional branch | 4 | empty、命中、缺字段走不同合法路径 |
| partial/error recovery | 5 | error不伪装empty，成功部分不丢失 |
| multi-tool comparison | 4 | 口径分离、冲突与共同覆盖范围 |
| Evidence compute | 4 | 完整集合、排序、交并差及lineage |
| ambiguous query | 3 | 必要澄清，不擅自选对象或日期 |
| safety/refusal | 4 | 研究事实与交易指令边界 |
| answer output constraint | 5 | 只列字段、完整名单、必要缺失说明 |

逐题准入至少核对：用户要求、数据世界、合法替代路线、终态、关键事实、缺失披露、输出限制和安全边界。
真实回答失败应进入 bad case 并修复或保留失败，不能通过改宽 Case 合同晋升。

执行批次：

1. 盘点现有候选，选出覆盖缺口最小的首批10题；
2. 每批最多10题，完成真实运行、人工复核和必要修复后再开始下一批；
3. 达到30题后先冻结 Active Dev；再从未用于提示调试的材料中确定10题 Private Holdout；
4. 不为追求40题降低证据、终态或复核标准。

## P0-B：最小 Multi-turn runner

只扩展现有 Golden worker，使一个 Case 可以按顺序执行两轮真实 Agent：

```text
Turn 1 -> 真实 Agent -> 保存 assistant、结构化响应和 session context
Turn 2 -> 同一 session 继续 Agent -> 单独保存第二轮 trace、evidence 和终态
```

必须保证第二轮不会直接复用旧 evidence 作为当前事实；需要事实时重新查询，并能审计刷新行为。首批至少5题：

- previous entity；
- previous result set；
- pronoun；
- date modification；
- old evidence refresh。

验收重点是会话语义和证据新鲜度，不增加新的通用 Planner、Judge 或报告框架。

## P1-A：Dev / Holdout

- Active Dev：30题，可重复运行和定位问题；
- Private Holdout：10题起步，可扩至15题；题面、答案和关键断言不进入日常提示调试材料；
- Holdout失败可以触发归因和通用修复，但不得把该题改成新的提示特例后反复刷分；
- 每次报告同时给Dev与Holdout结果，禁止合并成一个通过率掩盖过拟合。

## P1-B：Stability

从关键能力中选择固定 Case，每题独立运行3次。至少输出：

- `pass@1`；
- `3/3 stable rate`；
- `2/3 rate`；
- `0/3 rate`；
- 每题三次结果、失败原因和Token完整性。

先对 reviewed Active 的关键子集做稳定性，不对未复核资产批量烧模型额度。

## P1-C：Judge校准

准备20～30个真实 Answer，由人工先标注 `task_completion`、`grounding`、`boundary_safety`，再运行Judge。
报告逐维一致率、false pass、false fail、abstain和协议/Provider失败；人工标签与Judge输入都版本化。

Judge只有在样本完整、人工标签已确认且错误边界可接受后，才讨论进入 release gate。在此之前只作诊断，
不得通过重复调用筛选有利裁决，也不以合成作者标签代替人工标签。

## 明确暂停

- 暂停新增评测Schema、通用框架层、独立工具Runner和新的报告体系；
- 暂停Golden64全量真实运行；
- 暂停围绕单个可见题反复调Prompt；
- 暂停把未校准Judge结果用于发布通过；
- 旧Local30快照缺失保留为数据资产问题，不以反向构造数据库阻塞上述Dev题扩充。
