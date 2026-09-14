# 40 Core + 48 Live 出题蓝图

`core40_live48.json` 是唯一维护源。它定义88个具体问题及交付项、工具/关键参数计划、
校验意图、数据需求、终态口径和待绑定对象。不是 CaseSpec，不包含伪造 expected 数值、
虚构 World 引用或人工审核标记，不能直接交给真实 LLM Runner。

## 规模与分层

| 组 | 数量 | 重点 |
| --- | ---: | --- |
| OFF-001～026 | 26 | 每个业务工具一项正常路径计划；24个v1工具、2个extended专属工具 |
| OFF-027～040 | 14 | 组合过滤、事件语义、计数、日期边界、空结果、部分失败、追问、安全、依赖和截断 |
| LH-001～030 | 30 | 真实历史工具 + 固定历史基线/checksum，不用当前工具冒充历史能力 |
| LC-001～012 | 12 | 本轮日期、对象、顺序、数量及状态不变量，不保存当天具体值 |
| LE-001～006 | 6 | 外部来源健康、失败/超时隔离、降级和不编造 |

原有 `core-local-summary-count` 候选对应 OFF-001，不重复计算为第41道题。
一个问题可有多个能力标签，不靠换股票、换日期或同义改写凑40个能力。
Historical Live 与 Offline 可以覆盖同一种问题，但考查真实依赖与冻结决策两个不同层面。

## 覆盖矩阵与当前缺口

运行以下命令（backend目录），不联网、不调用LLM、不访问数据库：

```powershell
.venv/Scripts/python.exe -m app.agent_eval blueprint-coverage --book evals/blueprints/core40_live48.json --output-dir ../output/agent-eval/plans/core40-live48
```

输出 `coverage.json` 和便于阅读的 `questions.md`。目录已存在时拒绝覆盖，应使用新目录。
矩阵由当前生产工具目录生成，包含每个Profile的启用状态、Offline/Live题号、正常主路径、
显式参数计划和未显式规划参数，不把“有题号”当“已经录制/已通过”。

- v1：24/24个工具已有正常路径计划。
- extended：目前2/26个工具有该Profile下的正常路径计划；其余24个仍列为缺口。
  v1题目不自动计入extended成绩。后续安排跨Profile执行或专属Case时需明确逻辑题与运行变体的计数规则。
- 部分材料参数尚未显式规划，矩阵如实列出；未提及的默认参数不等于已经覆盖其边界。
- 所有资产就绪状态均为 `not_assessed`，报告 `runnable=false`、`release_eligible=false`。
  某个案例已实跑的信息由运行报告证明，不从题目蓝图反推。
- 添加工具或改动参数后，蓝图加载及现有测试会检查未知工具、Profile越界和参数漂移。
  新工具的默认Profile正常路径缺口必须处理，不能靠保留26这个旧总数掩盖新增能力。

## 数据与录制顺序

1. **本地事件组**：先复用 OFF-001 实录，录制 OFF-009/010/027～033/038/039 所需事件、
   过滤、计数、空结果和分页视图。每个有效参数组合由真实工具执行录制，不能把一份返回
   手工复制给不同过滤参数。
2. **K线、指数、晋级组**：录制 OFF-002/003/012/014，并核验交易日历、基期和成熟窗口。
   先确定源字段含义，再填 Golden；不能只因数值能对上就跳过指标语义。
3. **评级、预测、形态组**：录制候选、评分版本、不可变预测、成熟Outcome和形态路径。
   明确工具是否内部调用LLM或写入派生缓存；这些工具的录制/Live运行必须隔离，不能认为
   所有“查询型名字”的工具天然只读。
4. **当前/外部快照组**：录制热榜、新闻、策略状态与extended专属工具。必须用真实获取
   时间建立独立冻结World，不能把后来取得的当前新闻、热榜或配置标作9月11日历史快照。

历史本地主World仍以2026-09-11 18:00为起点；周末World单独使用9月13日锚点、9月11日
最新本地日期。新闻等当前快照World另行锚定实际录制时刻，不偷偷移动已发布World的时间。
所有日期及对象是否确有足量/并列/有效空样本，均须录制前核验；未满足时保留候选并记录
调整原因，不编造市场数据满足出题前提。

