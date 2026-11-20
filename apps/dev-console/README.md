# 原生功能客户端与管理控制台

仅原生 TypeScript、仓库 Fetch SDK 和 CSS；没有 UI 框架、CDN、内联脚本或 DeepChat 代码。界面以持续内测和功能验证为目的，正式视觉留给 Gemini。构建直接打包 [SDK 源码](../../packages/client/src/index.ts)，不复制 HTTP/SSE 实现。

当前认证方向是实际接入 Logto OSS 1.46.0 托管登录和后端 Authlib 1.9.9 OIDC RP；认证核心由上游服务/库处理，不把历史自研短信或社交路径当作开源复用。客户端按可信服务端 `AuthOptions.backend` 选择入口：`oidc` 时只展示托管手机号登录/注册及回调后的明确身份确认，不保留另一套本地密码、OTP、恢复码或旧用户名直接登录入口；`legacy` 与缺字段的明确老服务仅保留兼容路径。未配置短信或第三方通道时按钮诚实停用，不能把维护用官方 mock connector 当作真实渠道。登录后默认只呈现聊天记录、新聊天、消息和发送/停止回复，无需先填写技术设定。新聊天使用服务端 daily 预设与经济模式，可选三种中性风格；开场建议是静态提示，不伪装成 AI 已回复。业务资料/设备/数据控制、角色与可追溯记忆、历史导入、检索/导出/分叉、能量/CDK 和技术参数仍完整保留在渐进展开入口。后台管理认证继续完全独立，不借托管用户身份授予权限。这不是正式视觉、生产账号风控、年龄核验、合规或真实模型质量验证。

## 远端构建与托管

遵守根 [开发约束](../../AGENTS.md)：本地仅源码编辑/查看/同步。下面命令只在 SSH 主机 `hklite-lv4`、`allira` 用户下执行；Node 路径固定。版本锁定 TypeScript 5.9.3、esbuild 0.28.2、Playwright 1.65.1、markdown-it 15.0.3；[锁文件](./package-lock.json) 由服务器生成。

```sh
cd /opt/allira/workspace/apps/dev-console
export PATH=/opt/allira/tools/node24/bin:$PATH
npm ci --ignore-scripts
npm test
npm run test:browser -- tests/admin-access.spec.ts tests/admin-control.spec.ts tests/oidc.spec.ts tests/phone.spec.ts tests/phone-account.spec.ts tests/phone-races.spec.ts tests/simple.spec.ts tests/context-memory.spec.ts tests/pages.spec.ts tests/mvp.spec.ts tests/client-safety.spec.ts tests/platform.spec.ts tests/platform-extra.spec.ts
```

`npm test` 只执行 typecheck、隔离 `.test-dist` 构建及 Node test，不覆盖用户正在使用的 `dist`。Node 24 的 `--experimental-transform-types` 用于测试中导入 SDK 的 TypeScript 参数属性，其 experimental warning 是预期的。esbuild 禁用安装脚本后仍能使用 Linux optional 平台二进制；不要 `--omit=optional`。

只有根负责人完成备份、无活动任务检查并进入部署窗口后才执行 `npm run build`。它输出 `dist/test/index.html`、`dist/admin/index.html`、哈希 JS/CSS、两张同源 PNG、manifest 与 Service Worker。现有 FastAPI 将入口托管为 `/test/`、`/admin/`，资产为 `/console-assets/`。本包不启动 API、开发服务器或新监听端口。API 一律 `location.origin`，经既有回环 8000 和 SSH 隧道访问；localhost/127.0.0.1 或 HTTPS 才具备安全随机 UUID、剪贴板等浏览器能力。

不要回传 `node_modules`、`dist`、`.test-dist`、浏览器 profile、测试产物、数据库、环境配置或任何凭据。

## 托管认证、迁移与业务数据控制

OIDC 模式的认证 UI 由真实 Logto OSS 服务提供，AlliraAi 只通过 SDK 请求开始、接收服务端验证后的私有 pending、明确确认或取消。跳转地址必须为 HTTP(S)、没有 userinfo/fragment 且 origin 精确等于服务端配置投影 `authorizationOrigin`；不从返回地址自身推导信任，不读取 callback code/state/token 充当账号会话。OIDC callback 由服务端 Authlib 完整处理后 303 返回 `/test/`，页面只 GET 恢复 pending，不自动完成登录。

`AlliraOidcClient` 使用独立 HttpOnly Cookie 和内存 `X-Allira-OIDC-CSRF`，普通登录/迁移不借普通 Bearer、普通 Cookie CSRF 或管理权限。完成登录必须核对遮盖手机号、当前条款、成年与明确使用该身份。所有 login pending 都提供默认折叠的“我有旧账号，保留历史”，未绑定手机号的旧用户也能先输入旧用户名/当前密码、明确关联；与既有密码账号的同手机号碰撞会要求此证明，不静默创建另一业务身份或合并。原来仅用手机号且未设置密码的账号有独立“原来仅用手机号登录，保留历史”入口，明确开始 `purpose:phone_migration` 的新鲜托管认证；返回后再独立勾选同号关联、保留历史并撤销旧登录，才发送 `confirmPhoneMigration:true`。普通 login 不包含此标志，不能通过勾选升级用途；须先取消再开始专用认证。迁移由服务端原子保留内部业务 ID、聊天与能量，证明与确认字段离开/重新读取后清空。网络不确定只允许人工重新读取 GET，不自动重发认证或关联 POST。

