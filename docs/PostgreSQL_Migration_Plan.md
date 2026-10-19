# SQLite → PostgreSQL 迁移与多实例改造路线

本文档规划 LimitUpLab 从单机 SQLite 迁移到 PostgreSQL，并支撑多实例部署，同时对存储结构与数据库访问方式做优化。

- 状态：规划稿（未开始实施）
- 影响范围：`backend/app/database.py`、`backend/app/repositories/*`、`backend/app/agents/react_runtime/*`、`backend/app/services/{quality_*,finance_news_latest,system_health}*`、`backend/scripts/*`、`docker-compose.yml`
- 非目标：本期不引入 ORM；不改动评分逻辑与预测口径；不迁移前端

---

## 1. 结论摘要

1. **迁移的主要动因不是容量，而是并发与多实例写入。** 当前库 261 MB、46 张表，单机完全够用；但 SQLite 只允许单写入者，且现有代码有大量进程内状态（SSE worker 线程、内存限流器、文件锁、Docker 命名卷），这些在水平扩容时会直接失效。
2. **不做双写、不做灰度双跑。** 预测快照是不可变账本（`agent_predictions` / `recommendation_prediction_finals` / `prediction_snapshot_archive`），双写会产生两个"终版"来源，破坏可回放性。切换采用**停写 + 一次性 ETL + 校验 + 切流**，窗口选在非交易日收盘流水线完成之后。
3. **保留显式 SQL，不引入 ORM。** 68 个文件、367 处 `execute/executemany`，SQL 已高度调优且语义敏感（去重、不可覆盖、预算原子扣减）。ORM 迁移等于重写，风险大于收益。schema 变更用 Alembic 管理。
4. **先做数据访问层抽象，再逐域移植。** 引入 `app/db/` 统一连接/事务/行工厂，Repository 的 Protocol 接口保持不变，把 367 处 SQL 按领域拆成 6 个批次迁移，每批独立提交、独立验证。
5. **多实例需要额外改造 5 处进程内状态**：SSE worker、限流器、刷新进程文件锁、上游采集缓存、落盘 JSON 报告。这些不解决，数据库换成 PostgreSQL 也不能安全多副本。

---

## 2. 现状盘点（实测）

### 2.1 数据规模

| 指标 | 数值 |
| --- | --- |
| 库文件 | `backend/data/limituplab.sqlite`，261 MiB（4096 B × 66,761 页） |
| 表 / 索引 | 46 / 35 |
| Schema 版本 | `PRAGMA user_version = 12` |
| 日志模式 | WAL，`synchronous=NORMAL`，`busy_timeout=5000` |

行数 Top：

| 表 | 行数 | 说明 |
| --- | --- | --- |
| `stock_intraday_bars` | 499,052 | 分钟 K 缓存，体积与写入量主要来源 |
| `stock_daily_bars` | 165,102 | 未复权日 K |
| `stock_news_items` | 6,040 | 新闻 |
| `limit_up_events` | 5,417 | 涨停/炸板事件 |
| `agent_predictions` | 3,225 | 历史/实时预测快照 |
| `first_board_features` | 3,253 | 首板结构化特征 |
| `first_board_enrichment_snapshots` | 1,711 | 扩展输入快照 |
| `first_board_outcomes` | 854 | D+1/D+3 结果回填 |
| `react_events` / `quality_trace_snapshots` | 1,104 / 1,086 | Agent 运行事件与诊断快照 |
| `chat_sessions` / `chat_messages` | 128 / 342 | 会话 |

### 2.2 SQL 表面积

| 指标 | 数量 |
| --- | --- |
| 含 SQL 的模块文件 | 68 |
| `execute` / `executemany` 调用点 | 367 |
| `connect()` 调用点 | 66 |
| `initialize_database()` 调用点 | 67 |
| `BEGIN IMMEDIATE` | 26 |
| `INSERT OR IGNORE`（需改写） | 12 |
| `ON CONFLICT ... DO UPDATE`（SQLite/PG 通用，无需改写） | 18 |
| `AUTOINCREMENT` | 3 |
| 依赖 `rowid` 的文件 | 4（`chat_session_repository`、`quality_evidence`、`quality_repository`） |
| 依赖 `julianday()` | 1（`quality_evidence`） |
| 只读 URI（`?mode=ro` + `PRAGMA query_only`） | 5 个脚本/模块 |

