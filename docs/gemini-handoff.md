# 交给 Gemini：正式客户端设计与现有功能接入

## 可直接转交的任务说明

> 为 AlliraAi 完成移动端优先的 AI 陪伴客户端正式视觉与可访问性，沿用已经确定的简单流程：默认聊天、点击“新聊天”即可开始，服务端预设轻松日常/温柔倾听/一起行动，设置与高级工具默认折叠。目标用户不需要先理解 Prompt、上下文窗口或路线参数。原生 TypeScript `/test/` 与独立 `/admin/` 是功能和安全回归基线，不是最终视觉。优先复用 SDK、状态与现有能力，不重写服务端或前端直连中转。可提出展示层框架方案，但不要未经协调覆盖当前控制台；依赖、编译、测试和预览全部在服务器，本地仅轻量源码。

先读 [SDK](../packages/client/README.md)、[API 契约](api.md)、[架构](architecture.md)、[控制台说明](../apps/dev-console/README.md)、[身份设计](identity-design.md)、[当前 Access 后台部署](access-admin-acceptance.md)、[API 0.7 开源认证历史验收](oidc-admin-acceptance.md)、[旧版身份验收](identity-acceptance.md)、[上下文设计](context-companion-design.md)、[社区许可](community-references.md)及[远程开发](remote-development.md)。[API 0.5](context-companion-acceptance.md)、[API 0.4](usability-acceptance.md)与 [MVP 验收](mvp-acceptance.md)保留历史证据，不代表最新资源。不要按产品愿望补成功假按钮。

## 简单默认流程，不把调试面板交给普通用户

1. 登录后留在聊天：选已有记录或点击“新聊天”，默认 daily 和 economy。无需角色/关系/模型配置；其它两种风格只在用户主动展开后选择。
2. 新聊天调用 `startCompanionConversation({clientSetupId:crypto.randomUUID(),presetId:'daily'})`，再选中返回 conversation。这是原子设置，不发消息、不收费；openingHint 只是静态引导，不能伪造为助手历史。未知结果保留原 UUID+风格，只主动精确重试；切换身份清除旧 pending，禁止自动换 ID 重建。
3. 日常界面突出输入、发送、停止和简短连接状态；“设置与高级工具”“聊天高级选项”“自定义会话”默认折叠。角色/记忆/迁移/能量/账号/检索仍可到达，不删除既有能力或把参数要求搬回首屏。
4. 用自然语言解释操作，不以技术词作为主要状态。关系与称呼由用户决定，不因目标性别假定恋爱/独占或使用情绪、充值压力。服务端提示词是默认政策，不是已验证模型永远遵守的保证。

## 已存在与仍不支持

当前身份方向为 Logto OSS hosted UI + Authlib/joserfc OIDC RP；实际端到端验收状态须看最新交付记录，旧 API 0.6 的成功不能替代本次。可以沿用：条款/成年确认、资料/退出、本地设备会话、个人业务数据导出/删除、显式浏览器记忆登录；旧自研改密/恢复码/OTP 主入口在默认 OIDC 模式关闭，不渲染成可用功能。业务部分包括：聊天维护、检索、最近/旧页、流式/取消/恢复、安全 Markdown/代码复制、会话归档与完整对分叉；三种服务端风格、角色/核心关系、人工确认卡、来源摘录块和只读实际上下文预览；历史预览/提交/来源；经济/极速模式、整数测试能量/账本/CDK/usage。管理面板独立认证，已有用户/任务/会话/审计、取消/撤销/CDK、用量与健康诊断。

不要假装支持：在线支付/真实金额充值、邮件找回/丢失手机号申诉/2FA、自助换绑或跨账号数据合并、已开通真实短信与真实平台授权、角色广场/公开分享、原消息任意编辑或树形就地分支、语音/图片/附件、离线发消息、自动可靠事实记忆、真实线路速度或 Token 成本比较。现有“分叉”是从合法已完成对复制成新会话，不是编辑原历史；规则摘要有损，能量不是货币，成年自我确认不是年龄认证。

