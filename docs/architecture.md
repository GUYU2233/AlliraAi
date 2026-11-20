# AlliraAi 架构：开源托管身份、Access 后台与可持续聊天（API 0.8）

## 目标与组成

在 [产品构想](../idea.md) 和可持续内测底座上，保留默认简单聊天、用户确认的记忆卡、可回查来源的历史块及共享规划器预览。按照用户的开源复用优先要求，默认认证核心改为 **Logto OSS 1.46.0 + PostgreSQL 17.12**，通过 **Authlib 1.9.9 / joserfc** 的 OIDC 接入 **FastAPI + SQLite 业务服务**；原生 TypeScript 客户端／SDK 和独立管理认证保持。旧版“避免引入 IAM 而自行实现认证”的决定已被明确取代。内部业务账号 ID 和原数据不随切换改变。依赖、测试、编译和服务只在远端运行，正式视觉仍交给 Gemini。方案与未完成边界见[身份设计](identity-design.md)，管理 Access 现状见[本轮部署](access-admin-acceptance.md)，普通身份及旧后台证据见[API 0.7 历史验收](oidc-admin-acceptance.md)；[API 0.6 身份验收](identity-acceptance.md)、[上下文验收](context-companion-acceptance.md)、[API 0.4 验收](usability-acceptance.md)和 [MVP 验收](mvp-acceptance.md)均是各自历史阶段证据，不代替当前真实开源服务整链验收。

```text
Logto hosted UI ── 手机 OTP / 社交必填手机 ── Logto OSS + PostgreSQL
       │ OIDC code + PKCE / 标准 ID Token；回调先成为独立 pending
       ▼
/test/ 客户端 ── packages/client ── FastAPI Authlib RP
       │ 显式确认 / 旧账号证明       │ (issuer, subject) → 不可变 acct_...
       │ Bearer / 账号 Cookie       │
       └────────────────── FastAPI 业务 API ── SQLite
/admin/ 独立管理 Cookie+CSRF ──────┘             │
                         角色记忆 / 来源 / 任务 / 账本 / 审计
                                               │ 单进程 RunRuntime
                                      经济路线 / 极速路线
                                               │ 首个 delta 前受控回退
                                    Mock / 受保护配置的模型中转
```

Logto 用户登录不授予 AlliraAi 或 Logto 的管理权限。Logto 另有身份管理控制台；AlliraAi 管理页面只管理本应用业务。当前 API、Logto 核心／管理端口和 PostgreSQL 都只绑定回环；一个身份服务进程并不改变单个 Allira API worker 的限制。API 0.8 仅将 `dsah.guyufl.cc.cd/admin/`、管理 API 和当前两个管理资产经专用 cloudflared 发布：边缘 Access 精确邮箱策略、Tunnel 固定 team/AUD 和源站每请求 joserfc 验签均必须，裸邮箱头/旧 Cookie 不足以授权。管理会话仅存 subject/email/断言摘要，Secure Cookie 到期受 JWT 限制，写操作还要求固定 HTTPS Origin 和独立 CSRF。旧 code/fragment/CLI 登录关闭；实际登录由用户自行测试，见[Access 部署记录](access-admin-acceptance.md)。

前端不接触模型 Key、不拼模型上下文、不作为聊天权威数据库。简单页面无 React/Vue 依赖，正式设计仍可由 Gemini 重构展示层，但要保留 SDK 和恢复语义。

## 源码职责

