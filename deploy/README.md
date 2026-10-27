# LimitUpLab 香港服务器部署

这套配置面向单台 Linux 服务器上的公开 Demo：宿主机 Nginx 负责域名和 HTTPS，Docker 中的 Nginx 提供 React 静态文件并把 `/api` 转发给单 worker FastAPI。应用仅支持 PostgreSQL，使用 `docker-compose.postgresql.yml` overlay 和独立命名卷 `limituplab-postgres`；应用报告及历史迁移归档保存在 `limituplab-data`。

生产发布、日更和备份统一使用已审查安装的 `release.py` / `job.py`，按明确的数据库模式执行。本文是操作手册；一次切换是否完成，应以对应的迁移、读写、恢复和上线验收记录为准。

## 1. 准备服务器

以下命令以 Ubuntu 24.04 和项目目录 `/opt/LimitUpLab` 为例：

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2 git nginx certbot python3-certbot-nginx
sudo systemctl enable --now docker nginx
sudo timedatectl set-timezone Asia/Shanghai
sudo mkdir -p /opt/LimitUpLab /var/log/limituplab
sudo chown -R "$USER":"$USER" /opt/LimitUpLab
```

克隆仓库后进入项目目录：

```bash
git clone git@github.com:bibofu/LimitUpLab.git /opt/LimitUpLab
cd /opt/LimitUpLab
cp .env.production.example .env.production
chmod 600 .env.production
```

编辑 `.env.production`：

- 把 `example.com` 替换为真实域名。
- 填入新的 `DEEPSEEK_API_KEY`。
- 填入 `HITHINK_FINANCE_API_KEY`；不把凭据写入镜像或 Git。
- 使用 `openssl rand -hex 32` 生成 `LIMITUPLAB_SESSION_SECRET`。生产环境缺少足够长度的密钥时，后端会拒绝启动。
- 再生成一个不同的 `LIMITUPLAB_ADMIN_KEY`，用于内部诊断、运行轨迹和评分策略管理接口。
- 没有同花顺凭据时可设置 `INSTALL_HITHINK_FINANCE=false`，相关工具会按现有逻辑降级或明确报错。

浏览器首次访问时会得到签名的 HttpOnly 匿名访客 Cookie。会话、消息和 Agent 运行记录均按该匿名身份隔离；清除浏览器 Cookie 后会成为新的匿名用户，无法继续访问原会话。

内部接口必须携带 `X-LimitUpLab-Admin-Key`，例如：

```bash
curl --fail -H "X-LimitUpLab-Admin-Key: $LIMITUPLAB_ADMIN_KEY" \
  https://你的域名/api/agents/system-health
```

评分策略、数据健康、系统健康、日更状态、预测审计、因子诊断和 Agent 运行轨迹均属于管理员接口。生产环境同时关闭 FastAPI 的 `/docs`、`/redoc` 和 `/openapi.json`。

## 2. 构建并启动应用

PostgreSQL 首次部署或从 SQLite 切换，按 [PostgreSQL 部署与恢复手册](postgresql.md) 完成独立配置、数据库准备、停写备份、导入与验证，再启动应用。标签发布器不自动导入 SQLite 数据。保留至少一份校验过的 SQLite 一致性归档及迁移报告；不要删除仍保存应用报告的数据卷。

PostgreSQL 模式固定加载两份环境文件及两份 Compose 文件；例如查看容器状态：

```bash
docker compose --project-name limituplab --env-file .env.production --env-file .env.postgresql \
  -f docker-compose.yml -f docker-compose.postgresql.yml ps
