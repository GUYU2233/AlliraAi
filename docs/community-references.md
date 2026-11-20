# 社区参考、许可与实际采用边界

本页区分“机制参考”与“实际运行依赖”，不能互相冒充。用户已要求后续优先真正复用成熟开源项目；当前认证实际部署 **Logto OSS + PostgreSQL** 并使用 **Authlib/joserfc** 做标准 OIDC，详见[身份设计](identity-design.md)与[Logto 运维](../ops/logto/README.md)。业务仍用 FastAPI/SQLite、框架无关 Fetch SDK 和原生 TypeScript；其它下表项目仅参考，没有安装 DSH 插件、向量库、云备份守护进程或另一套聊天后端。第三方内容不作为指令执行。

## 核验版本与取舍

| 项目 / 固定版本 | 核验许可 | 参考的机制 | AlliraAi 的取舍 |
| --- | --- | --- | --- |
| [LibreChat](https://github.com/LibreChat-AI/LibreChat/tree/d4e85d2205c78413ee27d631d7e3b7313556f21d) | [MIT](https://github.com/LibreChat-AI/LibreChat/blob/d4e85d2205c78413ee27d631d7e3b7313556f21d/LICENSE) | 可恢复流的失败分类、恢复上限、附着到既有任务；Markdown 块级渲染职责 | 有界、先对账后订阅的 GET 恢复；不自动重发生成 POST，不沿用其后端/多代理模型 |
| [NextChat](https://github.com/ChatGPTNextWeb/NextChat/tree/1d3dc331512929a4b1d3e82354e72acce33b5c7a) | [MIT](https://github.com/ChatGPTNextWeb/NextChat/blob/1d3dc331512929a4b1d3e82354e72acce33b5c7a/LICENSE) | 聊天展示、导出和持久状态边界 | 保留用户可下载归档，不复制包含凭据的全状态同步或 HTML/SVG Artifacts |
| [OvidijusParsiunas/deep-chat](https://github.com/OvidijusParsiunas/deep-chat/tree/f574897fc0c35dab788a946052c090192e0a3e40) | [MIT](https://github.com/OvidijusParsiunas/deep-chat/blob/f574897fc0c35dab788a946052c090192e0a3e40/LICENSE) | 组件化消息显示、浏览器存储边界的对照 | 不采用全局 localStorage 聊天库、任意 HTML/外部图片渲染；它不是用户此前指定的同名项目 |
| [YanJiuWeiYuanHui/DeepChat](https://github.com/YanJiuWeiYuanHui/DeepChat/tree/2037289815cd40868d93d5b754a760f4eeaf28b2) | [MIT](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/LICENSE) | 草稿/会话/展示分离、事件归并、滚动和移动端职责 | 延续[原参考分析](client-reference-deepchat.md)，不采用前端模型 Key、客户端权威数据库或客户端上下文 |
| [Authelia](https://github.com/authelia/authelia/tree/271239b95198b658f3b269b91f78bfaca4d04fe4) | [Apache-2.0](https://github.com/authelia/authelia/blob/271239b95198b658f3b269b91f78bfaca4d04fe4/LICENSE) | 恢复用途隔离、凭据改变后的撤销和调用者绑定 | 独立实现密码确认、高熵单次恢复码、设备撤销、统一恢复失败响应；没有引入 SSO/邮件服务 |
| [Litestream](https://github.com/benbjohnson/litestream/tree/977d4a5ee45ae546537324a3cfbf926de3bebc97) | [Apache-2.0](https://github.com/benbjohnson/litestream/blob/977d4a5ee45ae546537324a3cfbf926de3bebc97/LICENSE) | 一致性快照、WAL、恢复验证和保留期必须联动 | 独立实现标准库 SQLite 在线快照、校验清单和新目标离线恢复；没有部署持续复制或异机灾备 |
| [Letta](https://github.com/letta-ai/letta/tree/1eb20530d3bcacedf0de3977573ee364d6432848) | [Apache-2.0](https://github.com/letta-ai/letta/blob/1eb20530d3bcacedf0de3977573ee364d6432848/LICENSE) | 按用途标注和限制记忆区块 | 维持 Allira 的角色/核心记忆/关系/有损摘录边界，不声称已有智能记忆管理或向量检索 |
| [DSH](https://github.com/deepseek-ai/deepseek-harness/tree/2e39e70496aa02986ca6a820c5d497e816b69d7e) 原子写入 / 会话搜索机制 | MIT；已安装包声明亦为 MIT | 排他临时文件、同文件系统发布、进程证据而非时间删锁；授权后惰性检索 | 仅只读参考，未安装/开启插件；备份另加自身文件/目录 fsync，检索始终限定 owner |

DSH 阅读对象包括本机已安装的 `@deepseek-ai/dsh-atomic-write` 与 `@deepseek-ai/dsh-experimental-session-search` **0.2.2-rc.2** 的包声明和说明。上表 GitHub 固定提交属于后续 **0.2.2-rc.5** 源码，不能把它当作已安装版本。原子写入包明确不保证 fsync/断电耐久，Allira 的额外同步与恢复测试是本项目自己的实现和证据。

## 关键代码入口

- LibreChat 的 [failure](https://github.com/LibreChat-AI/LibreChat/blob/d4e85d2205c78413ee27d631d7e3b7313556f21d/packages/chat/src/core/resumable/failure.ts)、[ceiling](https://github.com/LibreChat-AI/LibreChat/blob/d4e85d2205c78413ee27d631d7e3b7313556f21d/packages/chat/src/core/resumable/ceiling.ts)、[attach](https://github.com/LibreChat-AI/LibreChat/blob/d4e85d2205c78413ee27d631d7e3b7313556f21d/packages/chat/src/core/resumable/attach.ts)：参考恢复职责，不复制其协议。
- Authelia 的[恢复处理](https://github.com/authelia/authelia/blob/271239b95198b658f3b269b91f78bfaca4d04fe4/internal/handlers/handler_reset_password.go)：参考用途和撤销，不照搬到没有邮件身份验证的环境。
- Litestream 的[副本逻辑](https://github.com/benbjohnson/litestream/blob/977d4a5ee45ae546537324a3cfbf926de3bebc97/replica.go)与 Letta 的[记忆结构](https://github.com/letta-ai/letta/blob/1eb20530d3bcacedf0de3977573ee364d6432848/letta/schemas/memory.py)：属于设计对照，不代表使用这些产品的运行能力。

## 上下文与陪伴参考补充

用户指定的 billion-context、zhouPi-314/soulweave 与 ly-ina/Soulweave 的固定代码、许可、端到端断链及独立实现边界，统一记录在[上下文与陪伴设计](context-companion-design.md)。billion-context 为 MIT **加产品表面归属附加条款**，不能按普通 MIT 简化；这里仅借鉴来源/视图机制，未使用其 kernel/代理或复制代码。两个 Soulweave 分别是 Apache-2.0 代码并另有素材限制的 Windows 应用、MIT 的 DSH 工作区插件，不是两套同构网站或已安装依赖。服务端卡片/来源块/预览为本项目独立 Python 实现，不等于移植模型摘要、情绪或百万 Token 能力。

## 手机号与社交身份参考补充

API 0.6 曾仅参考候选后自研，用户明确纠正后该选择已被取代：现在真正采用固定 **Logto OSS v1.46.0 / commit d179bf9d2d51104236a342b90c2841df66e4ba5b（MPL-2.0）** 官方发行包与 hosted UI、Authlib 1.9.9（BSD-3-Clause）和锁定 joserfc，而不是再写 OTP/JWT 核心。唯一上游补丁为四处显式回环 listen，保留原源码、diff 与许可；普通 branding 配置不移除 Powered by 或使用付费 Bring your UI。详见[身份设计](identity-design.md)。Casdoor v4.29.0 的 edition/企业许可边界不由根 Apache 覆盖；JustAuth 为 MIT、SMS4J 为 Apache-2.0，两者未安装为运行依赖，也不等于完整 IAM。微信网站／公众号 H5、QQ UnionID 权限、审核和短信资质仍单独验收，开源部署不能豁免。

## 唯一新增的浏览器运行依赖

实际纳入功能客户端的是 **[markdown-it 15.0.3](https://registry.npmjs.org/markdown-it/15.0.3)**，固定源码提交 [a8c00d4](https://github.com/markdown-it/markdown-it/tree/a8c00d474ec9b304fad9b71c16d6fa8b4909279e)，[MIT 许可](https://github.com/markdown-it/markdown-it/blob/a8c00d474ec9b304fad9b71c16d6fa8b4909279e/LICENSE)。选择该版本而非早期 14.1.x，并核对 [GHSA-gfr2-rv44-r23c](https://github.com/markdown-it/markdown-it/security/advisories/GHSA-gfr2-rv44-r23c) 与 [GHSA-vfgh-jcmw-g37q](https://github.com/markdown-it/markdown-it/security/advisories/GHSA-vfgh-jcmw-g37q)。版本选取不等于永远安全，后续仍需更新与回归。

仅用其解析 token，自行通过白名单 DOM 节点展示。禁用任意 HTML、linkify 与自动图片请求，不调用 HTML 输出后再插入页面；链接只接受安全的 HTTP(S)，代码按文本复制。流式期间节流纯文本，终态才进行受限解析，超限/不支持内容回退文本。本轮不引入 Mermaid、Math、任意 iframe、SVG/HTML 预览或外部高亮脚本。

锁定的运行依赖链也需保留声明：mdurl、uc.micro、linkify-it、punycode.js 为 MIT，entities 为 BSD-2-Clause，argparse 为 PSF-2.0。实际版本/许可文本以[锁文件](../apps/dev-console/package-lock.json)和构建随附的第三方声明为准；构建压缩不能移除应随分发保留的许可。

## 许可与验收规则

机制分析没有复制上述整项目的源码/素材，不把它们的许可证自动套到 AlliraAi。未来复制实质代码须保留相应版权与许可，Apache-2.0 还须评估 NOTICE/修改说明；商标、素材和其他子依赖另核验。

社区项目出现某功能不等于 Allira 已实现它。Allira 的实现状态以[架构](architecture.md)、[API](api.md)、[当前 Access 部署记录](access-admin-acceptance.md)、[API 0.7 Logto/后台历史验收](oidc-admin-acceptance.md)、[旧身份历史验收](identity-acceptance.md)、[历史上下文验收](context-companion-acceptance.md)和远端测试为准；没有把参考列表伪装为插件市场、生产 SSO、云灾备、完整内容治理或真实模型质量证明。