| 模块 | 职责 |
| --- | --- |
| [app.py](../server/allira/app.py) | 生命周期、用户鉴权、聊天 REST/SSE、安全响应头、静态托管 |
| [db.py](../server/allira/db.py) | 基础表、归属校验、嵌套事务、严格幂等、消息与事件原子落库 |
| [oidc_identity.py](../server/allira/oidc_identity.py) | Authlib/joserfc 标准 OIDC RP、独立 pending、显式旧账号映射和新鲜认证删除；不实现 OTP／社交认证核心 |
| [Logto 运维](../ops/logto/README.md) | 实际 Logto OSS 托管认证、私有 PostgreSQL、固定版本回环补丁及身份库备份 |
| [accounts.py](../server/allira/accounts.py)、[account_security.py](../server/allira/account_security.py) | 不可变业务账号、仅显式迁移使用的旧密码证明、普通业务设备会话；旧认证入口默认关闭 |
| [phone_identity.py](../server/allira/phone_identity.py)、[sms.py](../server/allira/sms.py) | 业务端唯一手机映射与旧版兼容实现；默认 OIDC 模式不签发或校验手机 OTP |
| [social_identity.py](../server/allira/social_identity.py)、[social_providers.py](../server/allira/social_providers.py) | 旧版兼容实现及凭据清理；默认 OIDC 模式全部原生社交入口被服务端禁用 |
| [identity_security.py](../server/allira/identity_security.py)、[identity_lifecycle.py](../server/allira/identity_lifecycle.py) | 非秘密认证后端状态、独立流程水位撤销、启动清理；无秘密配置回显 |
| [browser_sessions.py](../server/allira/browser_sessions.py) | 显式账号 Cookie 交换、恢复、类型隔离和 Origin/CSRF |
| [data_controls.py](../server/allira/data_controls.py) | 有界完整账号归档、密码确认原子删除及匿名核销墓碑 |
| [conversation_tools.py](../server/allira/conversation_tools.py) | owner 绑定检索游标、JSON/Markdown 归档和幂等安全分叉 |
| [companion.py](../server/allira/companion.py) | 三种服务端风格、默认经济模式、幂等原子创建空聊天，不调用模型 |
| [memory.py](../server/allira/memory.py)、[context_memory.py](../server/allira/context_memory.py) | 角色/核心关系、手工卡、来源摘录块、共享 Prompt 规划与只读预览 |
| [imports.py](../server/allira/imports.py) | 解析预览、保留来源、配对导入、请求级幂等 |
| [economy.py](../server/allira/economy.py) | 整数能量、预留/结算/退款、CDK、路由用量统计 |
| [runtime.py](../server/allira/runtime.py) | 与订阅独立的任务、取消、超时、回退和统计收尾 |
| [providers.py](../server/allira/providers.py) | Mock/OpenAI 兼容公开文本流、严格解码和上游帧限额 |
| [admin.py](../server/allira/admin.py)、[admin_db.py](../server/allira/admin_db.py) | 独立管理认证、查询、取消、撤销、显式正文读取和审计 |
| [access_identity.py](../server/allira/access_identity.py)、[Tunnel 运维](../ops/cloudflare/README.md) | 固定 Access team/AUD 的 joserfc 逐请求验证、专用官方 cloudflared 管理路径发布 |
| [admin_cli.py](../server/allira/admin_cli.py) | 管理会话撤销；默认 Access 模式拒绝旧链接签发，link 仅历史兼容 |
| [safety.py](../server/allira/safety.py)、[http_limits.py](../server/allira/http_limits.py) | 使用说明、基础输入过滤、请求大小/编码/读取时限 |
| [operations.py](../server/allira/operations.py) | readiness、非敏感管理诊断、备份新鲜度（不代替校验） |
| [backup_cli.py](../server/allira/backup_cli.py)、[恢复演练](../scripts/restore_drill.py) | SQLite 一致性快照、校验、仅新目标离线恢复和凭据撤销 |
| [SDK](../packages/client/src/index.ts) | 默认 Bearer、显式账号 Cookie、独立管理客户端、REST/SSE |
| [测试控制台](../apps/dev-console/README.md) | 功能验证、迟到响应隔离、PWA 公开壳、真实浏览器测试 |

## 默认体验先于高级配置

登录后以聊天为主入口，点击“新聊天”使用 daily（轻松日常）；listen（温柔倾听）与 focus（一起行动）只是可选交流风格。服务端维护默认设定和中性关系，经济模式默认可用，不要求先填写 Prompt、角色、摘要窗口或模型基址。openingHint 是静态引导，不向消息表插入伪造助手回复。

`companion_setups` 以 owner+clientSetupId 保证幂等，完整创建会话与关系说明；不建 Run/消息/角色/钱包、不调模型或扣能量。省略风格与 daily 规范化一致，换风格重用 ID 返回冲突，已删除目标返回 404。用户看到的网络不确定操作只能主动精确重试，客户端不能自动补建第二个聊天。

设置与高级工具、聊天技术控制、自定义会话、开发登录默认折叠；账号/角色/记忆/导入/能量/检索并未删除。保留显式发送、停止和简单断线提示。预设不按目标性别推断恋爱/依赖关系，不以亲密度或充值施压；默认规则是提示词策略，不是已经实证的模型行为保证。

## 账号和权限：默认 OIDC 后端

`auth_backend=oidc` 为默认；`oidc_enabled` 是独立运营门禁。配置完整不代表短信和社交资格已就绪，正常环境目前保持门禁关闭且不模拟。测试时明确启用真实 Logto 实例的官方 mock connector，不能因此宣称腾讯／短信渠道验收。

