# API v1 与 SSE 契约

API 前缀 `/api/v1`；JSON 使用 camelCase。普通时间为 UTC ISO 8601 字符串（`Z` 或等价的 `+00:00` 偏移），**CDK `expiresAt` 是 Unix 秒数**，不混用。完整字段/约束见 [OpenAPI](openapi.json)、[SDK 类型](../packages/client/src/index.ts) 和 [SDK 示例](../packages/client/README.md)。REST 成功响应主要靠 TypeScript 静态类型，SSE 另做基本运行时协议校验；不能把类型声明当作完整响应 schema 校验。

## 认证、公开接口和列表

当前默认认证后端为 `oidc`，由实际 **Logto OSS hosted UI + Authlib/joserfc RP** 完成，而不是下面保留的 legacy 认证实现。正常环境 `oidc_enabled=false` 时，OIDC 会话／start 等返回 `503 oidc_unavailable`，不自动回退旧认证。`/auth/options` 是非秘密配置状态，不是上游健康探测。当前正式渠道仍未配置；旧版接口的存在或 OpenAPI 中列出并不意味着默认开放。

### 开源托管身份（API 0.7）

`AuthOptions={backend:"legacy"|"oidc",oidc:{enabled,provider:"logto",simulated,reason,authorizationOrigin:string|null},phone:{enabled,simulated,reason,countryCallingCodes:["+86"]},social:[{provider:"wechat"|"qq",enabled,simulated,reason}],legacyPasswordEnabled,registrationEnabled}`。`authorizationOrigin` 由服务端固定 issuer 派生，是客户端跳转的精确 allowlist，不能从返回 URL 自行构造可信范围。模拟状态应诚实展示。

使用独立 `AlliraOidcClient`。过渡 Cookie 为 host-only、HttpOnly、SameSite=Lax、Path=/api/v1/auth/oidc、最长 1800 秒；客户端仅 same-origin Cookie、独立 `X-Allira-OIDC-CSRF` 与精确 Origin，不附普通／管理 Bearer、不写 Storage。必须先显式 `getSession()` 才能提交，没有隐藏 GET 或 POST 自动重试。

| 方法 | 路径 | 输入/结果 |
| --- | --- | --- |
| GET | /auth/oidc/session | `{csrfToken,pending:OidcPending|null,enabled:true,simulated}`，显式初始化过渡 Cookie |
| POST | /auth/oidc/start | `{purpose?:"login"|"erase"|"phone_migration"}` → `{authorizationUrl}`，默认 login |
| GET | /auth/oidc/callback | 固定 OIDC code/state 回调；消费 state 后使用标准协议验证，处理结果固定 303 `/test/`，不带凭据；门禁关闭在路由依赖处返回 503 |
| POST | /auth/oidc/complete | `{acceptedTermsVersion:"2026-11-19",isAdult:true,confirmIdentity:true,confirmPhoneMigration?:boolean}` → AccountSession；确认标志默认 false |
| POST | /auth/oidc/migrate | 上述确认字段加 `{username,currentPassword}` → 保留原业务 userId 的 AccountSession；仅普通 login 用途 |
| POST | /auth/oidc/erase | `{confirmation:"DELETE MY ACCOUNT"}` → `{ok:true}`；需新鲜 erase pending、同一 remembered 普通 browser、普通及 OIDC 双 CSRF |
| POST | /auth/oidc/cancel | `{}` → `{ok:true}`，撤销本次过渡流程 |

`OidcPending={provider:"logto",purpose:"login"|"erase"|"phone_migration",maskedPhone,expiresAt}`，有效 600 秒。**pending 不是普通登录**，不能读取聊天／赋予管理权限；callback 成功不隐式 complete。验证码、社交授权和必填手机证明在 Logto 完成；RP 仅在标准验签和声明校验后接受已验证手机号。手机号仍只是逻辑主身份，内部不可变 `acct_...`、AccountUser 六字段和普通 AccountSession 不变。

新账号普通 complete 需明确条款／成年自我确认／使用身份。命中旧手机号或需保留历史时，不自动按手机合并：有密码旧账号显式证明旧用户名和当前密码；无密码旧手机账号必须另起 `phone_migration`，新鲜认证后以 `confirmPhoneMigration:true` 独立确认。同号目标必须 passwordEnabled=false、没有冲突 OIDC 关联。普通 login 下给 true、fresh 用途未明确给 true 均拒绝；目标冲突为 409 `identity_link_required` 或 `identity_conflict`，不创建替代账号或拷贝数据。迁移撤销旧本地会话／恢复码／旧流程并保留原聊天及账务。

`erase` 和 `phone_migration` 请求上游 `prompt=login,max_age=0`，校验 `auth_time` 为有限非布尔数字且处于 start 前最多 60 秒、当前前最多 300 秒至当前之间。erase start／提交同时需要当前普通 browser 的 `X-CSRF-Token`；显式任何 Authorization 都不允许替代该 browser。最后同 issuer/sub、确切普通会话和未撤销的 pending 原子消费；运行／物理 closing 导致 409 时有效 pending 回滚。**只删除本应用在线业务数据和映射，不删除 Logto 全局账号或物理擦除备份。**

