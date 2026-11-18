# 交给 Gemini：App 界面与数据层接入

## 可直接转交的任务说明

> 为 AlliraAi 设计并实现移动端优先的 AI 陪伴聊天 UI。不要重写后端或 SDK，不要在前端直连模型中转站，也不要把上游 Key/模型参数做成当前用户设置。可以自主选择前端框架与视觉风格；当前项目尚无 UI 框架、路由或设计系统。依赖安装、编译、测试与预览服务全部在香港服务器进行，不在笔记本安装依赖。先实现下列真实存在的聊天闭环，不实现充值、计费、CDK、聊天导入或长期记忆的假入口。

先阅读 [SDK 用法](../packages/client/README.md)、[API 契约](api.md)、[OpenAPI 快照](openapi.json) 和 [远程开发约束](remote-development.md)。后端当前只支持文本聊天，默认返回 Mock response.；这表示联调成功，不表示真实模型已接入。

## 功能边界

当前可以做：开发用户登录面板（仅本地开发）、新建/浏览会话、会话内消息分页、流式回复、明确的生成/失败/取消状态、停止生成、断线恢复、重载后对账。`instructions` 可以作为新建会话时的设定输入，但不要宣称它是已实现的角色管理或长期记忆。

不做：正式账号注册、公开可用登录、角色广场、导入、总结/记忆编辑、模型线路切换、充值/余额/套餐/CDK、语音/图片/附件、消息编辑/删除/分支。这些后端能力还不存在；可在设计方案中标注后续，但不能让页面假装调用成功。

## 连接方式

SDK 源码在 [packages/client](../packages/client/src/index.ts)，ESM、Fetch，不依赖 React/Vue。前端可以用包管理工作区接入，或由构建系统把模块源码纳入构建；不要手动复制一份 SDK 到 UI 中形成双份协议。具体 workspace 配置待框架选定后创建。

```ts
import { AlliraClient } from '@allira/client';

// 初始化后仍使用 token getter，便于开发登录后读取最新 Token。
let accessToken: string | undefined;
const client = new AlliraClient({
  baseUrl: 'http://127.0.0.1:8000', // 浏览器看到的 SSH 转发地址，不是服务器公网 API
  accessToken: () => accessToken,
});
const session = await client.createDevSession({ userId: 'gemini-demo' });
accessToken = session.accessToken;
const conversation = await client.createConversation({ title: '新对话' });
```

示例 Token 只在内存；正式 Token 保存/刷新策略必须等真实身份系统设计，不能把示例当生产安全方案。开发 userId 可为字母、数字、下划线、连字符，最长 100。

前端浏览器通常经 SSH 隧道访问本地 5173，API 经另一条隧道访问本地 8000；默认 CORS 允许这两个 loopback 5173 源。也可采用前端 `/api` 开发代理。**不要把 API 监听改成 0.0.0.0，也不要把服务器内网/回环地址当作用户浏览器可直接访问的公网地址。**

## 发送状态机

1. 用户点击发送时生成一个 `clientMessageId=crypto.randomUUID()`，保存原文本并禁用本会话重复发送。
2. `createRun` 返回后，以服务端 userMessageId / assistantMessageId 替换本地占位项，避免切页/幂等重试时重复消息。POST 网络结果不确定时只可使用**同一个 UUID 与原文本**重试。
3. 通过 `streamRunEvents(run.id,{after,signal})` 读取事件。message.delta 仅追加到对应 messageId，成功更新 UI 后才推进应用游标。
4. run.completed / failed / cancelled 都含完整 Message，必须用快照替换草稿并显示正确终态。失败/取消的片段可以保留展示，但不能当完整回答。
5. EOF/网络异常不是完成。保留 run.id 和应用已处理事件 ID，提示断线并提供有限退避的重连/手动恢复；不要自动创建第二个 Run。

```ts
const input = { clientMessageId: crypto.randomUUID(), content: draft };
const run = await client.createRun(conversation.id, input);
const controller = new AbortController();
let lastAppliedEventId = 0;
for await (const event of client.streamRunEvents(run.id, {
  after: lastAppliedEventId, signal: controller.signal,
})) {
  // applyEvent 必须先成功：按 id 去重、按 messageId 追加，终态用完整 message 覆盖。
  applyEvent(event);
  lastAppliedEventId = event.id;
}
```

`applyEvent`、draft 等是 UI 要实现的示意变量，不是 SDK 提供的状态管理函数。异常处理应围绕整个迭代实现；重连重新调用同一 streamRunEvents 并传 lastAppliedEventId。SDK 不自动重连。

### 停止与页面离开不同

- “停止生成”：调用 `cancelRun(run.id)`，按返回的实际终态更新 UI，再结束订阅。任务可能已经 completed，不能强行改显示为 cancelled。
- 页面离开/组件卸载：Abort 当前 Fetch 订阅即可；后台生成继续。不要把卸载默认当取消用户任务。
- 再次进入：加载消息列表；对 generating 消息用 runId 查状态。若仍 running 可从 0 回放并重建草稿，或在保留正确草稿/游标时续传；若已终态以服务端 Message 为准。

## 列表与错误

会话分页是稳定 UUID 顺序，不是更新时间倒序；前端可对已加载数据排序，但不能声称得到全局最近会话分页。消息按 sequence 升序，limit 默认 50/最多 100，要加载后续页才能拿到最新消息；列表中 generating 可能是生成中的阶段快照，终态再对账。

401 清理失效会话并要求重新开发登录；404 不展示他人资源信息；409 run_in_progress 跳回当前任务状态；409 idempotency_conflict 提示本地发送记录冲突，不能偷偷换 UUID 造成重复；413 提示当前上下文过长，不应假装已自动压缩；422 校验输入；500/网络问题保留待确认发送状态。上游生成失败通过 run.failed 表现，不只检查最初 POST 成功。

SDK 的 AlliraApiError 带 status/code；StreamInterruptedError 表示没有收到终态的 EOF，StreamProtocolError 表示事件协议异常。Abort 保留原始异常。不要直接将服务器英文 error.message 当最终用户界面文案；按 code 本地化，并避免输出 Token/原聊天到日志。

## UI 验收清单

- 手机屏幕可以创建会话、发送中文/emoji、看到逐步追加的回复与最终快照。
- 同一会话生成时不能再次发送；不同会话状态互不混淆，切页不重复 user 消息。
- 相同 UUID/文本在不确定网络后重试只出现一对消息；冲突会明确提示。
- 手动断开订阅再恢复后不重叠拼接、不丢终态；刷新页面能从服务端重建消息状态。
- 停止生成保留片段并展示 cancelled，完成后误按停止不改变 completed。
- loading、空列表、401/409/413、网络中断和生成失败均有明确状态，不以无限转圈掩盖。
- 不包含不可用的充值/长期记忆/导入假功能，不包含上游凭据、不公开开发登录。

默认 Mock 极快，不保证肉眼能看到打字机效果；可以在 UI 的本地视觉测试里模拟慢流，不要通过改真实协议或在服务端生产路径加假延迟实现演示。