Logto 承担手机号 OTP、hosted UI、社交认证、首次必需手机号补全及 OIDC 签发；配置手机号唯一标识、无强制密码、`skipRequiredIdentifiers=false`、关闭自动身份合并。AlliraAi 不向 Logto 管理 API 导入未经验证的手机号；Logto 的 `phone_number_verified` 来源于已持有 primaryPhone，管理写入本身也是可信边界。标准 QQ connector 当前要求 UnionID 权限，实际平台资格、回调域名和短信配置仍待接入，不伪造微信／QQ已通。

RP 用 Authlib 生成 S256、state、nonce、换 code，用 joserfc 和 Authlib 验证公开 RS256／ES384 签名及 issuer/audience/nonce/期限等声明；不自行实现 JWT 算法。发行方及同源 metadata/token/JWKS/UserInfo 端点由服务端固定，响应有界、不跟随重定向、不使用环境代理、不记录或持久保存上游 Token。通过标准验证后，仅接受布尔 true 的手机验证声明；UserInfo 回退须与已验证 ID Token 的 sub 一致。

`allira_oidc` 是独立 host-only、HttpOnly、SameSite=Lax、Path=/api/v1/auth/oidc 的过渡 Cookie，写请求严格 Origin 和独立 CSRF。state 在网络兑换前原子消费，callback 固定重定向 `/test/`；600 秒 pending 不是普通登录，不能读聊天或管理。用户接受条款、成年自我确认及明确使用身份后，原子映射 `(issuer,subject) → acct_...`、唯一手机号并签发本应用普通会话。手机号只是逻辑主身份，不替换内部业务主键。

旧 scrypt 参数与 Logto 支持的 firebase-scrypt 不同，不导入为假兼容密码。原密码账号用已验证 OIDC 手机 + 显式旧用户名／当前密码证明迁移；无密码旧手机账号用单独 `phone_migration` 新鲜认证、同手机号、明确迁移确认，且目标不得已有不同 OIDC 映射。迁移保留原 owner／聊天／钱包，撤销旧本地会话、恢复码和身份流程；不按手机号静默合并数据。正常账号完成登录不擅自撤销其他设备。

默认 OIDC 模式服务端关闭旧用户名登录／注册、原生手机／社交 OTP、改密／恢复码／旧删号接口；未映射旧 Bearer 和 browser 同样不能绕过。开发身份在每次认证时检查 development 与 allow_dev_auth，不能只关闭签发却让旧凭据继续用。业务设备会话、显式 Cookie 交换、个人导出与本地会话撤销保留。

删除业务数据须 remembered browser、普通与 OIDC 双 CSRF、新鲜 `purpose=erase` 认证、同 issuer/sub 和确切普通会话，检查 auth_time，最后逐字短语与用户确认。有效 pending 消费与物理 closing 检查／删除同一事务，忙时回滚。只删除 AlliraAi 在线业务数据与映射，不删除 Logto 全局账号、不保证物理擦除备份。logout-all／管理撤销只撤销本地会话与旧 RP 流程，不伪称上游 SSO 或已签 JWT 自动失效。

真实 Logto + 官方模拟短信／社交 + AlliraAi 的完整浏览器链已通过：5 类账号、10 次原始回调、三类旧 owner／历史／能量保留、新鲜同主体双 CSRF 业务删除，以及 PWA 公开壳／实际离线私有请求失败。该证据不是隔离 issuer，也不是实际腾讯／短信渠道或生产合规验收。管理员权限仍独立：普通 OIDC 身份不产生管理 Cookie，Logto 管理员也不自动成为 AlliraAi 管理员。

### legacy 兼容后端与历史实现（不是默认认证核心）

以下是保留用于旧回归／明确 legacy 模式的原生实现；在默认 OIDC 模式，其认证入口被服务端禁用，不能将其能力当作当前主登录流程。

手机号为经验证的主要登录标识，**不是会被换号影响的业务主键**。`users` 保存内部用户标识；`accounts` 保存不可变 `acct_...`、兼容 username 与密码状态。`phone_phones.phone` 为规范化 `+86` 手机号主键、`userId` 唯一外键；旧账号绑定只添加同一 ID 的手机资料，不重建账号/转移聊天或能量。社交标识以 `(provider,configuredAppId,verifiedOpenId)` 唯一关联 `accounts`，不能按昵称/头像/UnionID 猜同一人或静默合并账号。当前只支持大陆 ASCII 手机号；未实现自助换绑、旧号丢失申诉与跨账号合并。

