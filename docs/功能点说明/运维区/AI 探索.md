# 运维区 · AI 探索（`/settings`）

> 本文件按《功能点说明》模板逐格写：这一屏**有哪些功能点、每处显示读哪个字段、口径从哪来、
> 缺数怎么显示、改了写到哪里**。代码是唯一真值，本文件只指路（`文件:行`＋函数名），不复述代码逻辑。
> 行号当场 `grep -n` 核过（2026-10-11）；接口读数带实测日期（2026-10-11 本机 8910，只打**只读**状态接口，
> 同一发打两次取第二发；未实测的格子已标「未实测」）。
>
> ⚠️ 与「系统配置」（`/system`）的分工：**这一屏管「模型接入」**——选订阅/API、写连接、测连通、排来源。
> **屏幕上的话术模板（`PromptSlotsPanel`）实际并不挂在这一屏**，它挂在「系统配置」页
> （`frontend/src/pages/System.tsx:201` 才 import＋渲染 `PromptSlotsPanel`；本屏 `Settings.tsx` 从头到尾
> 没有引用它，grep 全仓仅 `System.tsx:16,201` 两处消费）。所以本文 §5 把**话术模板这套机制**（SCOPES/自建 key/校验/恢复默认）
> 按代码证据写清楚（它确属"AI 接入"这一族），但**编辑入口的屏幕位置在 `/system`，不在本屏**——这一条与本屏原始定位不一致，
> 已在 §5 抬头与「可疑点」如实标出，不替后端/前端的分工说话。`运维区/系统配置.md` 尚未建档（`docs/功能点说明/运维区/` 是本轮新建目录），
> 该屏细节待那一份补。

---

## 0. 坐标

| 问什么 | 答 |
|---|---|
| 导航位置 | 侧栏**底部运维区**第一条「AI 探索」（`frontend/src/components/layout/Layout.tsx:70`，`<Cog>` 图标；这三条不进 `MODULES`，面包屑标题在 `Layout.tsx:39` 显式给「AI 探索」） |
| 卡片入口 | 业务页清单里 `to: "/settings"`、标题「AI 探索」、副标题「与 AI 对话、订阅与 API 接入状态」（`frontend/src/lib/workspace/features.ts:36`） |
| 会话/恢复标识 | `CHAT_PAGES['/settings'] = '接入 AI'`（`frontend/src/lib/workspace/modules.ts:72`）——这是**稳定标识**，不是界面文案，别与「AI 探索」混 |
| 路由 | 静态 import `frontend/src/router.tsx:33`，注册 `:79`（`{ path: "/settings", element: <Settings /> }`，本项目没有 lazy/Suspense） |
| 页面组件 | `frontend/src/pages/Settings.tsx`（`Settings()` `:29`；两 tab：`AI 对话` + `接入设置`，`TABS` `:24-27`） |
| Tab 状态 | 存 URL 查询参数 `?tab=`（`useSearchParams` `:32`）；`tab=access`/`tab=chat` 之外一律回落 `chat`（`:34`）；`pick()` 用 `replace` 写回、切回 chat 时清空参数（`:35-38`） |
| 页头副标题原句 | 「一个页面里既能和 AI 聊，也能管来源；所选连接由首页、复盘、深挖与页面问答共用。」（`:40`）——这句是**共用声明**：这一屏改的来源，`/`、`/agent/review`、`/agent/deepdive` 与各页浮层问答都会跟着读 |
| 只读接口 | `GET /api/review-agent/status`、`GET /api/review-agent/subscriptions/{claude\|codebuddy}`、`GET /api/review-agent/access`、`GET /api/review-agent/daily`、`GET /api/review/dates`、`GET /api/prompts`（前缀 `/api/review-agent`，`review_agent/api.py:294`；`/api/prompts` 在 `vr/app.py:1136`） |
| 引擎侧实现 | 连接/来源判定 `review_agent/access.py`（`Access` `:31`，`snapshot()` `:67`，`start()` `:81`）、`review_agent/runtime.py`（`status()` `:602-618`、`connection()` `:121-157`、`_invoke` 桥接 `:683-691`）、本机回环转换层 `review_agent/responses_bridge.py` |
| 谁写库/落盘 | 来源与密钥**只写浏览器本地**（`localStorage`，见 §4）；连接测试结果另存 `~/.duanxian-agents/.../access-tests.json`（`access.py:38`）；引擎健康结论走指纹缓存文件（`runtime.py:282-302`） |
| 侧栏身份行 | `Layout.tsx:60` 的 `data-ai-identity`（见 §4） |
| 字段含义出处 | 后端交什么就显示什么；接口没交那条含义时，屏上不自己编（`不替后端说话`，见各节「来源」列） |

一句话定位：这是**接入与对话**这一屏——左边（对话）用它，右边（设置）配它。
它只决定"用哪个模型、这条连接通不通、默认话术长什么样"，**不生成复盘报告**（复盘是 `/agent/review`，见 §6）。

---

## 1. 页头与三个 Tab（`Settings.tsx:45-67`）

| 功能点 | 显示/行为 | 代码锚点 | 注意 |
|---|---|---|---|
| 标题 | 「AI 探索」 | `PageHeader level` 未显式给（默认级别）`:45` | — |
| 副标题 | 「一个页面里既能和 AI 聊，也能管来源，也能把屏幕上那些数以 MCP 服务交给外部智能体；所选连接由首页、复盘、深挖与页面问答共用。」 | 同上 `:45` | 取数边界声明：这里选的来源是**全站共用**的一份 |
| Tab「AI 对话」 | 默认选中；渲染 `<WorkspaceChat onConfigureAccess={() => pick('access')} />` | `TABS[0] :26-27`，`:55` | `WorkspaceChat` 仍传 `page="首页"`——那是**会话与待发的稳定标识**（localStorage/`?page=` 过滤键），不是文案；换成「AI 探索」会让已有会话从列表里凭空消失（文件头注释 `Settings.tsx:21-24`） |
| Tab「接入设置」 | 渲染三块：AI 来源（含 `AgentToggle`）＋ `SavedSources` ＋ 旧版说明段＋后端访问密钥折叠 | `:57-66` | 换 tab **不重发请求**：共用同一次 `workspace.refresh()`（`:58` `onSaved={workspace.refresh}`）；换来源会立刻反映到对话侧标题（`:55`） |
| Tab「MCP 服务」 | 渲染 `<McpServicePanel />`：登记表对照、接入配置、连接自检、试调用 | `:31`（tab 定义）、`:67`（面板） | 10-22 新增。用 `?tab=mcp` 而不是新路由 —— 同一 `/settings` 挂两条导航项会被 `frontend/test/modules.test.ts` 判红（与交易日志那三个 tab 同一个做法）。tab 解析由 `TABS.some(...)` 派生（`:39`），**加第 N 片不用回来改这行**；面板内容一律现读 `/api/mcp/*`，页面不抄第二份台账 |
| `role=tablist` | 三个 `<button role="tab" id="ai-tab-{id}" aria-selected aria-controls="ai-panel-{id}">` | `:46-52` | a11y 锚点固定，自动化验收按 id 找 |

