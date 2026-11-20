# 远程开发与运维边界

## 布局与访问

SSH 工具别名 `hklite-lv4`，Debian 13，16 vCPU / 约 15GiB RAM；规格不代表已验证的并发能力。实际地址/管理员登录保留在受保护 SSH 配置，不写入公开源码。所有项目依赖、lint、测试、编译、浏览器及服务在远端以低权限 `allira` 执行，本地只编辑/同步轻量源码。

| 位置 | 用途 |
| --- | --- |
| `/opt/allira/workspace` | allira 拥有的主工作区 |
| `/opt/allira/tools/uv-bin/uv` | uv 0.13.8，root 管理 |
| `/opt/allira/tools/node24` | Node 24.22.3 / npm 11.19.1，root 管理 |
| `/opt/allira/workspace/server/.venv` | Python 依赖；系统 Python 3.13.5 |
| `/opt/allira/workspace/packages/client/dist` | SDK 编译结果，仅远端 |
| `/opt/allira/workspace/apps/dev-console/dist` | 运行中的功能客户端、后台、PWA 公开壳；只由部署构建更新 |
| `/opt/allira/workspace/apps/dev-console/.test-dist` | 测试构建隔离目录，不覆盖当前 dist |
| `/var/lib/allira/dev.sqlite3` | 开发库，allira 可读写，敏感聊天数据 |
| `/etc/allira/dev.env` | 基础环境，root:allira，0640；不回传 |
| `/etc/allira/test-provider.env` | 可选受保护中转覆盖，root:allira，0640；不入仓库 |
| `/etc/systemd/system/allira-dev.service` | 现有单进程回环服务，未启用开机自启 |

服务只绑定 `127.0.0.1:8000`，单 worker、不 reload、`--no-proxy-headers`。`allira` 无 sudo；不开放入站业务端口或修改 SSH/防火墙。用户授权的 `dsah.guyufl.cc.cd` 仅通过专用 Cloudflare Tunnel 发布 Access 后台，不发布普通聊天、业务登录/API、Logto 或 PG。本轮域名/Access 变更及用户自行登录测试边界见[部署记录](access-admin-acceptance.md)。

笔记本仅建立 SSH 隧道（不在本地构建）：

```sh
# 终端先设置 SSH_TARGET 为实际 SSH 配置别名或 user@host。
ssh -N -L 8000:127.0.0.1:8000 "${SSH_TARGET:?请先设置受保护配置中的 SSH 目标}"
```

也可使用 SSH 插件转发远端 8000，再访问 `http://127.0.0.1:8000/test/`、`/docs`、`/healthz`、`/readyz`。管理入口统一为 `https://dsah.guyufl.cc.cd/admin/`；本地 `/admin/` 也不能绕过 Access，不再签发一次性管理链接。本地端口冲突时只改 SSH 转发左侧端口；插件别名未必是系统 ssh 别名，转发监听仍限制在本地回环。

## 构建与自动化（均在服务器）

```sh
# 后端：临时/内存测试库，不读业务环境文件、不调用真实中转
runuser -u allira -- sh -c '
  cd /opt/allira/workspace/server
  /opt/allira/tools/uv-bin/uv sync --locked --extra dev --python /usr/bin/python3
  .venv/bin/ruff check allira tests ../scripts/export_openapi.py ../scripts/restore_drill.py
  .venv/bin/pytest -q
'
# SDK：含 TypeScript build、传输模拟测试与类型反例
runuser -u allira -- sh -c '
  export PATH=/opt/allira/tools/node24/bin:$PATH
  cd /opt/allira/workspace/packages/client
  npm ci --ignore-scripts && npm test
'
# 页面：含 typecheck/esbuild、纯状态及策略测试
runuser -u allira -- sh -c '
  export PATH=/opt/allira/tools/node24/bin:$PATH
  cd /opt/allira/workspace/apps/dev-console
  npm ci --ignore-scripts && npm test
  npm run test:browser
'
# 契约快照：不启动生命周期、数据库或模型
runuser -u allira -- sh -c '
  cd /opt/allira/workspace
  server/.venv/bin/python scripts/export_openapi.py
'
```