### 2.3 已有的冗余索引（可直接删除）

| 索引 | 表 | 冗余原因 |
| --- | --- | --- |
| `idx_limit_up_events_trade_date` | `limit_up_events` | PK 为 `(trade_date, symbol)`，该索引是其前缀 |
| `idx_first_board_features_date` | `first_board_features` | PK 为 `(trade_date, symbol)` |
| `idx_stock_daily_bars_symbol_date` | `stock_daily_bars` | PK 为 `(symbol, trade_date)`，完全相同 |
| `idx_stock_intraday_bars_lookup` | `stock_intraday_bars` | 与 PK `(symbol, trade_date, period_minutes, timestamp)` 完全相同 |
| `idx_agent_predictions_date` | `agent_predictions` | UNIQUE `(trade_date, symbol, scoring_version, prediction_source)` 的前缀 |

### 2.4 大 JSON 字段（写入放大热点）

| 列 | 行数 | 平均 / 最大 | 特征 |
| --- | --- | --- | --- |
| `react_runs.checkpoint_json` | 93 | 77 KB / 592 KB | **每个图节点都 UPDATE 同一行**，PG 下 TOAST 行更新无法走 HOT |
| `chat_messages.metadata_json` | 344 | 24 KB / 536 KB | 与 `agent_runs.output_json` 内容高度重复 |
| `agent_runs.output_json` | 380 | 24 KB / 536 KB | 运行归档 |
| `quality_trace_snapshots.payload_json` | 1,098 | 9 KB / 98 KB | 合计 9.6 MB |
| `daily_review_snapshots.report_json` | 29 | 120 KB / 210 KB | 每日复盘快照 |
| `agent_live_prediction_snapshots.snapshot_json` | 37 | 42 KB / 54 KB | 不可变快照 |

### 2.5 阻碍多实例的进程内状态

| 位置 | 状态 | 多实例后果 |
| --- | --- | --- |
| `routers/react_chat.py:19` `_workers` | 运行中线程字典 | A 实例发起的 run 在 B 实例上无法判活，SSE 重连会误报中断 |
| `services/agent_rate_limit.py` | `deque` + `threading.Lock` | N 个副本 = 限额放大 N 倍 |
| `scripts/run_recommendation_refresh_loop.py` | 文件锁 | 多副本同时刷新，重复写快照 |
| 采集器模块级 `_cache` / `_inflight`（7 处） | 进程内缓存 | 上游调用量 ×N；不影响正确性 |
| `data/*.json`（`finance_news_latest.json`、`recommendation_refresh_latest.json`、`daily_close_loop_latest.json`） | 容器内文件 | 多主机部署时各副本读到不同内容 |
| `docker-compose.yml` 命名卷 `limituplab-data` | 单机卷 | 无法跨主机共享，必须先解除 |

---

## 3. 目标架构

```text
                ┌──────────── nginx (SSE: proxy_buffering off) ────────────┐
                │                                                          │
          backend-1 (uvicorn)                                    backend-N (uvicorn)
                │  psycopg_pool 10-20 conns                     │
                └───────────────┬───────────────────────────────┘
                                │
                       PostgreSQL 16 (primary)
                                │
        ┌───────────────────────┼────────────────────────┐
   quality-worker       recommendation-refresh      daily-update
   (advisory lock)      (advisory lock 选主)         (advisory lock)
```

- 数据库：PostgreSQL 16+，`pg_stat_statements`、`auto_explain` 可选。
- 驱动：`psycopg[binary]` 3.x + `psycopg_pool`。
- 迁移工具：Alembic（只管 DDL 版本，不接管 ORM 映射）。
- 连接串：`LIMITUPLAB_DATABASE_URL=postgresql://user:pass@host:5432/limituplab`，保留 `LIMITUPLAB_DATABASE_PATH` 作为过渡期兼容开关。
- 部署形态：单主机多容器即可满足"多实例"；跨主机时 PostgreSQL 独立部署、命名卷全部移除。

---

## 4. 关键技术决策

### D1 保留显式 SQL，不引入 ORM

**理由**：现有 SQL 里有大量语义敏感写法——`ON CONFLICT DO NOTHING` 保证预测只增不改、`UPDATE ... WHERE reserved+?<=?` 做预算原子扣减、`INSERT OR IGNORE` 做幂等——整体改写为 ORM 会改变锁粒度与冲突语义。
**替代方案（已否决）**：SQLAlchemy Core 全量重写；工作量与回归面远大于收益。