普通登录仍默认仅内存 Bearer；只有主动勾选记住设备才执行既有一次 Cookie 交换，丢响应仅 GET 并核对 owner，绝不盲目重发。刷新恢复先等待普通 Cookie GET 结果，再恢复托管 pending；删除用途 pending 不能单独变成账号登录。OIDC 模式的账号页不再呈现本地密码、OTP、恢复码或绑定手机表单，仍可修改业务显示资料、读取/撤销业务设备与显式导出。托管全局账号和真实短信/第三方连接器的配置属于上游，不在此页伪造可用。

删除 AlliraAi 业务账号必须已主动采用普通浏览器 Cookie，纯内存 Bearer 不偷偷兑换。用户显式开始时先 GET 普通 Cookie CSRF、GET 独立 OIDC 状态，再带**两项独立 CSRF**开始 `purpose:erase` 的 fresh Logto 重认证；回调后仍需逐字 `DELETE MY ACCOUNT` 和不可撤销勾选。最终 erase 前再显式 GET 普通 Cookie CSRF，只为该请求短暂传入 SDK，OIDC SDK 不持有或隐式获取普通 CSRF。作用域过期/401 阻止 POST，活动任务 409 只允许人工重试，由服务器继续校验确切普通会话、subject、用途和新鲜度。该删除只影响 AlliraAi 业务账号和在线数据，不删除 Logto 全局身份，不承诺备份物理擦除。

以下本地手机号/社交/恢复实现是 `legacy` 兼容与历史测试能力，不是本次开源认证核心，也不能据旧测试结果宣称已接入 Logto：

手机号仅接受大陆 11 位 ASCII 数字或对应 `+86` 号码；验证码为六位 ASCII，按手机号与 login/register/绑定/安全用途区分。手机号或用途改变立即使旧证明失效，页面显示服务端到期与 60 秒重发等待；不自动重试发送、注册、关联或安全 POST，未知结果要求人工重新核对。注册和首次社交关联必须确认成年与当前条款，站内密码在可选折叠栏，不强迫无密码账号先设置密码。短信未开通时不伪造验证码、不显示可发送的验证码/社交按钮；已设密码的手机账号仍可选密码登录。网络配置失败不开放旧注册；仅兼容老服务的明确 404 才保留旧注册测试路径。

社交使用独立 `AlliraSocialClient`、HttpOnly 过渡 Cookie 与内存 CSRF，不与普通用户或管理 CSRF/Bearer 混用。开始前 GET 会话，页面只跳允许的 HTTPS 微信/QQ 地址（或明确模拟时的同源 callback）；callback 后仅 GET pending，必须手动验证手机号并确认关联，已关联账号也须主动“继续社交账号登录”。不读取 URL 中的 code、state 或 access token 当作账号凭据；首次关联已有手机号只关联至已验证手机号所属账号，不静默合并另一旧账号数据。取消显式调用服务端。真实短信与微信/QQ 平台资质、回调域名、费用和上线验收仍需实际配置，模拟通道不能证明已开通真实服务。

默认账号/开发 Bearer 只在内存，刷新不恢复；开发 userId 不能冒用账号或管理身份。手机、社交和旧用户名共用原 AccountSession 完成流程，只有主动勾选“记住此设备”，账号登录后才执行一次 Cookie 交换：短期闭包持有 Bearer，交换成功清除 Bearer，以独立 HttpOnly Cookie 和内存 CSRF 工作。丢失交换响应时只 GET 会话并核验用户 ID，不盲目重发交换 POST。刷新只 GET browser session，不从存储恢复 Bearer。退出调用对应服务端注销；网络不确定会明确警告此设备 Cookie 可能仍有效。不能把“本地已退出”当作服务端注销成功。

旧用户名账号可在账号页核验当前密码、手机验证码并明确确认后绑定手机，服务器保持不可变 owner 和原聊天，不允许冲突号码自动合并；绑定不提供任意换号功能。无密码账号隐藏依赖当前密码的修改/恢复码/删除表单，改用服务器已验证绑定手机的 set_password 或 erase 专用证明：设密须确认撤销全部登录，删除仍须逐字 `DELETE MY ACCOUNT` 和不可撤销勾选，不接受在敏感操作页更换目标手机号。密码已设置的账号继续使用原密码安全操作。

