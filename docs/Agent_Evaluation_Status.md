# Agent 评测状态

## 当前状态

### 框架优先推进（2026-09-16）

- 后续按小任务补齐本地工具接入、通用评测器、LLM Judge及报告，问题逐步增加；Local30三轮全绿不再阻塞框架建设。
- 已完成源码工具盘点：26个注册工具，16个本地优先评测入口，其中3个已有Local30覆盖、13个待接入；另10个为远端或混合依赖。
- 已实现 `preflight-tool-coverage` 无LLM预检：26个工具全部登记，检查漏登、未知工具、重复及非法状态；登记合法不代表实际覆盖完成。未验证本地样本充足性，未新增模型调用。
- 已接入首个新增工具 `daily_board_promotion` 的隔离本地Runner：只读事务快照、生产Gateway执行、原生列表输出及数据摘要；合成数据库定向测试验证无外网调用、源数据库不变及缺失日K显式保留。尚未完成真实样本验收、通用判分或Judge接入，因此登记仍为planned。
- 完整工具矩阵、Judge缺口及命令用法见[工具覆盖清单](Agent_Evaluation_Tool_Coverage.md)。下一步先补该工具列表输出的录制/回放适配，不批量扩展工具。
- 最新Active Golden为`output/agent-eval/golden/local30-current-v6/suite.json`，运行时为`react-runtime-v14`；稳定面板代码已实现，但最新资产三轮正式稳定验收未完成。以下P0及建设流水为对应版本的历史结果。

### Local30 P0 缺口已关闭（2026-09-15）

- 基于统一Active Golden清单`output/agent-eval/golden/local30-current-v2/suite.json`，使用
  `react-runtime-v13`和DeepSeek `deepseek-v4-flash`完成新的30题独立真实运行：
  `output/agent-eval/runs/local30-p0-formal-003`。旧运行与失败报告均保留，未覆盖。
- 核心合同30/30、终态30/30、事实核心28/28、语义回答2/2、基础设施30/30、完整答案30/30；
  完整答案失败0、开放复核0。OFF-035正确返回`clarify`，OFF-036正确返回`refuse`。
- Agent共179次模型调用、1,126,148 Token；完整答案/语义裁判共30次调用、172,660 Token；
  隔离评测延迟P50为9.10秒、P95为13.58秒。裁判v4校准集10/10通过，明确区分并列的不同口径指标与
  错误比例绑定；裁判只能补充完整答案事实检查，不能覆盖确定性核心失败。
- LH-034曾暴露把源字段`计算机设`自行补成`计算机设备`的问题。运行时现只回答用户要求字段和必要口径，
  原样使用证据字段，疑似脏数据显式说明；最终复跑只交付所需代码、名称及必要筛选口径。
- 最终正式产物：`output/agent-eval/formal/local30-p0-final-001/report.json`；可读摘要为同目录
  `README.md`；`manifest.json`绑定Golden、运行批次、全部验收件、裁判提示及report/README/diff摘要；
  `diff.json`与`DIFF.md`对比上一正式基线`local30-current-003`。
- 相比上一基线，核心合同和终态均由93.33%升至100%；完整答案由2题通过、28题待复核变为30/30通过，
  OFF-035/036由核心及终态失败变为通过。该结论只适用于当前Local30的20 Offline + 10 Historical Live，
  不代表长期240 Offline + 48 Live门禁已完成，因此`release_eligible`仍为false。

### 上一Agent正式Golden基线（2026-09-14，历史）

- 使用统一Active Golden清单`output/agent-eval/golden/local30-current-v2/suite.json`重新执行全部30题，
  每题使用独立session与message_id；20道Offline走Frozen World，10道Historical Live走真实本地生产工具并校验基线，
  模型为DeepSeek `deepseek-v4-flash`。运行保存在`output/agent-eval/runs/local30-formal-001`。
- 30题全部成功执行且可评分，无provider、data、fixture或budget失败。Agent共173次模型调用、1,043,643 Token；
  同一隔离评测环境内延迟P50为7.25秒、P95为12.95秒，不能直接与生产延迟比较。
- 校准后的限定核心合同为28/30通过（93.33%）；28道事实题的核心事实为28/28通过；
  两道语义题经校准LLM Judge判定正文均通过，Judge共2次调用、1,628 Token。
- OFF-035应为`clarify`、OFF-036应为`refuse`，但runtime都返回`complete`，因此终态准确率28/30并将两题归因为
  `agent_failure`。回答正文分别确实追问缺失标的、明确拒绝交易指令；正文正确不能抵消结构化终态错误。
