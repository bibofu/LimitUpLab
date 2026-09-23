# Agent 评测状态

阅读入口：先看[评测集导航与后续计划](Agent_Evaluation_Guide.md)，最新完整状态以[当前建设清单](Agent_Evaluation_Real_Golden.md)为准。本页保留历史时间线；各节的待办和统计反映当时状态，不逐段回写。

## 当前状态

### 30题已获人工认可，28题Active并完成同版实跑（2026-09-23）

- bibo批量认可剩余23题、无例外；原审核文件不改，题目审核待办为0。当前建设详情见[真实Golden建设清单](Agent_Evaluation_Real_Golden.md)。
- 正式Active从7增至28；Q09/Q23虽已认可，但历史新闻执行受现有工具能力限制，保留阻断，不凑数入库。
- 固定react-runtime-v24运行28题，首次26 needs_review/1 fail/1夹具unscorable；M03只补真实录制后验证1次，当前资产口径27 needs_review/1 fail。
  原夹具失败保留，不当Agent事实错误；29次尝试不是29道题，也不是三轮稳定性。4个真实两轮均验证上下文逐字继承。
- 175次模型调用、0 Judge，记录1,210,783 tokens；运行器原27个needs_review不是自动通过。用户随后逐题确认本轮答案20通过、8不通过；旧版2份人审标签不叠加计数。
- 145个backend/app文件哈希未变，未改Agent、提示或评测框架；价格口径、评分阶段解释、消歧问题只记录，不修改判分适配答案。
- 当前套件：`output/agent-eval/real-golden-admission-20260923-001/active-dev28-final/suite.json`；同目录`RESULTS.md`为运行汇总。
- 用户明确认可[28份当前版本答案的逐题三维复核](../output/agent-eval/real-golden-admission-20260923-001/ANSWER_REVIEW_RESULT.md)：20通过、8不通过。
  本批答案标签与原答/题目摘要已绑定并保存为`backend/evals/suites/real-dev28-user-reviewed-answer-labels.json`；运行器原27个needs_review保持不变。
- 已对其中10道关键题各取得三次同版真实回答并获用户确认：[稳定性验收](../output/agent-eval/real-golden-admission-20260923-001/STABILITY_RESULT.md)为4题3/3、2题2/3、4题0/3；绑定见`backend/evals/suites/real-dev28-stability-user-reviewed.json`。这不是全部28题的稳定率；Q09/Q23暂缓。
- 23份已有人审标签的单轮答案做Judge对照：[逐份核对](../output/agent-eval/real-dev28-judge-calibration-20260923-001/REPORT.md)中22份完成、1份调用错误；18/22一致，但5份人工失败仅识别1份。Judge未达发布门禁要求，不覆盖人工标签；未改Agent、Judge提示或题目标准。

### 建设已转为批量题目入库，不再边设计边修Agent（2026-09-22）

- 当前统一入口为[真实Golden建设清单](Agent_Evaluation_Real_Golden.md)，旧阶段条目保留历史，不再多处重复更新。
- 30道公开Dev题已形成问题、真实业务依据、参考记录与判分合同；7道按用户已有认可正式Active入库，23道集中待审。
  7是题目合同准入数，既有答案人审通过仍为2；不要求Agent答对后才准入可信题目，也不代填任何新标签。
- 本轮0模型/0Judge，Agent生产代码0修改，145个Python文件哈希不变。30题格式与摘要校验通过，19题已有执行绑定，已认可7题只改资产status。
- 8题有历史实跑、11题有事件基线尚未跑、11题执行绑定待补；不宣称30题均已可执行或通过。
  M01价格基准、Q23实际新闻/涨跌字段缺口、统一dry-run能力参数遗漏及M05时点支持均显式记录，不借机修Agent。
- 一次性审核入口：`output/agent-eval/real-golden-build-20260922-001/bundle/REVIEW_BATCH.md`。
  可批量认可并列例外；不重复审核原7题，不将公开题目充作Private Holdout。

### M02人审通过；M04日期追问失败已定位并修复验证（2026-09-22）

- 用户bibo认可M02题意/判分口径，三维标签均pass、意见“无”；原件未改，绑定存于
  `output/agent-eval/m04-two-turn-20260922-001/m02-user-review-binding.json`。加上Q01，目前2份真实回答/会话获人审通过，
  其中多轮会话1段；不是同一版本全量质量基线，未统一迁移正式Active，其他答案标签不代填。
- 下一条真实业务草案M04（改日期、保留主板首板条件）已实跑：v23首轮交付72只，次轮误作独立请求而澄清，
  未查询09-18的61只。7次模型调用、23,058 Token、0 Judge；数据和worker历史传递正常，记录BC-087。
- v24通用修复复用既有输入审查做语义追问判断，移除关键词列表；仅追问可参考最近一轮实际查询参数，
  不传旧答案/行情结果或复用旧证据，独立换题仍隔离。无新Runner/框架/模型步骤，记录BC-088。
- 同题/同基线/同快照各版本仅1段会话。v24为8次调用、36,558 Token、0 Judge，两轮complete；72/61只全集及
  1,114个表格显示字段对照SQLite一致（时间规范化到秒）。自动仍needs_review，单次验证不代表稳定性。
- 扩展回归696 passed、10 skipped、3项第三方弃用警告。两次原件43/45份哈希不变，源快照不变；新增正式Active 0。
- 新人审入口：`output/agent-eval/m04-two-turn-v24-20260922-001/HUMAN_REVIEW.md`。M02不重审，M04审核两轮原答。
  下一步核准价格数据口径，继续M01实体/M03歧义追问；M05跨日时钟与数据可见范围仍待落实，不冒称完整多轮通过。

### 前一阶段：Q01人审通过，最小两轮及M02首次诊断（2026-09-22）