有密码账号的恢复码必须在账号页填写当前密码、勾选确认后显式替换。明文仅本次挂载内存显示，可主动下载或立即隐藏；离开功能页即清除，不自动读取旧码。公开恢复表单在旧账号折叠入口，使用遮盖输入，成功后撤销全部旧恢复码和登录，不自动登录。设备列表只有主动读取后显示；撤销须确认，撤销当前设备会清空页面认证。无密码账号仍可查看/撤销设备和显式数据导出。

账号完整导出须显式确认并下载；不包含账号凭据表，但用户自行贴进聊天或导入原文的秘密不会自动脱敏。密码方式删除要求当前密码、逐字 `DELETE MY ACCOUNT` 和不可撤销勾选三重条件，无密码方式以 erase 专用手机证明取代当前密码。活动/关闭中生成返回 409，超出导出上限返回 413，不静默截断。在线业务数据删除不等于所有介质物理擦除：匿名核销墓碑和既有备份按当前保留策略留存，旧备份不能未经恢复撤销流程直接上线。

改密、退出全部登录、恢复和设备撤销最终以服务端为准。目前所有受保护请求的 401 都保守清认证，包括输错当前密码返回的 `invalid_credentials`；因此轮换恢复码、改密或删号输错密码后可能需要重新登录。页面不展示原始错误正文、凭据或内部路径。

## 聊天、Markdown 与断线

会话列表分页，支持标题/设定、新建、修改与确认删除；生成时禁止修改/删除。消息默认最近 50 条，向前分页正序去重。每个会话的草稿及待确认发送仅在当前页面内存；退出和换用户会清空，刷新不恢复。

默认“新聊天”只调用服务端预设设置接口，以本次 UUID 与风格绑定；原子准备会话设定和中性关系背景，不生成消息或扣能量。不确定响应时锁定风格，只有主动“重试新聊天”才以同 UUID/风格找回原结果，不自动重发；已删除目标返回 404 时不偷偷换 UUID 重建。切换会话、功能或用户后迟到结果不能抢占当前界面。自定义标题/设定仍可在高级入口创建。

发送时 UUID 永久绑定原文和路由模式。POST 的网络/5xx/abort 结果不确定时，只能主动以同 UUID、原文、模式重试；新草稿和模式不会替换原请求。导入与分叉同样保存原 UUID 和全部参数，不自动重发 POST。放弃本地记录必须确认，不会删除已存在的服务端结果或取消后台生成。

[消息列表](./src/messages.ts) 按 ID 保留历史 DOM，仅改变化的节点；活跃助手增量最多每 100ms 绘制一次，终态立即同步覆盖。保留焦点和上滚位置，仅距底部少于 80px 时跟随。先 state 再 recent；如果最近消息含 state 尚未发现的 generating assistant，按 runId 查回任务。

[Markdown 渲染](./src/markdown.ts) 仅使用 markdown-it 的 CommonMark token，不调用 HTML renderer。固定 DOM 白名单，原始 HTML 转文本；图片仅显示替代文字，不创建 img 或发外链请求。链接仅允许绝对、无凭据的 HTTP(S)，新窗口带 `noopener noreferrer` 与无 referrer。代码块复制由用户手势触发并保留原始文本；不支持 GFM、数学、Mermaid、外链预览或语法高亮。输入超过 64,000 UTF-16 单元、超 4,096 token 预算或解析异常时退回纯文本。流中先纯文本，终态再解析 Markdown。

自动恢复只针对 EOF、网络中断和 5xx：最多 5 次退避、0.8–1.2 抖动、上限 6 秒；离线等待 online。每次只 GET 原 run 的 `after=0`，先清空本地 assistant 再重放，终态 Message 快照取代增量。401、协议错误、手动断开均停止自动恢复；切换会话/功能、退出及换用户会撤销计时器与订阅。不同作用域迟到请求由 epoch 和 AbortController 隔离。只有主界面的显式“停止回复”调用 cancel；断开/切页不取消后台任务。连接中断的“再试一次”也保留在主界面，不要求先理解订阅参数。本地连接与服务端状态分别显示。

## 检索、导出、分叉与记忆

会话工具检索本人标题/消息，关键词变化清游标并隔离迟到结果。仅显式点击下载请求 JSON/Markdown 归档；复制只能复制本次显式导出结果，离开或改选清除内存。分叉可读取最近 50 条内的完整已完成 user/assistant 对作为截止点；最终有效性由服务器原子事务判断，活动生成/半截或非法截止点返回冲突。分叉不复制能量费用和用量。

角色支持分页 CRUD，当前会话可绑定角色、核心记忆与关系背景，主动更新规则摘要；规则摘要不是实际模型长期记忆质量证明。人工记忆卡分事实/偏好/边界/关系，新增、编辑、启停和删除必须明确确认；可选绑定本会话最近 50 条中完整、相邻、同 run 且已完成对话对的用户消息，不自动把助手输出当事实或推断亲密关系。单会话最多 50 张、全部内容最多 20000 字符，启用卡均为必选上下文；超预算会拒绝，不静默丢弃边界。未知修改结果不自动重试，须刷新对应列表核对。