### D2 引入 `app/db/` 数据访问层

```python
# app/db/__init__.py（目标形态）
@contextmanager
def transaction(*, read_only: bool = False) -> Iterator[Connection]: ...
def fetch_all(sql: str, params: Sequence = ()) -> list[dict]: ...
def execute(sql: str, params: Sequence = ()) -> int: ...       # 返回 rowcount
def upsert_returning(sql: str, params: Sequence = ()) -> list[dict]: ...
```

- 统一 `dict_row` 行工厂，`row["column"]` 访问方式与现有 `sqlite3.Row` 一致，Repository 改动最小。
- 事务边界显式化：写操作走 `transaction()`，只读查询走 `transaction(read_only=True)`。
- 保留 `ResilientSQLiteConnection` 的重试思想，但 PG 侧重试只针对 `serialization_failure` / `deadlock_detected`，且必须整事务重试（不是单语句重试）。

### D3 占位符逐语句迁移，禁止全局正则替换

- 目标写法：`%s`。
- 过渡期可提供 `?` → `%s` 的兼容游标，但仅用于尚未移植的模块，并加一条 CI 断言：全部移植完成后该兼容层使用次数必须为 0，否则测试失败。
- 原因：AGENTS.md 明确禁止用正则批量覆盖修问题；`?` 与 JSONB 的 `?` 运算符有歧义风险。

### D4 事务与锁语义映射

| SQLite | PostgreSQL | 说明 |
| --- | --- | --- |
| `BEGIN IMMEDIATE` | `BEGIN` + 显式 `SELECT ... FOR UPDATE` | PG 默认 READ COMMITTED 不预取写锁 |
| 单行覆盖保护（`premarket_final` 不可覆盖） | `INSERT ... ON CONFLICT (trade_date) DO NOTHING` + `RETURNING` | 让唯一约束承担判定，减少应用层竞态 |
| 预算扣减 `.rowcount` 判定 | `UPDATE ... WHERE ... RETURNING 1` | 语义等价且可读 |
| 进程内 `threading.Lock` 保护的单例 | `pg_try_advisory_lock(key)` | 用于 worker 选主 |
| `connection.total_changes` 差值统计 | `executemany(..., returning=True)` 计数 | 仅 `first_board_repository:550` 一处 |

### D5 `rowid` 必须有显式替代

`chat_messages`、`react_calls` 里用 `ORDER BY rowid` 做稳定排序。PG 无 `rowid`（`ctid` 会因 VACUUM/UPDATE 变化，不可用于排序）。
**方案**：为 `chat_messages` 增加 `seq BIGINT GENERATED ALWAYS AS IDENTITY`，为 `react_calls` 增加 `seq BIGINT`；排序改为 `ORDER BY created_at, seq`。`julianday()` 改为 `timestamptz` 直接比较。

### D6 类型映射

| 现状（SQLite） | 目标（PG） | 影响 |
| --- | --- | --- |
| `trade_date TEXT`（`YYYY-MM-DD`） | `date` | 索引更小、比较正确 |
| `created_at/started_at/... TEXT`（带时区 ISO） | `timestamptz` | 现库全部为 tz-aware，可直接解析 |
| `stock_intraday_bars.timestamp TEXT`（无时区本地时间） | `timestamp`（Asia/Shanghai 语义） | 全库 499,052 行均为 naive，保持市场本地时间语义 |
| `closed_limit INTEGER` 0/1 | `boolean` | 消除 `int(bool)` 转换 |
| `amount/turnover_rate REAL` | `double precision` | 保留浮点语义，避免 `numeric` 带来的计算开销 |
| `estimated_cost_usd REAL` | `numeric(14,6)` | 金额统计需要精确 |
| `*_json TEXT` | `jsonb`（见 D7） | |
| `symbol TEXT` | `text` + `CHECK (symbol ~ '^\d{6}$')` | 约束前移 |

**转换风险**：迁移脚本必须逐列解析，遇到无法解析的值**中止并输出样本**，不允许静默置 NULL。

### D7 JSON 列策略

