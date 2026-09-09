# LimitUpLab 评测系统设计

> 本文件是 Claude 对 LimitUpLab 评测系统的设计建议，与 `claudeAdvice.md`（项目批判）、
> `interviewPre.md`（面试模拟）并列。定位：基于**当前真实 eval 代码**给出可落地的演进方案，
> 不悬空、不重写，建立在已有的四层 harness 上。
>
> 评审基线：`main` HEAD = `d98e58d`。读完 `eval_runner.py`、`capability_contract.py`、
> `tool_policy.py`、`run_agent_eval.py`、4 个 fixture 后写成。

---

## 0. 先纠正一个普遍误判

很多人（包括我自己在 `interviewPre.md` N99 的早期说法）会一句话概括：
"这个项目用 mock 跑 eval，所以 eval 不证明 LLM 能用。"

读完代码后这个说法**不精确**，需要纠正：

- 契约层 `run_query_contract_eval_suite`：确实无 LLM，确定性，这是对的。
- 路由层 `run_agent_planner_eval_suite`：**传真 LLM**，重复 N trial，测 capability + 工具选择，不执行工具。
- 多轮路由层 `run_agent_conversation_planner_eval_suite`：**传真 LLM**，测上下文路由稳定性。
- 端到端层 `run_agent_product_eval_suite`：默认 `OfflineEvalLLMProvider`（raise）+ `force_template_answer=True`，
  但**签名允许传真 provider**（`run_agent_eval.py:164` 的 `force_template_answer=not args.live_answer`）。

所以不是"没传真 LLM"，是"传真 LLM 的那两层只测路由、不测生成；测生成的那层默认走模板"。
README 的"11/11 离线 eval 通过"通过的是**模板路径**，不是真 LLM 生成质量——这一句仍然成立。
但 harness 的能力比"用 mock"强得多，问题在默认配置和判分方式，不在骨架。

---

## 1. 现有四层：哪些做对了（值得保留）

这是整个设计的前提——下面所有建议都是**增量**，不是重写。

### 1.1 三层工具分解（最聪明的一笔）

`AgentPlannerEvalSuiteResult` 同时报三个率：

- `raw_planner_tool_success_rate`：LLM 原始选对没
- `effective_tool_success_rate`：normalize + capability 补齐后对没
- `backend_repair_rate`：policy 兜底了几次

这能侦测出**最危险的反模式**：LLM 路由在退化，但 backend repair 把它兜住了，
表面绿。盯 raw 率下降就能发现。大多数项目的 eval 只有"最终对不对"一个率，
看不出"是 LLM 选对了还是后端兜住的"。这一笔必须保留，且应该作为头号监控指标。

### 1.2 trial 重复 + stability 签名

`stable = 全 trial 通过 且 (capabilities, tools) 签名唯一`。
对随机 LLM，单次 pass 没意义，重复才看得出抖动。这是正确的。

### 1.3 EvalObservedLLMProvider 包装器

`eval_runner.py:58` 的包装器计数 provider 调用/成功/错误。这让 fallback
不能把上游失败藏起来——provider 挂了，即使模板路径绿，`provider_errors` 也会暴露。
这是可观测性的正确设计。

### 1.4 契约层的纯确定性

`run_query_contract_eval_suite` 无 LLM 无网络，只测 `build_limit_up_query_contract`
的日期/板块/板高解析。这是对的——解析逻辑该有确定性回归，不该混进 LLM 评测。

### 1.5 多轮路由的 history seed

`AgentConversationEvalScenario` 用 `history` seed + 后续 `turns`，
`_conversation_turn_messages` 把上轮 assistant 回答塞进下轮 history。
测的是"第 3 轮能不能记住第 1 轮的股票"。这是多轮 agent 的核心风险点，测对了。

---

## 2. 核心诊断：顶层正好测在 LLM 风险最高的地方之外

把第 1 节的"做对了"和"测什么"放一起看，矛盾就出来了：

| 层 | 跑真 LLM? | 测生成质量? |
|---|---|---|
| 契约 | 否 | 否（纯解析） |
| 路由 | **是** | **否**（不生成回答） |
| 多轮路由 | **是** | **否**（不生成回答） |
| 端到端 | 默认**否**（可开） | **是**（但默认走模板） |