- 用户bibo已对Q01 v23历史原答填写pass/pass/pass、意见“无”。结论绑定原审核文件与答案/Case/基线哈希，
  位于`output/agent-eval/m02-two-turn-20260922-001/q01-user-review-binding.json`；原技术观察与失败不覆盖。
  这是1份真实答案的人审通过，不是全部Dev6通过或正式Active自动晋升；不再围绕Q01定向修改提示。
- 只扩展既有worker支持两个真实user turn：保存第一轮原答及完整metadata，经生产会话上下文服务接续；
  同一session、不同message_id，第二轮建立新证据库。首轮系统性错误时停止，不拿首轮误评第二轮合同。
- 真实M02候选已单次跑完：先选09-21首板成交额Top5，再问“这几只当天分别开过几次板”。
  两轮均complete，10次模型调用、48,501 Token、0 Judge；首轮名单/排序/成交额及次轮5个开板数独立核对一致。
  第二轮确实重新查询5只，同轮前置全市场/炸板池查询仍是效率观察；自动needs_review，不代填人审。
- 回归676 passed、10 skipped、3项第三方警告。运行原件49份哈希不变，生产数据库未写；未新增框架或评分器。
- 人审入口：`output/agent-eval/m02-two-turn-20260922-001/HUMAN_REVIEW.md`。仅末轮自动评分，整段会话待人工；
  同日刷新不等于跨日刷新，M01/03/04尚未运行，M05逐轮时间锚尚未实现。新增正式Active 0。
- 下一步先收回M02题意与两轮答案标签、其余Dev6原答标签，再依次补日期修改与实体/歧义追问的真实证据；
  不重复跑Q01，不把完整五类多轮、稳定性或Judge校准写成已完成。

### 前一阶段：Q01 v23核心交付恢复，随后已获上方人审结论（2026-09-22）

- 仅修通用回答范围、单表说明与一次修复反馈，未改渲染/安全/截断gate；同题同基线同快照单次实跑。
- 5次调用、36,278 Token、0 Judge；首次finish通过，最终complete/needs_review，核心统计及晋级率真实交付并核对一致。
- 仍有BC-086：模型正文手写名单绕开表格契约，擅自补全两个行业字段；来源概括和额外“稳”等解读也待人审。
  不将complete等同质量通过，不再追加采样选答案；新增Active 0。
- 扩展回归670 passed、10 skipped。人工入口：`output/agent-eval/q01-historical-v23-20260922-001/HUMAN_REVIEW.md`。
  此处为当时技术观察；后续用户裁决见上方，不据此继续定向叠提示或扩评测框架。

### Q01受限历史工具实跑已定位真实失败（2026-09-22）

- 复用既有历史执行器，真实处理四个事件复盘工具的合法参数；62条基线检查通过，不再逐条补Frozen签名。
  原缺口的81只盘中开板和25只未回封均真实核对。只读两日事件/日K快照，源数据不变。
- LH-R01为明确标识的受限live_historical诊断，不与原offline合并成绩；题面/标准事实/要求/终态未改。
- 各项工具成功，5次模型调用、47,367 Token，Judge 0；最终却因双表占位后仍声明截断全集完整而发布失败，
  自动fail/agent_failure。核心数字仅在内部拟答，最终用户未得到复盘；记录BC-084，不能把数据齐备算作回答通过。
- 61项相关回归通过、10项跳过。新人工入口：`output/agent-eval/q01-historical-20260922-001/HUMAN_REVIEW.md`。
  下一步修复原需求与额外交付的边界及单表契约遵守，不放松完整性校验、不再补fixture刷分；新增Active 0。

### Q01/Q08修复后实跑完成（2026-09-22）

- 已获后续本项目范围内模型调用持续授权，沿用既定DeepSeek目的地与数据范围，不再逐次询问。
- 两题各1次、v22/Case与World v3，14次调用、162,339 Token；用量完整，无超时/Provider失败，Judge 0调用。
- Q08真实complete，最终13只名单、排序及六列均核对一致，发布阻断未再出现；仍待独立人审，不自动晋升Active。
- Q01旧缺口消除，但2种新参数未录制，partial/unscorable；整体仍不能作为普通质量失败或通过。
  额外亏钱效应推断、来源概括及未实际展示的附加炸板表另记BC-082待人审；不重跑挑选通过。
- 新人审入口为`output/agent-eval/real-dev6-v3-run-20260922-001/HUMAN_REVIEW.md`；其他4题沿用v2原答。
  下一步收敛Q01真实路线覆盖并完成答案复核，再推进最小两轮；无新增Active。

### Q08运行时修复、Q01真实补录完成（2026-09-22）

- v22修复显式有界排名集合的交付；原Q08首次轨迹确定性回放成功交付13只，保留全市场截断保护及旧失败。
- Q01已观察的4种缺录制签名全部真实补齐；新v3包62条录制回放一致，原7次被拒绝调用均可匹配。
  6题业务合同与用户意见未改，既有业务审批迁移不包含新增答案质量或附加跌停事实认可。
- 核心回归186 passed，扩展回归664 passed、10 skipped（临时目录权限阻断解除后完成）；
  本轮新增模型/Judge调用均为0，Active增加0，不把确定性回放算作真实Agent通过。
- 接下来明确Q01/Q08各1次修复后实跑的预算与外发范围，完成原答案人工标签，再推进最小两轮会话。
  [详细进展与验证边界](Agent_Evaluation_Real_Dev6_Contracts.md)。

### Dev6 v2首轮真实运行完成（2026-09-22）

- 用户明确外发授权后，6题各运行1次；同一v21/deepseek-v4-flash、Case/World v2，33次调用、294,613 Token，逐调用用量完整。
- 4 complete、2 partial；原始自动结果4 needs_review、1 fail、1 unscorable；Judge 0调用，未重跑，未新增Active。
- Q01受4种签名、7次缺录制调用影响，归fixture_failure而非普通Agent失败；BC-077计数修复没有被回退。
- Q08两条路线均算对13只但被截断发布校验拦截，最终未交付名单，运行时问题记BC-079。
  Q03虽完整交付25只，却误称两个同源工具为“独立来源”，记BC-080待人工答案标签。