## 从蓝图到可运行Case

每题依次完成以下事项，不能直接把88条蓝图转换成88个active Case：

1. 核验数据可用性、历史时点语义和来源权限。
2. 按bindings从真实证据绑定股票等对象，确定请求上下文和最终日期。
3. 录制/回放并生成有版本与checksum的World，或创建独立Historical Live Baseline。
4. 将requirements拆成有原文引用的RequirementSpec，双人复核P0标注和有争议的终态。
5. 将checks落实成实际AssertionSpec；选择有依据的工具等价路线，不把tools列表当强制调用顺序。
6. 审核全部交付项与原始证据关系，接入必要Evaluator；尚不支持的断言保留needs_review。
7. 用真实Agent运行，保存原回答、抽取和诊断；完成校准及资产审核后再考虑晋升。

Blueprint的`tools`只表示规划覆盖/录制依赖，`arguments`是某条可行路线的关键参数计划，
不表示唯一正确路线。`terminal_policy`中的条件说明也不是已经执行的终态断言；OFF-040等
能力限制题的准确终态尚待审核，不应擅自固化。

Current Invariant 不增加当天固定值；Canary自然运行没有遇到超时/部分失败时，不得宣称
已覆盖这些分支。受控故障演练应独立标记，不得伪装成真实第三方故障。

## 本地事件首批材料化

已增加 `prepare-local-event-candidates`，从同一只读 SQLite 会话窗口执行生产
`limit_up_events`，分别录制 OFF-027 和 OFF-038；不复制不同参数之间的工具结果。

```powershell
.venv/Scripts/python.exe -m app.agent_eval prepare-local-event-candidates --database data/limituplab.sqlite --anchor 2026-09-11T18:00:00+08:00 --book evals/blueprints/core40_live48.json --output-dir ../output/agent-eval/candidates/local-events-batch1
```

目录已存在时应换新目录。每题保存 capture、case、world、review 四个文件，均为本地
未审核资产，不提交包含实际数据的文件。真实录制结果：OFF-027 命中32只、返回10只；
OFF-038 命中40只、返回20只；两题初始预览均为8行，record/replay均一致。

两题均保留日期与有序代码/名称名单，不以无序集合检查替代排序检查。原始工具按成交额
排序；完整Evidence事件对象包含成交额，只有trace摘要省略该字段（此前说明已纠正）。
题目不额外要求回答金额。预期名单来自真实返回，
仍需独立核验源数据过滤/排序，录制回放一致并不证明生产工具计算本身正确。

当前累计材料化3道Offline候选（含OFF-001），不是3道正式通过题。新增两题尚未调用
真实LLM；`answer_matches_observation` 已接入有序名单诊断器及answer-only LLM抽取器，
但尚未完成独立人工校准，
因此必须保留 `needs_review`，不得以终态为complete替代内容正确。单一参数签名World
也尚未覆盖所有等价调用路线，未录制路径应归为fixture缺口，不能直接判Agent错。

有序名单诊断器校验日期、身份、顺序、重复/缺失以及当前完整Evidence和可见分页。
仅名称且无法唯一定位、抽取不明、trace损坏、未建立可见性或额外业务声明均待复核。
合法compute路径尚需可见性适配，不因缺少该适配误判Agent失败。自动抽取的覆盖率
保持未知，不把模型声称“已抽完”当作人工校准。已审核名单级覆盖率以一个名单声明
加额外声明计数，不能与汇总抽取器的原子声明覆盖率直接比较。

可使用 `check-event-facts --case ... --world ... --response ... --extraction ...` 离线复核
保存的回答和人工审核抽取；不提供抽取时返回待复核。`run-offline`按断言目标选择名单
抽取/诊断器，自动抽取仅接收回答、不接收World或标准名单，原始抽取与诊断均落盘。
当前程序样例覆盖漏项、错序、重复、多项、代码名称错配、日期错误、名称式/表格式
表达、截断与分页、历史证据排除、trace损坏和P0双人审核要求；这些是合成契约测试，
不是已经完成的真实回答人工校准集。此前本地录制文件保持不可变，其中旧review说明
“不含成交额”的文本以本节纠正为准；代码生成的新review已修正，不重写历史checksum。