默认 OIDC 模式下，服务端统一以 `403 oidc_auth_required` 禁用 `/auth/register`、`/auth/login`、`/auth/recover`、`/account/password`、`/account/recovery`、`/account/erase` 及 `/auth/phone/*`、`/auth/social/*`、`/account/phone/*`。未映射旧普通会话也不能访问业务；开发凭据每次使用检查 development 与 allowDevAuth。下表及后续 legacy 小节仅是显式 legacy 配置的兼容契约，不是新的主认证核心。

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| GET | /info | `{environment,provider,allowDevAuth,consoleEnabled,adminEnabled}`，不返回 Key 或上游模型配置 |
| GET | /terms | `{version,adultOnly,content}`；当前版本 `2026-11-19` |
| GET | /models | `{items:[{mode,provider,configured,energyCost,fallbackTo}]}`，模式为 economy/fast |
| GET | /auth/options | 非秘密通道可用性 AuthOptions，禁用/模拟不等同真实短信或平台已开通 |
| POST | /auth/register | 旧用户名注册；默认禁用，仅 development + 显式 legacyRegistrationEnabled 可用 |
| POST | /auth/login | `{username,password}` → AccountSession |
| POST | /dev/session | 显式启用开发专用 `{userId}` → `{accessToken,tokenType:"Bearer"}` |
| GET | /session | 当前普通会话 → `{userId}` |
| POST | /session/logout | 撤销当前普通会话 → `{ok:true}`；browser 类型同时清 Cookie |
| POST | /auth/browser/session | 新账号 Bearer + 同源 Origin → BrowserSession；原 Bearer 被消费 |
| GET | /auth/browser/session | 显式 Cookie 恢复 → BrowserSession；无有效 Cookie 401 |
| POST | /auth/browser/logout | Cookie + Origin/CSRF → `{ok:true}`，撤销并清 Cookie |
| GET | /account | 注册账号 → AccountUser；开发身份返回 403 account_required |
| POST | /account/update | `{displayName}` → AccountUser |
| POST | /account/password | `{currentPassword,newPassword}` → `{ok:true}` 并撤销全部账号会话 |
| POST | /account/logout-all | `{ok:true,revoked}` |

`AccountSession={accessToken,tokenType:"Bearer",user:AccountUser}`；AccountUser 包含 `id,username,displayName,createdAt,termsVersion,isAdult`。用户名 3–32 位 ASCII 字母/数字/下划线，规范化为小写，不自动去空白；密码 10–128 字符、UTF-8 最多 256 字节；displayName 去首尾空白后 1–80 字符、最多 240 字节并拒绝控制/格式字符。注册必须提交当前条款版本与布尔值 true，不是可靠年龄核验。

受保护普通请求默认使用 `Authorization: Bearer <accessToken>`，不把 Token 放 URL，不接受请求 owner。SDK 默认 Bearer 与全部公开调用均显式 `credentials:omit`，不意外使用 ambient Cookie。可选 `browserSession:true` 仅同源 Cookie、无 Bearer；先显式交换或 GET 恢复以缓存内存 CSRF。BrowserSession 为 `{user:AccountUser,csrfToken,expiresAt}`，不返回 Cookie 原值。交换须新账号 Bearer + 精确 Origin，原 Bearer 被原子消费；同浏览器旧 Cookie 被轮换。Cookie host-only/HttpOnly/SameSite=Strict/Path=/api/v1，默认 7 天，HTTPS Secure，HTTP 仅 loopback peer+Host。

所有 Cookie 写请求须精确同源 Origin 与 X-CSRF-Token，并校验 Sec-Fetch-Site；非同源拒绝，显式 Authorization 错误绝不回退 Cookie。browser Token 不能当 Bearer；开发身份不可交换。没有自动刷新。开发 userId 1–100 位字母/数字/下划线/连字符，拒绝任何大小写 `acct_` 前缀；普通账号/名为 admin 的开发身份均无管理权限。

### 手机号主身份（API 0.6）

手机号是已验证的**主要登录标识**，不是 SQLite 账号主键。内部 `acct_...` 永远不因绑定而改变，既有 AccountUser/AccountSession 保持原字段；其中 username 对新手机账号是内部兼容名称，不应当作用户需要记住的登录名。仅接受 ASCII 中国大陆 11 位 `1[3-9]` 手机号或显式 `+86`，规范化保存为 `+86`；不接受空白、全角、其他国家号段。验证码必须是六位 ASCII 字符串，保留前导零。

AuthOptions 为 `{phone:{enabled,simulated,reason,countryCallingCodes:["+86"]},social:[{provider:"wechat"|"qq",enabled,simulated,reason}],legacyPasswordEnabled,registrationEnabled}`。registrationEnabled 不意味着旧用户名注册开放。SMS 默认 disabled，微信/QQ 默认 disabled；即使配置了社交提供方，没有手机验证通道也不开放社交登录。模拟标记必须真实展示，不能伪装真实短信或平台授权。

| 方法 | 路径 | 输入/结果 |
| --- | --- | --- |
| POST | /auth/phone/challenges | `{phone,purpose:"register"|"login"}` → 202 PhoneChallenge |
| POST | /auth/phone/register | `{challengeId:UUID,code,password?,displayName?,acceptedTermsVersion,isAdult:true}` → 201 AccountSession |
| POST | /auth/phone/login | `{challengeId:UUID,code}` → AccountSession；手机号必须已有账号，不自动注册 |
| POST | /auth/phone/password | `{phone,password}` → AccountSession；仅 passwordEnabled 的账号 |
| GET | /account/identity | AccountIdentity，普通注册账号鉴权，不返回验证码或社交 subject |
| POST | /account/phone/challenges | `{phone,currentPassword}` → 202 PhoneChallenge；旧账号验证绑定用途 |
| POST | /account/phone/bind | `{challengeId:UUID,code,currentPassword}` → AccountIdentity；保留当前 owner/会话，撤销其他会话 |
| POST | /account/phone/security/challenges | `{purpose:"set_password"|"erase"}` → 202 PhoneChallenge；手机号从当前账号读取 |
| POST | /account/phone/password | `{challengeId:UUID,code,newPassword}` → `{ok:true}`；撤销全部登录/恢复码/旧身份流程 |
| POST | /account/phone/erase | `{challengeId:UUID,code,confirmation:"DELETE MY ACCOUNT"}` → `{ok:true}`；清在线数据并撤销登录 |

PhoneChallenge 为 `{challengeId,maskedPhone,expiresAt,resendAfterSeconds:60}`，**没有验证码字段**。expiresAt 是 ISO 8601 UTC，可带 `+00:00` 偏移。随机码有效 300 秒、最多五次错误；用途、手机号和主体绑定，错误计数不随成功操作外层回滚。发送中不能消费，发送失败/取消作废；同手机号冷却 60 秒、每小时 5 次、每日 10 次，实际 peer 每小时 30 次、全局每日 100 次，另有入口限流。所有限额是当前内测护栏，不是生产分布式风控或可靠送达保证。