测试的 npm test 仅构建隔离 `.test-dist`；浏览器路由模拟通过虚拟 loopback 地址读取它并拦截所有请求，默认禁止 Service Worker，不触碰用户当前 `dist`。只有受控部署执行 npm run build 更新运行资源。后端 conftest 清理 ALLIRA_* 与 .env 影响、强制 Mock 并拒绝真实 HTTP transport；不能将该隔离改成读取业务环境来让测试通过。

浏览器的 Chromium 与 Linux 依赖已经仅在服务器安装，配置无第二个 webServer，trace/video/screenshot 关闭，敏感值只保留测试进程内存。`npm run test:browser` 路由模拟与实际服务测试按 [控制台说明](../apps/dev-console/README.md) 区分；设置 live 标志前先确认 `/api/v1/info` 为 Mock。**live 会创建测试账号/消息/CDK/审计，不能对真实中转跑全量。** 真实密码计算测试需要数十秒，不以超短超时重复启动测试进程。

```sh
# 仅在运行中的服务 provider=mock 时：真实 HTTP + SDK + SSE 冒烟
runuser -u allira -- sh -c '
  cd /opt/allira/workspace
  /opt/allira/tools/node24/bin/node scripts/smoke.mjs
'
```

脚本先检查 Mock/开发认证，再创建测试数据；不回显 Token。实际服务 live 与单元测试是不同验证，不能互相代替。代码或静态资源改完必须先构建，然后重启现有服务；静态挂载目录需在启动时存在。禁止另起 Uvicorn 或 `--reload`。

## 服务与只读诊断

```sh
systemctl status allira-dev --no-pager
systemctl restart allira-dev
systemctl stop allira-dev
# 日志只在受保护终端查看，分享前清理敏感数据
journalctl -u allira-dev -n 50 --no-pager
```

重启后等待 `/healthz`、`/readyz` 和 `/api/v1/info` 真正返回成功，不把刚 restart 后的瞬间连接拒绝误判为坏版本。readiness 仅本地 SELECT 1，不访问真实模型；后台健康诊断不返回私有路径或环境。重启会终结 running 并退款；用户端需显示实际失败与恢复入口。只允许一个 API 实例，否则内存任务所有权和重启恢复会互相干扰。

[systemd 模板](../ops/allira-dev.service) 按顺序读取 dev.env、可选 test-provider.env、可选 identity.env、可选 access.env，同名键由后层覆盖；可选文件缺失不报错。access.env 仅配置后台 Access，不改模型或普通身份配置。修改模板后由管理员审查再复制、daemon-reload；复制源码模板不等于当前服务已经使用它。不要在命令行/日志输出环境文件内容或 `systemctl show Environment`。

## 独立后台：Cloudflare Access

入口为 `https://dsah.guyufl.cc.cd/admin/`，Access 策略与源站独立只允许已确认的 `chuluoxiang@gmail.com`。普通 Logto 身份、开发 userId、裸邮箱头、旧链接/旧 Cookie 不授予管理权限。默认 `admin_auth_backend=access`，`issue-login` CLI 拒绝签发，旧 POST session 对格式有效的 code 返回 `403 admin_link_disabled`；link 只留隔离历史测试，不能作为公开故障回退。

`/etc/allira/access.env` 由服务器管理员保护为 root:allira 0640：四项完整配置为 `ADMIN_PUBLIC_ORIGIN=https://dsah.guyufl.cc.cd`、`ACCESS_TEAM_DOMAIN=https://guyufl.cloudflareaccess.com`、精确应用 `ACCESS_AUDIENCE` 和 `ACCESS_ALLOWED_EMAILS`（实际变量均加 ALLIRA_ 前缀，见环境示例）。只在服务器编辑，不读取/打印 Access 断言、Tunnel credentials 或登录证书。更改应用/AUD/策略必须同时评审边缘、Tunnel 与源站，不能只放行其中一层。

源站每请求用 joserfc 验证 `Cf-Access-Jwt-Assertion`，固定 issuer/AUD/RS256/public kid/type=app/subject/email/期限；不从请求选择 JWKS 或信任 X-Forwarded-*。页面清 hash 但不解析 code，直接 GET 管理 session；Secure/HttpOnly/SameSite=Strict/host-only Cookie 绑定 JWT 摘要，到期不晚于 JWT 与本地 TTL。写请求仍须固定 HTTPS Origin 和独立内存 CSRF。退出删除本地绑定后只导航固定 `/cdn-cgi/access/logout`。JWKS 不可用 fail closed，不能改回旧登录。