docker exec limituplab-backend-1 python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/ready', timeout=10).read().decode())"
curl --fail http://127.0.0.1:8080/health
```

`/ready` 必须返回 `status=ok, database=postgresql`。它由后端容器内访问；前端代理的页面 200 或 `/health` 不能替代数据库就绪检查。数据库端口不映射到宿主机；应用端口只绑定到 `127.0.0.1:8080`，不要把 PostgreSQL 或 FastAPI 的 `8001` 端口直接暴露到公网。

新安装也必须配置 PostgreSQL，构建及启动命令为：

```bash
docker compose --env-file .env.production --env-file .env.postgresql -f docker-compose.yml -f docker-compose.postgresql.yml build
docker compose --env-file .env.production --env-file .env.postgresql -f docker-compose.yml -f docker-compose.postgresql.yml up -d backend frontend recommendation-refresh memory-extraction memory-embeddings
docker compose --env-file .env.production --env-file .env.postgresql -f docker-compose.yml -f docker-compose.postgresql.yml ps
curl --fail http://127.0.0.1:8080/health
```

已有 SQLite 历史数据使用独立归档和导入工具迁移至空 PostgreSQL 库，不把文件复制当作 PostgreSQL 导入。

首次部署缺少行情数据时，完成第 4 节的任务入口安装和数据库选择后，再通过受锁保护的日更入口采集：

```bash
sudo python3 /usr/local/lib/limituplab/job.py daily-update
```

日更会真实采集并写入数据，不能当作无写入的就绪探针，也不能把未到收盘的数据补成当日收盘结果。

## 3. 配置域名与 HTTPS

先在域名服务商添加 `A` 记录指向服务器公网 IP。解析生效后：

```bash
sudo cp deploy/nginx/limituplab-site.conf.example /etc/nginx/sites-available/limituplab
sudo sed -i 's/YOUR_DOMAIN/你的域名/g' /etc/nginx/sites-available/limituplab
sudo ln -s /etc/nginx/sites-available/limituplab /etc/nginx/sites-enabled/limituplab
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d 你的域名 -d www.你的域名
```

只有已经配置 `www` DNS 记录时才保留第二个 `-d` 参数。Certbot 会修改宿主机 Nginx 配置并安装自动续期任务。完成后检查：

```bash
curl --fail https://你的域名/health
curl -I https://你的域名
sudo certbot renew --dry-run
```

宿主机和容器内两层 Nginx 都关闭了 Agent SSE 路径的代理缓冲，避免流式回答积压后一次性显示。

## 4. 安装每日收盘任务

采用自动部署时，先按 [标签自动部署说明](Tag_Deployment.md) 安装
`/usr/local/lib/limituplab/release.py`、`job.py` 和共享锁；下列 cron
文件通过该入口执行，避免日更、备份与部署同时切换版本。

数据库选择记录在 `/var/lib/limituplab-deploy/database.json` 和 `current.json` 的 `engine` 中。首次 PostgreSQL 切换需在同一共享锁和维护保护下同步记录；计划任务会核对记录与实际后端引擎。已选择 PostgreSQL 后，标记或配置缺失、记录冲突均阻止任务，不回退 SQLite。真实配置分别保存在 `/opt/LimitUpLab/.env.production` 与 `.env.postgresql`，不得提交。

```bash
sudo cp deploy/cron/limituplab-daily /etc/cron.d/limituplab-daily
sudo cp deploy/cron/limituplab-backup /etc/cron.d/limituplab-backup
sudo cp deploy/logrotate/limituplab /etc/logrotate.d/limituplab
sudo install -d -m 700 -o 10001 -g 10001 /var/backups/limituplab
sudo chmod 644 /etc/cron.d/limituplab-daily /etc/cron.d/limituplab-backup
sudo systemctl restart cron
```

收盘流水线在工作日北京时间 `15:30` 运行，并再次检查交易日历，因此节假日会跳过。备份任务每天 `03:25` 生成 PostgreSQL custom-format `pg_dump`、SHA256 校验文件并检查归档可读性，在 `/var/backups/limituplab/postgresql/` 保留最近 14 份通过校验的完整备份。日志位于 `/var/log/limituplab/`。历史 SQLite 归档和发布专属备份不参与日常轮换。

需要实际执行任务时，手动入口与 cron 相同；先备份，再按需运行日更：

```bash
sudo python3 /usr/local/lib/limituplab/job.py backup
sudo python3 /usr/local/lib/limituplab/job.py daily-update
docker logs --tail=100 limituplab-backend-1
docker logs --tail=100 limituplab-frontend-1
```

## 5. 更新、备份与回滚

### 日更超时与漏日恢复

生产任务统一通过 `/usr/local/lib/limituplab/job.py` 执行。该入口持有发布共享锁，
日更默认最多运行 3600 秒，备份最多运行 900 秒。超时后停止本次任务的唯一命名容器，
确认退出后再释放锁；如果无法确认退出，则写入维护标记并要求人工恢复。
任务日志包含容器名、时间上限和退出码，超时退出码为 124。

涨停池、炸板池分别在独立子进程中采集，默认每池 60 秒；可通过
`LIMITUPLAB_AKSHARE_POOL_TIMEOUT_SECONDS` 设置有限正数。超时会终止并回收子进程，
错误保留在 `source_errors` 中。即使已有缓存足够生成评级，采集失败仍会将日更标记为
`partial`，避免误报成功。业务任务使用 PostgreSQL 租约，部署与计划任务还共享宿主机发布锁；旧 `--lock-path` 参数仅兼容既有任务定义，不再建立文件锁。

腾讯日线和东财分时回退也采用独立子进程，默认 60 秒，可通过
`LIMITUPLAB_AKSHARE_KLINE_TIMEOUT_SECONDS` 调整。新浪分时优先接口保留原有 8 秒请求超时。
60 秒默认值为恢复中观察到的约 52 秒慢响应留出余量，同时限制失去响应的调用。

首页指数和晋级统计使用独立的采集预算：`LIMITUPLAB_MARKET_INDEX_TIMEOUT_SECONDS`
默认 30 秒，覆盖三个指数及全部来源回退；`LIMITUPLAB_TRADING_CALENDAR_TIMEOUT_SECONDS`
默认 30 秒，覆盖一次完整交易日历读取。指数的三个 CLI 查询并发执行；单次 CLI 使用
`LIMITUPLAB_HITHINK_TIMEOUT_SECONDS`（交互默认 15 秒）且不超过批次预算的一半，为补充来源
留出时间。预算包含子进程启动和序列化，超时后终止并回收
子进程，清理最多另用约 4 秒。配置必须为有限正数。代理环境锁只在子进程内持有；父进程
缓存成功结果并合并并发查询，失败不缓存、不使用过期数据。指数超时保留本地市场统计，
日历超时明确提示晋级率暂不可计算。整个指数批次超时后不保证继续尝试其他来源。

逐股历史 K 线在 `daily-update` 批处理中单独把 `LIMITUPLAB_HITHINK_TIMEOUT_SECONDS` 提升到
45 秒（`LIMITUPLAB_DAILY_HITHINK_TIMEOUT_SECONDS`，默认 45）。CLI 自身单请求超时 30 秒，
遇到上游 `4001`/429 会指数退避，`Retry-After` 最多再等 30 秒；沿用交互路径的 15 秒会让
批处理在第一次退避时就强杀子进程，把重试误记成 `hithink-finance request timed out after 15s`
并立刻降级腾讯源。交互路径（问答、个股详情）保持 15 秒，让慢请求尽快回退，不受批处理放宽影响。

板块快照先用 CLI 目录及批量行情，`LIMITUPLAB_SECTOR_SNAPSHOT_TIMEOUT_SECONDS` 默认
60 秒，控制最多 4 批并发的快照总预算；不包括目录请求及可选行业字段补充。单次 CLI
仍受 `LIMITUPLAB_HITHINK_TIMEOUT_SECONDS` 限制。字段缺失或榜单不完整时整榜回退原来源。

首页各数据区独立加载、显示实际数据日并支持局部重试；市场概况与晋级统计请求包含响应正文的预算为
40 秒，其他首页请求为 20 秒，其他普通 GET 为 120 秒，聊天 POST/SSE 沿用原策略。详情见
[2026-10-09 首页持续加载事故](incidents/2026-10-09-homepage-stall.md)。

漏日需要按真实交易日历逐日补齐，不能只补最新一天。先确认没有遗留运行容器并创建
当前数据库的一致性备份，再按日期执行，例如：

```bash
sudo python3 /usr/local/lib/limituplab/job.py backup
sudo python3 /usr/local/lib/limituplab/job.py daily-update --date 20260923 --max-attempts 1
```

`--date` 仅选择历史目标日期，不改变真实执行时间。历史补跑保存为 `historical_backtest`，
不能补成 live；已经错过开盘截止时间的盘前终选也不能事后生成。检查数据库最新日期、
日更报告和既有预测快照是否保持不变，不能只凭 `/health` 返回正常判断恢复成功。
修改 `job.py` 后，需要单独审查并更新服务器上 root 所有的安装副本；普通镜像发布不会覆盖它。

推荐使用 [标签自动部署流程](Tag_Deployment.md)：推送新的 `vX.Y.Z` 标签，
Windows、Ubuntu 和 PostgreSQL 验证全部通过后，由服务器发布器持锁备份并上线。
应用与迁移服务使用精确版本镜像、完整 overlay 和 PostgreSQL 就绪检查。
旧安装需先迁移至 PostgreSQL 并安装经过审查的发布入口，再使用此流程。

不要绕开共享锁直接 `git pull` 后更新生产容器。尤其不能在 PostgreSQL 部署中仅加载
基础 Compose 并使用 `--remove-orphans`：这会把 PostgreSQL 容器视为未声明服务而移除。
常规更新不删除容器数据卷，也不改变明确选择的数据库引擎。

手动生成与定时任务相同的一致性备份：

```bash
sudo python3 /usr/local/lib/limituplab/job.py backup
```

列出卷内备份：

```bash
sudo ls -lh /var/backups/limituplab
sudo ls -lh /var/backups/limituplab/postgresql
```

PostgreSQL 备份的校验和与归档可读性检查不能代替恢复演练；按 [恢复手册](postgresql.md) 恢复到独立空库，再核对业务数据和新会话。生产 PostgreSQL 接收新写入后，保留的 SQLite 已经落后，不能直接删除 URL 或修改模式标记反切。

应用自动回滚仅限数据库引擎一致、schema 未变且版本元数据未推进。结构变化或无法核验时保持维护并人工恢复，不能只回退 Git 提交后强行启动旧代码。`limituplab-data` 与 `limituplab-postgres` 都必须保留；正常升级和恢复不使用 `docker compose down -v` 或删除数据卷。

## 6. 上线验收

- `https://域名/health` 返回 `{"status":"ok"}`。
- 后端容器内 `/ready` 返回正确数据库类型；PostgreSQL 模式必须明确为 `postgresql`。
- 首页、盘前推荐、复盘、涨停池和个股详情可访问。
- Agent 可以流式输出，浏览器刷新后会话仍存在。
- 携带管理员密钥访问 `/api/agents/system-health`，显示最新交易日和 LLM 配置正常。
- 后端与数据库容器重启后，业务数据、新会话和 Agent 回答仍存在；SSE 重放与保存内容一致。
- PostgreSQL 备份恢复到独立空库后，业务内容指纹和验收回答一致；原 SQLite 及迁移前备份继续保留。
- 服务器防火墙只开放 SSH、HTTP 和 HTTPS。
- 日更服务确认连接所选数据库；如实际执行收盘任务，另核对真实运行结果，日志中没有密钥。