注册和首次社交关联**不强制设置密码**；省略 password 创建 passwordEnabled=false 的账号，不能用占位/未知密码登录。需要时通过手机安全验证码主动设置密码；没有密码时隐藏当前密码确认与恢复码签发，提供手机用途专属的设密/删号流程。验证码登录不是双因素认证。不要把成年勾选等同实名或年龄认证。

AccountIdentity 为 `{primaryIdentifier:"phone"|"legacy_username",phone:string|null,maskedPhone:string|null,phoneVerified,requiresPhoneBinding,passwordEnabled,social:[{provider,linkedAt}]}`。完整 phone 仅本人的私有资料；不可存公共缓存、日志或浏览器持久存储。旧账号继续用用户名登录后主动验证当前密码和新手机号；成功仍是同一 userId、原聊天和能量。其他账号已经占用该手机号返回 409 phone_in_use，不静默合并或迁移数据；没有自助换绑、旧号丢失申诉或跨账号数据合并接口。

手机安全 challenge 同时绑定账号、**确切普通会话**与用途；不能将登录码、其他窗口/账号的码或 set_password 码用于 erase。耗时密码计算后再次检查原会话、密码快照和手机号，最终消费与改密/删除同一事务。删号沿用逻辑 running/物理 closing 的 409 检查，拒绝时不烧掉本次有效码。启动和离线恢复清理所有短期身份凭据；重启不会重置最近的持久短信发送限额。

### 微信／QQ：独立待绑定身份

使用独立 `AlliraSocialClient`，不附普通或管理 Bearer。`allira_social` 是仅过渡用途的 host-only/HttpOnly/SameSite=Lax/Path=/api/v1/auth/social Cookie，最长 1800 秒；其 CSRF 与普通、管理客户端完全分开。首次 GET 明确初始化后才允许写入，所有写请求严格同源 Origin+社交 CSRF；callback 允许外站顶层 GET，并核验固定配置 origin/path，不按普通 Strict Cookie 写规则处理。

| 方法 | 路径 | 输入/结果 |
| --- | --- | --- |
| GET | /auth/social/session | `{csrfToken,expiresAt,pending:SocialPending|null}`，必要时新建过渡 Cookie |
| POST | /auth/social/{wechat\|qq}/start | `{}` → `{authorizeUrl,expiresAt}`；只允许显式配置的提供方与固定回调 |
| GET | /auth/social/{wechat\|qq}/callback | 平台 code/state 回调；成功或失败统一 303 到固定 `/test/`，不在返回 URL 携带普通凭据 |
| POST | /auth/social/phone/challenges | `{phone}` → 202 PhoneChallenge；绑定当前 pending |
| POST | /auth/social/bind-phone | `{challengeId,code,password?,displayName?,acceptedTermsVersion,isAdult:true,confirmBind:true}` → AccountSession |
| POST | /auth/social/complete | `{}` → AccountSession；仅已有有效社交映射且有已验证手机号 |
| POST | /auth/social/cancel | `{}` → `{ok:true}`，撤销过渡 Cookie 与流程 |

SocialPending 为 `{provider,displayName,expiresAt,requiresPhoneBinding}`，有效 600 秒；**不是登录会话**，不能读聊天或授予管理权限。state 为 300 秒、浏览器/提供方/AppID/固定回调绑定，兑换前先持久消费，网络失败不能重放。命名空间为 `(provider,configuredAppId,verifiedOpenId)`，不按昵称、头像或 UnionID 自动合并；不在本地持久保存 access_token/refresh_token。

首次 callback 只形成 pending；用户收到新 `social_bind` 用途 OTP、接受条款/成年确认并明确 confirmBind 后，才原子创建手机账号或关联该手机号的既有账号并签发普通会话。同手机号关联不合并其他账号数据；已映射到不同账号的社交身份返回 409 identity_conflict，不能重新指派。密码是可选项：新手机号可主动设密，已有账号主动填写时必须匹配其当前密码，不能覆盖；首次 QQ 不要求另填密码。真实平台是否接受本站全部关联流程仍需平台验收。

改密、恢复、logout-all、管理员撤销和删号同时清理相关身份凭据。单调 revision 在 start 时快照并传播到 pending，最终 finish/attach/consume 均检查目标账号撤销水位，阻止已在交换中的旧 OAuth 请求在撤销后重新登录；不误伤其他账号。删号不保存已删社交标识墓碑，迟到回调最多成为无映射 pending，必须新的手机证明与明确确认才可独立重新注册，不能恢复旧账号。

AuthOptions 与四个公开手机接口（发送注册/登录码、注册、验证码登录、手机密码登录）始终 credentials:omit、无 Bearer/CSRF；社交客户端仅 same-origin Cookie/独立 CSRF。返回普通 AccountSession 后，仍由应用主动选择内存 Bearer 或显式交换普通浏览器会话。所有 OTP/绑定/授权 POST 无自动重试；迟到 GET/401 不得覆盖更新的身份或新 challenge。

### 恢复、设备与数据控制

| 方法 | 路径 | 输入/结果 |
| --- | --- | --- |
| GET | /account/recovery | `{enabled,remainingCodes,updatedAt:string|null}`，不含明码 |
| POST | /account/recovery | `{currentPassword}` → `{codes:string[],createdAt}`，轮换全部旧码 |
| POST | /auth/recover | 公开 `{username,recoveryCode,newPassword}` → `{ok:true}`；成功撤销全部登录/恢复码，不自动登录 |
| GET | /account/sessions | `{items:[{id,createdAt,lastSeenAt,expiresAt,label,current,type:"bearer"|"browser"}]}` |
| POST | /account/sessions/{id}/revoke | 无请求体 → `{ok:true}`；未知/他人 ID 无效果成功 |
| GET | /account/export | AccountDataExport，同步一致、显式读取全部敏感档案 |
| POST | /account/erase | `{currentPassword,confirmation:"DELETE MY ACCOUNT"}` → `{ok:true}` |