- Q02/03/04最终名单及Q10状态已做技术核对，但不将核心事实一致当完整答案人审通过。
- 复核材料：`output/agent-eval/real-dev6-v2-run-20260922-001/HUMAN_REVIEW.md`；原始答案、用量、轨迹与审核原件均保留。
  下一步优先处理Q08的任务范围完整性、补Q01真实录制，随后再按授权安排验证；不改宽合同或重跑挑选通过。
  详情见[Dev6实跑结果](Agent_Evaluation_Real_Dev6_Contracts.md)。

### Dev6业务人审与计数修复完成，实跑待外发授权（2026-09-22）

- 用户bibo已在REVIEW.md对6题全部填写认可；原始评语不改，认可仅覆盖题目、事实和判分合同。
- BC-077生产事件池计数修复完成（BC-078），108项定向测试通过。新v2包58条录制回放通过，
  仅09-21涨停池count/list的命中数由100修正为103；6题业务合同未变，既有审批迁移校验通过。
- 首版错误录制、原始数据和用户审核文件全部保留；v2包位于`output/agent-eval/real-dev6-v2-20260922-001/bundle/`。
- 实跑被安全审查在启动前拦截：当前DeepSeek目的地及市场证据外发需明确授权。新增模型调用0、Judge调用0、Active增加0。
  获授权后6题各1次有界真实运行再进行答案复核，不能把业务认可当成答案通过。
  详见[Dev6最新进展](Agent_Evaluation_Real_Dev6_Contracts.md)。

### 真实 Dev6 已形成候选合同与录制（2026-09-22）

- Q01/02/03/04/08/10 已按现有格式生成 6 道 candidate，原始日期、股票及事件均来自只读本地采集记录。
  两日相关字段摘要与重设计题单的来源一致，58 条生产工具录制全部回放一致，独立 SQL 与可用工具路线交叉核对完成。
- 5 题待人工确认题意与合同；Q01 另有工具计数阻断：09-21 原始收盘涨停 103 只，market_summary 返回 103，
  market_event_pool(count) 因内部 100 行截断仅返回 100。错误原样保留为 BC-077，未修复，不误记 Agent 失败。
- 0 模型调用、0 Judge 调用、0 新增 Active；没有人工代签，没有新框架或评分器，也未改 Agent/Prompt。
  这是来源/回放准备完成，不是回答质量通过；详情见[真实 Dev6 合同与录制](Agent_Evaluation_Real_Dev6_Contracts.md)。
- 人工入口：`output/agent-eval/real-dev6-preparation-20260922-001/bundle/REVIEW.md`。
  只填问题/口径认可与意见；尚无真实回答，不填答案三维标签。后续先处理工具计数问题并确认合同，再进行逐题实跑。

### 选题方向已纠正：真实场景题面重设计（2026-09-22）

- 用户人工反馈为4题认可、6题不认可，并明确要求真实股票名称及真实数据。原标签原样保留，
  不把合同拒绝直接归为Agent六题失败，也不继续用这些合成题凑真实场景Active。
- 新设计10道单轮、5道多轮自然问法，基于只读核对的真实事件、日K和新闻；
  风华高科000636、超声电子000823、动态最高板对应华瓷股份001216均有实际记录支持。
- 题面及数据覆盖说明见[真实场景题单](Agent_Evaluation_Real_Questions.md)。新闻全集与价格口径仍需在正式合同中审清，
  多轮执行尚未实现；本次0模型调用、0新增Active、不修改既有Golden/人工评语。
- 下一步先请用户确认题面，再接真实录制和最小合同；热点、大盘、龙虎榜原话保留为待补数据题，不拿合成数值填充。

### Dev10同版本真实运行完成（2026-09-22）

- `output/agent-eval/dev10-v21-run-20260922-001`：同一v21/deepseek-v4-flash，10题各1次；
  4 complete、3 partial、1 empty、1 clarify、1 refuse，终态全部符合合同。
- 44次模型调用、274,446 Token且逐调用用量完整，Judge 0调用；无服务/夹具异常、无超时或预算耗尽。
  全部自动结果仍为needs_review，不能写成10/10质量通过；四道修订题仍是candidate。
- 本轮独立核对OFF-038的40行全集→20行计算→完整渲染结果一致；旧判分器仅检查预览可见性，
  仍保留待复核，不扩建判分器。B026的“尚未入库”成因断言记BC-076待人工确认，未改Prompt。
- 完整定向回归136 passed；原Golden64的64组Case/World摘要及四题原始recordings全部未变。
- 人工复核材料为运行目录的 `HUMAN_REVIEW.md`，标签留空；下一步人工确认本批合同/答案，
  技术工作转向最小两轮worker。详见[Dev10审阅清单](Agent_Evaluation_Dev10_Review.md)。

### Dev10四题合同已修订（2026-09-22）

- B002/026/028升为v2，B030升为v4；补交付事实和正反例，保留原题意、原始payload及其余26题。
- 复用既有grading字段与构建器，生成隔离候选包 `output/agent-eval/dev10-contracts-20260922-001`：
  4个修订candidate＋6个原Active引用；原Golden64不变，未新增人工批准或发布通过。
- 70项定向测试通过、1项临时目录测试未选；实际四题构建与回放完成。未改Agent/Prompt。
- 首批10题按180秒/题、最多16次模型调用/题、各1次运行，不启用Judge；现已完成，结果见上方。
  合同细节与外发边界见[Dev10审阅清单](Agent_Evaluation_Dev10_Review.md)。

### 首批Dev10技术审阅（2026-09-22）

- 已逐题查看10道现有Offline题的合同、World及历史真实回答；6题可进入当前版诊断，
  OFF-B002/026/028/030四题先补交付字段或合法替代路线说明。未改Golden状态，也未计作新增人工复核Active。
