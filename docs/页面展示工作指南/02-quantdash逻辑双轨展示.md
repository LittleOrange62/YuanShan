# 02 · quantdash 逻辑双轨展示契约

> 配合 [数据加工工作指南/09-quantdash逻辑规格.md](../数据加工工作指南/09-quantdash逻辑规格.md) 使用。
> 09 定义"算什么、怎么算、存哪"；本文定义"**怎么摆到页面上**"。
> 前置纪律：[01-数据口径展示规范](01-数据口径展示规范.md) 六条全部适用，本文不重复。

## 0. 一句话需求

用户原话：**"实际上我的逻辑跟它的逻辑是不一样的，我需要它的逻辑也能够展示出来。"**

所以页面不是"用 quantdash 的算法替换我们的"，也不是"去重后只留一份"，而是**两套口径并排给出，差异可见、来源可查**。
差异本身就是用户要的信息 —— 他想知道"同一个交易日，两种算法分别判成什么阶段，为什么不一样"。

## 1. 双轨命名与视觉区分（硬约束）

| 轨 | 数据来自 | 标签文案 | 视觉 |
|---|---|---|---|
| 本轨 | `analytics.*` / `duanxian/pg_sources.py` | **`本地口径`** | 主色，正常字号 |
| qd 轨 | `qd.*` schema | **`quantdash 口径`** | 次级色 + 虚线边框或角标 `QD` |

三条不许：

1. **不许把 qd 轨的数值混进本轨的图表**（同一根柱子里一半来自 `analytics` 一半来自 `qd`）。要对比就并排两根，或同一张图里两条**不同线型**的序列 + 图例注明口径。
2. **不许只留一个数然后标"综合"**。任何"综合/加权/取较可信者"都是偷偷替用户做判断。
3. **不许把 `quantdash 口径` 简写成 `QD` 而不给悬浮解释**。首次出现的卡片要有一句：`逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），本项目仅重实现其算法口径，未复制源码`。这句话是法律要求也是诚实要求。

## 2. 情绪周期：三列阶段必须一起显示

`qd.sentiment_cycle` 有 **三个** stage 列（`stage_ui` / `stage_mcp` / `stage_feishu`），因为 quantdash 自己的页面、自己的 AI、自己的飞书机器人用的是**三套互相矛盾的阈值**。这不是我们的 bug，是要呈现的事实。

布局建议（阶段胶囊一行三个 + 本轨一个）：

```
2026-09-22 情绪周期
  本地口径        [ —  ]   本轨暂无阶段算法（见下）
  quantdash·页面  [ 试错 ]  命中条件：首板占比 62.3% ≥ 60
  quantdash·AI    [ 分歧 ]  命中条件：兜底（未命中任何阶段）
  quantdash·飞书  [ 分歧震荡 ]
  ⚠ 三者不一致 —— 原项目页面与 AI 读到的阶段本就不同
```

四条要求：

1. **每个阶段胶囊必须能展开"命中了哪一条"**。后端返回 `stage_reasons`（数组，≤3 条中文），前端原样显示。兜底 `分歧` 也要给理由（`"未命中任何阶段条件，兜底为分歧"`）—— quantdash 原版这里不给文案，用户看到"分歧"却不知道为什么。
2. **三者不一致时要有显式提示**，不要让用户自己比对。判断很简单：`stage_ui !== stage_mcp || stage_ui !== stage_feishu` → 显示 `⚠`。
3. **本轨目前没有阶段算法**（这是既有事实：YuanShan 的"情绪五档"只有 UI 没有计算）。所以本轨这一格**如实显示 `—` + `本轨暂无阶段算法`**，不要拿 qd 轨的值填进去冒充本轨。这条是 01 规范第一条的直接应用。
4. **`confidence` 必须改名显示**。后端字段叫 `confidence`，但它实际是"阶段基数 + 风险加成 + 量能加成"的确定性查表加和，**不是统计置信度**，理论最大 89。前端显示文案用 **`判定强度`**，并加悬浮说明：`非统计置信度：由阶段/风险/量能三项查表相加得出，最大 89`。直接显示"置信度 65%"会让人以为有 65% 的把握。

