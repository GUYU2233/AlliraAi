# Logto OSS 接入与后台控制面板验收（API 0.7）

> 历史阶段记录：下文描述 API 0.7 当次 Logto 与后台验收。API 0.8 已将后台一次性链接改为 Cloudflare Access，见[现行部署与边界记录](access-admin-acceptance.md)；本页旧管理登录、schema、测试计数及“未公开”均不代表最新状态。

日期：2026-11-19（Asia/Shanghai）。本记录取代把 API 0.6 自研认证视作当前方案的说明；[旧身份验收](identity-acceptance.md)仅保留历史证据。当前实际采用 **Logto OSS 1.46.0 + PostgreSQL 17.12 + Authlib 1.9.9 / joserfc 1.8.0**，不是调研后另写认证核心。设计、固定版本和许可见[身份设计](identity-design.md)、[第三方声明](third-party-notices.md)与[Logto 运维](../ops/logto/README.md)。

**记录状态：真实 Logto 全链、后台实际浏览器、隔离回归、schema7 新离线目标恢复及两端源码/最终运行审计均已通过，临时配置和账号已恢复清理。短信/社交使用官方 Mock，不代表真实渠道或生产验收。**

## 1. 架构与交付范围

- Logto 官方发行包与默认 hosted UI 承担手机号 OTP、社交连接器、必填手机补全和 OIDC 签发；保留上游品牌及 MPL 许可。唯一上游代码补丁是四处 core/admin HTTP/HTTPS listen 显式绑定 127.0.0.1。普通 logo 通过官方 Management API 配置，不移除 Powered by、不使用付费去品牌或 Bring your UI。
- AlliraAi 用 Authlib/joserfc 校验标准身份，只保留业务会话、CSRF、短期事务及明确的旧账号映射胶水。默认 OIDC 模式在服务端关闭旧自研主登录/注册/手机/社交/恢复与密码敏感操作，不以隐藏按钮代替门禁，也不在身份服务不可用时回退自研核心。
- 手机号是已验证的逻辑主登录标识，内部不可变 acct_... 是业务外键。旧用户名/hash/salt、聊天、角色及能量保留；增量 schema7 不批量导入不兼容的 scrypt 密码，不覆盖旧 owner 或运行数据库。
- 后台复用原生 TypeScript 页面、AlliraAdminClient 与独立管理 Cookie/CSRF。新增字面搜索/身份筛选、脱敏账号与已有钱包、点击才读取的设备分页及确认撤销、只读路由配置与双库元数据诊断。不是重新实现 IAM，不授予普通 OIDC 身份管理员权限。
- 业务 API 为 0.7.0、SQLite schema7。导出 OpenAPI 共 102 条路径（包含开发专用参考）；正常部署关闭开发认证，实际服务为 101 条路径，缺少的只有 `/api/v1/dev/session`，不是构建或接口漂移。

## 2. 分阶段自动化门禁

全部安装、lint、测试、编译、Chromium 和服务操作都在 hklite-lv4 以低权限 allira 执行。本地仅轻量源码/文档和同步校验。以下是不同阶段的证据，不相加为真实渠道调用数。

| 门禁 | 已核验结果 | 证据范围 |
| --- | --- | --- |
| 后端最终完整回归 | 1183 passed，148.04 秒；Ruff 和 git diff --check 通过 | 包含旧兼容、标准库真实签名的模拟发行方、OIDC 迁移/撤销/删除、后台权限/游标/设备与有界备份元数据；无实际外部请求 |
| 后台专项 | 新旧 admin 合计 88 passed，16.79 秒 | 独立管理员、查询游标、所有者分页、单设备原子撤销与无效请求不产生副作用 |
| 诊断专项 | 新旧 operations 合计 53 passed，2.96 秒 | 不打开/哈希 dump、不连接 PG/模型、权限/链接/JSON/条目上限及报告诚实性 |
| SDK | 436/436；独立旧 390/390；144 个有效类型声明检查 | FakeFetch、兼容 DTO、错误/abort、认证 epoch/迟到响应/注销竞态；不是实际服务验收 |
| 普通功能客户端与身份页 | Node 25/25、隔离浏览器 86/86 | 含真实公开 SW 的合成 303 回归，不访问实际身份服务 |
| 后台最终同次客户端门禁 | Node 25/25、隔离浏览器 97/97，SSH 87.533 秒；唯一后台实际用例 --list 仅 1 项 | 原有 86 条加后台 11 条；包含源码 typecheck、隔离 .test-dist 构建，不覆盖用户 dist、不访问实际服务 |

