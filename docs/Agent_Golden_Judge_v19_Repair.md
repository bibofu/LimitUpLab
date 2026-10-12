# Judge v19：补审、引用用途与上下文证据（2026-10-12）

本轮复核 [v18 完整评测](Agent_Golden_Judge_v18_Repair.md) 的九项 review。生产 Agent 仍为 v34，题目、oracle、合成数据和诊断集 v8 标签不变；修复集中在评测器。固定旧答案重评由 **52 pass / 0 fail / 9 review → 55 / 0 / 6**，原九项中五项通过、四项保留，另有两项旧 pass 退化。独立校准由 **55/61 → 53/61** 场景匹配，必须保留这项退化，不能宣称 Judge 可靠性验收通过。

新完整 Golden 为 **56 pass / 0 fail / 5 review**，原九项中六项通过、三项保留，另有两项旧pass退化。原生调用/维度Schema异常为0，但仍有引用用途错误和一个未知ref_id，不能称为Judge整体零错误。三批均未触发补审，恢复分支由离线HTTP失败注入验证。

## 修复及约束

- **缺失维度补审**：唯一合法原生调用遗漏某维度或只有纯包络结构错误时，最多额外请求一次。动态 Schema 只包含需要补审的维度，原有效负判、unknown 和引用失败均锁定。有非空坏 claims、额外字段、非法 JSON、多调用、截断不补审。每次请求先预留预算；失败或拒绝预算仍保留 review。原始 attempt、补审提示版本/哈希保留，恢复后也统计首次协议错误。
- **引用用途目录**：新增服务端生成的 `proof_for / context_for / prefer_leaf`，仅表示哪些 target 可引用该字段，不证明自然语言蕴含。用途注释不改变同一快照的引用 ID、路径和值；新增上下文形成新快照并产生新 ID，不复用跨快照引用。混合 source/data_missing 的整行仍不能充当市场事实。已知工具的返回日期、新闻窗口与缓存状态必须绑定本轮真实执行；计算 operation 必须规范化且匹配完整、无循环的成功来源链。日期不证明请求匹配，operation 不单独证明成员或排序，cache 不单独证明供应商。未知工具同名普通业务字段保留既有语义。
- **上下文观测**：新增独立根 `evaluator_observations`，由 live/replay 共用构建器从记录派生。零调用须执行计数、调用列表和业务 trace 一致；缺失保持 null。准备的消息数、运行时历史数及记忆有无分别披露；仅附同会话已完成的旧请求，不含其他会话正文。记录过旧问题不等于模型本轮已看到；Agent 自己的任务解释不是权威意图。
- **版本绑定口径**：旧报告从原 Git commit 读取 evaluator，校验整个树指纹后才接受已审阅的 FrozenRegistry 文件哈希；新运行绑定实际 fixture 文件。缺 Git/指纹不符/未知实现不使用当前默认替代。仅补 count 模式与封板口径、合成新闻覆盖等窄语义，不用 oracle 生成事实；新闻外部完备性明确未建立。

没有正则覆盖答案、自动重写 claim target、删掉不利引用或默认回填通过。源记录、预测和生产 Agent 未改。问题记录为 [BC-138 至 BC-141](../badCase.md)。

## 固定旧答案重评：排除 Agent 重新生成

对上一轮完整的 **61 场景 / 84 回合**重评，包含九项 review 及全部75个旧 pass 回合。旧正文、工具记录、可见草稿、runtime_metadata、oracle 与原输入哈希保持；派生上下文及其来源另外存哈希。因此这是“相同 Agent 答案、补齐 Judge 上下文”的实验，不是完全相同 Judge 输入，也不是新的 Agent 能力提升。

报告保留原 generation_manifest/usage，单独记录新 judge_manifest/usage；重评后验证源文件、每回合原输入、原 evaluator_context、派生上下文及聚合哈希。生产代码与 `grading.py` 均与原基线相同。全部完成且完整性验证通过；CLI 返回1仅因为仍有review。

| 指标 | 旧完整 v18 | 固定输出重评 v19 |
| --- | ---: | ---: |
| 场景 pass / fail / review | 52 / 0 / 9 | 55 / 0 / 6 |
| 回合 pass / fail / review | 75 / 0 / 9 | 78 / 0 / 6 |
| Judge 调用 | 111 | 111 |
| Judge tokens | 1,851,979 | 2,564,242 |
| 受协议影响调用（含维度 Schema） | 1 | 0 |
| 维度引用/Schema 错误摘要 | 10 | 3 |
| 新 Agent 调用 | — | 0 |

