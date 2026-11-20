# Logto OSS 认证与不可变业务账号映射（API 0.8，普通 OIDC 协议沿用 0.7）

设计核验日期：2026-11-19（Asia/Shanghai）。**实际使用 Logto OSS 作为认证核心，Authlib/joserfc 作为标准 OIDC 客户端与验签库；不再把参考开源项目后自写 OTP/OAuth 核心称为复用。** 手机号是经验证的逻辑主登录标识，AlliraAi 内部不可变 `acct_...` 才是聊天、角色、能量等业务外键。真实渠道、真实服务集成及模拟回归是不同的验收层级，不能互相替代。

本文取代 API 0.6 的“自行实现薄认证层”架构选择。旧版本测试和[旧身份验收记录](identity-acceptance.md)属于历史证据，不自动证明本次 OIDC 接入通过；本次真实开源服务与后台的分阶段证据见[当前验收记录](oidc-admin-acceptance.md)。现有业务数据不因切换认证系统而删除或批量改写。

## 1. 实际复用什么

- **Logto OSS v1.46.0**，固定 commit `d179bf9d2d51104236a342b90c2841df66e4ba5b`：[源码与 MPL-2.0](https://github.com/logto-io/logto/tree/d179bf9d2d51104236a342b90c2841df66e4ba5b)。实际运行官方发行包、默认 hosted UI、手机号验证码、社交连接器、OIDC Provider 与 Management API；保留上游品牌及许可文件。
- **Authlib 1.9.9**（[BSD-3-Clause](https://docs.authlib.org/en/stable/community/licenses.html)）：Authorization Code、PKCE S256、OAuth 客户端与 OIDC claim 校验。**joserfc** 负责标准 JWK/JWT 签名验证；依赖版本见[后端依赖](../server/pyproject.toml)和[锁文件](../server/uv.lock)。没有手写 JWT 或另一个身份发行方。
- **PostgreSQL 17.12** 保存上游身份、应用、交互和签名材料；AlliraAi 继续使用现有 SQLite 保存业务数据、身份映射与业务会话。不能再把 SQLite 单库快照称为完整账号备份。
- **AlliraAi 自有胶水**仅负责同源/CSRF、短期浏览器事务、验证后的 `(issuer,sub)` 到业务 ID 映射、明确旧账号迁移、条款与成年自我确认、业务会话和业务数据删除。普通身份不获得管理权限。

固定发行包摘要、官方连接器、监听补丁、运行目录与原生证据见[Logto 运维交接](../ops/logto/README.md)，许可及传递依赖见[第三方声明](third-party-notices.md)。当前唯一上游补丁是四处 HTTP/HTTPS core/admin `.listen` 增加显式 `127.0.0.1`；同时保留源码和发行 bundle 的原文件、diff 与摘要。不改上游认证、验证码或 claim 算法。

### 其他候选的取舍

[Casdoor v4.29.0](https://github.com/casdoor/casdoor/tree/3d81f45bc98be0fe379b3a21973c783060066af5) 有社区功能与商业 edition 边界，不能只看根 Apache 许可推断所有能力免费。本次核验没有充分证明其补手机号提示一定在签发 token 前形成强门禁，因此没有选用，不把“不确定”写成已发现漏洞。

[JustAuth](https://github.com/justauth/JustAuth/tree/694bbf1b010d93404e3bfb4824d90e9ddfaebebb) 是 MIT Java 社交适配库，[SMS4J](https://github.com/dromara/SMS4J/tree/523bedc37a70714e8d08b5448e8bc7bb6fe0509f) 是 Apache-2.0 Java 短信适配库；两者并不共同构成完整身份系统。本次不新增 JVM 中间服务，也没有声称它们已成为运行依赖。

Logto 默认 hosted UI 足够完成本任务，不使用付费 Bring your UI、去品牌或付费控制台管理能力；不因部署了开源版而宣称所有企业功能可用。优先配置、标准协议和小补丁；若将来必须大量重写、购买许可或做不可逆迁移，先向用户说明取舍。

## 2. 上游手机号与社交策略

上游 signup 配置 `identifiers=['phone']`、`password=false`、`verify=true`；signin 只允许 phone + verificationCode。`socialSignIn.skipRequiredIdentifiers=false`、`automaticAccountLinking=false`。首次社交身份缺手机号时由 **Logto 自己**完成手机号验证码补全，不能在补全前完成本应用普通登录。新用户不被强制另设密码。

真实微信/QQ连接器和短信渠道在没有资质、获批应用及受保护配置时关闭；维护期只使用固定发行包中官方 Mock SMS/Mock Social，绝无通用验证码或 HTTP 查码接口。模拟社交传输不能当作真实腾讯授权验收。

Logto 实测 issuer 是 `http://127.0.0.1:3001/oidc`，默认签名 `ES384`、公钥曲线 P-384、PKCE `S256`。上游 `phone_number` 使用 `861…`，RP 只对严格 `861[3-9][0-9]{9}` 补 `+`，再复用既有严格 +86 规范化。不会扩大其它输入入口的手机号规则。

上游[scope.ts](https://github.com/logto-io/logto/blob/d179bf9d2d51104236a342b90c2841df66e4ba5b/packages/core/src/oidc/scope.ts) 的 `phone_number_verified` 来自 `Boolean(primaryPhone)`，不是额外保存的验证凭据。**禁止运维直接把未验证旧号码导入 primaryPhone 后假装完成 OTP**；管理 API 写入属于受信任高权限边界。

## 3. 数据模型与增量迁移（当前 schema8）

- `accounts.userId → users.id`：继续使用原 `acct_...`。旧用户名、盐/哈希和 `passwordEnabled` 保留，不批量导入上游，不改聊天/角色/能量外键。
- `phone_phones`：规范化号码唯一、`userId` 唯一并级联账号，是业务侧已经验证的手机号映射；不是可变业务主键。
- `oidc_identities`：主键 `(issuer,subject)`，`userId` 唯一且外键级联。只接受固定 issuer 的已验证 subject；昵称、头像、UnionID 或相同手机号都不能静默重新指派现有映射。
- `oidc_browsers / oidc_states / oidc_pending`：独立过渡浏览器、一次 state 和待明确完成的身份；保存 purpose、原始 flowRevision，敏感用途另绑定确切普通会话/目标账号和开始时间。它们不是普通登录凭据。
- `oidc_revocation_clock / oidc_user_revocations`：单调撤销序号与账号水位，防止已撤销的交换中流程晚到复活；不是分布式 IAM 全局撤销系统。

schema6→7 是增量初始化，schema7→8 只新增级联管理会话的 Access 摘要绑定，不改普通账号/聊天/能量数据。旧自研身份表保留以兼容历史数据与隔离测试，但在 `auth_backend=oidc` 时原注册、密码登录、恢复码登录、原 OTP/腾讯主入口和原密码/OTP 敏感修改路由由服务端拒绝，不只是前端隐藏。

每次普通鉴权也检查固定 issuer 的持久映射：旧未迁移账号的有效 Bearer/Cookie 不能绕过切换；非账号开发会话只在 development 且 `allow_dev_auth=true` 时可用，关闭开发认证后已发旧凭据也拒绝。无 OIDC 配置或运营 gate 关闭时不会退回自研核心。

## 4. 标准 OIDC 流程

1. 独立 `AlliraOidcClient` GET session 初始化 `allira_oidc` HttpOnly、host-only、SameSite=Lax、Path `/api/v1/auth/oidc` 的 1800 秒过渡 Cookie 与内存 CSRF；普通 Bearer 不混入。
2. 同源 POST start 创建 300 秒 state，保存随机 nonce 与 PKCE verifier；只接受固定配置 issuer/client/callback。前端授权 URL 的 origin 必须精确等于 AuthOptions 提供的受信任 origin，不从返回 URL 自行建立信任。
3. callback 匹配浏览器与 state 后，**兑换前先持久标记 exchanging**。发现、token、JWKS、UserInfo 都限定固定同源端点、15 秒总时限、64KiB JSON、无环境代理、无重定向/压缩响应；失败不能重用 state。
4. Authlib/joserfc 严格验签 `RS256` 或 `ES384` 公钥、issuer/aud/azp/nonce/exp/iat 和适用的 at_hash。不接受 none、对称密钥、私钥或其它曲线。只有已验证 IDToken 后才可按需读取 UserInfo，且 subject 必须相同、`phone_number_verified is True`；IDToken 已明确 false 时不能覆盖。
5. 运营门禁开放时，callback 的正常成功和受控协议失败只 303 到固定 `/test/`，不回显 code/token/原始错误；门禁关闭或配置缺失则先返回 503，不伪装可用。成功先生成 600 秒 purpose-bound pending；页面明确条款、成年及身份关联后才 complete，不自动把 pending 当登录。
6. 普通业务会话沿用已有 AccountSession、Bearer 或主动 browser 交换；成功后清过渡 Cookie/内存 CSRF。私有 token、pending、手机号不进入浏览器 Storage、URL 或公共 PWA 缓存；所有有副作用 POST 不自动重试。

有限回调诊断只允许 `oidc_callback_failed stage=<固定枚举> category=<固定枚举>`，不记录异常文本、动态类名、堆栈、URL、phone、subject、code 或 token。其目的仅是定位协议边界，不能通过放宽验证让测试变绿。

## 5. 旧账号安全保留

上游[password.ts](https://github.com/logto-io/logto/blob/d179bf9d2d51104236a342b90c2841df66e4ba5b/packages/core/src/utils/password.ts) 不原生支持 Allira 的标准 scrypt 参数，不能混同 Firebase scrypt 或悄悄导入不兼容哈希。因此保留原业务 ID，用新 OIDC 证明和明确关联迁移：

- **旧有密码账号（原号码已绑或未绑）**：正常 pending 中始终提供“我有旧账号，保留历史”；新手机号证明 + 用户主动填写旧用户名/当前密码 + 明确关联。使用既有有界密码 worker，只用作所有权证明，不恢复原密码登录主入口。post-await 再校验凭据快照、pending、账号/手机号归属与撤销水位；同一原子事务消费 proof、建立映射、撤销旧本地登录/恢复码/过渡流程，再签发新普通会话。
- **旧仅手机无密码账号**：必须用户先选择专用 `phone_migration`，要求上游 `prompt=login,max_age=0`，签名 `auth_time` 严格有效且新鲜，并另行确认 `confirmPhoneMigration=true`。只匹配同号、原无密码、未关联其它 OIDC 身份的账号；保留原 owner、历史及能量，撤销旧本地凭据。普通 login pending 加此标志不能升级权限。
- **碰撞**：普通 complete 遇既有号码不会静默合并；已映射另一账号的 `(issuer,sub)` 不能重指派。任一后置检查/提交失败，proof 消费与业务变更一起回滚。

手机号并不证明永久自然人或实名，回收号/丢旧号/自助换号/跨账号合并与申诉仍需独立设计，不能用测试号场景代替。

## 6. 业务删除、会话与管理边界

删除 AlliraAi 业务数据需已明确记住的普通 browser 会话、独立 OIDC CSRF 与普通 CSRF；Bearer 不被暗中兑换。先发起 purpose=erase 的 fresh hosted 认证，IDToken `auth_time` 为有限非 bool 数值、在流程开始附近且最近 300 秒内。pending 绑定同一 issuer/sub、目标 owner 和确切普通会话。最后逐字确认 `DELETE MY ACCOUNT`，同事务消费 pending 并执行既有数据删除。

逻辑 running 或物理 closing 返回 409，有效删除 proof 消费回滚。该动作只删除 **AlliraAi 在线业务数据和本地映射**，不等于删除 Logto 全局用户、外部平台账号或物理擦除历史备份。再次用同一上游身份重新注册不会恢复已删业务 owner。

本地设备/全部会话撤销只撤销 AlliraAi 会话及相关 pending/交换中流程，不宣称自动撤销上游所有 session/grant。上游密码改变不保证所有既发 JWT 即时失效，end-session 也不等于全部 refresh grant 撤销。未来扩展上游生命周期操作须有明确权限与失败处理，不能把业务管理员等同 Logto 管理员。

API 0.8 的管理身份改为 Cloudflare Access，旧一次性 CLI/code 登录关闭；独立管理 Cookie 与 CSRF 仍保留且绑定当前已验签 JWT 的 subject/email 摘要及断言摘要，每请求重新验证 Access。普通 OIDC 登录永不授予 AlliraAi 管理权限，Logto Console 与本应用后台分工不变。仅管理路径经 `dsah.guyufl.cc.cd` 专用 Tunnel 发布，详见[Access 部署记录](access-admin-acceptance.md)；本页普通用户的 Logto/OIDC 流程不因此公开。

## 7. 部署、备份和真实渠道边界

API、Logto core/admin 和私有 PG 只监听服务器回环 `8000 / 3001 / 3002 / 54329`，均由低权限 managed service 运行，不开放公网、不另起 Allira API。固定回调为本机维护地址；公网 HTTPS、反代、域名与移动端回跳须另行授权验收。

所有秘密只在受保护服务端文件；`/etc/allira/identity.env` 是独立普通身份覆盖层，之后的 `/etc/allira/access.env` 只包含管理 Access 配置，不改普通身份或原模型配置字节。正常渠道未配置时 `oidc_enabled=false,oidc_simulated=false`，拒绝真实登录且诚实提示，不伪造已发送短信。维护期可临时真实 Logto + 官方 Mock 连接器 + 模型 Mock，结束立即关闭连接器、恢复原覆盖层、删除精确测试账号/文件，绝不回滚旧运行数据库。

SQLite 备份/新离线目标恢复需清 12 个当前 schema 临时凭据表（含 schema8 的 admin_access_sessions 摘要绑定），保留持久账号映射与撤销水位。Logto PG 使用原生 custom dump 与 SHA/manifest，在全新随机 DB 恢复验证后精确删除验证库；不覆盖运行库。联合备份要在维护窗口协调两库，不是跨库分布式原子事务；还需保护集群角色、runtime/env 和外部密钥。同机保留不等于异机灾备或已删数据物理擦除。

真实短信的供应商开通、签名/模板、资费/送达；微信网站应用和获批域名；QQ 应用与 UnionID 权限均未由 Mock 证明。当前 stock QQ connector 强制 UnionID，未获得权限时保持关闭；若未来改 OpenID，应固定 AppID namespace/身份模式并补测试，禁止 `unionid || openid` 漂移。网站微信扫码登录不能混同公众号 H5。QQ[审核规范](https://wiki.connect.qq.com/网站审核规范/)禁止强制另填独立密码，流程也仍须平台审核，开源许可不豁免。