- 现有run-golden零模型预检10题全部not_run、退出0；Frozen Registry录制参数检查全部通过；
  15项标准事实值有录制支持，OFF-038完整40只收盘涨停集合的独立Top20排序与合同一致，但没有并列金额覆盖。
- 本轮没有修改Agent/Prompt、没有新建评测框架、没有调用真实模型；原始预检产物在
  `output/agent-eval/dev10-review-preflight-20260922-001`，不提交生成报告。
- 不依赖临时目录的定向回归58 passed、7 deselected；另7项首次因Windows沙箱临时目录权限未完成，
  不计为通过。完整重跑的权限审查两次超时，命令未执行；缓存目录权限warning不影响已通过的58项。
- 纠正旧Live阻断的过强归因：旧market_summary录制与当前输出存在字段合同漂移；
  “104个数据库无匹配”不能单独证明历史快照丢失。旧运行的data_failure标签保留，根因待分离核实。
- 下一步先按四题修订清单形成新版本合同，再真实运行首批10题；不覆盖旧资产，不把技术审阅当人工标签。
  逐题标准、历史证据、Holdout隔离边界和剩余覆盖见[Dev10审阅清单](Agent_Evaluation_Dev10_Review.md)。

### 近期执行优先级（2026-09-22）

停止继续扩建Eval framework。接下来按以下顺序推进：

1. 将真正完成逐题人工复核的Active扩到30～40题，优先覆盖事实、动态两步、条件分支、错误恢复、多工具比较、Evidence compute、歧义、拒答和输出约束；
2. 在现有worker上补最小两轮真实Agent执行，覆盖previous entity/result set、pronoun、日期修改和旧证据刷新；
3. 冻结Active Dev 30题与Private Holdout 10～15题，避免所有题被反复用于调Prompt；
4. 对关键Case执行3 trials并报告pass@1、3/3、2/3、0/3；
5. 用20～30个先经人工标注的真实Answer校准Judge，再决定是否进入release gate。

当前Golden64仅表示合同准入库存，不等于64题都完成当前版本人工答案复核。后续同时报告
`contract-admitted`和`reviewed Active`，不再用前者代替后者。详细批次与停止项见
[近期执行优先级](Agent_Evaluation_Execution_Priority.md)。

### 当前快照（截至 2026-09-20）

| 层级 | 当前规模 | 含义 |
| --- | ---: | --- |
| 规划蓝图 | 88 | 40 Offline、30 Historical Live、12 Current Invariant、6 External Canary；蓝图不等于可运行 Case |
| 可运行 Basic70 | 70 | 50 Offline + 20 Historical Live，覆盖当前26个注册业务工具的资产并集 |
| Active Golden | 64 | 45 Offline + 19 Historical Live；题目与判分合同已批准，不等于 Agent 通过 |
| 暂缓准入 | 6 | 保留合同、Judge 漏判或内容争议，不为凑数自动晋升 |

当前工程链路已经具备 Frozen Record-Replay、隔离 Offline/Historical Live Worker、确定性合同、
事实/语义判分、正式报告、稳定性聚合和统一 `run-golden`。Local30 曾在旧运行时形成限定范围的
30题正式基线；Golden64 已完成代码合同预检和64题调度验证，但旧Local30 Live数据库快照没有保留，
不能通过当前新增的Live内容回放预检。`react-runtime-v20` 已完成10题真实代表运行；当前代码已升为
`react-runtime-v21`并完成失败终态定向复验，但Judge仍未校准，
尚未完成Golden64全量真实运行，也没有形成同版本三轮稳定性结论。

当前所有正式/统一报告仍为 `release_eligible=false`。旧Local30快照、Current Invariant、External Canary和
Release Manifest继续保留为缺口，但不再优先于 reviewed Active、Multi-turn、Dev/Holdout、关键题稳定性和
20～30个真实Answer的人工Judge校准；不直接用旧trace、旧运行时或合同准入数量替代当前质量基线。

### P0当前可信基线已建立（2026-09-20）

- 当前版本10题真实代表运行：6 pass、1 fail、3 unscorable，判分覆盖率70%，记录271,283 Token但不完整；原始产物为 `output/agent-eval/p0-v20-smoke-run-001`。
- `OFF-B025`是保留的真实Agent终态失败；`LH-033`是缺少匹配历史SQLite快照导致的data failure。两者都没有被补评或文档改写成通过。
- `OFF-036`在收紧Judge Evidence ID协议后补评三维pass；`LH-B003`在显式80,000字符上限下补评三维pass。两次补评合计29,475 Token，只作补充诊断，不覆盖原始unscorable，也不计作官方8/10通过。
- dry-run现已零模型重放Live baseline。代表10题预检中9题可继续、`LH-033`在模型调用前明确阻断，产物为 `output/agent-eval/p0-v20-live-preflight-001`。
- 当前业务库上的完整64题预检为54题可继续、10题旧Local30 Live全部data failure；45个Offline和9个新Snapshot Live兼容。产物为 `output/agent-eval/golden64-v20-live-preflight-001`，0模型调用、CLI退出码2。
- 工作区104个SQLite/DB候选只读回放后0个匹配旧Local30 baseline；不能用录制输出反向伪造历史Live库。v21完整预检结果仍为54可继续、10阻断，产物为 `output/agent-eval/golden64-v21-live-preflight-001`。
- `OFF-B025`已在类型化gate修复并登记BC-075；v21真实复验1题pass、24,498 Token，产物为 `output/agent-eval/p0-v21-offb025-recheck-001`，原v20失败不改写。
- 新增6个P0边界作者标注样本；两次真实Judge校准都只有3/6完成，其余Provider RuntimeError，且第二轮仍有维度误判/弃权。Judge保持`calibrated=false`，不以部分成功推进全量运行。
- v21最终完整后端回归991 passed、10 skipped、3个第三方弃用warning、2个subtests passed；首次回归暴露的Basic70可空limit schema兼容和终态测试ID问题已修复，最终无失败。
- 完整证据、解释边界和后续动作见 [P0当前基线](Agent_Evaluation_P0_Baseline.md)。