## 3. 连板天梯：并列显示，且必须暴露"数据没有"

qd 轨天梯的板级行**包含首板**（`board_tier=1`），本轨若没有对应行就留空，**不许把首板股并进"七板以上"**（quantdash 原版就是这么错的，D-12）。

每只股票的卡片字段与显示规则：

| 字段 | 显示 | 缺值时 |
|---|---|---|
| `board_count` | `N 板` | — |
| `pct_change_actual` | 真实涨幅 | 不许用代码前缀硬推的 10/20/30 冒充（D-14） |
| `limit_up_pct_regime` | 角标 `10%`/`20%`/`30%`/`5%` | — |
| `is_one_word` | 角标 `一字` | — |
| `is_turnover_board` | 角标 `换手` | **本轨扩展，quantdash 没有** → 角标要带 `+` 或 tooltip 注明"非 quantdash 口径，本项目按换手率 ≥5% 判定" |
| `first_seal_time` | `HH:MM` 首封 | **`seal_time_available=false` 时显示 `封板时间不可得`，不许显示 `--`** |
| `industry_name` / `concept_name` | 原值 | NULL → 整块不显示。**不许填 `市场热点` / `连板` / `Unknown`**（D-13） |

⚠️ **`seal_time_available` 是这一屏最重要的字段**。历史数据里只有 2026-09-22 一天有分钟线，其余全部无封板时间。quantdash 原版在这种情况下**用哈希函数编造一个封板时间**（`derivePseudoTime`，D-28），页面上看起来完全正常 —— 这正是"假数据比没数据危险"的典型案例。
我们的页面必须让"没有"这件事**在视觉上无法忽略**：整列灰底 + 顶部一条横幅：

```
⚠ 封板时间仅 2026-09-22 可得（本地分钟线只覆盖该日）。
  其余交易日的龙头并列时改按成交额排序，见「龙头是怎么选出来的」。
```

## 4. 龙头：把 tie-break 显示出来

`qd.board_ladder_leader.leader_tie_break` 四种取值，前端**必须**在龙头名字旁边给出对应文案：

| 值 | 文案 |
|---|---|
| `seal_time` | `按最早封板时间选出（quantdash 原口径）` |
| `amount_no_seal_time` | `⚠ 无封板时间，改按成交额最大选出 —— 与 quantdash 原口径不等价` |
| `code_asc` | `⚠ 成交额也并列，按代码升序取首个（确定性兜底，无业务含义）` |
| `no_candidate` | `当日无涨停，无龙头` |

第二、三种情况**必须视觉上降级**（灰色/斜体 + ⚠），因为那不是 quantdash 的口径，是我们为了不留空而做的确定性选择。
`leader_next_close_pct` 为 NULL 时，`leader_status_label` 会落在"高标抱团 / 一字观察 / 待次日确认"三个之一 —— 显示时补一句 `次日数据未到`，别让用户以为是判断结论。

## 5. 板块周期：两套东西不是同一个东西

**必须在页面标题或说明里点明**，否则用户会以为我们在重复实现：

| | 本轨 `analytics.ignition` | qd 轨 `qd.sector_persistence` |
|---|---|---|
| 是什么 | **五档生命周期阶段**：ignite / follow / coil / fade / none | **轮动矩阵 + 持续性统计**，**没有阶段枚举** |
| 核心字段 | `stage`, `score`, 五要素（squeeze/position/ignition/volume/breadth） | `streak_days`, `top_three_appearances`, `strength_delta_pct`, `strongest_repeat_name` |
| 回答的问题 | "这个板块处在启动的哪个阶段" | "领涨板块换了几手、谁连庄最久、强度在增还是减" |

布局：左右两栏或上下两块，**不要合并成一张表**。合并会迫使其中一套字段留空，看起来像数据缺失。

qd 轨轮动矩阵（最近 5 个交易日 × 每日前 8）显示要求：

