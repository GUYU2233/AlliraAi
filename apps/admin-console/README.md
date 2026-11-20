# AlliraAi 管理控制台

采用 **Vite 8.3.8 + Vue 3.5.45 + Element Plus 2.14.8**，复用已有 TypeScript SDK 和管理 API。管理入口仍为 <https://dsah.guyufl.cc.cd/admin/>，通过 Cloudflare Access 登录；不接收旧 fragment/code，不修改普通聊天和 PWA。

## 界面

左侧分组导航、顶部身份栏、数据卡片、筛选表格和抽屉详情，适配窄屏。页面包括概览、用户与设备、会话与消息、生成任务、兑换码、能量用量、路由配置、健康诊断、管理审计。危险操作需显式确认，不自动重发请求。

## 远程构建

所有命令只在 `hklite-lv4` 的 `/opt/allira/workspace` 下以 `allira` 用户执行。先把 `/opt/allira/tools/node24/bin` 加入 PATH。

```sh
cd /opt/allira/workspace/apps/admin-console
npm ci --ignore-scripts
npm run build
```

产物仅一个 `admin-*.js`、一个 `admin-*.css` 和入口 HTML。部署仍由现有功能客户端的构建脚本统一完成：

```sh
cd /opt/allira/workspace/apps/dev-console
npm run build
```

正常构建会调用本项目的 Vite 构建，再把两个管理资产及入口复制到现有 FastAPI 静态目录；`--test` 保留历史客户端的隔离产物，不覆盖在线目录。更新后需明确重启现有单进程 `allira-dev`，让源站重新读取两个管理 hash 资产。不要另开开发 API、修改 Access 策略或扩大公开资产范围。

## 验证与边界

按用户的开发期直接交付要求，本轮只完成必要的 `vue-tsc`/Vite 编译及一次使用静态样例数据的页面效果确认；没有新增或运行全面功能、安全回归，也没有代用户完成真实 Access 登录。`tests/fixtures.ts` 和 Playwright 配置是暂停的草稿，不代表已存在可执行用例或覆盖率。实际页面数据来自管理 API，不包含预览样例。

第三方组件实际打包使用，45 个锁定运行依赖的许可声明保留在唯一 JS 产物和私有构建元数据中。`lodash-unified` 发布包未附独立 LICENSE，按其 package.json 的 MIT 声明与作者信息附上标准 MIT 文本；参见 `third-party-lodash-unified.txt`。构建元数据不发布为第三个管理资产。