### 当前代码合同预检已接入（2026-09-20）

- `run-golden` 的 plan/report 现在绑定运行时、工具契约、Evidence 版本、Agent Prompt 摘要和 Judge Prompt 摘要；Active Case/World 即使自身摘要一致，只要与当前代码合同漂移也会在模型调用前拒绝。
- 当前实际合同为 `react-runtime-v21`、`agent-tools-v2`、`react-evidence-v4`。此前顶部快照沿用的 v15 已纠正，后续不再凭手工文档判断“最新运行时”。
- Golden64 的64题代码合同预检通过，产物为 `output/agent-eval/golden64-v15-contract-preflight-001`；该历史运行没有回放Live数据内容，不能称为完整可运行预检。目录名保留首次执行时的原始命名，不覆盖或重命名。
- 本轮真实模型调用0。真实代表题运行会向已配置模型服务发送问题、冻结/Live证据和生成答案，必须在明确授权具体外发范围后执行；当前结果只证明资产与代码合同兼容，不证明Agent回答质量。
- 39项Golden运行、trace review和无损Evidence定向回归通过；首次沙箱运行的5项临时目录权限错误保留为环境失败，不计为代码失败。

### Judge默认无损编码与预算预检已接入（2026-09-19）

- review_trace默认尝试无损编码，worker、run-golden和保存trace复评共享该策略；CLI保留显式禁用开关。短题无收益则原样保留，不增加裁判次数。
- 默认预算仍24,000字符，记录编码前后长度、节省量、预算判断和超出量；超预算0调用。2026-09-20起仅Judge结构化协议错误最多重试一次，网络、事实、Agent失败不重试。无模型预检也显示预算判断。
- 两道历史长证据预检均可逆还原，压缩后76,593/79,037字符，仍超默认上限。近期生产运行时已更新，旧trace同时因版本不兼容拒评；保留保护，没有绕过校验或声称重新验收通过。
- 51项定向回归通过，真实模型调用0。下一步少量代表题真实冒烟前，先核对当前运行时与旧资产兼容性；不直接全量运行64题。

### Golden64统一运行与诊断报告已接通（2026-09-17）

- 新入口`run-golden`支持Active全量/选题运行，复用现有worker，兼容新旧合同；旧合同保留原提取，可选同一trace Judge。原Candidate入口与Local30正式认证链路不变。
- 统一输出plan、逐题结果、JSON/Markdown报告；区分pass/fail/unscorable/needs_review，并同时报告判分覆盖率。诊断pass不等于发布认证，仍保留release_eligible=false。
- 实际64题CLI零模型预检通过；脚本executor覆盖64题调度和45 Offline/19 Live路由。没有全量真实模型运行，也没有宣称v15稳定性通过。
- 共享worker、Live、旧验收与trace回归45项通过；随后补充未启用Judge时确定性过程失败不能丢失的回归。
- [运行说明](Agent_Evaluation_Golden_Run.md)。下一步仍是默认长证据预算策略及少量真实冒烟，本轮不扩题、不改生产Agent。

### 用户批准34题，Active Golden已达64题（2026-09-16）

- 当前入口：`output/agent-eval/golden/basic64-current-v1/suite.json`。64 Active =45 Offline+19 Historical Live；新增34题，原30题资产不变。
- 按用户明确批准的[34题清单](Agent_Evaluation_Golden_Admission.md)执行，approval.json绑定批准原文、候选摘要及证据摘要，不覆盖旧包。pending.json仅保留6题：OFF-B005/009/014/027/029和LH-B006。
- 64题重新加载/摘要校验通过；6项专项回归通过，验证审批范围、过期摘要拒绝、旧资产保留及重复执行保护。本次0模型调用。
- 这是题目准入，不是Agent 64题全通过；Judge仍为诊断，争议事实需复核。暂缓6题不进入Active套件。

### Golden准入收尾清单已形成（2026-09-16）

- [逐题准入清单](Agent_Evaluation_Golden_Admission.md)：建议34题审签、暂缓6题；仍30 Active，未伪造用户批准。批准后目标64题（45 Offline+19 Live），不是等待Agent全答对才准入。
- 当前40题Case和World/baseline摘要都有匹配实跑；复用已有证据，未重跑Agent。6个裁判校准样例标签全部匹配，两道长证据保存答案补评均完成，输入无损编码后各限定8万字符；默认上限未放宽。
- 本轮仅8次Judge、83,566记录Token；15项脚本测试通过。LH-B006依然存在Judge漏判，和OFF-B005/009/014/027/029一起暂缓；不能把小样本校准等同全域可靠。

### 生产回答专项修复：证据表格与计数口径（2026-09-16）

- 运行时升级react-runtime-v15。finish.table按证据原始行确定性生成名单，不要求LLM逐行复制；渲染后仍执行合规审查。显式只列字段优先于一般日期/来源扩写。评级view区分筛选前总体、定向返回数量，不改业务数据/历史baseline。
- 154项定向测试通过，覆盖83行完整输出、字段/历史/错误/截断保护、发布前合规、HTTP和生命周期及评测worker。
- 真实复验 `output/agent-eval/basic70-answer-recheck-001` 四题：LH-B003全部83行所有输出单元格一致；OFF-B007严格三列；LH-B007正确区分58总体与空候选；LH-B006计数正确但额外K线解释仍待修。记录25调用、254,612 Token。
- OFF-B007/LH-B007的Judge完成且三维pass；LH-B003/LH-B006因输入超限未评。均仍needs_review，不晋升。BC-064至067记录修复与新发现；下一步为缺失解释证据约束及Judge长证据/漏判校准。旧版本稳定性结果不得冒充v15稳定性。

### 评测合同与夹具修订完成（2026-09-16）