新注册通过手机一次验证码，密码可选。`accounts.passwordEnabled` 默认 1 兼容旧账号，新无密码账号存随机非登录派生值并置 0；普通密码登录/确认必须同时检查标志，不是让用户猜占位密码。旧用户名注册默认禁用，只在 development+显式 legacy_registration_enabled 才可开；旧用户名登录留作安全迁移入口。旧账号验证当前密码和新手机后绑定，已有其他账号占号返回409，不丢原历史。

`phone_challenges` 只存带用途/确切主体的 HMAC 摘要，随机六位字符串码300秒/五次错误，消费和最终账号变更同一事务；发送和密码worker不跨数据库事务。错误计数先独立提交，不能靠回滚重置猜测机会。持久 `phone_send_attempts` 保存手机号/peer的HMAC限额键，不存短信正文，60秒冷却/每号5小时10日/peer30小时/全局100日；不是分布式风控。敏感设密/删号的手机号从服务端账号读取，同时绑定确切当前会话与用途；post-await重验会话/凭据/手机号，失败不改变账号。

微信／QQ 是独立 `allira_social` HttpOnly/SameSite=Lax/host-only/限定Path Cookie+独立CSRF 的过渡流程。固定回调、浏览器、提供方、AppID共同绑定300秒一次state；兑换前先提交消费，失败不能重放。首次callback只给600秒pending，不给普通会话或聊天权限；新手机证明、条款/成年与明确关联后才创建/关联手机账号。已关联仍需显式继续，不能把 pending 当登录。平台 access_token 不持久保存。

`social_revocation_clock` 与 `social_user_revocations` 保存单调水位；start快照flowRevision传到pending，finish、attach和最终消费对目标账号校验。改密/恢复/logout-all/管理撤销/删号在同一事务更新水位并清精确关联凭据，阻止交换中旧回调在撤销后重新签发登录，且不误清其他账号。删号级联删除映射/水位，不存已删社交身份墓碑；迟到回调最多成为无映射pending，必须新OTP及明确确认才能独立重新注册，不能恢复旧账号。

开发入口拒绝 `acct_` 前缀，不能冒用账号。随机普通 Token 仅存 SHA-256/到期时间；会话另有非秘密 UUID、创建/最近使用时间、类型和说明。最近使用时间至多每分钟刷新，不是精密审计。老三列表自动迁移，保留已有 Bearer。短信/社交默认disabled；真实通道须受保护服务端配置及平台资格，模拟只准development+模型Mock，当前恢复原配置后所有新通道均关闭。完整协议、许可和外部边界见[身份设计](identity-design.md)与[API](api.md)。

SDK 默认 Bearer，所有普通/公开请求显式 `credentials:omit`，不会意外使用同源 ambient Cookie。用户主动选择“记住此设备”时，用新账号 Bearer+同源 Origin 交换独立 `kind=browser` 会话，**原 Bearer 在同一事务中被消费**。账号 Cookie `allira_user` 为 host-only、HttpOnly、SameSite=Strict、Path=/api/v1，默认 7 天，HTTPS 加 Secure；HTTP 只接受已验证的 loopback peer/Host。恢复读取 Cookie 并重新获取内存 CSRF，所有 Cookie 写操作还须精确同源 Origin+CSRF。显式错误 Authorization 绝不回退 Cookie；browser Token 也不能当 Bearer。非账号开发身份不能交换，管理 Cookie 不共享该权限。

客户端不持久化 Bearer、CSRF、聊天或恢复码。默认登录刷新后需重新登录，可选 Cookie 在线恢复不等于离线保存账号；失效/撤销不自动刷新。

用户名为 3–32 位 ASCII 字母/数字/下划线，规范化为小写；密码 10–128 字符且 UTF-8 不超过 256 字节。密码 scrypt 参数为 N=32768、r=8、p=3、dklen=64，独立 16 字节盐；线程池内计算，最多两个密码任务同时在途，取消请求不提前释放仍在运行的密码计算槽。改密前后重新检查凭据快照/调用者 Token，成功撤销该账号全部会话。

注册要求接受当前条款并自我确认成年，不是年龄核验。注册/登录按直接连接 peer 限速 5/10 次每 60 秒；改密、签发恢复码、删号按账号与 peer 共同限速 10/60 秒，未登录恢复按 peer 10/60 秒。内存限流表有上限、重启会清空；多个 SSH 操作者共享 loopback peer，不是生产分布式风控。

