# 开源运行依赖与定制边界

本页说明 AlliraAi 实际采用的组件及维护义务，不把仅调研的项目写成已使用。依赖的完整许可随原安装包保留；分发部署包时须保留相应 LICENSE、NOTICE 和依赖自带声明，不能仅保留本页摘要。

## 身份服务与数据库

- **Logto OSS v1.46.0**，固定 commit `d179bf9d2d51104236a342b90c2841df66e4ba5b`，官方发行包 SHA256 `140ca60719d60866782527aeb4cf72caee6d3fba3c730b9214e0a43326a96382`，**MPL-2.0**。[固定源码与许可](https://github.com/logto-io/logto/tree/d179bf9d2d51104236a342b90c2841df66e4ba5b)。实际复用 hosted UI、OTP、官方连接器、OIDC Provider 与 Management API。保留 Powered by Logto，不使用付费去品牌、Bring your UI 或其它商业能力。普通 branding logo 是官方配置，不是去品牌补丁。
- 唯一上游代码修改：HTTP/HTTPS core/admin 四处 listen 增加显式 `127.0.0.1`。修改源码、原文件、发行 bundle、diff 和前后摘要由[固定版本准备器](../scripts/logto/prepare.py)保留，详见[运维交接](../ops/logto/README.md)。若对外分发修改后的 MPL 覆盖文件，须按 MPL 向接收者提供相应修改源码、许可及修改说明；其它依赖的许可不被 MPL 自动取代。
- **PostgreSQL 17.12**，使用 Debian `17.12-0+deb13u1` 二进制包私有提取，不修改数据库源码；[PostgreSQL License](https://www.postgresql.org/about/licence/)。原包 copyright/许可保留在服务器私有 runtime 中；libpq、ICU 等随包声明也保留。原生 pg_dump/pg_restore 复用现有工具，不把自写文件复制称为数据库备份协议。

## 后端 OIDC 库

直接声明及传递版本以[项目依赖](../server/pyproject.toml)和[锁文件](../server/uv.lock)为准。本次远端安装元数据核验如下：

- **Authlib 1.9.9**：BSD-3-Clause；[官方许可](https://docs.authlib.org/en/stable/community/licenses.html)。用于 OAuth Authorization Code、PKCE 与 OIDC claim 校验，商业支持不是使用开源库的前提。
- **joserfc 1.8.0**：BSD-3-Clause；[上游](https://github.com/authlib/joserfc)。用于标准 JWK/JWT 签名验证；没有手写 JWT 算法。
- **cryptography 50.0.2**：Apache-2.0 OR BSD-3-Clause；[上游许可](https://github.com/pyca/cryptography/tree/50.0.2)。保留安装包内完整声明和其原生传递依赖许可，不把本条当作原生库完整清单。
- **cffi 2.1.1**：MIT-0；[上游](https://github.com/python-cffi/cffi)。本条按该安装版本的 License-Expression 记录，不沿用旧版本许可猜测。
- **pycparser 3.12**：BSD-3-Clause；[上游](https://github.com/eliben/pycparser)。

AlliraAi 只编写业务身份映射、短期事务、CSRF、旧 owner 明确关联、业务会话及数据控制胶水，不重新实现上游 OTP/OAuth/JWT 认证核心。库版本升级必须重新核验协议、许可与真实服务兼容，不能只跑模拟发行方后宣称升级验收完成。

## 后台 Access 与 Tunnel

管理访问使用 Cloudflare 托管 Access/DNS/边缘服务，以及实际运行的官方 **cloudflared 2026.11.1**，不是自研隧道。固定 commit `518d7f7969b764df628bb4b1088f0f154901dc4e`，Linux amd64 官方二进制 SHA256 `97bb3b02d23a5980fc9c15a1a270e692da1923f103f920d5b16d4931d5ab55e7`，[Apache-2.0 许可](https://github.com/cloudflare/cloudflared/blob/518d7f7969b764df628bb4b1088f0f154901dc4e/LICENSE)。不修改上游 Tunnel/JWT 核心，保留发行许可；项目仅配置限定主机/路径、文件权限、固定版本及输出脱敏。源站复用现有 joserfc 验证 Access JWT，不手写密码学。Cloudflare 的托管服务本身不被描述为自托管开源软件，不承诺免费额度、SLA 或全部企业功能。运维与凭据边界见[专用 Tunnel](../ops/cloudflare/README.md)，部署证据见[Access 记录](access-admin-acceptance.md)。

## 管理前端与已有客户端依赖

管理前端实际采用 **Vite 8.3.8、Vue 3.5.45、Element Plus 2.14.8、@element-plus/icons-vue 2.3.2**（MIT），构建使用 @vitejs/plugin-vue 6.0.11、vue-tsc 3.3.14 和 TypeScript 5.9.3。精确依赖与传递版本见[管理项目锁文件](../apps/admin-console/package-lock.json)。[Vite 构建脚本](../apps/admin-console/build.mjs)将 45 个锁定运行依赖的完整许可声明保留到唯一管理 JS 与构建元数据中，不另公开第三个管理资产。lodash-unified 1.0.3 发布包未带独立许可文件，按包内声明的 MIT 和作者信息附上[标准 MIT 文本与说明](../apps/admin-console/third-party-lodash-unified.txt)。界面复用上游组件，AlliraAi 自行编写业务布局与 SDK 状态胶水。

普通客户端的 Markdown 渲染继续使用 markdown-it 15.0.3 及其传递依赖，七份完整许可随[原客户端构建脚本](../apps/dev-console/build.mjs)生成的公开 bundle 保留。详见[社区参考](community-references.md)。普通聊天/PWA 不因管理前端改造而迁移框架。

## 仅调研、未作为运行依赖

Casdoor、JustAuth、SMS4J 仅用于本轮选型，不是当前认证运行服务。不能将 Casdoor 根 Apache 许可覆盖到企业 edition，也不能将 JustAuth + SMS4J 两个适配库表述为完整 IAM；历史自研身份的测试结果不等于复用它们。参考版本和选择依据见[身份设计](identity-design.md)。

开源许可不代替微信/QQ 平台审核、UnionID 权限、短信资质、隐私或公开部署合规验收。当前真实连接器未配置，维护期官方 Mock 只证明本机开源系统的集成行为。