1. 热度配色阈值照搬 quantdash：**涨幅 ≥4 深红 / ≥2 红 / ≥0 浅红 / <0 绿**。这是**纯涨幅配色，不含资金流** —— tooltip 要写明，否则用户会以为红色代表资金流入。
2. `strength_delta_pct` 为 NULL 时显示 **`新晋领涨`**（这是 quantdash 原本的 UI 语义，照搬）。NULL 在这里是有含义的，不是缺数据。
3. `top_three_appearances` 为 0 时显示 `0`，**不许显示 1**（quantdash 缺数据默认 1，D-21）。
4. `strongest_repeat_name` 为 NULL 时显示 `无连庄板块`，**不许回落成当前领涨板块**（D-22）。
5. 本轨的"统计板块"（`昨日涨停/昨日突涨/板块趋势/最近情绪指数`）按 01 规范第五条**打标不裁**：默认不展示但可用开关打开。qd 轨取样范围是"概念+行业前 24"，本身不含这类统计板块，两轨的板块总数不一样是**预期**的，要在角落注明分母（`本轨 230 个行业板块 / qd 轨 概念+行业涨幅前 24`）。

## 6. 信息采集：宁可空，不可假

| 情形 | 显示 | 禁止 |
|---|---|---|
| 该日无新闻 | `当日无采集到的资讯` | ❌ 回落 5 条逼真的示例新闻（quantdash 的 `MOCK_NEWS`，D-24，界面上无法与真实新闻区分） |
| 某股匹配不到新闻 | `无相关新闻` | ❌ 回退取最新 3 条无关新闻塞进去（quantdash 原行为） |
| `sentiment` 为 NULL | 不显示情感标签 | ❌ 显示"中性"（NULL 是"没做情感分析"，不是"中性"） |
| `published_at` 缺失 | 显示 `captured_at` 并标注 `采集时刻，非发布时刻` | ❌ 拿采集时刻当发布时刻显示（D-25） |

新闻卡片**必须同时显示 `published_at` 与 `captured_at`**（或至少悬浮可见），因为两者可能差很久。

## 7. AI 对接：页面上要能看出"AI 读的是哪一轨"

quantdash 的 STRUCTURED_DATA_RULES 第 6 条：**若数据将被 AI 使用，MCP 或服务层必须能读到同一份结构化结果**。
对应到页面：复盘报告里引用的每个数字，都要能点开看到它来自哪张表、哪一轨。

三条要求：

1. 复盘正文里的引用角标（YuanShan 已有的 `validate_answer` 引用机制）要**带上轨标签**：`[本地口径]` / `[quantdash 口径]`。AI 同时读到两轨时，如果它只引一轨而下结论，用户要能看出来。
2. 空值渲染统一成 **`—`**（照搬 quantdash 的 `{{var}}` 渲染规则：null/undefined/空串 → `—`）。**不许渲染成 `0`、`无`、或空字符串**。这条比看起来重要：`0` 会被 AI 当成真实数值继续推理。
3. Prompt 里那句风格约束要保留并可见：**"数据不足必须指出，不要补脑"**。建议在复盘页脚显示一行：`本项目要求 AI 在数据不足时明说，不做推测补齐`。这是给用户的承诺，也是给未来改 prompt 的人的锚。

**不搬的两样**（前端不要为它们留位置）：
- ❌ quantdash 的"验证结论"是对模型输出文本做**关键词正则分类**（`明显偏差|基本失效` → missed，兜底一律 partial），会把"没提到"和"部分成立"混为一谈。我们用 YuanShan 自己的引用校验。
- ❌ 游资 skills 不注册成引擎 skill（Vibe-Trading 那 90 个 skill 已被强制禁用，且会破坏 agent 启动）。quantdash 这套只是 **prompt 追加文本**，标题写明"优先级高于默认风格要求，但不能违背事实数据" —— 按这个方式做，落 `config.help_note`。

## 8. 接口契约（`/api/qd/*`，形状如下）

> 2026-09-24 终态裁定：这些端点与 `qd` 表**都是 YuanShan 自己的东西**，不存在"等谁提供"。
> 目前 `sentiment-cycle` 已由 `duanxian/qd_calc.py` 本地实算（响应带 `computed_by:"yuanshan"`），
> PG `qd` 表退为对照基准与兜底；尚未实算的项在界面上显示「本地计算尚未覆盖该项」。

