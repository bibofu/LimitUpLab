# 本地单实例 PostgreSQL 切换验收

> 历史记录：下文描述 2026-10-19 切换时的状态，不作为当前配置或启动指南。当前本地运行方式见 [本地 PostgreSQL 手册](../deploy/local-postgresql.md)，SQLite 退役边界见 [退役验收](SQLite_Retirement.md)。

日期：2026-10-19。范围仅为当前 Windows 工作区，不涉及远程生产环境或多实例扩容。

## 2026-10-19 切换后的运行状态

- 本地 `backend/.env` 已选择 PostgreSQL，目标为 `127.0.0.1:55433/limituplab`。未配置 URL 的其他工作区仍可使用 SQLite。
- PostgreSQL 18.6 使用独立非超级用户业务账号及 SCRAM 密码认证，仅监听回环地址。数据目录为 `.local/postgresql/data`，二进制位于 `.local/postgresql/pgsql/bin`。
- 后端运行于 `127.0.0.1:8001`，前端运行于 `127.0.0.1:5173`；`/ready` 返回 `status=ok, database=postgresql`。
- 推荐刷新进程已恢复且确认仅一个实际 worker；`LimitUpLab-DailyCloseLoop` 已恢复启用，工作日 16:00、Limited 权限及原重试配置保留，入口增加 PostgreSQL 就绪检查。没有提前运行收盘采集或伪造当天预测。
- 原 `backend/data/limituplab.sqlite` 保留。它是切换前的数据，不再接收本地正常业务的新写入；没有双写。
- `.env`、密码、原配置备份、数据库目录及生成报告均未提交。迁移代码已从 `codex/postgresql-migration` 快进合并到本地 `main`，当前工作区已切换到 `main`。

## 最新数据迁移

暂停旧后端、推荐刷新进程和日更计划任务后，用 SQLite backup API 创建新的事务一致性副本，再导入新建的 PostgreSQL 正式本地库。此次没有复用上一次的演练数据库。

| 检查 | 结果 |
| --- | --- |
| 源数据 | 41 张在用业务表，共 691,526 行 |
| PostgreSQL Schema | Alembic `0003_single_champion`，45 张业务和协调表 |
| 全量迁移 | 每表行数、全字段内容哈希核对通过 |
| 原 SQLite | 迁移及问答验证后，原数据库主文件 SHA-256 均未改变 |
| 一致性副本 | `.local/postgresql/backups/sqlite-before-cutover/limituplab-20261019-143031.sqlite` |
| 遗留数据 | 5 张旧表共 6 行仍保留在原 SQLite；不属于现行业务表 |
| 迁移耗时 | 27.198 秒，包含一致性备份、检查、Schema 和导入核对 |

源库的最新交易日为 2026-10-16。验收发生于 2026-10-19 收盘前，健康检查的期望数据日同样为 2026-10-16，`data_fresh=true`；没有把未收盘数据补成当日收盘结果。

迁移报告：`output/local-postgresql-migration-report.json`。切换和原文件核对记录：`output/local-postgresql-cutover.json`。

## 单实例读写与恢复验收

1. **业务数据与仓储读取**：14 张关键业务表再次全内容对拍；实际读取涨停事件、首板特征、日 K、分钟 K、预测、Outcome、历史快照与 Champion 策略。没有重算或覆盖历史预测。
2. **事务写入**：用独立 UUID 的会话和缓存验证提交、跨连接读取、更新、删除、缓存过期和事务回滚；探针数据已清理。
3. **实际 HTTP**：验证就绪、涨停/首板/连板/炸板列表、个股事件和 K 线、历史复盘、推荐快照、会话增改删，并通过 5173 的真实前端代理核对返回内容。
4. **实际 Agent 三问**：经运行中的 8001 后端、既有真实模型服务和工具完成；答案均为 `complete/answered`，持久化与 SSE 重放一致。
5. **重启持久化**：确认 PostgreSQL 停止时 PID 文件消失、重启后进程 PID 改变，后端重新启动。三条新回答和全部会话消息仍一致，重启后新增和删除会话成功；关键业务表指纹仍匹配。
6. **切换后备份恢复**：创建包含三条新回答的 PostgreSQL 自定义格式备份，核对 SHA-256 并恢复到独立空库。14 张业务表指纹和三条新回答再次核对通过。

