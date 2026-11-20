# 历史：自研手机号主身份与社交绑定验收（API 0.6）

> 这是已经被新架构取代的历史记录，不是当前 Logto 接入证明。用户要求真正复用开源认证核心后，当前方向为实际 Logto OSS + Authlib/joserfc OIDC，参见[现行设计](identity-design.md)。以下版本、测试和配置状态均只描述 API 0.6 当次交付；不据此宣称新流程或真实渠道验收成功。

日期：2026-11-19（Asia/Shanghai）。本轮按“手机号为主登录标识、首次微信/QQ登录必须引导绑定手机号”实现；方案与平台边界见[身份设计](identity-design.md)，端点见[API](api.md)，正式客户端视觉继续交给[Gemini](gemini-handoff.md)。**结论：远程受保护环境中的实现、隔离回归和实际 Mock 浏览器流程通过；真实短信、真实微信/QQ 平台登录尚未开通或验收。**

## 1. 当前部署与账号模型

- 已部署 API **0.6.0 / 92 条 OpenAPI 路径 / SQLite schema6**，仍为原有 allira-dev、低权限 allira、单 Uvicorn worker、127.0.0.1:8000，不热重载、不启第二 API、不新增公网端口/域名/防火墙/SSH 改动。业务服务 boot-disabled，原 SSH 隧道保留。
- 手机规范化 +86 后唯一映射账号，是逻辑主登录身份；内部 acct_... 不变，旧聊天/角色/能量/owner 不重写。schema5→6 回归保留旧 username、密码 hash/salt、会话和聊天；实际维护库完成加法迁移，没有以旧库覆盖运行库。
- 新注册为手机验证码，密码可选；passwordEnabled=false 的账号不允许占位密码登录。旧用户名注册默认禁用，旧用户名登录只保留迁移入口；旧账号验证密码与手机后绑定，号码冲突返回冲突，不静默合并。
- 首次微信/QQ 授权只给独立 pending，无聊天身份。手机号验证码+当前条款/成年自我确认+明确关联后才创建或关联手机账号；已关联也要显式继续。平台 namespace=(provider,AppID,verified OpenID)，不按昵称/头像/UnionID 合并，既有映射不能重指派。
- OTP、普通账号、社交过渡与管理权限分离；OTP 用途/确切会话绑定，设密、恢复、全部退出、管理撤销与删除同步撤销旧 proof，包含尚在网络交换中的 OAuth。删除保留历史备份风险，没有把成年人勾选当年龄/实名验证。

## 2. 分阶段验证证据

所有依赖、lint、测试、TypeScript/esbuild 编译、Chromium 和服务操作均在 hklite-lv4 的远程工作区执行，本地仅轻量源码/文档编辑和一致性校验。以下是分阶段门禁，**不是同次一个总套件，更不能相加成真实上游请求数**。

