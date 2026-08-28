# LimitUpLab 香港服务器部署

这套配置面向单台 Linux 服务器上的公开 Demo：宿主机 Nginx 负责域名和 HTTPS，Docker 中的 Nginx 提供 React 静态文件并把 `/api` 转发给单 worker FastAPI。SQLite 位于 Docker 命名卷 `limituplab-data`，不会随镜像重建或 `git pull` 消失。

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
- 再生成一个不同的 `LIMITUPLAB_ADMIN_KEY`，用于内部诊断、评测、运行轨迹和评分策略管理接口。
- 没有同花顺凭据时可设置 `INSTALL_HITHINK_FINANCE=false`，相关工具会按现有逻辑降级或明确报错。

浏览器首次访问时会得到签名的 HttpOnly 匿名访客 Cookie。会话、消息和 Agent 运行记录均按该匿名身份隔离；清除浏览器 Cookie 后会成为新的匿名用户，无法继续访问原会话。

内部接口必须携带 `X-LimitUpLab-Admin-Key`，例如：

```bash
curl --fail -H "X-LimitUpLab-Admin-Key: $LIMITUPLAB_ADMIN_KEY" \
  https://你的域名/api/agents/system-health
```

评分策略、数据健康、系统健康、日更状态、离线评测、预测审计、因子诊断和 Agent 运行轨迹均属于管理员接口。生产环境同时关闭 FastAPI 的 `/docs`、`/redoc` 和 `/openapi.json`。

## 2. 构建并启动应用

```bash
docker compose --env-file .env.production build
docker compose --env-file .env.production up -d
docker compose --env-file .env.production ps
curl --fail http://127.0.0.1:8080/health
```

应用端口只绑定到 `127.0.0.1:8080`，不要把 FastAPI 的 `8001` 端口直接暴露到公网。

首次部署没有数据时，执行一次收盘流水线：

```bash
docker compose --env-file .env.production --profile jobs run --rm daily-update
```

如果需要迁移开发机上的 SQLite，在启动 Web 服务前执行：

```bash
docker compose --env-file .env.production run --rm -T --no-deps backend \
  sh -c 'cat > /app/data/limituplab.sqlite' < backend/data/limituplab.sqlite
```

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

```bash
sudo cp deploy/cron/limituplab-daily /etc/cron.d/limituplab-daily
sudo cp deploy/cron/limituplab-backup /etc/cron.d/limituplab-backup
sudo cp deploy/logrotate/limituplab /etc/logrotate.d/limituplab
sudo install -d -m 700 -o 10001 -g 10001 /var/backups/limituplab
sudo chmod 644 /etc/cron.d/limituplab-daily /etc/cron.d/limituplab-backup
sudo systemctl restart cron
```

收盘流水线在工作日北京时间 `16:10` 运行，并再次检查交易日历，因此节假日会跳过。备份任务每天 `03:25` 使用 SQLite 在线备份 API 生成一致性快照，保留最近 14 份。日志位于 `/var/log/limituplab/`，备份位于仅服务用户可读的 `/var/backups/limituplab/`。

手动验证：

```bash
docker compose --env-file .env.production --profile jobs run --rm daily-update
sudo /usr/bin/docker run --rm -v limituplab-data:/app/data \
  -v /var/backups/limituplab:/backups limituplab-backend:local \
  python scripts/backup_database.py --database /app/data/limituplab.sqlite \
  --output-dir /backups --retain-count 14
docker compose --env-file .env.production logs --tail=100 backend frontend
```

## 5. 更新、备份与回滚

更新代码：

```bash
cd /opt/LimitUpLab
git pull --ff-only
docker compose --env-file .env.production build
docker compose --env-file .env.production up -d --remove-orphans
curl --fail http://127.0.0.1:8080/health
```

手动生成与定时任务相同的一致性备份：

```bash
sudo /usr/bin/docker run --rm -v limituplab-data:/app/data \
  -v /var/backups/limituplab:/backups limituplab-backend:local \
  python scripts/backup_database.py --database /app/data/limituplab.sqlite \
  --output-dir /backups --retain-count 14
```

列出卷内备份：

```bash
sudo ls -lh /var/backups/limituplab
```

回滚应用代码时只回滚 Git 提交并重建镜像，不删除 `limituplab-data` 卷。任何包含 `docker compose down -v` 或 `docker volume rm limituplab-data` 的命令都会删除生产数据库，不应在正常升级中使用。

## 6. 上线验收

- `https://域名/health` 返回 `{"status":"ok"}`。
- 首页、盘前推荐、复盘、涨停池和个股详情可访问。
- Agent 可以流式输出，浏览器刷新后会话仍存在。
- 携带管理员密钥访问 `/api/agents/system-health`，显示最新交易日和 LLM 配置正常。
- 容器重启后 SQLite 数据与会话仍存在。
- 服务器防火墙只开放 SSH、HTTP 和 HTTPS。
- 日更任务手动执行成功，且日志中没有密钥。
