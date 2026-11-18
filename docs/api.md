# API v1 与 SSE 契约

API 前缀 `/api/v1`，JSON 字段使用 camelCase，时间为 UTC ISO 8601 字符串（后缀 `Z`）。详见 [OpenAPI 快照](openapi.json) 与 [TypeScript DTO](../packages/client/src/index.ts)。成功 JSON 在 SDK 中仅做类型声明，不是运行时 schema 校验。

除健康检查、OpenAPI/文档和显式启用的开发会话外，业务请求必须使用 `Authorization: Bearer <accessToken>`。不能把 Token 放 URL 查询参数。`userId` 不由受保护的业务接口接受，用户身份来自服务端会话。

## REST 端点

| 方法 | 路径（省略 /api/v1） | 输入/结果 |
| --- | --- | --- |
| POST | /dev/session | 开发专用 `{userId}` → 200 `{accessToken,tokenType:"Bearer"}` |
| POST | /conversations | `{title?,instructions?:string|null}` → 201 Conversation |
| GET | /conversations | `cursor?&limit?` → 200 Page<Conversation> |
| GET | /conversations/{id} | → 200 Conversation |
| GET | /conversations/{id}/messages | `cursor?&limit?` → 200 Page<Message> |
| POST | /conversations/{id}/runs | `{clientMessageId:UUID,content}` → 202 Run |
| GET | /runs/{id} | → 200 Run |
| POST | /runs/{id}/cancel | 无请求体 → 200 Run |
| GET | /runs/{id}/events | `after?:number` → 200 text/event-stream |

健康检查是根路径 `GET /healthz` → `{status:"ok"}`；运行时契约 `GET /openapi.json`，交互文档 `/docs`。

## DTO

```ts
type Conversation = {
  id: string; title: string; instructions: string | null;
  createdAt: string; updatedAt: string;
};
type Message = {
  id: string; conversationId: string; runId: string; sequence: number;
  role: 'user' | 'assistant'; content: string;
  status: 'generating' | 'completed' | 'failed' | 'cancelled'; createdAt: string;
};
type Run = {
  id: string; conversationId: string; clientMessageId: string;
  userMessageId: string; assistantMessageId: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  errorCode: string | null; createdAt: string; updatedAt: string;
};
type Page<T> = { items: T[]; nextCursor: string | null };
```

开发 `userId` 为 1–100 位英文字母/数字/下划线/连字符。默认新会话标题“新对话”，标题去首尾空白且 1–200 字符，instructions 可空且最多 20000 字符。content 必须非空白且最多 20000 字符，保留原始空白以支持严格幂等。多余输入字段一律拒绝。

列表 `limit` 默认 50、范围 1–100；`nextCursor=null` 表示当前页后没有数据。会话以稳定的 UUID 字典顺序分页，**不是最近更新时间排序**；消息以 sequence 升序，游标为最后一条 Message 的 id。把 cursor 当不透明值，不自行计算。未知/他人 cursor 返回 400 invalid_cursor；cursor 仅在对应的列表作用域内有效。当前没有删除资源接口。

## 发送与幂等

一次点击发送先生成 UUID（如 `crypto.randomUUID()`），只生成一次。网络不确定时可按原 UUID+原文本重试 POST，返回同一 Run，不创建第二对消息/第二次模型调用。相同 UUID 修改文本为 409 idempotency_conflict。相同会话中不同 UUID 遇到 running 则为 409 run_in_progress。

POST 返回不保证当前任务仍 running，幂等重试可能读到已结束 Run。不要将“重新订阅事件”实现为“再 POST 一条消息”。失败/取消后如果用户确实选择重新生成，应创建新的 UUID；这是新一轮发送，旧片段仍保留在历史。

## SSE 帧与终态

使用 **Fetch** 订阅以便附带 Bearer，不使用不能正常设置该头的原生 EventSource。示例：

```text
id: 1
event: run.started
data: {"run":{...}}

id: 2
event: message.delta
data: {"messageId":"...","delta":"你好"}

id: 3
event: run.completed
data: {"run":{...},"message":{...}}

```

终态可为 run.completed、run.failed 或 run.cancelled，payload 都是 `{run,message}`，message 为完整 assistant 快照。delta 为追加文本，不是完整替换。失败/取消也是正常送达的终态事件；与 HTTP/网络错误不同。

每个 Run 独立编号，自 1 开始递增。`after=0` 为全量回放，`after=N` 只取 id>N。上界为 JS 安全整数，不能超过当前 Run 已存在的最大事件 ID，否则 400。未来事件等待通过正常保持连接实现，不通过预填未来 after 实现。仅支持显式 query after，传 Last-Event-ID 头会返回 400，以避免双游标含糊。

心跳注释 `: connected` / `: heartbeat` 可忽略。响应使用 `Cache-Control: no-cache, no-transform` 和 `X-Accel-Buffering: no`。客户端必须正确跨网络分片解码 UTF-8 和 CRLF，建议直接使用 SDK。

## 恢复和取消

记录**应用已成功处理**的最后 event.id；恢复时用同一个 run.id 与 after。SDK 抛 StreamInterruptedError 表示未收到终态就 EOF，lastEventId 是解析器读取到的位置，不一定等于应用成功处理位置；网络错误与 Abort 保留原异常。不做自动重连或 POST 重试，前端决定重连时机、退避上限与用户提示。

处理完终态后结束订阅，不再恢复。若进程重载后只保留了终态 ID、再请求得到空流，用 GET Run 和消息列表对账，不能无限重连。终态快照是最终准确信息，不要依赖累计 delta 的草稿或 EOF。

AbortController 只断开当前事件连接；后台任务继续。真正停止生成需要 POST /runs/{id}/cancel。取消与模型刚好结束竞争时，先落库的终态获胜；对已结束任务取消返回原终态，不把 completed 改成 cancelled。

## 错误

HTTP 错误统一：

```json
{"error":{"code":"run_in_progress","message":"This conversation has an active run."}}
```

- 401 unauthorized：缺失、无效或过期的开发会话；当前未提供自动刷新。
- 404 not_found：资源不存在或不属于当前用户。
- 409 idempotency_conflict / run_in_progress：重复 UUID 内容冲突/同会话生成中。
- 400 invalid_cursor：游标不属于作用域、超前或使用 Last-Event-ID。
- 413 context_too_large：上下文字符安全上限触发；尚无自动摘要，不产生新 Run。
- 422 validation_error：UUID、空白、长度、额外字段、分页参数等不合法；不反射原输入。
- 500 internal_error：服务端异常，响应不暴露内部或上游细节。

框架未识别的路径/方法错误也遵循 envelope，code 为 http_error。上游失败通常在创建成功之后发生，因此表现为 Run failed + run.failed 事件，而不是把初始 POST 改成 500；具体 Run.errorCode 见 [架构文档](architecture.md)。

## 契约演进

保持 v1 additive 优先；破坏性变化使用新版本前缀并升级 SDK。改 DTO/端点时在服务器运行 [OpenAPI 导出脚本](../scripts/export_openapi.py)，同步快照、SDK 和测试。SSE 具体 payload 以此文档及 SDK 联合契约为准，OpenAPI 的 text/event-stream 是字符串描述，不能独立生成完整事件类型。
