# 可溯源上下文与陪伴记忆：参考、设计与边界

这轮针对用户指定的三套项目做**固定提交静态研究**，再在现有 FastAPI/SQLite/Fetch SDK 上独立实现，不安装 DSH 插件、不运行第三方代理，也不搬它们的桌面客户端、提示词或素材。正式客户端视觉仍由 Gemini 设计；运行状态与证据须以本项目的验收和当前 API 为准。

## 三个参考不是同一种产品

| 参考与固定提交 | 实际定位和核验许可 | 借鉴与不采用 |
| --- | --- | --- |
| [billion-context](https://github.com/ranxianglei/billion-context/tree/a446a3c1c817ba9dfbde5efc22746fc27b2fff52) | 可逆、分层会话范围压缩与原内容检索；[LICENSE](https://github.com/ranxianglei/billion-context/blob/a446a3c1c817ba9dfbde5efc22746fc27b2fff52/LICENSE) 是 MIT **加产品表面归属附加条款**，不能简写为普通 MIT | 借鉴“摘要是可替换视图、保存来源、查回原文、明确预算”；没有引入其 kernel、代理、压缩工具或内部数据库，没有复用其缓存/成本/质量宣传数值 |
| [zhouPi-314/soulweave](https://github.com/zhouPi-314/soulweave/tree/4c2042f485610a48bf1c44a62ecd8e2339fe9bcd) | Windows Electron 陪伴应用，含人格、规则候选记忆、检索与桌面/语音集成；[代码 Apache-2.0](https://github.com/zhouPi-314/soulweave/blob/4c2042f485610a48bf1c44a62ecd8e2339fe9bcd/LICENSE)，[NOTICE](https://github.com/zhouPi-314/soulweave/blob/4c2042f485610a48bf1c44a62ecd8e2339fe9bcd/NOTICE) 的素材/附带组件另有限制 | 借鉴用户可编辑人格、确认偏好和边界、冲突须裁决；不搬 Live2D/角色形象/音色/QQ 链、非 UUID 回退角色、自动收费行为或关系施压式召回 |
| [ly-ina/Soulweave](https://github.com/ly-ina/Soulweave/tree/5e9c54a2460890d53a87bc8280fb9cdd0fc632f1) | `@ly-ina/dsh-soulweave` 0.2.7，DSH 人格/状态/检索插件，Node SQLite，并非另一套网站客户端；[MIT](https://github.com/ly-ina/Soulweave/blob/5e9c54a2460890d53a87bc8280fb9cdd0fc632f1/LICENSE) | 借鉴人格/关系/短状态职责分开、显式回忆和来源提示；不复用本机工作区文件能力、内部压缩库直写、无多租户绑定的默认存储或自动持久化事实 |

本项目的上下文机制参考了 **[billion-context](https://github.com/ranxianglei/billion-context)**，但未使用该软件或 acp-kernel 的运行实现。此处明确归属，不把独立 Python 规则摘录称作已经移植了可逆多级语义压缩。未来若引入其代码/内核，必须重新检查版权、附加归属条款与实际产品展示义务；其它项目的素材、商标和子依赖也不随代码许可一并授权。

## 静态研究发现的关键断链

- billion-context 的 [kernel 设计](https://github.com/ranxianglei/billion-context/blob/a446a3c1c817ba9dfbde5efc22746fc27b2fff52/kernel/DESIGN.md) 把内容存储、压缩块和展开视图分开，摘要由外部调用者提供；这不是 Allira 已具备模型生成摘要、缓存命中率或成本收益的证据。
- 桌面 SoulWeave 的 [规则摘录](https://github.com/zhouPi-314/soulweave/blob/4c2042f485610a48bf1c44a62ecd8e2339fe9bcd/src/core/memory-writer.ts#L54-L115) 不调用 LLM；[写入链](https://github.com/zhouPi-314/soulweave/blob/4c2042f485610a48bf1c44a62ecd8e2339fe9bcd/src/core/memory-writer.ts#L182-L252) 未给候选绑定对应 sourceMsgId。不能把“有来源字段”当成逐卡原句可追溯。
- 其 [云端入口](https://github.com/zhouPi-314/soulweave/blob/4c2042f485610a48bf1c44a62ecd8e2339fe9bcd/src/main/index.ts#L7059-L7141) 虽有接收 history 的 helper，主调用没有传 history 且提前返回；[DSH 路径](https://github.com/zhouPi-314/soulweave/blob/4c2042f485610a48bf1c44a62ecd8e2339fe9bcd/src/main/index.ts#L7170-L7205) 也未证明自动检索并注入本地记忆。静态结构不等于端到端记忆效果。
- DSH Soulweave 的 [内置记忆实现](https://github.com/ly-ina/Soulweave/blob/5e9c54a2460890d53a87bc8280fb9cdd0fc632f1/memory-builtin.mjs) 是另一种工作区插件语境，不能直接代替 Allira 的 owner/conversation/source 校验；短状态和关系说明也不是自动情绪识别或关系事实学习。

以上是特定版本的静态观察，不是对外部项目发行包、真实模型或未来版本的运行审计；也不据两个同名项目推断作者间不存在合作。

## Allira 的独立实现原则

### 原始消息、摘录存储、上下文视图分开

原始 Message 和完成状态仍为权威；只对 `Run` 实际关联、序号相邻、同会话的 completed user→assistant 对做规则摘录。每个历史块绑定这两个原消息 ID、起止序号、类别和创建时间；每侧最多取 500 字符并标明截断。范围 ID 由会话及来源确定，同一对不重复归档。

`summary` 旧字段保留兼容，成为预算内的视图，不再是唯一的历史笔记存储。默认最近 8 对、历史视图 6000 字符；移出视图不删除块或原文。旧版本 summary 无法凭空恢复精确出处，首次使用保存为 `legacy_summary` 并明确没有精确来源；不伪造旧原文回链，不用“重新总结”把同一旧内容再次填回去。

块可停用，作用是后续历史提示不采用它，**不代表删除原始聊天或其它副本**。有界列表仅检索摘录摘要；原文在用户显式点选某块时读取并重新验证精确完成对，缺失则告警而不重建。

### 用户明确填写偏好、边界和关系

卡片分类为 fact / preference / boundary / relationship，分类标签不是系统验证过事实。卡片由本会话 owner 手工确认，支持修改、启停、删除及可选来源。来源只能是此会话真实已完成对话对中的 user 消息；不将助手自述、失败片段、他人会话或孤立导入当成用户同意。

每会话最多 50 张卡，全部卡（包括停用）的 content 合计最多 20000 字符；单卡标题 1–100、内容非空白 1–2000。所有启用卡属于必须保留的固定上下文，与角色/核心设定/关系/当前输入一起超限时返回 413；不悄悄丢掉用户边界以换取更长聊天。停用/删除不自动清除原聊天、旧摘要、下载档案或历史备份。

没有自动提高亲密度、情绪打分、跨角色共享、后台收费生成或“你不聊天我会难过”的留存压力。没有自动从模型输出生成并确认事实。后续如加主动陪伴，应默认关闭、明确选择加入、可撤回、持久限额和防回环；角色不能以关系施压要求充值或转账。

### 一份规划器用于预览和真正发送

预览与 Run 使用同一个服务端规划器：固定设定与启用卡→最近真实完成对→预算内完整历史块→当前原始输入。可选历史以整块/整对选取，不静默切当前输入或用户边界。摘录明确标注为不可信历史数据，不授予工具/权限或当作用户新指令。

预览是受保护的 POST，也要求 Cookie Origin/CSRF。它在 SAVEPOINT 内临时规划并始终回滚，返回各段字符数、入选卡/块 ID、待写入块 ID、遗漏计数和完整消息数组；不创建 Run、事件、能量预留、不查模型或提前写摘要。预览仍会经过正常认证的会话最近使用维护，不是整个 HTTP 系统绝对无写入承诺。

这是真实拼装逻辑的**当前快照**，不是锁定未来请求：两次请求之间设定、历史或配置变化会改变结果。它不检查余额、频率、任务槽，也不能保证上游一定接受。计量为 `characters_not_tokens`，不是精确 Token、缓存命中率、压缩收益或模型质量。

### 归档和删除不遗漏记忆；分叉不带入未来

单会话 JSON/Markdown 与账号归档包含启用及停用的卡片/块；仍受会话 16MiB/50000 合并记录及账号 32MiB/100000 记录等限额，超限拒绝，不静默漏掉记忆。数据都属于私有档案，包含用户文本，不自动去除用户贴入的秘密；不进入 PWA 公共缓存。

删除会话/账号级联清理相应在线卡片与块；旧备份仍按保留策略存在。分叉不自动复制任何卡片、块或旧摘要，哪怕未绑定来源的卡也可能记录分叉点之后的信息。原有“当前角色/核心记忆/关系/会话设定作为新会话当前设定”的显式快照策略保留，不能将其包装成完全回到过去。导入归档仍只接受合法原相邻完成对，新增记忆数组不自动恢复为已确认记忆。

## 预设好，普通用户默认只聊天

服务端 daily/listen/focus 对应轻松日常、温柔倾听、一起行动，默认 daily+economy；一键 setup 原子创建会话与中性关系说明，不调用模型、不创建 Run/消息或消耗能量，owner+UUID 幂等且删除不复活。openingHint 是界面提示，不是已有 AI 回复。风格选择可选展开，自定义参数仍在高级入口。

客户端以输入、发送、停止和简短连接状态为主；账号、角色/记忆、迁移、检索、能量、预览与技术参数收进默认折叠设置/高级工具。既有功能不被移除，未知 POST 仍保留人工确认和精确重试，不用自动重发来制造“简单”的假象。目标用户不必掌握压缩、窗口、Token 或 Prompt，不按性别替她们确定称呼、恋爱或关系边界。Gemini 承接正式视觉，而不是重新发明这些功能和隐私契约。

## 本轮没有承诺的能力

未接入自动 LLM 总结、向量库、情绪模型、多级压缩内核或无界长上下文；不宣称百万 Token、固定节省比例、永不遗忘或任何真实陪伴/依赖行为质量。需要单独的真实任务集、错误事实/边界违反评估和用户许可后，才能评价实际模型收益。前端仅补功能控制，正式视觉、移动真机和公开部署的欠账保持原边界。
