# Windows 本地单实例 PostgreSQL

本地应用使用 `backend/.env` 中的 `LIMITUPLAB_DATABASE_URL`。SQLite 原文件继续保留；设置 PostgreSQL URL 后，连接失败会中止启动，不会回退到 SQLite。真实配置、数据库目录、日志和备份均不提交 Git。

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

`start_dev_detached.py` 使用 `/ready` 确认后端连接的是配置的数据库类型。如果端口 8001 已有旧 SQLite 后端，启动会要求先退出旧进程，以免误用未切换的后端。PostgreSQL 模式下推荐刷新任务使用已有数据库租约检查存活，不依赖旧 SQLite 文件锁。

就绪检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8001/ready
# 期望 status=ok、database=postgresql
```

数据库日志位于 `.local/postgresql/postgresql.log`，后端与任务日志仍在 `backend/*.log`。`status`/`ensure` 错误不会输出连接密码。管理脚本只接受当前项目 `.local/postgresql` 下的显式路径，并核对运行进程的数据目录、端口和回环监听地址；对未配置管理路径的外部 PostgreSQL 仅检查连接，拒绝执行停止操作。

切换前应停止旧后端与写入任务，使用 SQLite backup API 获取一致快照，导入到空的当前版本 PostgreSQL 数据库，并逐表核对计数和内容哈希。完整导入与备份恢复命令见 [PostgreSQL 部署手册](postgresql.md)。原 SQLite 不是自动同步副本：正式切换后的 PostgreSQL 新写入不会出现在旧文件中，回切前必须明确处理这些增量，不能直接将旧文件当作最新数据。