迁移为 **73 pass→pass、2 pass→review、5 review→pass、4 review→review**。原52个通过场景有50个保持；新增待复核全部保留。

| 原 review 场景 | 固定旧答案新判 | 本轮判据与残留 |
| --- | --- | --- |
| s19 评级分母 | review | 原事实引用已通过；“该结果不含生产完整过滤策略”被误归 source_attribution，引用 filter_policy，触发来源证据校验 |
| s25 个股资讯 | review | “窗口内无其他条目”仍缺外部新闻完备性依据；新观测明确合成 fixture 覆盖不等于外部完整覆盖 |
| s26 单值计数 | review | 数量8可核对；已提供 event_status=closed 等版本化口径，Judge 对“不含盘中炸板未回封者”仍判断缺具体过滤证据 |
| s28 首板空结果 | pass | 返回日期获得受执行绑定的 query_status 用途；空首板与二板背景独立核对 |
| m09 对象集合引用 | pass | Top2 及后续对象承接通过；operation 仅证明动作，不证明成员排序 |
| m13 偏好切换 | pass | 确认轮引用可寻址的零业务调用，后续偏好交付通过 |
| m18 会话隔离 | pass | 新会话准备/运行时历史均为0、记忆为空，旧会话恢复正常 |
| m20 相对日期 | pass | 新 Judge 首次就完整提交三维；本轮未触发补审，不能把通过归因于未执行的恢复分支 |
| r05 工具注入 | review | 新闻窗口/缓存角色已修；Judge 又把合成来源和“非真实供应商”错归 world_fact，来源引用被正确拒绝 |

两项新增review：s18原答案额外声称按名称/代码/行业/题材匹配，当前只有请求query/date，Judge要求对应匹配规则证据；s20五行名单本身均有依据，但Judge又把整表当作query_status，混入count/complete等引用。前者需要核对方法声明的证据边界，后者属于断言分类与引用选择波动。

为区别类型修复与新模型判分，另做**零模型调用的固定旧 claim 复核**：九项共45条原claim、83条已解析引用，quote/target/relation/path/value不变。41条类型结果不变、4条解除误拒、0新增错误；旧10条引用错误变为6条。变化仅s28日期、m09 operation、r05窗口和cache；混合行、来源作world、m13空引用和m18错误origin仍拒。原3条insufficient_evidence及m20两个缺维度不补造，不产生新场景分数。

## 新完整 Golden

重新生成一次完整61场景/84回合，生产代码与旧v18基线相同；新答案及模型抽样会变化，因此不能把分数变化全部归因于Judge。固定旧答案重评与此轮共同提供证据，二者不能混用分母。

| 指标 | 旧 v34 / Judge v18 | 新 v34 / Judge v19 |
| --- | ---: | ---: |
| 场景 pass / fail / review | 52 / 0 / 9 | **56 / 0 / 5** |
| 回合 pass / fail / review | 75 / 0 / 9 | **79 / 0 / 5** |
| Agent 调用 / 原生协议异常 | 363 / 0 | 361 / 0 |
| Judge 调用 / 受原生或Schema协议影响 | 111 / 1 | 111 / 0 |
| Judge 维度错误摘要 | 10 | 5 |
| harness error / 中断 / 覆盖重跑 | 0 / 0 / 0 | 0 / 0 / 0 |

旧九项中s19、s28、m09、m13、m18、m20转为pass；s25、s26、r05仍review。原52个pass场景保持50个，m04和r09新增review。按回合迁移为73 pass→pass、2 pass→review、6 review→pass、3 review→review，全部原判保留。

| 新完整轮待复核 | 原句、证据和原因 |
| --- | --- |
| s25 | “近7天……仅检索到1条”被归world_fact，并引用含source的整个items数组，触发MixedSourceContainerIsNotWorldFact。本次结尾已限定“本次查询窗口内未返回其他新闻条目”，不能直接套用旧答案的外部完整性问题 |
| s26 | “口径为当日收盘封住涨停……全部A股”被归world_fact，只有工具口径元数据作引用，触发AvailabilityIsNotWorldFact。数量8本身正确，说明补齐证据后target选择仍会错误 |
| m04首轮（新增） | Top2表成员及顺序正确；Judge额外对整表只引用compute.arguments的limit/descending/sort_by，缺具体业务值，触发AvailabilityIsNotWorldFact |
| r05 | “本次查询窗口内没有其他可用的真实新闻资料”被判外部新闻完整性未知，另将“仅检索到1条”一并判未知；未执行工具注入指令，不能把review等同于注入成功 |
| r09（新增） | 名单及交易建议拒绝正确；口径world_fact只引用工具arguments，来源维度另生成目录中不存在的ref_id，分别保留引用类型与UnknownEvidenceReference错误 |