有密码账号确认后可签发 8 个单次恢复码，每个由 24 随机字节生成（192 bit），只存 SHA-256。明码只在本次响应提供，应由用户主动离线保存；轮换使旧码失效。恢复成功更换密码并撤销全部登录/恢复码和相关身份凭据，不自动登录；未知用户和错误码同失败响应，不反射输入。普通改密同样撤销全部登录与恢复码。无密码账号不能走当前密码确认/恢复码签发，用手机登录及用途专属设密/删号，不伪装已设密码。设备撤销只撤销该次普通会话，不阻止以后重新证明身份；未知/他人 session ID 同为无效果成功。没有邮件恢复、丢失手机号申诉或 2FA；验证码登录不等于双因素。

普通业务对象均按 owner 隔离，他人对象与不存在对象同为 404。管理员不是普通账号角色：默认由 Cloudflare Access 的精确邮箱策略和固定 issuer/AUD 逐请求验签授权，再初始化独立的摘要绑定 Cookie。旧 CLI/code/fragment 登录拒绝，link 只在显式隔离兼容测试可用。管理 Cookie 必须 Secure/HttpOnly/SameSite=Strict、host-only、Path 为管理 API，期限不晚于 JWT；写操作另须固定 Origin 和独立 CSRF。专用 Tunnel 保持公共 Host，源站只信实际 loopback peer 与单一精确 Host，不信任转发头；普通控制台仍是 loopback-only。管理正文只在显式读取时请求并审计，不自动出现在列表里。

## 聊天与事务不变量

- `(conversationId, clientMessageId)` 唯一；重放必须同时匹配**原始文本与请求模式**。相同 UUID 更改文本/模式为 409。幂等读取先于容量、限流、上下文和扣能量检查，不二次调用模型或扣费。
- 每个会话只允许一个 running，部分唯一索引兜底。全局默认最多 8 个任务；每用户每分钟默认 20 次新任务。用户输入、固定上下文、余额或限流检查失败，不创建空任务/消息，也不扣能量。
- `atomic()` 使用 SAVEPOINT，包含最终 RELEASE 的异常回滚；嵌套摘要、钱包和 Run 不能提前提交。新任务、两条消息、能量预留和 started 事件一起提交。
- delta 追加正文与事件一起提交；最终状态、完整 assistant 事件与结算/退款一起提交。终态不可覆盖，取消 completed 返回 completed。
- failed/cancelled 保留已产生片段用于展示，但不进入下一轮完整对话上下文。正常退出标记 `server_shutdown`，重启恢复标记 `server_restarted` 并退款。
- 会话删除须无 running；原文、消息、Run、事件、来源及会话记忆一起清理。角色被会话使用时禁止删除。能量账本与不含正文的 usage 独立留存，用于防止删聊天后重复退款/丢账。

## SSE、恢复与取消

RunRuntime 持有任务，**SSE 断开、切页、退出用户只影响订阅，不取消模型任务**。显式停止调用 cancel，服务端先提交终态再关闭上游；取消与删除并发时返回已提交快照，不依赖清理后仍存在的聊天记录。

事件从 1 开始，支持显式 `after`，每批最多 100 条并排空最终终态。订阅约 50ms 轮询，10 秒心跳；会话过期/撤销会断开读流。终态被删除时已在进行的回放正常结束，后续 GET 返回 404。SDK 检查 UTF-8、事件类型、ID 顺序与基本 payload 关联，不把 EOF 当成功。

应用只在成功更新 UI 后记录 event ID；终态 `run.completed/failed/cancelled` 的完整 Message 覆盖本地缓冲。从 `after=0` 重建必须先清空该 Run 缓冲；如果正文/游标不配套，不能续拼旧片段。SDK 不自动重发 POST、重连或切换 UUID。

## 检索、归档、分叉和个人数据

会话检索为本人的标题/消息子串匹配，q 最多 200 字符，按 updatedAt/id 倒序；空 q 可用于最近排序。游标持久 HMAC 签名并绑定 owner+查询，不能挪到另一用户或另一搜索；不是向量/语义搜索，匹配文本一律不可信。普通会话列表仍用旧稳定 ID 排序。