历史块分页检索只搜索规则摘要，原文仅在显式点击单块来源时请求并纯文本展示；旧版摘要无精确来源时明确提示。停用卡或块只改变提示上下文，不删除原始聊天；分叉不暗拷贝卡、块或旧摘要。摘要视图的 6000 字符预算不代表其他原文已删除。

上下文检查只读 POST 调用真实规划器并回滚暂时业务写入，显示字符预算、分段、入选/省略/待摘录 ID 与警告，不生成、扣费或调用模型。响应已包含私有提示词并留在当前挂载内存，默认折叠仅是显示控制，不冒称尚未读取；清除、关闭高级入口、切页、换会话/用户会释放引用并隔离迟到响应。这不是 Token、缓存或可靠语义记忆证明。

导入支持 JSON/Markdown/文本，文件或粘贴最多 1 MiB UTF-8，先纯文本预览再确认提交。导入原文只显式读取，关闭/离开清除；不会执行其中 HTML。

模式、余额、整数账本、CDK 兑换和单任务实际路由/结算从服务端读取。兑换成功或离开清输入码。输入/输出字符数不是 Token、供应商报价或生产成本；Mock 不表示实际模型效果。

## PWA 与依赖声明

[Service Worker 策略](./sw-policy.mjs) 仅允许六项同源公开 GET：`/test/`、manifest、当前哈希公开聊天 JS/CSS、同源 192/512 PNG。拒绝查询串、Authorization、非 GET 和外源；不处理 `/api/`、`/admin/`、管理资产或 SW 自身。公开网络抓取省略 Cookie/Bearer、不跟随重定向；升级只清理自身缓存前缀。它不缓存手机号、验证码、OIDC 或社交 pending/授权地址/过渡会话、旧账号证明与迁移确认、账号身份、聊天、草稿、密码、恢复码、CDK、Cookie 会话响应、私有记忆卡/块/来源或上下文预览，也不提供离线写队列。

离线刷新仅公开页面壳，不能以缓存恢复身份或私有数据。联网后，主动记住过的设备可以 GET Cookie 会话恢复身份；内存草稿/未确认请求不恢复。Manifest 与 SW scope 都是 `/test/`，安装提示由浏览器决定。浏览器测试显式检查 PNG 解码、非隐私 profile 的 Chromium 安装资格、真实 API 离线失败与公开壳；这不是各手机平台兼容性保证。

唯一新增运行时依赖是 markdown-it 15.0.3（MIT）。其六项依赖并非全部 MIT：entities 为 BSD-2-Clause、argparse 为 PSF-2.0，mdurl 含附加通知。构建读取七包完整 LICENSE/LICENCE/COPYING/NOTICE 并嵌入可下载的 JS bundle，缺声明则构建失败；不添加私有或额外 SW 缓存路径。测试检查声明存在，不将许可确认表述为法务审计。

## 管理认证与诊断

后台入口使用 Cloudflare Access。页面不接收 SSH 一次性链接、code、用户 Bearer 或 Logto 身份作为管理证明：旧 fragment 立即丢弃，不解析、不暂存、不交换，没有 CLI 文案或旧“登录管理”按钮。启动只显式 `GET /api/v1/admin/session`，由边缘／后端验证 Access assertion 并初始化独立 HttpOnly 管理 Cookie 与内存 CSRF。可选 `authentication:'cloudflare-access'`、`identity.email` 和固定 `logoutUrl` 仅是展示元数据；旧三字段响应明确显示身份未知，不伪造 email。401 展示 Access 入口，通过固定 `/admin/` 完整文档导航让边缘认证处理，不信查询参数或返回 URL，不自动 POST 登录或重试。SDK 的 code 方法只保留显式服务器测试兼容；Access 部署应拒绝 code 交换和无有效 assertion 的旧 Cookie。

注销先由 SDK 同步捕获当前 CSRF，再立即清除本地 CSRF、私有列表和页面作用域，随后只等待一次服务注销结果。成功且仍是同一 auth epoch 时才完整导航固定同源 `/cdn-cgi/access/logout`，不使用会话或响应里提供的任意 URL；迟到旧查询、注销成功或 401 都不得清除或导航覆盖后来的管理登录。失败／网络不确定不自动重试或跳转，不把本地清空冒称 Access 已退出。所有业务修改仍需独立管理 CSRF、Origin 和原有确认。页面不读取或构造 Access JWT，也不把普通账号或开发身份授予管理员。

[管理控制模块](./src/admin-control.ts) 为用户提供字面查询与身份筛选：查询只匹配业务用户 ID、旧用户名和显示名称，不搜索手机号原文；百分号、下划线和反斜杠不是通配符。查询在去除两端空格前拒绝控制／隐藏格式／非法 Unicode，归一化后最多 100 字符、400 UTF-8 字节；筛选变化清空旧游标、行和设备。只展示 DTO 明确提供的脱敏手机号、身份类别、余额和创建时间，旧服务缺字段显示“未知”，不臆测为正常、开发身份或已绑定。用户与设备分别分页，只有主动点“查看设备”才读取该用户仍有效的本地 Bearer／浏览器会话；不通过设备 ID 构造 Cookie，不显示 token。

