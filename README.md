# AlliraAi

移动端优先的 AI 陪伴聊天项目。当前采用 FastAPI/SQLite 业务服务端、框架无关 TypeScript SDK、原生 TypeScript 功能客户端和 **Vite + Vue 3 + Element Plus 独立管理前端**；认证已接入 **Logto OSS 1.46.0 + PostgreSQL，通过 Authlib/OIDC 验证**，不再将自研手机／社交认证作为默认核心。登录后默认打开聊天，点击“新聊天”即可开始，不要求先配置模型、角色或记忆。三种中性风格、经济模式与渐进展开的高级工具保持，正式视觉交给 Gemini。普通聊天与 Logto 仍是回环内测环境，仅管理后台经用户授权的 Cloudflare Access 域名发布；真实短信／微信／QQ 未配置时普通登录通道关闭。真实本机 Logto + 官方模拟短信/社交的完整链已通过：新手机注册、三类旧账号保留 owner/历史/能量迁移，以及新鲜双 CSRF 的本地业务删除；这不等于真实短信/腾讯渠道验收。当前边界见[身份设计](docs/identity-design.md)、[Access 后台部署](docs/access-admin-acceptance.md)与[API 0.7 开源认证历史验收](docs/oidc-admin-acceptance.md)；[旧版身份验收](docs/identity-acceptance.md)、[上下文验收](docs/context-companion-acceptance.md)与[可持续内测验收](docs/usability-acceptance.md)仅保留各历史阶段证据。

## 功能范围

