# Windows 本地单实例 PostgreSQL

本地应用只使用 `backend/.env` 或进程环境中的 PostgreSQL `LIMITUPLAB_DATABASE_URL`。URL 缺失、无效或数据库不可用时明确失败，不创建或回退到 SQLite。迁移前一致性归档与只读导入工具继续保留；旧运行文件的清理状态见 [SQLite 退役记录](../docs/SQLite_Retirement.md)。真实配置、数据库目录、日志和备份均不提交 Git。

本地配置模板为 `backend/.env.local-postgresql.example`。项目采用已初始化的 PostgreSQL 18 集群，二进制位于 `.local/postgresql/pgsql/bin`，数据位于 `.local/postgresql/data`，仅监听 `127.0.0.1:55433`。使用独立业务用户和 SCRAM 密码；密码含 URL 保留字符时必须百分号编码。模板不能直接替换包含其他设置的完整 `.env`。

从项目根目录运行：

```powershell
# 检查状态；不会自动启动或修改 schema。
& backend/.venv/Scripts/python.exe backend/scripts/local_postgresql.py status

# 启动已配置的本地集群，并验证认证、目标数据库和 Alembic schema。
& backend/.venv/Scripts/python.exe backend/scripts/local_postgresql.py ensure

# 启动后端、前端和本地推荐刷新任务；首次切换后先做独立数据验收。
& backend/.venv/Scripts/python.exe scripts/start_dev_detached.py --skip-data-check

# 先退出后端和后台任务，再停止项目自己的 PostgreSQL。
& backend/.venv/Scripts/python.exe backend/scripts/local_postgresql.py stop
```

`scripts/start_local.cmd`、`scripts/start_dev_detached.py` 和 `backend/scripts/start_backend.cmd` 均先执行数据库就绪检查。原先的数据新鲜度检查与刷新仍可正常运行；`--skip-data-check` 仅跳过联网数据更新，不跳过数据库检查。直接运行 `uvicorn` 或后台任务前，需要先执行 `ensure`。这些脚本不注册 Windows 服务，也不自动初始化、导入数据或升级 schema。

Windows 每日收盘计划任务使用 `backend/scripts/run_scheduled_close_loop.py --trigger scheduled`。它先执行 `ensure`，成功后才调用原收盘脚本，保留参数和退出码；数据库不可用时阻断任务。当前已有 `LimitUpLab-DailyCloseLoop` 已更新为此入口并恢复启用，工作日 16:00、原权限与重试设置保持。电脑重启后即使没有先启动网页应用，计划任务也会准备项目本地数据库。

`start_dev_detached.py` 使用 `/ready` 确认后端返回 PostgreSQL。端口 8001 上已有的服务未通过该检查时，必须先确认并退出旧进程。推荐刷新任务通过数据库租约检查存活。

就绪检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8001/ready
# 期望 status=ok、database=postgresql
```

数据库日志位于 `.local/postgresql/postgresql.log`，后端与任务日志仍在 `backend/*.log`。`status`/`ensure` 错误不会输出连接密码。管理脚本只接受当前项目 `.local/postgresql` 下的显式路径，并核对运行进程的数据目录、端口和回环监听地址；对未配置管理路径的外部 PostgreSQL 仅检查连接，拒绝执行停止操作。

历史 SQLite 导入仍可使用保留的一致性归档，目标必须是空的当前版本 PostgreSQL 数据库，导入后逐表核对计数和内容哈希。完整导入与备份恢复命令见 [PostgreSQL 部署手册](postgresql.md)。归档不接收切换后的写入，不能作为最新业务库；恢复应把 PostgreSQL 备份导入独立空库并验证。

当前工作区已完成正式切换，数据核对、实际 HTTP 读写、三问、重启及恢复证据见 [本地切换验收](../docs/Local_PostgreSQL_Cutover.md)。保留 `.local/postgresql`，其中包含当前业务数据；不要把它当作可随时删除的测试输出目录。用 `postgres_admin.py backup` 创建独立备份，不能在运行中简单复制数据目录代替一致性备份。

回归使用独立 `LIMITUPLAB_TEST_DATABASE_URL`，真实 Golden 使用独立 `LIMITUPLAB_EVAL_DATABASE_URL`。两个测试用途均不得指向业务库；常规回归会创建并回收自身的临时 schema。审计备份和显式新闻文件缓存等辅助存储的默认目录可通过 `LIMITUPLAB_DATA_DIRECTORY` 配置，其他报告按各脚本的输出参数选择；数据目录设置不再决定数据库类型。