专用 Tunnel 的固定版本、私有 0700/0600 文件、官方离线 ingress 校验、systemd 和回滚边界见[运维说明](../ops/cloudflare/README.md)。当前 connector 为显式启动的 static unit，未设置开机自动启动；API/Logto/PG 也未改变原启动策略。无凭据公网请求只核对 Access 302，不跟随登录；用户要求实际登录及操作自行测试，不能据此写成端到端成功。

紧急撤销本地管理会话（不会撤销 Cloudflare Access 自身会话；Access 启动也会清旧本地证明）：

```sh
runuser -u allira -- sh -c '
  cd /opt/allira/workspace/server
  .venv/bin/python -m allira.admin_cli --env-file /etc/allira/dev.env revoke-sessions
'
```

CDK 明码只在签发响应显示一次，刷新列表不能找回；签发响应未知时不要自动重试生成另一批。正文只在管理者显式读取时获取并留审计。用户页按 ID／用户名／显示名做字面查询、按当前身份关联筛选，只显示脱敏手机与已有钱包；点击才请求设备，单设备与全部本地撤销分开确认，不是封禁或 Logto 全局 SSO 注销。读写/切页/查询/owner/退出都有版本护栏，结果不确定不自动重发；旧服务未提供身份字段时显示未知。

路由页只是与 `/models` 共用的配置摘要，无模型请求、凭据编辑或健康保证。健康页分别显示业务 SQLite 及身份 PG 的备份元数据，认证门禁不等于上游可用性探测；PG 目录由 `ALLIRA_IDENTITY_BACKUP_DIRECTORY` 指定，默认私有 Logto backups。`checksumVerified=false` 始终保留，历史恢复报告 `recorded` 不是本请求的恢复或 SHA 证明；不打开 dump、不连 PG、不调用 Logto Management。Allira 后台与 Logto Console 权限、数据范围、会话撤销分开。仅本次受限 Access 管理域名获得公开授权，不代表普通业务或 production 全面上线。

## 指定测试中转与 Mock 切换

用户已授权在服务器配置指定测试中转。真实 Key 仅在受保护环境文件，没有写入源码/SDK/前端。少量短请求与完整真实链路结果见 [验收记录](mvp-acceptance.md)，不能据此宣称稳定性、极速优势、价格或负载容量。

[环境示例](../server/.env.example) 只含说明/占位。可选覆盖必须由管理员在服务器内编辑，不通过聊天索取凭据、不把它复制到本地。关键项为：

```ini
ALLIRA_PROVIDER=openai
ALLIRA_UPSTREAM_BASE_URL=https://your-authorized-relay.example/v1
ALLIRA_UPSTREAM_MODEL=your-verified-model
# Key 仅在服务器私密编辑器中填写，不使用 shell history 传参
ALLIRA_UPSTREAM_MAX_TOKENS=256
ALLIRA_MOCK_CHUNK_DELAY_SECONDS=0
# 极速未验证时留空，按配置回退经济路线并退差价
ALLIRA_FAST_UPSTREAM_MODEL=
ALLIRA_FALLBACK_TO_ECONOMY=true
```

回归前先确认无 running/reserved 或上游 closing 任务，在 root-only 0700 维护目录保存一致性 SQLite 快照、原环境/单元和前端构建。凭据备份只在服务器、0600，不打印或回传；用摘要记录原内容和维护版本，防止覆盖维护期人工变更。在同一个受保护覆盖文件中**只改已知非秘密键**：provider=mock、mock_chunk_delay_seconds=0.3、fast_upstream_model 为空，保留 Key 原位不输出。API 0.5 来源块专项还临时设置 context_recent_turns=1，便于两轮聊天后触发摘录；这不是新的用户默认值，最终恢复完整原环境时必须同时撤销。停止现有服务后部署构建，再只启动同一个实例；等待实际 GET info=Mock、readyz=200 后才允许 HTTP/live。