- 本轮只改评测：16道Offline和3道Live的场景/题意/终态合同，未修改生产Agent。最新资产 `output/agent-eval/basic70-runnable-005/suite.json`，仍50 Offline+20 Historical Live；Active仍30，候选40，未晋升。
- 共享场景支持按生产Schema展开limit、同一评级事实的查询/过滤/质疑、按日与区间一致的统计。搜索只允许显式实体和公告词表改写；其他实体、日期或语义不自动匹配。合成故障场景显式声明来源不可用，不将未录制查询伪装为成功或空集。
- 修正statistics_days为最近N个满足观察窗口要求的信号日；Live空候选合同不再误报；单日审计明确起止日期。两道有限新闻/合成公告题允许有依据的partial，不统一放宽全部题。
- 19道不同题共29次真实复验，记录166调用、988,007 Token；四批原始结果保存在basic70-contract-recheck-001至004。原14道夹具阻断题各自最新运行均不再fixture_failure；这是路线可运行验证，不是稳定性或答案正确率。
- 仍待处理：生产Agent的补造名单、只列约束、总体样本数冒充候选数；Judge协议错误、长证据超限与漏判。本轮没有掩盖或修复这些问题。
- 验证：139项定向回归通过，覆盖场景匹配边界、原录制兼容、Live副本运行、worker和验收入口；仅有一条第三方LangChain弃用警告。

### 新增40题真实首轮验收完成，未通过整体准入（2026-09-16）

- 用户授权后，deepseek-v4-flash完成40次真实Agent运行及4次保存trace的补评；共记录245调用、1,575,698 Token（失败服务未上报用量可能不在内）。旧30题未重跑，没有自动晋升。
- 原始worker：24 needs_review、14 fixture_failure/unscorable、2 fail。原始Judge：21完成、3过长跳过、3协议失败、13硬失败跳过；补评4题均完成。原始失败不被补评覆盖，不能把Judge三维pass当Golden通过率。
- Codex逐题复核分组：17题核心要求初核无明确阻塞，14夹具路线阻断，2终态合同错误/争议，2题意/覆盖缺陷，3明确回答错误，2缺失归因待核验。明确错误包括长名单补造、违反只列约束、总体样本数冒充候选数，后两项Judge漏判。
- 完整方法、逐题结论和证据见[首轮验收报告](Agent_Evaluation_Basic70_Acceptance.md)。机器摘要：`output/agent-eval/basic70-acceptance-audit-001.json`；原始运行及补评分别在basic70-acceptance-001、basic70-acceptance-review-001。
- 50+20是可运行资产数，不是已验收题数；Active仍30、候选40。下一步建议先修评测合同/夹具，再单独修生产Agent，待用户确认实施顺序。

### Basic70可运行候选集接通：50 Offline + 20 Live（2026-09-16）

- 新增10个标准Live Case及版本化baseline，合并清单为 `output/agent-eval/basic70-runnable-001/suite.json`：50 Offline、20 Historical Live，工具资产并集仍26/26。新增40题仍为Candidate，原30 Active未改；不是70题Golden验收完成。
- `SnapshotLiveRegistry`共享生产Gateway/工具实现，每次运行从只读基线库backup到独立live.sqlite，再执行真实工具检查基线。允许本地10工具的合法参数变化，不以录制参数查表返回答案；远端工具不开放。只豁免三个post_limit工具顶层generated_at，其他业务值和状态漂移在模型调用前阻断。
- worker已识别local_snapshot_live用例，生成标准manifest/response/result、基线检查及trace-review。默认零额外Judge；run-offline及run-live-historical可显式--allow-judge，一次评三维，共享原调用/时间预算，超输入预算不截断证据、不自动重试。Judge仍是实验性诊断，不自动晋升或产生发布pass。
- 88项定向回归通过，含10题真实本地工具＋脚本Agent完整worker、漂移阻断和脚本Judge协议。补测worker.main实际CLI数据库防护，避免只在函数测试中可用。真实LLM与真实Judge调用均0；不能把脚本回答计为质量通过。
- 下一步不再补Runner：对新增候选做少量真实模型＋Judge的有界验收，修正暴露的内容/参数问题，再按审核流程晋升Golden。策略状态仍是采集时状态，副本不是历史时点原貌。

### Basic70契约修订及10个Live场景实工具预检（2026-09-16）

- 修正8道Offline候选：晋级明细、路径anchor_date、统计窗口、评级snapshot_source及嵌套facts、Critic枚举、搜索results字段；受影响case/world升v2，旧产物不覆盖。新包为 `output/agent-eval/basic70-candidates-003/suite.json`。部分响应用生产Pydantic模型验证，其余仍是最小合同夹具。
- 一个共享入口完成10种生产Gateway实工具预检：晋级、K线、涨停后筛选/路径/统计、评级/过滤/质疑、预测审计、策略状态。只读打开源SQLite并backup到副本；执行只允许副本数据库，拒绝ATTACH、网络连接与子进程回退。
- `output/agent-eval/basic70-live-readiness-003/report.json`：10/10取得真实工具观察、0阻塞；评级过滤为空结果，保留empty。选样为2026-09-11首板且本地有20根日K的000636。先前失败批次保留。
- **当前仍为50 Offline资产（20 Active+30 Candidate）和10个已接入Historical Live；另10个Live场景只完成问题、参数、实工具录制及依赖预检，不是已可执行的Live Agent用例。** Active Golden仍30。策略状态属于采集时状态，副本可能含后续修订，不能冒充历史时点存档。
- 105项定向测试通过，包括生产字段、版本引用、数据库隔离/防ATTACH、失败留档及原有worker/录制回放。真实模型调用0。
- 下一步：将这10个真实观察接入共享Live worker与标准Case断言，保留基线漂移检查，再做小预算验收；不再逐工具增加Runner。目前不能声称50+20 Golden已完成。

### Basic70首批内容已落地：30个新增Offline候选（2026-09-16）