- 基础设施成功率30/30。28道事实题的额外声明仍超出已校准核心合同，完整回答均保持`needs_review`；
  因此当前结果不是“完整答案28/30通过”，`release_eligible`仍为false。
- 正式评分报告：`output/agent-eval/formal/local30-current-003/report.json`；可读表格：
  `output/agent-eval/formal/local30-current-003/README.md`。报告重新校验Active Case、候选验收摘要、World/Baseline、
  技术验收和抽取/Judge提示摘要，避免把不属于当前Golden的旧运行混入结果。
- 正式评分器定向测试8项通过；统一后端验收791 passed、3 warnings、2 subtests passed。
  验收报告：`output/validation/20260914T105624Z-9808662d/summary.json`。

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
- 本批已有30题通过用户业务审核：20道Offline、10道Historical Live（含单独批准的OFF-033替代题）。
  第一批OFF-001、010、027、028、031、032；第二批OFF-038、035、036及OFF-041至050；
  第三批LH-001、002、003、004及LH-031至036。用户逐题认可口径、标准事实与判分规则，
  绑定当前suite v3 / case v5及各自Case/World摘要；独立记录为数据包内的
  `user-review-20260914-batch01.json`、`user-review-20260914-batch02.json`、
  `user-review-20260914-batch03.json`。OFF-033替代题使用独立审批与等价迁移记录。
- 旧OFF-033 v5人工虚构主题题被否决，历史诊断pass不代表业务审核通过，原始资产与驳回记录保留。
  替代题为“2026-09-11科创板有哪些收盘涨停股票？请列出代码和名称，并注明数据日期。”
  原始行与真实工具均确认正常空结果；用户已明确认可替代题、标准事实和规则。
  配方：`backend/evals/suites/off033-replacement.json`；最初审批资产为`off033-replacement-v1`，
  case v6、51条录制，独立审批`user-review-20260914.json`绑定实际Case/World摘要，未继承旧题审批。
  空名单必须有当前可见、日期/市场/收盘口径一致的正常空结果支持，不能因成员集合为空而自动接地通过。
  复杂空结果路线尚无适配器时保持needs_review，不默认通过。
- 替代题前两次独立真实运行保存在`output/agent-eval/runs/off033-replacement-001`及`-002`。
  首次出现两次NativeFunctionCallingError，终态partial，归因provider_failure；第二次终态empty、
  核心事实及过程诊断pass，但尝试额外的全市场amount降序查询时缺录制，归因fixture_failure。
  两次均unscorable，合计15次模型调用、143,685 Token；保留失败，不挑选局部pass冒充整体通过。
  随后逐次把模型实际采用的合法交叉核验路线通过真实工具录制，失败报告均保留、World均新建版本而不覆写。
- 当前资产`off033-replacement-v4`为suite v4 / case v9 / 55条录制。机器等价迁移只在问题、
  requirements、assertions、终态、profile、mode、severity及capabilities完全不变时继承既有业务审批；
  产物明确标注不是新的人审。第五次运行无fixture/provider错误，终态empty，轨迹及核心事实诊断pass；
  总体仍needs_review，因为真实回答额外声明及完整语义质量不在核心合同审批范围。
- 空结果抽取器首轮校准0/6，准确暴露出“科创板数量”误填为全市场数量的字段歧义；未把失败改写为通过。
  明确全市场总数与带板块/主题/条件筛选数量的schema及提示后，第二轮6/6通过，四条真实录制路线均通过。
  OFF-033 v9现已作为`output/agent-eval/golden/OFF-033-v9`的Active Golden，范围仅为genuine-empty核心要求；
  release_eligible与answer_quality_approved仍为false。
- 当前审核进度：30题已确认，业务审核无待办；旧题的否决不撤销，替代题独立计入。
  原suite.json保留生成时快照，以后续独立审核记录及本节为准。批准题仍需技术/抽取校准验收，
  除已单独完成技术验收者外不自动晋升Active；OFF-035/036既有终态失败不因题目获认可而转为通过。
- 不是30道已激活Golden，也不是28/30完整质量通过；当前认可的Offline题已补齐20道。
  当前只有OFF-010 v1、LH-004 v1、OFF-033 v9三个限定核心合同的Active Golden；其余27题仍待技术验收。
  自动抽取、额外声明、语义遵循及校准仍有needs_review。
- 集中审核问题、事实和规则：`output/agent-eval/datasets/local30-v4/REVIEW.md`。
  替代题见其独立包中的REVIEW.md。后续继续对其余27题做技术/抽取校准验收；不得复制审批给业务合同有变化的题目。
- 上轮定向测试42项通过；统一后端验收763 passed、3 warnings、2 subtests passed。
  验收报告：`output/validation/20260914T075129Z-f7e95643/summary.json`。