---

## 2. Tab「AI 对话」：`WorkspaceChat`（`frontend/src/components/workspace/WorkspaceChat.tsx`）

这一 tab 是把原先常驻首页的对话面板整体搬过来的（`Settings.tsx:16-17` 注释）。首页只留右上角浮层（`Layout.tsx:86`）。

### 2.1 头部读数

| 显示 | 读哪个字段 | 来源 | 缺数/条件 |
|---|---|---|---|
| 大标题（Agent 关） | 「今天，想聊什么？」 | `WorkspaceChat.tsx:127`，`page==='首页'` 且 `state.enabled===false` | 非首页时标题是「聊聊「${page}」」——本屏传的 page 恒为「首页」，所以只会出「今天…」两句之一 |
| 大标题（Agent 开） | 「今天，想复盘什么？」 | 同上 | 取决于 §4 的 AgentToggle |
| 副行（会话中） | 「本会话：普通对话/证据 Agent · {provider} · {model}」 | `conversation.source` `:127` | 会话锁死它**当初**的模式与来源（`fixedMode` `:37`），中途翻开关不改老会话 |
| 副行（无会话） | 「YuanShan Agent · 查证据、比较变化」/「普通对话 · 不调用工具」 | `state.enabled` `:127` | — |
| 身份串 `state.label` | 见 §4 的 `connectionLabel` | `state.label` `:128` | 「接入设置」按钮就地切 tab（`onConfigureAccess` `:130`），不外链跳页 |

### 2.2 逐控件（名｜屏幕位置｜读哪个字段｜改了写到哪｜是否立刻重发请求｜缺数显示｜悬停原文）

| 控件 | 位置 | 读/写 | 立刻重发请求？ | 缺数显示 |
|---|---|---|---|---|
| 会话下拉 `新建对话/选择` | 头部行 `:132` | `agentRequest('/conversations?mode=&page=&category=&tag=&search=')` | 换 mode/page/筛选会重发列表（`useEffect` 依赖 `:48`） | 读取失败置「无法读取历史会话，请刷新重试。」`:44` |
| 按分类 / 按标签 下拉 | `:133-134` | `/conversation-facets` 的 `categories/tags` | 有数才渲染（`facets.categories.length`）| 空 ⇒ 整条不出现 |
| 搜索框 | `:135` | `filter.q`，≤100 字 | 输入即进依赖，防抖在下一拍 | placeholder「标题或提问」 |
| 复盘日 `input[type=date]` | `:137` | `anchor`；仅 `state.enabled`（Agent 开）或老会话是 agent 时出现 | 不重发对话，但改 anchor 后 `send()` 会核对有无报告 | 已有会话时禁用（`disabled=!!conversation`） |
| 已存报告日期条 | `:138` | `agentFetch('/api/review/dates')` 的 `dates` | Agent 开时拉一次 | `!datesLoaded`→读错误原文；空→「尚无报告，请先生成复盘」 |

### 2.3 红线自证句（屏上原文，逐字）

- 无会话（direct）底部提示：「普通对话仅发送问题与本会话历史，不读取页面、复盘或本地文件。」（`:142`）
- Agent 开：「仅查询所选日及此前已有的公开复盘。没有资料时会明确报告缺口；本聊天不补充联网数据。」（`:142`）
- 发送时若 Agent 关而请求带联网/标的：后端直接拒「普通对话不能请求联网或标的工具」（`review_agent/api.py:189-190`），前端 `send()` 也不会带（`scope:{...,allow_network:false,symbol:''}` `:111`）。

---

## 3. Tab「接入设置」

### 3.1 AI 来源卡：`AgentAccess`（`frontend/src/components/AgentAccess.tsx`）

头部读数（`:125`）原句：「选择订阅或模型 API，首页聊天、复盘和研究共用这份连接。」

**模式二选一**（`:126-128`，两枚 `aria-pressed` 大按钮）：

| 按钮 | 屏幕文案 | 点了之后 |
|---|---|---|
| 使用订阅 | 「Codex · Claude · WorkBuddy」 | `choose('codex-private')`（`:127`），出三张订阅卡 |
| 使用 API 密钥 | 「new-api 网关 · DeepSeek · 通义 等」 | `choose(DEFAULT_ACCESS_PROVIDER)`＝`new-api`（`:128`；默认值来自 `LOCAL_GATEWAY.provider`，`lib/ai-access.ts:16,21`） |

订阅三选一（`:130`，仅 `使用订阅` 时出现）：`codex-private`（Codex 订阅）、`claude`（Claude 订阅）、`codebuddy`（WorkBuddy 订阅）——`SUBSCRIPTION_PROVIDERS` `ai-access.ts:6-10`。

API 服务商下拉（`:132`，`aria-label="Agent 接入来源"`）：`API_PROVIDERS` `ai-access.ts:24-40`。option 文案后缀规则（`:132` 内联）：`glm/kimi/qwen`→「（阿里云百炼）」；`isChatBridge`（`new-api`/`openai-compat`）→「（Chat Completions）」；`api-compatible`→「 Responses API」。

### 3.2 接入状态卡读数与三态（`null`＝没探到，不等于 `false`）

`AgentAccess` 按 provider 分两条探测路径，**都不发起付费模型调用**（只读状态；见 `state.tsx:45` 注释）：

| provider 类型 | 读哪个接口 | 存进哪个 state | 类型形状 |
|---|---|---|---|
| `claude`/`codebuddy`（`cli`，`:36`） | `GET /api/review-agent/subscriptions/{provider}`（`:43`） | `subscription`（初值 `null`） | `Subscription` 类型 `:7`：`installed/authenticated/available/detail` |
| `codex-private` | `GET /api/review-agent/status`（`:44`） | `catalog`（初值 `null`） | `Health` 类型 `:6`：`installed/subscription_ready/engine_reason/models/default_model/models_error` |
| 所有 | 每 1500ms 轮询 `GET /api/review-agent/access`（`:52`） | `job`（最近一次登录/测试任务快照，`:24`） | `Job` 类型 `:5` |

