# 远程开发与运维边界

## 当前服务器布局

SSH 工具别名 `hklite-lv4`，Debian 13，16 vCPU / 约 15GiB RAM。实际主机地址和管理员登录信息保留在受保护的 SSH 配置中，不写入公开仓库。此规格不是并发能力保证，尚未做容量/网络压测。

| 位置 | 用途与权限 |
| --- | --- |
| `/opt/allira/workspace` | 源码主工作区，allira 用户拥有 |
| `/opt/allira/tools/uv-bin/uv` | uv 0.13.8，root 管理 |
| `/opt/allira/tools/node24` | Node.js 24.22.3 / npm 11.19.1，root 管理 |
| `/opt/allira/workspace/server/.venv` | 服务端依赖，仅服务器存在 |
| `/opt/allira/workspace/packages/client/node_modules` | SDK 开发依赖，仅服务器存在 |
| `/opt/allira/workspace/packages/client/dist` | SDK 编译结果，仅服务器存在 |
| `/var/lib/allira/dev.sqlite3` | 开发数据库，运行用户可读写 |
| `/etc/allira/dev.env` | systemd 环境文件，root:allira，0640；不回传本地 |
| `/etc/systemd/system/allira-dev.service` | 单进程开发服务，按需启动，未启用开机自启 |

系统 Python 为 3.13.5。`allira` 是独立系统用户，无 sudo 权限；SSH 当前仍使用既有管理员连接，不在此次工作中修改登录配置、防火墙或现有端口规则。运行时单独安装于项目工具目录，未添加第三方 apt 源。

## 日常命令（全部在服务器执行）

```sh
# 管理员 SSH 会话中：安装锁定依赖、检查并测试后端
runuser -u allira -- sh -c '
  cd /opt/allira/workspace/server
  /opt/allira/tools/uv-bin/uv sync --locked --extra dev --python /usr/bin/python3
  .venv/bin/ruff check allira tests
  .venv/bin/pytest -q
'

# 编译并测试 SDK
runuser -u allira -- sh -c '
  cd /opt/allira/workspace/packages/client
  export PATH=/opt/allira/tools/node24/bin:$PATH
  npm ci --ignore-scripts
  npm test
'

# 生成契约快照；不启动 API、不接触运行数据库
runuser -u allira -- sh -c '
  cd /opt/allira/workspace
  server/.venv/bin/python scripts/export_openapi.py
'

# 开发服务操作；服务已安装，无需再次复制模板
systemctl status allira-dev --no-pager
systemctl restart allira-dev
journalctl -u allira-dev -n 50 --no-pager
systemctl stop allira-dev

# 默认 Mock 开发服务运行后，真实 HTTP + SDK + SSE 冒烟
runuser -u allira -- sh -c '
  cd /opt/allira/workspace
  /opt/allira/tools/node24/bin/node scripts/smoke.mjs
'
```

冒烟会以固定开发用户创建测试会话/对话，不打印 Token；它是 Mock 专用，会断言 Mock 文本，不应对配置了真实模型的服务执行。单元测试使用临时库/内存库，不访问运行中的开发数据库。

**不要并行启动第二个 Uvicorn，不使用 `--reload`，保持 `--workers 1`。** 修改源码后通过 systemctl restart 生效，SQLite WAL 并不能解决多进程任务所有权。开发重启会终结正在生成的任务，前端应显示明确失败而非无限等待。

## 本地访问：SSH 隧道

API 绑定 `127.0.0.1:8000`。可以通过 SSH 插件将服务器回环端口 8000 转发到笔记本空闲端口；或在笔记本使用标准 SSH：

```sh
# 仅建立隧道，不执行本地开发/编译任务
# 先在当前终端设置 SSH_TARGET 为受保护配置中的 SSH 别名或 user@host。
ssh -N -L 8000:127.0.0.1:8000 "${SSH_TARGET:?请先设置实际 SSH 目标}"
```

之后访问 `http://127.0.0.1:8000/docs`、`http://127.0.0.1:8000/healthz`。SSH 插件的别名不保证存在于系统 ssh 配置，使用前需确认终端里的 SSH 目标。若本地端口占用，改左侧端口并相应改 SDK baseUrl。