- 替代题及空结果接地改动完成后，统一后端验收775 passed、3 warnings、2 subtests passed。
  报告：`output/validation/20260914T082306Z-6f69034c/summary.json`。这些契约测试不代替真实LLM质量验收。
- 审批等价迁移、空结果抽取校准与限定晋升完成后，统一后端验收778 passed、3 warnings、
  2 subtests passed；报告：`output/validation/20260914T094445Z-66c6d740/summary.json`。
- 新增无LLM批量预检`preflight-batch`，同时核对用户审批摘要、Case/World版本、独立Oracle及通用适配器范围。
  对`local30-v4`结果为：21道selection、4道count、2道highest、2道semantic_terminal通过预检；
  旧OFF-033因没有获批而单独blocked，符合预期。报告位于`output/agent-eval/preflight/local30-v1/preflight.json`，
  model_calls=0。该预检只说明可进入后续路线执行/抽取校准，不代表29题技术验收或回答质量已经通过。
- 批量预检落地后统一后端验收781 passed、3 warnings、2 subtests passed；
  报告：`output/validation/20260914T095025Z-c8d2662e/summary.json`。
- 结构化批量验收先打通4道count：OFF-001、OFF-031、OFF-032、LH-031。Offline使用冻结录制路线，
  Historical Live通过真实本地生产工具并先校验基线漂移；两组唯一抽取合同各含plain、空格/单位、约数、
  错误数量、错误日期和多声明6种样例，共12次DeepSeek调用、14,018 Token，全部通过。
  晋升器会重新校验审批、Case/World、提示摘要、校准标签与路线，篡改任一项即拒绝。
  4题现位于`output/agent-eval/golden/local30-count-v1`，Active范围仅为数据日期与收盘涨停家数；
  answer_quality_approved及release_eligible仍为false。在该阶段限定核心合同Active Golden资产共7道，
  其中OFF-010/LH-004为旧版本，不能替代当前local30 v5的验收；当时当前30题已有5道完成技术验收，
  另25道为后续批次目标：21道selection、2道highest、2道semantic_terminal。
- count批量验收落地后统一后端验收783 passed、3 warnings、2 subtests passed；
  报告：`output/validation/20260914T095815Z-12ee61d0/summary.json`。
- 后续批量一次完成21道selection：8组Offline/Live重复事实按摘要复用，最终13组唯一名单合同各校准6种表达，
  共78次DeepSeek调用、128,478 Token。每组均覆盖plain、table、reverse、omission、duplicate、wrong_date；
  每道题的直接生产工具路线仍独立执行，不能用共享校准替代路线核验。21题全部通过并按成员身份、日期、
  完整性和声明顺序的核心合同晋升，产物为`output/agent-eval/golden/local30-selection-v1`。
- 当前版本OFF-010、LH-004两道highest各验证三条路线及6种抽取样例，共12次调用、15,082 Token，
  全部通过并晋升至`output/agent-eval/golden/local30-highest-v1`，旧版本Active资产仍作为历史记录保留。
- OFF-035澄清、OFF-036拒答使用独立LLM Judge；两套rubric各含2个正例、4个对抗反例，
  12次调用、7,599 Token全部通过。Judge只判语义，不覆盖确定性工具禁用和终态检查；两题虽已作为
  可执行Golden合同激活，当前Agent实跑返回complete的既有失败仍然有效，不因Case激活而改写。
- 当前30道（20 Offline + 10 Historical Live）业务合同与评测器均已技术验收并激活。
  统一自包含清单为`output/agent-eval/golden/local30-current-v2/suite.json`：30个Case、3个按摘要去重的
  World/Baseline，约12.2 MB。最初未去重的`local30-current-v1`约164 MB，仅保留为历史生成物，不作为当前入口。
  `status=active`表示题目合同和评测器可用，不表示当前Agent 30/30通过；全量模型质量、稳定性、额外声明
  与release gate仍需另行运行，因此answer_quality_approved和release_eligible保持false。
- 30题批量技术验收与统一清单落地后，统一后端验收789 passed、3 warnings、2 subtests passed；
  报告：`output/validation/20260914T102011Z-96cb7917/summary.json`。

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

当前仓库已有可回放的Local30正式Golden基线，但其范围仅为20 Offline + 10 Historical Live，
不能据此宣称完整Agent能力、跨运行模型稳定性或发布门禁通过。普通pytest、前端测试和构建仍只是代码回归检查，
不得称为Agent行为评测；长期240 Offline + 48 Live、Current Live、External Canary和三次稳定性面板仍待建设。

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
