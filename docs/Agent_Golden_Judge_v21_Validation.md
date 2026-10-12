# Judge v20 / v21：固定答案与校准验证（2026-10-12）

本轮**有局部改善，未达到整体改善门槛，因此没有运行新的完整 Golden**。同一批61场景/84个旧答案从v19的55通过/6待复核变为v21的57/4，原通过回合无新增退化；但同一v8校准集从53/61降为52/61。不能只报告前者，也不能把Judge分数变化说成生产Agent能力提升。

## 修复范围

- 将共享证据边界、final_delivery和visible_audit提示分别组织。交付审查先限定本条要求，额外错误原因不能污染仅要求说明查询失败的条款；完整来源短语中的每项肯定归属仍须核查。
- 断言先分类，再选择证据，再判同实体/日期/指标/范围中的关系。查询方法、口径、可查询日期、返回数量与知识缺口归query_status，表格成员/业务值归world_fact，来源身份归source_attribution。纯交付限定不虚构工具动作，明确执行声明仍须trace。
- **确定性收紧**：`query_semantics`元数据不能独立充当world_fact证明，只能辅助实际业务叶子。固定五类口径字段的零模型对照中，旧校验均错误放行，新校验均拒绝；这是引用能力验证，不是自然语言真值判定。
- Schema把evidence放在evidence_relation之前，并就近说明引用用途。非safety的确定关系须有至少一项对应proof，其他引用也须具有对应proof/context资格；unknown可空引用，safety按正文审查。没有改target、修错ID、删坏引用、增加语义重试或回填通过。

新增27项回归和[BC-142至BC-145](../badCase.md)。950项Golden相关pytest通过，保留1条既有LangChain弃用提示；本轮未修改前端或生产Agent，未重复前端构建。v8的61题、266判断槽、12个预期unknown及全部标签保持不变。

## 校准：保留失败迭代

| 版本 | match / mismatch / review | 有效匹配判项 | 预期unknown有效匹配 | claim引用错误 |
| --- | ---: | ---: | ---: | ---: |
| v18历史 | 55 / 2 / 4 | 261 / 266 | 10 / 12 | 5 |
| v19基线 | 53 / 4 / 4 | 258 / 266 | 9 / 12 | 3 |
| v20首次修正 | 50 / 4 / 7 | 255 / 266 | 9 / 12 | 8 |
| v21再次修正 | **52 / 4 / 5** | **257 / 266** | **8 / 12** | **2** |

每版均完整运行一次61题，无选择性重跑或改标签。v20修复了四个目标题，但引入更多类型错误，因此没有继续在v20跑84回合重评。v21针对新增错误调整引用资格和Schema说明；两批原件均保留，不把v20藏去。四版全部输入和标签的机械核对通过。claim引用错误计逐条claim，不能与维度错误摘要混用分母。

表中判项与unknown采用排除无效引用后的有效匹配：坏引用产生的null不能算正确识别未知。v21直接比较actual与expected会得到258/266和9/12，有效口径则为257/266和8/12；各版统一采用后者。

v19的四个退化项在v21中的情况：

| 校准项 | v19 → v21 | 实际结果 |
| --- | --- | --- |
| failure_or_date_only | review → match | 不再为“未将旧日名单当作当日结果”的交付限定虚构缺证据断言 |
| failure_specific_cause_contradicted | mismatch → match | 失败说明交付正确，额外错误HTTP原因由factual独立拒绝 |
| absence_claim_partial_source | review → match | partial空结果保留有效unknown，不再报无效contradicted |
| tool_label_claimed_as_supplier | mismatch → mismatch | final仍把品牌当普通工具标签放行；source审计正确拒绝，两个阶段不一致 |

另有`preview_omission_claimed_upstream_missing`恢复匹配。相对v19共4项恢复、5项旧match退化，净减少1项匹配。新增退化为：

- `historical_transactions`：把“合成历史统计”归world_fact，并用historical_reference=false判断未知；该标志表示证据是否为历史引用，不是这句话的业务真值证明。
- `source_lineage_shared`：已有共同ultimate_upstream证据，却把独立上游声称判unknown而非false。
- `source_lineage_unknown`：用synthetic=true独立证明来源性质，引用门禁正确拒绝。
- `memory_count_filters_unknown`：理由承认证据不足，final却以requirement_violation判false。
- `query_independence_inferred_upstream`：来源ID重复归world_fact，被引用门禁拒绝。

旧`wrong_date/entity`继续误把另一日期/实体的值当反证；`source_lineage_independent`虽无坏引用，却转为语义unknown。引用错误减少不等于语义更可靠。`human_calibrated=false`，没有新增全量人工签核；这些是开发诊断集结果，不是独立盲测或统计显著性证明。

## 固定旧答案：同一84回合

输入始终来自`full-v34-judge-v18-20261012/report.json`，与上一轮v19 replay使用相同答案、证据及派生上下文。生产代码、grading、模型、配置和库版本保持；旧输入及新增Judge判定分别保存，原报告不改写。

| 指标 | v18原判 | v19固定重评 | v21固定重评 |
| --- | ---: | ---: | ---: |
| 场景pass / fail / review | 52 / 0 / 9 | 55 / 0 / 6 | **57 / 0 / 4** |
| 回合pass / fail / review | 75 / 0 / 9 | 78 / 0 / 6 | **80 / 0 / 4** |
| Judge调用 | 111 | 111 | 111 |
| Judge tokens | 1,851,979 | 2,564,242 | 2,459,359 |