`catalog`/`subscription` 为 `null` 时那一句**不显示任何判死**的话——例如 `provider==='codex-private'` 那一行（`:138`）：

- `catalog === null`（没探到）：整行 `catalog && ...` 短路，屏幕**空白**，只留 placeholder；
- `catalog.installed === false`：「引擎实测跑不起来：{engine_reason||'原因未取得'}。这不是登录问题，先重跑 npm ci --prefix runtime（慢网要几分钟）」；
- `installed === true` 且 `subscription_ready === true`：「产品专用登录已建立」；
- `installed === true` 且 `subscription_ready === false`：「产品专用 Codex 尚未登录」＋出现「登录 ChatGPT」按钮（`:138`，仅 `catalog.installed` 为真才给登录按钮）；
- `models_error` 非空：另起一行原样贴出（`:138`）。

**`engineHealth` 一族 reason 全码表**（前端判定，`frontend/src/lib/workspace/status.ts:5-13`；后端字段住在 `runtime.py::status() :602-618`）：

| 后端返回 | `engineHealth` 判 | `ConnectionStatus` | `connectionLabel`（`:18-27`）出的句子 |
|---|---|---|---|
| 缺 `installed`/`subscription_ready`（非 bool）或缺 `models_error`（非 str） | 早退 | `error` | 「无法确认 AI 状态，请检查设置」 |
| `installed === false` | 取 `engine_reason` | `uninstalled` | 「产品引擎实测跑不起来{：reason}（不是登录问题，需重跑 npm ci --prefix runtime）」 |
| `installed && !subscription_ready` | — | `invalid` | 「{name} 未登录或登录失效，请设置」 |
| `installed && subscription_ready && models_error` | 取 `models_error` | `error` | 「无法确认 AI 状态，请检查设置」 |
| `installed && subscription_ready && !models_error` | — | `authenticated` | 「已登录：{name}（连接待实测）」（或 `previouslyTested` 时见 §4） |

> 后端 `installed` 的**含义是"实测跑得起来"，不是"目录里有 launcher"**（`runtime.py:603`）：`npm ci` 半截装会留下"launcher 在、平台包没了"，照样满足 `is_file()`，所以判据跑的是 `node codex.js --version`，结论按 `package-lock.json` 内容＋launcher mtime/大小做**指纹缓存**（`engine_probe` `:267-302`）。登录态同理走缓存 `login_probe`（`:305-`），`status()` 里回传的 `login_checked_seconds_ago` 是**这一结论的年龄**（`runtime.py:616`），交年龄是为了别让"此前已登录"被读成"此刻已登录"。`engine_reason` 只在 `installed=false` 时非空（`:617`）。

### 3.3 逐字段与逐控件表（接入设置 · AI 来源）

| 名 | 屏幕位置 | 读哪个字段 | 来源 | 改了写到哪里 | 立刻重发请求？ | 缺数显示 | 悬停原文（title/说明句） |
|---|---|---|---|---|---|---|---|
| API 服务商 | `:132` | `provider` | 本地 state | 只改草稿；`choose()` 重置 catalog/subscription 并按新 provider 重探（`:120-122,41-46`） | 是（切 provider 触发探测） | — | 无独立 title；下方 `preset?.detail`（`:133`） |
| 模型 | `:135` | `model` | 草稿／catalog 默认（`:44`） | 存进 localStorage 的来源条目需点「保存来源」 | 否（datalist 选值不重发） | `cli` 时 placeholder「default 表示订阅默认」 | 下方一句：api「可选择预设，也可直接填写账户可用的模型标识。」／claude·codebuddy「default 使用订阅默认模型…」／codex「模型列表来自产品专用登录。」 |
| API 基础地址 | `:140` | `baseURL` | 草稿 | 同上 | 否 | — | placeholder 按 chat 与否给本机/内网示例 |
| API 密钥 | `:141` | `apiKey`（`type=password`） | 草稿 | **仅存本机浏览器**，发送时才交给本机后端（`:146`） | 否 | — | 无 title；`:146` 整段安全说明（含「同一系统账户下的程序可能读取这些本地数据」） |
| 测试连接 | `:148` | — | — | `start('probe')` `POST /access/probe`（`:108`） | **会发一次真实请求、可能消耗额度**（`:146` 原句警告） | `busy\|\|validation\|\|订阅不可用` 时禁用 | — |
| 保存来源 | `:148` | — | — | `save()` → `saveAgentConnection` 写 localStorage（`:84-92`） | 否（不测试） | — | — |
| 刷新状态 | `:148` | — | — | `setRevision+1` 重跑探测 effect（`:41-46`） | 是（重发只读探测） | — | — |
| 登录 ChatGPT | `:138`（仅 codex 且 installed） | — | — | `start('login')` `POST /access/login` | 起官方登录（`auth_url` `:147`） | — | — |

探测/reconcile 行为：轮询到的 `job.status==='complete'` 且 id 匹配本次 pending 时，写通过标记、`setNotice` 一句
「{来源} / {model} 真实连接与证据工具测试通过。…」（`:68-73`）；`failed/cancelled` 时 `recordProbe(...,false)`（`:63-65`）。
提交未确认（网络断了但请求可能已进后端）时置 `unconfirmed`，给「重新确认本次测试」按钮而非盲目重试（`:111-113,149-150`）。

### 3.4 已保存 AI 来源：`SavedSources`（`frontend/src/components/SavedSources.tsx`）

头部（`:29-31`）：「共 {rows.length} 条 · 可用 {usable} 条 · 列表顺序即自动切换优先级，顺序循环」。
`usable` = `failures < FAIL_LIMIT` 的条数（`:27`；`FAIL_LIMIT=2`，`lib/agent-sources.ts:8`）。