单设备和全部本地登录撤销都需要新的勾选确认；未知 POST 结果不自动重发，只有显式刷新对应用户／设备列表并核对后才可重新确认。查询、owner、分页、视图和管理登录作用域共同阻止迟到读取或撤销结果污染新选择。撤销覆盖该账号／开发身份本地登录和对应待处理证明，保留聊天、能量和身份；**不是封禁、删除账号或 Logto 全局 SSO 注销**，不阻止重新开发登录或经认证重新登录。原有显式消息查看、停止任务、删除会话、签发／停用 CDK 的确认门禁保留。完整 CDK 仅本次主动签发显示，离开／清除即释放，不确定签发不自动重试。

“路由配置”仅 GET 脱敏供应商类型、模式／能量／回退和运行字符／频率／并发配置，不显示或编辑 Key、上游地址或秘密模型配置，不调用模型；`configurationOnly=true` 不是服务可用性、额度、报价或容量探测。

“健康诊断”分开显示业务 SQLite、OIDC 运行门禁与计数、Logto PostgreSQL 备份元数据。可选 `identity`／`identityBackup` 缺失明确为未知，`serviceProbe=not_performed` 不解释为身份服务健康；普通管理登录不授予独立 Logto Console 权限，本页不调用 Logto Management。`recorded` 只表示历史恢复报告，`checksumVerified=false` 明确表示本次未做校验；“近期备份”不等于可恢复或新演练通过。原始 warnings 只显示数量，不回显可能带路径、issuer/sub、手机或凭据的文本；两种数据库是独立备份边界，不宣称物理擦除、全局身份生命周期或异机灾备。

## 验证范围

拦截浏览器测试使用独立可信虚拟回环 `127.0.0.1:49183`，不启动监听；全部页面/资产/API 精确拦截，外源中止，Service Worker 默认 block，构建读 `.test-dist`。[Access 登录回归](./tests/admin-access.spec.ts) 覆盖启动 GET／旧 fragment 丢弃／零 code POST、惰性 email 与未知可选字段、401 固定完整导航、错误和网络不确定不自动重试、显式 GET 恢复、注销立即清空／captured CSRF／固定退出／恶意返回 URL 忽略、失败不跳转、迟到注销不能影响新登录及业务 401 清空。SDK 使用 FakeFetch 验证相同字段、独立传输、无伪造 Access assertion／Bearer、CSRF version 竞态与声明边界；不是实际 Access 认证证明。[管理新增 11 项](./tests/admin-control.spec.ts) 覆盖字面查询／身份筛选／游标绑定、Unicode 边界与 XSS 惰性、旧字段未知、设备显式读取与分页／新确认、撤销 0／1 与全部、未知 POST 只显式刷新解锁、迟到查询／owner／写响应／视图隔离、只读路由、不泄漏原始诊断、可选备份与历史报告语义、401／403／429、立即注销及旧 401 不污染新登录。托管模式继续覆盖关闭全部旧凭据入口、可信 issuer 跳转、pending 非登录、明确身份/条款/成年与一次 Cookie 交换、旧用户名主动迁移和 409 证明、无密码手机专用新鲜用途/独立确认/普通用途不得升级、未知 POST 不重试、迟到身份隔离，以及 ordinary restore 屏障、删除双 CSRF/逐字确认/409 保留证明/离页清理/过期 ordinary 阻止 POST。兼容测试仍覆盖旧手机号/社交/绑定/安全证明，保留默认简聊/渐进展开/预设原 UUID 重试、人工记忆卡来源与只读上下文、旧聊天/管理/MVP、Markdown、节点/焦点/滚动、重连停止条件、Cookie/CSRF、恢复/设备/删号、检索/分叉和虚拟真实 SW 原始 303 观测回归。Node test 覆盖 reducer、UTF-8 边界、严格手机号/验证码/社交和 OIDC 授权 URL、退避、SW allowlist 与许可；这些拦截用例不是实际 API 或 Logto 集成证明。

Playwright 无 webServer，trace/video/screenshot 全关。含真实凭据的 live 使用手动 context，失败只报静态阶段名、清除秘密并关闭 context；不输出原始响应/错误、CLI 链接、恢复码或完整 CDK。公开壳安装测试使用服务器全新临时非隐私 profile，结束校验精确路径再删除，不碰用户 profile。