下一步完成独立校准，再做这两题真实Agent诊断。新录制包含个股明细和
原始工具对象，外发前需明确新增数据范围，不能沿用此前只针对市场汇总的授权。
交易日历仍只是观察到的单日，尚不支持相对交易日题；Live蓝图仍未材料化。

## 首次事件题真实运行：发现Fixture缺口

2026-09-13，经用户明确批准新增个股明细外发范围后，使用配置模型
`deepseek-v4-flash`、`api.deepseek.com`运行两题，独立进程、新session/message ID。
原始报告保存在本地 `output/agent-eval/runs/real-events-off027-001` 和
`real-events-off038-001`，不提交真实回答、录制数据或请求日志。

| 题号 | 模型调用（含审核/抽取） | 实记Token | 耗时 | 结论 |
| --- | ---: | ---: | ---: | --- |
| OFF-027 | 10 | 66515 | 12.97秒 | unscorable / fixture_failure |
| OFF-038 | 8 | 53356 | 10.15秒 | unscorable / fixture_failure |

两题首次业务调用均为预期过滤/成交额降序，但请求 `limit=100`，而World仅分别录制
`limit=10/20`。这是一条可能正确的“先取更多、再截取前N”路线，当前exact-signature
回放无法执行。随后尝试的替代工具也未录制，最终未交付名单。不能把被Fixture污染的
partial/empty终态和trajectory fail计入Agent正常场景失败率，更不能放宽expected名单
或强迫模型只请求10/20来制造通过。此次两题都没有获得成功的业务证据。

抽取阶段另有独立问题：模型把多条原句合并或改写后填入 `additional_claims`，未通过
逐字原文校验，保存 `extraction-error.json`（ValueError）。原模型返回仍保存在逐次调用
日志中。此为评测抽取器问题，不是Agent事实错误；不得将抽取失败默认pass。

外发前还确认完整事件对象的D+1/3日/5日字段在这批数据中均为0、continued_next_day
均为false，采集器存在相同占位写法。没有逐行历史来源证明时，这些值不能当作成熟
Outcome或真实后续表现。此次保持生产工具返回原样，仅用于暴露真实诊断风险。

下一步先补真实工具 `limit=100` 录制及前N/完整可见性断言，建立新World版本并保留
旧版本、失败报告；再改进抽取原文定位并验证拒答/无名单样例。不得自动重试直至通过，
重跑必须注明Fixture/抽取器版本变化，且两次受污染运行不纳入稳定性统计。现阶段仍是
3道Offline候选、0道正式晋升，Live仍只有蓝图。

## 第二次事件题运行：两题正常链路已跑通

针对上述失败，Case/World升级为版本2，生成目录为
`output/agent-eval/candidates/local-events-batch2`。每题新增一份真实生产工具执行的
`limit=100`录制及独立回放校验，分别返回32/40条；与原始前10/20条逐字段核对前缀。
原Case/World和首次失败记录不覆盖。仍不声称支持所有工具/参数等价路线。

有序名单校验现在支持“较大完整结果的前N项”，但必须对应真实World和当前可见证据。
抽取器升级 `answer-only-event-list-v2`：宿主给答案逐行编号，模型仅引用额外声明的
行号，由宿主还原原文。越界、空行、无效成员仍拒绝；不通过拼接或正则修补模型文本。
同步修正run manifest，使extractor prompt摘要对应实际选择的抽取器。

| 题号 | 新运行目录（output/agent-eval/runs下） | 调用 | Token | 耗时 | 本次结果 |
| --- | --- | ---: | ---: | ---: | --- |
| OFF-027 | real-events-off027-002 | 7 | 62649 | 11.11秒 | complete；名单诊断pass；整题needs_review |
| OFF-038 | real-events-off038-002 | 7 | 67254 | 12.13秒 | complete；名单诊断pass；整题needs_review |