- 仅整体读写的列（`facts_json`、`reasons_json`、`risks_json`、`position_json`）→ `jsonb`，代码里 `json.loads(...)` 改为直接读取（适配层同时兼容 `str` 和 `dict`）。
- 超大且频繁整行更新的列（`react_runs.checkpoint_json`）→ **拆表**：

  ```sql
  CREATE TABLE react_checkpoints (
      run_id     text PRIMARY KEY REFERENCES react_runs(run_id) ON DELETE CASCADE,
      revision   integer NOT NULL,
      payload    jsonb   NOT NULL,
      updated_at timestamptz NOT NULL
  ) WITH (fillfactor = 70, toast_compression = lzma);
  ```

  这样 `react_runs` 主行保持小尺寸，`UPDATE` 更容易走 HOT；checkpoint 负载也可启用 LZMA 压缩。
- `chat_messages.metadata_json` 只保留实际消费的字段，去掉与 `agent_runs.output_json` 重复的大块内容。

### D8 备份与运维替换

| 现状 | 目标 |
| --- | --- |
| `scripts/backup_database.py`（online backup API + integrity_check） | `pg_dump -Fc` + WAL 归档（PITR）；保留策略沿用 14 份 |
| `PRAGMA integrity_check` | `pg_dump` 恢复演练 + `amcheck` 可选 |
| `.sqlite-wal/-shm` 忽略规则 | 失效，改为 `pg_dump` 产物忽略 |

### D9 切换策略：停写一次性切换，禁止双写

- 预演：在生产快照副本上完整跑一遍 ETL + 对拍，记录耗时（预估 < 10 分钟）。
- 窗口：非交易日，且在当日 `daily-update` 完成后；切换前把源库置为只读。
- 步骤：停 backend/worker → 校准源库 → 跑 ETL → 校验 → 切 `LIMITUPLAB_DATABASE_URL` → 启新栈 → 冒烟 → 观察 24h → 下线 SQLite 写入路径。
- 回滚：保留源库文件与 ETL 时间点标记；回滚 = 切回旧镜像 + 旧卷，允许丢失切换后的新数据（仅限窗口内产生的会话）。

### D10 测试基线：pytest 直连 PostgreSQL

- CI 增加 PostgreSQL 16 service；本地 `docker compose up -d postgres`。
- 每个测试用独立 schema（`CREATE SCHEMA test_<uuid>` + `search_path`）或外层事务回滚，替代当前"临时 sqlite 文件"模式。
- 保留 `Protocol` 驱动的纯单元测试，不依赖真实库。
- Agent 黄金集（`backend/evals/golden`）指向迁移后的 fixture 库，确保评分结果字节级一致。

---

## 5. 数据模型优化清单

### 5.1 索引

**删除**：见 2.3 的 5 个冗余索引。

**新增/调整**（按实际查询补充，上线前用 `EXPLAIN (ANALYZE, BUFFERS)` 验证）：

```sql
-- Agent 概览与首板页：按日取已封板事件
CREATE INDEX idx_limit_up_events_date_closed
    ON limit_up_events (trade_date, board_height DESC, first_limit_time DESC)
    WHERE closed_limit;

-- 结果回填任务：只扫未就绪行
CREATE INDEX idx_outcomes_pending ON first_board_outcomes (base_trade_date)
    WHERE outcome_ready = false;

-- 运行日志清理
CREATE INDEX idx_react_events_run_seq ON react_events (run_id, seq);

-- 会话消息游标分页
CREATE INDEX idx_chat_messages_session_seq ON chat_messages (session_id, created_at, seq);

-- 预测快照按标的历史查询
CREATE INDEX idx_agent_predictions_symbol_version
    ON agent_predictions (symbol, trade_date DESC, scoring_version);
```

### 5.2 分区

当前 499k 行，**不建议立刻分区**。触发条件与方案：

- 触发：单表 > 2,000 万行或 > 50 GB。
- 首选对象：`stock_intraday_bars`、`react_events`、`quality_trace_snapshots`。
- 方式：`PARTITION BY RANGE (trade_date)` 按月；查询侧必须带 `trade_date` 过滤才能裁剪（现有 `load_stock_intraday_bars` 已带日期范围，符合条件）。

### 5.3 保留策略（先立规再写清理任务）