后端仍有既有 Starlette/TestClient 与 Authlib httpx→httpx2 弃用警告；未为消除警告擅自切换传输库或删除安全断言。

## 3. 真正 Logto 服务的完整验收

唯一成功的完整默认用例为 [live-oidc.spec.ts](../apps/dev-console/tests/live-oidc.spec.ts)：**1/1 passed，30.6 秒，SSH 32.532 秒**。它不是模拟 OIDC 发行方，而是访问实际回环 Logto hosted UI、Provider、token/JWKS/UserInfo 与 Allira RP。短信/社交传输使用固定官方 Mock 连接器，因此不等于真实短信或微信/QQ。

完整用例覆盖五个隔离 owner：新手机号无密码注册；旧未绑手机的有密码账号明确迁移；同号冲突后的旧密码证明迁移；旧无密码手机账号专用 fresh phone_migration 加额外确认；官方模拟社交缺手机号时由 Logto 强制补手机。三类旧账号都保留原 owner、完整历史和能量。

十次回调均独立核对原始 CDP GET/document/main-frame 303、固定 /test/ 与私有 pending；pending 不等于普通登录。五次业务删除均要求 fresh、同一 issuer/sub、确切普通 browser 会话和普通/OIDC 双 CSRF。六项公共 PWA 缓存、192/512 PNG、非隐身安装资格、实际离线私有请求失败及联网 Cookie 恢复通过。生成、非法凭据头、外部请求、页面错误、传输错误及 localStorage/sessionStorage 均为零，测试 context/profile 已清理。

另有一次 callback-only 1/1 passed（4.1 秒，SSH 6.104 秒），只证明首个原始回调和 pending，未 complete/迁移/删除，不能与完整用例混同。

### 已定位并回归的观测问题

最初三次尝试在 callback 状态断言处停止，均及时恢复正常配置并精确清理测试账号，未算成功。零身份合成实验和已安装 Playwright 源码确认：在公开 SW 参与重定向时，Playwright 可把原 callback URL 与最终公开页面的 200/fromServiceWorker=true 关联。该值不能证明 RP 原始返回 200。测试改为同时检查原始 CDP redirectResponse 303、最终页面和私有 pending，并增加隔离回归；未因此放宽 JWT、state、nonce、CSRF 或网络规则。

上游默认 logo 的两个外部图片请求通过官方 branding 配置改为既有公开本地 PNG，网络白名单没有放宽。之后 callback-only 与完整默认用例分别通过。

## 4. 后台能力与验收边界

当前后台增加：按 ID/用户名/显示名字面搜索及固定身份筛选；只显示脱敏手机和已有钱包；不因查询创建钱包或读取聊天正文。筛选游标绑定查询、身份和固定 issuer，不能串用。设备为有效且目标 owner 所有的稳定 UUID，会话 tokenHash 不返回。

单设备实际撤销在同事务内清旧身份流程、删除目标会话并审计；其它普通设备不受单设备删除影响。未知/外人/过期设备是零撤销，不推进撤销水位或制造审计。全部撤销仍是本应用会话，不是封禁或 Logto 全局 SSO 注销。

路由页只展示脱敏配置和限制，不编辑秘密、不探测模型或报价。双库诊断分别说明业务 SQLite 与身份 PG：有界私有元数据最多 1000 项、JSON 64KiB、属主/权限/链接/inode 检查；PG dump 只读文件元数据，**checksumVerified 始终 false**。restoreStatus=recorded 只表示已有历史报告，不是当前请求重新校验和或恢复成功。身份 serviceProbe=not_performed，不把配置齐全当服务健康探测。

后台实际用例 [live-admin-control.spec.ts](../apps/dev-console/tests/live-admin-control.spec.ts) **仅运行一次，1/1 passed，4.6 秒，SSH 6.709 秒**。CLI 一次性链接显式交换和 Cookie 刷新通过；单设备撤销 1 个、全部本地撤销剩余 2 个，九次私有 fixture Bearer 只读探针为 6 次 200、3 次 401，另一账号始终不受影响。原聊天数量、余额和身份关联保留；脱敏/字面筛选、点击设备、只读路由、双库诊断及注销后 Cookie 401 通过。生成、聊天正文读取、非法凭据头、跨源、页面/传输错误和 Storage 留存均为零，无 HttpOnly Cookie 提取，context 清理成功。

