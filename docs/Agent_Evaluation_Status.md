# Agent 评测状态

## 当前状态

### 最新交付：20 Offline + 10 Historical Live（2026-09-14）

- 已材料化30道可运行题目，不再只有问题蓝图。题目配方在 `backend/evals/suites/local30.json`。
- 当前数据包：`output/agent-eval/datasets/local30-v4`，suite v3 / case v5，304条真实生产工具录制；
  以只读原始行独立计算标准事实，再与真实工具的完整封板/未封板分区交叉核对。
- 20道Offline覆盖日期、最高与并列、市场/板高/开板条件、回封交集、前10/20排序、完整名单、空结果、澄清、拒答。
  10道Live经用户确认全部为Historical Live，以真实本地工具执行，不回放Frozen结果。
  范围为market_summary、limit_up_events及本地market_event_pool，**不声称完整工具目录、当前Live或外部Canary覆盖**。
- 完成首轮30次真实DeepSeek运行；补录合法路线、改进题目区分度后，另外复跑8题。
  共233次模型调用、1,546,832 Token；日期/数据类型和外发范围均已获用户确认。
- 最终复核入口 `output/agent-eval/reviews/local30-001/README.md`：
  28道业务题核心事实诊断pass；澄清OFF-035、拒答OFF-036都错误返回complete，终态检查fail。
  两题原文分别追问/拒绝，并非已证实发生交易建议泄漏。未修改Agent运行时以隐藏此问题。
- 最终选用的30份运行没有fixture_failure等不可评分项；首次6题的录制缺口仍在原报告中，未覆写。
  名称“远 望 谷”与“远望谷”在代码相同前提下允许空白差异；不采用正则覆盖或模糊股票匹配。
  计数支持market_event_pool元数据；计算后名单仅在独立select复算和先前证据依赖成立后作为可见支持。
- 本批已有29题通过用户业务审核：19道Offline、10道Historical Live。
  第一批OFF-001、010、027、028、031、032；第二批OFF-038、035、036及OFF-041至050；
  第三批LH-001、002、003、004及LH-031至036。用户逐题认可口径、标准事实与判分规则，
  绑定当前suite v3 / case v5及各自Case/World摘要；独立记录为数据包内的
  `user-review-20260914-batch01.json`、`user-review-20260914-batch02.json`、
  `user-review-20260914-batch03.json`。第三批未扩展批准OFF-033或任何替代题。
- OFF-033的业务口径被用户明确否决，原话为“这个问题问的就莫名其妙”。当前人工虚构主题的
  空结果问题不得晋升或计入业务已认可的Golden容量；需重新设计自然的空结果问题，核验后重新审核。
  未批准替代题，未覆盖原始Case/World/运行结果。该题历史诊断pass不代表业务审核通过。
- 当前审核进度：29题已确认、1题否决待重新设计；现有题目没有尚未给出业务意见的条目。
  原suite.json保留生成时快照，以后续独立审核记录及本节为准。批准题仍需技术/抽取校准验收，
  不自动晋升Active；OFF-035/036既有终态失败不因题目获认可而转为通过。
- 不是30道已激活Golden，也不是28/30完整质量通过；当前认可的Offline题为19道，需补回1道。
  自动抽取、额外声明、语义遵循及校准仍有needs_review。旧OFF-010/LH-004 v1 Active及审批保持不变。
- 集中审核问题、事实和规则：`output/agent-eval/datasets/local30-v4/REVIEW.md`。
  后续需完成业务确认和相应技术/抽取校准验收，再晋升；不得复制旧审批给修改后的题目。
- 本轮定向测试42项通过；最终统一后端验收763 passed、3 warnings、2 subtests passed。
  验收报告：`output/validation/20260914T075129Z-f7e95643/summary.json`。

以下为首批建设流水，数量和最新状态以上面为准。

### 首批建设进度（2026-09-14，历史记录）

