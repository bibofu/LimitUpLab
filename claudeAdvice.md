# Claude 评审记录 — codex 的批判者

> 本文件是 Claude 对 LimitUpLab 项目的持续批判性评审记录。每次评审续写一节，
> 不覆盖历史，保留意见演变轨迹。定位：独立的第二意见，专门挑 codex 产出的毛病，
> 但坚持只说有依据的问题——基于真实代码与真实数据，不臆测、不夸大。
>
> 约定：未经明确指示不提交、不推送。建议项标注 ROI 与是否已动手。

---

## 第 1 次评审 — 2026-08-29

### 0. 评审基线

- 评审对象：`main` HEAD = `c5a2af9`。
- 自上次会话以来分支历史被外部（codex）改写，我的旧提交 `f2d9d49` 已不在分支上，
  但诊断代码被重写后重新合入并增强。
- 本次评审基于**当前真实代码**，不依赖记忆中的旧版本。
- 测试：诊断模块 12/12 通过；全套 243/244 通过（1 个 Windows 平台测试缺陷，见 §3.2）。
- 真实数据诊断实跑一次：`n=247, trade_dates=19, verdict=no_robust_signal`。

### 1. 自上次以来的演进（codex 的产出）

| 提交 | 内容 | 评价 |
|---|---|---|
| `cdf6821` → `8ff5ddc` | 完整账号鉴权加了又整体 revert（−2178 行），改走匿名会话路线 | ✅ 决策纪律好，没留半成品 |
| `0a402c5` / `a491797` / `c5a2af9` | 匿名访客身份（HMAC 签名 cookie）+ 内部 admin key 网关 + HTTP 下保留会话 | ✅ 实现质量高（见 §2.1） |
| `daa2504` | Agent 限流与用量核算（per-owner/per-IP/日预算/并发上限） | ✅ 公开 demo 必需 |
| `1e10f17` | SQLite 并发加固（WAL/busy_timeout 等，带并发测试） | ✅ |
| `6a4181e` | 生产数据库自动备份（cron + logrotate） | ⚠️ chmod 权限断言有平台问题（§3.2） |
| `acf076b` | finance CLI 用 Node 22 | ✅ 小修 |
| 诊断重写 | pooled Spearman → 逐日 IC + 符号翻转 + 按日留一 OOS + 日期阻断 bootstrap | ✅ 方法论真升级（§2.2） |

### 2. 做得对的（值得肯定）

#### 2.1 安全实现的工程纪律（`security.py`）

- HMAC 签名 cookie，`hmac.compare_digest` 常时比较，防时序侧信道。
- 生产强制 `LIMITUPLAB_SESSION_SECRET` ≥ 32 字节；`LIMITUPLAB_ADMIN_KEY` 同样要求且**不得与 session secret 相同**——这条很多项目会漏。
- 仅在 https/wss 下置 `secure`；cookie 仅对 `/api/agents/chat` 路径下发（`_VISITOR_COOKIE_PATH_PREFIX`），最小暴露面。
- `AnonymousVisitorMiddleware` 在 ASGI 层装 `owner_id`，路由里 `current_owner_id` 校验格式，双层防御。

这条生产链路的实现质量，是本次评审里最高的。

#### 2.2 诊断的方法论升级是真升级

我的原版：pooled Spearman + Bonferroni + Lasso。问题：把同日候选当独立样本 → 伪重复、p 值虚假乐观。

codex 重写为：
- **逐日横截面 IC**（每个交易日一个 Spearman）→ 统计推断单位是日期，不把股票数当独立时间样本。
- **符号翻转置换检验**（4096 次）→ 不依赖正态假设，对截面 IC 这种重尾分布更稳。
- **Bonferroni** 控 14 因子族错误率（α=0.05/14=0.00357）。
- **Lasso 按交易日留一 OOS R² + 日 IC 置换 p 值** 评估，而非看样本内拟合——避免过拟合读数。
- **日期阻断 bootstrap** 评估选择稳定性（70% 保留率阈值）。

caveats 也诚实：明说「未发现信号 ≠ 已证噪声」「Outcome 单一口径」「缺失 enrichment 截断为常数会压低相关」。