矩阵里"是+是"那一格是空的：**传真 LLM + 测生成**。
而 LLM 最大的风险——生成回答时的事实性、幻觉、答非所问——恰好只发生在那一格。

具体后果：

1. README "11/11 通过"通过的是模板路径。模板是确定性的，过不过基本取决于
   `looks_like_*` 路由对不对 + 模板拼对没，和"真 LLM 答得好不好"无关。
2. 你最有 LLM 风险的环节，**没有任何一层在默认配置下测它**。
3. `run_agent_eval.py` 已经有 `--live-answer` 能开传真 LLM 生成，
   但即使开了，判分还是 `_check_product_turn` 的 substring 匹配——
   substring 测不出幻觉、答非所问、事实不可追溯。

所以问题分两半：
- **a) 默认配置**：端到端层该传真 LLM 但没默认传真。
- **b) 判分方式**：即使传真，substring 判分测不对生成质量。

这两半是下面 P0/P1 要解决的。

---

## 3. 第二个缺口：量化侧完全没有回归闸

注意：第 1 节四层**全是 Agent 的**。项目的另一半——14 因子评分、
`first_board_discovery._recall_score`、`auction_final_recommendations.score_auction_fact`、
`recommendation_intelligence` 的调整分——**这些 magic numbers 在真实数据上的排序质量，
没有任何回归评测**。

现状：
- 单测种的是合成单调信号（`test_diagnostic_detects_planted_signal`）。
- `factor_signal_diagnostic` 是**诊断**（证伪工具），不是**回归闸**（改动不能退化）。
- `prediction_quality_audit` 算的是 Top10 vs baseline，但没接进 CI 闸门，改动评分可以静默退化。

类比：Agent 侧有四层 eval，量化侧一层都没有。这是个不对称。

---

## 4. 建议设计（按 ROI 排序）

### P0 — 补上唯一缺的那一格：传真 LLM + LLM-as-judge（端到端层）

**目标**：填上 §2 矩阵里"传真 LLM + 测生成"的空格。

**关键发现**：技术上这不是"建新层"。`run_agent_eval.py` 已有 `--live-answer` flag，
开了就是传真 provider + `force_template_answer=False`。所以 P0 = a) 默认开 + b) 换判分器。

**判分器：LLM-as-judge，评 3 个 substring 测不出的维度**

复用现有 `OpenAIChatCompletionsProvider.generate`（`llm_provider.py:161`）作 judge。
对每个 product turn，把 `response.answer` + `response.tool_results`（工具输出）+ 用户问题喂给 judge，
判：

1. **grounding**：答案里每个数字/实体声明，是否可追溯到某个工具的 output？
   - 给 judge 的 prompt 里附上 `tool_results` 的 JSON 摘要，让它逐条核验。
   - 这是 substring 永远测不出来的——"次日平均 −0.86%"这种数字 substring 能对上但来源可能错。
2. **relevance**：回答是否答了用户问的问题，而非答非所问 / 泛泛而谈？
   - 这是 `intent` 误判的下游症状：intent 错了，回答方向就错，但 substring 可能碰巧对上。
3. **hallucination**：答案里有没有工具输出中不存在的具体断言（虚假股票名、虚假日期、虚假涨跌幅）？

judge 输出结构化：`{grounding: pass|fail, relevance: ..., hallucination: ..., reasons: [...]}`。
和现有 `_check_product_turn` 的 7 维度**并列**，不替换——substring 留给安全词（`PRODUCT_SAFETY_TERMS`），
那里精确匹配是恰当的；新增的 3 维度由 judge 管。

**质量闸**：judge 维度的 pass rate 低于阈值 → 退出非零。
不像 CI 那样每次跑（成本），按发布节奏跑，存 `data/agent_eval_judge_latest.json`。

**复用 `EvalObservedLLMProvider`**：judge 层也包一层，这样 provider 退化时
即使模板路径绿，`provider_success_count` 也会掉，暴露真问题。