| 表 | 保留 | 说明 |
| --- | --- | --- |
| `agent_predictions` / `agent_live_prediction_snapshots` / `recommendation_prediction_finals` / `prediction_snapshot_archive` | **永久** | 不可变账本 |
| `first_board_outcomes` / `daily_review_snapshots` | 永久 | 复盘依据 |
| `react_events` / `react_calls` | 90 天 | 已完成 run 的事件流 |
| `quality_trace_snapshots` | 90 天 | 已有 `quality_trace.py:168` 的清理逻辑可复用 |
| `agent_usage_events` | 365 天 | 成本与限流统计 |
| `stock_intraday_bars` | 2 年 | 可按需缩短 |

### 5.4 大字段与写放大

- `react_checkpoints` 独立表 + `fillfactor=70` + `toast_compression=lzma`。
- 高频更新表（`react_runs`、`recommendation_intelligence_current`）设置 `fillfactor=80`。
- `agent_cache` 定期 `DELETE ... WHERE expires_at < now()`（现有 `delete_expired` 已实现）。

---

## 6. 数据库访问性能优化

| # | 优化 | 现状 | 目标 |
| --- | --- | --- | --- |
| 1 | 连接池 | 每次操作 open/close，66 处 | `psycopg_pool` min=4 / max=20 / max_lifetime=30m |
| 2 | 迁移执行位置 | 67 处 per-request `initialize_database()` | 启动时 Alembic upgrade，请求期零 DDL |
| 3 | 消除全表加载 | `list_events()` 每次聊天加载 5,417 行 | Agent 注册表改为按需查询，单次只取目标日 |
| 4 | 批量写入 | `executemany` 逐行 | 首导用 `COPY`；日常增量用多值 `INSERT` |
| 5 | 预处理语句 | 无 | `prepare_threshold=5`（psycopg 自动 server-side prepare） |
| 6 | 分页 | `LIMIT/OFFSET` 为主 | 会话消息改 `(created_at, seq)` 游标 |
| 7 | 统计观测 | 无 | `pg_stat_statements` + 慢查询阈值 200 ms |
| 8 | 缓存 | 进程内 + `agent_cache` 表 | 保留 L1 进程内，热点概览加 L2（DB 或 Redis，见待决策） |

**第 3 项是应用层最大收益点**：`AgentToolRegistry` 目前接收 `events: list[LimitUpEvent]` 全量列表，改造为接收仓储对象并提供 `events_for_date()` / `latest_trade_date()`，聊天路径数据量从 5,417 行降到单日约 60 行。

---

## 7. 多实例改造清单

| # | 问题 | 方案 | 验收 |
| --- | --- | --- | --- |
| M1 | SSE run 执行绑定进程 | `react_runs` 增加 `owner_instance`、`lease_expires_at`、`heartbeat_at`；worker 定期续约；SSE 只读 DB 判断存活 | 杀掉持有 run 的实例后，另一实例能接管并正确报错/恢复 |
| M2 | 刷新进程文件锁 | `pg_try_advisory_lock(hashtext('recommendation_refresh'))` | 起 3 个副本，仅 1 个执行 |
| M3 | 内存限流 | 落库原子计数（`UPDATE ... RETURNING`）或 Redis 滑动窗口 | N 副本下全站 QPS 限额不放大 |
| M4 | 落盘 JSON 报告 | `finance_news_latest.json` 等改为表存储或对象存储 | 任意副本读到的内容一致 |
| M5 | Docker 命名卷 | 移除 `limituplab-data` 卷；备份落外部存储 | 跨主机部署可用 |
| M6 | 单用户并发 | 已有 `react_active_session` 部分唯一索引，PG 支持；补 `owner_instance` 清理孤儿 | 并发提交返回 409 |

---

## 8. 分阶段路线图

> 预估为单人工作量，含自测，不含评审等待。

### P0 准备（1–2 天）

- 记录基线：关键页面 P95 延迟、Agent 单轮耗时、日更耗时、库体积。
- 输出 ADR：为什么迁移、为什么不双写、为什么不用 ORM。
- 冻结 schema：迁移期内不再新增 SQLite 表结构变更。
- **验收**：基线报告入库到 `docs/`。

### P1 数据访问层抽象（3–5 天）

- 新增 `app/db/`：`connection()`、`transaction()`、`fetch_all/fetch_one/execute`、错误分类。
- `get_database_path()` / `connect()` 保留为兼容入口，内部委托到新层。
- 引入 `LIMITUPLAB_DATABASE_URL`，未设置时回退 SQLite（保证旧脚本可跑）。
- **验收**：全部现有测试在 SQLite 下通过；新增 6 个 DB 层单元测试。