以下“退役”说明描述旧体系，不表示新体系尚未启动。目前：

- 长期设计：240 Offline、48 Live；已有40 Offline与48 Live具体问题蓝图。
- 实际材料化：8道Offline资产（OFF-001、010、027、028、031、032、033、038），
  1道受限本地Historical Live资产（LH-004），均已有真实运行。
  OFF-010与LH-004已晋升为限定最高连板核心要求的active Golden；其余7道仍为候选。
- 新批次路径 `output/agent-eval/candidates/core-batch3`，新增5道Offline和LH-004；
  OFF-010/028经用户批准改为9月8日，以覆盖真实并列最高和回封/未回封样本。
- 本批6次实跑均返回complete，但OFF-031/033受未录制路线污染，判unscorable/fixture_failure；
  其他4题needs_review。不能将complete、单项诊断或肉眼核对宣称为Golden通过。
- LH-004使用只读源数据库加载的隔离内存快照、真实生产方法；基线漂移时在模型调用前中止。
  当前仅开放market_summary/limit_up_events，不是完整Profile或完整48题Live覆盖。
- 收敛首批Golden：OFF-010与LH-004已生成集中审核包，路径为
  `output/agent-eval/golden-review/OFF-010-v1/REVIEW.md`、`LH-004-v1/REVIEW.md`。
  新Business Fact Verifier支持最高高度/无序完整集合、历史计数、盘中开板成员和有效空值；
  两题已有回答经新抽取后核心业务断言通过，额外声明/人工校准仍待复核。
  原始review.json保持生成时状态，绑定题目、baseline、回答摘要；其后审批与晋升用新文件记录。
- 2026-09-14用户已明确确认OFF-010、LH-004的口径、标准事实（均已核实）和判分规则。
  每份审核包新增不可覆盖原始材料的 `user-review-20260914.json`，绑定实际case/baseline摘要，
  状态为business_contract_confirmed。该确认不扩展为原回答额外声明、抽取清单、校准或发布批准。
  剩余技术事项由实现侧推进：可接受调用路线验证、抽取校准和最终晋升条件检查；
  不再重复要求用户确认已批准的业务事实。
- 两题各3条技术路线验证通过，真实DeepSeek校准各6/6通过（总12调用、14002 Token）。
  校准标签是已批准事实的确定性变体，不冒充人工标注；Live只有1名成员，其换序样例
  与原序相同，不视为新增独立表达覆盖。校准范围不包括所有自由表述或额外业务声明。
- 正式本地资产位于 `output/agent-eval/golden/OFF-010-v1` 和 `LH-004-v1`，包含active Case、
  World/Live Baseline、业务审批、技术验收和晋升摘要。候选及历史运行保持不变。
  active代表题目核心合同已验收，不代表任何一次回答全部正确；release_eligible仍false。
- 三题都已有真实LLM运行。两道名单题先暴露参数录制缺口，再在Case/World v2下
  成功查询、读取证据、计算并完整交付；名单检查通过，额外声明与抽取校准仍待复核。
- 新增过程诊断：读取/计算必须依赖先前Observation；独立重算无过滤的amount排序
  select；校验目标名单在当前证据视图中可见；统计重复签名，不强制唯一调用路线。
- 对两道保存的真实运行进行离线过程复核，均通过上述窄范围检查，各1次读取、1次计算，
  无重复签名。本次未新增模型调用，也未重写原运行结论。
- `process.json`是独立诊断，暂不并入发布评分；当前范围不包含完整Requirement、异常恢复、
  多轮、安全和生命周期检查，也不以trace内部一致性替代World事实核验。

下一步应补新题业务断言的确定性判定、修复已暴露录制路线缺口，并继续扩充独立能力题。
详细候选版本和运行记录见 `backend/evals/blueprints/README.md`。以下建设流水记录保留
历史顺序，数量与能力状态以上述最新进度为准。

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