**风险与对冲**：
- judge 自己也会错判 → 用和被测**不同**的 model 当 judge（用 deepseek-v4 生成，用更强或不同的 model 判），
  或至少用不同 prompt；并对 judge 结果抽样人工审，建立 judge 的 ground truth。
- 成本 → 不是 CI 必跑，发布前跑 + 每周回归。

### P1 — 把 substring golden 换成 grounding 校验（端到端层判分重构）

**目标**：干掉 `_normalize_answer_text` 那堆 hack，让判分不再和 LLM 表述方差搏斗。

现状：`_check_product_turn` 的 `fact_completeness` = "答案里有没有出现 `answer_contains` 的子串"。
所以你才需要 `_normalize_answer_text`（`09:→9:`、`三连板→3板`、全角→半角）——
这本身是在和 LLM 表述搏斗，且**随 LLM 换措辞就脆**。

**改法**：
- `fact_completeness` 改成 grounding 校验：从答案里抽出数字/实体声明，
  校验每个都出现在 `response.tool_results` 的 output 里。**grounding = 每条声明可追溯到工具输出**。
- substring 匹配只保留给 `PRODUCT_SAFETY_TERMS`（安全词）和 `PRODUCT_INTERNAL_TERMS`（内部词泄露），
  那两类精确匹配是对的。
- `expected_tool_matched_counts` 这类**会随数据回填变脆**的 golden 期望，从 fixture 里移除或改为范围。

**这个改动和 P0 的 grounding 维度重叠**——P0 的 judge 也评 grounding。
区别：P1 是确定性 grounding（程序抽声明 + 程序比对），P0 是 LLM grounding（judge 判）。
建议：先做 P1 的确定性 grounding（便宜、可入 CI），P0 的 LLM grounding 作为更深的复核层。

### P2 — 给量化侧补回归闸（填补 §3 的空白）

**目标**：评分改动不能静默退化。这是现在完全空白的一块。

**三个组件**：

1. **诊断回归**：把 `scripts/run_factor_signal_diagnostic.py` 接进回归闸。
   现在它是手动 CLI，结果存 `data/factor_signal_diagnostic_latest.json`。
   改成：每次评分相关改动，跑诊断，对比上次的 `verdict_status` 和关键指标
   （`blocked_oos_r2`、`strongest_factor` 的 IC 方向）。退化超阈值 → 非零退出。
   注意：诊断结果随 outcome 数据增长会变，所以闸门比的是"同数据快照下"的前后，不是绝对值。

2. **Top10 排序稳定性**：新增一个"评分改动前后，同一批历史日期的 Top10 排序有多大变化"的回归。
   `prediction_quality_audit` 已有 Top10 vs baseline 的计算，接进来。
   排序大变不一定是退化（可能是改进），但**需要被看见**——所以闸门不是"不许变"，
   是"变了就要人工 review"，输出 diff 报告。

3. **discovery 量价验证**：这是 `claudeAdvice.md` 第 2 次评审 P1.3 的建议——
   给 `first_board_discovery._recall_score` 跑一次和 14 因子同款的日期阻断验证
   （Top10 vs "次日是否首板"），建立基线。这本身不是回归闸，但有了基线之后，
   discovery 改动才能有回归对象。

**和 Agent eval 的关系**：这是独立系统，不共用 harness。
Agent eval 测"理解 + 路由 + 生成"，量化闸测"排序质量"。两条线，现在只建了前者。

### P3 — 让评测集是活的（打破自己出题自己答）

**目标**：消除路由准确率在 fixture 上系统性偏高的乐观偏差。

现状：`looks_like_*` 关键词就是对着 `agent_eval_cases.json` / `agent_paraphrase_eval_cases.json`
这批 fixture 调的。fixture 是开发时手写的固定集合，再没动过。
**自己出题自己答，路由准确率必然偏高**——你的真实用户问法不在 fixture 里。

**改法**：
- 加一个真用户问题 → 人工 triage → 进 fixture 的回流。哪怕每周审 N 条。
- 来源：生产 SSE 日志里捞 user message（脱敏）。`agent_run` 表已存 `input_json`，可查。
- triage 标准：每条标注 expected capabilities + 是否路由正确 + 是否需要新 skill。
- 这和 `claudeAdvice.md` N86 的建议同源——评测集要有线上回流，否则泛化缺口看不见。