本次 Access 改造只执行远端 SDK／声明编译与虚拟回环拦截回归。最终同一命令以远端 allira 用户通过 SDK build 和 **452/452**（原有 436 项加 Access FakeFetch 16 项，包含严格声明编译与 149 条类型负例）、console 源码 typecheck、esbuild **`.test-dist`**、Node **25/25**，以及全部十三个路由测试文件 **110/110**（原有 97 项加 Access 13 项，约 1.4 分钟；SSH 总耗时 **94.445 秒**），exit 0；未将 Playwright 转译或源码 typecheck 冒称测试文件全量严格 TypeScript 检查。本轮不访问实际 Cloudflare、后台 API 或 Logto，不启动监听／服务、不安装依赖、不覆盖正常 `dist`，不新增或运行 live、不等待用户登录。除约定的两项旧认证 UI 断言适配外，普通认证／聊天／PWA、管理控制逻辑和历史 live 保持冻结。部署后由用户自行验证真实 Access 入口、允许的管理身份及固定退出路由。拦截与 FakeFetch 测试不能证明真实边缘策略、assertion/JWKS 校验、部署路由或公网安全均已验收。

下文旧一次性链接后台专项和 0.7／0.6／0.5／0.4 实际结果均为**历史证据**。相关源文件原样保留，不适用于当前 Access 登录页面，不在本次执行；尤其不能再次运行依赖旧 fragment／CLI 交换的历史后台 live。未来实际维护验收仍须单独授权、备份、无活动生成和现有单进程 Mock READY，不以历史授权自动继续。

[历史 0.7 一次性链接后台实际专项](./tests/live-admin-control.spec.ts) 只在当次 root 备份、部署现有单进程 Mock API、确认 OIDC／短信／社交及官方 mock 均关闭并发出专用 ROOT_READY 后运行。私有 fixture 为 allira 所有的 0600 单链接普通文件，0700 私有父目录、绝对路径、`O_NOFOLLOW`，含两名关联 owner、一个开发身份、首 owner 两条 Bearer 证明与一条浏览器设备 ID、另一 owner 一条 Bearer 证明及预置业务计数；浏览器设备 ID 仅与 UI GET 顺序核对，绝不构造 Cookie。唯一允许的普通请求是 Node 进程持 fixture Bearer 对 `/session` 的九次只读隔离核验，凭据不进入浏览器、日志或产物。管理链接由 CLI `--env-file /dev/null issue-login --json` 子进程产生，仅传 PATH/HOME/LANG 与明确的 development／数据库路径／管理 enabled／OIDC disabled／Mock allowlist 环境，所有 stdio 为 pipe，不继承受保护身份／模型配置；一次性链接只从 Node 内存传给同源管理页，立即清除 fragment、手动交换。专项核验 Cookie 刷新、脱敏查询与身份筛选、显式设备读取、单设备与全部撤销后的 200／401 owner 隔离、聊天数／能量不变、只读路由、分开的备份／门禁诊断及注销失效；仅一次单撤销、一次全撤销和管理注销，不调用 Logto／模型、不读取聊天正文或原始运维记录。trace／截图／视频关闭，失败只保留固定阶段、类别与数字，结束关闭本次临时浏览器；fixture 与本次业务／管理数据由 root 精确清理。分页、未知结果和迟到竞态使用完全拦截测试，不额外增加实际写请求。本轮最终冻结前，远端 allira 同次通过源码 typecheck、esbuild `.test-dist`、Node **25/25** 与全部隔离浏览器 **97/97（原有 86 项加后台 11 项，约 1.3 分钟，SSH 总耗时 87.533 秒）**，唯一后台 live 仅 `--list` 确认一个 case；普通认证／聊天／PWA 与范围外源码保持冻结。本地／远端 67 文件 SHA 一致后，root 备份并在现有单进程 API 0.7.0 的 Mock／development／管理 enabled、OIDC disabled 且非模拟、短信／社交和官方 mock 关闭窗口完成同份正常构建部署与专用 ROOT_READY，随后只执行约定一次：**exit 0，1/1 通过（4.6 秒，SSH 总耗时 6.709 秒）**。通过一次显式管理链接交换与 Cookie 刷新、一次单设备撤销、一次全部本地会话撤销和管理注销；九次只读普通会话核验为六次 200、三次 401，另一 owner 不受影响，原聊天数、能量余额和关联身份保留；只读路由、门禁与分开的备份语义、注销后管理 Cookie GET 401 均通过。生成、聊天正文读取、异常外部请求、错误凭据头、页面错误、额外业务请求与运输失败的终态计数均为零，浏览器 Storage 未留存秘密，临时 context 关闭成功；不读取或复制 HttpOnly Cookie。完成立即 BUSINESS_FREEZE，不重跑。root 随后确认 finish **exit 0（5.353 秒）**：精确恢复常态 overlay，保持原受保护模型配置字节不变、供应商恢复 OpenAI，OIDC 仍关闭且非模拟；精确清理本次两名注册账号和一名开发身份、测试管理会话与未用一次性 code，私有 fixture 移除、外键完整、生成数零。此后只更新本证据段，其他 66 文件与 runtime 36 文件冻结；不再执行 HTTP、构建或测试。该后台验收不等同封禁、全局 SSO 注销、Logto Management／真实渠道／模型可用性、恢复演练、生产负载或合规证明。