| 门禁 | 最终结果 | 范围及限制 |
| --- | --- | --- |
| 后端 | Ruff 通过；pytest **795/795，104.74 秒** | 包含旧能力、手机/短信、社交/平台 MockTransport、root 身份集成、迁移、备份恢复、隔离与撤销竞态。conftest 清理业务 ALLIRA_* 和 .env、强制 Mock 并拒绝真实 HTTP transport。 |
| SDK | TypeScript build、**288/288**；独立旧套件 **216/216**；**83** 个有效声明反例 | 普通手机方法、独立 AlliraSocialClient、默认 omit、Cookie/独立 CSRF、pending 非会话、迟到 GET/401、不自动重试 POST、原 SSE/聊天兼容。不是运行时全协议验证或真实平台调用。 |
| 功能页 | TypeScript/esbuild 通过，Node **23/23** | 测试只写 .test-dist，保留实际 dist；严格手机/验证码/授权 URL、状态/UTF-8/退避/公开缓存与第三方声明。 |
| 隔离浏览器 | **68/68**，约 1.1 分钟 | 虚拟回环 127.0.0.1:49183 精确拦截，无监听；原 49 项与新增身份 19 项，覆盖用途/冷却、pending/明确关联、旧 owner、无密码安全操作与迟到响应。 |
| 唯一实际身份 Mock | **1/1**，约 1.2 分钟；SSH 总耗时 **74.667 秒** | 在现有单 API 部署冻结源码、确认 Mock/test-file/test 社交通道后，只跑一次 live-identity；真实 HTTP/Cookie/callback/浏览器/PWA，但发送器和提供方模拟，生成 POST 为零。 |
| 独立复核 | 额外 **23** 个只读隔离 Mock 内存安全探针，最终 16 个身份文件静态复核一致 | 复核包含交换中授权撤销闭环、最终 phone target/OTP 消费、Cookie/Bearer/管理隔离及删除恢复；不冒称第二套全量回归、真实网络或独立渗透测试。 |
| OpenAPI | **0.6.0 / 92 paths**；468633 字节 | 导出脚本显式 Mock、SMS/social disabled、内存数据库且不启动生命周期/业务服务；SHA256 618ef9b333e978e50c62465862b3878c6d1bdd1103fb8a2c22db5a6f81eba117。 |

后端保留一条上游 StarletteDeprecationWarning：Using httpx with starlette.testclient is deprecated; install httpx2 instead. Node transform-types 仍有 experimental 提示；没有通过忽略失败或删除竞态用例获得结果。全量首次运行两处旧合成 fixture 不适配新增字段/临时表，修正为显式列和完整凭据表后重跑全量通过，未削弱实现约束。

## 3. 唯一实际 Mock 的业务覆盖

约定脚本为[身份实际专项](../apps/dev-console/tests/live-identity.spec.ts)，上限 200 秒；trace/video/screenshot 关闭，错误只报静态阶段。发送前记录私有 outbox 字节位置，202 后最多 15 秒只读新增完整且 phone/purpose 匹配的 JSONL；随机 OTP 仅服务器进程读取并经正常用户填表提交，不走 HTTP debug、不默认验证码、不浏览器自动回填、不输出凭据。

| 场景 | 实际通过的流程 |
| --- | --- |
| 手机 A | 无密码手机号注册→显式 Cookie/刷新→set_password 专属 OTP 设置密码并撤销旧 Cookie→手机密码登录→最后主动密码确认删除。 |
| 微信 B | 首次模拟 provider callback 只有 pending→手机证明/条款/成年/明确关联→再次已关联授权仍需显式继续→erase 专属 OTP 确认删除。 |
| QQ C | 独立首次模拟 callback/手机明确关联→用户主动选填密码→登录后密码确认删除；未强制社交用户建立独立密码。 |
| 旧账号 F | root 预置的随机合成旧用户名账号与两条原文→旧密码登录→绑定手机→手机验证码登录→同 owner 与原文保留→确认删除；未操作其他既有账号。 |
| 权限与副作用 | pending 不能读聊天；账号/社交 Cookie+CSRF 独立，公开登录不携账号凭据；本次 Run/生成 POST 为零、页面错误零、浏览器 Storage 秘密留存零；四个测试账号均删除。 |
| PWA | 六项精确公开缓存、192/512 PNG 解码、非隐私 Chromium 安装资格、实际离线私有 API 失败、公开壳刷新与联网后 Cookie 恢复；不缓存手机/OTP/pending/identity/私有正文。 |

临时 browser context/profile 已清理。这里只证明远程 Chromium 模拟环境流程，不是短信送达率/资费、微信/QQ 审核/实际授权、Android/iOS 真机或可访问性验收。

## 4. 安全维护与原配置恢复

维护前确认 running/reserved=0，保存原环境字节/hash、旧 dist 与 SQLite API 一致性快照。先前第一次合成旧账号 seed 因传入 SecretStr 而不是 DTO 所需原始字符串失败，未创建测试账号或发送请求；立即安全恢复原环境/旧 dist，确认健康。随后只修正 seed 的输入类型，在已有 schema6 上重新创建快照，以新的 Mock 配置 hash 守卫再维护；**没有回滚运行库或重复实际浏览器专项**。