这是一次正确方向的方法论演进，不是表面功夫。

#### 2.3 真实数据上的诚实结论（两系统互证）

实跑诊断：`n=247, trade_dates=19, verdict=no_robust_signal`。

- 最强单因子「板块强度与接力」平均日 IC=−0.27，未校正 p=0.005，**仍未过 Bonferroni 0.0036**。
- 联合 Lasso 留一 OOS R²=0.034，日 IC p=0.42。
- 与 v3 audit 的「暂无信号」结论**两个独立系统再次一致** → 可信度高。

项目没有用「样本内 R²=0.108」自欺，而是公开报告留一 OOS R²=0.034，这是诚实的。

### 3. 仍存在的问题（按 ROI 排序）

#### 3.1 【真 bug，未修】eval_runner 的 `os.environ` 跨请求污染

- 位置：`backend/app/agents/eval_runner.py:239-254`
- 现象：`GET /api/agents/eval` 在 try/finally 里逐用例改写 `os.environ["LIMITUPLAB_FORCE_TEMPLATE_ANSWER"]`。
- 危害：单 uvicorn worker 下，并发真实 chat 请求可能在 eval 改写窗口内读到该 env，
  被强制走模板答案。finally 会恢复，但存在竞态窗口。这是一个**正确性 bug**，不是代码洁癖。
- 修法：改用函数参数传 `force_template_answer` 给 provider，或用 `contextvars.ContextVar`，
  彻底不碰进程级 `os.environ`。约几行改动。
- 状态：**未动手**（上次已标记为 critique #2，本轮仍在）。待用户指示。

#### 3.2 【测试红，平台缺陷】备份权限断言在 Windows 必挂

- 位置：`backend/tests/test_backup_database.py`，对应 `scripts/backup_database.py:38` 的 `backup_path.chmod(0o600)`。
- 现象：Windows 上 `chmod` 是 no-op，文件 mode 恒为 `33206`，断言 `st_mode & 0o777 == 0o600` 永不成立。
- 性质：**测试缺陷**，不是代码 bug（生产部署在 Linux，权限在那里生效）。
  但它让本地套件常驻一个红，掩盖新引入的真实失败。
- 修法：断言前 `skipUnless(os.name == "posix")`，或 POSIX 专属。
- 状态：**未动手**。待用户指示。

#### 3.3 【科学瓶颈】核心问题未被回答——但原因是数据，不是代码

- 诊断 `VERDICT_MIN_TRADE_DATES=10`，现 19 天已能下结论（不再 `insufficient_sample`），
  但 19 个 outcome-ready 日的统计功效仍低；优化器 60 天晋升闸更远未到。
- 瓶颈纯粹是**结果数据积累速度**，不是代码质量。
- 值得盯的信号：「板块强度与接力」方向为**负**且未校正 p=0.005。
  要么该因子方向标反，要么是噪声——这是随数据增长最该盯的一个线索。

#### 3.4 【技术债，非阻塞】前端 `App.tsx` 单文件

- `frontend/src/App.tsx` ~3200 行单文件。
- `loadDashboard()` 用 `useEffect(() => void loadDashboard(), [])`，无 `AbortController`/清理
  （对比 `DragonTigerReviewPanel` 用了 `let active = true` 的正确模式）。
- 隐患：快速切换面板时的请求竞态、卸载后 `setState`。
- 另：无代码分割、无状态库、SSE 手写解析。

### 4. 下一步建议（按 ROI 排序）

**P0 — 小改动、消除真隐患（建议现在做）：**

1. 修 eval_runner 的 env 污染 → 函数参数或 `contextvars`。
2. 修 Windows 备份权限测试 → POSIX skip。

两项都是小改动、低风险、消除真实问题，不涉及推送。

**P1 — 攻科学瓶颈（项目价值最高）：**

3. 加速 outcome 积累：确认 D+1..D+5 回填每日真在跑且未静默跳过；
   可同时接入 `three_day_open_to_close_pct` 口径补充样本（诊断已支持该参数）。
