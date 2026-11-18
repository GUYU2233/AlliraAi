# AlliraAi 架构：第一阶段聊天底座

## 决策与范围

先让 App 与后端之间的协议稳定，再由 Gemini 设计界面。App 当前指**可被移动 Web/PWA 等客户端使用的数据层**，不是已经实现的原生安装包。客户端没有绑定 React/Vue/Next，也不预设视觉风格。

当前实现 TypeScript ESM Fetch SDK + Python FastAPI + SQLite。原因是 SDK 不锁死前端选型，FastAPI 提供 OpenAPI，SQLite 让单机早期开发不依赖额外数据库服务。依赖、测试和编译全部在香港服务器执行，笔记本只保留源码镜像。

```text
Gemini 负责的 UI（未实现）
          │ 调用 SDK，不直连上游
packages/client（TypeScript / Fetch / SSE）
          │ /api/v1，Bearer，camelCase
FastAPI 路由 + 用户归属校验
          ├─ Database：sessions / conversations / messages / runs / run_events
          └─ RunRuntime：脱离 SSE 订阅连接的任务生命周期
                    │ ModelProvider
                    ├─ MockProvider（默认）
                    └─ OpenAICompatibleProvider（可配置的服务端上游）
```

## 源码职责

- [app.py](../server/allira/app.py)：应用生命周期、请求鉴权、REST、SSE 和脱敏错误。
- [db.py](../server/allira/db.py)：SQLite 建表、归属校验、事务、幂等、事件、Prompt 装配。
- [runtime.py](../server/allira/runtime.py)：模型生成任务、超时、取消、输出上限、退出收尾。
- [providers.py](../server/allira/providers.py)：Mock/OpenAI 兼容流；HTTP 客户端归应用所有。
- [schemas.py](../server/allira/schemas.py)：严格输入和输出 DTO，生成 OpenAPI。
- [config.py](../server/allira/config.py)：仅服务端读取的环境变量和配置约束。
- [SDK](../packages/client/src/index.ts)：HTTP 方法、Token 注入、Fetch SSE 解析，不负责 UI 状态管理。

## 数据模型与不变量

`sessions` 保存随机 Bearer 的 SHA-256 摘要、开发用户标识、过期时间；不保存明文 Token。开发登录本身**不验证身份**，生产账号系统尚未做。

`conversations` 以 `ownerId` 隔离；`instructions` 仅是本会话的系统设定，不是角色库或长期记忆。所有读取、发消息、取事件和取消任务都先校验归属，他人资源与不存在资源均返回 404。

`runs` 代表一次逻辑发送，`(conversationId, clientMessageId)` 唯一。客户端消息 UUID 相同且文本逐字符相同时返回同一 Run；文本不同为 409。新消息遇到本会话正在生成则 409，数据库部分唯一索引兜底。同一 UUID 在不同会话不共享幂等范围。

`messages` 中一次发送原子产生两条记录：user 为 completed，assistant 从 generating 开始。`sequence` 在会话内递增，历史按它排序。

`run_events` 保存每个 Run 从 1 开始的顺序事件。每次 delta 将消息追加与事件一起提交；终态将 Run、assistant 状态和完整终态事件一起提交。终态不可再覆盖，取消已结束任务是幂等读取。事件与正文目前重复存储，以换取简单可靠的恢复；尚无事件保留期/清理策略，不适合无限增长的正式流量。

```text
Run:      running ──→ completed | failed | cancelled
Assistant: generating ──→ completed | failed | cancelled
```

任务错误码：`upstream_error`、`upstream_timeout`、`output_too_large`、`internal_error`、`server_shutdown`、`server_restarted`。failed/cancelled 保留已有正文，但下次 Prompt 不包含这对失败/取消消息。

## 流式协议与恢复

生成任务在创建 Run 后启动，由 RunRuntime 持有；**订阅连接断开不取消生成**。用户明确停止生成要调用取消端点，不是只 Abort Fetch。任务总超时默认 180 秒，HTTP 上游连接超时 10 秒/读写等超时 60 秒。

订阅端从 SQLite 按 `after` 游标追赶，轮询间隔 50ms，空闲约 10 秒输出 heartbeat 注释。每次最多读 100 条，持续排空直至读到最新终态；不是每用户常驻线程。SSE 响应禁用代理缓冲，未来反代也必须正确配置流超时。

`run.started`、`message.delta`、`run.completed/failed/cancelled`。终态载荷含完整 Run 和 assistant Message，UI 必须用它替换累计草稿，不能只凭 EOF 判完成。SDK 不偷偷重发 POST 或重连；应用按自己**已处理成功的事件 ID**恢复，以避免重复拼接。

若客户端已经处理过终态，不应再次订阅。若以已到达终态 ID 的 `after` 请求，服务端没有新事件，返回空流；SDK 会抛未确认结束。此时用 GET Run + 消息列表对账，不应自动新建任务。

上游适配器只产生公开文本 `delta.content`，忽略 reasoning/role/usage；校验 UTF-8、JSON、结束标记和 SSE 分片，禁止跟随上游重定向，错误不回传原始响应、请求 URL 或 Key。当前不支持工具调用、多模态、上游用量入账或复杂路由，不能用计费场景验证它。

## 上下文策略：明确不是长期记忆

目前 Prompt 是 `会话 instructions（如有）+ 本会话所有已 completed 的 user/assistant 对 + 当前 user 输入`。不生成摘要、不做向量检索、不静默删历史。总字符数超过默认 120000 直接返回 413，不产生 Run；字符数不是精确 Token 计数，真实模型可能有更小的上下文限制。输出默认最多 60000 字符，超过后失败且保留此前片段。

这只是安全护栏，不是成本系统，不承诺任何节省比例。后续独立抽出 ContextBuilder，再实现 `核心人设 + 核心记忆 + 摘要 + 最近窗口`；导入数据应保留原文溯源，不能把未经确认的摘要直接当不可变事实。

## 部署约束与已知欠账

**严格单 API 进程/单 Uvicorn worker。** SQLite 同步短事务在事件循环执行，任务表在进程内；WAL 不意味着可启动多 worker。第二进程的重启恢复会错误终结第一进程任务；当前没有跨进程排他锁，应通过单 systemd 服务操作，不同时启动第二实例，也不使用热重载。启动时旧 running 被标记 failed/server_restarted；正常退出用 failed/server_shutdown。

数据库当前 `CREATE TABLE IF NOT EXISTS`，还没有版本化迁移。生产/多人并发前需要 PostgreSQL、迁移机制、队列与 Worker、持久消息通知、多进程所有权和并发/速率限制。

正式上线前还必须完成：真实注册登录/撤销与恢复、TLS 与反代、内容/数据保护、请求体与上游帧限额、全局/每用户并发限制、运维监控告警、备份恢复演练、会话/账号删除及留存策略。当前的长度限制不是全面抗滥用机制，开发服务不能公开。

## 后续接入顺序

1. Gemini 基于现有 SDK 完成对话列表、消息页、流式状态、停止/恢复等 UI；不要提前展示不存在的充值/长期记忆功能。
2. 正式身份系统与数据库迁移、备份、必要限流，准备受控内测环境。
3. 聊天导入与可核验的长期记忆/ContextBuilder；与真实中转站做稳定性测试。
4. 最后再按实际成本设计双线路、额度、CDK 和商业风控；没有先做报价或省钱承诺。
