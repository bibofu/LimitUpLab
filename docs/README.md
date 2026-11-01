# 文档与本地资料索引

维护日期：2026-11-01。实现与操作说明优先从下表进入；历史验收按记录日期解释，旧版本的配置、测试数量和接口不能视为当前状态。

## 实现与操作

| 主题 | 入口 |
| --- | --- |
| 代码阅读与 Agent 调用链 | [代码阅读指南](code-reading-guide.md)、[LangChain / LangGraph 集成](LangChain_Integration.md) |
| Runtime、恢复与记忆 | [重构边界](Memory_Runtime_Refactoring.md)、[记忆实现](Memory_Implementation.md)、[长期记忆 v2](Long_Term_Memory_v2.md) |
| 研究口径 | [预测时间契约](Prediction_Time_Contract.md)、[整理与回撤研究](Consolidation_Strategy_V1.md) |
| 反馈与观测 | [线上反馈和 Badcase](Online_Observability_and_Badcase.md)、[Langfuse](Langfuse_Integration.md) |
| 数据库与部署 | [本地操作](../deploy/local-postgresql.md)、[部署与恢复](../deploy/postgresql.md)、[Schema 迁移](../backend/migrations/README.md) |
| 项目展示 | [实现与面试准备](Project_Implementation_Interview_Guide.md)，历史章节结合架构更新提示阅读 |

## 必要历史记录

- 版本里程碑：[V1](V1_Milestone.md)、[V1.1](V1.1_Milestone.md)、[V1.2](V1.2_Milestone.md)、[V1.3](V1.3_Milestone.md)、[V1.4](V1.4_Milestone.md)、[V1.5](V1.5_Milestone.md)。保留各版本范围、验收结果与未完成事项。
- 数据库演进：[本地切换与前置演练](Local_PostgreSQL_Cutover.md)、[PostgreSQL 独立性](PostgreSQL_Independence.md)、[SQLite 退役](SQLite_Retirement.md)。各次验收的原始结果分别保留，不合并成新的通过率。
- [代码质量与清理记录](code-quality-audit.md)、[Agent Badcase](../badCase.md)、[Memory Badcase](../memory_badcase.md)。

历史页面引用的已退役源码与文档链接固定到对应 Git 提交，指向当时实现。当前功能入口以上方实现说明为准。

早期迁移计划的必要证据已合并到本地切换记录；旧操作步骤可从 Git 历史查阅。图标草稿已完成选择，正式矢量文件位于 `frontend/public/favicon.svg`，方案对比图保留在本地 `output/icon-options/preview.png`。

## 本地研究与验证资料

以下目录被 Git 忽略，包含原始证据，不随文档提交：

| 资料 | 保留位置与边界 |
| --- | --- |
| 研究数据与报告 | `output/research/`、`output/ningde-analysis/`；保留数据表、统计结果、报告与复现脚本 |
| Agent 与性能验证 | `output/agent-trace-20261022/`、`output/agent-optimization-20261023/`、`output/production-agent-trace-20261023/`、`output/capacity/`；保留不同实验与失败证据 |
| 记忆与重启恢复 | `output/memory-refactoring/`、`output/memory-referents/`、`tmp/` 中的 Memory 验证原件 |
| 回归、发布与数据库切换 | `output/validation/`、`output/deployment/`、`output/sqlite-retirement/` 及本地切换记录列出的报告、源归档与备份 |
| 本次清理审计 | `output/cleanup-20261101/`、`output/cleanup-review-20261101/`、`output/cleanup-ci-20261101/`、`output/cleanup-local-postgresql-20261101/`；保留删除原因、重复文件映射、哈希和验证结果 |

Agent 优化实验的 `first-pass/shared-runs.json` 记录与 `runs/` 共用的九份相同 run；原有两批索引、比较结果及首轮独有记录保留，`compare_runs.py` 按校验值读取共用文件。其他重复截图和记录的原路径到保留路径映射见本次清理清单。

清理不以日期作为唯一依据：不同实验的原始记录、失败样本、源数据、备份校验对及独有未提交草稿均保留。`output/ci-postgresql/` 已于 2026-11-01 单独完成核查：无进程、端口或运行配置引用；冷副本恢复后，两个测试库及 `postgres`、`template1` 均无用户关系和大对象，未发现准备事务或复制槽。原目录及检查副本已清理，原始日志、目录清单和只读核查结果保存在 `output/cleanup-ci-20261101/`。

`.local/postgresql/data` 也已单独退役：旧业务、测试及恢复演练库均已备份并完成实际恢复、逐表内容与结构核验，备份保存在 `.local/postgresql/backups/native-retirement-20261101/`。工具程序、不同时间点的历史备份和迁移源长期归档保留；SQLite 源的重复 `output` 副本已改为路径映射记录。当前 Docker 库及运行配置保持原状，详见[本地 PostgreSQL 手册](../deploy/local-postgresql.md)。
