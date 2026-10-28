# Windows 本地 PostgreSQL（Docker pgvector）

本地应用只使用 `backend/.env` 或进程环境中的 PostgreSQL `LIMITUPLAB_DATABASE_URL`。URL 缺失、无效或数据库不可用时明确失败，不创建或回退到 SQLite。迁移前一致性归档与只读导入工具继续保留；旧运行文件的清理状态见 [SQLite 退役记录](../docs/SQLite_Retirement.md)。真实配置、数据库目录、日志和备份均不提交 Git。

## 当前运行方式

本地业务库使用 Docker Desktop 和官方镜像 `pgvector/pgvector:pg18`：

- 容器：`limituplab-pgvector`；
- 镜像：`pgvector/pgvector:pg18`；
- 端口：仅监听 `127.0.0.1:55434`；
- 数据库：`limituplab`；
- 扩展：`vector`（已验证 0.8.7），安装在 `public` schema；
- Alembic schema：`0015_memory_embeddings`；
- 数据卷：`/var/lib/postgresql`。

`backend/.env` 中的业务 URL 指向 `127.0.0.1:55434`。密码只保存在未提交的本地配置或环境变量中。临时测试库使用同一实例上的 `limituplab_test`，禁止把 `LIMITUPLAB_TEST_DATABASE_URL` 指向业务库。

原生 PostgreSQL 18 集群 `.local/postgresql` 不再作为当前本地运行库，保留用于回滚和旧验收资料。不要删除该目录，也不要在 Docker 业务库运行期间把它当作当前数据源。原生集群的二进制、数据目录和停止方式仍由 `local_postgresql.py` 管理，但当前 `.env` 已移除 `LIMITUPLAB_LOCAL_PG_BIN` / `LIMITUPLAB_LOCAL_PG_DATA`，因此 `ensure` 只检查外部数据库连接，不启动原生集群。

## 启动与检查

先启动 Docker Desktop，确认容器存在并运行：

```powershell
docker start limituplab-pgvector
docker ps --filter name=limituplab-pgvector
```

首次创建容器时，使用官方镜像和独立密码；下面的占位符必须在本地替换，不要把真实密码写入文档或 Git：

```powershell
docker run -d --name limituplab-pgvector --restart unless-stopped `
  -e POSTGRES_DB=limituplab `
  -e POSTGRES_USER=limituplab `
  -e POSTGRES_PASSWORD=REPLACE_WITH_LOCAL_PASSWORD `
  -p 127.0.0.1:55434:5432 `
  -v limituplab-pgvector-data:/var/lib/postgresql `
  pgvector/pgvector:pg18
```

从项目根目录运行：

```powershell
# 检查外部数据库连接、目标库和 Alembic schema；不会创建数据库或升级 schema。
& backend/.venv/Scripts/python.exe backend/scripts/local_postgresql.py ensure

# 启动后端、前端、推荐刷新、记忆提取和向量回填任务。
& backend/.venv/Scripts/python.exe scripts/start_dev_detached.py --skip-data-check
```

`scripts/start_local.cmd`、`scripts/start_dev_detached.py` 和 `backend/scripts/start_backend.cmd` 均先执行数据库就绪检查。原先的数据新鲜度检查与刷新仍可正常运行；`--skip-data-check` 仅跳过联网数据更新，不跳过数据库检查。直接运行 `uvicorn` 或后台任务前，需要先执行 `ensure`。这些脚本不注册 Windows 服务，也不自动初始化、导入数据或升级 schema。

`start_dev_detached.py` 额外读取 PostgreSQL 租约 `job:memory-extraction` 和 `job:memory-embeddings`，只补启不存在的 worker。租约查询失败时直接中止启动，避免重复 worker。端口 8001 上已有后端未通过 `/ready` 的 PostgreSQL 检查时，必须先确认并退出旧进程。

就绪检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8001/ready
# 期望 status=ok、database=postgresql
```

## pgvector 与检索

迁移 `0015_memory_embeddings` 执行：

- `CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public`；
- `long_term_memories.embedding public.vector(512)`、`embedding_model`、`embedding_source_hash`、`embedding_updated_at`；
- active、未删除且有向量记录的 HNSW 部分索引。

本地 embedding 使用 `fastembed` 和 `BAAI/bge-small-zh-v1.5`，模型文件缓存在 `backend/data/memory_embeddings`，不提交 Git。首次运行会下载模型；下载失败时检索退回词法匹配，并记录 `memory_vector_status=unavailable`，不会阻断聊天。回填 worker 可单独运行：

```powershell
& backend/.venv/Scripts/python.exe backend/scripts/run_memory_embeddings.py --once
```

worker 使用租约 `job:memory-embeddings`，每批默认 100 条，源哈希或模型名变化时重算。保存向量只增加 `management_revision`，不改变内容 `revision`。

## 每日任务与日志

Windows 每日收盘计划任务使用 `backend/scripts/run_scheduled_close_loop.py --trigger scheduled`。它先执行 `ensure`，成功后才调用原收盘脚本，保留参数和退出码；数据库不可用时阻断任务。电脑重启后即使没有先启动网页应用，计划任务也会准备数据库连接；Docker Desktop 和 `limituplab-pgvector` 需要处于运行状态。

容器日志：

```powershell
docker logs --tail 200 limituplab-pgvector
```

后端与任务日志仍在 `backend/*.log`；记忆提取和向量回填分别写入 `backend/memory_extraction.log` 与 `backend/memory_embeddings.log`。审核 worker 默认输出到启动终端，可运行 `backend\.venv\Scripts\python.exe backend\scripts\run_review_worker.py --once` 手动处理一批。`status`/`ensure` 错误不会输出连接密码。

## 测试库与备份

回归使用独立 `LIMITUPLAB_TEST_DATABASE_URL`，常规回归会创建并回收自身的临时 schema。测试 schema 使用 `<schema>,public` 的 `search_path`，确保能解析 `public.vector`。首次准备测试库时可以执行：

```powershell
docker exec limituplab-pgvector psql -U limituplab -d postgres -c "CREATE DATABASE limituplab_test"
docker exec limituplab-pgvector psql -U limituplab -d limituplab_test -c "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"
```

访问相同 Docker 实例上的既有测试库时，官方镜像默认允许通过发布端口使用 SCRAM 密码连接；如果本地修改过 `pg_hba.conf`，需要保留对应规则。不要把业务库开放给不受信任来源。

历史 SQLite 导入仍可使用保留的一致性归档，目标必须是空的当前版本 PostgreSQL 数据库，导入后逐表核对计数和内容哈希。完整导入与备份恢复命令见 [PostgreSQL 部署手册](postgresql.md)。归档不接收切换后的写入，不能作为最新业务库；恢复应把 PostgreSQL 备份导入独立空库并验证。用 `postgres_admin.py backup` 创建独立备份，不能在运行中简单复制数据目录代替一致性备份。

当前工作区的长期记忆混合检索、真实 Agent 验收和测试结果记录在 [长期记忆 v2](../docs/Long_Term_Memory_v2.md)。原生 PostgreSQL 切换过程的历史证据仍见 [本地切换验收](../docs/Local_PostgreSQL_Cutover.md)。

回归不得指向业务库；审计备份和显式新闻文件缓存等辅助存储的默认目录可通过 `LIMITUPLAB_DATA_DIRECTORY` 配置，其他报告按各脚本的输出参数选择；数据目录设置不再决定数据库类型。