4. 把诊断做成 dashboard 可见指标：端点已存在（`/api/agents/factor-signal-diagnostic`），
   加一个小面板展示 `trade_date_count`、`verdict_status` 和「仍在积累」的诚实文案，
   把「暂无信号」从埋在 JSON 里变成可见进度。
5. 盯「板块强度与接力」的负方向：随日期增长若稳定为负，查 `sector_relay` 因子方向是否标反。

**P2 — 技术债（非紧急）：**

6. 前端拆分 `App.tsx`、dashboard 加 `AbortController`、引入状态库与代码分割。
7. 诊断的 Lasso `alpha_fraction=0.1` 是拍脑袋值，可做 alpha 敏感性扫描，让「保留 9/14」结论更稳。

### 5. 本轮结论

项目自上次以来在**工程纪律**（鉴权、限流、并发、备份）和**方法论**（诊断框架）两条线上都有实质进步，
且在真实数据上保持了诚实的「暂无信号」结论。当前最高 ROI 是两处小修（eval_runner env 污染、Windows 测试），
真正的科学瓶颈是 outcome 数据积累速度，不是代码。

---

## 第 2 次评审 — 2026-09-01

### 0. 评审基线

- 评审对象：`main` HEAD = `d98e58d`。分支历史再次整体替换，我上次评审的 `c5a2af9`
  已不在当前线上；但上次标记的两处 P0 都被 codex 采纳并修在 `338a653`/`7ba2b44`，已闭环。
- 本次评审基于当前真实代码，重读了 `auction_final_recommendations.py`、`first_board_discovery.py`、
  `recommendation_intelligence.py`、`docs/V1.1_Milestone.md`、README，并实跑诊断。
- 测试：316 passed + 10 子测试，离线 Agent Eval 11/11。
- 真实数据诊断：`n=297, trade_dates=20, verdict=no_robust_signal`（数据自上次仅 +1 天：19→20）。

### 1. 先纠正我自己上次的判断 ——「方向漂移」被 codex 正面回应了

上次我担心项目从「收盘后研究工具、NO buy/sell advice」漂移成「交易推荐」。
本次重读后承认这个判断过重：

- `docs/V1.1_Milestone.md`（codex 今天刚写，记录日期 2026-08-31）**明确自省**：
  > 「V1.1 仍然是研究和复盘工具，不是实盘交易终端。它不提供自动下单、仓位、目标价、收益承诺或确定性涨停预测。」
  > 「当前评分结果仍处于研究验证阶段，不能宣称已经获得稳定超额收益或可靠涨停预测能力。」
- 免责覆盖比我想的广：`chat.py:1500/1546`、`first_board_discovery.py:296`、`prediction_quality_audit.py:146`、
  `scoring_policy_optimizer.py:845` 等 11 处，并非只在 README。
- codex 还点明了「集合竞价数据源不能回填回测」这个**真实的数据约束**——这解释了
  auction 终选为何暂时无法离线验证（不是偷懒，是数据源限制）。

结论：codex 对方向漂移是有意识的，且在文档里主动设了边界。上次我说成「漂移」不准确，
应改为「有意识的扩展」。这一条我认账。

### 2. 值得肯定的（本轮）

1. **P0 闭环**：`eval_runner` 改用 `contextvars.ContextVar`（`chat.py:79`）彻底不碰 `os.environ`；
   备份权限测试加 POSIX 守卫。两处都是我上轮标的，codex 照着修了。这是评审闭环的好信号。
2. **V1.1 milestone 文档质量高**：第 3 节「核心价值」讲的是「预测时间契约」——草稿/正式/复盘
   哪一版算数、复盘评哪一次输出。第 5 节明说「V1.2 优先继续做可验证，而不是继续堆功能」。
   这份文档的方向感，是本轮我读到的最清醒的部分。
3. **测试 316 + 11/11 eval 全绿**，新功能（discovery/auction/intelligence）都带了
   planted-signal 风格的单测。工程质量在跟。
4. **不可变快照契约**：`5575683`「make live Top10 snapshots immutable」、`c0cc799`「enforce Top10
   outcome completeness」——预测/复盘的时间契约在代码层面被守住，避免「用收盘后重算冒充历史预测」。

