# SQLite 运行支持退役

> 架构更新（2026-10-24）：SQLite 迁移/归档脚本、`app/storage/migration.py` 和相关测试已删除。本文为历史验收记录；运行时只使用 PostgreSQL。

日期：2026-10-21。范围为当前代码和 Windows 本地工作区；本次不发布生产，不删除历史 SQLite 一致性归档或迁移工具。

## 改造边界

- 应用、研究、维护和启动入口要求 PostgreSQL URL；缺配置明确失败。辅助报告和显式文件缓存通过独立数据目录定位。
- 仓储采用数据库无关类名和 `database_url` 参数。移除 SQLite 运行连接、WAL 重试、请求时建表和方言分支；数据库结构仍由 Alembic 管理。
- 回归使用独立 `LIMITUPLAB_TEST_DATABASE_URL` 和 `LIMITUPLAB_TEST_EVAL_DATABASE_URL`，为测试创建临时 schema；实际 Golden 评测使用独立 `LIMITUPLAB_EVAL_DATABASE_URL`。缺少测试配置直接失败，拒绝业务库、实际评测库及 URL 路由覆盖。
- 保留 `migrate_sqlite_to_postgres.py`、`app/storage/migration.py`、冻结 schema 清单和历史迁移测试；`archive_sqlite.py` 提供显式历史源一致性备份。它们处理历史 SQLite 源，不是运行时数据库支持。
- 原库运行文件仅在服务状态确认、归档哈希核对、回归和真实三问通过后清理。保留 `.local/postgresql`、生产数据卷、迁移报告及历史 Golden 输出。

## 验收记录

本次在 Windows、Python 3.13.14 和 PostgreSQL 18.6 完成实际验证。源码提交前运行全套；新增隔离保护与历史保留断言后，再执行最终全套。工作流同时更新 Windows/Linux 独立测试库配置；未推送或宣称远程 CI、生产发布完成。

| 检查 | 本次结果 |
| --- | --- |
| 服务状态和 PostgreSQL readiness | 开始时本地服务停止；恢复后确认 PG，代码完成后重新加载后端与刷新 worker；8001 后端、5173 前端与代理通过 |
| 旧库及迁移前一致性归档核对 | 原库和归档 SHA-256 均与切换记录一致；历史归档 `integrity_check=ok`，未修改其内容 |
| 后端完整回归 | **3,307 项、22 项子测试通过，0 跳过**；保留 3 条既有依赖弃用提醒 |
| 前端测试和构建 | **243 项通过**；TypeScript/Vite 构建通过，保留既有 chunk 大小提醒 |
| 真实模型三问、持久化和 SSE 重放 | 明确口径三问全部 `complete`，答案逐项对照 PG；保存、GET 和 SSE 重放一致 |
| 不可变记录 | 预测、live 快照、归档、终选快照与评分策略五表全字段指纹在实际验收前后相同 |
| 新备份与恢复 | 新 PG dump 校验通过；恢复到新建独立库后，五表指纹与三条新回答完全一致；临时恢复库已移除 |
| 原库运行文件清理及归档保留 | 独占打开确认无占用、WAL 为零后，仅删除 `backend/data/limituplab.sqlite`、`-wal`、`-shm`；归档保留；删除后接口和三条回答重放通过，文件未重新生成 |

最终全套报告为 `output/validation/20261021T050818Z-c276fce8/summary.json`。运行与清理证据位于 `output/sqlite-retirement/`：`live-http-verification.json`、`backup-restore-verification.json`、`cleanup.json`。生成报告和数据库未提交 Git。

历史归档继续位于 `.local/postgresql/backups/sqlite-before-cutover/limituplab-20261019-143031.sqlite`。本次最新 PG 备份为 `.local/postgresql/backups/sqlite-retirement/limituplab-pg-20261021T051303Z-2f0c3e2ec9174ef6a8d3df09db6a4bb7.dump`，同目录保留 SHA-256 文件。

## 实际问答与边界

实际问题使用本地最新已收盘数据日 2026-10-20，经运行中的 HTTP 服务、既有真实模型和工具执行，未替换模型或鉴权：

| 问题 | 核对结果 | 耗时 | 运行 ID |
| --- | --- | --- | --- |
| 当天收盘仍封住涨停的数量，盘中开板后回封也计入 | 52 只，与 PG 记录一致 | 6.599 秒 | `run_16536a82841a479292433f58b0493d30` |
| 最高连板高度及名称、代码 | 4 连板，吉大正元 003029 | 6.501 秒 | `run_527423e719e7488188557530db104f9b` |
| 首板按代码升序前三只及概念 | 001239、001283、001288；名称及概念一致 | 7.745 秒 | `run_21cb13ea70cc480da1d9900082764e44` |

初次验收的“排除炸板”被模型解释为排除盘中开板后回封，原回答不计准确通过，保存在 `initial-ambiguous-http-verification.json`，并登记 [BC-213](../badCase.md)。本次明确验收提问后重验；未修复原自然语言歧义，也不把数据库退役验收当作全问题问答质量保证。

测试迁移保留业务契约：SQLite 专属 WAL/自动建表/文件锁断言替换为 PG 超时、外键事务、Alembic 版本拒绝与共享租约检查；历史 SQLite 导入验证继续运行。退役清理回归为五张受保护表各种入实际记录，并全字段对拍，覆盖重复清理、无操作和备份失败。

2026-10-19 的迁移、恢复和 PostgreSQL 独立性结果继续保存在 [本地切换验收](Local_PostgreSQL_Cutover.md)、[功能独立性验收](PostgreSQL_Independence.md) 和生产发布记录中。这些历史记录不替代本次改造后的验证。