| 控件/读数 | 屏幕位置 | 读哪个字段 | 改了写到哪 | 缺数显示 |
|---|---|---|---|---|
| 「这个不通自动用下一个（默认开启）」勾选 | `:33-41` | `failoverEnabled()`（localStorage `astock-agent-failover`，缺省即开，`agent-sources.ts:63`） | `setFailoverEnabled` 落盘并广播 `astock-connection-changed`（`:64-67`） | — |
| 每条序号+名字 | `:49` | `sourceLabel(row)` | — | — |
| 「本次任务会用」 | `:51` | `row.id === currentSource()?.id`（`serving`） | — | 无匹配则不出现 |
| 「你选的当前项（已让位）」 | `:52` | `row.id===active && !==serving` | `setActiveSource` | — |
| 测试徽标 `TestBadge` | `:8-14` | `testState`（`passed/failed/untested`）＋`failures`＋`testNote`＋`testedAt` | `noteSourceResult`（`:103-112`） | `untested`→「未测试」；`failed`→「已判定不通（连续 N 次）：{testNote}」；`failures>0 且<2`→「失败 {failures} 次，再失败 {2-failures} 次才切换到下一条」 |
| 上移 / 下移 | `:56-57` | 列表顺序 | `moveAgentSource` | 首行上移禁用、末行下移禁用 |
| 移除 | `:58` | — | `removeAgentSource`（删 localStorage 条目） | 弹 toast「已从本机列表移除；该来源的历史会话记录仍在本地保留」 |
| 清除测试标记 / 设为当前 | `:64-65` | — | `clearSourceMark` / `setActiveSource` | 「清除测试标记」仅 `testState!=='untested'` 时出现 |

底部边界原句（`:69`）：「来源与密钥只存在这台电脑的浏览器里；「测试连接」会向所选服务发真实请求并可能消耗额度。切换当前来源只影响之后新建的对话与任务。」

自动切换的判据（勾选框下那段，`:37-40` 逐字）：同一条**连续失败 2 次**才判不通并让位；失败 1 次等于先重试一次；
**切换只在任务开始前生效**，同一轮对话不换模型，已有会话锁当初来源；全部不通时不清标记、按顺序轮流再试；
**上下文超限、本机存储与后端访问密钥问题不算来源故障**（对应 `reportAgentOutcome` 的 `NOT_SOURCE_FAULT`，`agent-sources.ts:143-144`）。
当开启自动切换且 `usable===0` 时出现红框告警（`:44`，`role=alert`）。

### 3.5 旧版说明段 ＋ 后端访问密钥折叠（`Settings.tsx:54-55`）

- 说明段原句（`:54`）：「旧版分散的 AI 设置不再用于产品调用；此前保存的单条来源已自动并入下面的来源列表，密钥没有被迁移到别处。页面问答在 Agent 关闭时仅阅读本次明确提供的材料；开启后可查询公开资料。完整复盘与个股深挖由各自的任务按钮明确发起。」
  这条"单条并入列表"的实现在 `agent-sources.ts:38-52 readSources()`：读到旧 `astock-agent-connection` 单键时，转成一条带 `crypto.randomUUID()` 的列表项、设为 active、落新键、删旧键。
- `<details>`「后端访问设置」→「后端访问密钥（可选）」（`:55`）：`loadAccessKey/saveAccessKey`（`lib/api.ts:19-31`），存 localStorage 键 `ACCESS_KEY`；**这是访问本后端的密钥（对应部署侧 `VR_API_KEY`），不是 AI 服务商密钥**（`:55` 原句强调）。空则不发送（`api.ts:36-40 authHeaders`）。保存后调 `workspace.refresh()` 让身份行重探（`:55`）。401 时报「请在「AI 探索 · 接入设置」页填写后端访问密钥（VR_API_KEY）」（`agent-api.ts:36`）。

---

## 4. Agent 开关与侧栏 `data-ai-identity`

### 4.1 `AgentToggle`（`frontend/src/components/workspace/AgentToggle.tsx`）

| 问什么 | 答（代码证据） |
|---|---|
| 屏幕上那句原话 | 「开启Agent」按钮右侧灰小字「（更深入·较慢·费Token）」（`:13`）；`compact` 时两侧文字都不渲染，只剩开关本体 |
| 它改的键 | localStorage `astock-workspace-agent-v1`，值 `'on'`/`'off'`（`lib/workspace/state.tsx:5-6 modeKey/readMode`；写与翻转 `:53-56 toggle`） |
| 生效时机 | **翻开关只影响"下一个新会话"**：`WorkspaceChat` 的 `mode = state.enabled?'agent':'direct'`（`:24`），但已有会话锁它当初的 `fixedMode`（`:37`），改了会拦「模式已改变，请新建会话后发送。」（`:100`）。不是"下一个心跳"也不是"重启"——`toggle` 立即写盘并 `setEnabled`，当前页 header 文案随之变，但**不重发对话请求** |
| a11y | `role="switch"`、`aria-checked`、`aria-label="开启Agent"`（`:5`）；`aria-description`「切换新对话模式。关闭时不调用工具，开启时可查询已有复盘证据。已有会话保留原模式。」（`:6`）、`title`「新对话使用所选模式；已有会话保留原模式。」（`:7`） |
| 报错 | `state.error`（存不下时）：「无法保存开关，请允许本地存储后重试。」（`state.tsx:55`）显示为 `role=alert`（`AgentToggle.tsx:15`） |
| 出现位置 | 接入设置 AI 来源卡右上（`Settings.tsx:52`）、侧栏底部（`Layout.tsx:60`）、侧栏收起态居中（`Layout.tsx:61`） |

### 4.2 侧栏 `data-ai-identity` 那句话（`Layout.tsx:60`）

结构：品牌行下方一个 `<div data-ai-identity>`，内含固定小字「A 股短线复盘与跟踪工作台」＋一条 `Link to="/settings"`（悬停 `title={state.label}`）＋一个 `AgentToggle`。

那句话的文字由这段决定（`Layout.tsx:60` 内联三元）：

```
state.previouslyTested && (state.status === 'authenticated' || state.status === 'saved')
  ? `已接入AI：${state.name}`
  : state.label
```

- `previouslyTested` = 当前选中来源带 `verifiedAt`（`loadAgentConnection(true)` 且 `testState==='passed' && testedAt>0 && 未来自未来`，`state.tsx:27-28`＋`agent-sources.ts:136`）。
- `state.name` = `sourceLabel(当前来源)`（`state.tsx:35`）。
- 否则显示 `state.label = connectionLabel(status, name, previouslyTested, engineReason)`（`state.tsx:57-58`，全码见 §3.2 表），其中 `saved` 态是「已保存AI：{name}（待实测）」、`authenticated` 且已实测是「{name}（此前连接实测通过）」（`status.ts:24-26`）。

**出现条件全枚举**（`state.tsx:25-52` effect）：

