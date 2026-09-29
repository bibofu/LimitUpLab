# 2026-09-29 生产数据停更事故记录

## 结果摘要

本记录时间均为北京时间。2026-09-29 恢复验收时，原始数据已补齐至 9/28，数据新鲜度检查通过，日更与备份的超时保护已部署。9/28 日更仍为 `partial`，仅剩 6 只股票的历史 20 日 K 线缺口。9/29 已错过盘前固化窗口，保留 `missed_cutoff`，没有补造当日终选。

## 原因与影响

- 9/23 16:10 启动的日更持续停留在 `running / attempt_count=1`，原始数据与特征最新日期停在 9/22。
- 进程长期低 CPU、持有网络连接，尚未完成原始数据导入。生产版本的 AKShare 涨停池、炸板池调用没有请求超时；底层 `requests.get` 也没有 `timeout`。阻塞位置符合这两处 HTTP 调用，未获取 Python 栈，因此不能进一步断言是哪一个池。
- 宿主任务在持有发布共享锁时无时限等待容器。后续日更和备份等待锁两小时后失败；等锁超时不会终止已经持锁的任务。
- 原日更还可能把原始采集 `partial/error` 与健康缓存组合误判为成功，已同步修正。

## 修复与实际部署范围

- 涨停池、炸板池及 AKShare 日 K、分时回退调用改为可终止子进程，默认超时各 60 秒。生产环境未覆盖 pool/kline 超时配置。
- 日更 watchdog 上限为 3600 秒，备份为 900 秒。超时后停止并确认实际容器状态；启动或停止状态无法确认时保留维护保护。维护标记无法持久化时继续持锁，等待恢复。
- 原始采集 `partial/error` 会保留来源错误、继续重试；耗尽重试后标记 `partial`。跳过导入的既有行为保持兼容。
- 修复提交：`098eb3a`、`fc3a492`、`f55180a`、`9cbb919`、`e74ab25`。

本次热修复仅部署到 **daily-update、backup 使用的本地镜像及宿主 `job.py`**。Web 后端、前端和推荐 worker 保持原 `v1.4.1` 镜像。

| 项目 | 实际值 |
| --- | --- |
| 热修复标签 | `hotfix-data-timeout-20260929-v2` |
| 日更与备份镜像 ID | `sha256:82859dfc3948eeccfa9972667e90a7c58714be249c06a21fc9724f3e98261056` |
| Compose 日更镜像引用 | `limituplab-backend:local` |
| 宿主 `job.py` SHA-256 | `34e5a1a42367d998edd4c1c39426d4d37a23d72b2d151528c0d17ff048dbb293` |
| 热修复留存目录 | `/var/lib/limituplab-deploy/hotfixes/data-timeout-20260929` |

## 数据恢复与验收

| 交易日 | 原始事件数 | 新增预测 |
| --- | ---: | --- |
| 2026-09-23 | 77 | 10 条 `historical_backtest` |
| 2026-09-24 | 62 | 10 条 `historical_backtest` |
| 2026-09-28 | 44 | 10 条 `historical_backtest` |

- `system-health` 的 `latest_local_trade_date`、`expected_data_date` 均为 `2026-09-28`，`data_fresh=true`。
- 当前追踪范围的 Outcome 为 `healthy`：160 条，缺失 0 条。9/28 分时缓存 44 只就绪，缺失 0 只。
- 9/28 日更唯一剩余原因是 6 只股票历史 20 日 K 线覆盖不足：`002860`、`600301`、`600825`、`600929`、`601123`、`605577`。因此不能将整体日更描述为全部成功。
- 对修复前备份进行逐表 `EXCEPT` 核验：既有 `agent_predictions`、`agent_live_prediction_snapshots`、`recommendation_prediction_finals`、`prediction_snapshot_archive`、`recommendation_intelligence_snapshots` 的修改或缺失记录均为 0。新增 live 预测为 0。
- 9/29 盘前状态按实际时间记录为 `missed_cutoff`。公开推荐接口仍通过历史展示回退返回 9/23 的 `final`，共 10 条；它不代表 9/29 新终选。
- Linux 容器内 58 项 unittest 全部通过；测试未联网、未挂载生产数据卷。
- `release.deployment_lock(timeout=0)` 成功，维护标记不存在，无遗留日更容器，推荐 worker 正常运行。
- cron 服务正常；服务器实际日更时间为工作日 16:10，备份时间为每日 03:25。

公网验证：

- `/health` 返回 HTTP 200；公开原始事件最新日期为 9/28。
- `/api/market/overview` 返回 HTTP 200，`trade_date=2026-09-28`、`limit_up_count=33`、`first_board_count=26`。
- `/api/agents/first-board-ratings` 返回 HTTP 200，`trade_date`、`data_as_of` 均为 `2026-09-28`，`snapshot_source=calculated`。

## 备份与追踪材料

- 修复前备份保留：`/var/backups/limituplab/incidents/data-stall-20260929/limituplab-20260929-103746.sqlite`。
- 最终一致性备份：`/var/backups/limituplab/limituplab-20260929-113001.sqlite`，`integrity_check=ok`。
- 恢复报告：`/app/data/data_recovery_20260929.json`。
- 恢复日志：`/var/log/limituplab/data-recovery-retry-20260929.log`。

以上数据库、生成报告和日志仅留在服务器，不纳入 Git。后续正式版本必须包含本次修复，避免发布覆盖热修复；继续检查下一次计划日更和备份的实际运行结果，并保留 6 只历史 K 线缺口的显式状态。