### P2 PostgreSQL Schema 与迁移工具（3–4 天）

- 写 Alembic `0001_initial`，覆盖 41 张在用表（按 5.1 调整索引、按 D5/D6 调整类型）。
- 处理遗留表：`users`、`user_identities`、`auth_sessions`、`auth_challenges`、`auction_final_recommendation_snapshots` —— **不迁移**，记录到 `docs/` 归档说明。
- 建立 CI 的 PostgreSQL 16 service 与测试隔离方案（D10）。
- **验收**：空库 `alembic upgrade head` 成功；schema 与 ER 图比对无误。

### P3 ETL 与对拍（2–3 天）

- `scripts/migrate_sqlite_to_postgres.py`：
  1. 只读打开 SQLite（`mode=ro` + `PRAGMA query_only`）；
  2. 按外键依赖顺序分批 `COPY`；
  3. 时间/布尔/JSON 列逐列转换，失败即中止并打印样本；
  4. 输出每表行数与耗时。
- 对拍脚本 `scripts/verify_migration.py`：
  - 行数一致；
  - 数值列 `SUM/AVG` 相对误差 < 1e-9；
  - 每表随机 200 行逐字段比对；
  - 20 个关键查询结果集哈希一致。
- **验收**：全量对拍 0 差异；ETL 耗时记录在案。

### P4 Repository 分批移植（8–12 天）

按依赖顺序，每批独立 PR + 独立验收：

| 批次 | 范围 | 说明 |
| --- | --- | --- |
| B1 | `limit_up_repository`、`first_board_repository`（读） | 基础事实读取，影响面最大 |
| B2 | `first_board_repository`（写）、`daily_pipeline_repository` | 预测快照写入、不可覆盖逻辑 |
| B3 | `chat_session_repository`、`chat_memory_repository`、`agent_run_repository` | 这里要处理 `rowid` → `seq` |
| B4 | `agent_cache_repository`、`agent_usage_repository`、`recommendation_intelligence_repository` | 缓存与用量 |
| B5 | `quality_*`（5 个仓储）、`quality_schema.py` | 预算原子扣减、trace 清理 |
| B6 | `react_runtime/lifecycle.py`、`consolidation_repository`、`post_limit_repository`、研究脚本 | 只读 URI → `SET TRANSACTION READ ONLY` |

- **验收（每批）**：该域 pytest 全绿 + 至少 3 个真实 Agent 问答端到端通过（AGENTS.md 要求）。

### P5 多实例改造（5–8 天）

- M1–M6 全部落地；`docker-compose` 拆出 `postgres` 服务与 `PG_*` 环境变量。
- 压测：2 个 backend 副本 + SSE 并发 20，验证无重复执行、无 409 误判。
- **验收**：随机杀副本 3 次，会话可恢复，预测快照无重复无覆盖。

### P6 性能优化（3–5 天）

- 第 6 节的 8 项逐条落地并测量。
- `EXPLAIN (ANALYZE, BUFFERS)` 记录 top 20 查询计划，归档到 `docs/`。
- 对比 P0 基线：目标 API P95 不劣化，Agent 单轮耗时下降 ≥ 20%（来自第 3 项全表加载消除）。

### P7 切换与观察（2–3 天）

- 非交易日执行 D9 流程；切换前后各做一次全量校验。
- 观察 24 小时：错误率、SSE 中断率、慢查询、连接池饱和度。
- **验收**：无 P0/P1 故障；数据健康页 `status=healthy`。

### P8 清理（1–2 天）

- 删除 SQLite 兼容层、`?` → `%s` 兼容游标、`backup_database.py`、WAL 相关配置。
- 更新 `README.md`、`AGENTS.md`（数据库约定）、`.env.production.example`。
- 归档 `数据采集.md` 中关于 SQLite 只读核查的段落。

**合计：约 4–6 周（单人）**；其中 P4 可与 P5 部分并行。

---

## 9. 迁移期验证矩阵