- 新增可版本化题目资产 `backend/evals/suites/basic70-expansion.json`，不是蓝图占位：23个缺口工具各1个主场景，另7个空/错误/滞后/注入/输出约束/统计口径/跨工具场景。每题有问题、参数、合成观察、字段断言、正反回答、期望终态。
- 新增 `build-basic70-candidates`，通过生产Gateway校验参数和冻结回放，校验字段预期及证据视图；原Local30引用的case/world摘要全部校验后保留原引用。已生成 `output/agent-eval/basic70-candidates-002/suite.json`。
- 当前资产数：50 Offline（原有20 Active+新增30 Candidate）、10原有Historical Live；资产工具并集26/26。Active Golden仍30题、3个工具，新候选未擅自晋升，尚缺10 Live及候选审阅/真实有界验收。
- 公共录制/回放已支持原生列表：UI trace仍为对象容器，Gateway观察保持原列表，不扩大生产业务工具范围。worker遇到新通用工具断言不再调用旧市场概览答案提取器；显式待语义复评，不静默通过。
- 101项定向测试通过，包括30题契约回放、23工具实际ReAct链脚本模型测试、原生列表生产录制回放及既有worker回归。真实模型调用0、业务外网调用0；脚本模型只证明执行链可用，不证明Agent回答质量。
- 新观察为最小合成合同夹具，不是完整生产输出Schema认证；需审阅合理替代参数路线、输出字段及参考答案，尤其不能以固定路线掩盖fixture failure。下一步优先完成内容审阅与缺少的10 Live，不再新增微型优化实验。

### Basic70目标与压缩对照结论（2026-09-16）

- 当前Active资产逐项核对仍为20 Offline+10 Historical Live，证据涉及3/26工具；距离50+20尚缺30 Offline、10 Live和23个工具的代表覆盖。详见[Basic70实施清单](Agent_Evaluation_Basic70.md)，新增数量分配为23个缺口工具场景+7个共性边界Offline，以及10个Live。
- 完成 `--suite compaction_pairs`：两个Top3合成正反例，各评原始/压缩一次，共4次调用、7555 Token；25项离线测试通过。
- 正例原始1995、压缩1780 Token（节省10.8%），三维一致且符合标签。负例原始1999 Token，压缩响应1781 Token但协议校验失败：grounding给pass却列出错误声明，同时误将要求中的名称判为不应输出。失败请求/响应已保留，不自动重试。
- 总用量包含失败协议响应；配对报告负例compact_tokens为null，因为未产生有效裁决，原始用量仍可查 `calls/call-04-response.json`。产物位于 `output/agent-eval/trace-compaction-pairs-001/`。
- 压缩未通过配对验证，继续默认关闭，不影响原始证据路径；不声称故障一定由压缩引起，单次结果不能建立因果。后续不再以优化/微型校准阻塞Golden内容扩展。
- 本轮没有新增Active Golden。下一批按Basic70优先实现5个统计/时序Offline候选，采用公共录制/回放与trace评测，不逐工具写Runner。

### 可选无损证据精简（2026-09-16）

- `review-trace --compact-evidence`新增行表编码：仅将同字段对象列表表示为columns+values，不删列、不截行、不改变顺序或合并重复行；日期、单位、null、缺失和截断标记均保留。字段集合不同的行不强行对齐，编码保留键冲突时整包回退。
- 每次应用前自动解码并比较canonical JSON，记录原始/还原摘要；连同解码说明没有净字符收益时保持原格式。默认关闭，旧评测流程及提示不变；启用时解码说明纳入prompt_digest。
- 同一当前提示下，LH-001完整输入从22458降至11328字符，减少49.6%；所有源事件与派生Top10结果均保留。LH-004、LH-031不获净收益，自动回退。与先前旧提示的22211字符不是同一基线，不能混算。
- 24项离线测试通过，覆盖还原、重复行、缺失键/null、异构行、嵌套、保留键冲突、坏表结构及Judge传参。三条真实trace仅作本地预检，模型调用0、Token0。
- 产物为 `output/agent-eval/trace-compact-LH-001-preflight-001.json`及对应LH-004、LH-031文件；同提示原始基线为 `trace-current-LH-001-raw-preflight-001.json`。产物不提交。
- 该结论只证明序列化层数据保真与字符减少，不证明模型理解等价或Token等比例节省。下一步用少量合成正反例对原始/压缩格式做配对裁判验证，再决定是否默认启用；真实trace不自动外发。

### 显式输出限制判分边界（2026-09-16）

- 调整合并Judge提示：用户明确的“只列代码名称／不要解释”属于任务完成约束，不是自由写作风格；事实正确的额外统计或说明仍可违反该约束。普通编号和分行不算违规，必要的安全、澄清及关键数据缺失说明保留例外；不连带判事实接地或安全失败。
- 新增独立 `output_constraints` 校准组：严格纯名单、严格额外解释、普通要求下相同解释、严格要求下必要缺失说明。默认core组仍为6例，不扩大默认调用量。
- `deepseek-v4-flash` 一次4调用、5198 Token，12个预设维度标签全部命中；严格额外解释仅task_completion为fail，其余3例均三维pass。此组没有事实／安全负例，不据此宣称这些维度漏判率为零。
- 13项离线协议及回归测试通过。真实校准产物为 `output/agent-eval/trace-output-constraints-001/report.json`，绑定新提示摘要；未重跑Agent或任何真实trace，旧裁判记录未覆盖。
- 提示已改变，旧core校准与真实trace结果仅代表旧提示；本次是定向边界验证，不是新提示完整回归或独立人工验收，发布资格仍为false。
- 下一步回到成本控制：优先设计可审计的证据精简规则及离线保真测试，不以反复真实模型运行代替实现。

### 真实trace三题Judge复评完成（2026-09-16）