- **开源托管认证与隔离**：Logto 负责手机号验证、托管登录页、社交登录后的必需手机号补全及 OIDC 签发；AlliraAi 通过 Authlib/joserfc 验证标准身份，明确确认后建立业务账号映射。旧账号通过新 OIDC 身份和显式原账号证明迁移，保留内部 ID、聊天与能量；无密码旧手机账号使用专属新鲜认证与明确迁移确认。默认 OIDC 模式在服务端禁用旧手机／社交／用户名登录和恢复入口，不双开认证核心。普通 Bearer、普通 Cookie、OIDC 过渡 Cookie 与独立管理 Cookie 不互授权限。真实通道未就绪时保持禁用；实际开源服务整链与模拟渠道、真实渠道分别记录，不能互相替代。
- **聊天**：最近页/旧页、按本人标题与正文检索、JSON/Markdown 导出、到完整对话对的幂等分叉、受限安全 Markdown 和代码复制。严格 UUID+原文+模式幂等，SSE 有界 GET 重连/回放；显式取消、超时与重启恢复，断开订阅不取消后台生成。
- **开箱即聊**：服务端三种陪伴风格与默认经济模式；幂等创建空聊天不调用模型、不扣能量，开始建议是页面提示而非伪造的 AI 回复。用户决定称呼和关系边界，不默认恋爱或独占关系。
- **角色、记忆与上下文**：角色库、核心记忆/关系、手工确认的事实/偏好/边界/关系卡，可启停和修改；历史摘录块绑定完整对话来源，预算仅限制视图、不删除原文。实际上下文预览复用发送规划器且回滚业务写入，不调用模型或预扣能量；规则摘录不等于可靠语义记忆。
- **历史迁移**：JSON、Markdown、文本预览与原子导入，保留原始来源。只把真实相邻 user→assistant 对写入聊天时间线，孤立/不支持记录保留在来源中，不伪造回复。
- **能量与管理**：整数测试能量、预扣/结算/失败取消退款、路由回退差额退款、CDK 单次核销和撤销、用户与任务查询、显式聊天读取及审计。后台增加字面搜索/身份筛选、脱敏账号与已有钱包、点击才读的设备分页、逐设备/全部本地撤销、纯配置路由及分开的 SQLite/Logto PG 诊断；不等同上游全局 SSO 管理。不是支付系统，也不是上游 Token 计费。
- **功能客户端/PWA 与后台入口**：`/test/` 仍经 SSH 隧道访问，以聊天为默认入口，设置与高级工具渐进展开。管理后台改为 [dsah.guyufl.cc.cd/admin/](https://dsah.guyufl.cc.cd/admin/)，由 Cloudflare Access、专用 Tunnel 及源站逐请求 JWT 校验保护，独立管理 Cookie+CSRF 保留；旧一次性链接已关闭。该域名不发布普通聊天、业务登录/API、Logto 或数据库。PWA 仍只缓存公开壳，不缓存 API 或管理内容。实际 Access 登录与使用按用户要求由用户自行测试，见[本轮部署记录](docs/access-admin-acceptance.md)。
- **运行维护**：公开轻量 readiness、后台非敏感诊断、每日同机 SQLite 一致性快照（最近 7 份）、校验和/完整性验证与仅向全新离线副本恢复的演练。保留历史备份不等于物理擦除，也不是异机灾备。

代码默认 Mock。指定测试中转可由服务器受保护配置启用；自动化回归必须使用 Mock，少量真实中转验收单独执行。未验证真实线路速度优势、渠道报价、Token 成本、生产容量、iOS/Android 安装体验或法律合规。

## 访问与开发

全部依赖安装、lint、测试、编译和服务均在 SSH 主机 `hklite-lv4` 上以 `allira` 用户执行。本地只留轻量源码与文档，不回传依赖、构建物、数据库或环境凭据。

远端工作区 `/opt/allira/workspace`；单个现有 `allira-dev` 服务只监听 `127.0.0.1:8000`，不使用热重载或第二 worker。通过 SSH 隧道转发后访问：

- 测试客户端：`http://127.0.0.1:8000/test/`
- 管理页面：[https://dsah.guyufl.cc.cd/admin/](https://dsah.guyufl.cc.cd/admin/)，使用 Cloudflare Access；仅允许已确认的管理邮箱，普通账号和旧 CLI 链接无权登录。回环地址也不能绕过 Access。
- 接口文档：`http://127.0.0.1:8000/docs`

隧道、构建、Mock/真实中转切换和管理运维见 [远程开发](docs/remote-development.md)及[专用 Tunnel](ops/cloudflare/README.md)。只有明确授权的管理域名/路径经 Access 发布；开发登录、普通业务和身份服务仍不能公开。此部署不等于普通用户网站或 production 全面上线，不能仅改环境变量绕过门禁。

```sh
# 以下均在服务器管理员 SSH 会话执行，不在笔记本运行。
runuser -u allira -- sh -c '
  cd /opt/allira/workspace/server
  /opt/allira/tools/uv-bin/uv sync --locked --extra dev --python /usr/bin/python3
  .venv/bin/ruff check allira tests ../scripts/export_openapi.py ../scripts/restore_drill.py
  .venv/bin/pytest -q
'
runuser -u allira -- sh -c '
  export PATH=/opt/allira/tools/node24/bin:$PATH
  cd /opt/allira/workspace/packages/client && npm ci --ignore-scripts && npm test
  cd /opt/allira/workspace/apps/dev-console && npm ci --ignore-scripts && npm test
'
```

## 文档入口

- [架构与边界](docs/architecture.md)：账号、生成、记忆、导入、路由、事务与留存策略。
- [API 与 SSE 契约](docs/api.md)：端点、DTO、游标、幂等、认证与错误码。
- [Gemini 前端交接](docs/gemini-handoff.md)：在已存在功能上进行正式设计，保持状态与安全语义。
- [Vite 管理前端](apps/admin-console/README.md)：Vue 3/Element Plus 后台、构建与现有 Access 入口接入。
- [测试控制台说明](apps/dev-console/README.md)：原生 TypeScript 普通客户端、PWA 与历史浏览器测试。
- [远程开发](docs/remote-development.md)：路径、构建、服务、管理登录、受保护中转配置、备份和 GitHub。
- [OpenAPI 快照](docs/openapi.json)：由后端导出；运行时 `/openapi.json` 对应实际配置启用的路由。
- [SDK 用法](packages/client/README.md)：完整类型和调用；不要在 UI 重写一套协议。
- [DeepChat 参考](docs/client-reference-deepchat.md)：固定版本的机制参考，没有复制第三方源码或引入其框架。
- [手机号与社交身份设计](docs/identity-design.md)：开源方案、手机号数据库模型、绑定状态机、许可及真实渠道条件。
- [当前 Access 后台部署记录](docs/access-admin-acceptance.md)：API 0.8/schema8、管理域名、旧链接关闭、隔离门禁与非登录部署检查；实际登录由用户测试。
- [Logto 与后台历史验收](docs/oidc-admin-acceptance.md)：API 0.7 真实 Logto、旧账号迁移、旧管理登录及分阶段安全证据，不代替 Access 登录验收。
- [旧版身份验收](docs/identity-acceptance.md)：API 0.6 自研实现的历史门禁；不作为当前 Logto/OIDC 的验收证明。
- [上下文与简单客户端历史验收](docs/context-companion-acceptance.md)：API 0.5 的来源记忆、只读预览、默认预设和实际门禁。
- [上下文与陪伴设计](docs/context-companion-design.md)：billion-context、两套 Soulweave 的固定版本取舍与许可。
- [可持续内测历史验收](docs/usability-acceptance.md)：API 0.4 的账号、数据控制与备份门禁。
- [社区参考与许可](docs/community-references.md)：固定版本、实际依赖、借鉴/拒绝的机制；未安装 DSH 插件。
- [实际第三方依赖声明](docs/third-party-notices.md)：Logto、Authlib/joserfc、PostgreSQL 与传递依赖的许可及定制边界。
- [MVP 历史验收](docs/mvp-acceptance.md)：上一阶段证据，不代表当前全部能力。

## 上游与敏感数据

[环境示例](server/.env.example) 不含真实凭据。模型基址、模型名、Key 与输出 Token 上限由服务端配置，前端只选择 `economy/fast` 业务模式。不要将环境文件、Token、密码、CDK 明码或聊天数据写入日志、提交仓库或回传本地。

代码托管目标为 [GUYU2233/AlliraAi](https://github.com/GUYU2233/AlliraAi)。服务器工作区中的新改动不等于已经推送；发布状态以 Git 提交和远端核验为准。
