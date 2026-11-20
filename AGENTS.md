# AlliraAi 开发约束

## 远程优先（用户明确要求）

笔记本性能与磁盘有限，**所有依赖安装、测试、lint、编译与开发服务都在 SSH 主机 `hklite-lv4` 执行**。本地只做轻量源码/文档编辑、查看和源码同步；不得在本地运行 npm install/ci、npx、uv sync、pytest、tsc 或开发服务。

- 远端主工作区：`/opt/allira/workspace`；执行项目命令使用低权限 `allira` 用户。
- Python：`/opt/allira/workspace/server/.venv/bin/python`；uv：`/opt/allira/tools/uv-bin/uv`。
- Node：`/opt/allira/tools/node24/bin/node`；npm 命令先把该 bin 目录加入 PATH。
- 操作细节与命令见 [远程开发文档](docs/remote-development.md)。
- 同步前确认两端改动；只同步指定源码/锁文件/文档，不覆盖其他代理正在编辑的范围。测试前确保远端包含最新源码，修复后再同步回轻量本地镜像。
- 不回传或同步 `.venv`、`node_modules`、`dist`、缓存、日志、数据库（含 WAL/SHM）、`.env`、服务器环境文件或凭据。

## 开源复用优先（用户明确要求）

- 新增能力先检索并评估成熟、持续维护的开源项目，优先实际采用其服务、库或模块，再做必要定制；不能把“调研参考后重新手写”表述为复用。认证等安全关键能力尤其不得默认自行重造核心。
- 选型先核验开源版的实际能力、许可证及企业/云专有边界、维护状态、运行与数据迁移成本。接入要固定版本，保留许可/第三方声明，并明确哪些由上游负责、哪些是 AlliraAi 胶水或定制。
- 可以在开源项目基础上修改，但尽量使用配置、标准协议、官方扩展点和小补丁；不为避免依赖而未经沟通另写一套同等核心。若关键需求受企业许可限制、必须大量自研或有不可逆迁移，先说明取舍并请用户决定。
- 当前认证方向已由先前自研核心调整为实际接入成熟开源认证系统。旧实现的测试结果仅是历史证据，不等于已采用开源服务；切换须保留不可变业务账号 ID 和历史数据，完成真实本机开源服务的集成验收，不能仅用模拟发行方冒充接入成功。

## 当前任务边界

用户已授权持续自主开发，将 idea.md 的功能性 MVP 完善为可持续内测的平台（除正式客户端视觉设计）：账号/恢复码/设备会话、浏览器 Cookie+CSRF、角色记忆与受限上下文、历史迁移/检索/导出/分叉、测试能量/CDK/路由、安全 Markdown/断线恢复、数据控制、管理诊断和同机备份恢复。功能须预设好：默认简单聊天、三种中性陪伴风格和经济模式，高级能力渐进展开，不要求目标用户先理解技术参数。新记忆卡由用户明确确认，历史块保存来源，预览共享真实规划器并回滚业务写入；不自动推断亲密关系或宣称可靠语义记忆。使用原生 TypeScript，正式视觉仍留给 Gemini。社区与 DSH 插件仅核验机制/许可，不据此安装或更改 DSH；新增依赖必须保留第三方声明。管理认证必须独立于普通用户和开发登录，不得通过前端隐藏按钮或开发 userId 授予管理权限。仅对经过测试的能力声明已实现；Mock/规则摘要不等同真实模型质量、报价统计、生产容量或合规验证。参见 [架构](docs/architecture.md) 与 [Gemini 交接](docs/gemini-handoff.md)。

## 运行与安全

- API 仍只监听服务器 `127.0.0.1:8000`，普通客户端/业务 API 经 SSH 隧道访问。用户明确授权的 `dsah.guyufl.cc.cd` 仅经专用 Cloudflare Tunnel 发布后台及当代两个管理资产，不能公开聊天、普通登录/业务 API、Logto 或 PostgreSQL；不得擅自扩域名、放开入站端口或修改防火墙/SSH。后台默认 `admin_auth_backend=access`：Cloudflare Access 精确邮箱策略、Tunnel 固定 team/AUD 与源站每请求 joserfc 验签三层门禁，另保留 JWT-digest 绑定的 Secure 管理 Cookie、精确 Origin 和独立 CSRF。旧管理 code/fragment/CLI 签发默认禁用，不得以旧 Cookie、裸邮箱头或普通 Bearer 回退；显式 link 仅用于隔离历史兼容测试。用户要求实际 Access 登录与使用自行测试，不代走登录/2FA，不把隔离 JWT 回归或无凭据 302 检查写成实际登录验收。
- 当前认证采用实际 Logto OSS hosted UI + Authlib/joserfc OIDC RP；旧自研密码／手机／社交／恢复核心只留显式 legacy 兼容测试，默认 OIDC 服务端关闭旧入口。手机号是经验证的逻辑主登录标识，不做业务主键；内部 acct_...、聊天和能量保留。Logto 要求必填手机、OTP、新账号不强制密码、禁止自动关联与跳过必填身份；stock QQ 另需 UnionID 权限，真实微信/QQ渠道尚未验收。旧有密码账号显式证明旧用户名与密码；无密码旧手机账号通过独立新鲜 phone_migration 和额外确认保留 owner，不静默合并。普通 Bearer、显式 browser Cookie、OIDC 过渡 Cookie 与独立管理权限分离，各自 CSRF；fresh erase 需普通/OIDC 双 CSRF、同 issuer/sub 和确切普通会话，只删本应用业务数据，不宣称删除上游全球账号或全局 SSO。无真实资质/受保护凭据时 OIDC 运行门禁关闭，官方 mock 仅维护期启用；不能输出验证码或伪造真实渠道。开发凭据每次使用都须 development+allowDevAuth，production 禁用；公开部署、真实渠道、邮件/2FA、换号申诉、全局身份生命周期和分布式风控仍未完成。
- 只允许单 API 进程、单 Uvicorn worker；不使用热重载或同时启动第二实例。使用现有 `allira-dev` systemd 服务管理，改源码后明确重启。
- 代码默认 Mock。用户已授权指定测试中转的少量短请求，凭据仅在受保护服务端配置；自动化回归必须先确认 Mock，不做真实负载测试，不索取或输出 Key/Token。
- 所有业务资源按用户隔离；聊天幂等按 `(conversationId, clientMessageId)`，一键预设按 `(ownerId, clientSetupId)`；SSE 断开不取消生成，只有显式 cancel 停止；终态事件为准。浏览器自动恢复仅有界 GET，不自动重发生成/导入/分叉/预设 POST。记忆卡、来源块和预览属于私有内容，不进公共缓存；停用不等于删除原文，分叉不暗拷贝卡片、块或旧摘要。
- 测试页面构建使用隔离 `.test-dist`，不要让测试覆盖用户正在使用的 `dist`。部署前备份、确认无活动任务，维护期 Mock 验收结束后恢复原受保护配置；不回显其内容。
- `allira-backup.timer` 同机每日快照保留最近 7 份；恢复只到新的离线目标，不覆盖运行库。历史备份可能保留已删账号/旧密码状态，不能直接将其上线或宣称已做物理擦除/异机灾备。

## 验证

在服务器运行后端 pytest/Ruff 与 SDK npm test（包含 TypeScript build）；必要时执行 Mock 专用 [真实 HTTP/SSE 冒烟](scripts/smoke.mjs)。API 变化后使用 [导出脚本](scripts/export_openapi.py) 更新 OpenAPI、SDK、文档与测试。不得将仅静态阅读、Mock 或模拟上游测试表述为真实模型/生产容量验证。