恢复码一次提供 8 个、每个 192 bit 随机、仅摘要入库；轮换/改密/恢复使旧码失效。敏感密码确认按账号与 peer 限流 10/60 秒，恢复按 peer 10/60 秒；使用共享有界密码计算槽。未知用户/错误码统一 invalid_credentials；401 会保守清除 SDK 的本地浏览器状态，UI 要求重新登录。设备 UUID 非秘密，lastSeenAt 最多每分钟刷新。恢复码必须用户主动安全下载，不能存网页存储/日志/测试 trace。

AccountDataExport 为 `{format:"allira.account.v1",exportedAt,user,identity?:AccountIdentity|null,conversations:ConversationArchive[],characters,wallet:Wallet|null,ledger,usage,warnings}`。identity 为兼容新增字段，包含本人的已验证手机与提供方/关联时间，不含社交 subject。最多 1000 会话/100000 记录/32MiB，单会话另有 50000 条消息/卡片/块合并记录和 16MiB；活动生成 409、超限 413，不截断、不创建钱包。不含密码派生值/OTP/Token/恢复码/管理或提供方配置，但用户自行粘贴的秘密仍在聊天/导入原文中，不自动脱敏，也不直接作为全系统备份恢复格式。

传统 `/account/erase` 需当前密码与精确确认串；无密码账号使用上面的本会话 erase 专属手机证明，不填写未知占位密码。两条路径遇到 active 或物理 closing 均 409 account_busy；有效手机码消费在拒绝时回滚。耗时密码计算后再次认证，最终原子清理在线个人业务/凭据与手机/社交映射；已使用 CDK 留随机匿名核销墓碑，不能重复兑换。既有备份按保留策略继续存在，不承诺所有介质物理擦除。

列表默认 `limit=50`，范围 1–100，响应 `Page<T>={items:T[],nextCursor:string|null}`。游标不透明、必须在原用户/筛选/列表作用域使用，失效/他人游标 400 invalid_cursor。会话、角色等稳定 ID 排序**不是全局最近更新时间排序**；账本/审计使用独立数字游标字符串。

根路径 `GET /healthz`、`/readyz`、`/openapi.json`、`/docs` 不在 `/api/v1` 下。healthz 是存活；readyz 只查询本地数据库 SELECT 1，返回 `{status:"ready"}`，失败 503 not_ready，不探测真实模型或返回路径。OpenAPI 导出快照包括开发管理接口，运行时按配置启用，禁用开发/管理入口为 404。

## 预设陪伴与一键开始

| 方法 | 路径 | 输入/结果 |
| --- | --- | --- |
| GET | /companion/presets | 公开 CompanionPresets；不读普通 Token/Cookie |
| POST | /companion/conversations | `{clientSetupId:UUID,presetId?:"daily"|"listen"|"focus"}` → 201 CompanionStart |

`CompanionPresets={defaultPresetId:"daily",items:[{id,title,description,openingHint,defaultMode:"economy"}]}`；三种风格为轻松日常、温柔倾听、一起行动。CompanionStart 为 `{conversation,presetId,replayed}`。服务端预置自然中文、诚实 AI 身份、尊重用户边界和该风格的 instructions，以及中性关系说明；不默认恋爱/独占，不按性别推断关系。

设置请求按 owner+clientSetupId 幂等，省略 presetId 与显式 daily 相同；改风格重用同 UUID 返回 409，重放先于生成中检查。会话和初始关系原子创建，没有 Run、消息、角色库、模型调用或能量扣款。删除目标后返回 404，不重建。openingHint 只是公开页面提示，不是模型生成的回复。结果不明时只能用户主动重试原 UUID+风格，不自动换 ID/再 POST。普通 `/conversations` 自定义接口继续作为高级能力保留。

## 会话、消息和任务

| 方法 | 路径 | 输入/结果 |
| --- | --- | --- |
| POST | /conversations | `{title?,instructions?:string|null}` → 201 Conversation |
| GET | /conversations | `cursor?&limit?` → Page<Conversation> |
| GET | /conversations/{id} | Conversation |
| POST | /conversations/{id}/update | 非空 `{title?,instructions?:string|null}` → Conversation；生成中 409 |
| POST | /conversations/{id}/delete | 无请求体 → `{ok:true}`；生成中 409 |
| GET | /conversations/{id}/state | `{conversation,activeRun:Run|null,lastSequence}` |
| GET | /conversations/{id}/messages | `cursor?&limit?` → 从旧到新 Page<Message> |
| GET | /conversations/{id}/messages/recent | `before?:messageId&limit?` → 最近一页，页内 sequence 升序 |
| POST | /conversations/{id}/runs | `{clientMessageId:UUID,content,mode?:"economy"|"fast"}` → 202 Run |
| GET | /runs/{id} | Run |
| POST | /runs/{id}/cancel | 无请求体 → Run 的实际终态快照 |
| GET | /runs/{id}/events | `after?:number` → text/event-stream |
| GET | /runs/{id}/usage | RunUsage；归属安全，聊天删除后仍可查询非正文账务 |

Conversation 包含 `id,title,instructions,createdAt,updatedAt`；Message 包含 `id,conversationId,runId,sequence,role,content,status,createdAt`；Run 包含 `id,conversationId,clientMessageId,userMessageId,assistantMessageId,status,errorCode,createdAt,updatedAt`。Message.role 为 user/assistant；Message.status 为 generating/completed/failed/cancelled；Run.status 为 running/completed/failed/cancelled。失败/取消保留部分正文，但不作为完整历史对进入未来 Prompt。

