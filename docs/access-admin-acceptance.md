# Cloudflare Access 后台部署与边界记录（API 0.8）

日期：2026-11-20（Asia/Shanghai）。本轮按用户要求关闭管理一次性链接，发布独立后台域名；**实际 Access 登录、2FA 和后台使用由用户自行测试**。本记录不把合成 JWT、隔离页面或无凭据 302 检查写成真实管理员登录验收。[API 0.7 记录](oidc-admin-acceptance.md)保留普通 Logto 与旧后台的历史证据，不代表当前管理登录方式。

## 1. 当前入口与发布范围

- 管理入口：[https://dsah.guyufl.cc.cd/admin/](https://dsah.guyufl.cc.cd/admin/)。Access 应用 `85d627b2-b8dc-4d52-ab1e-8882ee8da2a0`，team `guyufl.cloudflareaccess.com`，会话 1h，仅允许经用户确认的 `chuluoxiang@gmail.com`，没有整域名邮箱、everyone、bypass 或 login_method 泛放行。
- 专用 Tunnel `allira-admin-dsah`，UUID `ba1f6a67-3450-4aa3-b94d-7fdfdda76d0a`。仅新增 zone `guyufl.cc.cd` 下的代理 CNAME：`dsah.guyufl.cc.cd` → `ba1f6a67-3450-4aa3-b94d-7fdfdda76d0a.cfargotunnel.com`，TTL Auto。没有更改其它域名、现有 DSH Tunnel、防火墙或 SSH。
- 官方固定 cloudflared 2026.11.1，仅允许 `/admin`、`/admin/`、`/api/v1/admin/*` 和管理 hash JS/CSS；源站进一步只允许**当前构建恰好两个**管理资产，其余路径拒绝。公网未登录时 Access 可先对所有路径重定向；这不代表被拒路径已发布或已经访问源站。
- 普通聊天、业务登录/API、PWA、文档、health、Logto 3001/3002 与 PostgreSQL 都不通过此域名发布。API 继续只监听 `127.0.0.1:8000`，单进程/worker，无热重载、不信任转发头；普通功能仍经 SSH 隧道使用。

## 2. 认证与旧入口关闭

- 三层门禁：边缘精确邮箱 Access 策略；Tunnel 每条允许规则的固定 team/AUD 与 `access.required=true`；源站每请求由现有 joserfc 校验 RS256/public RSA kid、固定 issuer/AUD、type=app、subject、精确邮箱和期限。JWT 核心与隧道直接复用成熟库/官方二进制，不自造密码学。
- 源站仅接受单个 ASCII `Cf-Access-Jwt-Assertion`（最多16KiB），不信裸邮箱头、Authorization、用户 Cookie 或旧管理 Cookie 作为替代。JWKS 固定 HTTPS、64KiB/15s、无 proxy/redirect/compression；缓存最多300s、单飞刷新、未知 kid/失败退避，不使用过期缓存。
- 管理 Cookie 是 host-only/Secure/HttpOnly/SameSite=Strict、Path=/api/v1/admin；本地仅存 subject/email/完整断言摘要，并将期限限制在 JWT 到期与本地 TTL 内。断言更新后须重新 GET session 轮换，旧摘要绑定不能继续使用。写请求额外要求固定 HTTPS Origin 与独立内存 CSRF。
- 页面进入即清 hash、不解析或兑换 fragment，直接 GET 管理 session；401 提供固定 `/admin/` 完整导航。退出仅在当前 epoch 成功响应后导航固定 `/cdn-cgi/access/logout`，不相信任意返回 URL，失败/迟到响应不导航。
- 默认 `admin_auth_backend=access`；CLI `issue-login` 拒绝签发，格式有效的旧 POST session 返回 `403 admin_link_disabled`。Access 启动清除旧未用 code、旧管理会话及绑定，不保留旧链接/旧 Cookie 回退；显式 link 只用于隔离历史兼容测试。
- 仅为 Access/IdP 返回保留窄导航兼容：精确 Host、实际回环 peer、GET/HEAD `/admin[/]`、navigate/document 可继续到 JWT 校验；不提前返回页面。错误 Origin、API、资产、iframe/fetch 和写请求的 cross-site 仍拒绝。

## 3. 远端隔离验证

所有安装、lint、测试、编译和服务均在 `hklite-lv4`，项目命令由低权限 allira 在 `/opt/allira/workspace` 执行；本地只编辑/同步轻量源码与文档。

| 门禁 | 结果与证据边界 |
| --- | --- |
| 后端 | Ruff 通过；全量 pytest **1434 passed / 173.72s**。包含175项合成 RSA/joserfc/JWKS 单元测试及76项真实 create_app 的隔离 Access 集成测试；不是实际 Cloudflare 登录 |
| SDK | TypeScript build 与 **452/452**；149个有效声明反例。含旧436与新增16项可选 Access DTO、兼容及竞态检查；FakeFetch，不是实际 API |
| 原生页面 | src typecheck、隔离 `.test-dist` build、Node **25/25**、13份完全拦截浏览器路由 **110/110**（旧97+新增13），单次远程命令94.445s；没有运行实际 Access 登录或历史 link LIVE |
| Tunnel | 官方固定版本/配置校验及25项离线规则用例（6允许、19 catch-all404），另有配置/权限/UUID拒绝用例；不是浏览器认证验收 |

保留两个既有告警：Starlette TestClient 的 httpx 迁移提示，以及 Authlib httpx 模块建议改用 httpx2。未为消除告警更换 MockTransport 或减弱安全断言。首次全量回归的唯一失败为历史 SimpleNamespace 夹具缺少新管理配置；只补显式 link 兼容字段，随后全量通过。旧 schema 测试只更新当前版本和新增绑定表，历史合成 schema7/手动 OIDC 历史用例保留。

## 4. 实际部署与非登录检查

- 维护前确认运行/预留为零，备份原 unit、dist、三份受保护环境及 SQLite 一致性快照；只新增第四层 `/etc/allira/access.env`（root:allira 0640），不改原 dev/provider/identity 配置字节，不调用真实模型、短信或腾讯。
- 在服务器低权限 normal build 后重启**原有唯一** API，部署 API **0.8.0/schema8**。schema7→8 仅新增级联 `admin_access_sessions` 摘要绑定，不删除/覆盖原业务账号或历史；部署前后15张业务表计数一致，外键通过、running/reserved=0、旧管理证明为零。
- 源站非登录负向检查：管理页面/API/当前两个管理资产无 JWT 均401；裸邮箱头/无效JWT仍401；公共 Host 下11类普通业务路径404；未知 Host403；有效格式旧 code POST403；回环管理 session 不绕过 Access。私有 health/ready/客户端保持可用，模型 provider 为原 openai，普通 OIDC 关闭且 nonsimulated，旧手机/社交通道关闭。
- 官方配置离线校验通过后才单独启动新 connector，再创建唯一 CNAME。connector 健康；无凭据 HTTPS 请求得到302至预期 `guyufl.cloudflareaccess.com`，**不跟随重定向、不执行登录、不读取真实 JWT/会话**。
- 导出 OpenAPI **0.8.0/102 paths**；实际运行 **101 paths**，仅少 `/api/v1/dev/session`，因为导出覆盖显式开发契约而部署 `allowDevAuth=false`。这不是构建漂移。
- 单个 API、Logto、PG 保持回环与原 boot-disabled 策略；专用 Tunnel unit 为显式启动的 static unit、未自动开机启用。服务器重启后应按运维说明启动并检查，不把当前健康写成永久可用保证。

初次部署脚本的负向探针因旧 code 太短（触发422）及 readiness 预期 `ok` 而非 `ready` 停止；DNS/connector 当时仍未发布。只纠正探针并独立核验，不重复部署、不回滚旧运行库、不放宽产品门禁。发布后最初403在配置传播后变为预期302，未为此放宽策略。

## 5. schema8 同机恢复演练

对本轮 schema8 的 **577536字节**一致性快照进行只读 manifest/SHA/完整性验证，再恢复到全新0700离线目录：12张短期凭据表（含 Access 绑定）清空，中断 run/预留能量恢复、SQLite integrity/FK 均通过。没有替换运行库、启动第二 API、登录后台或调用模型；报告与副本只留服务器私有目录。

最初探针误读 create_backup 返回值中的 `status` 字段而报 KeyError；快照本身已安全发布，随后复用该新快照 verify 后完成演练，没有盲目重建或回滚数据库。此结果不是异机灾备、物理擦除、RPO/RTO 或双库联合自动恢复证明；Logto PG/角色/环境另有独立恢复边界。

## 6. 用户测试与回滚边界

用户可直接打开管理入口，以已确认邮箱经过 Access 登录；本轮**未代做真实登录、2FA、授权后读写或退出的端到端验证**，因此尚不能承诺真实 IdP 回跳及全部使用体验。若登录后异常，先核对固定域名、精确邮箱、应用 AUD 与源站配置，不关闭验签/CSRF、不恢复旧链接、不公开普通业务作为排障捷径。

紧急关闭公网只停止新专用 `allira-admin-tunnel.service`；需要撤回 DNS 时只针对上面的唯一 CNAME，需明确授权，不修改既有其它 Tunnel。保留 Access/源站 fail-closed 门禁及 schema8，不恢复旧库或直接把旧 dist/旧 link 认证上线。临时授权证书/UUID credentials 仅在服务器0700/0600路径由官方CLI读取，不同步、不哈希输出、不放进聊天/源码。

固定版本/许可及启动约束见[第三方声明](third-party-notices.md)、[Tunnel 运维](../ops/cloudflare/README.md)、[远程开发](remote-development.md)。源码部署并不等于 GitHub 发布；本轮未 stage、commit 或 push，最终核验以远端 Git 与源码清单为准。

## 7. 最终只读复核

- 202份轻量源码/文档/锁文件与远端 Git tracked+untracked 非忽略清单逐项一致，237个本地 Markdown 文件链接有效；未同步依赖、dist、数据库、环境文件或凭据。原 dev/provider/identity 三份受保护环境字节保持不变，新增 Access overlay 未漂移；源码中未发现实际上游/OIDC秘密或私钥。
- 最终 Ruff 与 Access 单元/集成子集 **251 passed / 29.04s** 再次通过；该子集包含于此前全量1434，不重复累加。API、Logto、PG、专用 connector 均以 allira/NoNewPrivileges 运行，前四个业务监听端口仍仅127.0.0.1；unit 与源码一致，备份 timer 活跃，schema8/FK 与 running/reserved 检查通过。
- 官方 Tunnel 离线配置与25项规则复核通过；只核验远端证书/UUID credentials 的权限、属主、单链接和非符号链接元数据，未读取或哈希其内容。审计脚本曾误用 HTML/state 文件位置；按实际构建与准备器路径纠正后通过，没有为审计改动或重建运行服务。
- 使用浏览器式请求头、不带 Cookie/JWT、不跟随重定向复核时，入口与管理 API 均302到预期 Access team。部分通用脚本请求曾被边缘直接403，未因此修改或放宽任何 Cloudflare 策略；302和403都不证明真实身份登录、授权后使用或退出已验收。最终审计未撤销用户会话、发起生成或代做登录。
