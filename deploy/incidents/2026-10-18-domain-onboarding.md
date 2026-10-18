# 2026-10-18 新域名接入与 404 修复

## 现象与原因

`limituplab.tech` 的根域名 A 记录已指向 `118.193.34.72`，访问仍返回 404。
本次 DNS 查询未返回 AAAA、CAA 记录。

宿主机 `/etc/nginx/sites-available/limituplab` 仅配置旧域名。
新域名请求落入 80 端口默认站点，由 Certbot 生成的 `return 404` 返回错误。
DNS 解析生效后，仍需配置对应的 Nginx 虚拟主机与 HTTPS 证书。

## 配置变更

- 由部署用户通过 `flock` 获取现有 `/var/lock/limituplab-release.lock`，再以 `sudo`
  执行配置，避免与发布、日更和备份并发。
- 新增 `/etc/nginx/sites-available/limituplab-tech` 及 `sites-enabled` 下同名链接，
  独立承接 `limituplab.tech`，代理到 `127.0.0.1:8080`。
- 保留 `/etc/nginx/snippets/limituplab-maintenance.conf` 引用，以及 SSE 所需的
  HTTP/1.1、关闭代理缓冲、300 秒读取超时和转发请求头配置。
- 未修改旧域名站点、应用代码或业务数据。操作后旧站点配置的 SHA-256 仍为
  `38e86b6b7572a1023b67f7f1c2757881715da9c3006df535fa3010df8d0b5fe7`。

Nginx 配置备份位于 `/var/backups/limituplab-domain-config/20261018T053953Z/nginx`，
另有最初备份 `/var/backups/limituplab-domain-config/20261018T053932Z/nginx`。

## 证书与续期

通过以下命令成功签发并安装独立证书，启用 HTTP 到 HTTPS 的跳转：

```bash
sudo certbot --nginx --non-interactive --redirect --cert-name limituplab.tech -d limituplab.tech
```

证书路径为 `/etc/letsencrypt/live/limituplab.tech/fullchain.pem`，SAN 仅包含
`limituplab.tech`，签发者为 Let's Encrypt YE2。有效期截至 2027-01-16
04:41:32 UTC，即北京时间 2027-01-16 12:41:32。未配置 `www` 域名。

`certbot.timer` 为 enabled、active，以下定向续期演练成功：

```bash
sudo certbot renew --cert-name limituplab.tech --dry-run --no-random-sleep-on-renew --non-interactive
```

## 验证与边界

- 续期演练后 `nginx -t` 再次通过，Nginx 与 `certbot.timer` 均为 active；
  旧站点配置哈希保持一致，生产证书签发者仍为 Let's Encrypt YE2。
- 外部请求严格校验 TLS，未跳过证书验证。
- HTTP `/recommendations?strategy=relay` 返回 301，跳转保留原路径与查询参数。
- HTTPS 首页、`/health`、`/favicon.svg`、上述深路由及入口 JS、CSS 均返回 200；
  `/health` 返回 `{"status":"ok"}`，旧域名的 `/health` 也返回 200。
- 浏览器实际打开新域名首页，页面正常渲染并显示数据。

以上验证覆盖域名接入、TLS、静态资源与首页可用性，不代表完整业务验收或 Agent
问答全量验证。新域名使用独立的访客 Cookie，旧域名的匿名会话不会自动迁移。