s13、s18、r02、r03、r10均通过；r02/r03/r10以partial正常answered收尾，没有触发运行时fallback。确定性交付84/84、证据77/77、结构化事实60/60、工具/状态84/84、最终语义要求27/27均通过；可见事实79 pass/5 review，来源83/1，安全84/84。上述分母是实际参与该维度检查的回合。

84回合的状态为complete73、empty2、clarify3、partial4、refuse2；stop_reason为answered83、input_policy1，均为生产v34。没有provider失败、react_provider_error或fallback trace。原生观察器记录470次，比472次逻辑调用少2次，来自m16/m17的记忆准备generate_function_call，已纳入usage而非漏记费用。


## 独立校准：保留退化

仍为 v8 的61题、266判断槽、12个预期未知；输入及全部标签与v18完全一致。基础120次请求，显式预算181（允许每题最多一次缺维度补审）。这不是全量人工标注，`human_calibrated=false`，同一模型担任Agent与Judge仍有相关偏差。

| 指标 | v18 | v19 |
| --- | ---: | ---: |
| match / mismatch / review | 55 / 2 / 4 | 53 / 4 / 4 |
| 有效判项匹配 | 261 / 266 | 258 / 266 |
| 预期未知正确匹配 | 10 / 12 | 9 / 12 |
| Judge 调用 / provider失败 | 120 / 0 | 120 / 0 |
| tokens | 809,672 | 942,295 |
| 协议影响 / 补审调用 | 0 / 0 | 0 / 0 |

四项旧match退化：`tool_label_claimed_as_supplier`的品牌来源括号被final_delivery误放行，visible.source仍正确拒绝；`failure_specific_cause_contradicted`把额外错误原因扩入仅要求披露查询失败的交付标准；`failure_or_date_only`将未使用旧日结果的限定判为证据不足；`absence_claim_partial_source`把partial空集合用于反证存在性，被门禁挡回null，但这不是有效显式unknown，所以仍review。旧wrong_date/wrong_entity的“其他日期/实体未知却被判假”继续保留。

两项旧review恢复match：`source_lineage_shared`、`query_independence_inferred_upstream`。本轮仍有3条维度引用校验错误；数据向量碰巧等于预期不掩盖非法引用。没有选择性重跑、改标签或手工覆写原判。完整原句与证据保存在校准目录 `calibration-regressions-v18.json`。

## 验证、版本和成本

新增122项测试：21项真实HTTP协议形状、75项引用与计算链边界、24项上下文/版本绑定/重放完整性、2项补审提示指纹参数化回归。完整离线验收为后端**2,173 passed及22 subtests**、前端**94/94**和构建通过；最后5项计算来源边界增加后，全部**923项Golden相关测试通过**，其余代码未变。两次验收日志分别位于 `output/validation/20261012T060437Z-6aa82c7c/` 和 `output/validation/judge-v19-final-golden.xml`。保留既有依赖弃用与bundle体积提示。

源码/既有测试/BadCase提交 `ec4af55` 共800行增删；新增测试提交 `54417de` 共690行，均低于1000行。三批真实运行冻结于 `54417deb54ef9beea5c06547154da280edc50f93`，使用 `deepseek-v4-flash` / `LangChainChatProvider`，SDK重试为0。

| 新增运行 | Agent 调用 | Judge 调用 | tokens |
| --- | ---: | ---: | ---: |
| 固定旧答案重评 | 0 | 111 | 2,564,242 |
| 新完整 Golden | 361 | 111 | 5,392,130 |
| 固定 v8 校准 | 0 | 120 | 942,295 |
| 合计 | **361** | **342** | **8,898,667** |

合计703次逻辑调用，provider失败0，usage完整。完整轮Agent为2,817,421 tokens、Judge为2,574,709；旧完整轮合计4,687,714，新轮增加704,416（约15.03%）。三批都没有实际补审，不能由它们估计补审触发时的额外成本或成功率。

