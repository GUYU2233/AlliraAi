# 客户端实现参考：DeepChat

## 定位与结论

用户指定参考：[YanJiuWeiYuanHui/DeepChat](https://github.com/YanJiuWeiYuanHui/DeepChat)。本次静态阅读固定在 [2037289815cd40868d93d5b754a760f4eeaf28b2](https://github.com/YanJiuWeiYuanHui/DeepChat/commit/2037289815cd40868d93d5b754a760f4eeaf28b2)（版本 1.1.0，阅读日期 2026-11-19），避免后续 main 变化导致引用错位。

这是 React Web 客户端，使用 Zustand 管理状态、Rspack 构建，并通过 Capacitor 提供 Android 打包路径；不是同名的 Electron 桌面项目。依据是其 [package.json](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/package.json) 和 [中文说明](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/README.zh-CN.md)。

**借鉴交互机制和模块职责，不把它当作 AlliraAi 的替代底座。** 下文保留最初静态研究的事实与取舍；该次研究没有引入其源码、框架或素材。此后 AlliraAi 已独立实现原生 TypeScript 功能客户端、角色/摘要、导入、路由和 PWA 壳，当前能力以 [MVP 验收](mvp-acceptance.md)、[SDK](../packages/client/src/index.ts) 和 [API 契约](api.md) 为准；正式视觉仍留给 Gemini。服务端权威持久化及 [远程开发约束](remote-development.md) 不变。

## 已核实的机制与取舍

下表左侧是阅读源码得到的事实；右侧是对 AlliraAi 的实现建议，不表示已经做完这些 UI。

| 源码事实与固定版本来源 | AlliraAi 的借鉴方式与限制 |
| --- | --- |
| [drafts.ts](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/src/services/chat/drafts.ts) 用独立 Map 按会话保存内存草稿，`null` 表示新对话草稿；与会话详情缓存分离。 | 按 `conversationId` 管理未发送文本，切会话不串草稿；附件暂不迁移。网络结果不确定的发送记录要另存 UUID 和原文本，不能与可随手编辑的草稿混为一谈。若未来做本地持久草稿，先明确隐私、用户隔离和退出清理。 |
| [reply.ts](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/src/services/chat/reply.ts) 将 `applyEvent`、`settleReply` 等归并逻辑写成与组件分开的函数。 | 沿用“可测试的事件归并器”思路，但输入必须是 Allira `RunEvent`；按 `runId/messageId/event.id` 归并，终态使用服务端完整 Message 覆盖。不要复制它的内容块事件名、结束原因或续写协议。 |
| [view.ts](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/src/services/chat/view.ts) 从原始回复派生展示结构，不直接修改原始内容块。 | 区分服务端 Message 与本地 MessageView；状态文案、复制文本、Markdown 派生结果属于展示层。当前只有正文和生成状态，不添加伪造的思考、工具、引用或用量。 |
| [sse.ts](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/src/services/api/sse.ts) 将 UTF-8 字节解码、CR/LF 分行、SSE 字段累积和异步读取分层，提前退出会释放读取器。 | 学习分层与边界测试，不在 UI 再写第二套 SSE 解析。Allira SDK 已负责这些传输边界；UI 只负责事件归并、应用游标、恢复策略与连接状态。 |
| [client.ts](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/src/services/api/client.ts) 区分 HTTP、network、stream、interrupted、protocol 等错误，未收到 `message_stop` 的 EOF 不算正常完成。 | 保留“断流不等于完成”的体验，用 AlliraApiError / StreamInterruptedError / StreamProtocolError 和业务错误码分类显示。Allira 的结束标志是 `run.completed/failed/cancelled`，不是 `message_stop`；不要直接展示原始上游错误正文。 |
| [SessionView.tsx](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/src/components/session/SessionView.tsx) 组合标题栏、消息列表和输入 footer；[MessageList CSS](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/src/components/messages/MessageList.module.css) 在移动端切换滚动所有者；[viewport.ts](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/src/app/viewport.ts) 用 visualViewport 管理键盘可见区域。 | 先明确唯一滚动层、输入栏与安全区的关系，避免键盘遮挡和双重滚动。借鉴机制，不把其 768px 断点或可视高度阈值当跨端保证；后续用 Safari/Android 真机验证键盘、旋转和缩放。 |
| [MessageList.tsx](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/src/components/messages/MessageList.tsx) 用 40px 跟底阈值、用户上滚意图和 ResizeObserver 控制跟底，切换布局时恢复位置；当前仍是 messages.map 全量渲染。 | 不在每个 delta 无条件滚到底。保留用户阅读位置，按显式发送/回到底部操作恢复跟底；不要原样照搬其新消息 ID 变化即重新跟底的行为。分页插入用锚点补偿；长会话是否需要虚拟化要实测，不能宣称参考项目已具备。 |
| [parse.ts](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/src/services/markdown/parse.ts) 独立解析 AST；[Markdown.tsx](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/src/components/markdown/Markdown.tsx) 与其节流模块将流式渲染和解析/展示分开，结束后刷新最新正文。 | 借鉴解析/展示解耦与终态立即刷新；流式半截 Markdown 必须可降级展示。安全链接策略、禁用任意 HTML 和独立渲染安全测试仍需我们实现；不能仅凭 AST 就宣称 XSS 安全。其渲染节流上限 2000ms 只是实现取舍，不是性能保证；不照搬方言或先加入公式/图表。 |

## 不能照搬的架构

DeepChat 的说明将其定位为纯前端静态客户端，可连接多种模型服务；其 API 模块接收模型 Key，`reply.ts` 也包含历史上下文装配逻辑。这与 AlliraAi 的服务端聊天底座是不同产品边界：

- **模型配置留在服务端。** 前端只调用 `@allira/client`，不迁移其 API Key 表单、模型接口选择、本地模型配置或直连上游链路。
- **会话和生成结果以 Allira 服务端为准。** 浏览器内存或将来的缓存只是副本，不把前端离线数据存储直接替换为权威聊天库；当前不增加离线写入与同步协议。
- **上下文与记忆留在服务端。** 不搬它的客户端 Prompt 装配、自动记忆提取或静默裁剪策略。当前服务端采用受限窗口与有损规则摘录，固定上下文过大仍返回 413；不代表已验证模型级长期记忆。
- **不复制未支持能力。** 消息编辑、分支、语音/图片、真实 Token 用量等仍需独立后端契约。当前受限历史导入和 economy/fast 业务路由使用 Allira 自有接口，不能移植按钮或本地状态假装具备更广的协议能力。
- **Android 只是可评估路径。** Capacitor 可作为后续方案参考，但未创建 Android 工程或交付原生安装包；当前自有 PWA 公开壳的浏览器验证不等于真机安装通过。

## 建议 Gemini 保持的职责分层

这是后续 UI 的职责建议，不是新增 SDK API，也不要求使用特定状态库：

1. **页面/组件：** 会话列表、消息展示、输入、状态与恢复入口；仅派生展示，不直接拼模型请求。
2. **会话状态层：** 按用户和会话隔离草稿、分页、Message 副本、待确认发送及 Run 视图；避免所有逻辑堆进单个大 Store。
3. **Run 订阅控制器：** 管理本地连接状态、AbortController、`lastAppliedEventId`、有限退避与快照对账。与服务端 `running/completed/failed/cancelled` 状态分开，断线不把 Run 直接改成 failed。
4. **现有 SDK：** REST/SSE 传输和错误类型；不复制到 UI、不改成直接调用模型。

单个会话同一 Run 只保留一个有效的本地订阅归并者；切页后忽略旧订阅的迟到事件。登出/切换用户时终止本地订阅并清理旧用户缓存，不自动取消服务端任务。

### Allira 特有的恢复陷阱

- `clientMessageId` 在一次逻辑发送开始时生成，UUID 与**原文本及 mode**绑定。POST 结果不确定时复用它们，不悄悄 trim/修改文本或模式再重试，也不能换 UUID 冒充重连。
- `lastAppliedEventId` 只在事件成功更新应用状态后推进；终态完整快照替换草稿，不能追加第二次正文。
- 消息列表可能已有部分 generating 正文，却没有对应事件游标。此时若从 `after=0` 重放，必须清空该 Run 的本地累积并重建，不能把回放 delta 叠加在已有阶段正文上；只有正文与游标配套保存时才增量续传。
- 断线恢复重订阅同一 Run，不重新创建 Run。已确认终态不再订阅；若请求的 after 已是终态 ID，用 GET Run 和消息列表对账。
- “停止生成”调用 `cancelRun`；页面卸载只 Abort 本地 Fetch。不能把 DeepChat 的请求级取消逻辑等同于 Allira 的服务端任务取消。
- 会话列表仍按 UUID 稳定分页，不是更新时间倒序；当前已提供 messages/recent 向前加载接口，页内 sequence 升序、用 before 前插，与普通 messages 的 cursor 不能混用。

## 后续 UI 最小验证

除 [Gemini 交接验收](gemini-handoff.md#ui-验收清单) 外，至少验证：草稿切会话隔离；同一 Run 重放不重复文本；页面离开不取消后台生成；停止后使用实际终态；断线提示与 Run 失败提示可区分；刷新后从服务端重建状态；流式半截 Markdown 和危险链接安全降级。

这是最初研究提出的验收要求；当前 Allira 自有客户端实现与浏览器实测见 [验收记录](mvp-acceptance.md)。外部 DeepChat 仅做静态分析，未安装/构建/运行，不据此宣称其移动性能或安全性已经通过验证。

## 许可证与复用

该固定版本的 [LICENSE](https://github.com/YanJiuWeiYuanHui/DeepChat/blob/2037289815cd40868d93d5b754a760f4eeaf28b2/LICENSE) 为 MIT，声明 `Copyright (c) 2026 DeepSeek`。如果将来复制或修改其中代码、样式或实质性片段，必须保留其版权和许可声明，并复核对应素材及第三方依赖的许可；AlliraAi 自身的授权不会因“参考了 MIT 项目”自动变成 MIT。

本次仅记录出处和架构/交互建议，没有导入第三方源码或素材。外部 README 的未来规划或功能宣称不作为 AlliraAi 已实现能力的证据。
