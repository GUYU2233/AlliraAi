# 专用 Allira 管理后台 Cloudflare Tunnel

## 范围与冻结门禁

本目录管理独立 `allira-admin-dsah` locally-managed Tunnel，不复用或修改现有 DSH/dsh-web Tunnel。准备器只负责 create/validate；root 已在源站 API 0.8/schema8 与负向门禁通过后，单独安装并启动 connector、创建唯一 DNS。入口为 [https://dsah.guyufl.cc.cd/admin/](https://dsah.guyufl.cc.cd/admin/)，本轮实证见[Access 部署记录](../../docs/access-admin-acceptance.md)。用户明确实际登录和使用自行测试；我们只做隔离回归、源站无凭据拒绝和公网不跟随登录的重定向检查。

仅允许管理域名 `dsah.guyufl.cc.cd`。Access 应用 ID 为 `85d627b2-b8dc-4d52-ab1e-8882ee8da2a0`；team domain 为 `guyufl.cloudflareaccess.com`，cloudflared `teamName` 使用 **`guyufl`** 而非完整域名，AUD 固定为 `5d0bf52323efc13e59ce561efbfa100b2c9f443f4e2b7b420aea6e381cbb6621`。允许邮箱由 root 的 Access 策略与源站安全配置独立控制，此处不读取、复制或打印。`/self-test` 等任何未明确允许的路径都不发布，匹配 Tunnel 的 404 规则；未登录时边缘 Access 可能先要求认证，不能仅凭边缘跳转推断已命中源站。

## 实际开源复用与固定版本

直接使用 Cloudflare 官方 `cloudflared` **2026.11.1**，tag 对应 commit `518d7f7969b764df628bb4b1088f0f154901dc4e`。运行包固定在 `/opt/allira/tools/cloudflared-2026.11.1/cloudflared`，大小 **40,346,301** bytes、SHA256 **`97bb3b02d23a5980fc9c15a1a270e692da1923f103f920d5b16d4931d5ab55e7`**；校验器每次检查，不下载 `latest`，不自动更新。Tunnel 核心、Access JWT 验签、路径规范化、TLS 和重连均由上游承担，没有自制隧道或 JWT 核心。Allira 仅有路径/文件权限/输出脱敏/版本锁定胶水。

上游许可证为 [Apache-2.0](https://github.com/cloudflare/cloudflared/blob/518d7f7969b764df628bb4b1088f0f154901dc4e/LICENSE)，发行包及第三方许可不删改。Cloudflare 边缘、DNS、Access 是托管服务，不是自托管 OSS；此处不把商业托管能力声称为开源服务，也不承诺免费额度或企业特性可用。

## 文件与私密边界

- [准备/校验器](../../scripts/cloudflare/tunnel.py) 只能接受 `create` 或 `validate`，没有 start、route dns、delete 或修改旧 Tunnel 的命令。
- [systemd 模板](allira-admin-tunnel.service) 使用 `allira`、`NoNewPrivileges`、空 capabilities、只读系统、私有 tmp、隐藏 home、严格网络 family；仅 Tunnel 私有目录可写。没有 `[Install]`、不会开机启动；源文件本身不执行服务安装。预启动必须通过固定 binary+配置+官方 ingress 离线校验。
- `/var/lib/allira/cloudflare/` 为 allira:allira `0700`；官方生成的 UUID credentials、`config.yml`、状态与锁文件为 `0600`。本目录不可同步、下载或纳入源码/日志；凭据内容及 hash 都不输出。
- 官方 login certificate 保持原位置 `/home/allira/.cloudflared/cert.pem`，仅检查 regular/owner/`0600`/nlink=1/非符号链接。只有创建时的**官方 CLI**读取，Python 胶水不读。运行仅凭新 UUID credentials，不需要 cert；systemd `ProtectHome=true` 隐藏登录 cert。
- 创建输出（可能含敏感数据）全部捕获，不原样写日志；仅允许解析 `id`/`name`，只存非秘密 Tunnel UUID 和固定元数据。失败输出统一脱敏，不自动再次创建，以免重复资源。`create-config.yml`/pending 文件存在就拒绝重试，异常须人工检查，不能自动删除后盲重试。
- 正常服务 stdout/stderr 均丢弃，避免 cloudflared 在错误或 debug 日志中记录请求/身份头；管理 app 自身保留最小脱敏诊断。无 token/秘密命令行参数，无日志级别提升。

## 精确入口与 Access 双重保护

私有配置是 JSON（YAML 子集），由脚本根据非秘密 UUID 生成并逐字段相等验证。三条允许规则全部同时要求：

1. hostname 精确为 `dsah.guyufl.cc.cd`，不匹配其它主机或通配子域；
2. path 仅 `^/admin/?$`、`^/api/v1/admin/.*$`、`^/console-assets/admin-[A-Za-z0-9_-]+\.(js|css)$`；
3. service 只能是 `http://127.0.0.1:8000`，`httpHostHeader` 保持管理域名；
4. 每条配置 `originRequest.access.required=true`、固定 teamName 与唯一 AUD；最后 catch-all `http_status:404`。

不暴露普通聊天/账号/登录/OIDC、测试页、health、Logto 3001/3002、Postgres 或其它本机端口。Tunnel 中 JS/CSS hash 格式仅是第一层：**源站还必须只提供当前构建恰好两个管理 hash 资产**，不得因该格式扩大源站资源 allowlist。源站继续检查 Access 签名/issuer/audience/唯一邮箱，不信任用户自行发送的邮箱或裸 header；边缘 Access、Tunnel origin Access 与源站检查各自不能替代其它门禁。

`metrics=127.0.0.1:0` 不创建公网监听；明确固定 `protocol=http2`（向边缘出站 TLS）。`disable-path-normalization=false` 显式开启该版本的安全路径规范化，避免默认关闭导致 dot segment/backslash/重复 slash/semicolon 混淆命中未保护规则。**仅规范化匹配，转发源站的原始路径不被重写**，故源站仍需严格拒绝异常路径，不应放宽。

## 远端低权限准备

仅在用户授权登录证书已经到位后执行。所有命令都在 `hklite-lv4`，低权限 allira；本地仅编辑/同步源码。

```sh
# root 仅初次创建指定私有目录；不读取其内容。
install -d -o allira -g allira -m 0700 /var/lib/allira/cloudflare
# 一次性真实创建新专用 Tunnel。仅创建资源和凭据，不建立 connector 或 DNS。
runuser -u allira -- /usr/bin/python3 /opt/allira/workspace/scripts/cloudflare/tunnel.py create
# 后续离线校验：只检查文件元数据/固定配置并解析 ingress，不调用 origin/API/DNS。
runuser -u allira -- /usr/bin/python3 /opt/allira/workspace/scripts/cloudflare/tunnel.py validate
```

验证包含官方 `tunnel ingress validate` 与 `tunnel ingress rule URL` 的允许/拒绝测试。此流程不探测真实源站、Access 登录、浏览器或公网 DNS。实际发布后的管理员登录/使用按用户要求由用户自行测试，不代走身份验证；不能把离线规则、无凭据 302 或合成 JWT 检查说成真实 Access 端到端验收。

## 准备阶段实证（历史步骤，不等于当时已上线）

已通过低权限官方 CLI **一次**创建专用 Tunnel：UUID `ba1f6a67-3450-4aa3-b94d-7fdfdda76d0a`，name `allira-admin-dsah`。首次胶水因官方 credentials 默认 `0400` 而拒绝期望 `0600`；随后只以官方精确 name list 读回非秘密 UUID，核验该 Tunnel 连接数为零，未再次 create、未删除或修改任何其它 Tunnel。只对安全 owner/regular/nlink1/no-symlink 的 pending credentials 做 `fchmod(0600)`，不读内容，再完成 UUID 文件和私有配置。

官方 `ingress validate` 与 **25** 个离线规则用例通过（6 允许、19 catch-all404）；仅连接源站管理 path 的规则含固定 Access 校验和 Host header，普通业务/Logto/测试页/其它域名均匹配404。配置 canonical JSON SHA256 为 `43805009f8f938b5c16eff9cb312034c7ba8a32c7de547e6eba6d6d2e3b87223`（不是 credential hash）。源码远端 Ruff/语法与 systemd 模板校验通过；本步骤没有安装/启用/启动 systemd、DNS 写入、源站请求、Access 登录或浏览器验收。

## 已发布状态与紧急停止

root 已安装同名 systemd unit 并显式启动，新 Tunnel 健康；仅新增代理 CNAME `dsah.guyufl.cc.cd` → `ba1f6a67-3450-4aa3-b94d-7fdfdda76d0a.cfargotunnel.com`（TTL Auto）。API、Logto 与 PG 保持各自回环监听，未开放入站端口、未变更其它 Tunnel 或 DNS。`allira-admin-tunnel.service` 使用 `After` + `Wants=allira-dev.service`：部署时短暂停止或重启 API 不再连带停止 connector，API 不可用期间由隧道返回源站不可用，API 恢复后即可继续转发。不要改回 `Requires`，否则手动停止 API 后 connector 会停止，却不会随 API 启动自动恢复。该 unit 仍为 static、无开机自动启动；服务器重启后需要授权运维启动并检查，不承诺当前连接永久在线。

以下仅在受保护的服务器管理员终端执行，不输出证书、UUID credentials 或请求头：

```sh
systemctl show allira-admin-tunnel.service -p ActiveState -p SubState -p User -p NoNewPrivileges
# 紧急关闭新后台公网入口，保留源站 Access fail-closed：
systemctl stop allira-admin-tunnel.service
# 排障完成后显式恢复（必须先确认原单 API、固定配置与离线 validate）：
runuser -u allira -- /usr/bin/python3 /opt/allira/workspace/scripts/cloudflare/tunnel.py validate
systemctl start allira-admin-tunnel.service
```

不要通过关闭源站验签、改回 link、开放 API 入站或恢复旧数据库作为回滚。DNS 删除或 Access 策略扩大另需明确授权；本次只允许该唯一 hostname 和精确已确认邮箱。无凭据 HTTPS 请求至管理页/管理 API 会302到固定 Access team，未跟随登录；真实管理员登录与操作仍由用户测试。

## 官方证据

- [官方 locally-managed Tunnel 创建流程](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/)：create 与 DNS route/run 是独立步骤。
- [官方 Access origin 参数](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/#access)：`access.required`、teamName、AUD 与请求断言验证。
- 固定源码 [Access 配置类型](https://github.com/cloudflare/cloudflared/blob/518d7f7969b764df628bb4b1088f0f154901dc4e/config/configuration.go#L237-L250)、[每条 ingress 注入官方 JWT validator](https://github.com/cloudflare/cloudflared/blob/518d7f7969b764df628bb4b1088f0f154901dc4e/ingress/ingress.go#L429-L437)、[路径规范化只用于匹配](https://github.com/cloudflare/cloudflared/blob/518d7f7969b764df628bb4b1088f0f154901dc4e/ingress/ingress.go#L40-L80)。
- 固定源码 [官方创建/私有 credentials 写入与可选 JSON 输出](https://github.com/cloudflare/cloudflared/blob/518d7f7969b764df628bb4b1088f0f154901dc4e/cmd/cloudflared/tunnel/subcommand_context.go#L126-L200)。