未来 Gemini 的前端开发服务也运行在服务器，通过另一条隧道访问；本阶段没有安装前端框架、没有启动 5173 服务。当前 CORS 精确允许 `http://localhost:5173` 与 `http://127.0.0.1:5173`，其它本地源需显式加入服务端配置；不要改成通配符。也可让未来前端反向代理 `/api`，避免跨源。

## 环境与秘密

[示例环境](../server/.env.example) 不是生产配置。开发环境启用了任意用户开发登录，只适合受 SSH 保护的本机回环访问。不要把 host 改为 `0.0.0.0`，不要发布这套登录到公网，也不要把环境文件、Token、上游 Key 回传或提交源码。

配置真实上游时由管理员在受保护的远端环境中设置 `ALLIRA_PROVIDER=openai`、上游 base URL、model 和可选 API Key；不要把真实值粘贴在聊天中。随后重启并独立验证。当前仅完成 Mock 和 httpx 模拟上游测试，**未连接真实中转站**。

## 源码同步与磁盘原则

服务器负责依赖、编译和运行，本地仅存轻量源码镜像。远端主工作区已初始化 Git，托管目标是 [GUYU2233/AlliraAi](https://github.com/GUYU2233/AlliraAi)，主分支 `main`。继续工作时先确认两端改动，确定唯一编辑端，勿用整目录覆盖正在修改的工作区。

禁止同步 `.venv`、`node_modules`、`dist`、`.env`、数据库及 WAL/SHM、缓存、日志、`/etc/allira` 和凭据。只回传明确需要的源码、锁文件、文档/OpenAPI；本地不执行 npm install、uv sync、测试或构建。不要用本地清理命令顺手删除系统共享缓存或其它项目文件。

### GitHub SSH 身份

`allira` 用户使用为本项目单独生成的 Ed25519 身份，私钥只保存在服务器 `/home/allira/.ssh`，目录权限 0700、私钥权限 0600。SSH 别名 `github-allira` 固定使用该身份，仓库 origin 为 `git@github-allira:GUYU2233/AlliraAi.git`；它是正常 GitHub SSH 地址的本机专用别名，不是其它托管服务。

当前公钥实际添加在 `GUYU2233` 的账号 SSH keys 中，已完成认证和本仓库推送。**账号级 SSH key 并非仅限本仓库，可访问范围取决于该账号的权限。** 若需收窄为单仓库访问，应另行生成新身份，添加为本仓库的可写 Deploy key，验证后再撤销旧账号级授权；本次不自动变更 GitHub 授权范围。私钥不应复制到源码、笔记本或聊天中，只有公钥可以提供给 GitHub。

SSH 已启用严格主机校验，使用 [GitHub 官方公布的 Ed25519 主机密钥](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)；不要为连接方便关闭主机校验。提交者为 `GUYU2233`，邮箱使用 GitHub 的公开 noreply 地址；这只是 Git 作者元数据，不代表提交已经签名。

```sh
# 在服务器管理员 SSH 会话中执行；仅检查仓库级访问，不输出私钥。
runuser -u allira -- sh -c '
  cd /opt/allira/workspace
  git status --short
  git ls-remote origin
'
```

推送前检查暂存区是否仅包含预期源码，并扫描凭据与运行数据。远端已有历史时先 fetch、检查差异，不默认强推或覆盖；更换密钥时同步更新对应的 GitHub 账号 SSH key 或仓库 Deploy key，并在新身份验证后撤销旧授权。GitHub 仅保存已推送的 Git 内容，不备份数据库、环境文件和服务器私钥；仍需另行安排受保护的运行数据备份。

## 数据备份与恢复

数据库是敏感聊天数据；当前还未配置自动备份和恢复演练。需要备份时使用 SQLite backup API 或停服后的一致性快照，不要只复制活跃主库而遗漏 WAL。备份应有受限权限、异机副本和保留期，不传到笔记本占空间。

恢复前停服务，先备份当前状态，再由管理员恢复库并确认 allira 归属；重启会把遗留 running 任务标记为 failed/server_restarted。还没有数据库版本迁移工具，升级结构前先设计迁移与回滚，不要删库掩盖问题。

## 尚未做的生产运维

未开放新公网业务端口；未配置域名、TLS、反代、正式身份、自动备份、监控告警、发布流水线、全局限流或生产并发。systemd 模板只降低开发服务权限，不等于服务器已全面加固。正式上线是独立任务，不能只把 dev 改成 production 就宣称完成。