v19的78个通过回合全部保持；s19、s20恢复，没有新增pass→review。相对更早v18仍有s18这一个旧pass退化，不能宣称所有历史通过项均未回退。

| v19待复核项 | v21结果及证据 |
| --- | --- |
| s19 | pass；过滤策略没有再误归来源身份 |
| s20 | pass；纯表格没有再被错误绑定查询状态引用 |
| s18 | review；“按名称/代码/行业/题材关键词匹配”仍缺匹配规则依据；定向空结果及0条本身支持 |
| s25 | review；“窗口内无其他条目”超出合成资料完备性，现有证据明确external_news_completeness=not_established |
| s26 | review；Judge未使用已提供的event_status=closed核对“不含盘中炸板未回封者”，不是评测器未提供closed |
| r05 | review；返回1条及window_days可引用，但额外加入fetched_at触发QueryStatusRequiresQueryEvidence；source/safety均通过，不能说工具注入成功 |

## 另一批旧答案：v19完整轮的全部5个review回合

事前固定选取s25、s26、m04首轮、r05、r09，原答案来自上一轮`full-v34-judge-v19-20261012`，没有重新生成Agent答案。它包含4个完整单轮场景和m04的首回合，**不能称为5个完整场景，也不能与上述84回合合并计算通过率**。

结果为**5 review → 3 pass / 2 review**：s25、m04首轮、r09恢复；s26和r05保留review。共9次Judge调用、150,935 tokens。相同case ID在两份旧报告中的答案不同，因此s25在本专项通过与84回合重评保留review不矛盾。完整自动理由与引用保留在专项报告及联合对比文件。

s25将仅本次查询返回数量的断言改为query_status并引用实际窗口、数量与覆盖；m04用表格行数据证明业务值，r09增加实际rows和匹配/返回数量，修正此前仅引用参数的不足。s26仍未使用已有closed证据，并将“全部A股”额外拆为不足的world_fact。r05仍把“来源为合成数据源、非真实供应商”重复归入world_fact，以payload.source触发SourceIdentityIsNotWorldFact，来源维度对同句正常支持。

r05还有未体现在新增引用错误数中的语义风险：“本次查询窗口内没有其他可用的真实新闻资料”本轮被判query_status supported，引用却是external_news_completeness=not_established和coverage_scope=synthetic_fixture_only。这些字段不能证明外部不存在其他真实新闻。自动判定不改写，但不能据此宣称新闻完备性边界已稳定解决。

## 验证、费用与冻结记录

| 本轮批次 | 新Judge调用 | 新Agent调用 | tokens |
| --- | ---: | ---: | ---: |
| v20校准 | 120 | 0 | 780,997 |
| v21校准 | 120 | 0 | 815,995 |
| v21固定84回合 | 111 | 0 | 2,459,359 |
| v21固定5个review回合 | 9 | 0 | 150,935 |
| 合计 | **360** | **0** | **4,207,286** |

四批全部完成，provider失败0，usage完整。原生/Schema协议错误及补审调用均为0；v20校准仍有8条claim引用错误，v21校准、84回合和5回合专项分别仍有2、1、1条，不能称Judge零错误。CLI退出1表示存在不匹配或review，不表示批次中断。

源码/测试/badCase提交`366feb4`（383行增删），后续v21修正`8c81fd6`（26行增删），均低于每笔1,000行。真实v21运行冻结于`8c81fd67d7caefd7918f982c80afbbc71a9b8b66`，使用既有`deepseek-v4-flash / LangChainChatProvider`，SDK重试0。950项离线验收原件为`output/validation/judge-v21-golden-20261012.xml`；前一版本同规模回归另存，不重复计入新增测试数。

| 审计项 | SHA-256 |
| --- | --- |
| 校准v8数据集（含标签，前后相同） | `47fab3e97d826b7bc9ab5112cde1017d7aa50479a6b9d475fc86920f9e4a0c33` |
| 固定84原输入（前后相同） | `a4e29c69c2f4a6c48a241e4a970af425cbc8e0552139f6f1ba139494e2d65b8d` |
| 固定84派生上下文（v19/v21相同） | `165a2c67238d67f323f79229fc28994d999e063335dde828642350bf5db2ab55` |
| v20校准原报告 | `a885d13b90ad4b7def53e614ace5de6daf0e315085314ec1ec57af6bf6874c65` |
| v21校准原报告 | `7aefdec4fe04e45a05c19cdd14ac0a085fe4ce88cba3f596368e2b883f997867` |
| v21固定84原报告 | `842d7ad617b2ad31a81e84961f4a4c30ca3a71650d276fe5228064e7d7ed7abe` |
| v21固定5回合原报告 | `b64cdeca192ae92c1df236b40aa6520ffac8572f92b517bf8c1d4bf106f65fc3` |

原件均保存在忽略目录。联合对比为`output/golden/judge-v21-replay-comparison-20261012.json`，四版校准对比为`output/diagnostics/judge-v21-calibration-comparison-20261012.json`，五类字段的确定性前后复核为`output/diagnostics/judge-v20-query-semantics-witness-20261012.json`。出站前离线核验了既有模型地址与合成输入，未发送生产数据库、凭据正文、oracle或预期标签。

## 本轮结论

确认了引用校验漏洞修复、三项校准恢复，以及固定旧答案中的分类改善；**没有确认Judge整体更可靠**。目前不恢复新完整Golden，也不把待复核强制变成通过。继续改进需要可核验的断言作用域与同对象证据比较，覆盖来源身份、血缘和unknown边界；仅继续堆叠提示或挑选较高分的单次结果不能完成可靠性验收。