[0.7 托管实际专项](./tests/live-oidc.spec.ts) 只在 root 完成备份、部署唯一 Mock API、真实本机 Logto OSS/官方维护 mock connectors 并明确 ROOT_READY 后运行；其历史通过证据单独保留，不在后台窗口重复执行。专项直接操作实际 hosted UI，真实授权回调由后端 Authlib 处理，不拦截或伪造 OIDC callback/令牌；只有上游官方 mock-social 的外部运输边界按该测试连接器的协议模拟，不代表真实微信/QQ。默认 240 秒上限，五个本次业务 owner：新手机号、未绑定旧用户名 A、同手机号密码账号 B、无密码手机账号 C、需补手机的官方 mock-social。A 先主动迁移不创建新账号，B 碰撞后证明，C 使用专用新鲜用途加独立确认；核验原 ID、两条历史消息和预置能量。每人最终均需 fresh hosted 重认证与双 CSRF、逐字/勾选确认才删除 AlliraAi 业务数据；保留 Logto 全局身份，由 root 结束后清理本次上游测试账号。全程零生成 POST，并检查六公开缓存、PNG/安装资格、实际离线私有请求失败与联网普通 Cookie 恢复。

私有 fixture A/B 提供 `{username,password,userId,conversationId,phone,expectedWallet?}`，C 提供 `{userId,conversationId,phone,expectedWallet?,passwordless?:true}`，新身份配置提供 `{newPhone,socialPhone,socialSubject}`；均要求本次 root 创建的 allira 所有、0600 单链接普通文件、0700 私有目录，精确绝对路径与 `O_NOFOLLOW`。官方 Mock SMS 覆盖写入的私有记录只在发送成功后由服务器 Node 进程有界读取，核验新鲜度、号码与 Register/SignIn 用途；六位随机值仅短暂填到真实 Logto hosted 表单，不借 HTTP debug、页面自动回填、日志或测试产物。维护 mock connector 不是实际短信投递、真实第三方渠道、生产负载或合规证明。

[0.5 历史实际专项](./tests/live-context.spec.ts) 检查默认 daily/可选 listen、经济模式显式生成、人工卡与来源、只读规划回滚、摘录源对、停用不删原文、另一 owner 隔离、Cookie+CSRF、六公开哈希缓存/非隐私安装资格/实际离线私有请求失败与测试账号确认删除，历史结果如下，不代替当前身份专项。

[0.4 历史实际专项](./tests/live-platform.spec.ts) 覆盖记住设备/刷新/私有草稿清空、Markdown、检索/归档/完整对话分叉、恢复码签发/设备撤销/恢复撤销旧登录、新 Cookie/账号归档、管理诊断、Cookie 下六公开缓存/实际离线 API 失败/联网恢复、双确认删号。旧独立 PWA 测试曾因全局 `serviceWorkers:block` 未 override 卡在 ready；已加显式 allow 和有界阶段定位，不将失败报作全绿。最终专项包含非隐私安装资格与 PNG 解码，避免重复旧 live。

当前 0.7 托管模式最终冻结前，远端同次通过源码 typecheck、esbuild `.test-dist`、Node **25/25** 和全部隔离浏览器 **86/86（约 1.2 分钟，整个命令 81.460 秒）**，包括原有 85 项与新增的虚拟路由／真实公开 SW 合成回归；完整模式与 callback-only 模式各 `--list` 核验唯一 case。既往实际尝试在 Page Response 层将 SW 可见的 callback 200/fromSW 误当原始响应而失败，不计作通过；root 合成诊断和新回归证明，该可见响应可归属于最终公开壳，而 CDP `Network.requestWillBeSent.redirectResponse` 仍能观测原始主框架 GET/document **303 → 精确 `/test/`、无 query/hash**。因此只修正测试观测，未改认证运行时、SW、依赖或网络边界；实际专项仍强制核验原始 303、最终公开 URL、独立私有 pending GET 和明确 UI 确认，不以最终 200 代替回调证明。root 备份、确认无活动生成、在唯一现有 Mock API 0.7.0/schema 7 上启用真实本机 **Logto OSS 1.46.0 + Authlib 1.9.9** 后，分两次独立 ROOT_READY 窗口执行约定的一次专项：首 owner **callback-only 1/1（4.1 秒，SSH 6.104 秒）** 仅到原始回调／pending，complete、migrate、erase、生成均为零，明确不是完整链验收；随后默认**完整链 1/1（30.6 秒，SSH 32.532 秒）**通过五类 owner、十次真实原始 303 回调、新手机登录、A 未绑旧账号先证明迁移、B 同号碰撞后证明、C 无密码手机专用新鲜用途与独立确认、官方 mock-social 强制补手机，核验旧 ID／两条历史消息／能量保留，五次 fresh 同主体／确切普通会话双 CSRF 与逐字／勾选确认业务删除，以及六公开缓存、192/512 PNG、非隐私 Chromium 安装资格、实际离线私有请求失败与联网普通 Cookie 恢复；生成 POST、错误凭据头、异常外部请求、页面错误、运输失败及浏览器 Storage 留存均为零，context／精确临时 profile 清理成功。这是实际开源 issuer 与真实 Authlib RP 回调，不是模拟发行方；短信和社交仍是上游官方维护 mock connector，并非真实短信、微信／QQ渠道或生产风控／合规证明。两次结束均立即 BUSINESS_FREEZE，由 root 精确恢复原常态 overlay、关闭 OIDC 运行门禁和官方 mock、保持原受保护模型配置字节不变；完整链业务 fixture 由 UI 删除，root 核验清理五个上游测试身份与四个私有 fixture、外键完整及零生成。此后仅更新本证据段，本地／远端其他 63 文件和 runtime 35 文件保持冻结；不继续业务 live，不把本段表述为模型质量、负载、手机全平台兼容或 Logto 全局身份生命周期验收。