所有浏览器任务明确冻结且无活动 Run/预扣后，验证当前配置仍是预期维护版，再在服务器以排他临时文件+0640/正确属主+原子发布恢复所保存的**完整原始环境字节**，不是凭记忆重建密钥或默认值。重启同一个服务后检查版本、info=原提供方、readyz、页面、端口和无预扣；不通过真实生成验证恢复，不在并行 live 中切换提供方。若维护期间配置摘要变化，停下检查，不覆盖未知修改。

真实单次全链路验收是显式操作，不在 npm/pytest 或自动循环中：

```sh
# 仅在确认已授权、provider=openai、模型输出上限较低后执行一次
runuser -u allira -- sh -c '
  cd /opt/allira/workspace
  ALLIRA_REAL_SMOKE=1 /opt/allira/tools/node24/bin/node scripts/relay-smoke.mjs
'
```

该脚本只创建一个经济 Run，检查实际终态、usage、同 UUID 无二次扣能量、历史和清理；不打印 Token/输出正文，失败不重试模型请求。真实模型响应可能失败；不能把 GET 模型列表或模拟上游测试说成已验证真实流式。切换配置前先备份数据库和配置到服务器受限目录。

## Logto OSS／OIDC 与身份验收

当前实际部署 Logto OSS v1.46.0、PostgreSQL 17.12 与 Authlib/joserfc RP；固定版本、监听补丁、官方管理 API、许可和私有文件见 [Logto 运维](../ops/logto/README.md)与[身份设计](identity-design.md)。`allira-dev.service` 先读 dev.env、原受保护模型覆盖，再读独立 `/etc/allira/identity.env`；该独立身份覆盖层包含 OIDC 的四项完整配置及运营门禁，其后 access.env 只管后台，均不修改原模型配置字节。无真实短信／平台条件时 `auth_backend=oidc,oidc_enabled=false,oidc_simulated=false,allow_dev_auth=false`；旧密码／OTP／社交主入口也不能作为回退。

上游配置必填 phone、验证 OTP、新账号不强制密码、关闭自动账号关联与跳过必填身份。只有正式资质和私有凭据齐备才配置真实连接器；stock QQ 需要 UnionID，微信网站应用不等于公众号 H5。禁止直接导入未验证 primaryPhone 来伪造手机证明。Allira `/auth/options` 只报告非秘密配置，不探测模型或保证 Logto 可达；普通身份与 Logto 管理员、Allira 独立管理员互不赋权。

[实际 OIDC 专项](../apps/dev-console/tests/live-oidc.spec.ts)只在 root 明确维护 READY、现有单 API 确认模型 Mock、真实 Logto 官方 Mock SMS／Mock Social 已启用后执行；默认回归不发真实网络。官方 OTP 文件是 `/tmp/logto/mock_sms_record.txt`，服务器 owner=allira／目录0700／文件0600／nlink1／无符号链接，覆盖保存最后一条；测试在本次发码成功后只私读新鲜、精确号码和 Register／SignIn 用途的随机码，不走 HTTP debug、不输出、不并发发码。A/B/C 合成旧 owner 与新号码只由测试进程读取私有 fixture；不得改真实旧账号或把秘密传到构建/Storage/日志/截图/trace。

维护前确认无活动与预扣，保存原模型字节摘要、正常 identity 覆盖层、unit、dist；停写 API／Logto 后分别创建 SQLite 与 PG 私有快照，不称为跨库分布式原子备份。维护期间只临时改独立 identity 覆盖层的模型 Mock 和模拟标志。BUSINESS_FREEZE 后先关闭官方 Mock／删除 OTP、逐字节恢复正常 identity 覆盖层、重启同一 API、核验原 provider 和 OIDC disabled+nonsimulated，再按已保存 owner／手机号精确清理合成业务与上游用户及私有文件；**不得恢复旧数据库来清测试**。本地会话与 pending 撤销不等于 Logto 全局 session/grant 删除。