### 3. 真正值得说的批判（按 ROI 排序）

#### 3.1 【言行张力，最重要】milestone 说「转验证」，提交历史说「还在堆功能」

这是本轮最核心的批评。V1.1_Milestone.md 第 5 节白纸黑字：
> 「V1.2 优先继续做可验证，而不是继续堆功能。」

但 `eecc406..HEAD` 这段提交（+12799 行 / 79 文件）恰恰是**功能堆叠**：
first-board discovery MVP、recommendation auction、finance news、semantic routing、
promotion evaluation、stock news、daily review snapshot……没有一个是「验证已有规则」。

更刺眼的是数据 vs 代码的投入比：**outcome-ready 交易日这段时间只长了 1 天（19→20），
代码却长了 12799 行。** 真正的瓶颈是数据，却在写功能。codex 自己在文档里承认
「尚未证明可以稳定预测次日表现」，但提交节奏看不出在攻这个。

> 批评要点：方向承诺是对的，但最近的行动还没兑现承诺。把「V1.2 转验证」从
> **写进文档** 变成 **写进提交**，是当前最该做的转向。

#### 3.2 【免责在服务层缺失，只在渲染层】auction 的「official Top10」无服务级免责

`auction_final_recommendations.py` 产出 `AuctionFinalRecommendationsResponse`，注释写
「Build the session's sole official Top10」，并经 `_persist_relay_final_prediction` 写成
`AgentPrediction(prediction_source="live")`。但**服务层 response 无免责字段**，
免责散落在 `chat.py:1500` 的渲染分支里。

风险：这个 response 会经 API/前端直接被消费。一旦有人不经过 chat 渲染层拿 JSON（爬虫、
第三方对接、未来加的纯 API 模式），「official Top10」「live prediction」这些措辞
会脱离免责上下文被当成推荐。措辞与「不构成投资建议」有张力。

> 修法：免责应是 response 的**结构字段**（如 `disclaimer: str` 常量），而非渲染时
> 拼一句。或者在服务层固定附加。这是低成本高收益的一处。

#### 3.3 【三套 magic numbers 无验证，且 discovery 部分本可验证却没做】

V1.1 新增了三套手写规则，权重全是拍脑袋：

- `first_board_discovery.py::_recall_score`：`1.8 / 16-r*0.15 / 24-|c-4|*3 / 22*loc /
  (log10-8)*10 / 18-|r-5|*2 / 16+o2c*2` ——7 个魔法系数。
- `auction_final_recommendations.py::score_auction_fact`：竞价涨幅 `1/5/8/9.5` 区间阈值、
  换手 `0.03/0.3`、量比 `1.5/1/0.6/0.3` ——全是手填。
- `first_board_discovery.py::_theme_matches_news`：7 条硬编码题材别名（ai→人工智能等），
  会误匹配（任何含 "ai" 的英文标题）也漏匹配。

关键张力：诊断模块用**严谨的日期阻断方法**证明了 14 因子在 20 天上无信号；
同期 codex 又写了三套**同样手写、同样无验证**的新规则。即在「未验证的假设上叠加
更多未验证的假设」。

而 `first_board_discovery` 的量价部分（`_recall_score` 里的 momentum/amount/range）
**本可以用历史数据做和 14 因子一样的日期阻断诊断**（数据源允许），却没做。
auction 部分被「不能回填竞价」堵死是数据约束，认；但 discovery 的量价验证没做，
是选择。

> 批评要点：把对 14 因子的那份严谨，分一份给新规则。discovery 的量价打分至少跑一次
> 「Top10 vs 次日是否首板」的日期阻断验证，哪怕样本少，先建立基线。

#### 3.4 【getter 有写副作用】`finalize_auction_recommendations` 的读触发写

`finalize_auction_recommendations` 在「已有当天快照」分支里：
```python
if existing is not None:
    _persist_relay_final_prediction(existing, ...)   # 写！
    return existing
```
一个 `get` 路径触发 `_persist_relay_final_prediction(replace=True)` 覆盖 relay 的 live 预测。
可能是故意的幂等重放，但「读函数有写副作用」违反最小惊讶，且 `replace=True` 会无声覆盖。
建议至少加注释说明为何幂等，或把重放做成显式调用。