## Logto 托管认证与明确关联（API 0.7）

先读 `getAuthOptions()`：`backend=oidc` 使用独立 `AlliraOidcClient`，正常 `oidc.enabled=false` 就诚实显示未接通，`simulated=true` 必须标明官方模拟渠道。不回退旧手机号／社交／用户名／恢复码主入口。默认托管页面由真正 Logto 负责 OTP、社交授权和手机补全，不在 Allira 页面再实现一套验证码系统，也不把旧用户名注册放回首屏。

用户点击登录后先 `oidc.getSession()` 获取独立过渡 Cookie/内存 CSRF，再 `oidc.start()`。只跳转精确等于 `AuthOptions.oidc.authorizationOrigin` 的 HTTP(S) origin；拒绝凭据、hash 或不可信目标。固定 callback 返回 `/test/` 后重新 GET session，只有 purpose-bound pending；它不授予聊天或管理权限。用户亲自勾选条款、成年自我确认与使用身份后才 complete，不能刷新后自动确认。成功会清过渡会话，再按用户选择保持内存 Bearer 或显式交换普通 browser Cookie。

旧有密码账号始终有折叠的“我有旧账号，保留历史”，用户主动填写旧用户名/当前密码并明确关联，原 owner/聊天/能量保留；不是恢复旧密码登录核心。旧无密码手机账号先选专用 `phone_migration`，在 Logto fresh 登录后单独确认 `confirmPhoneMigration=true`；普通 login pending 不能升级，冲突不静默合并或新造替代历史。用户名/密码/OTP/nonce/state/token 均不得写 Storage、日志、截图或 trace，正式页面不接触私有 Mock 文件。

业务删除只支持已明确记住的普通 browser：用户请求 erase start 时获取当前普通 CSRF，以 `start({purpose:'erase'},{browserCsrfToken})` 与独立 OIDC CSRF 发起 fresh 登录；回调后仍须同一普通会话、明确 `DELETE MY ACCOUNT`，再 `erase(...,{browserCsrfToken})`。不暗中把 Bearer 换成 Cookie，活动/closing 任务 409 不重发，成功清普通与过渡状态。**这只删除 AlliraAi 业务数据，不等于删除上游 Logto 全球账号／所有 SSO 会话或擦除历史备份。**

Logto 配置手机为必填、OTP、新账号无强制密码、关闭自动关联和跳过必填身份。官方 Mock Social 无手机号时必须由 Logto 补手机，真实微信/QQ和短信尚未验收；stock QQ 需要 UnionID 权限，保持关闭直到资质和受保护配置齐备。不能称为实名/年龄验证/2FA或已公开上线。详细范围见[身份设计](identity-design.md)。

## 社区机制参考