验收结束声明 BUSINESS LIVE FREEZE，之后不再做业务请求；root 核验测试 owner/原文已删、无测试社交映射、FK 正常，再停原服务、逐字节恢复原受保护环境并核验 SHA256，重新启动同一服务。临时 outbox 与旧账号 fixture 在验证绝对路径、属主、0700/0600、单链接普通文件后定点清理；私有环境/备份/凭据始终仅保留服务器。

恢复后的只读检查：OpenAPI 0.6.0/92 paths、schema6、info.provider=openai、ready、health/test/admin/sw/manifest 200、running/reserved=0；phone.enabled=false、phone.simulated=false，全部 social enabled=false/simulated=false。这意味着真实和模拟新身份入口当前都关闭，页面明确提示，**不能为方便演示留下测试后门**。原先授权的模型中转配置保持原字节；本轮没有调用真实模型、发送真实短信或交换真实腾讯 code。日常代码默认仍是 Mock。

allira-dev 保持 User=allira、NoNewPrivileges=yes、active、boot-disabled；allira-backup.timer 继续 active/enabled、每日同机快照保留最近七份。仅做只读健康确认，不用再生成来验证环境恢复。

## 5. schema6 新目标恢复演练

本轮另外对当前运行库通过 SQLite backup API 创建 **491520 字节、schema6、retentionWarning=false** 的一致性快照，在全新的私有离线目录运行[恢复演练](../scripts/restore_drill.py)，实际通过 checksumVerified、sessionsRevoked、interruptedRunsRecovered、完整性/FK 与 schema6 初始化检查。明确覆盖当前八张实际临时凭据表，保留持久账号、手机/社交映射、密码及撤销水位；旧可选 browser_sessions 表也在恢复器的兼容撤销清单。

没有替换运行库、启动第二 API、恢复旧凭据到线上或发真实请求。精确备份/副本路径仅写入服务器私有 0600 验收报告，不回传数据库或日志。这是**同机新目标恢复演练**，不是异机灾备、历史任意版本迁移或已删除数据物理擦除证明。

## 6. 源码冻结与交付边界

UI 58 个源码/配置/测试/锁文件本地与远端逐项一致；业务验收后只补 README 结果，runtime 32 文件不变。最终总清单摘要 a7d803aa0229fd993fed0a046a97e13f768bcb0a2c3ce0b4010c11cb83ecb7e6，运行时摘要 00a57a6e22f929c1e1dba83bd0b770f61eb17f4748466ee1a20f1f39376e41a4；live-identity 源码 SHA256 029208216b38d0896d5fa8ecef71c4e098b2396adb0a56c448903456dbb9e36b。SDK 运行时代码保持冻结，最后仅澄清 README 的旧用户名注册默认关闭。

[README](../README.md)、[架构](architecture.md)、[API](api.md)、[OpenAPI](openapi.json)、[Gemini 交接](gemini-handoff.md)、[远程操作](remote-development.md)、[参考许可](community-references.md)、[配置示例](../server/.env.example)与本轮身份设计同步。正式视觉仍未改为 UI 框架，不安装依赖型 IAM/DSH 插件，不回传依赖、dist、私有数据库或受保护环境。

仍需外部条件/单独验收：短信产品与网关真实送达、微信网站/QQ 应用权限和审核/备案/HTTPS callback、公开 TLS/反代、号码回收与换号申诉、邮件/2FA、分布式风控/容量、法律与内容治理、异机灾备和手机真机。开源许可与 Mock 通过都不能替代这些条件。

Git 的远端工作区仍停在 94dcb5a9d995ec1c3b7c1bc106765a6a84077548，包含此前功能的新改动**未 stage/commit/push**；GitHub 不是当前部署版本。本轮先交付远端源码与轻量本地镜像，不将未发布说成已发布。
