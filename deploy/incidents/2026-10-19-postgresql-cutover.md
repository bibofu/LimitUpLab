# v1.6.1 生产 PostgreSQL 切换验收

日期：2026-10-19，Asia/Shanghai。范围为现有服务器的单实例数据库切换，原 SQLite 文件、数据卷和历史备份全部保留；没有扩展 API 副本或启用额外评估 worker。

## 发布与当前状态

- 发布标签：`v1.6.1`，提交 `cb5adaa998bac36cca06e995fce75bc2336345e9`。标签固定在应用发布提交，后续运维文档提交不移动标签。
- [GitHub Actions 发布流水线](https://github.com/bibofu/LimitUpLab/actions/runs/39214960610) 的验证及部署全部成功。发布前本地完整验证为后端 3,214 项、22 项子测试，前端 243 项及构建通过。
- PostgreSQL 18.6，Alembic `0003_single_champion`。业务账号不是超级用户，也没有创建数据库或角色权限。数据库端口没有映射到宿主机。
- 15:25 进入维护并停写；15:30 已完成切换和公网验收、退出维护。后端、前端、推荐刷新进程均在运行，实际镜像与发布提交匹配；后端 `/ready` 返回 `status=ok, database=postgresql`。
- [生产网站](https://limituplab.tech/) 首页、`/health`、首板列表和复盘快照经公网 HTTPS 均返回 200。

## 最新数据迁移与原 SQLite 保留

先在独立 PostgreSQL 演练库完成导入、核对和读写验证。正式切换持有发布/日更/备份共享锁，阻止新的公网请求，等待已有 Agent 运行结束，停止全部在用 SQLite 写入进程，并确认没有运行中的容器继续挂载原数据卷。

随后用 SQLite backup API 创建新的事务一致性副本，完成完整性和外键检查，再导入空的正式 PostgreSQL 库：

| 检查 | 结果 |
| --- | --- |
| 在用源数据 | 41 张业务表，740,731 行；源库最新交易日为 2026-10-16 |
| 目标表 | 41 张原业务表逐表行数与全字段内容哈希一致；4 张新协调表确认为空 |
| 原 SQLite 大小 | 838,086,656 字节 |
| 原 SQLite SHA-256 | `78cbb6192e915795adc00ca3547055de1eeffdd5550d4cb269e2c8323cb91333`，迁移及验收后未变 |
| 一致性副本 SHA-256 | `ef9b248b9409616f1e38b861ad75b26f7d0137285dbed7884b4b4069112e816c` |
| 遗留表 | 6 张共 6 行保留在原 SQLite；未删除或伪装为在用业务数据 |

原文件位于 `limituplab-data` 卷内的 `/app/data/limituplab.sqlite`。一致性副本为服务器 `/var/backups/limituplab/deployments/postgresql-cutover-20261019/source.sqlite`。6 张遗留表为 `auction_final_recommendation_snapshots`、`auth_challenges`、`auth_sessions`、`user_identities`、`users`、`first_board_discovery_snapshots`；只有最后一张有 6 行，其余为空。

源文件与一致性副本的物理文件哈希不要求相同；迁移核对的是逐表全字段内容。没有重算历史预测、补造盘前事实或提前运行当天收盘采集。

## 读写、Agent 与恢复验证

1. 14 张关键业务表完整内容对拍、8 类仓储读取通过；独立 UUID 探针完成会话和缓存的提交、跨连接读取、更新、删除及回滚，探针数据已清理。
2. 实际 HTTP 验证涨停、首板、连板、炸板列表与 PostgreSQL 查询一致，复盘、推荐、K 线返回正常；独立会话创建、改名、读取、列表与删除通过。
3. 真实模型服务完成以下三问；均收到完整 SSE 回答、`task_status=complete`，保存结果与 SSE 重放一致。

| 问题 | 核对范围 | 耗时 |
| --- | --- | --- |
| 2026-10-16 收盘涨停数量，排除炸板 | 回答数量与 PostgreSQL 查询一致 | 10.292 秒 |
| 同日最高连板及股票 | 高度、名称和代码与 PostgreSQL 一致 | 12.064 秒 |
| 同日按代码升序前三只首板的代码、名称、概念 | 完成回答，前三只代码及名称与 PostgreSQL 一致 | 9.993 秒 |

停止应用并实际重启 PostgreSQL，再启动应用；三条回答持久化哈希及 SSE 重放再次通过。随后生成 `postgresql.dump`，核对 SHA-256 并恢复到独立空库 `limituplab_restore_20261019`；14 张关键表、仓储读写和三条新回答哈希再次核对通过。

切换备份位于 `/var/backups/limituplab/deployments/postgresql-cutover-20261019/postgresql.dump`，SHA-256 为 `b83435c416309fce7aba60aca2451c3b6d94925a00a7c27ab9492e5422df6375`，同目录保留 `.sha256` 文件。恢复演练不覆盖正式数据库。

## 后续发布、日更与备份

已安装更新后的 `/usr/local/lib/limituplab/release.py` 与 `job.py`。`/var/lib/limituplab-deploy/database.json` 和 `current.json` 均明确选择 PostgreSQL；发布与计划任务读取两份环境文件和 Compose overlay，核对运行引擎，配置缺失或模式冲突时停止，禁止静默退回 SQLite。

- 日更保留服务器原有工作日 **16:10** 调度；已通过实际 `daily-update` 服务的只读数据库探针确认使用 PostgreSQL，没有为验收提前执行收盘数据采集。仓库 cron 模板的默认时间与该服务器现有配置可不同。
- 推荐刷新保持单个运行进程。后续标签发布继续使用固定版本镜像和 PostgreSQL 备份/Schema 检查。
- 每日 **03:25** 备份入口已切换为 PostgreSQL；手动运行相同 `job.py backup` 成功，归档结构和 SHA-256 通过，退出码 0。
- 本次日常备份为 `/var/backups/limituplab/postgresql/limituplab-pg-20261019T073021Z-9d2151a8bf974d9abddb7bcd24da6729.dump`，SHA-256 为 `7e8312d00472adc9686dcca701c9d36fb228a13877a8cc9dd2286deb0bec63f6`。轮换仅管理该目录的完整、有效备份对，保留最近 14 份，本次删除 0 份。

原 SQLite 和切换专属备份不参与 PostgreSQL 日常轮换。SQLite 是切换前保留数据，不再接收正常业务新写入，没有双写；直接移除数据库 URL 会丢失对新数据的访问，不能作为回退流程。需要恢复时优先将 PostgreSQL 备份恢复到独立库后验证。

## 证据与边界

服务器切换记录及最终检查位于 `/var/lib/limituplab-deploy/postgresql-cutover-20261019/`。导入、业务验证、HTTP/Agent 与恢复核对报告位于保留数据卷的 `/app/data/backups/postgresql-production-*.json`；真实配置、凭据、原始数据库和生成报告不提交到 Git。

本次验证证明单实例切换、即时读写和备份恢复完成。多实例分流、故障接管和并发吞吐尚未验收；异地备份、WAL 归档与 PITR 仍为后续工作。运维命令见 [部署手册](../README.md) 和 [PostgreSQL 恢复手册](../postgresql.md)。
