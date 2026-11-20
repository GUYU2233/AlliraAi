# 私有 Logto OSS 运行与验收交接

本目录配合 [运行包装器](../../scripts/logto/runtime.py)、[固定发行包准备器](../../scripts/logto/prepare.py)、[官方管理 API 配置器](../../scripts/logto/manage.py)、[原生验收脚本](../../scripts/logto/native-proof.mjs) 和 [原生 PostgreSQL 备份器](../../scripts/logto/backup.py)。不替代 AlliraAi 的账号映射、业务会话、CSRF、明确迁移或数据删除流程；不启动第二个 Allira API。

## 固定上游与许可

- 上游：Logto OSS `v1.46.0`，commit `d179bf9d2d51104236a342b90c2841df66e4ba5b`。
- [官方 release](https://github.com/logto-io/logto/releases/tag/v1.46.0)；[固定 commit 的 package.json](https://github.com/logto-io/logto/blob/d179bf9d2d51104236a342b90c2841df66e4ba5b/package.json)。
- 官方 `logto.tar.gz`：291001448 bytes；SHA-256 `140ca60719d60866782527aeb4cf72caee6d3fba3c730b9214e0a43326a96382`。准备器会拒绝校验不一致。
- MPL-2.0；发行包内 `LICENSE` 和所有第三方声明完整保留，不把上游发行包复制进业务源码。分发修改后的 MPL 文件时应依 MPL 提供相应修改源码及声明。
- Node 支持约束为 `^22.23.4 || ^24.22.1 || ^26.12.1`。远端实际 Node `24.22.3` 满足，不代表任意 Node 24 都支持。
- 实际使用 stock hosted UI，保留 Powered by 品牌；不用付费 Bring your UI、隐藏品牌、多控制台成员或强制管理员 MFA。参见[自托管许可边界](https://docs.logto.io/logto-oss/self-hosted-license)。手机注册、社交补全、OIDC 和管理 API 在开源核心中，不依赖云账户或许可刷新。

## 唯一上游补丁：显式回环监听

[上游 init.ts 固定源码](https://github.com/logto-io/logto/blob/d179bf9d2d51104236a342b90c2841df66e4ba5b/packages/core/src/app/init.ts) 的 HTTP/HTTPS 核心及管理员监听共四处没有 host 参数。`ENDPOINT` 只设置 URL/issuer，不限制绑定地址。

准备器同时修改上述源码和发行包 `packages/core/build/main-5SWK6CWG.js`：给四处 `.listen(port, callback)` 增加第二参数 `'127.0.0.1'`，不改认证、OTP、OIDC 或 claim 实现。原 bundle SHA-256 `e58ee9a9451d8e4847fa7c60c235f1c96ae33dcf293aa2440a7b5ae7e0a9b7d7`；每个修改前原文件、unified diff、修改前后摘要保留在运行目录 `allira-patches` 与 `allira-preparation.json`。每次启动先复核摘要和四处回环监听，非固定版本拒绝启动。

## 运行隔离与秘密

- 远端只用 `allira` 用户；运行根目录 `/var/lib/allira/logto` 为 `0700`，秘密文件 `0600`。
- 核心 `127.0.0.1:3001`、管理 `127.0.0.1:3002`；PostgreSQL `127.0.0.1:54329`，独立于 Allira API `127.0.0.1:8000`。没有公网域名/端口、防火墙或 SSH 配置变更。
- [独立 unit](allira-logto.service) 依赖 `allira-identity-db.service`。root 负责安装/重启已有 managed unit，默认不启用开机自启；包装器不会自行启动数据库或 API。
- 仅 `/var/lib/allira/logto/runtime.env` 存放该实例 `DB_URL`、issuer、RP/M2M client ID/secret。解析器不使用 shell/source/eval，拒绝符号链接、非 owner、宽权限、多硬链接、未知键和重复键；不会输出内容。核心进程环境不传入 RP/M2M secret。
- 首次 `db seed --skip-when-exists --disable-admin-pwned-password-check` 需要临时 CREATEROLE，已由 root 在 seed 后撤销；运行角色 `logto` 的 superuser/createdb/createrole/replication 均应为 false。不要为了重跑 seed 擅自改权限。
- [管理员初始化脚本](../../scripts/logto/admin.mjs) 只用于全新实例首次管理员 hosted UI 注册，将随机凭据与暂用 bearer 写入私有文件，不是普通用户的开发登录后门。不要在已有实例盲重跑 bootstrap。
- [管理配置器](../../scripts/logto/manage.py) 的 bootstrap 通过上游 API 创建 Traditional RP 与具标准管理角色的 M2M client；从 `/api/applications/:id/secrets` 私下取值写入受保护文件，不要求用户在聊天中发送秘密。

真实 discovery：`http://127.0.0.1:3001/oidc/.well-known/openid-configuration`，issuer 精确为 `http://127.0.0.1:3001/oidc`。默认签名算法实测为 `ES384`（EC P-384），PKCE 为 `S256`；不能把模拟 RS256 发行方当作该服务验收。所有发现端点均为同一 origin。

## 登录页品牌图：官方配置复用公开本机资源

Stock OSS 的 [SIE seed](https://github.com/logto-io/logto/blob/d179bf9d2d51104236a342b90c2841df66e4ba5b/packages/schemas/src/seeds/sign-in-experience.ts#L28-L31) 默认指向 `https://logto.io/logo.svg` 和 `logo-dark.svg`；这些是被动图片，不是 OAuth 或短信通信。离线测试仍保持外网阻断，不为默认 logo 放开外网。

[管理配置器](../../scripts/logto/manage.py) 的独立 `branding-local` action 只读正常 `dist/test/manifest.webmanifest`，选择唯一 `192x192`、`image/png`、`purpose=any` 图标；路径须精确匹配 `/console-assets/public-icon-192-<16位小写SHA256前缀>.png`，从正常 `dist/assets` 验证内容摘要、PNG 签名及 192 尺寸，拒绝符号链接、非 owner、多硬链接、组/其它用户可写文件。不会读取隔离 `.test-dist`、重建静态资源或向 Allira API 发请求。

```sh
# 正常维护配置；不启用 Mock，不重启任何服务。
runuser -u allira -- /usr/bin/python3 /opt/allira/workspace/scripts/logto/manage.py branding-local
```

该命令复用既有标准 M2M 管理权限，先 GET 原 SIE/零连接器，再仅以 `PATCH /api/sign-in-exp` 的 `branding` 字段，把 `logoUrl`/`darkLogoUrl` 指向已公开的 `http://127.0.0.1:8000/console-assets/...png`。先合并原 branding 其它键，避免 [JSONB replace](https://github.com/logto-io/logto/blob/d179bf9d2d51104236a342b90c2841df66e4ba5b/packages/core/src/queries/sign-in-experience.ts#L19-L22) 清掉已有 favicon。PATCH 后重新 GET，并严格比较整个 SIE 除这两个 logo URL 外完全未变、连接器仍为零；输出只有静态结果与数量，不打印配置或秘密。应用或图标重新构建后应在维护窗口明确重跑，不能长期硬编码旧哈希。

普通 logo URL 是 [OSS branding schema](https://github.com/logto-io/logto/blob/d179bf9d2d51104236a342b90c2841df66e4ba5b/packages/schemas/src/foundations/jsonb-types/sign-in-experience.ts#L26-L33) 原生允许的配置，[商业许可检查](https://github.com/logto-io/logto/blob/d179bf9d2d51104236a342b90c2841df66e4ba5b/packages/core/src/routes/sign-in-experience/index.ts#L353-L397) 不限制它。不会发送 `hideLogtoBranding`、自制 UI、CSP、手机策略或 SMS/social 字段；Powered by Logto 的 inline SVG/链接完整保留。这是官方配置，不是新上游补丁；本机图标与 Logto 是不同 origin（端口不同），但均为已有公开 loopback allowlist 资源，不应把它说成 Logto origin 同源资源。

已在正常关闭渠道状态实际执行一次该官方管理配置：公开图标校验通过、整个 SIE 仅 logo URL 变化、认证策略未变、连接器仍为零、Powered by Logto 保留。远端 allira Ruff/语法检查和不联网的路径/摘要负向测试通过；本次不运行浏览器或 LIVE，不把管理配置成功表述为真实登录、图像渲染或短信渠道验收。

## 正常配置与维护 Mock

正常数据库策略：`signUp.identifiers=['phone']`、`password=false`、`verify=true`；`signIn.methods` 只有 phone+verificationCode，密码非必需；`socialSignIn.automaticAccountLinking=false`、`skipRequiredIdentifiers=false`。正常 SMS/social connector 数量均为零；无真实渠道资质与受保护配置时不能承诺可注册、微信或 QQ 已可用。业务 RP 应报告不可用并保持渠道禁用，而不是绕过验证。

以下仅在 root 明确维护 READY、没有并发短信测试时执行。不会修改真实 provider/model 配置：

```sh
# 运行已有固定版本/摘要复核，不联网下载安装。
runuser -u allira -- /usr/bin/python3 /opt/allira/workspace/scripts/logto/prepare.py --verify-only
# 只链接发行包内官方连接器，首次执行后需 root 重启 managed Logto 读取 factory。
runuser -u allira -- /usr/bin/python3 /opt/allira/workspace/scripts/logto/runtime.py cli connector link --mock -p /var/lib/allira/logto/releases/v1.46.0
# 暂时激活两个官方 Mock（要求当前无其它 connector）。
runuser -u allira -- env ALLIRA_LOGTO_MAINTENANCE=1 /usr/bin/python3 /opt/allira/workspace/scripts/logto/manage.py mock-on
# 原生 Logto 验收；8000 回调被 CDP 阻断，不作为 Allira 业务链验收。
runuser -u allira -- env ALLIRA_LOGTO_MAINTENANCE=1 /opt/allira/tools/node24/bin/node /opt/allira/workspace/scripts/logto/native-proof.mjs social
# 仅删除带专有 allira-native-social- 前缀、且仅关联官方 mock-social 的原生 fixture。
runuser -u allira -- env ALLIRA_LOGTO_MAINTENANCE=1 /usr/bin/python3 /opt/allira/workspace/scripts/logto/manage.py clean-native
# 关闭维护：只删除两个官方 Mock DB connector 和私有 OTP 文件；不删除其它账号。
runuser -u allira -- /usr/bin/python3 /opt/allira/workspace/scripts/logto/manage.py mock-off
runuser -u allira -- /usr/bin/python3 /opt/allira/workspace/scripts/logto/manage.py status
```

上游官方 factory ID 是 `mock-short-message-service`（target `mock-sms`）与 `mock-social-connector`（target `mock-social`）。它们在固定官方发行包中真实存在；不自写验证码/OAuth 核心。配置使用上游 dummy config-template，绝无真实短信或腾讯调用。

Mock SMS 将最后一条记录覆盖写入 `/tmp/logto/mock_sms_record.txt`，JSON 为 `{phone,code,type,payload}`，手机号为国家码+数字（无 `+`），`type` 为 `Register` 或 `SignIn`，`payload` 有 code/locale。目录 `0700`、文件 `0600`。自动化只以 `O_NOFOLLOW` 打开，检查 owner/type/mode/nlink、新鲜 mtime、精确测试手机号及用途后在内存读取 code；不经 HTTP、UI、日志或聊天输出。文件只能容纳最后一条，禁止并发发码。

原生 UI 英文控件实证：初始 `input[name=identifier][type=tel]`，国家按钮默认 `+1`；点击该按钮、填写 `input[name=country-code-search]` 为 `86`、点击精确 `+86` 按钮，再填国内 11 位。直接注册 Create account，登录 Sign in，社交补手机页 Continue。官方验证码发送 API 为 `POST /api/experience/verification/verification-code`，非旧 interaction 路径。6 位输入为 `passcode_0` 至 `passcode_5`，最后一位自动提交，不再点击禁用中的 Continue。

[固定上游消息限流默认值](https://github.com/logto-io/logto/blob/d179bf9d2d51104236a342b90c2841df66e4ba5b/packages/schemas/src/consts/message-rate-limit.ts#L42-L45) 为同一归一收件号码滚动 600 秒最多 10 次；Register/SignIn 共享 `VerificationCodeSend` 桶，不是每次必须间隔 60 秒。UI 的重发倒计时与系统窗口不能混同；没有写入内部 override，不得为测试改数据库或绕过限流。每次登录必须读本次新鲜 `SignIn` 用途记录，不能拿注册旧码复用。

Mock Social 仅在隔离浏览器 route `http://mock-social`，保留上游 state 和已核验的 Logto redirect origin，提供测试 stable userId；不给 phone/email，使真实 Logto 必须显示 `/continue/phone`。这是官方 Mock 传输，不是已验证的微信/QQ接入。

## 原生证据和 claim 边界

原生维护验收已实际通过：官方 hosted UI、官方 Mock Social 首次无手机→必补手机、官方 Mock SMS、Authorization Code + S256、真实 ES384 JWKS/issuer/aud/nonce/auth_time 校验、userinfo 与签名 sub 一致、`phone_number_verified=true`、新账号无密码。结束后 native fixture、OTP、两个 Mock connector 已清理，default tenant users 为 0。管理员属于另一 tenant。

实测 `phone_number` 为 `861…`（没有 `+`）。Allira 的可信 issuer claim 边界应只对严格中国号码补 `+` 后交给既有规范化函数；不扩大其它入口的手机号规则。[上游 scope.ts](https://github.com/logto-io/logto/blob/d179bf9d2d51104236a342b90c2841df66e4ba5b/packages/core/src/oidc/scope.ts) 把 `phone_number_verified` 映射为 `Boolean(user.primaryPhone)`，不是独立证明字段。管理 API 导入/写入 primaryPhone 会绕过发码证明；禁止把未验证历史号码直接导入，Mock tenant 证明不等于真实运营商证明。

[上游 password.ts](https://github.com/logto-io/logto/blob/d179bf9d2d51104236a342b90c2841df66e4ba5b/packages/core/src/utils/password.ts) 不支持 Allira 标准 scrypt 参数作为现成 Legacy 算法；不要混同 Firebase scrypt 或悄悄导入旧哈希。保留内部 acct ID 与历史，通过明确旧账号证明+全新手机证明建立 `(issuer,sub)` 映射，不静默合并。

QQ 连接器 stock 强制 UnionID；未来如选择 OpenID，应固定 identity mode 并按 AppID namespace 小改 guard/id 返回且补测试，不能 `unionid || openid` 漂移。当前 QQ 无资质/凭据，未做无意义补丁，保持禁用。微信网站连接器 stock 使用官方 `qrconnect`/`snsapi_login`，但真实验收仍需平台网站应用、获批域名与受保护凭据。两者不是这次 Mock 测试结果。

注销/销户需要 Allira 本地会话及 pending 交易失效、上游 session + grant 链撤销；上游改密码不会自动撤销所有已发 token。离线 JWT 不能魔法瞬时收回；业务本地撤销/状态检查仍必要。不要把 OIDC end-session 当作全部 refresh grant 撤销。

## PostgreSQL 备份与新目标恢复验证

旧 SQLite 备份不包含独立 Logto PostgreSQL，不能再称为完整账号备份。[backup.py](../../scripts/logto/backup.py) 使用已存在的 allira Unix peer 管理角色，只在 `/var/lib/allira/logto/backups/<UTC>_<随机>` 新建 `0700` 目录和 `0600` 文件，原生 `pg_dump --format=custom --no-owner --no-acl`，写 SHA-256/大小/时间 manifest；不回传本地。

```sh
runuser -u allira -- /usr/bin/python3 /opt/allira/workspace/scripts/logto/backup.py create-and-verify
# 验证指定已有备份也只恢复到全新随机 DB，不覆盖运行库。
runuser -u allira -- /usr/bin/python3 /opt/allira/workspace/scripts/logto/backup.py verify --backup /var/lib/allira/logto/backups/20261119T105030Z_a150475d3da4
```

工具固定在 `/var/lib/allira/postgresql/runtime/usr/lib/postgresql/17/bin`，脚本设置私有 runtime libpq 的 LD_LIBRARY_PATH；不把 DBURL/密码放在命令行、HTTP 或输出中。恢复验证先 `pg_restore --list`，再建 `allira_verify_logto_<24hex>` 新库，撤销 PUBLIC CONNECT，只向该库 `pg_restore --no-owner --no-acl`；检查核心 schema/tenant/app 计数，finally 精确 DROP 同一随机库。没有 schema alteration、角色授权变更、数据库服务启动或业务 SQLite访问。

已实测两次 create-and-verify 和一次对已有 dump 的 verify：恢复 79 张表、2 tenants、6 applications、1 管理员；新验证库清除；没有覆盖现存数据库。具体私有 manifest/report 在备份目录，不应发布原 dump。

**边界：** 这是单 PostgreSQL 原生一致快照，不含 SQLite、集群角色、受保护 runtime/env、外部密钥文件，且同机保留不是异机灾备；新集群恢复还需安全重建已有 tenant/运行角色和相应私有配置（当前验证复用了已有集群角色）。Allira SQLite 与 Logto PG 联合备份须由维护窗口协调并记录各自时间，不是跨库分布式原子事务。没有改现有 backup timer、保留策略、自动恢复上线，也不宣称备份中的已删身份已物理擦除。脚本故障会保留私有失败备份供维护，不自动删除或覆写。
