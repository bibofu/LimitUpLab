# Agent 评测状态

## 当前状态

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
不能通过当前新增的Live内容回放预检。当前 `react-runtime-v20` 已完成10题真实代表运行，
尚未完成Golden64全量真实运行，也没有形成同版本三轮稳定性结论。

当前所有正式/统一报告仍为 `release_eligible=false`。尚缺 Current Invariant、External Canary、
充分人工校准的 Judge/Extractor、可移植的正式数据资产，以及把 P0、判分覆盖率、稳定性、Live、
预算和失败归因汇总为 Release Manifest 的发布决策层。下一步顺序是：恢复旧Local30匹配快照、
修复已记录的终态失败并校准Judge、Golden64全量真实运行、同版本 Stability Panel，再扩充 M2/M3 容量；不直接用旧 trace 或旧运行时
成绩替代当前基线。

### P0当前可信基线已建立（2026-09-20）

- 当前版本10题真实代表运行：6 pass、1 fail、3 unscorable，判分覆盖率70%，记录271,283 Token但不完整；原始产物为 `output/agent-eval/p0-v20-smoke-run-001`。
- `OFF-B025`是保留的真实Agent终态失败；`LH-033`是缺少匹配历史SQLite快照导致的data failure。两者都没有被补评或文档改写成通过。
- `OFF-036`在收紧Judge Evidence ID协议后补评三维pass；`LH-B003`在显式80,000字符上限下补评三维pass。两次补评合计29,475 Token，只作补充诊断，不覆盖原始unscorable，也不计作官方8/10通过。
- dry-run现已零模型重放Live baseline。代表10题预检中9题可继续、`LH-033`在模型调用前明确阻断，产物为 `output/agent-eval/p0-v20-live-preflight-001`。
- 当前业务库上的完整64题预检为54题可继续、10题旧Local30 Live全部data failure；45个Offline和9个新Snapshot Live兼容。产物为 `output/agent-eval/golden64-v20-live-preflight-001`，0模型调用、CLI退出码2。
- 完整证据、解释边界和后续动作见 [P0当前基线](Agent_Evaluation_P0_Baseline.md)。

### 当前代码合同预检已接入（2026-09-20）

- `run-golden` 的 plan/report 现在绑定运行时、工具契约、Evidence 版本、Agent Prompt 摘要和 Judge Prompt 摘要；Active Case/World 即使自身摘要一致，只要与当前代码合同漂移也会在模型调用前拒绝。
- 当前实际合同为 `react-runtime-v20`、`agent-tools-v2`、`react-evidence-v4`。此前顶部快照沿用的 v15 已纠正，后续不再凭手工文档判断“最新运行时”。
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