| 情况 | status | 侧栏那句 |
|---|---|---|
| 无本地保存来源 | 有列表→`error`，全空→`missing` | 「无法确认 AI 状态，请检查设置」/「未接入AI，请设置」 |
| `claude/codebuddy` | 探 `/subscriptions` 后 `uninstalled/invalid/authenticated/error` | 见 §3.2 label 表 |
| 非 codex 的 API 来源（new-api/openai-compat/…） | 直接 `saved`（`state.tsx:43`） | 「已保存AI：{name}（待实测）」，若曾实测通过则「已接入AI：{name}」 |
| `codex-private` | 探 `/status` → `engineHealth` | 同上 |

> 判定只走 `status` 一个口径，**前端不许自己再判"有文件就算装好"**（`status.ts:2-4`、`state.tsx:11-13`）：半截装时"文件在"与"跑得起来"背离，页面若按"有登录态吗"说话就会把没装引擎说成"未登录"（2026-09-25 误判记录）。

---

## 5. 协议层的本机事实（本项目已定，非通用做法）

> 这一层屏幕上几乎看不见：`接入设置` 只让你填地址/模型/密钥，它**不告诉你**引擎说的是哪种线协议。
> 排障看 `docs/系统配置工作指南/06-自动执行机制自查表.md`（`responses_bridge` 记账两行在 `:260-261`、`:280`）。

四条逐条指到实现：

1. **引擎只认 Responses**：固定版本 Codex 引擎的 provider 配置写死 `"wire_api": "responses"`
   （`review_agent/runtime.py:376-380`，`config_for()`），文件头原句「Official pinned Codex CLI owns the loop」（`:1`）。
   界面上唯一露出这一点的地方：`api-compatible` 那个服务商 option 后缀写了「 Responses API」（`AgentAccess.tsx:132`）。

2. **上游网关没有 `POST /v1/responses`**：new-api 这类兼容网关只提供 `/chat/completions`。
   `responses_bridge.py` 的模块 docstring 写得很直白：「a gateway that serves /chat/completions cannot be reached directly」（`:3-4`）。
   所以这类 provider 归进 `CHAT_PROVIDERS = ['new-api','openai-compat']`（`lib/ai-access.ts:23`），下拉后缀标「（Chat Completions）」。

3. **工具必须带 namespace**：引擎的 tools 用 `type:"namespace"` 分组、每个函数一个 namespace＋name。
   转换层 `tool_maps()` 把「namespace + 子函数」拍平成 Chat 的函数名 `namespace__name`（`SEP="__"`，`responses_bridge.py:25,119-139`），
   并留一张 `reverse` 表；`unflatten()` 在回收 function_call 时按原 namespace 还原回去（`:110-116,239-244`）。
   `to_chat()` 里对 `function_call` 也做同样 `flatten(namespace, name)`（`:159-164`）。

4. **本机有一层回环转换**：`responses_bridge.py` 起一个 **只听 127.0.0.1** 的一次性 HTTP 服务
   （`_start()` `:374-381`，`ThreadingHTTPServer(("127.0.0.1", 0), Handler)`），
   把引擎发到它的 `/v1/responses` 请求就地终止，翻成对真实网关的**流式 chat completion** 再转发出去（`to_chat` `:142-176`；
   `do_POST` 只认 `/v1/responses`，其余 404「本机模型转换层只提供 /v1/responses」`:299-300`；
   上游 `client.stream("POST", base_url + "/chat/completions")` `:323`），再把 SSE 分片重组成 Responses item 序列喂回引擎（`Stream.tail` `:227-259`）。
   接线点：`runtime.py::_invoke` 里 `if source["provider"] in CHAT_PROVIDERS: base_url, bridge_token = open_session(...); config["model_providers.astock_api"]["base_url"] = base_url; key = bridge_token`（`:683-691`）——
   **服务商密钥只留在这个 Web 进程，引擎只拿到回环地址＋一次性 token**（`responses_bridge.py:5-7` docstring；`Sessions.add` 用 `secrets.token_urlsafe(32)`，`:80-82`）。

> 这一层界面上不体现的部分（端口随机、token 一次性、TTL 2h/最多 8 会话 `SESSION_TTL/MAX_SESSIONS` `:21-23`、
> 上游超时 `connect15/read180/write30` `:24`、失败只记 `state.json` 的 `responses_bridge.reason`（截 200 字）`:29-46`）——排障看
> [../../系统配置工作指南/06-自动执行机制自查表.md](../../系统配置工作指南/06-自动执行机制自查表.md)。

---

## 6. 话术模板（机制写清；**编辑入口不在本屏，在 `/system`**）

⚠️ 再次点明：任务把"话术模板"划给本屏，但代码里 `PromptSlotsPanel` 只被 `System.tsx` 引用并渲染
（`frontend/src/pages/System.tsx:16` import、`:201` `<PromptSlotsPanel />`），`Settings.tsx` 全程没有它。
`prompt_slots.py` 文件头也自称「可在**系统配置页**改」（`duanxian/prompt_slots.py:1`）。
**本屏（AI 探索）不承载话术编辑 UI**；下面把这套机制按其真实归属写清楚，屏幕位置以代码为准。

### 6.1 「类型 + 可选模板」的结构

- **类型表 `SCOPES`**（`prompt_slots.py`）：一个类型＝页面上一个取数点＋那一行能给模型的**字段白名单**。
  内置三类：`lianban`（盘面数据·连板股那一行，13 个字段）、`firstboard`（首板分析·首板股那一行，11 个字段）、
  `stock_brief`（10-22 加：个股深度复盘·400 字公司说明，5 个字段）。
  ⚠️ 这一档**刻意没有 `{material}` 占位符**：材料走这条通道的 `context`（硬顶 8,000 字），话术走最后一条消息（硬顶 4,000 字），
  合进一个模板会让两个上限互相挤。见《个股研究/个股深度复盘.md》§3.2。
- **内置槽位 `SLOTS`**：`lianban_dive`、`firstboard_dive`、`stock_brief`，每个＝「类型(scope)＋一份默认话术」。
- 页面配置渲染：`GET /api/prompts` 把 `list_slots()`（内置在前＋自建，`:241-243`）与 `scopes`（字段清单唯一来源）一起交出（`vr/app.py:1136`）。
- 选中哪份记在浏览器本地：`localStorage` 键 `yuanshan-prompt-choice-{scope}`（`lib/prompts.ts:104-112 useTemplatePicker`）。

### 6.2 键规则与"恢复默认删不到自建"

