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
- 在 GitHub 创建 OAuth App，回调地址填写 `https://你的域名/api/auth/github/callback`，然后配置 `LIMITUPLAB_GITHUB_CLIENT_ID` 和 `LIMITUPLAB_GITHUB_CLIENT_SECRET`。授权流程使用 `state` 和 PKCE，GitHub Access Token 不会持久化。
- 配置 SMTP 发件服务并设置 `LIMITUPLAB_EMAIL_LOGIN_ENABLED=true`。生产环境只允许 `smtp` 投递模式，验证码与登录 Session 在数据库中都只保存摘要。
- 没有同花顺凭据时可设置 `INSTALL_HITHINK_FINANCE=false`，相关工具会按现有逻辑降级或明确报错。

浏览器首次访问时会得到签名的 HttpOnly 匿名访客 Cookie。公开行情页面可以匿名浏览，生产环境的 Agent 对话要求使用 GitHub 或邮箱验证码登录。首次登录会把当前匿名会话迁移到用户账号，之后使用独立的 HttpOnly 登录 Session 恢复历史会话。

GitHub OAuth App 可参考 [GitHub OAuth Web Flow 官方文档](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps) 创建。只申请基础用户资料权限，不申请仓库权限。

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
sudo chmod 644 /etc/cron.d/limituplab-daily
sudo systemctl restart cron
```

任务在工作日北京时间 `16:10` 运行。流水线会再次检查交易日历，因此节假日会跳过。日志位于 `/var/log/limituplab/daily-update.log`。

手动验证：

```bash
docker compose --env-file .env.production --profile jobs run --rm daily-update
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

使用 SQLite 在线备份 API生成一致性备份：

```bash
docker compose --env-file .env.production exec -T backend python -c \
  "import sqlite3,time; s=sqlite3.connect('/app/data/limituplab.sqlite'); d=sqlite3.connect('/app/data/backup-'+time.strftime('%Y%m%d-%H%M%S')+'.sqlite'); s.backup(d); d.close(); s.close()"
```

列出卷内备份：

```bash
docker compose --env-file .env.production exec backend ls -lh /app/data
```

回滚应用代码时只回滚 Git 提交并重建镜像，不删除 `limituplab-data` 卷。任何包含 `docker compose down -v` 或 `docker volume rm limituplab-data` 的命令都会删除生产数据库，不应在正常升级中使用。

## 6. 上线验收

- `https://域名/health` 返回 `{"status":"ok"}`。
- 首页、盘前推荐、复盘、涨停池和个股详情可访问。
- Agent 可以流式输出，浏览器刷新后会话仍存在。
- GitHub 和邮箱验证码均可登录；退出后无法访问账号下的历史会话，重新登录后可以恢复。
- 数据库中不存在明文邮箱验证码、登录 Session Token 或 GitHub Access Token。
- 携带管理员密钥访问 `/api/agents/system-health`，显示最新交易日和 LLM 配置正常。
- 容器重启后 SQLite 数据与会话仍存在。
- 服务器防火墙只开放 SSH、HTTP 和 HTTPS。
- 日更任务手动执行成功，且日志中没有密钥。