标题默认“新对话”、去首尾空白后 1–200 字符；instructions 可空、最多 20000 字符。content 非空白、最多 20000 字符，保留原始空白。未知输入字段通常拒绝。recent 的 `nextCursor` 为当前页最早消息 ID，下一页使用 **before** 而非 cursor，合并时前插；普通 messages 则使用最后一条消息 ID 的 cursor 向后。两种游标不能互换。

`state` 与 `recent` 是两个时间点的快照，不构成跨请求事务。恢复时先读 state 再读 recent，优先处理较新消息中的 generating.runId，不能只信旧 state.activeRun。终态列表快照不应继续本地累计未配套的 delta。

### 严格幂等与容量

点击发送时只生成一次 UUID，冻结**原文与 mode（默认 economy）**。相同会话中相同 UUID+原文+模式返回原 Run，不再扣能量/限流或启动模型；任一不同返回 409 idempotency_conflict。冲突不自动换 UUID。POST 网络结果不明时按原三元组重试；重新生成是用户明确的新 UUID。

同会话新 UUID 遇到 running 为 409 run_in_progress；全局任务槽默认 8，**包括已终态但上游仍在关闭的任务**，容量满为 429 capacity_exceeded。幂等重放优先于容量。用户新任务默认 20/分钟。新任务、消息、事件、摘要变化和能量预扣在一个事务，不满足条件不留下空记录。

## 会话检索、归档和分叉

| 方法 | 路径 | 输入/结果 |
| --- | --- | --- |
| GET | /conversation-search | `q?&cursor?&limit?` → Page<{conversation,matchedMessageId:string|null,snippet}> |
| GET | /conversations/{id}/export | `format?:json|markdown` → `{format,filename,content}`，默认 json |
| POST | /conversations/{id}/fork | `{clientForkId:UUID,throughMessageId?,title?}` → 201 `{conversation,replayed,copiedMessages}` |

检索 q 默认空、最多 200 字符，子串字面匹配本人标题/消息，limit 默认 20（1–100），按 updatedAt/id 倒序；ASCII 大小写匹配不等于 Unicode 语义/向量检索。游标 HMAC 绑定 owner+q，不能用作普通列表游标。snippet 是不可信文本，不能当 HTML 插入。

JSON content 是序列化 `ConversationArchive={format:"allira.conversation.v1",conversation,character:Character|null,memory:ConversationMemory,messages:Message[],importSource:ImportSource|null,memoryCards:MemoryCard[],memoryBlocks:MemoryBlock[]}`；含全部原始消息状态、原始来源和启用/停用记忆。新增数组由服务端默认空值，SDK 对旧归档保留可选字段兼容。Markdown content 同样携带卡片与摘录，所有不可信正文安全围栏，不直接变任意 HTML。活动任务 409、最多 50000 条消息/卡片/块合并记录、最终 16MiB，超限拒绝不截断。客户端只能在用户点击时敏感下载，不存公共缓存。

分叉 throughMessageId 省略表示当前全部完整对；提供时必须是本人此会话的已完成相邻完整对末端 assistant，非法点 400 invalid_fork_point，外来/不存在 404。原参数与 clientForkId 固定，结果未知可主动精确重试；changed/null/omitted 语义保留并按接口校验，409 不自动换 ID。复制真实 completed pair、新 ID、角色/核心记忆/关系与 instructions；不复制失败片段、原来源、旧 usage/费用、可能含未来内容的旧 summary、任何记忆卡或摘录块（无来源卡也不暗复制）。源/目标删除后保留幂等墓碑，已删除目标返回 404 而非重建。

## 角色和会话记忆

| 方法 | 路径 | 输入/结果 |
| --- | --- | --- |
| GET/POST | /characters | 列表 Page<Character>；创建 `{name,description?,instructions?}` → 201 Character |
| GET | /characters/{id} | Character |
| POST | /characters/{id}/update | 非空 `{name?,description?,instructions?}` → Character |
| POST | /characters/{id}/delete | `{ok:true}`，正被会话使用则 409 |
| GET | /conversations/{id}/memory | ConversationMemory |
| POST | /conversations/{id}/memory | 非空 `{characterId?:string|null,coreMemory?,relationship?}` → ConversationMemory |
| POST | /conversations/{id}/memory/summarize | 无请求体 → ConversationMemory，生成中 409 |

Character 为 `id,name,description,instructions,createdAt,updatedAt`；ConversationMemory 为 `conversationId,characterId,coreMemory,relationship,summary,summarizedThrough,updatedAt`。清空角色用 null。摘要及处理位置由服务端管理，不允许任意客户端覆盖 summary。核心记忆/关系是用户提供的设定，角色和会话均须本人所有。

服务端默认窗口最近 8 个完整真实对话对、规则摘录视图最多 6000 字符、总 Prompt 120000 字符。summary 保留旧 DTO 兼容，但只是持久来源块的有界视图；每个新折叠对保存来源 ID 和截断标记，不再用尾部截断覆盖唯一记忆。旧 summary 迁为无精确来源的 legacy_summary；不伪造回链。块/原文不因视图预算被删除，处理水位单调前进，停用的旧块不会借原文重新进入最近窗口。

### 用户记忆卡、来源块和实际上下文预览

下表路径均省略 `/conversations/{id}`；普通 owner 鉴权，Cookie 写入要求 Origin+CSRF，修改及预览遇 running 返回 409。

| 方法 | 路径 | 输入/结果 |
| --- | --- | --- |
| GET | /memory/cards | `{items:MemoryCard[]}`，包括停用卡 |
| POST | /memory/cards | CreateMemoryCardInput → 201 MemoryCard |
| POST | /memory/cards/{cardId}/update | 非空 UpdateMemoryCardInput → MemoryCard |
| POST | /memory/cards/{cardId}/delete | 无请求体 → `{ok:true}` |
| GET | /memory/blocks | `q?&cursor?&limit?` → Page<MemoryBlock>，默认 20，范围 1–100 |
| POST | /memory/blocks/{blockId}/update | `{enabled:boolean}` → MemoryBlock |
| GET | /memory/blocks/{blockId}/source | `{block,messages:Message[],warnings:string[]}`，显式私有原文读取 |
| POST | /context/preview | `{content}` → ContextPreview，不创建 Run/事件/扣款/模型调用 |