| 层级 | 手段 | 通过标准 |
| --- | --- | --- |
| 单表 | 行数 + 主键集合比对 | 完全一致 |
| 数值 | `SUM`/`AVG`/`MAX` 相对误差 | < 1e-9 |
| 明细 | 每表随机 200 行逐字段 | 0 差异（时间统一转 UTC 比较） |
| 查询 | 20 条关键 SQL 结果集哈希 | 顺序敏感查询按显式 ORDER BY 比对 |
| 接口 | 20 个 GET 接口 JSON 快照 | 字段与数值一致 |
| Agent | 黄金集 3 个问题 | 工具序列与关键数值一致 |
| 前端 | 5 个主要路由冒烟 | 无报错、数据日期一致 |
| 并发 | 2 副本 × 20 并发 SSE | 无重复 run、无限流放大 |

---

## 10. 风险清单

| 风险 | 概率 | 影响 | 触发信号 | 预案 |
| --- | --- | --- | --- | --- |
| 时间/时区转换错误 | 中 | 高（快照排序、D+1 判定全错） | 对拍出现 `created_at` 差异 | 逐列转换 + 固化 `Asia/Shanghai`；失败即中止 |
| `ON CONFLICT` 语义与 `INSERT OR IGNORE` 不等价 | 中 | 高（外键级联删除） | 迁移后子表行数减少 | 12 处逐一人工确认，禁止机械替换 |
| `ORDER BY` NULL 位置变化 | 中 | 中（名单顺序变化） | 接口快照 diff | 显式 `NULLS LAST` |
| 并发下终版快照被覆盖 | 低 | 高（破坏可回放） | `recommendation_prediction_finals` 出现多行同日 | 依赖唯一约束 + 集成测试 |
| SSE 跨实例中断 | 高（未改造前） | 中 | 重连报"运行已中断" | M1 先于扩容落地 |
| 限流放大 | 高（未改造前） | 中（成本） | 用量统计超预期 N 倍 | M3 |
| 测试仍跑 SQLite 导致漏测 | 中 | 高 | 上线后才发现方言差异 | P2 起 CI 强制 PG |
| ETL 窗口超时 | 低 | 中 | 预计 <10 分钟，实测超 30 分钟 | 预演 + 可断点续跑 |

---

## 11. 待决策问题

1. **是否引入 Redis**：限流、跨实例缓存、SSE 事件总线都可以用它，但会增加一个组件。若只解决限流，DB 原子计数即可，建议先不引入。
2. **部署形态**：单主机多容器（当前）还是跨主机？后者必须先解决 M4/M5（文件产物与卷）。
3. **是否部署 PgBouncer**：backend 副本数 × 池大小接近 `max_connections` 时再上，初期不需要。
4. **读写分离**：当前读多写少但数据量小，暂不需要只读副本；`materialized view` 也不必要。
5. **迁移窗口**：是否可以接受周末半天停服？不可接受则需重新评估 D9（但双写仍不建议）。
6. **本地开发默认库**：迁移完成后是否彻底移除 SQLite 支持（推荐），还是保留 `LIMITUPLAB_DATABASE_PATH` 作为离线演示模式。

---

## 12. 附：SQL 方言对照

| SQLite | PostgreSQL | 出现次数 |
| --- | --- | --- |
| `?` 占位符 | `%s` | 367 处调用 |
| `INSERT OR IGNORE` | `INSERT ... ON CONFLICT DO NOTHING` | 12 处 |
| `INSERT OR REPLACE` | 已全部使用 `INSERT ... ON CONFLICT (...) DO UPDATE SET ...`，无需改写 | 0 处 SQL（仅一处注释残留） |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `bigint GENERATED ALWAYS AS IDENTITY` | 3 处 |
| `PRAGMA user_version` | `alembic_version` 表 | 3 处 |
| `PRAGMA table_info(t)` | `information_schema.columns` | 2 处 |
| `sqlite_master` | `to_regclass('public.t')` | 3 处 |
| `rowid` | 显式 `seq` 列 | 9 处 |
| `julianday(x) < julianday(y)` | `x < y`（`timestamptz`） | 2 处 |
| `?mode=ro` + `PRAGMA query_only` | `SET TRANSACTION READ ONLY` | 5 处 |
| `BEGIN IMMEDIATE` | `BEGIN` + `FOR UPDATE` / 唯一约束 / advisory lock | 26 处 |
| `connection.total_changes` | `executemany(..., returning=True)` | 1 处 |
| `sqlite3.Row` | `psycopg.rows.dict_row` | 全局 |