| 键族 | 前缀常量 | 含义 | 恢复默认能否删掉 |
|---|---|---|---|
| 内置槽位的覆盖 | `SETTING_PREFIX = "ai_prompt."`（`:27`），如 `ai_prompt.lianban_dive` | 覆盖某个内置槽位的话术 | **能**：`reset()` 只 `DELETE FROM config.setting WHERE key = ai_prompt.<slot>`（`:302-309`） |
| 自建模板 | `CUSTOM_PREFIX = "ai_prompt.custom."`（`:30`），id 由 `_ID_RE=[A-Za-z0-9_-]{4,40}` 或 `uuid4[:12]`（`:31,274-277`） | 用户手打的一份模板，一条一个键（不是一整张数组，`:214-216`） | **删不到**：`reset()` 的 WHERE 只命中 `ai_prompt.<slot>`，永不碰 `ai_prompt.custom.*`。刻意分成两段前缀，就是"否则'恢复默认'会顺手删掉用户手打的资产"（`:28-30` 注释） |

`source` 字段必须随每条交回前端，区分"页面配的那份"与"内置默认（还没存过）"（`effective()` `:203-211`）；
自建模板若类型已被代码删掉，交回 `(类型 X 已不存在)` 并提示建议删掉重建（`custom_rows()` `:226-231`）。

### 6.3 保存时的三条校验（`_check_text` `:160-178`，分别拦什么）

| # | 拦什么 | 触发 | 报错原句（节选） |
|---|---|---|---|
| ① 空话术 | 整段空白 | `if not out` | 「话术不能为空」 |
| ② 未知占位符 | 该类型没有的 `{xxx}` | 与 `SCOPES[scope].fields` 比对 | 「这些占位符这个类型没有对应数据：{…}；可用的是 {…}」 |
| ③ 缺"不给点位"约束 | 整段没有任何"别给交易动作/点位"的句子 | `_TRADE_ADVICE`（`:49`）不命中 | 「这份话术里完全没有约束模型给点位的句子。…要能自由给价位的通道在右侧「AI分析」（按需研究档），不是这里。」 |

第③条是**按意思拦、不按措辞拦**（`:163-165` 注释）：换成自己的措辞可以，整段删掉不行——否则 README 对外承诺变成配置页上随手可关的开关。
`save_custom()` 在此之上还额外校验：`scope in SCOPES`、标题非空且 ≤60 字、id 合 `_ID_RE`（`:263-277`）。
前端 `unknownIn()` 只是提前把②的名单显示出来（`PromptSlotsPanel.tsx:57-61,131-133,201`），**真正的闸在服务端**，前端不报错不代表后端放行。

---

## 7. 与「复盘报告」（`/agent/review`）的关系

- **本屏没有任何触发复盘的按钮。** `Settings.tsx` 的两个 tab 里：AI 来源/已保存来源只写来源；`WorkspaceChat` 发的是一问一答
  `POST /api/review-agent/turns`（`WorkspaceChat.tsx:110`），它读的是**已有复盘的证据**（`build_bundle(reviews, anchor)`，`api.py:203`），
  不生成报告。真正生成复盘的入口是 `POST /api/review-agent/daily`（`api.py:306`），挂在 `/agent/review`（`router.tsx:42`），不在本屏。
  引擎的 `developer_instructions` 甚至明写「生成复盘请去复盘报告，行情去盘面数据；不要承诺聊天开关可以执行这些任务」
  （`runtime.py:673`）——所以即便有人在对话里敲"帮我生成复盘"，代码也不会去跑 daily。
- **报告正文有三道闸**（详见 `复盘/复盘报告.md` §4.2，此处只点到本屏会读它的哪些证据）：
  ① 生成中逐字段校验（引用/自由数字/交易动作，`check_report_text` `daily.py:30-36`＋`grounding.py`）；
  ② 保存检查（`review_store.save` 的 `usable` 判定与 `_rejected/` 归档，`review_store.py:93-142`）；
  ③ 出口/通道边界（`runtime.py:636` direct 档出口、`evidence.py:504-556` 追问包）。
- **`~/.duanxian-agents/reviews/` 的当前数据现实**（2026-10-11 实测）：只有 `_rejected/`（188 份），根下 0 个 `<date>.json` ⇒
  `GET /api/review/dates` 交出 `{"dates": []}`（实测 200，`dates` 长度 0）。
  因此本屏对话的 Agent 模式下"已存报告日期条"会显示**「尚无报告，请先生成复盘」**（`WorkspaceChat.tsx:138`），
  选任意日期发送都会被拦「所选日期尚无已存报告。请先到复盘报告生成，或选择下方已有日期。」（`:98`）。
  这不是这一屏坏了，是**库里就没有可用正文**（六次 daily 作业全 failed，最新一次 `GET /api/review-agent/daily` 实测
  `status:"failed"`、`error:"AI 内容或引用未通过核对：findings[0]: 解释含自由生成数字…"`）。

---

## 8. 数据现实（2026-10-11 本机 8910 实测，只打只读接口，同一发取第二发）

| 接口 | 结果（第二发） | 关键读数 |
|---|---|---|
| `GET /api/review-agent/status` | 200（首打 0.465s／二发 0.007s） | `installed:true`、`subscription_ready:false`、`engine:"Codex 0.153.4"`、`engine_reason:""`、`engine_probe:"codex-cli 0.153.4"`、`login_checked_seconds_ago:0.5`、`models:[]`、`default_model:""`、`models_error:""` ⇒ `engineHealth` 判 `invalid`（引擎跑得起来但产品专用未登录） |
| `GET /api/review-agent/subscriptions/claude` | 200（二发 0.717s） | `installed:false`、`authenticated:false`、`available:false`、`status:"not_installed"`、`detail:"本机未检测到 Claude Code"`、`version:null`（`null`＝没探到版本，不等于版本为 0） |
| `GET /api/review-agent/access`（接入状态快照） | 200（二发 0.006s） | 最近一次是 `kind:"probe"`、`status:"complete"`、`message:"模型与工具调用已通过真实测试"`、`elapsed_seconds:70.54`、`source:{provider:"new-api",model:"allmlmlh",baseURL:"http://192.168.176.68:3000/v1"}` **三键、无密钥字段** |
| `GET /api/review-agent/daily` | 200（二发 0.005s） | 最后一个 job `date:"2026-09-24"`、`status:"failed"`、`running:false`、`elapsed:233`、`stage:"生成题材热点并核对引用"`、`error` 是第①道闸原句、`source` 同样无密钥 |
| `GET /api/review/dates` | 200（二发 0.005s） | `{"dates": []}` —— 0 个可用报告日期 |
| `GET /api/prompts` | 200（首打 0.270s／二发 0.011s） | 那一轮打出的是 2 个内置槽位 `lianban_dive/firstboard_dive`（`edited:false`、`source:"内置默认（还没存过）"`）、2 种类型（`lianban` 13 字段、`firstboard` 11 字段）、**0 份自建模板**。**10-22 起代码里是三类三槽**（新增 `stock_brief`，见 §话术槽位那一条），这条是当时的实测记录，别照抄成现状；8910 上跑的那份要重启之后才交三个（重启前用只读侧实例打出过 3 条：模板下拉里「内置 · 个股深度复盘 · 400 字公司说明」已在） |
| `~/.duanxian-agents/reviews/` | — | 只有 `_rejected/`（188 份）、根下 `<date>.json` **0 份**（与 `dates:[]` 一致） |