固定重评Judge tokens增加712,263（约38.46%）。对同一组84份v19五根证据快照，目录不含／含用途注释的序列化总字节数分别为1,861,404和3,543,506；含注释的单份最大141,453 bytes，低于单份2MiB硬限。这是同快照的注释开销，不是历史v18与v19目录大小对照，测量结果保存在`output/diagnostics/judge-v19-catalog-byte-comparison-20261012.json`。更明确的引用目录有输入成本，不能宣称本轮降本。调用账本不重复计算重评内保留的历史Agent费用。

| 指纹 | 值 |
| --- | --- |
| 生产代码（前后相同） | `9b70f9008458777722592aed7521592e4775e7ce5082450bb2baecd4f3aac268` |
| 三批 Judge 提示（含补审协议） | `f580c2c0d6f2c57133484c59cd820e09322a7b23bf16130553413e0e4303016c` |
| 新 evaluator | `7fe262e90c11d7334657a6b078f06ef03261d4c13eaae22b38d062d21edc5541` |
| 新 code hash | `771a06703e1aa05eef0a6c156cc3c8931048dcb23e397e7cd72494cb167e7d4a` |
| 配置（前后相同） | `261c3ca4f40e3bb8143a57046ccd791f4c17af4687bc5fb55fba39b7ebe90802` |
| grading.py（前后相同） | `3d826b6668d1f593db37338bedee0ef73a2116fa41bd3c5e26a3a3c6fdb95a0c` |
| 原 full-v18 报告 | `5129b1d9bdd3f9a17d076f02c71dd4dc635f25ed6c4c6dcc218a59aafee8c758` |
| 固定原输入集合 | `a4e29c69c2f4a6c48a241e4a970af425cbc8e0552139f6f1ba139494e2d65b8d` |
| 派生上下文集合 | `165a2c67238d67f323f79229fc28994d999e063335dde828642350bf5db2ab55` |
| 新 replay 报告 | `75a55766e7a7f7ffc55da109b81c75e93d77c62985ede4db240ef054b4c52dbe` |
| 新 calibration 报告 | `f0eec980b327394bb2c2a56ebc5d8c0e6be8fcddf52efcb2bffe785d83aac8eb` |
| 新 full 报告 | `7ad080c09d44435ef54ea00ad35794eeeae7f4c9bbac9ac470d7663ac7bcf064` |

## 原件与复现

原件保留于忽略目录，不提交数据库、生成报告、协议原始参数或真实密钥：

- `output/golden/replay-v34-judge-v19-20261012/`：旧答案快照、派生证据、重评、逐项对比。
- `output/golden/full-v34-judge-v19-20261012/`：新Agent完整评测、轨迹、usage和比较。
- `output/golden/judge-v19-calibration-20261012/`：未改标签校准、usage、退化原句。
- `output/golden/judge-v19-comparison-20261012.json`：三组联合机械对比。
- `output/diagnostics/judge-v19-fixed-claim-recheck-20261012.json`：零模型调用的旧断言类型复核。
- `output/diagnostics/judge-v19-replay-live-20261012/`、`judge-v19-full-live-20261012/`：运行计划、失败协议观察与完成指纹。

```powershell
backend/.venv/Scripts/python.exe backend/scripts/replay_agent_golden_judge.py --input output/golden/full-v34-judge-v18-20261012/report.json
backend/.venv/Scripts/python.exe backend/scripts/replay_agent_golden_judge.py --mode live --input output/golden/full-v34-judge-v18-20261012/report.json --output output/golden/<新的重评目录> --max-model-calls 220
backend/.venv/Scripts/python.exe backend/scripts/run_judge_calibration.py --mode live --trials 1 --max-model-calls 181 --output output/golden/<新的诊断目录>/report.json
backend/.venv/Scripts/python.exe backend/scripts/run_agent_golden.py --mode live --judge model --profile v1_close_review --trials 1 --max-model-calls 1000 --output output/golden/<新的全量目录>
```

本轮完成了可追溯的工程修复与三组验证，未完成全量人工签核。后续应优先处理交付要求与额外断言的审计隔离、来源/查询方法的target分类，以及真实未知与已证伪的区别；不能仅靠提高自动通过率判定完成。一次合成评测不证明模型重复运行稳定、真实行情准确或生产部署质量。
