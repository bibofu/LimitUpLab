# PostgreSQL 功能独立性验收

日期：2026-10-19。目标是所有现行业务、研究、维护和评测功能都可以使用 PostgreSQL，不依赖遗留 SQLite 文件。原文件和归档未删除；SQLite 兼容实现、历史迁移读取及兼容回归保留，不要求删除 Python 的 `sqlite3` 模块。

本次为 `v1.6.1` 正式切换之后的代码补齐，8 个提交已推送到远程 `main`，并随 `v1.6.2` 发布至服务器。远程 CI、生产接口及三问验收均已完成，见 [发布记录](../deploy/incidents/2026-10-19-v1.6.2-release.md)。服务器原 SQLite 继续保留，本次没有执行清理。

## 已修复的遗漏

| 功能 | 原问题 | 当前行为 |
| --- | --- | --- |
| 回撤、缩量比较及案例导出/证据检查 | 两个 CLI 默认传旧 SQLite 路径；三个入口未加载 `.env` | 默认加载配置并使用 PostgreSQL；显式 `--db` 只供历史归档，四个研究入口完整链路已验证 |
| 质量工单列表 | `group_concat(DISTINCT source)` 在 PostgreSQL 报错 | 显式选择 PostgreSQL `string_agg`；来源去重、空来源、筛选和分页契约保持 |
| 通用备份与预测时间审计 | 通用备份入口只能打开 SQLite | 默认使用配置库，PostgreSQL 生成 dump 与 SHA-256；轮换只处理自有命名且校验通过的备份对 |
| 退役 discovery 清理 | 只支持 SQLite；表已退役后重跑仍清空现行推荐表 | 默认支持 PostgreSQL；退役表缺失时只读退出，存在才先备份再事务清理，重复运行不清空新推荐 |
| 示例导入 | 未加载数据库配置，日志固定打印 SQLite 路径 | 使用所选数据库；已有事件时默认拒绝覆盖，明确传 `--replace-existing` 才允许替换演示库事件 |
| Golden 评测 | 固定 `session.sqlite`；部分默认连接可能混用业务 PG | 独立 `LIMITUPLAB_EVAL_DATABASE_URL`，每 case/trial 新 schema；业务为 PG 而评测 URL 缺失时拒绝运行 |

研究校验仍比较原查询结果的全字段指纹。业务库增长后，旧冻结 CSV 与新查询不一致会报错；重新导出或恢复相应研究快照，不把缺口用默认值填平，也不修改历史指纹来通过验证。

## 验证结果

- 完整回归：后端 **3,257 项测试、22 项子测试**通过，包含真实 PostgreSQL 临时 schema；保留 3 条既有依赖弃用提醒。前端 **243 项测试**及 TypeScript/Vite 构建通过。汇总位于忽略目录 `output/validation/20261019T083006Z-45ac6f40/summary.json`。
- 无 SQLite 连接的集成验证：将 `sqlite3.connect` 替换为立即报错，并把默认 SQLite 路径设为不存在；实际 HTTP 涨停/首板/连板/炸板、K 线、复盘、策略池、会话增改删、回答读取/SSE重放、质量反馈与管理接口，以及事务提交/回滚通过。
- 研究端到端：同样禁止 SQLite，依次完成基础冻结研究、形态比较、案例导出和证据检查；修改 PostgreSQL 中事实后，旧研究指纹被正确拒绝。
- 维护：真实 PostgreSQL 备份 CLI 生成可读归档并通过校验，未生成 SQLite；退役表缺失、重复清理、备份失败及受保护记录不变由隔离测试覆盖。没有对真实业务库执行退役清理或示例替换。
- 测试入口同时清空业务和评测 URL，再显式注入独立测试库，防止开发者本地 `.env` 或继承环境将回归指向正式库。
- 本地常驻后端已重启加载新代码：`/ready` 返回 `postgresql`，质量工单接口和 5173 前端入口均为 HTTP 200；重启前确认没有进行中的 Agent 请求。

最后通过实际 FastAPI 路由、真实模型服务与本地 PostgreSQL 数据完成以下三问，整个验收进程禁止任何 SQLite 连接，实际 SQLite 连接尝试数为 **0**。三条回答均为 `complete`，保存结果与 SSE 重放一致，原 SQLite 主文件 SHA-256 未变。

| 实际问题（2026-10-19 本地数据） | 完成耗时 | 核对 |
| --- | --- | --- |
| 收盘涨停数量，排除炸板 | 8.708 秒 | 数量与 PostgreSQL 一致 |
| 最高连板高度及对应名称、代码 | 5.461 秒 | 高度、名称、代码一致 |
| 按代码升序前三只首板及概念 | 8.549 秒 | 回答完成，前三只名称和代码一致 |

原件为 `output/postgresql-no-sqlite-live-20261019.json`，包括仓储读写与质量工单 HTTP 200 结果。此三问验证正常业务链路，不代表所有自然语言问题都能完整回答。

## Golden 独立存储

当前本地工作区已创建 `limituplab_eval`，私有 `backend/.env` 已配置对应评测 URL，配置备份保留在 `.local/postgresql/backups/`，均不提交。业务库仍为 `limituplab`。

真实模型执行 `s01_main_first` 一次，完成合成场景问答、5 次模型调用；存储为独立 PostgreSQL schema，SQLite 文件数为 0，确定性检查没有失败。该次 `--judge none`，未评估的裁判项维持 `review`，不把基础设施验证写成新的 Golden 通过率。原件为 `output/golden/postgresql-no-sqlite-20261019/`。

使用方法和存储保留边界见 [Golden 评测说明](Agent_Golden_Evaluation.md)。其他环境须单独创建评测库并配置 URL，不能照搬本地凭据。

## 文件保留与运维边界

没有删除旧 SQLite、归档副本或整个 `limituplab-data` 卷。该卷仍保存日更报告、推荐刷新报告和迁移证据。历史 SQLite 到 PostgreSQL 的导入工具必须读取指定 SQLite 源，这是历史数据工具的用途，不是 PostgreSQL 业务运行依赖。

数据库维护命令默认读取现有配置，例如：

```powershell
& backend/.venv/Scripts/python.exe backend/scripts/backup_database.py --output-dir output/backups/postgresql
```

本地需将 PostgreSQL 18 的 `bin` 目录加入 PATH，或在带客户端工具的后端容器执行；生产定时备份继续使用已安装的 `job.py backup` 入口。不要用示例导入、退役清理或真实收盘采集作为只读健康检查。