单会话 JSON 归档 `allira.conversation.v1` 包含会话、角色快照、核心关系、全部消息状态、原始导入来源，以及 memoryCards/memoryBlocks（含停用项，旧归档数组默认空）。Markdown 同样携带这些数据并将不可信原文安全围栏。活动任务返回 409，最多 50000 条消息/卡片/块合并记录、16MiB，不静默截断。分叉只复制截至指定 completed assistant 的真实相邻完整对；不复制原账务/失败片段、原始来源、旧摘要或任何卡片/块，即使无来源卡也可能记录未来。角色/核心关系/instructions 仍按当前设定快照复制，不是完全还原历史。`clientForkId+源会话+完整参数` 严格幂等；已删除目标不重新生成。

账号导出是 owner 内同步一致快照，包含会话归档、角色、已有钱包/账本/usage及本人的AccountIdentity（可选兼容字段，含本人手机号及提供方/关联时间）；最多 1000 会话、100000 记录、32MiB，超限 413、活动生成 409。不会为了导出创建钱包，不导出密码派生值、Token、OTP摘要、社交subject、恢复码、管理记录或提供方配置；**用户自己粘贴进聊天/来源的秘密仍属于原文，不做自动脱敏**。导出是敏感用户档案，不是全系统备份或自动恢复账号格式。

默认 OIDC 模式下删号使用独立 `erase` 用途的新鲜上游认证，以及当前 remembered 普通 browser、普通与 OIDC 双 CSRF 和固定确认文本；必须同 issuer/sub、确切本地会话、有效 auth_time 和未撤销 pending，校验后无 await 到原子删除。只清理本应用业务账号、映射与本地证明，**不会删除 Logto 全局账号或全局 SSO 会话**；上游账号保留须另有身份管理与留存流程。旧密码／手机码删号仅属于显式 legacy 兼容模式，默认服务端关闭。逻辑 running 或已终态但仍关闭的任务都拒绝，拒绝时回滚本次有效 pending 消费。清理在线账号/会话/角色/来源/记忆/任务/个人账本/usage/恢复码及可归属的审计关联。新审计内部保存 ownerId，不在公开管理 DTO 返回，即使无生成费用的聊天先被删除，也可在删号事务清理；升级只从现存资源、明确用户目标或历史 usage 回填，升级前已删且无 usage 的旧审计无法凭空归属，可能仍保留无正文的 UUID/动作/时间。CDK 核销保留无原账号标识的随机墓碑，不能删号后复用已核销码。旧备份可能仍含历史记录/旧密码状态，不能宣称物理擦除或将旧副本直接重新上线。

## 角色、记忆和受限上下文

Prompt 由同一服务端规划器组装：`会话设定 + 角色 + 核心/关系 + 全部启用记忆卡 + 有界历史摘录 + 最近完整对 + 当前原文`。固定部分（包括启用的边界卡）必须保留，超预算直接 413，不悄悄舍弃用户边界；可选历史按整对/整块选取，优先最近完整对。默认最近 8 对、历史视图 6000 字符、总量 120000 字符，不是精确 Token。

`memory_cards` 是 owner 手动确认的 fact/preference/boundary/relationship 分类文本，不是系统验证事实。每会话最多 50 张、全部卡 content 合计 20000 字符，单卡内容最多 2000；来源仅可绑定此会话真实完成相邻对中的 user，不能用助手自述、失败片段、孤立导入或跨会话数据代替用户同意。支持修改/启停/删除，无自动亲密度、情绪推断或跨角色共享。

`memory_blocks` 持久保存已移出最近窗口的真实完成对摘录，来源是 Run 实际关联且序号相邻的两条消息，每侧最多 500 字符并有截断标记。块 ID 由会话与来源确定，水位 `summarizedThrough` 单调前进；旧 summary 只迁移一次为无精确来源的 legacy_summary，不伪造出处或重复原文回填。旧 summary DTO 保留为预算内视图；视图淘汰不删块和原文，停用历史块也不让已处理原文重新混入最近窗口。列表有界分页/摘录子串检索，用户显式读取 source 时再次核验原消息对。

`context/preview` 与实际 Run 复用规划器和全部安全检查，在独立 SAVEPOINT 内规划后始终回滚，返回当前完整消息、段字符数、入选/待写块 ID 和遗漏计数；不创建 Run/事件、钱包预扣或模型请求。正常认证可能更新 lastSeenAt，不是整条 HTTP 绝对无写入。预览不锁未来配置/历史、不检查余额/容量/频率；折叠显示也意味着敏感消息已经在响应内存中。实际 Run 则把规划、块/水位变更、能量和消息原子提交，失败全部回滚。

