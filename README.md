# AlliraAi

移动端优先的 AI 陪伴聊天项目。当前阶段先搭建**与 UI 框架解耦的客户端数据层和聊天后端**，界面交给 Gemini；暂不实现成本、计费和充值。

## 当前可用

- 框架无关 TypeScript Fetch SDK：Bearer、分页、创建生成任务、SSE、显式断线续传、取消。
- FastAPI 后端：开发会话、用户数据隔离、对话与消息、UUID 幂等发送、单对话单任务、可回放事件。
- SQLite 持久化：消息、Run 和事件共同提交；退出/重启有明确失败状态，不会把半截回复假装成完整答案。
- Mock 模型及 OpenAI 兼容流适配器；默认 Mock，不需要配置密钥，也不会产生模型调用费用。

这不是已完成的商业 MVP：正式注册登录、聊天导入、长期记忆、线路切换、CDK、支付、生产风控、PWA 和 UI 都尚未实现。

## 开发方式：服务器承担运行与构建

按用户要求，依赖安装、测试、编译和开发服务均在 SSH 主机 `hklite-lv4` 上执行。本地只保留轻量源码与文档，不安装依赖，不同步远端虚拟环境、node_modules、构建产物、数据库或凭据。

远端工作区：`/opt/allira/workspace`。运行用户：`allira`。开发 API 只监听 `127.0.0.1:8000`，使用 SSH 隧道访问；不要为了预览改成公网监听。

```sh
# 在服务器上，由 SSH 管理用户执行；不是在笔记本执行
runuser -u allira -- sh -c '
  cd /opt/allira/workspace/server
  /opt/allira/tools/uv-bin/uv sync --locked --extra dev --python /usr/bin/python3
  .venv/bin/pytest -q
  .venv/bin/ruff check allira tests
'
runuser -u allira -- sh -c '
  cd /opt/allira/workspace/packages/client
  export PATH=/opt/allira/tools/node24/bin:$PATH
  npm ci --ignore-scripts
  npm test
'
```

开发 API 的受限 systemd 模板见 [ops/allira-dev.service](ops/allira-dev.service)，操作、GitHub SSH 与隧道说明见 [远程开发文档](docs/remote-development.md)。代码托管目标为 [GUYU2233/AlliraAi](https://github.com/GUYU2233/AlliraAi)；仅提交源码、锁文件与文档，运行数据和凭据不进入仓库。

## 文档入口

- [架构与边界](docs/architecture.md)：分层、状态机、持久化、当前限制与后续扩展顺序。
- [API 与 SSE 契约](docs/api.md)：端点、分页、幂等、恢复语义和错误码。
- [Gemini 前端交接](docs/gemini-handoff.md)：可以直接交给前端实现者的约束与验收清单。
- [远程开发](docs/remote-development.md)：服务器工具、路径、测试、SSH 隧道、安全边界、备份。
- [OpenAPI 快照](docs/openapi.json)：从后端生成，包含仅开发模式可用的会话端点；运行时 `/openapi.json` 是当前配置下的实际路由。
- [SDK 用法](packages/client/README.md)：完整方法、流式示例和错误处理。

## 上游配置

配置只保留在服务器受保护的环境中，参考 [server/.env.example](server/.env.example)。前端和 SDK 不接收上游 Key、模型基址或用户自选模型参数。不要把任何凭据粘贴到聊天或提交到源码。

默认 `ALLIRA_ALLOW_DEV_AUTH=false`，启用时任何能访问接口的人都能以任意开发用户登录；**绝不能开放到公网**。`production` 环境禁止开发登录，但当前没有正式登录入口，因此不能通过改一个环境变量就当作生产可用。