历史 0.6 自研兼容身份专项冻结前，远端同次通过 typecheck、esbuild `.test-dist`、Node **23/23** 与全部 route 浏览器 **68/68（约 1.1 分钟）**，包含原有 49 项和身份新增 19 项；本地/远端 **58 文件逐项 SHA256 一致**，runtime 32 文件冻结。root 完成备份与无活动任务检查、部署同份源码到现有唯一 API 0.6.0/Mock/schema 6，并明确 test-file 短信与 test 社交通道 READY 后，仅执行约定一次 [身份实际专项](./tests/live-identity.spec.ts)，**1/1（约 1.2 分钟，SSH 总耗时 74.667 秒）** 通过。覆盖无密码手机号注册/Cookie 刷新、微信首次手机明确关联和已关联显式继续、QQ 独立手机关联时主动选填密码且删除、旧用户名绑定与手机验证码登录保留 owner/原聊天、set_password 撤销旧 Cookie、erase 用途专用验证码、所有本次测试账号删除、零生成 POST、账号与社交独立凭据边界、六公开缓存、192/512 PNG、非隐私 Chromium 安装资格、实际离线私有 API 失败和联网 Cookie 恢复；页面错误和浏览器 Storage 留存均为零。验证码只由服务器 Node 读取发送后新增私有 outbox 随机记录，无 HTTP 验证码、日志凭据或浏览器自动回填；context/profile 清理完成。结束立即通知 root **BUSINESS LIVE FREEZE** 并请求恢复原受保护配置，之后仅更新本段记录，不再执行其他业务 live。该结果不是实际短信投递、真实微信/QQ 平台接入、真实模型质量、生产负载、所有手机兼容或合规证明。

历史 0.4 最终隔离同次运行通过 typecheck、esbuild `.test-dist`、Node **18/18**（含完整第三方声明断言）与全部 route 浏览器 **36/36（28.1 秒）**。本远端 44 个源码/配置/测试/锁文件逐文件 SHA256 一致后冻结；root 核验并正常构建部署同一份源码。随后只运行约定一次最终实际 Mock 0.4 专项，**1/1（13.1 秒）** 通过，覆盖非隐私 PNG/安装资格、六公开缓存、真实离线私有 API 失败、Cookie/恢复/设备/数据删除全链。已立即声明业务 LIVE 冻结，由 root 恢复原受保护环境；此后仅更新本段测试记录。旧聊天/管理/MVP 实际链另一次运行通过，但旧独立 PWA 的历史超时及未单独复跑情况如上保留，不冒称同次全量绿。以上不是实际模型质量、真实负载、生产容量或合规验证；不再继续业务 live。

历史 0.5 最终冻结前，远端同次通过 typecheck、esbuild `.test-dist`、Node **19/19** 与全量 route 浏览器 **49/49（45.5 秒）**；本地/远端 **49 文件逐项 SHA256 一致**，运行时 28 文件冻结不变。root 完成备份、无活动任务检查、现有单 API Mock 部署及 recent window=1 维护设置后，仅运行事先约定的一次 [0.5 实际专项](./tests/live-context.spec.ts)，**1/1（10.8 秒）** 通过：默认 daily / 可选 listen 设置本身无生成与能量扣除，两次显式经济模式生成，人工边界卡绑定完成用户来源，只读规划预算及待摘录回滚，精确来源对、停用不删原文、跨 owner 404、Cookie+CSRF、六公开资源缓存、192/512 PNG 解码、非隐私 Chromium 安装资格、实际离线私有 API 失败、联网 Cookie 恢复与测试账号确认删除。结束立即通知 root **BUSINESS LIVE FREEZE**，由 root 恢复原受保护配置；此后仅更新本段结果和源码清单摘要，不再执行其他业务 live。此验收不是实际模型质量、真实负载、生产容量、各手机安装兼容性或合规验证。