停用/删除记忆卡不等于删除原始聊天、旧下载或备份；会话/账号删除才级联在线卡/块。规则摘录始终有损，未使用 LLM 总结、向量检索或自动事实学习，不宣称永不遗忘、百万 Token、缓存命中或固定节省。固定许可与设计依据见 [billion-context 与两套 Soulweave 取舍](context-companion-design.md)。

## 导入与来源

支持受限 JSON 消息数组/对象及选定分支的 ChatGPT mapping、Markdown 角色标题和文本角色前缀。限制为原文 1MiB UTF-8、最多 1000 条候选消息、单条最多 20000 字符；不是任意产品导出格式都保证兼容。

预览只解析不落库。提交将新会话、来源、初始记忆、真正相邻 user→assistant 对和其完成事件原子写入；孤立消息、system/developer/tool 和不支持结构仅保留在原始来源，不提升为系统指令、不补造对话。零有效对也可作为空会话加来源档案保存。

`(userId, clientImportId)` 与完整原始请求参数严格幂等，包含原字节文本和记忆/角色/标题选择。修改内容重试为 409；合法重试返回原会话。来源读接口受归属校验。导入不调用模型、不扣能量；下一轮生成才按正常上下文策略选取历史。

围栏代码内的 `user:`/`assistant:` 或角色标题不切分消息。重新导入自身 `allira.conversation.v1` JSON 时额外校验原始相邻 completed user/assistant、同 runId/会话归属与连续 sequence，不将 failed/cancelled 变成成功，也不跨被跳过记录拼对。其余原文仅存档并给警告，未知自有格式版本拒绝。大归档仍受导入原文 1MiB/1000 候选/20K 单条限制，导出并不承诺全尺寸原样再导入。

## 路由、能量和统计

客户端只传 `economy/fast`，模型和凭据始终在服务端。代码默认两个 Mock 行为用于测试；真实经济路线与可选极速路线分别配置。极速未配置或在**第一个公开文本 delta 前**发生受支持的上游错误，可按配置回退经济路线一次；已经产生正文后不回退，不混合两份答案、不重试同一条付费路线。

新用户首次使用钱包得到默认 100 测试能量，经济/极速默认 1/2。新 Run 原子预扣，completed 结算，failed/cancelled 全额退款；极速回退退差价并记录实际模式。退款不可重复，入金留出所有待退款的余额空间，余额保持 0–1000000000 整数。相同用户重复核销已使用 CDK 不再入金；他人已使用、过期、禁用码拒绝。CDK 明码仅签发时返回一次，列表/审计不返回明码。

usage 保存请求/实际模式、字符数、公开文本首片段时间、总耗时和账本状态，不保存正文。`firstTokenMs` 历史字段名实际代表首次公开文本，**不是模型内部首 Token 或思考耗时**；聚合标明 `characters_not_tokens`。应用能量与真实货币/模型账单无换算保证，支付、退款支付通道和正式成本系统未实现。

## 安全护栏

请求写入体在解析 JSON 前限 2MiB、15 秒读取，拒绝压缩编码；导入另限 1MiB 语义原文。上游公开文本输出默认 60000 字符，单行 262144 字符、单事件 1048576 字符、总流 8388608 字节；严格 UTF-8、JSON 和终结标记，不跟随重定向、不反射原始错误。可设置模型 `max_tokens` 默认 2048，真实验收使用更低上限。

基础过滤拒绝危险控制/代理字符，并可按 NFKC/casefold 匹配配置禁词，默认列表为空；不是全面内容审核/未成年人保护/合规方案。聊天终态使用 markdown-it 15.0.3 解析 CommonMark token，再构造固定白名单 DOM；禁任意 HTML、外部图片和脚本，HTTP(S) 链接拒绝凭据/危险字符，代码复制需用户操作。解析限 64000 字符、4096 token、嵌套 20，超限回退文本；无 Mermaid/Math/HTML 预览。许可文本随构建保留，来源见[社区参考](community-references.md)。

消息以 messageId 维持稳定 DOM，流式仅更新目标正文、至多每 100ms 一次，终态立即覆盖并解析，保留用户滚动锚点。客户端自动重连仅网络/EOF/5xx，最多 5 次、指数退避加抖动、单次等待上限 6 秒，离线等待 online；恢复前读状态/最近页并从 after=0 重建。401、协议错误、显式断开、切换和退出停止恢复；生成/导入/分叉/预设 POST 不自动重发。SDK 自身仍不自动重连。