MemoryCard 为 `{id,conversationId,category,title,content,sourceMessageId:null|string,enabled,createdAt,updatedAt}`；category 为 fact/preference/boundary/relationship，标签不代表系统已验证事实。创建必填 category/title/content，可选 sourceMessageId 与默认 true 的严格 boolean enabled；更新至少一项，只有 sourceMessageId 可用 null 清空。标题去空白后 1–100，内容保留原文且非空白 1–2000；每会话最多 50 张卡（含停用），全部 content 合计最多 20000 字符，超限 409 memory_limit_exceeded。来源只能是本会话真实相邻 completed pair 中的 user；助手、失败/孤立、他人来源拒绝。卡片不由模型自动确认。

MemoryBlock 为 `{id,conversationId,kind:"extractive_pair"|"legacy_summary",sourceMessageIds:string[],startSequence,endSequence,summary,enabled,createdAt}`。新块每侧最多摘录 500 字符并标明截断，ID 由会话/配对来源确定；legacy 没有精确原文，source 返回空消息和警告。列表 q 子串匹配摘录，最多 200 字符，按 startSequence/id 升序；HMAC 游标绑定 owner+conversation+q，不与其它列表通用。source 再核验真实配对，缺失时警告而非补造。停用仅影响后续上下文，不删聊天、旧下载或备份；修改后旧 preview 应失效。

ContextPreview 为 `{conversationId,measurement:"characters_not_tokens",characterLimit,totalCharacters,remainingCharacters,sections,messages,includedCardIds,includedBlockIds,pendingBlockIds,excludedCardCount,omittedBlockCount,omittedRecentPairCount,warnings}`。sections 元素 `{kind,characters,messages}`，kind 为 instructions/character/core/relationship/memory_cards/history_notes/recent_messages/current_message；messages 是完整 `{role,content}` 数组，属于敏感数据，折叠展示不意味着响应没有进入内存。

预览 content 使用与 Run 相同的非空白原始文本（最多 20000），调用同一规划器和全部输入安全检查，在 SAVEPOINT 中始终回滚规划的记忆变化，临时块 ID 可与下一次真实规划对齐。会话正常 lastSeenAt 认证维护不在“不写业务数据”的承诺内。所有启用卡与角色/核心/关系/当前输入必须保留，超限 413；之后保留尽可能多的最近完整对与预算内完整摘录块。计量仅字符，预览不检查余额/限流/容量，也不锁定未来配置或历史，不保证模型接受。规则摘录不是语义总结、精确 Token/缓存收益或可靠陪伴质量。

## 历史导入

| 方法 | 路径 | 输入/结果 |
| --- | --- | --- |
| POST | /imports/preview | `{format:"json"|"markdown"|"text",content}` → ImportPreview |
| POST | /imports/commit | `{clientImportId:UUID,format,content,title?,characterId?,coreMemory?,relationship?}` → 201 ImportCommitResult |
| GET | /conversations/{id}/import-source | `{format,content,archivedMessages}`；无来源 404 |

ImportPreview 包含 `messages:[{role,content}],warnings,suggestedTitle,sourceHash`，预览不落库且 messages 不等于全部原始记录。ImportCommitResult 为 `{conversation,importedMessages,archivedMessages,replayed}`；importedMessages 是时间线内真实配对的消息条数，archivedMessages 是未配对/未支持来源记录数。只接受相邻 user→assistant 配对，不为孤立记录伪造回复，不将导入 system/developer/tool 提升为系统指令；完整原文始终受保护留存。导入不调用模型/不扣能量。

原文最多 1MiB UTF-8、最多 1000 条候选、单条 20000 字符；解析支持范围见 [架构](architecture.md)。围栏内的角色标记不切消息；自有 allira.conversation.v1 归档只导入原始相邻且同 run/归属/连续序号的 completed pair，不跨 failed/cancelled/不支持记录拼对，未知自有版本拒绝。其余保留原文并警告；大导出不保证可直接完整再导入。幂等以用户+clientImportId+原始完整参数为准，重试保留原文/角色/记忆/标题不变；修改后 409。UI 不应在结果未知时自动换 UUID 导入第二份。

## 整数测试能量

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| GET | /energy | `{balance,unit:"energy",economyCost,fastCost}` |
| GET | /energy/ledger | Page<LedgerEntry>；仅本人 |
| POST | /energy/redeem | `{code}` → Wallet |

LedgerEntry 包含 `id:string,delta,balanceAfter,kind,referenceId,createdAt`。默认首次 100，经济/极速默认 1/2。新任务预扣，completed 结算，failed/cancelled 退款；极速在首次公开文本前回退经济时退差价。相同 UUID 重放和相同用户重复核销不重复记账。

RunUsage 包含 `runId,requestedMode,actualMode,cost,state,promptCharacters,outputCharacters,firstTokenMs,durationMs,createdAt,updatedAt`。cost 为实际路线能量，state 为 reserved/settled/refunded；firstTokenMs 为首次公开文本片段而非内部推理 Token。全部统计是字符，不是供应商 Token/货币账单。

## 独立管理接口

管理前缀 `/api/v1/admin`，默认 `admin_auth_backend=access`；入口为 `https://dsah.guyufl.cc.cd/admin/`。源站仍是单进程回环服务，专用 Tunnel 只发布后台、管理 API 和当前两个管理 JS/CSS。边缘精确邮箱策略、Tunnel 固定 team/AUD 和源站逐请求 `Cf-Access-Jwt-Assertion` 标准验签各自必需；不信任裸邮箱头、普通 Bearer、旧登录码或旧管理 Cookie。建议用 `AlliraAdminClient`，credentials=same-origin，不附 Bearer；管理 Cookie 只承担 JWT 绑定的本地会话与独立 CSRF，不代替 Access 证明。写请求必须精确公开 HTTPS Origin 和 X-CSRF-Token，不能信任 X-Forwarded-* 推导 Origin。实际登录由用户自行测试，详见[部署与边界记录](access-admin-acceptance.md)。