| 端点 | 成功 | 不可用 |
|---|---|---|
| `GET /api/qd/sentiment-cycle?date=` | `{available:true, date, own:{...}\|null, qd:{stage_ui, stage_mcp, stage_feishu, stage_reasons[], confidence, risk_level, volume_state, metrics{...}, deviation[]}}` | `{available:false, date, reason}` |
| `GET /api/qd/board-ladder?date=` | `{available:true, date, tiers:[{board_tier, label, members[]}], leader:{..., leader_tie_break}, seal_time_available:bool, deviation[]}` | `{available:false, date, reason}` |
| `GET /api/qd/sector-cycle?date=` | `{available:true, date, rotation:[...], persistence:{...}, scope:"概念+行业 涨幅前 24", deviation[]}` | `{available:false, date, reason}` |

四条约定：

1. **`own` 与 `qd` 分两个键**，永不合并。`own` 为 `null` 表示本轨没有这个能力（如阶段算法），前端按 §2 第 3 条如实显示。
2. **`deviation[]` 必须原样透出并可展开**。它是"我们与 quantdash 原实现哪里不一样"的清单，形如 `[{code:"D-1", note:"原实现用线性模型伪造上涨家数，此处为本地日线实算"}]`。藏起来就等于让用户以为我们 1:1 复刻了。**后端没给 `deviation` 时显示"无法判断是否与 quantdash 原实现等价"**，不留白也不谎称 1:1。
3. `date` 缺省时后端取 `qd.sentiment_cycle` 的最新 `trade_date`，**不许回落到"今天"**（今天可能没收盘，也可能不是交易日）。
4. 失败一律 `{available:false, reason}`，**不许**返回上一个有数据的交易日冒充（01 规范第一条）。

### 8.1 `stage_reasons` 的形状（2026-09-24 由总协调裁定）

契约初版只写"数组，≤3 条中文"，没定归属，导致前端不知道该把哪条理由贴到哪个 stage 胶囊上。**定为**：

```json
"stage_reasons": [
  {"stage": "stage_ui",     "hit_rule": 5, "reason": "首板占比 62.3% ≥ 60 → 试错"},
  {"stage": "stage_mcp",    "hit_rule": 6, "reason": "未命中任何阶段条件，兜底为分歧"},
  {"stage": "stage_feishu", "reason": "默认分歧震荡"}
]
```

- `stage` 必须是 `stage_ui` / `stage_mcp` / `stage_feishu` 之一（列名，不是阶段中文名），前端按它分发到对应胶囊；
- `hit_rule` 是 §4.1 优先级表里的序号（可选，有则显示"命中第 N 条"）；
- 每个 stage 各自一条，**不许混成一个不分来源的字符串数组**；
- 后端没给或 `stage` 对不上时，前端在三个胶囊下合并显示一份原始列表并标注"未能归属到具体口径"，**不要猜**。


## 9. 前端硬约束（改之前先看）

沿用 01 规范第八条，另加两条本轮实测到的：

- `tsc` 开了 `noUnusedLocals` / `noUnusedParameters`，且 `tsc -b` **先于** `vite build` —— 一个未使用的 import 就会打断整个构建。2026-09-24 就因此让 `scripts/setup` 失败过一次（`AgentAccess.tsx` 的 `DEFAULT_ACCESS_PROVIDER`）。改完先 `cd frontend && npm run build` 再提交。
- `tsc -b` 是增量构建，会读 `.tsbuildinfo` 缓存。如果构建报了明明已经修好的错，先删 `frontend/**/*.tsbuildinfo` 再跑一次，别急着改代码。
- `frontend/test/*.test.ts` 里有 3 处用 `readFileSync` **断言组件源码字符串**（`dive-storage.test.ts`、`risk-status.test.ts`、`modules.test.ts`）：改 JSX 写法就可能误报。动这些组件前先跑 `node --test frontend/test/*.test.ts`。