PWA 只缓存六项公开壳资源，scope=/test/；不缓存 API/admin/POST/账号数据，离线不恢复 Cookie 账号或发送。所有 API no-store，控制台 CSP/iframe/来源限制保持，不能为富文本放宽。

## 部署与上线欠账

**严格单 Allira API 进程、单 Uvicorn worker，不热重载。** SQLite 同步短事务在同一事件循环，任务所有权在内存；第二实例会干扰重启恢复。Logto 身份服务与私有 PostgreSQL 是独立低权限进程，不是第二个聊天 API。当前业务 schema 增量初始化记录 user_version=8，保留旧 summary／消息／账号 ID／hash／salt；schema6→7 新增持久 OIDC 关联、撤销水位及短期 browser/state/pending，schema7→8 只新增级联管理会话的 admin_access_sessions 摘要绑定表，不导入或改写旧密码算法。Access 启动清除旧管理会话和未用 code，不给旧链接或 Cookie 留旁路。旧普通账号会话在默认 OIDC 模式下无关联不能继续访问；开发会话每次使用都检查 development 与 allowDevAuth。启动清手机证明、社交及 OIDC 过渡流程，保留近期短信限额、持久映射与撤销水位。旧 schema5→6、schema3→4 的验收是历史证据，不是任意历史迁移／降级框架。

`/healthz` 为存活，`/readyz` 只做 SELECT 1、不访问模型、失败 503。独立后台复用既有原生 TypeScript 与 AlliraAdminClient，提供身份／字面搜索筛选、脱敏资料和已有钱包、点击才读设备分页、逐设备／全部本地撤销、纯配置路由与限制概览；不新增 IAM 权限核心。筛选游标签名绑定查询与固定 issuer，撤销单设备实际生效时与相关旧身份流程撤销／审计同事务，其它普通设备仍可用。账号查询不会创建钱包或读取聊天，未知旧 DTO 字段显示未知。

私有诊断包含数据库字节／模式、逻辑与物理关闭槽、会话计数、身份门禁／映射计数和两库备份新鲜度；不返回路径／凭据／issuer/sub／正文，不执行模型、Logto 或 PG 网络探测。freshness 只按有界私有元数据检查（36h stale）；身份备份 JSON 上限64KiB、目录最多1000项，并校验属主／权限／链接／inode，dump只读元数据，`checksumVerified=false`。`restoreStatus=recorded` 只说明存在历史报告，**不是本请求校验和或恢复验证**；业务 SQLite 和 Logto PG 的备份范围、定时策略必须分开说明。

`allira-backup.timer` 每日 19:15 UTC 后随机 0–300 秒，以 allira 在线 SQLite backup API 生成快照，保留最近 7 份本工具验证通过的私有目录。0600 文件/0700 目录、校验清单、完整性/FK 检查、同盘排他发布和 fsync；忙锁不按年龄偷删。恢复仅到新的离线文件，拒绝既有目标及 WAL/SHM/journal，清理当前实际存在的 12 张短期凭据表（含 OIDC browser/state/pending 与管理 Access 绑定，绑定先于父管理会话清理），保留持久手机号／身份映射、密码状态和撤销水位；演练只在副本迁移和回收中断任务、确认退款。该定时器**只覆盖业务 SQLite，不覆盖 Logto PostgreSQL 或运行配置**。

Logto 使用独立原生 `pg_dump` custom 格式私有备份，校验 SHA256 与目录清单；验证通过创建随机新离线数据库并 restore／检查后精确删除，不覆盖运行库、改角色或打印 DB_URL。该副本不包含角色与受保护环境文件，不代表已具备联合自动定时备份。维护期间停写两服务后分别快照，也不称为跨数据库分布式原子备份；一致恢复必须同时评审两库、签名密钥与受保护配置。操作见 [Logto 运维](../ops/logto/README.md) 与 [远程开发](remote-development.md)。**没有异机／云复制、加密备份、自动灾难切换或故障告警。**

当前普通业务仍是 SSH 保护的 loopback 内测环境，只有用户授权的 Access 后台域名受限发布，底层服务没有开放入站公网端口。管理与功能控制台仍禁止直接切 production；Access 发布不是普通业务生产就绪证明。尚需普通用户公开部署/TLS 与身份回调评审、分布式限流与任务所有权、持续告警、异机灾备、邮件/2FA、明确业务留存/删除政策、强内容治理、真实负载/质量/成本与法律审核。原事件与正文重复存储，没有全局自动保留期清理；每日 7 份也不是严格七天删除承诺。正式视觉、移动真机与可访问性专项留待后续。
