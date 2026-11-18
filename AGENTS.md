# AlliraAi 开发约束

## 远程优先（用户明确要求）

笔记本性能与磁盘有限，**所有依赖安装、测试、lint、编译与开发服务都在 SSH 主机 `hklite-lv4` 执行**。本地只做轻量源码/文档编辑、查看和源码同步；不得在本地运行 npm install/ci、npx、uv sync、pytest、tsc 或开发服务。

- 远端主工作区：`/opt/allira/workspace`；执行项目命令使用低权限 `allira` 用户。
- Python：`/opt/allira/workspace/server/.venv/bin/python`；uv：`/opt/allira/tools/uv-bin/uv`。
- Node：`/opt/allira/tools/node24/bin/node`；npm 命令先把该 bin 目录加入 PATH。
- 操作细节与命令见 [远程开发文档](docs/remote-development.md)。
- 同步前确认两端改动；只同步指定源码/锁文件/文档，不覆盖其他代理正在编辑的范围。测试前确保远端包含最新源码，修复后再同步回轻量本地镜像。
- 不回传或同步 `.venv`、`node_modules`、`dist`、缓存、日志、数据库（含 WAL/SHM）、`.env`、服务器环境文件或凭据。

## 当前任务边界

当前只交付框架无关 TypeScript SDK 与 FastAPI 文本聊天底座，界面由 Gemini 后续设计。不要擅自引入 UI 框架或宣称正式登录、长期记忆、导入、计费/CDK、PWA 已实现。参见 [架构](docs/architecture.md) 与 [Gemini 交接](docs/gemini-handoff.md)。

## 运行与安全

- 开发 API 只监听服务器 `127.0.0.1:8000`，经 SSH 隧道访问；不擅自开放公网端口、改防火墙、域名或 SSH 登录配置。
- 当前开发登录允许任意开发用户身份，不可暴露公网；production 环境必须关闭，但正式账号系统尚未实现。
- 只允许单 API 进程、单 Uvicorn worker；不使用热重载或同时启动第二实例。使用现有 `allira-dev` systemd 服务管理，改源码后明确重启。
- 默认 Mock。没有用户授权和受保护服务端配置时，不调用真实上游，不索取或输出 Key/Token。
- 所有业务资源按用户隔离；幂等按 `(conversationId, clientMessageId)`；SSE 断开不取消生成，只有显式 cancel 停止；终态事件为准。

## 验证

在服务器运行后端 pytest/Ruff 与 SDK npm test（包含 TypeScript build）；必要时执行 Mock 专用 [真实 HTTP/SSE 冒烟](scripts/smoke.mjs)。API 变化后使用 [导出脚本](scripts/export_openapi.py) 更新 OpenAPI、SDK、文档与测试。不得将仅静态阅读、Mock 或模拟上游测试表述为真实模型/生产容量验证。