两题都成功执行 `limit_up_events(limit=100)`、`read_evidence`、`compute_result(select)`，
再交付完整前10/20名单。无Fixture拒绝，无抽取错误；日期、代码/名称和有序名单均与
World及本轮可见证据一致，trajectory检查通过。不是把之前的失败重写成通过。

整题仍待复核的明确原因：回答附带成交额、换手率、行业题材及解释性结论，超出当前
有序名单校验范围；自动抽取未独立校准，覆盖率未知。尤其“某产业链是当日资金最集中
方向”等推断，不能因名单正确就视为已验证。这些额外声明现已保留逐字原文供后续校准。
下一步应利用这批成功实跑答案补充数值/额外声明校准，并并行于后续工作逐批材料化
更多独立能力题；不再重复搭建同一单题链路，也不把两道题冒充40题或稳定性结论。

## 执行过程诊断（不只看答案）

新增 `check-process --case ... --response ... --output NEW.json`，可直接复核已有trace，
无需再次调用LLM；新Worker自动保存 `process.json`，summary单列process_diagnostic。
当前为独立诊断而非发布gate，不修改或覆盖之前的整题成绩。

- 按trace顺序检查read_evidence/compute_result的源Evidence是否已在此前Observation出现，
  同轮多个调用不假定后一个能读取前一个尚未观察到的结果。
- 对无filters的select（原顺序或amount排序）独立重算排序/offset/limit，核对完整items，
  不仅检查工具“成功”。其他计算操作保留needs_review，计算输出不一致先归为待复核，
  不能未经归因就判Agent答案错误。
- 从完整记录重建可见视图，核对日期/代码/名称的交付集合是否被看到；历史证据不充当本轮
  可见数据。直接获得足量证据可以通过，不强迫调用分页/计算。
- 报告调用尝试和重复签名数量；包含finish等控制调用。重复可能是合理重试或复用，
  未经场景约束不自动判错。缺失/损坏trace转为needs_review。

对 `real-events-off027-002`、`real-events-off038-002` 已分别生成
`process-review-v1.json`。两题的先观察后依赖、独立select重算、目标集合可见性均通过；
各4次调用尝试（含finish）、1次read、1次compute、0重复签名。此次没有新增模型调用，
没有增加题目数量，也没有验证复杂异常恢复、多轮、权限与生命周期等尚未覆盖能力。

## 第三批：5道Offline与首道Historical Live

2026-09-14完成本地数据核验、29个实际工具参数组合的录制/回放、5道Offline材料化及
首道Historical Live。当前累计8道Offline候选、1道受限Historical Live候选、0正式晋升。
本批每题都有独立进程/新session的真实DeepSeek运行记录。

| 题号 | 本批问题/事实边界 | 调用/Token | 实跑结论 |
| --- | --- | --- | --- |
| OFF-010 | 9月8日并列最高：3只4板 | 5 / 25759 | complete；needs_review |
| OFF-028 | 9月8日科创板盘中开板：4只，其中1只回封 | 5 / 26677 | complete；needs_review，首次抽取异常 |
| OFF-031 | 9月10日涨停35家，不用9月11日40家替代 | 6 / 34921 | complete；fixture_failure，不评分 |
| OFF-032 | 9月13日周末锚点，最新本地日9月11日40家 | 5 / 23989 | complete；needs_review |
| OFF-033 | 指定主题有效无匹配 | 9 / 94194 | complete；fixture_failure，不评分 |
| LH-004 | 实际查询9月11日最高连板成员及高度 | 5 / 11282 | complete；基线检查通过，needs_review |

OFF-010/028原蓝图日期9月11日没有所需并列/科创板开板样本，经用户明确批准绑定到
9月8日。题目原蓝图保留、材料化日期调整写入review，不伪造9月11日样本。OFF-033
为“有没有”的是非问句，候选暂允许complete/empty两种终态，最终语义口径仍需审核。

材料化命令（backend目录）：