| 方法 | 路径（省略管理前缀） | 输入/结果 |
| --- | --- | --- |
| POST | /session | Access 模式对格式有效的旧 `{code}` 返回 `403 admin_link_disabled`；不是登录入口 |
| GET | /session | 验证本请求 Access 断言，创建/轮换绑定的 Secure/HttpOnly 管理 Cookie，返回 `{authenticated:true,csrfToken,expiresAt,authentication:"cloudflare-access",identity:{email},logoutUrl:"/cdn-cgi/access/logout"}` |
| POST | /logout | 须 Access、当前绑定 Cookie、Origin 与 CSRF；删除本地会话并清 Cookie，返回 `{ok:true}`，页面随后导航固定 Access logout 路径 |
| GET | /overview | 用户/会话/消息/任务计数、activeSessions、environment/provider/maxConcurrentRuns |
| GET | /users | `q?&identity?&cursor?&limit?` → Page<AdminUser>；identity=all/oidc/legacy/developer |
| GET | /users/{id}/sessions | `cursor?&limit?` → Page<AdminDeviceSession>，仅目标用户仍有效的本地设备 |
| POST | /users/{id}/sessions/{sessionId}/revoke | `{ok:true,revoked:0|1}`；UUID、目标 owner 校验，仅撤销该本地设备 |
| POST | /users/{id}/revoke-sessions | `{ok:true,revoked}`，同时适用于账号与开发用户；不是全局 SSO 注销 |
| GET | /conversations | `userId?&cursor?&limit?` → Page<Conversation + ownerId + messageCount> |
| GET | /conversations/{id}/messages | 显式读取正文 Page<Message>，记录审计 |
| POST | /conversations/{id}/delete | `{ok:true}`，有活动任务 409 |
| GET | /runs | `status?&userId?&cursor?&limit?` → Page<Run + ownerId> |
| GET | /runs/{id} | Run + ownerId |
| POST | /runs/{id}/cancel | 实际终态 Run + ownerId |
| GET | /audit | Page<AdminAudit>，只有动作/目标/时间，无正文和凭据；内部删号归属不作为 DTO 字段返回 |
| POST | /cdks | `{energy:1..1000000,count?:1..50,expiresInDays?:1..365}` → `{items:[{code,prefix,energy,expiresAt}]}` |
| GET | /cdks | Page<CdkSummary>，不返回明码；含 redeemedBy/redeemedAt/disabled |
| POST | /cdks/{prefix}/disable | `{ok:true}` |
| GET | /usage | Run/字符/首次文本均值、reservedEnergy/settledEnergy/refundedEnergy，measurement=characters_not_tokens |
| GET | /routes | AdminRouteOverview；仅当前路由与限制配置，不访问模型或上游身份服务 |
| GET | /diagnostics | AdminDiagnostics；业务/身份双库备份元数据与门禁配置，不访问模型或 Logto |

`refundedEnergy` 从账本累计 run.refunded 和 run.route_refunded 的正 delta，包含终态退款与差额退款，删除聊天不清零。CDK 明码仅签发时显示；签发是有副作用操作，响应丢失后不自动重试签发批次。管理页面不再解析或兑换 code fragment，进入即清除 hash 后 GET session；不展示旧 CLI 登录入口。CSRF 只在内存，不保存 Access 断言。源站固定 HTTPS team issuer/AUD、RS256、公钥 kid、type=app、subject、精确邮箱与期限，使用 joserfc；只接受单个 ASCII `Cf-Access-Jwt-Assertion` 头，不用 Cookie/Bearer/邮箱头替代。JWKS 固定 HTTPS、64KiB/15s，缓存最多300s、单飞刷新与失败退避，失败不使用过期缓存。绑定仅存 subject/email/断言的摘要，旧 Cookie 或不同/更新断言不能直接复用原本地会话，须重新 GET session。`expiresAt` 不晚于 Access JWT 到期或本地 TTL，Cookie 是 host-only/Secure/HttpOnly/SameSite=Strict/Path=/api/v1/admin。显式 link 后端仅用于隔离历史测试，不能配置公开域名或作为部署回退。

AdminUser 保留 `userId,conversationCount,runCount,activeSessions`，新增 `createdAt,accountType:registered|developer,username:string|null,displayName:string|null,identityStatus:oidc|legacy|developer,maskedPhone:string|null,phoneVerified,passwordEnabled,walletBalance:number|null`。身份只按当前固定 issuer 的业务关联判断，不返回 issuer/sub、完整手机号、派生密码或 tokenHash。未创建的钱包为 null，列表不会创建钱包或读取聊天。`q` 先拒绝控制/格式等 Unicode C 类字符再 trim，最多 100 字符/400 UTF-8 字节；只按用户 ID、用户名、显示名称做字面子串匹配，`%/_/\\` 不作为用户通配语法，不搜索完整手机。非空搜索/身份筛选游标签名绑定查询、筛选和 issuer 配置；换条件须重置，不能跨筛选复用。无筛选沿用旧稳定 userId 游标。

AdminDeviceSession 为 `{id,createdAt,lastSeenAt,expiresAt,label,type:bearer|browser}`，不含秘密、当前会话标志或正文。列表按稳定 session UUID 分页，游标必须属于同一个仍有效 owner；单设备撤销中未知/他人/过期 UUID 返回 revoked=0，不写审计或推进撤销水位。实际 revoked=1 时，删除该设备、撤销目标相关旧 OIDC/身份过渡流程和写审计在一个事务中完成；其它普通设备仍可用，全部撤销接口才删除其全部本地设备。**这些不是封禁、账户删除或 Logto 全局 session/grant 撤销。**

