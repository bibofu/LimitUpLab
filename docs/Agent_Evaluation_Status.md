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

## 新设计

新评测体系的长期设计已经确定，详见
[`Agent_Evaluation_Design.md`](./Agent_Evaluation_Design.md)。该文档定义了分层模型、
Offline/Live 固定容量、Frozen World、Case Schema、覆盖矩阵、确定性 Evaluator、
LLM-as-a-Judge 边界、稳定性、版本治理和发布门禁。

当前已开始 M1：`backend/app/agent_eval` 提供 Case、World、结果、预算和 Manifest
类型，以及 JSON 资产加载和跨文件引用校验；对应测试纳入现有 pytest 发现范围。
Frozen Registry 已接入现有 ToolGateway，覆盖有效参数精确匹配、Profile 权限、日期锚定、
标准化结果状态与来源错误回放。Record-Replay 已保存真实 ToolResult、标准化完整结果和
模型可见证据，包含完整性校验及观测结构指纹；新增只读本地 `market_summary` 录制命令。
2026-09-11 单日数据实录（58 条事件、40 条封板）通过七项回放一致性检查，产物仅在
被忽略的 output 目录、未晋升为正式 World；该录制步骤未调用真实 LLM。
此录制不是不可变历史时点数据，也不具有完整交易日历覆盖。
现已由录制导出一个本地候选 World/Case，附来源与资产摘要、待审核清单，未自动晋升。
轨迹/终态 Evaluator 已能检查工具选择、禁止尝试、关键参数和错误 complete；基于真实
runtime trace 的脚本模型测试验证此链路。未实现的 Fact 等检查显式 needs_review，
部分检查报告的 release_eligible 始终 false，不能当成正式 EvalResult 或质量基线。
现已补充独立的审核声明清单/抽取契约、数量单位归一化及窄范围 Summary Fact Verifier。
核验 World、本轮完整 Evidence 和实际可见 metadata/rows，报告声明清单覆盖率，
零抽取、不确定和输入漂移明确 needs_review；附合成标注的反误杀契约校准测试。
现已接入单题隔离 Runner、真实 LLM 声明抽取和统一 EvalResult。2026-09-13 首次通过
DeepSeek 运行完整链路（真实输入安全/Agent/回答合规/抽取），5次调用、23,841 Token，
Worker约8.69秒。Agent返回complete，主问题的日期和40家数量与证据相符。
由于模型主动补充的高度、比率、行业等尚未全面覆盖，且抽取未校准，总结果needs_review。
原始报告位于本地忽略目录 `output/agent-eval/runs/real-summary-001`，未重跑或覆盖。
自动抽取不冒充人工审核，独立覆盖率未知，不能仅凭这次结果建立质量基线。
正式评测题库、批量 Runner、经过校准的抽取器及完整 Answer Fact Evaluator 仍未完成，
M1尚未达标。下一步应从真实回答出发建立独立标注和校准，再扩充资产。
具体运行命令、边界及后续工作见 `backend/evals/README.md`。

已完成40道Offline与48道Live（30历史/12当前/6外部）的具体问题蓝图及规划覆盖矩阵。
88条蓝图保存在 `backend/evals/blueprints/core40_live48.json`，不是88个可执行Case；
正式active题目仍为0，既有市场汇总候选属于OFF-001。数据、bindings、终态及断言物化
尚待逐题完成。v1的24个工具已有正常主路径计划；extended除两个专属工具外的24个
正常路径仍是明确缺口，不能借用v1覆盖代替。下一步从本地事件小批录制与真实回答校准开始。