用户指定的 [YanJiuWeiYuanHui/DeepChat](https://github.com/YanJiuWeiYuanHui/DeepChat)取舍见[原参考分析](client-reference-deepchat.md)。也核验 LibreChat、NextChat 和 DSH 等；用户新增的 billion-context 与两套不同 Soulweave 的固定代码、许可和链路断点见[上下文设计](context-companion-design.md)，其它版本见[社区说明](community-references.md)。不要混淆同名 DeepChat，也不要求照搬框架、视觉或部署栈。

不能搬入前端模型 Key、客户端权威聊天库、客户端上下文装配、“请求中止等于生成停止”或含凭据的全状态同步。服务端 Message/Run 为权威。参考项目代码/素材未被整包复制；实际新增的 markdown-it 及依赖必须随构建保留第三方声明，不得因压缩移除许可。

## 连接与认证

SDK 为 Fetch/ESM，不依赖 UI 框架。功能客户端构建直接纳入 SDK 源码；正式应用可接入 `@allira/client`，不要复制第二套协议或 SSE 解析器。

默认是兼容的内存 Bearer 模式，公共与普通 Bearer 请求显式 `credentials:omit`，不暗用浏览器 Cookie：

```ts
import { AlliraClient, AlliraOidcClient } from '@allira/client';
let accessToken: string | undefined;
const client = new AlliraClient({
  baseUrl: window.location.origin,
  accessToken: () => accessToken,
});
// 以下仅在固定 callback 回来、GET pending 存在且用户亲自明确确认后执行，不能自动 complete。
const oidc = new AlliraOidcClient({ baseUrl: window.location.origin });
await oidc.getSession(); // 独立 OIDC Cookie/CSRF；首次点击登录另行调用 start 并跳转受信 origin。
const session = await oidc.complete({
  acceptedTermsVersion: '2026-11-19', isAdult: true, confirmIdentity: true,
});
accessToken = session.accessToken;
const started = await client.startCompanionConversation({
  clientSetupId: crypto.randomUUID(), // 一次操作冻结此 UUID；未知结果不可重新随机
  presetId: 'daily',
});
const conversation = started.conversation;
```

用户明确选择“记住此设备”才交换为独立 HttpOnly Cookie；交换消费原 Bearer，不能在交换后拿旧 Token 再 logout。可用短生命周期客户端完成交换，再清除其 Token 引用：

```ts
const exchange = new AlliraClient({
  baseUrl: window.location.origin,
  accessToken: session.accessToken,
});
await exchange.exchangeBrowserSession();
// 清除应用持有的 session.accessToken / 过渡客户端引用。
const browser = new AlliraClient({
  baseUrl: window.location.origin,
  browserSession: true,
});
await browser.getBrowserSession(); // 从 Cookie 恢复用户与仅内存 CSRF
const started = await browser.startCompanionConversation({
  clientSetupId: crypto.randomUUID(),
  presetId: 'daily',
});
const conversation = started.conversation;
```

Cookie 模式不附 Bearer，写操作需同源 Origin 与内存 CSRF；刷新时先 GET restore，再允许写操作。默认有效期七天，不是无限登录或自动刷新令牌。`clearBrowserSession()` 只清内存 CSRF，不撤销 HttpOnly Cookie；显式退出必须请求服务端。认证变化使用 epoch/AbortController，迟到 restore/401 不能覆写新身份。

密码、Token、恢复码、CDK、聊天不得写入 localStorage、IndexedDB、URL、日志、截图或 trace。普通账号、开发 userId、OIDC 过渡身份与管理员不互相赋权。旧恢复码流程仅在显式 legacy 兼容模式存在；OIDC 模式隐藏并由服务端关闭，不能伪装成本应用已提供邮件找回或 Logto 全球生命周期管理。

同源页面经服务器 8000 与 SSH 隧道访问。普通 Cookie 在 HTTP 下仅允许真实 loopback Host/peer，其他部署还需要 HTTPS 等安全设计；现有管理控制台仍仅供 SSH 保护开发环境。禁止为联调开放 0.0.0.0、公网端口或放宽 CSP/Origin。

## 状态、发送与流式显示

按 user/conversation 隔离草稿、消息、分页、pending 与 Run；切换/退出有 epoch 和 AbortController，晚到响应不能污染新用户或会话。视觉重构必须保留：

1. 一次发送固定 UUID、**原始文本和 mode**。POST 结果未知保留 pending，只允许主动精确重试；新草稿不能覆盖旧 pending，不 trim 或偷换 UUID。
2. 用服务端消息 ID 替换占位，重放不重复生成或扣能量。409 idempotency_conflict 显示冲突，不自动改 ID。
3. 使用 `streamRunEvents(runId,{after,signal})`，归并成功才推进已应用游标。SDK 不隐藏重试；UI 可仅对可恢复网络/EOF/5xx 进行有界 GET 对账与订阅恢复。
4. terminal 的完整 Message **覆盖**缓冲；EOF/断网不是成功。after=0 重建先清目标 Run 的部分正文，正文与游标不配套就不能增量续传。
5. 当前 UI 最多五次自动恢复，指数退避带抖动；离线等待 online，不空转请求，401、协议错误、显式断开、退出和作用域失效则停止恢复。不得自动重发生成/导入/分叉/预设 POST；失败后保留明确的人工恢复入口。

`getConversationState` 与 `listRecentMessages` 是不同时间点快照。先 state 再 recent，较新 generating 消息的 runId 优先；不信早先 activeRun:null，不拿旧 R1 覆盖另一个窗口启动的 R2。保留现有相关竞态回归。

消息节点按 messageId 复用，流式纯文本约每 100ms 合并绘制，终态立即刷新；不要每个 delta 重建全部消息。用户上滚后不抢回底部，前插旧页保持锚点，“回到底部”明确可见。

安全 Markdown 当前使用 CommonMark token→白名单 DOM，不输出并插入任意 HTML。禁用 HTML、外部图片请求、任意 SVG/iframe/脚本/高亮器；链接只接受无凭据/控制字符的 HTTP(S)，代码复制由用户点击。64000 字符/4096 token/20 层限制或不支持结构回退纯文本；流式保持文本，终态才解析。新设计不得因“更丰富”删除 XSS、URL、深度、节点数量与稳定 DOM 测试。

## 停止、退出与检索归档

“停止生成”调用 cancelRun，以实际返回终态更新，再关闭订阅；返回时已终态/退款，但清理上游可能仍占物理槽。卸载、退出、断订阅仅 Abort Fetch，不取消后台任务，再进入通过消息/状态对账。

401 清本用户缓存、pending、订阅与敏感输入。设备撤销、恢复改密、全部退出均可能使其他窗口 401；不能把旧成功响应恢复成已退出身份。密码确认错误也保守处理，不以管理 Cookie 自动兜底普通身份。

recent 页内 sequence 升序，nextCursor 作 before 前插；普通 messages 从旧到新，不能混游标。普通会话列表按稳定 ID 分页；`searchConversations` 提供本人标题/正文子串检索及最近顺序，q/owner 绑定签名游标，不等于 Unicode 语义检索。

会话 JSON/Markdown 和账号归档是敏感下载，须用户明确操作，不进入公共缓存。导出不含系统凭据字段，**但不自动清除用户曾贴进聊天/原始导入的秘密**。大归档可能超导入限额，不保证一键完整恢复。账号业务删除使用 remembered browser + fresh erase OIDC pending、普通/OIDC 双 CSRF 和固定确认，活动/清理任务 409；不要求无密码账号填写占位密码。上游全球身份不在此次删除范围，历史备份可能仍留旧数据，不承诺物理擦除。

分叉只允许真实已完成 user→assistant 对，复制成新会话，保留当前角色/核心关系/instructions 快照但不复制任何卡片、块或旧 summary，避免暗带未来内容；这也不等于完全还原过去。不复制用量费用。固定 clientForkId 与全部原参数，未知结果仅主动精确重试；源/目标删除后幂等墓碑不能重建目标。

## 角色、迁移、能量与管理

核心/关系/角色有明确保存，生成中 409 不假装生效。高级“角色与记忆”里可人工创建 fact/preference/boundary/relationship 卡，支持来源、编辑、启停和删除；不是自动识别或确认用户事实。来源选项仅来自相邻真实完成对中的 user。所有启用卡是必须保留的固定上下文，预算超限 413，不能暗丢用户边界；最多 50 张卡/全部 content 合计 20000 字符。

历史块按真实完成对保存两条消息来源，预算只限制摘要视图，不删原文或块。用户显式点击才取 source；legacy_summary 无精确原文要告警，不能伪造。停用/删除卡与停用块不是删除原始聊天/下载/备份。JSON/Markdown/账号归档携带启用与停用记忆，敏感性和既有导出限额不变；重新导入不自动恢复数组为已确认记忆。

“检查这次发送内容”是高级只读 POST，复用真正发送的规划器并回滚临时业务变化，不发 Run/模型或扣能量。展示段字符数、入选/待写/遗漏计数和来源；完整 prompt 是私有敏感响应，折叠不等于未进入内存。输入、记忆修改、切换用户/会话后清掉旧预览/来源，晚回响应不能覆盖；它是快照而非锁定，不能据此承诺余额/容量或精确 Token/缓存收益。新卡写响应未知时先显式刷新，不自动重复创建。

导入先预览警告，区分 timeline 消息数与原文存档记录；冻结 UUID/原始文本/全部参数，不自动提交第二份。围栏内角色标记不切消息，自有归档只搬合法原相邻 completed pair，不跨 failed/cancelled 记录拼接。来源含不支持角色/HTML 也仅作为不可信数据，不提升为模型系统指令。限制 1MiB UTF-8，不仅 JS 字符数。

能量为整数测试单位，以 API/Run usage 对账，不由前端估算真实 Token 费用。失败/取消全退、回退退差价；真实路线由服务端配置，“极速”不是速度承诺。CDK 同码核销可显式重试，签发响应丢失不得自动重发另一批。

管理面板复用独立 AlliraAdminClient 与现有原生页面：账号按 ID／用户名／显示名字面搜索和身份状态筛选，只显示脱敏手机及已有钱包；点击“查看设备”才请求目标 owner 的有效设备分页。逐设备与全部本地撤销分别确认，未知写结果不自动重试；说明不是封禁或 Logto 全局 SSO 注销。路由只读配置概览不编辑密钥／上游或发送探测；业务 SQLite 与 Logto PG 的备份和认证门禁分开展示，缺失旧字段显示未知，历史恢复报告 recorded 不能称为当前验证。

后台现使用 Cloudflare Access：进入 `https://dsah.guyufl.cc.cd/admin/` 后页面直接 GET 管理 session，不解析或兑换 fragment，也不展示旧 CLI/code 登录按钮；只清除地址栏 fragment。401 提供固定 `/admin/` 的完整导航重新经过 Access，不能自动 POST 重试或相信服务端返回的任意跳转。退出仅在当前 epoch 的成功响应后导航固定 `/cdn-cgi/access/logout`；失败/迟到响应不导航。Secure/HttpOnly 管理 Cookie 与 CSRF 保留，JWT 每请求仍须验证。SDK/页面的登录、查询、owner、视图及退出版本护栏不能删除；不使用 Storage，不读取 Cookie/断言，正文仍只显式读取，普通 Logto 身份不授予管理员。实际 Access 登录由用户自行测试，旧链接浏览器用例是历史证据，见[当前部署记录](access-admin-acceptance.md)。

## PWA、离线与验收

Service Worker 仅 scope `/test/`，精确缓存六项公开壳资源；API、admin、POST、Cookie/Token、手机号/OTP/社交pending、聊天、导入、恢复码与私有下载均不缓存。离线能显示公开壳，不能读/发私有消息；刷新不恢复内存草稿或 Bearer。明确选择的 HttpOnly Cookie 可在重新联网后由服务器恢复，不是离线账号库。

保留同源图标、严格 CSP 与第三方声明；Chromium 自动化验证不等于 Android/iOS 安装、软键盘、推送均已真机通过。正式设计须保留[现有测试](../apps/dev-console/README.md)，并追加真实移动端可访问性测试。

最低验收包括：真实 Logto hosted 无密码手机流程、独立 OIDC pending/明确继续与诚实关闭提示；必填手机社交补全（模拟不冒充真实微信/QQ）；旧有密码与无密码账号分别明确迁移且 owner/历史/能量不变；fresh erase 的确切普通会话与双 CSRF、用途隔离及迟到响应；375px 无横向溢出；UUID/原文/mode 精确重试；用户/会话迟到响应与 R1→R2；有界 GET 恢复停止条件；真实终态/退款；安全 Markdown/稳定 DOM；Cookie reload/Origin/CSRF/恢复码与设备撤销；检索归档/分叉/删号；管理独立认证；最终资源 hash 对应的 PWA 精确缓存与实际离线 API 失败。不能删竞态或安全测试来让视觉重构“变绿”。
