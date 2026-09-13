# Agent 评测状态

## 当前状态

旧版 Agent 评测集已于 2026-09-13 全部退役并从本地仓库删除。删除范围包括：

- Chat Eval V2 公开 Dev、冻结工具事实和 V1 迁移清单；
- Live Behavioral Eval V1 及其冻结工具世界；
- 题库型评测样本、已生成的本地评测报告和旧基线产物；
- Evaluator、Runner、Gate、Judge 校准、生成器和命令行入口；
- `/api/agents/eval`、系统健康评测字段及仅依赖旧评测的测试；
- 仅服务于旧方案的设计与验证文档。

删除原因是这些资产主要反映旧 Query/Planner/Tool Policy 或过渡期
Plan-and-Execute 设计，无法充分评价当前 bounded ReAct 的 Observation 后决策、
集合计算证据链、Requirement 状态、`finish` 终态和实体/日期/指标关系正确性。

## 使用边界

当前仓库没有可用于宣称 Agent 质量、模型稳定性或发布通过的正式评测集。
普通 pytest、前端测试和构建仍是代码回归检查，但不得称为 Agent 行为评测。
旧评测代码和兼容入口不再保留。后续方案不得直接复用旧数据模型、通过阈值或报告结构。

历史里程碑和 `docs/code-quality-audit.md` 中的旧评测结果只记录当时事实，
不代表当前版本验收状态。

## 下一步

新评测集的目标、分层、数据世界、断言模型、Judge 边界、稳定性和发布门槛
将在后续讨论中重新确定。新方案落地前，不恢复旧数据集或沿用旧通过阈值。
