# 按版本标签自动部署

## 发布约定

向 `bibofu/LimitUpLab` 推送 `vX.Y.Z` 标签后，GitHub Actions 先运行 Windows、Ubuntu 两组完整验收，再通过 SSH 请求服务器部署这个标签对应的精确提交。普通 main/codex 分支和 PR 只验证，不上线；不创建 GitHub Release 也可以触发。

```bash
git push origin main
git tag -a v1.3.1 -m "LimitUpLab V1.3.1: tag-based deployment"
git push origin v1.3.1
```

以上版本号仅为首次发布示例。发布前确认本地与远程无同名标签；后续递增版本，不移动或强制覆盖标签。标签提交必须在远程 main 历史中，并且是服务器当前提交的后继，避免迟到的旧标签倒退生产版本。保留原 `v1.3.0`。

## 一次性配置

目标为 `ubuntu@118.193.34.72`，生产仓库 `/opt/LimitUpLab`，公网域名 `limitupagent.xyz`。

1. 生成专用 Ed25519 密钥，注释必须为 `limituplab-actions-deploy`。私钥只保存在本机安全目录和仓库 Actions Secret `DEPLOY_SSH_KEY`；不要使用个人管理密钥，不要提交密钥。
2. 通过既有可信 SSH 通道核验服务器主机公钥。`production_known_hosts` 固定该公钥，主机密钥变化时应人工核验并更新，不能关闭验证或临时信任 `ssh-keyscan` 的未核验结果。
3. 上传经过审查的本目录及专用公钥，在服务器执行 `sudo python3 <上传目录>/install.py --public-key <公钥路径>`。安装器只安装 root 所有的脚本、维护开关和共享锁 cron，不重启应用容器；原 Nginx/cron 配置保存在 `/var/backups/limituplab-deploy-config/`。
4. 安装器追加受限 `authorized_keys` 条目：只允许执行 `deploy vX.Y.Z SHA`，禁止终端、端口转发及普通 shell。不要替换现有个人登录公钥。
5. 在 GitHub 仓库 Settings → Secrets and variables → Actions 配置 `DEPLOY_SSH_KEY`。Secret 可供有权限的仓库工作流使用；仓库写权限本身应仅授予可信人员。

已安装脚本不从新标签自动覆盖：部署入口的更新需要管理员审查后重新安装，防止未经检查的入口逻辑直接替换生产保护措施。

## 运行与数据保护

- GitHub 部署任务使用全局生产并发组、不取消进行中的部署；服务器部署、日更和备份共用 `/var/lock/limituplab-release.lock`。GitHub 可能合并尚未执行的并发等待请求；需要每个版本上线时，应等待前一次结束后再发布下一标签。
- 部署等待共享锁最多一小时；日更/备份最多等待两小时。超时失败并保留日志，不强制停止已有日更。遗留手动启动的 daily-update 容器也会检查并等待最多十分钟。
- 北京时间 08:30–09:35 禁止开始切换；构建和任务等待后再次检查。命中窗口时失败，稍后在同一标签工作流选择 Re-run failed jobs，不需要移动标签。
- 在 `/var/lib/limituplab-deploy/releases/<SHA>` 创建独立 worktree，并使用 SHA 镜像标签构建。只引用服务器原 `.env.production`，构建上下文排除该文件；构建/拉取失败不停止旧应用。
- 切换时创建 `/opt/LimitUpLab/.maintenance`，公网 Nginx 返回 503；内部 8080 保留健康检查通道。停止刷新、前端和后端后，用 SQLite 在线备份 API 创建并完整性校验专属发布备份。
- 发布备份在 `/var/backups/limituplab/deployments/<运行标识>/`，不进入每日最近 14 份备份的轮换集合。生产数据库卷 `limituplab-data` 不删除。
- 新前后端容器健康、内部代理及页面路由通过，刷新进程启动并保持运行后，更新 local 镜像引用、快进生产 main，记录 `current.json` 并解除维护。日更与定时备份继续使用当前 local 镜像。
- Schema 检查和备份容器以可写方式挂载数据卷，但 SQLite 连接固定使用 `mode=ro`。这是为了允许 SQLite 创建或读取 WAL/SHM 协调文件；把 Docker volume 标成 `:ro` 会导致数据库文件权限正确时仍报 `unable to open database file`，不得用 `immutable=1` 忽略可能尚未 checkpoint 的 WAL。
- SSH 断开不主动中断服务端部署；客户端失败后先查服务器日志和状态，不立即启动第二次部署。部署中途重启主机、进程被强杀或数据库结构变化后的失败需要人工恢复，不能宣称所有故障都能自动回滚。

## 失败与恢复

| 失败阶段 | 行为 |
| --- | --- |
| 标签校验、拉取、构建、窗口检查 | 旧服务保持运行 |
| 备份或健康检查失败，数据库结构未变且版本元数据未推进 | 停止目标容器，恢复旧镜像并验证后退出维护 |
| 数据库版本或结构摘要变化、无法核验结构、元数据已推进 | 保留维护状态，停止目标服务，等待人工判断 |
| 恢复旧服务仍失败 | 保留维护状态和日志，不继续尝试覆盖数据库 |

每次发布保存 JSON 状态与日志，包含旧/新 SHA、镜像、备份路径和阶段，位于 `/var/lib/limituplab-deploy/`；GitHub Actions 摘要记录标签、SHA 和退出结果，不输出生产配置或私钥。

人工恢复时先保存失败后的数据库一致性副本，检查迁移兼容性和期间写入，再决定修复前进还是在明确批准后恢复发布前备份。本工具不自动还原数据库、不提供绕过维护检查的强制参数。恢复到可用版本并确认应用和任务后，才可移除维护标记。

## 验证与边界

`python scripts/check_project.py` 包括发布参数校验、时间边界、锁冲突、构建/备份/健康失败、数据库迁移后禁止自动回滚及成功记录测试。CI 必须两种操作系统全部通过；运行时构建与验收环境不同，仍需容器健康检查。

切换时除页面路由与健康接口外，还检查一进二、缩量整理、高位回撤三个业务数据接口；一进二无快照的明确 404 可接受，500 不可接受。首次上线还需核验公网页面、刷新容器、数据库持久化与生产 SHA。页面路由返回 200 不是所有业务数据完整的证明；数据缺口按现有界面明确显示。本方案适用于单机短暂维护，不承诺零停机。

从旧版双策略数据库升级时，仓储读取会在内存中排除已退役的 discovery 条目并附警告，不放宽公开模型或改写历史快照。正常刷新替换旧 current 前会归档原始 JSON，保留升级前证据。