```powershell
.venv/Scripts/python.exe -m app.agent_eval prepare-core-batch --database data/limituplab.sqlite --book evals/blueprints/core40_live48.json --output-dir ../output/agent-eval/candidates/core-batch3
```

Offline用已有run-offline；Live使用：

```powershell
.venv/Scripts/python.exe -m app.agent_eval run-live-historical --case ../output/agent-eval/candidates/core-batch3/LH-004/case.json --baseline ../output/agent-eval/candidates/core-batch3/LH-004/baseline.json --database data/limituplab.sqlite --output-dir ../output/agent-eval/runs/core-batch3-LH-004-001 --allow-llm
```

路径已存在须换新路径。Live的Case不引用World；baseline.json沿用World结构作为预先
版本化的历史对照数据，而不是运行时工具返回映射。Worker只允许对指定SQLite URI以
mode=ro读数据，关闭连接后在隔离进程内执行真实工具方法，无execute_frozen_calls。
基线预检会重新执行29个录制参数并比较完整payload/state；漂移归data_failure且不调用
模型。该模式是本地历史数据快照上的真实工具测试，不是外部数据源Canary，也不证明
生产HTTP租约、完整Profile或当前实时数据源可用。

本批已知缺口必须保留：

- OFF-031尝试market_event_pool历史计数；OFF-033尝试limit=50及market_event_pool，
  均尚未录制。虽然随后用已有路线回答了问题，整体运行已被Fixture污染，不计质量分。
- 首次运行通用业务题使用了旧summary数值抽取/校验路线，不适用于历史计数、并列高度、
  无序成员与有效空结果。因此OFF-031/033原facts中的fail不能作为Agent事实错误；
  OFF-028保留抽取错误。运行后已修正分派：这些business_contract使用行号式抽取并
  对未实现的业务断言明确needs_review，不拿最新summary真值校验历史事件。原报告不覆盖，
  后续新增运行或重新判定必须另存版本和来源摘要。
- OFF-032主日期/数量可检查，但主动补充的“炸板/未回封39家”等口径尚需独立审核。
- 本批事实标准已来源化，但除周末summary适配外，新增业务事实断言还没有完整自动判定；
  不能把“可运行候选”说成已完成校准的Golden。下一步应补这几类断言和已知合法路线。

## 首批Golden集中审核包

新增business-facts-v1，不再把answer.business_contract全部视为未实现。当前检查：
最高高度和完整无序并列集合、指定日计数、盘中开板成员、有效空匹配；先从完整baseline
建立可复核标准，标准自身与baseline冲突时待复核，不判Agent错。检查本轮Evidence
与baseline事件行、可见视图的一致性；LIVE不借用Offline世界引用作本轮证据。
缺项、重复、错日期或错高度可明确判错。自动抽取与额外声明仍需要校准/独立审核，
核心检查通过不是整题质量通过。当前需要reviewed代码/名称身份；不明确的名称表达待复核。

本次优先收敛OFF-010（Offline）与LH-004（Historical Live），生成：

- `output/agent-eval/golden-review/OFF-010-v1/REVIEW.md`
- `output/agent-eval/golden-review/LH-004-v1/REVIEW.md`

每份含问题、标准事实、判定合同、原始回答、自动诊断、审核清单和绑定摘要；附case、
baseline、extraction、facts与review.json。没有生成虚假的审核人或active状态。
现有回答分别消耗1次抽取调用（1905/1629 Token），核心最高高度及集合检查均pass，
整题因额外声明与未审核抽取保留needs_review。没有重新运行Agent或修改旧报告。

```powershell
.venv/Scripts/python.exe -m app.agent_eval prepare-golden-review --run-dir ../output/agent-eval/runs/core-batch3-OFF-010-001 --output-dir ../output/agent-eval/golden-review/OFF-010-v1 --allow-llm
```

此命令目前仅用于最高连板合同的审核材料，目录已存在拒绝覆盖。审核人需独立确认
需求、baseline事实、允许路线及校准；题目可晋升不取决于Agent本次是否答对。
这两份材料尚未获人工批准；OFF-031/033的Fixture缺口未在本轮偷偷放宽或掩盖。
