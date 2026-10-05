# Golden v1.6：协议诊断、模拟环境与答案契约

日期：2026-10-05。范围为评测审查计划的第1、2项。主套件为 `agent-golden-v1.6`，裁判为 `golden-judge-v13`，诊断集为 `golden-judge-calibration-v6`。本轮没有调用真实模型，未建立新的 Agent 通过率。

## 原始制品状态

用户确认 v1.5 原始报告暂时无法获取。两份 v12 裁判报告、协议探针及审计文件仍是待补材料；不能从摘要重建原始响应，也不能据新诊断反推历史异常原因。

新增只读核验入口：

```powershell
backend/.venv/Scripts/python.exe backend/scripts/audit_golden_artifacts.py
```

它用已发表的 SHA-256 核验两份 v1.4 Agent 报告和两份 v12 裁判报告，区分 verified、missing、mismatch、unreadable。当前为 2 verified、2 missing，退出码1表示原件尚未全部核验。不会创建缺失报告、覆盖文件或修改成绩。原件取得后放入原 `output/golden/` 路径再核验，也可使用 `--root` 指定只读归档目录。报告与会话库继续不进入 Git。

## 协议诊断

模型适配器的 native function calling 异常新增白名单结构信息：完成原因、响应类型、有效与无效调用数、ID 是否缺失或重复、参数类型/长度、JSON 失败类别。错误消息和执行控制保持原有行为，不增加重试。

裁判两个阶段分别保存 `judge_diagnostics`；诊断报告使用 `phase_diagnostics`。Schema 错误仅保留合法字段路径和内置错误类型，额外字段名转换为未知标记；不保存模型文本、原始参数、调用ID、异常正文或配置。

报告分别统计整个阶段失败、维度/判项错误，以及受协议错误影响的调用数。一次可见审查的三个维度同时 Schema 校验失败，只计一次受影响调用。引用依据无效、预算拒绝和网络异常不混入协议错误；旧报告没有调用分母时异常率为未知。异常仍保留 review，确定性失败不被覆盖。

这些信息帮助下一次发生异常时定位结构原因；历史 `NativeFunctionCallingError` 未重现，根因尚未确认。

## 模拟世界和实际字段语义

- 新增 `market_index_trend`，支持指数日期窗口、涨跌天数、区间收益和回撤，覆盖空、失败、部分、截断及过期变体。参数使用生产 Schema，指标与生产归一化代码在相同合成数据上对照。
- 评级 `filtered_out` 如实记录同日事件未满足合成资格的原因；`symbols` 仅缩小候选返回范围，不混为资格排除。合成资格明确为收盘封住、首板及配置了合成分数，不代表完整生产评分策略。
- 执行器从 Agent 实际收到的 `EvidenceStore.view` 采集白名单 `count_scope`、返回候选数、证据ID及消息哈希。两个裁判阶段都能读取这些字段，避免遗漏 Agent 已知的口径。该信息不能证明额外 ST/新股过滤或上游独立性。
- K 线使用共用的合成历史序列，历史查询交叠日期的价格一致。华岳科技10日收益在9月18/21/22日分别为0.8/1.0/1.2%，星河软件9月22日为-2.4%，北辰制造为3.6%；均为人工合成数值。均线、收益、趋势、回撤精度与生产语义对照。
- 默认 profile 的24个工具覆盖11个；扩展 profile 的26个工具覆盖13个。尚未实现的工具继续报告环境缺口；禁止使用真实服务补齐。

## 答案契约与诊断集

`s18` 显式检查只回答有无评级记录，额外真实名单仍违反范围；`m17` 的正确计数与额外过滤/来源声明分别检查：未作声明允许通过，缺依据待复核，有明确反证判失败。该变更属于评测补强，不能视为生产回答行为已经修复。

新增 `s31_scalar_historical_return`，验证同一股票的历史指标没有被最新值替代；`m11/m14` 采用另一股票的不同数值。主套件共61场景、84回合，开发/保留分组为48/13，保留集仍已曝光。

原49条裁判诊断的输入与标签冻结，新增6条范围/过滤正负对照，共55条、242判项。完整一轮需要108次逻辑请求，默认和最大预算120。当前仅验证了题目、标签传递与失败/未知传播；尚未验证模型对新增对照的实际判定。

```powershell
backend/.venv/Scripts/python.exe backend/scripts/run_agent_golden.py --mode validate --profile v1_close_review
backend/.venv/Scripts/python.exe backend/scripts/run_agent_golden.py --mode validate --profile extended
backend/.venv/Scripts/python.exe backend/scripts/run_judge_calibration.py --mode validate
```

profile进入恢复与比较的manifest，两个profile须使用不同结果目录。固定世界、主契约和裁判输入变化使旧成绩无法直接配对；下一阶段应完成真实模型校准、独立人工复核，再冻结新基线。

## 本轮验证

2026-10-05，全部 `test_golden_*.py` 与 `test_langchain_provider.py`、`test_llm_provider.py` 共507项离线测试通过；仅有依赖包 LangChain 的待弃用提示。JUnit 记录位于本地 `output/validation/golden-v16-854f2ad7ef744c528f62313469f93db8/junit.xml`，不进入 Git。

两个 profile 的主套件校验和裁判诊断集校验均通过；原件核验为2份匹配、2份缺失。检查范围包含协议异常脱敏、失败传播、真实模型输入的元数据采集、profile 隔离、恢复指纹、合成指标与生产计算一致性及原49条诊断冻结，没有验证真实模型准确率。