完成后立即先恢复独立环境原字节并确认 provider=openai、OIDC disabled/nonsimulated，再精确删除本轮两个注册 fixture、一个空开发 fixture 和私有文件；测试管理会话/未用登录码残留为零、外键检查通过。无真实模型或 Logto/SMS 请求，无运行库回滚。正常后台已部署新构建。

## 5. 维护、备份与正常状态

认证成功验收后，立即恢复独立 identity.env 的原字节；原受保护模型配置从未改写。恢复后 provider=openai、OIDC enabled=false/simulated=false、旧认证主入口仍关闭，官方连接器全部关闭，精确五个上游 fixture 删除、所有业务 fixture 已由用例删除，临时私有文件及目录清理，外键检查通过。恢复正常 provider 只做健康/配置读取，没有追加真实模型请求。

API 继续由既有单 worker allira-dev 监听回环 8000；Logto core/admin 与 PG 分别仅回环 3001/3002/54329，均低权限、NoNewPrivileges、未启开机自启；SQLite backup timer 保持启用。未开放公网、改防火墙/域名/SSH，未启动第二个 Allira API。

维护窗口停止业务与 Logto 写入后，分别取得 SQLite 一致性快照和原生 PG custom dump。PG 已执行 SHA/目录检查及新随机离线 DB 恢复、核对 79 表/两 tenant/六应用/一个管理员后精确删除验证库；不覆盖运行库或提升运行角色。SQLite 定时器只覆盖业务库，**没有联合自动备份、跨库分布式原子快照、异机灾备或配置/角色自动恢复**。本轮另对恢复正常后的 schema7 库创建 **577536 字节** SQLite 一致性快照，retentionWarning=false；在新的 0700 离线目标通过 checksum、11 张短期凭据清理、中断运行/预留能量恢复以及完整性/外键检查。没有替换运行库、启动第二 API 或调用上游；实际报告仅保存在服务器私有目录。

## 6. 未完成事项与发布边界

真实短信资质、签名/模板、资费和送达；真实微信网站应用/获批域名；QQ 应用与 stock connector 所需 UnionID 权限，均未开通或验收。正常登录通道保持关闭，不能将官方 Mock 当真实腾讯授权。手机不等于实名或永久自然人；回收/换号申诉、全局上游生命周期、邮件/2FA、分布式风控、公开 TLS/反代、异机灾备/告警、移动真机和生产合规仍需另行设计验收。

未重新发起真实模型/SMS/腾讯调用，没有把测试能量当真实 Token/货币。没有自动 stage、commit 或 push；GitHub 旧提交不等于当前部署。

## 7. 最终源码与运行审计

- 两端轻量源码清单 **193 文件逐项 SHA256/大小一致**，远端 Git tracked + untracked 非忽略清单精确相同；**210 个本地 Markdown 链接有效**。UI 67 个源文件、36 个 runtime 文件保持已验收冻结哈希；原普通认证/聊天/PWA 范围未被后台工作覆盖。
- 最终 root 再执行 Ruff、git diff --check 及后台/诊断四测试文件 **141 passed，18.12 秒**；这是完整 1183 回归的交叉复查，不另加成一个总数。只有已知两个弃用警告，无真实上游请求或服务重启。
- 原受保护模型配置及独立正常身份配置逐字节与维护前相同；193 源文件无实际配置秘密或私钥。初次扫描误把 MAX_TOKENS 的数值识作 token，收紧为凭据键后核对通过，未泄露或更改秘密。
- 正常 API 0.7/schema7，101 条实际路径；开发参考导出 102 条，仅额外含已关闭的 dev/session。健康、就绪、测试页、后台、SW、manifest 均 200；六个公开资源与已部署构建字节一致。
- 单 API、Logto、PG 均低权限运行且 NoNewPrivileges；仅回环 8000/3001/3002/54329，未启开机自启；业务快照 timer 保持启用。Logto 固定版本及四处 listen 补丁再次校验，运行 DB 角色无 superuser/CREATEROLE/CREATEDB/replication。
- 正常 provider=openai；OIDC gate 与 simulated 均 false，旧手机/社交入口不回退。上游默认 tenant 测试用户和连接器均零，临时 fixture、OTP、OIDC 过渡凭据及本次管理凭据无残留；活动 run、预留能量为零，外键检查通过。
- 远端 HEAD 仍为 `94dcb5a9d995ec1c3b7c1bc106765a6a84077548`，本轮及此前功能改动尚未 stage/commit/push。SSH 隧道本地 8000 到远端 8000 保持可用；不把未发布源码声称为 GitHub 最新版。