浏览器 PWA/SW 下不能仅凭 Playwright 的 callback URL + response.status 判断原始 RP 重定向：公开壳的最终 SW 200 可能关联原 callback Request。专项从 CDP 原始 redirectResponse 核验 GET／document／主 frame／303 与固定 `/test/`，再单独验证最终页面、私有 pending 和用户明确继续；不放宽 RP 验签/state/nonce。无身份合成重定向只验证观测方法，不算认证成功。API 0.6 的[旧身份专项](../apps/dev-console/tests/live-identity.spec.ts)及[旧验收](identity-acceptance.md)保留历史，不证明本次实际开源服务接入。

## 同步、GitHub 与磁盘

远端 Git 主分支 `main`，托管目标 [GUYU2233/AlliraAi](https://github.com/GUYU2233/AlliraAi)。修改前确认两端状态和唯一编辑端，只同步明确的源码/锁文件/文档；检查 hash 或 diff，不覆盖其他代理写范围。新增改动是否已发布，以提交和远端核验为准，不能因服务器存在文件就宣称 GitHub 已更新。

禁止同步依赖、dist、缓存、日志、数据库/WAL/SHM、`.env`、`/etc/allira`、私钥、凭据到笔记本。不要清理系统共享缓存来掩盖本项目磁盘占用。本地不执行安装、测试、lint、编译或开发服务。

`allira` 专用 Ed25519 私钥只在服务器 `/home/allira/.ssh`，目录 0700、私钥 0600；SSH 别名 `github-allira` 固定使用它，origin 为 `git@github-allira:GUYU2233/AlliraAi.git`。公钥实际是 `GUYU2233` **账号级 SSH key，不是单仓库 Deploy key**，权限取决于账号；收窄为仓库身份需另行授权迁移，本任务不擅改。

严格主机校验固定 [GitHub 官方 Ed25519 主机密钥](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)，不关闭校验。提交作者 noreply 元数据不代表提交已签名。推送前检查暂存区与凭据扫描，不强推、不覆盖既有远端历史；GitHub 只备份已推送源码，不备份运行数据库/环境/密钥。

## 一致性快照、保留期和恢复演练

[备份 CLI](../server/allira/backup_cli.py)仅处理绝对、无符号链接、正确属主与私有权限的服务器目录。create 使用 SQLite backup API 获取 WAL 一致性副本，不复制正在写的主库文件来冒充备份；快照 0600/目录 0700，排他锁、随机临时目录、校验清单、文件和目录 fsync 后发布。输出不能包含源库或被源路径包含，保留期不删除源库/未知目录。不会按锁年龄擅自删锁；占用时失败并由管理员核查。

已安装的 [oneshot](../ops/allira-backup.service) 与 [timer](../ops/allira-backup.timer)以 allira 运行，**每天 UTC 19:15（北京时间次日 03:15）加最多 5 分钟随机延迟**，Persistent=true；自动目录 `/var/lib/allira/backups/automatic` 仅保留最近 7 份合法、可校验的本工具快照，不把所有旧手工快照算入同一策略。定时器已经启用，业务 API 仍未开机自启；不混淆两者。

```sh
systemctl status allira-backup.timer --no-pager
systemctl list-timers allira-backup.timer --no-pager
# 手动触发相同 oneshot（只读业务库，不重启 API）
systemctl start allira-backup.service
systemctl show allira-backup.service -p Result -p ExecMainStatus
```

CLI 直接使用示例，均仅在服务器：

```sh
runuser -u allira -- sh -c '
  cd /opt/allira/workspace/server
  .venv/bin/python -m allira.backup_cli create \
    --database /var/lib/allira/dev.sqlite3 \
    --output-dir /var/lib/allira/backups/automatic --retain 7
'
# BACKUP_DIR 必须是已经选定的快照目录，输出不包含聊天正文。
runuser -u allira -- /opt/allira/workspace/server/.venv/bin/python \
  -m allira.backup_cli verify --backup-dir "${BACKUP_DIR:?请选择服务器快照目录}"
```

verify 检查严格 manifest、SHA-256、size、SQLite integrity_check/foreign_key_check；后台 freshness 只检查权限安全的有界元数据和大小，36h 视为 stale，**recent 不代表 SHA 或恢复已经通过**。同秒多个备份按固定名称排序，不保证是墙钟最后创建；最新候选损坏会保守显示 invalid，不隐瞒失败。

恢复只能写入**全新离线目标**；目标主库及 `-wal/-shm/-journal` 任一存在（包括悬空符号链接）均拒绝，绝不删除或覆盖它们。恢复副本撤销普通/浏览器会话、管理 Access 摘要绑定、管理 Cookie/登录码、恢复码、phone_challenges、social_browsers/social_oauth_states/social_pending 及 oidc_browsers/oidc_states/oidc_pending（当前实际 12 张短期凭据表，绑定先于父会话删除），保留账号密码摘要/可用标志、已验证手机、持久社交/OIDC 映射与撤销水位、历史和能量。之后仍必须运行当前程序的 schema 迁移、interrupted run 退款与完整性检查，不能把“文件已复制”当作能安全上线。

[恢复演练脚本](../scripts/restore_drill.py)创建新的 0700 drill 目录，只在新副本启动 Database 的 Mock 配置，验证 schema 升级、凭据为零、running/reserved 清零和完整性，不创建第二个 API、不替换运行库、不调用模型：

```sh
runuser -u allira -- /opt/allira/workspace/server/.venv/bin/python \
  /opt/allira/workspace/scripts/restore_drill.py \
  --backup-dir "${BACKUP_DIR:?请选择服务器快照目录}" \
  --drill-dir "${NEW_DRILL_DIR:?必须是私有父目录中的全新绝对目录}"
```

API 0.8 本轮对 schema8 运行库的 577536 字节一致性快照完成校验，在全新离线目标清理 12 张短期凭据表（含 Access 摘要绑定），并验证中断任务/预留能量恢复与完整性/外键；不替换运行库、不启动第二 API、不进行实际登录或调用模型，报告只留服务器私有目录，详见[Access 部署记录](access-admin-acceptance.md)。API 0.7 的 schema7/577536 字节、11 张短期凭据演练见[历史验收](oidc-admin-acceptance.md)。API 0.6 的 schema6/491520 字节演练见[旧身份验收](identity-acceptance.md)，仅是历史证据。此前 schema3→4 同机演练保留在 [API 0.4 历史验收](usability-acceptance.md)，API 0.5 预设与来源上下文见[历史验收](context-companion-acceptance.md)。这些是同机离线副本演练，**不是异机灾备、RPO/RTO 或物理擦除证明**。账号删除只清在线数据，历史备份仍可能保留被删账号、旧密码摘要和聊天；将旧库直接上线会回滚删除/密码变更。不得自动替换当前库；真实故障恢复需停服务、保存当前状态、检查历史与凭据风险，再进行经授权的数据迁移和切换，不能删库掩盖迁移失败。

当前业务库为增量 schema8：schema7→8 仅新增级联管理会话的 admin_access_sessions 摘要绑定；此前 schema6→7 新增 OIDC 持久映射、撤销水位与过渡流程，保留既有账号 ID/hash/salt、历史及能量；不把旧标准 scrypt 密码导入不兼容的上游算法。默认 OIDC 模式要求持久 issuer 映射，未迁移旧会话与已关闭的开发会话不能继续访问。现有唯一服务已增量到8，不能回滚旧库来清测试；schema5→6 与此前 schema3→4 演练只是历史证据，尚非任意历史迁移／降级框架。SQLite 定时备份不覆盖新增身份数据库；另用 [PG 原生备份器](../scripts/logto/backup.py) create-and-verify，只在新的随机离线数据库验证后精确删除，不改运行库／角色／timer。联合恢复还须协调两库、tenant 角色和受保护 runtime/env，当前没有联合自动灾备。启动会将遗留 running 标为 failed/server_restarted 并按现存 usage 退款；只允许一个 API 实例，禁止第二实例误恢复同一运行库。

## 公开部署仍需的条件

本次仅部署授权的 Access 后台域名/TLS/Tunnel；普通业务公开域名与 Logto 回调、生产安全评审、异机备份/灾备、分布式风控与任务所有权、外部监控告警、邮件/2FA/换号找回、支付和真实成本计价、完整内容合规与移动真机仍未完成。隔离回归与未登录重定向不是实际管理员登录验收；实际登录由用户自行测试。仅改 dev→production 不等于上线，不能放开原生端口或删除 Access/CSRF 门禁。