| 实际问题 | 核对结果 | 完成耗时 |
| --- | --- | --- |
| 2026-10-16 收盘涨停数量，排除炸板 | 59 只，与 PostgreSQL 结构化记录一致 | 9.392 秒 |
| 同日最高连板和股票 | 11 板，新华传媒（600825） | 5.901 秒 |
| 同日按代码升序的前三只首板及概念 | 000099、000420、000573；名称、概念逐项一致 | 8.812 秒 |

实际问答报告：`output/local-postgresql-http-verification.json`。重启记录：`output/local-postgresql-restart.json`。读写验收：`output/local-postgresql-verification.json` 与 `output/local-postgresql-verification-after-restart.json`。备份恢复报告：`output/local-postgresql-backup-verification.json`，其中记录本地备份路径和恢复校验库名。

最终完整后端回归已通过 **3,174 项测试、22 项子测试**，保留 3 条已有依赖弃用提醒，包含计划任务数据库启动入口。测试在独立 `limituplab_test` 库的临时 schema 和临时 SQLite 上执行，不能写入正式本地库。日志为 `output/local-postgresql-pytest.log`，JUnit 为 `output/local-postgresql-pytest.xml`。

本地健康检查返回 `healthy`。既有涨停后研究缓存覆盖率为 231/237，仍如实显示缺口；推荐页保留原有盘前固化缺口，不借数据库切换回填不可重现的盘前事实。

## 日常使用

从项目根目录运行以下入口，会先启动并检查已配置的本地 PostgreSQL，再启动应用：

```powershell
& backend/.venv/Scripts/python.exe scripts/start_dev_detached.py --skip-data-check
```

`--skip-data-check` 仅跳过联网更新；省略它可保留原有数据新鲜度检查流程。数据库停启、配置模板及错误处理见 [本地操作手册](../deploy/local-postgresql.md)。

实际任务恢复记录为 `output/local-postgresql-task-after.json`；数据库和后端保持运行。本轮没有扩展多实例功能。

快进合并并切换到 `main` 后，已再次验证 PostgreSQL 连接和 Schema、后端 `/ready` 与 `/health`、前端及 API 代理。14 张关键业务表内容指纹、8 类仓储读取、会话与缓存写入、事务回滚及实际 HTTP 会话增改删均通过，验证数据已清理。原 SQLite 与迁移前一致性备份的 SHA-256 均与切换记录一致。此次复核报告为 `output/local-postgresql-main-verification.json`。

SQLite 只作为保留数据与兼容入口。正式切换后的新会话、缓存、运行记录和日更写入以 PostgreSQL 为准；不得直接删掉 URL 后把旧 SQLite 当作最新库。需要回退时，先停写并处理切换后的新增数据。

## 前置迁移演练与资料归档

2026-10-19 正式切换前，在独立 PostgreSQL 演练库验证了 41 张业务表、691,526 行的导入；四张协调表为空，45 张表在独立备份恢复后再次核对。源 SQLite 一致性副本为 273,821,696 字节，导入前后 SHA-256 不变。逐表结果以保留的原始报告为准：

| 资料 | 本地路径 |
| --- | --- |
| 源一致性副本与导入核对 | `output/pg-migration-source.sqlite`、`output/pg-migration-report.json` |
| 已验证备份及校验值 | `output/pg-migration-rehearsal-verified.dump`、同名 `.sha256` |
| 独立恢复逐表核对 | `output/pg-restore-verification.json` |
| 演练真实问答 | `output/pg-agent-smoke-report.json` |
| 当时全套回归 | `output/validation/20261019T071057Z-4c654967/summary.json` |

2026-11-01 清理时保留上述资料，重新核对备份 SHA-256；仅移除已正常关闭的 `output/postgresql-runtime/test-cluster` 演练目录及重复安装产物，服务日志保留。`.local/postgresql` 和业务数据库不在清理范围。

早期 `PostgreSQL_Migration_Plan.md` 的重复状态说明和失效操作步骤已移除；原文仍可通过 `git show 70e0c42:docs/PostgreSQL_Migration_Plan.md` 查阅。Schema 基线与数据语义见 [迁移说明](../backend/migrations/README.md)，当前操作见 [部署与恢复手册](../deploy/postgresql.md)，生产切换证据见 [生产验收](../deploy/incidents/2026-10-19-postgresql-cutover.md)。数据库切换、全局写锁优化与多实例生产验收是不同事项，历史单实例验证不证明后两者完成。