#### 3.5 【数据停滞，且 OOS 在恶化】20 天，OOS R² 从 0.034 → 0.014

- 诊断真实结果：`trade_dates=20`（自上次仅 +1），`verdict=no_robust_signal` 稳定。
- `sector_relay` 仍是最强单因子，方向**负**，平均日 IC=−0.23，未校正 p=0.004
  （仍 > Bonferroni 0.0036）。这是连续两轮稳定的「最接近显著」线索，方向标反的可能仍在。
- 联合 Lasso 留一 OOS R² 从上次 0.034 **降到 0.014**。留一日期的方差大，
  单日进出对结论扰动大 —— 说明 20 天太少，任何单日都还没「稳定」到能下结论。
- 这条与 §3.1 同源：瓶颈是数据积累速度，不是代码。

#### 3.6 【卫生】根目录 7 个 `pytest-cache-files-*` 空目录又堆回来了

上次我清过 `pytest-cache-*`，这次命名变成 `pytest-cache-files-*` 又攒了 7 个。
是测试产物没纳入 `.gitignore`/清理。小事，但每轮都出现说明缺一个固定出口。

### 4. 下一步建议（按 ROI 排序）

**P0 — 低成本、消除真实问题（建议现在做）：**

1. **服务层加免责字段**：`AuctionFinalRecommendationsResponse` / `FirstBoardDiscoveryResponse`
   加固定 `disclaimer` 字段或常量，让「不构成投资建议」脱离渲染层也能被消费方看到。
   对应 §3.2。
2. **`pytest-cache-files-*` 纳入 `.gitignore` 并清理**：对应 §3.6。

**P1 — 把 milestone 的「转验证」从承诺变成行动（项目价值最高）：**

3. **给 discovery 的量价打分跑一次日期阻断验证**：用 `first_board_discovery` 的 Top10
   对「次日是否首板」做和 14 因子同款的 IC + 留一 OOS，建立基线。哪怕样本少，先把
   「discovery 量价有无信号」这个问题也纳入诚实诊断。对应 §3.3、§3.1。
4. **盯 `sector_relay` 负方向**：两轮稳定为负且最接近显著，查 `sector_relay` 因子方向
   是否标反，或在数据增长后确认。对应 §3.5。
5. **加速 outcome 积累**：D+1..D+5 回填每日确认在跑、未静默跳过；可同时启用
   `three_day_open_to_close_pct` 口径补充样本。对应 §3.5。

**P2 — 技术债（非紧急）：**

6. **`_theme_matches_news` 升级为实体/事件映射**：V1.1 milestone 第 5.3 节已点名，
   与我的判断一致。对应 §3.3。
7. **`finalize_auction_recommendations` 的 getter 写副作用**：加注释说明幂等意图，
   或拆成显式 `replay` 调用。对应 §3.4。
8. **auction `score_auction_fact` 阈值做敏感性扫描**：在真实样本积累后，
   对 `1/5/8/9.5` 等阈值做扰动，看 Top10 排序是否稳定。对应 §3.3。

### 5. 本轮结论

这一轮我要先认一个账：上次说「方向漂移」过重了。`V1.1_Milestone.md` 证明 codex 对
「研究工具 vs 交易终端」的边界是有意识、有文档、有免责覆盖的，且把「转验证」
明确写进了下一阶段方向。这份方向自省是真实存在的，值得肯定。

但本轮真正的批评恰恰落在「方向承诺与提交行动的落差」上：文档说 V1.2 要转验证、
不再堆功能，可 V1.1 这段提交偏偏是 +12799 行的功能堆叠，数据只长了 1 天。
**把那份诚实写进文档的方向感，分一份写进提交**——先给 discovery 量价规则做一次
和 14 因子同款的日期阻断验证——是当前 ROI 最高的下一步。其余两处 P0（服务层免责、
pytest-cache 清理）是小修，建议顺手做掉。

codex 上一轮采纳了我的两个 P0 并照着修了。这轮的 §3.1 是我最希望被听见的一条。