### P4 — stability 从二元签名升级为"翻转分类"

**目标**：减少噪音，区分"不稳定但安全"和"不稳定且坏"。

现状：`stable = 全 trial 通过 且 签名唯一`。
问题：LLM 在两个**都正确**的 capability 间翻转（比如 `market_index_trend` 和 `market_environment`
都能答"今天大盘怎么样"），按现规则判 unstable，但其实无害，反而浪费 review 注意力。

**改法**：记录翻转的目标，分类：
- 翻转到**也可接受**的 capability → `unstable_safe`，不告警。
- 翻转到**错误/空** → `unstable_bad`，告警。
- 区分后，`unstable_cases` 这个数才有行动价值。

---

## 5. 落地顺序与依赖

```
P1 (确定性 grounding，入 CI)   ← 最先，便宜、无 LLM 成本、立刻去 hack
   ↓
P0 (LLM-as-judge 层，发布前跑)  ← 依赖 P1 的 grounding 作为底层复核
   ↓
P2 (量化回归闸)                 ← 独立，可并行
   ↓
P3 (评测集回流)                 ← 持续，不阻塞
   ↓
P4 (stability 分类)             ← 小改，随时
```

P1 先做，因为它**无 LLM 成本、可入 CI、立刻干掉 `_normalize_answer_text` 的脆弱性**。
P0 依赖 P1（judge 的 grounding 维度和 P1 的确定性 grounding 互补）。
P2 独立于 Agent eval，可并行启动。

---

## 6. 不做什么（反过度设计）

- **不重写 harness**。四层骨架是对的，§1 列了 5 个做对的点。增量改判分器和配置，不推倒。
- **不上 LLM-as-judge 做唯一裁判**。judge 自己会错判，所以 P0 要配人工抽检 + 不同 model。
  把 judge 当"提醒哪些 turn 该看"，不当"自动判生死"。
- **不追求评测集覆盖所有问法**。P3 的回流是为了补泛化缺口，不是穷举。
  穷举做不到也没意义。
- **不给量化侧上和 Agent 一样重的四层**。量化侧只需一个回归闸（P2），
  不需要路由/多轮/judge 那套——它没有 LLM 路由问题。
- **不把诊断当回归闸用**。诊断是"当前有无信号"的证伪工具（`verdict=no_robust_signal`），
  P2.1 是把它的**输出变化**当闸门，不是把诊断结论本身当闸门。
  "无信号"不是退化，"同数据下 R² 突然掉"才是。

---

## 7. 一句话总结

你的 eval **分层和分解做得比大多数人好**——三层工具率、trial 稳定性、provider 可观测性
都是正确设计。问题不在没建，在于**顶层把真 LLM 生成质量这个最该测的东西让位给了确定性模板**，
且即使传真 LLM，判分还是 substring。P1 先用确定性 grounding 替掉 substring hack（便宜、入 CI），
P0 再叠一层 LLM-as-judge 复核生成质量（发布前跑）。另一条线 P2 给完全空白的量化侧补回归闸。
两件事都是增量，不重写。

---

## 附：与已有评审的交叉引用

| 本文件建议 | 对应 `claudeAdvice.md` | 对应 `interviewPre.md` |
|---|---|---|
| P0 LLM-as-judge | — | N99（eval 用 mock 还是真 LLM） |
| P1 grounding 替 substring | — | N78（工具失败→幻觉） |
| P2 量化回归闸 | 第2次评审 P1.3（discovery 量价验证）、§3.3（magic numbers 无验证） | B5（严谨证伪旧规则、草率写新规则） |
| P3 评测集回流 | 第2次评审 §3.1（言行张力） | N86（路由评测有无线上回流）、N100（真用户跑过吗） |
| P4 stability 分类 | — | N76（双路路由冲突） |

三份文档交叉印证：`claudeAdvice.md` 指出问题、`interviewPre.md` 把问题变成会被问的题、
`evalDesign.md` 给出怎么修。同一个判断从三个角度写，可信度比单写一次高。