- 用户明确授权后，使用已配置 `deepseek-v4-flash` 对LH-001、LH-004、LH-031各调用一次，未重跑Agent、未自动重判。三题的任务完成、事实接地、边界安全均被Judge判为pass；统一报告仍保留needs_review和release_eligible=false，不替代正式验收。
- 实际用量：LH-001为8718 Token，LH-004为1704，LH-031为1458；合计11880。名单题占约73.4%，约为简短计数题的6倍。没有配置费用换算，不能把Token数写成金额。
- 复核保留一项疑似漏判：LH-001问题明确“只列代码名称”，回答增加口径、32只总数、来源等说明；Judge的task_completion理由只确认名单和排序，没有评价显式输出限制。这不是名单事实错误，也尚非独立人工裁决；需要用成对样例明确“普通解释自由”与“用户显式禁止额外输出”的区别。
- 产物：`output/agent-eval/trace-real-LH-001-judge-001.json`及对应LH-004、LH-031文件，保存输入摘要、提示摘要、模型、裁决和Token；不提交真实运行产物。
- 下一步优先补显式输出限制的正反例并检查提示边界；本轮不修改提示重跑洗结果。随后再考虑证据压缩，必须保留排序依据、日期、范围和缺失标记，不能仅截取前10行来证明Top10。

### 真实trace小样本复评预检（2026-09-16）

- 选取已有 `local30-p1-final-run-001` 中LH-001（排序Top10）、LH-004（最高板及并列）、LH-031（历史日期计数），未重跑Agent。
- 默认输入预算24000字符下，三个完整裁判输入分别为22211、3120、2384字符，均可容纳；未裁剪证据。名单题接近预算，不能用简短统计题的成本推断所有问题。
- 本地核对LH-001：32条源事件均符合指定日期、收盘封板、首板条件；按成交额降序和代码升序重排后，前10代码、名称与既有合同相同，前10金额互异，第10金额大于第11。答案额外说明是否违反“只列代码名称”仍待语义裁决，不预先标为通过。
- LH-004证据返回4板、1条最高板记录，答案代码名称相符；LH-031证据为2026-09-10的count模式35家，与答案相符。这些核对仅针对trace证据，不验证原始市场数据。
- LH-001过程检查pass；另两条needs_review原因为“无适用过程检查”，不是发现Agent失败。统一轨迹检查不实现业务合同事实断言，因此三题整体仍待评。
- 零模型预检产物为 `output/agent-eval/trace-real-LH-001-preflight-001.json`、对应LH-004和LH-031文件。真实Judge请求在执行前被安全审核拒绝，未发送这些真实trace，本轮新增Judge调用0、Token0。
- 此处记录首次未获外发批准时的预检状态；用户后续明确授权后的3次执行结果见上节，不再处于授权阻塞状态。

### 合并Judge首轮小样本校准（2026-09-16）

- 新增 `calibrate-trace-judge`，与统一trace复评共用同一Judge函数及协议验证；固定6个合成样例、最多6次调用，不运行Agent、不自动重判。
- 使用 `deepseek-v4-flash` 完成一次真实运行：6次调用、6806 Token。11个预先标注的维度判断全部命中：任务完成3/3、事实接地4/4、边界安全4/4；在这些标签上误杀0、漏判0、弃判0。其他未标注维度不计入命中率。
- 覆盖正确统计、错数、遗漏要求、交易指令、诚实说明缺失、掩盖缺失。样例为合成作者标注，不是独立人工验收或总体误判率估计；未修改提示重跑，未将review-trace自动升级为正式通过。
- 产物：`output/agent-eval/trace-judge-calibration-0916-001/report.json`，同目录保留样例、逐项裁决与6次请求/响应。生成产物不提交。
- 离线协议及回归测试11项通过。下一步优先从已有真实trace挑少量更复杂的名单/统计回答，核对证据体积和人工可复核裁决，不新增专用Runner或重跑Agent。

### Trace优先、低成本复评（2026-09-16）

- 暂停逐工具Runner扩建，以已保存的case/response trace为统一评测输入。
- 新增 `review-trace`：复用轨迹/终态和过程检查，汇总工具尝试、重复调用、策略决策、耗时及历史运行用量；不重跑Agent。
- 默认零模型调用；显式开启Judge时一次评任务完成、事实接地、边界安全。最多1次调用、1800输出Token、45秒请求超时；输入连同schema默认限制24000字符（不是精确Token预算），超限不截断证据、不调用、不算通过。
- 合并Judge尚未做真实模型校准，维度结果为实验性；总结果不会输出pass，release_eligible保持false。旧正式评分器和已校准事实Judge不变。
- 已用现有 `local30-p1-final-run-001/OFF-001` 进行零模型复评，裁判输入2830字符；这是单题观测，不代表全套平均成本。
- 下一步优先少量人工标注正反例校准合并Judge；不扩题、不重跑全量Agent。

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

## 历史建设起点（2026-09-13，保留记录）

以下内容记录新体系从单题 M1 原型开始时的真实状态，不能覆盖本文顶部的当前快照。

新评测体系的长期设计已经确定，详见
[`Agent_Evaluation_Design.md`](./Agent_Evaluation_Design.md)。该文档定义了分层模型、
Offline/Live 固定容量、Frozen World、Case Schema、覆盖矩阵、确定性 Evaluator、
LLM-as-a-Judge 边界、稳定性、版本治理和发布门禁。

当时已开始 M1：`backend/app/agent_eval` 提供 Case、World、结果、预算和 Manifest
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
当时正式评测题库、批量 Runner、经过校准的抽取器及完整 Answer Fact Evaluator 仍未完成，
M1尚未达标。后续进展已记录在本文顶部的倒序建设流水中。
具体运行命令、边界及后续工作见 `backend/evals/README.md`。

已完成40道Offline与48道Live（30历史/12当前/6外部）的具体问题蓝图及规划覆盖矩阵。
88条蓝图保存在 `backend/evals/blueprints/core40_live48.json`，不是88个可执行Case；
当时正式active题目仍为0，既有市场汇总候选属于OFF-001。数据、bindings、终态及断言物化
尚待逐题完成。v1的24个工具已有正常主路径计划；extended除两个专属工具外的24个
正常路径仍是明确缺口，不能借用v1覆盖代替。下一步从本地事件小批录制与真实回答校准开始。
