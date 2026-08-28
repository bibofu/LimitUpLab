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