> 屏幕上的**具体来源名/待实测串**取决于用户浏览器 `localStorage` 里那几条已保存来源，
> curl 打不到、也不该代填 ⇒ §4 各态文案标「未实测（浏览器侧）」；本轮实测只覆盖服务端只读状态。
> 含 key/token/secret/authorization 字样的字段**只报键名与是否存在、值一律不打印**——上表所有接口返回均无此类明文键；
> `.env` 未读取、未摘录。

---

## 9. 不许破的纪律（红线优先）

1. **红线（最高优先）：复盘笔记 / 提醒 / 持仓内容绝不进任何 AI 提示词；持仓页禁止任何 AI 入口。**
   - 靠什么保证：
     - **界面侧白名单**＝`frontend/src/lib/workspace/pageAskAiStore.ts`——只有首页盘面数据 / 个股研究 / 自选股三页会 `registerPageAskAi`，
       持仓页 `/my-stocks` **永远不登记**（`:17-18` 注释明写这是"持仓数据不接入任何 AI prompt"红线的界面落点）；
       右侧「AI分析」拿不到登记就**不放输入框**（`AiAnalysisPanel.tsx:114-119` 原句：「持仓（重点关注）**永远不登记** —— 成本与盈亏不接入任何 AI」）。
     - **后端侧白名单**：生成通道只读 `grounding.py:18-23 SOURCES`＋`public_worker.py:15-19` 的取数名单；追问通道只读 `evidence.py:258-345`（未知字段绝不跨边界）；本屏对话的 `bundle` 走 `build_bundle(reviews,…)`，压根不碰 `journal/positions/review_notes/risk` 这些表。
     - **散点文件头自证**：`duanxian/journal.py:9`、`positions.py:16`、`risk.py:15`、`review_notes.py:18` 各写「本模块的数据不接入任何 AI prompt」（当场 `grep -n` 核过这四行）。
   - **若有人加一个把 `note` 带出去的按钮会踩什么**：
     - `WorkspaceChat.send()` 发的 `scope` 只有 `{mode,page,allow_network,symbol}`（`:111`），`note` 进不去——后端 `ResearchScope`/`TurnInput` 是 `extra="forbid"`（`api.py:25,71-87`），多带字段直接 422；
     - `PrivateRoute` 还会在 422 时**刻意不回显被拒输入**（`api.py:135-139`，"那可能是一整个含密钥的 llm 对象，绝不 echo"）；
     - 真把 note 拼进 prompt 就是**跨边界**：`evidence.py`/`grounding.py` 的白名单是"未知字段绝不带出"的设计，加了就等于把个人持仓/日志洗进发给外部模型的提示词——直接违反 README 对外承诺，且 `pageAskAi.test.ts`、`test_agent_public_data.py` 一类红线圈钉会红。要放开得走另一条已裁定的通道（按需研究档 `analysis`），不是在这里加按钮。

2. **`null` 不等于 `false`**：没探到就是没探到。`catalog/subscription` 为 `null` 时那一行不判死（§3.2）；`version:null`、`dates:[]` 都按"未取得"显示，不当"没有/为 0"。

3. **判定只走一个口径**：引擎健康由 `status.ts::engineHealth` 一处判（`:2-4`），前端不许另起"有文件就算装好"（2026-09-25 误判教训）；来源能不能用由「测试连接」与任务结果决定，**保存不等于可用**（`AgentAccess.tsx:83-92,146`）。

4. **`CHAT_PAGES['/settings']='接入 AI'` 是会话/恢复标识**，不是界面文案（`Settings.tsx:20-23`、`modules.ts:63-64`）——别为了对齐导航去改它，否则已有会话从列表凭空消失。

5. **回环转换层的密钥不外泄**：服务商密钥只留 Web 进程，引擎只拿回环地址＋一次性 token（§5-4）；`access.py::_persist`「No credentials or private CLI outputs: only request fingerprint and safe status」（`:57`），daily/access 返回的 `source` 恒三键无密钥（实测）。

6. **话术只能改话术，改不动红线**：第③条校验与 `_TRADE_ADVICE`、`COMPLIANCE_CLAUSES`（`prompt_slots.py:37-49`）保证"不给交易动作/点位"这句删不掉。

---

## 10. 验收与复现

```bash
# 只读状态接口（冷启首打慢数倍，取第二发的耗时才算证据）；含敏感字段只看键名不打印值
curl -s -o "$TEMP/ai_status.json"  -w "%{http_code} %{time_total}s\n" "http://127.0.0.1:8910/api/review-agent/status"
curl -s -o "$TEMP/ai_sub.json"     -w "%{http_code} %{time_total}s\n" "http://127.0.0.1:8910/api/review-agent/subscriptions/claude"
curl -s -o "$TEMP/ai_access.json"  -w "%{http_code} %{time_total}s\n" "http://127.0.0.1:8910/api/review-agent/access"
curl -s -o "$TEMP/ai_daily.json"   -w "%{http_code} %{time_total}s\n" "http://127.0.0.1:8910/api/review-agent/daily"
curl -s -o "$TEMP/ai_dates.json"   -w "%{http_code} %{time_total}s\n" "http://127.0.0.1:8910/api/review/dates"
curl -s -o "$TEMP/ai_prompts.json" -w "%{http_code} %{time_total}s\n" "http://127.0.0.1:8910/api/prompts"

# 后端：引擎/接入/话术/红线（文件名已 ls 确认存在于 tests/）
PYTHONIOENCODING=utf-8 .venv\Scripts\python.exe -X utf8 -m pytest tests/test_agent_access.py tests/test_prompt_slots.py tests/test_analysis_lane.py tests/test_product_boundaries.py -q

# 前端：来源存储 / 接入草稿 / 页面 AI 白名单（红线）
cd frontend && node --test "test/agent-sources.test.ts" "test/ai-access.test.ts" "test/pageAskAi.test.ts"
```