AdminRouteOverview 为 `{items:ModelRoute[],limits:{runTimeoutSeconds,maxConcurrentRuns,maxPromptCharacters,maxOutputCharacters,upstreamMaxTokens,runRatePerMinute},configurationOnly:true}`。items 与公开 `/models` 共享配置来源，仅含业务模式、provider 类型、configured、测试能量与 fallback；不暴露模型名、基址或 Key，不编辑配置、不测延时/额度/真实容量。

AdminDiagnostics 为 `{version,startedAt,uptimeSeconds,schemaVersion,database:{ok,journalMode,databaseBytes,walBytes},runs:{running,inFlight,closing,capacity},sessions:{bearer,browser,admin},backup,identity,identityBackup,warnings}`。`backup` 保留业务 SQLite 的 `{configured,latestAt,ageSeconds,status}`；status=missing/recent/stale/invalid，36h stale。

`identity` 为 `{backend,configured,enabled,simulated,linkedAccounts,legacyAccounts,developerUsers,pendingFlows,serviceProbe:'not_performed',administration:'separate'}`，只查固定 issuer 的本地映射和限时过渡流程；configured/enabled 不代表上游可达、真实渠道资质或认证健康。`identityBackup` 为 `{configured,latestAt,ageSeconds,status,databaseBytes:number|null,restoreStatus:not_reported|recorded|invalid,restoreVerifiedAt:string|null,checksumVerified:false}`，仅显示私有 PostgreSQL 原生备份 manifest/report 元数据；recorded 只表示历史报告，不表示本请求已恢复或已重新校验 dump。读取上限每目录 1000 项、JSON 64KiB，检查属主、权限、符号/硬链接与打开后 inode；不打开 dump、不计算其 SHA、不连 PG、不运行恢复。SDK 新区块为可选以兼容旧服务，UI 缺失显示未知，不能推断正常。此接口不返回私有路径/提供方凭据/原始正文，不是云监控、告警、全局 IAM 控制台或真实模型探针。

## SSE 帧、恢复和取消

通过 **Fetch + 默认 Bearer / 显式同源 Cookie** 订阅；不使用第二套原生 EventSource。默认模式 credentials:omit，浏览器会话模式同源 Cookie，不附 Bearer；服务器持续按原 kind 再认证。

```text
id: 1
event: run.started
data: {"run":{...}}

id: 2
event: message.delta
data: {"messageId":"...","delta":"你好"}

id: 3
event: run.completed
data: {"run":{...},"message":{...}}

```

terminal 为 run.completed/run.failed/run.cancelled，payload 均为完整 `{run,message}`；它们是成功送达的业务终态，而非 HTTP 错误。导入的 completed Run 只有 id=1 的 run.completed，不存在虚假的 started/delta。

每个 Run 独立事件 ID 自 1 递增，`after=0` 全量回放，after=N 仅 id>N；必须为 0–JS 安全整数且不超过当前最大 ID。只接受显式 query，Last-Event-ID 头返回 400，避免双游标。心跳 `: connected` / `: heartbeat` 忽略；响应最终 no-store，X-Accel-Buffering=no，不应在反代缓冲。

只记录 UI **成功应用**的事件 ID。delta 是追加，terminal 完整 Message **覆盖**本地缓冲。after=0 重建须先清空缓冲；正文/游标不配套不可续拼。SDK 对类型/顺序/基本关联/UTF-8 做校验，EOF 无终态抛 StreamInterruptedError；lastEventId 是解析位置，不等于 UI 已成功处理位置。SDK 不自动重连或 POST 重试。

AbortController/退出登录/断页只关闭订阅，后台继续；显式 cancel 才终止生成并退款，先落库终态再收尾。任务结束和取消竞争，先提交的终态获胜。Token 失效/撤销断开正在读的 SSE；已删除终态内容使回放正常关闭，之后 GET 404。若 after 已到终态而得到空流，GET Run/消息对账，不能无限重连。

## 错误与限制

```json
{"error":{"code":"run_in_progress","message":"This conversation has an active run."}}
```

| HTTP | 常见 code/含义 |
| --- | --- |
| 400 | invalid_cursor、invalid_cdk、invalid_content_length、invalid_import 等 |
| 401 | unauthorized、invalid_credentials、access_unauthorized；清除失效状态重新登录，无自动刷新；Access 缺失/无效证明不回退旧 Cookie |
| 402 | insufficient_energy；不创建任务/扣能量 |
| 403 | account_required、reserved_identity、registration_disabled、admin_link_disabled、管理 peer/Host/Origin/CSRF 拒绝 |
| 404 | not_found；他人与不存在同响应，禁用开发路由也是 404 |
| 409 | idempotency_conflict、run_in_progress、memory_limit_exceeded、username_taken、角色使用中、cdk_used |
| 408/413/415 | request_timeout、request_too_large/context_too_large、unsupported_encoding |
| 422 | validation_error、unsafe_control_character/content_blocked；不反射原输入 |
| 429 | capacity_exceeded、rate_limited、密码计算忙等；避免立即无限重试 |
| 500/503 | internal_error、console_not_built、access_unavailable 等；配置或 JWKS 不可用时 fail closed，无内部/上游正文泄漏 |

生成失败通常发生在 POST 成功之后，使用 Run.errorCode 与 run.failed 表示：upstream_error/upstream_timeout/internal_error/output_too_large/server_shutdown/server_restarted。部分输出保留但不当完整答案。UI 按 code 本地化，不打印 Token/密码/原聊天或原始上游错误。

写请求 wire 最大 2MiB、15 秒读取、拒绝压缩；输入/import另有更低语义约束。Prompt 默认 120000 字符、输出 60000；真实模型 max_tokens 由服务端限制，这些不代表精确 Token 预算。详见 [架构与上线欠账](architecture.md)。

## 契约演进

优先 additive；破坏性变化使用新前缀并升级 SDK。修改端点后在服务器运行 [导出脚本](../scripts/export_openapi.py)，同步 OpenAPI/SDK/文档/测试。SSE 在 OpenAPI 中描述为字符串，完整事件 union 仍以 SDK 和本文为准。