> 本文**未跑 pytest**（按要求）。上列命令供复现，文件名均已 `ls` 确认存在。

浏览器逐控件清单（`/settings`，**只看不点付费动作**）：
- 两个 tab 切换 → `?tab=` 变、对话标题随 `state.enabled` 改；`onSaved` 触发 `workspace.refresh()` 后 §4 身份串重算；
- AI 来源：订阅/API 两枚大按钮互斥、`aria-pressed` 跟手；切 provider 后 `catalog/subscription` 归 `null` 再重探（**不发付费 probe**）；
- 「测试连接」按钮在 `busy/validation/订阅不可用` 时禁用（**:148**）——本轮**未点击**（会发真实请求）；
- 后端访问密钥 `<details>` 展开、保存后 toast「已保存后端访问设置」；
- AgentToggle 翻动 → 侧栏/接入设置两处同步、header 文案随之变，但老会话文案不变（锁 `fixedMode`）；
- 渲染纯文本 `**` 计数应为 0（`pageAskAi`/`AiAnalysisPanel` 用的是 `<b>`，非 markdown 星号）。

---

## 11. Tab「MCP 服务」（10-22 新增）：`McpServicePanel`

> 这一片不产生任何新数据，只把「智能体能取到哪一格数」摊开。**总台账在 [../../MCP服务登记表.md](../../MCP服务登记表.md)**
> （工具 ↔ 来源端点 ↔ 处理函数 ↔ 主读表 ↔ 屏幕落点 的逐条对照）；这一节只写上屏形状与读法，判据与算法不在此复述。

### 11.1 五块内容（自上而下）

| 块 | 读哪个接口/字段 | 屏上控件 | 缺数显示 |
|---|---|---|---|
| ① 四格计数 | `/api/mcp/registry` 的 `counts.A / B / C / tools` | 只读数字 | 「未取到」；连不到后端显示「连不到后端」 |
| ① 权限边界 | `/api/mcp/status` 的 `redlines[]`（三条由服务侧代码给，页面不抄） | `ExplainBox`（默认收起） | 「未取到：原因」 |
| ② 接入前自检 | `status.checks[]` 的 `label/available/detail/reason`；顶部那句读 `all_green/blocking/optional_pending` | 逐行 ✓/✗ 图标 | `available=false` 出红叉＋`reason` 原文；**接口没返回就显示「未取到」，绝不显示 ✓**。⚠️ `status.available` 只回答「这份体检取到了没有」，**不等于**全绿——HTTP 模式没起属可选项（10-22 修：早先把它写成 `all(checks)`，于是那一档没起会把已在手的四条绿项一起判成「未覆盖」而整块藏掉） |
| ③ 工具对照表 | `registry.capabilities[]`（`capability_id/label/where/layer/path/params`） | 层筛选按钮（全部/A/B/C）＋可点列头排序＋每行「看明细」 | 「未取到：原因」 |
| ③ 明细展开 | 同上的 `handler`、`source_module`、`tables`、`slow`、`note` 与 `params[]`（名/类型/默认/上下界/必填） | 表下参数子表 | 无参数时明写「无参数」 |
| ④ 接入配置 | `status.config.stdio` / `.http` / `.http_start_command` / `.cwd` | 三段 `<pre>` ＋ 各自「复制」按钮 | 「未取到：原因」 |
| ⑤ 试调用 | `POST /api/mcp/try`（body `capability_id`＋`params`），走的是智能体同一条 `mcp_service.client` | 下拉选工具＋参数 JSON 框＋「真的调一次」 | 失败显示「调用失败：原因」并补一句「这不是『今天没有数据』」 |

### 11.2 计数与条数一律由接口说

- 页面源码里不出现「24 个」「共 N 条」这类抄死的数（`frontend/test/mcpServiceTab.test.ts` 有一条 `doesNotMatch` 钉着）。
- 台账漂移时后端在 `registry.doc` 里回报 `missing_in_doc/extra_in_doc/path_mismatch`，界面在对照表下方**红框点名**——这一屏不替漂移打圆场。

### 11.3 试调用这一发 POST 不写任何东西

它只是带请求体的读：`vr/mcpboard.py::try_invoke` → `mcp_service/client.py::call_capability` → 对本机 `127.0.0.1:8910` 发一次 GET。
三道闸（`tests/test_mcp_registry_sync.py` 逐条钉）：只认登记表里的 `capability_id` 或索引里的 GET 路径；参数名必须是该端点真认识的键（不认识的**直接报错**，不静默丢弃）；`refresh` 一类触发重算的参数一律不外露、不许传。

### 11.4 复现命令

```bash
# 只读三条接口（冷启首打慢数倍，取第二发的耗时才算证据）
curl -s http://127.0.0.1:8910/api/mcp/registry > .local/mcp_reg.json
curl -s http://127.0.0.1:8910/api/mcp/status    > .local/mcp_status.json
# MCP 服务本体：装配自检 + 真起一个 stdio 客户端
.venv/Scripts/python.exe -m mcp_service --check-tools
# 校验：台账 ↔ 登记表 ↔ 真实端点 ↔ 屏幕能力清单
.venv/Scripts/python.exe -m pytest tests/test_mcp_registry_sync.py -q
cd frontend && node --test test/mcpServiceTab.test.ts
```

---

## 相关

- 引擎/协议层排障：[../../系统配置工作指南/06-自动执行机制自查表.md](../../系统配置工作指南/06-自动执行机制自查表.md)（`responses_bridge` 记账两行 `:260-261`、`:280`）
- 复盘报告正文三道闸与引用核对：[../复盘/复盘报告.md](../复盘/复盘报告.md)（§4.2）
- 口径展示纪律：[../../页面展示工作指南/01-数据口径展示规范.md](../../页面展示工作指南/01-数据口径展示规范.md)
- 引擎本体：`review_agent/`（`access.py` 登录与真测、`runtime.py` 引擎/连接/状态、`responses_bridge.py` 本机回环转换、`subscription_bridge.py` 订阅探测、`product_policy.py` 红线边界、`daily.py`/`grounding.py` 复盘正文闸）
- 话术模板机制：`duanxian/prompt_slots.py`（`SCOPES`/`SLOTS`/`_check_text`），编辑入口渲染在 `frontend/src/pages/System.tsx:201`（`/system`，本屏不含）
