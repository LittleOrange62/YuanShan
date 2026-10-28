# 复盘 · quantdash 口径对照（`/qd-compare`）

> 本文件按《功能点说明》的模板逐格写：这一屏**有哪些功能点、每格显示什么、口径从哪来、缺数怎么显示、
> 哪句话是谁写的**。代码是唯一真值，本文件只指路（`文件:行`＋函数名），不复述代码逻辑。
> 数据现实类数字带实测日期（**2026-10-11 本机实测**，服务在 8910，同一发打两次取第二发，
> 整包落 `$TEMP/qdprobe/*.json` 后用 `.venv/Scripts/python.exe -X utf8` 读；基准场 2026-10-09，
> 10-10/10-11 非交易日）。没实测到的数字一律标「未实测」。
> **路径写法约定**：`qd_calc.py` / `qd_ladder.py` / `qd.py` / `qd_sector.py` 不带目录时一律指
> **读侧的 `duanxian/`**（屏幕上那些数出自它们）；写库侧一律写全 `app/compute/qd_*.py`。
> 这一族里有两份同名 `qd_ladder.py`/`qd_sector.py`，混着引用会指错地方。
> ⚠️ 这一屏的口径文字**不来自接口 `definitions`**（后端五个端点都没有这个键，见 §2.3），
> 所以凡"悬停原文"一栏给的都是**前端硬写的 Caliber/短标签**或**后端 `deviation[].note` 原句**，两类分得很清。

## 0. 坐标

| 问什么 | 答 |
|---|---|
| 导航位置 | 一级模块「复盘」→「quantdash 口径对照」（`frontend/src/lib/workspace/modules.ts:32`，复盘模块 `:29-33`；页面标题映射 `:74`） |
| 卡片入口 | 「数据与选股」组卡片 `to: "/qd-compare"`、标题「quantdash 口径对照」、副标题「两套算法并排，差异可见」（`frontend/src/lib/workspace/features.ts:32`）；同一张清单也渲染在系统配置页的卡片网格（`frontend/src/pages/System.tsx:118-120`）。路由在册由测试钉死：`frontend/test/workspace.test.ts:24`（`/qd-compare` 在 `expected` 清单里） |
| 路由 | 静态 import `frontend/src/router.tsx:11`，注册 `:50`（本项目没有 lazy/Suspense） |
| 页面组件 | `frontend/src/pages/QdCompare.tsx`（108 行，四块：情绪周期 / 连板天梯 / 板块周期 / 信息采集；外壳 `Section` `:100-108` = `GlassCard` + h2 + note） |
| 子组件 | `frontend/src/components/qd/`：`Track.tsx`（双轨外壳 `TrackSection`/`TrackTag`/`PanelState`/`DeviationList`/`StagePill`/`DistBars`/`ComputedLine`）、`SentimentCyclePanel.tsx`、`BoardLadderPanel.tsx`、`SectorCyclePanel.tsx`、`NewsPanel.tsx` |
| 取数层 | `frontend/src/lib/qd.ts`（类型 + 五个 fetch + `dash/num/pct/stageMismatch/stageReasons/computedNote` + 三块固定文案 `QD_PROVENANCE`/`TIE_BREAK_COPY`/`STAGE_COLUMNS`）、`frontend/src/lib/useQd.ts`（六个 hook） |
| 接口①（qd 轨情绪） | `GET /api/qd/sentiment-cycle?date=`（`vr/app.py:444`，`date` 无长度/范围校验，空=后端取该表最新场） |
| 接口②（qd 轨天梯） | `GET /api/qd/board-ladder?date=`（`vr/app.py:462`） |
| 接口③（qd 轨板块） | `GET /api/qd/sector-cycle?date=`（`vr/app.py:479`） |
| 接口④（区间分布） | `GET /api/qd/distribution?days=`（`vr/app.py:495`，**`days` 夹在 0~5000**，0=全表；实测 `?days=6000` → **422** `Input should be less than or equal to 5000`） |
| 接口⑤（本地快讯） | `GET /api/qd/news?date=&limit=`（`vr/app.py:513`，**`limit` 夹在 1~500**，默认 200；实测 `?limit=0`、`?limit=501` 都 **422**。前端 `fetchQdNews(date, limit=200)` 写死 200，`qd.ts:307-308` ⇒ 界面上没有调上限的入口） |
| 接口⑥（本轨板块，不是 qd 轨） | `GET /api/market/sector-cycle?date=`（`qd.ts:340-341` 走同一个 `qdGet`，是"本地口径"那一轨的入口，见 §4） |
| 视图函数 | `duanxian/qd.py`：`sentiment_cycle()` `:162`、`board_ladder()` `:220`、`sector_cycle()` `:305`、`distribution()` `:395`、`news()` `:463`；实算层 `duanxian/qd_calc.py`（情绪）、`duanxian/qd_ladder.py`（天梯/龙头/板高度） |
| 读什么表 | 兜底与对照基准：PG `qd` schema 九张表里的 `qd.sentiment_cycle`、`qd.board_ladder`、`qd.board_ladder_member`、`qd.board_ladder_leader`、`qd.board_height`、`qd.sector_rotation`、`qd.sector_persistence`（DDL `app/db/sql/12_qd_schema.sql`，列注释同文件 `COMMENT ON` 段 `:96` 起）。本地实算读 `market_data.stock_daily`/`index_daily`/`stock_minute`、`reference_data.security`/`sector_member`/`sector_info`/`trade_calendar`/`security_st_period`、`analytics.ignition`（本轨板块）。快讯读本地 jsonl（`data_path("news")`），**不读 PG `qd.news_item`**（实测该表 0 行，是预期：`duanxian/coverage.py:153-156` 的 `known_issue` 原句「PG 侧 0 行是**预期**」） |
| 谁写库 | 加工段 **e1** 的三步（清单 `ingest/jobs/e1_fill_all.py:179-181`，入口 `:594-599`，都带 `--recent <span>`）：`qd_ladder`→`ingest/jobs/qd2_ladder`、`qd_sentiment`→`qd1_sentiment`、`qd_sector`→`qd3_sector`；**顺序 ladder 必须在 sentiment 之前**（`:176-180` 注释：反了 sentiment 整批拒写）。另有 `/jobs` 手动作业三条 `calc_qd_ladder`/`calc_qd_sector`/`calc_qd_sentiment`（`app/db/sql/13_qd_jobs.sql:37-52`，`trigger='manual'`，依赖 `calc_qd_ladder → calc_qd_sentiment`）。参数三键落 `config.setting`（`app/db/sql/14_qd_settings.sql:19-33`），计算作业**硬只读**依赖（`app/compute/qd_common.py:59-80`，读不到直接抛 `KeyError`，不退回代码内默认值） |
| 系统配置自检在册 | **不在册**。`frontend/src/pages/System.tsx:50-70` 的 `CAPABILITIES` 清单 grep 无 `/qd/` 任何一条 —— 这一屏的五个端点都没有"从界面这一侧探测它在不在"的登记行（对照：`/quant/volume-double` 在册 `:57`） |
| 字段含义出处 | 库注释在 `app/db/sql/12_qd_schema.sql`（`:96` 表注释、`:107-177` 逐列注释；实测九张表的 `obj_description` 都含 PolyForm 字样）。**但 qd 轨五个端点都不交 `definitions`**（`duanxian/qd.py` 全文无 `_comments()` 调用，对照倍量形态那一屏 `duanxian/quant.py::_comments()`），所以**屏幕上没有一栏是从库注释原样搬来的**；界面上的口径文字只有三来源：① 后端 `deviation[].note` 原句、② 前端硬写的 `Caliber`/短标签（`qd.ts:406-419`、`SentimentCyclePanel.tsx:16-23/142-145`、`BoardLadderPanel.tsx:22-33/186`、`SectorCyclePanel.tsx:26-32/169`）、③ 后端 `scope`/`basis`/`reason` 这类整句 |

一句话定位：这一屏存在的理由是**差异本身**（文件头注释 `QdCompare.tsx:15-21`）—— 同一交易日，两套算法各判成什么、
为什么不一样。所以这一屏**没有任何"综合"值**，本轨没覆盖的项就显示"本轨暂无"，绝不拿 qd 的值顶上。
展示契约全文：`docs/页面展示工作指南/02-quantdash逻辑双轨展示.md`（§1 双轨命名、§2 三列 stage、§3 封板时间、
§4 tie-break、§5 两套板块周期、§6 快讯、§8 接口形状）；算法契约：`docs/数据加工工作指南/09-quantdash逻辑规格.md`。

⚠️ **与首页的关系（别按旧文档找）**：用户十条一批里的第 **5** 条
（`docs/页面展示工作指南/04-新增页面落地进度.md:396`：「首页去掉 quantdash 口径对照那块」）摘掉的是
首页那块「情绪周期」（三列 stage 并排），**摘的是重复的展示、不是可达性**，入口留在首页那条链接上
（`frontend/src/pages/SummaryBoard.tsx:345`）。首页代码里那块的位置只剩注释
（`SummaryBoard.tsx:60-65`「2026-10-06 起不在首页了，所以这里也不再取它 —— 取而不用等于每天白打一次全库排名」、
`:262-265` 整段注释）。测试跟着降到 5 块：`frontend/test/stalenessCoverage.test.ts:28-32`
（原句点名「首页的『情绪周期』那一块整块摘掉 → 六块变五块」）。
**但首页今天仍在两处显示 qd 轨数值**（见 §6.5）。

---

## 1. 页头（`QdCompare.tsx:43-57`）

| 功能点 | 显示/行为 | 代码锚点 | 注意 |
|---|---|---|---|
| 标题 | 「quantdash 口径对照」`PageHeader level={1}` | `:43` | 与导航文字逐字一致（`modules.ts:32`） |
| 副标题 | 「两套算法并排给出，差异可见、来源可查。不做综合、不加权、不替谁更可信。」 | `:44` | 这句是**本屏的宪法**：任何"取较可信者"的改动都违反它（契约 02 §1 第 2 条） |
| 「交易日」日期框 | `type="date"`，**默认空串**（`useState("")` `:25`） | `:47-51` | 空=不传 `date`，由后端各表取自己的最新 `trade_date`。契约 02 §8 第 3 条 + `useQd.ts:5-11` 明令**不许前端预填"今天"**（今天可能没收盘、也可能不是交易日）。填了非交易日 → 四块全部走 `available:false` 分支（实测 2026-10-10 三条 qd 端点都给 `available:false` + 原因，见 §2.2） |
| 「重新取数」 | `setNonce(n => n+1)` → 六个 hook 全部重置为 `loading` 再重发 | `:52-55`，`useQd.ts:14-20`、`useDistribution` `:44-52` | 区间分布不跟日期、只跟 nonce（`:33` 注释「区间分布是整段历史的统计」）；`fetchQdNews` 的 loader 必须是**模块级常量** `newsLoader`（`useQd.ts:32-37`），写成箭头函数进依赖数组会永远停在"取数中…" |
| 「当前生效交易日：YYYY-MM-DD」 | `effective = date \|\| shown[0]`，`shown` 取 `[sentiment, ladder, sector, ownSector]` 四个 payload 的 `date` | `:36-39`、`:59-62` | ⚠️ 只取**第一发非空**的日期，也就是**情绪周期那一块的日期**。而 `available:false` 的 payload 也带 `date`（`qd.py:73-80`），实测选 2026-10-10 时顶部照样写「当前生效交易日：2026-10-10」，下面四块却全是"本屏无数据" —— 顶部那句**不保证那一块有数**；未指定日期时后面那句括号已经把风险说了：「（未指定 → 由后端取各表最新 trade_date，不同表可能落在不同日期）」 |
| 页脚免责 | `<Disclaimer compact />` | `:92` | — |
| 页脚承诺句 | 「本项目要求 AI 在数据不足时明说，不做推测补齐；本页同理 —— 取不到就显示取不到，不拿上一场的数、不填 0、不猜。」（经 `plain()` 剥粗体星号） | `:93-95`，`plain()` `frontend/src/lib/agent.ts:62-66` | 契约 02 §7 第 3 条要求保留并可见的那句 |

四个 `Section` 的 note 是**原样硬写在页面文件里的中文句子**（`:65`、`:69`、`:73`、`:77`），后端不交、也不该由后端交；
每一块下面先抄那句原话，再讲格子里读什么。
⚠️ **全页只有一张可排序的表**：板块周期·本轨那 9 个 `SortHeader`（`SectorCyclePanel.tsx:87-96`，见 §4.1）。
情绪块（胶囊 + 读数 + 网格）、天梯块（卡片）、快讯块（列表）**都没有排序键**，
所以"逐列点击实测排序"这件事在本屏只覆盖 §4.1 那一处；其余格子的实测方式是"点开悬停/展开体读原句"。

---

## 2. 第一块「情绪周期」

`Section note` 原句（`QdCompare.tsx:65`）：

> 「情绪周期 —— quantdash 自己的页面 / AI / 飞书用三套矛盾阈值，三列必须一起看。本轨暂无阶段算法。」

组件：`frontend/src/components/qd/SentimentCyclePanel.tsx`。

### 2.1 本轨那一节（`TrackSection track="own"` `:51-61`，`provenance={false}`）

| 格子 | 读哪个字段 | 口径出处 | 缺数显示 | 悬停/说明原文 |
|---|---|---|---|---|
| 阶段胶囊 | `data.own.stage`（`own` 整体是 `null`） | 后端明写：`sentiment_cycle()` 两条分支都固定交 `"own": None`（`duanxian/qd.py:127`、`:199`；`vr/app.py:447-448` docstring 原句「`own` 恒为 null —— 本轨没有情绪阶段算法，"情绪五档"只有 UI 没有计算」） | 虚线框 + `—` | 旁边灰字：`own` 存在时「本轨已给出阶段值（口径见 deviation）」，否则「**本轨暂无阶段算法**」（`:58`） |
| hint | — | 前端写的（`:52`） | — | 「本轨（analytics.*）目前只有指标，没有把指标映射成阶段的确定性算法。」 |

实测 2026-10-11：屏幕上就是 `—` + 「本轨暂无阶段算法」，接口 `own` = `None`。

### 2.2 qd 轨那一节（`TrackSection track="qd"` `:63-119`，带来源声明）

面板内读数与顺序（自上而下）：

| 顺序 | 显示 | 值从哪来 | 实测 2026-10-11（最新场 2026-10-09） |
|---|---|---|---|
| 1 | `ComputedLine` 一行灰字 | `computedNote(env)`（`qd.ts:72-79`）按 `computed_by` 三种取值出三种文案；**没有该键时不默认成本项目算的** | 「本屏数值由本项目计算（PG 的 qd 表只作对照基准与兜底）」（`computed_by="yuanshan"`，来自 `qd.py:139`） |
| 2 | 「阶段判定口径」+ `Caliber`「口径」按钮 | 前端硬写 `STAGE_CALIBER`（`:16-23`） | 点开七行：三列分别来自"网页端阈值 / 喂给自家 AI 的 MCP 阈值 / 飞书机器人阈值"，末两句「判定强度 = 阶段基数 + 风险加成 + 量能加成 的确定性查表加和，理论最大 89；⚠️ 它不是统计置信度，「判定强度 65」不代表 65% 的把握。」 |
| 3 | 三个 `StagePill`（`STAGE_COLUMNS` `qd.ts:367-371`） | `qd.stage_ui` / `stage_mcp` / `stage_feishu` | 「quantdash·页面」= **修复**，「quantdash·AI」= **修复**，「quantdash·飞书」= **分歧震荡** |
| 4 | 不一致才出的黄底句 | `stageMismatch()`（`qd.ts:361-365`，非空列去重后 >1 即算不一致） | 出：「⚠ 三者不一致 —— 原项目页面与 AI 读到的阶段本就不同。这里不做取舍，全部摆出来。」（ui=mcp≠feishu ⇒ 正在屏幕上） |
| 5 | 判定强度大数 | `qd.confidence`，`num(value, 0)` | 「**77** / 89 判定强度」 |
| 6 | 「风险档」/「量能状态」 | `dash(qd.risk_level)`、`dash(qd.volume_state)` | 「medium / 风险档」、「持续放量 / 量能状态」（值交原样英文码，不翻译） |
| 7 | 「判定所用指标」网格 | `qd.metrics` 的**全部键**（`Object.entries` 顺序=后端顺序，前端不排序不筛） | **28 个键**全出（`qd_calc.py:484-500` 的字典，PG 分支则是 `qd.py:31-41` 的 `_METRIC_COLS` 28 列）；实测 4 个键为 `null` ⇒ 显示 `—`：`success_rate_pct`、`avg_board_gain`、`premium_score`、`broken_rate_sealed_pct` |
| 8 | 「与 quantdash 原实现的差异 N 条」折叠 | `DeviationList`（`Track.tsx:79-109`） | 实测 **12 条**（默认收起，`aria-expanded`） |
| 9 | 区间分布（见 §2.4） | 另一个端点 `/api/qd/distribution` | 实测 1255 场 |

`StagePill`（`Track.tsx:112-145`）的功能点：点胶囊展开「命中条件：…」，理由从 `qd.stage_reasons` 按
`stageReasons()`（`qd.ts:382-404`）分发 —— 库里/契约用的键是 **`stage`**（列名，不是阶段中文名），
后端若写成 `column` 也收；**按位置猜归属那条路径已删除**（`:379-381` 注释）。实测交 3 条、`hit_rule` 两条为
`3`、飞书那条没有 `hit_rule`（`qd_calc.py:472-477`），三句 reason 尾巴都带同一串事实（`facts` `:468-471`）：
「风险 medium、最高板 9、涨停 72 家、首板占比 83.3%、龙头板数 9、高板 4 家、炸板修复率(T-1) 51.2%、龙头次日收盘 10.01%、量能 持续放量」。
stage 为 `null`/空串时胶囊是**虚线框 + `—`**，下面补一行 `emptyText`（默认「该列为 NULL」）。
胶囊可点 iff `has && reason`（`disabled={has && !reason}`）—— 没给命中条件时点不动，展开体写的是
「后端未给出命中条件（契约 §8 要求 stage_reasons 带 column）」（`:139`）。

`metrics` 网格的取数与着色规则（`:101-110`）：键名原样（`font-mono`、`title={k}` 悬停给键名本身），
值 `typeof v === "number"` 才走 `num(v, Math.abs(v) < 1 ? 3 : 2)`，否则 `dash(v)`；
**只有绝对值 <1 的比率才给 3 位**（`qd.ts:351-353` 的 `num` 由调用侧决定位宽：`Math.abs(v) < 1 ? 3 : 2`）——
实测这一发 28 个键里**没有一个小数档位 <1 的数值**，所以 3 位那档本轮**未实测到**（`broken_repair_rate_lag1_pct=51.2`
走两位；`premium_score`、`success_rate_pct`、`avg_board_gain`、`broken_rate_sealed_pct` 四格是 `—`）。
`qd.ts:344-358` 三个出口函数是这一屏所有"空"的唯一收口：`dash`（空串也算空）、`num`、`pct`
（**只有 `Number.isFinite` 才格式化**，NaN/Infinity 一律 `—`，不悄悄变 0）。

`Caliber`（`:99`，前端硬写）逐字：「字段名里带 `_pct` 的是百分比、带 `_rate` 的是比率（分母已写进字段名，见 01 规范第四条）。
NULL 与 0 是两件事：这里显示 — 的是 NULL。」—— 出处真的在 `qd_calc.py:191-199`（`_broken_rate` 的分母是**触板数**，
docstring 原句点名"字段名带 `touched` 就是为了不许和 `broken_rate_sealed` 互换"）。

### 2.3 caveats 全码表（这一屏没有 `caveats[]`，只有 `deviation[]`）

⚠️ **结构差异要先说清**：倍量形态那一屏的告警走后端 `caveats[]`（`{code,text}`），
这一屏**一个 `caveats` 键都没有**（实测四个 qd 端点的顶层键里都没有），
对应机制是 **`deviation[]`**（`{code,note}`），且 `qd.sentiment` 那一条是
**从 PG 基准表读回来的**（`_deviation_of("qd.sentiment_cycle", date)`，`qd.py:156-159`），不是本地实算现生成的 ——
屏幕同一张卡上"数值由本项目算 / 差异清单读基准表"两件事并存，这是事实。

`code` 的命名规矩住在 `app/compute/qd_common.py:14-22`（模块头注释）：
`D-*` = quantdash 那 42 处伪造点**未被复现**；`L-*` = 本轨与原口径的已知差异/本机缺输入的局限（"不是缺陷，不许和 D-* 混用"）。
实测 2026-10-11 情绪那块 12 条码（原句长，逐条只给触发条件与作者）：

| code | 这句话在说什么（摘要） | 谁写的（住所） |
|---|---|---|
| `L-3` | T+1 尚未入库 ⇒ 晋级率四列留 NULL | 基准表逐行 deviation；规则见 `qd_common.py:19` |
| `L-12` | 契约写"不舍入"但列是 `numeric(6,3)` | 同上 |
| `D-3` | 日线推不出"曾封板后炸开"，分子只能用**触板未封**近似；分母是触板数，与东财炸板池口径差约 64%（实测 09-22 36.3 vs 22.2）。⚠️ 交回来的原句里是 `64%%`（双百分号，SQL 字面量残留），屏幕上就显示 `64%%` | `app/compute/qd_sentiment.py` 写入；解释住 `qd_calc.py:191-199` |
| `D-37` | 两市成交额=上证+深成指，不含北交所与深市非成分股，**不等于全 A 成交额** | `qd_calc.py:146-159` |
| `Q-fix-1` | 2026-09-25 勘误：本列曾按 `amount_change_pct = T 对 T-2` 落库，现按 T 对 T-1；受影响 7 列。**编号刻意不占 `D-*`**（"那族是 quantdash 的缺陷未被复现，这是本实现自己的勘误"） | `docs/数据加工工作指南/09-quantdash逻辑规格.md:584` 勘误 5 |
| `L-19` | `risk_level` 的炸板率输入用**触板口径**，不是契约字面写的 `(1-晋级率)×100` | `09 号文档:470-505` 勘误 1 |
| `D-43` | 阶段分布严重偏斜是**照搬优先级链的必然结果**，不是本实现算错；句内写死「实测 1169 天：stage_ui 退潮 936 … 冰点 0 次」 | 表注释 + 每行固定 deviation（实测 1255/1255 行都带 `D-43`）；文案住所 `09 号文档:570-583` |
| `D-4` | quantdash 判阶段时混用交易日；本行把 T-1 场次一律命名 `_lag1` | `qd_calc.py:404-433` |
| `D-5` | 缺失判据按"条件不成立"处理，不用 0/-99 哨兵 | `qd_calc.py:239` |
| `D-9` | 兜底分歧也给理由文案（quantdash 不给） | `qd_calc.py:472-477` |
| `L-10` | 三套阈值给出三个阶段 —— 如实呈现不取平均；飞书版契约只三条硬条件，冰点/试错无判据可依 | `qd_calc.py:267-277` |
| `D-8` | 判定强度是确定性查表加和、非统计置信度；去掉恒不触发的 `min(95,·)` 死上限 | `qd_calc.py:280-289` |

天梯那块 9 条（`D-12/13/14/15/16/17/18/19/28`，清单住所 `duanxian/qd_ladder.py:22-36`）、
板块那块 2 条（`L-17/L-16`，基准表 `qd.sector_persistence.deviation`）、
快讯每条 3 条（`SRC-1/SRC-2/SINA-T`，`duanxian/newswire.py`）。
`DeviationList` 空数组时**不谎称 1:1**，交的是原句：
「差异清单：后端未给出 `deviation[]`（无法判断是否与 quantdash 原实现等价）」（`Track.tsx:84-87`）。

**`definitions` 键清单：这一屏没有这个机制。** 五个 qd 端点都不交 `definitions`，
所以屏幕没有"字段含义 = 库注释原文"那一栏（对照 `docs/功能点说明/量化选股/倍量形态.md` §2.1）。
键名列表可查的只有两处：① `qd.metrics` 交出 28 个键（`qd.py:31-41` `_METRIC_COLS` 原文，
`amount_yi`/`amount_change_pct`/`rise_count`/`fall_count`/`flat_count` 全在里面）；
② 库注释住 `app/db/sql/12_qd_schema.sql:96-177`。二者**没有一条被接口当数据交出** ⇒
文档与屏幕都只能说"后端没交，页面不自己编"。

### 2.4 区间分布（`DistributionBlock` `:147-182`，共用 `DistBars` `Track.tsx:155-197`）

这块的存在理由（`qd.ts:252-256` 注释原句）：「只看某一天，"天天退潮"像故障；把上千天摊开才看得见
quantdash 优先级链自身的偏置。界面只呈现分布与优先级顺序，**不改判定、不做取舍**。」

| 元素 | 读哪个字段 | 口径出处 | 实测值（2026-10-11，`days=0`=全表） |
|---|---|---|---|
| 头部一句 | `data.days`、`from_date`、`to_date`，取 `stage_distribution.stage_ui` 里**计数最大的那一档**（`Object.entries(...).sort()[0]`，`:156`）+ `sample_total.stage` | 后端只交分布；**"取第一名当代表"是前端自己算的**（后端没交 `top_stage` 之类） | 「近 **1255** 个已判定交易日（2021-08-02 ~ 2026-10-09）里，quantdash·页面 有 **846 场判为「退潮」**（占 67.4%）。下面的序号是优先级顺序：排前面的档会截走后面的档。」⚠️ 句子写"**近** N 个"，而默认 `days=0` 是**全表**（`vr/app.py:499` 原句「`days=0` = 全表」），这一屏没有日期框能把它变成"近" |
| 三条分布条（`quantdash·页面 / ·AI / ·飞书` 各一条） | `stage_priority[col]`（顺序）、`stage_distribution[col]`（计数）、`never_seen_stages[col]`、`sample_total.stage` | 优先级顺序**住在后端**：`qd.py:385-389` `STAGE_PRIORITY`，注释原句「顺序与取值抄自 `qd_calc.py:247-277`，不是从这里推的」 | 页面/AI：退潮 846 / 冰点 1 / 修复 208 / 主升 26 / 试错 173 / 分歧 1（和为 1255，**没有"（未判定）"档**）；飞书：退潮 783 / 修复 0 / 主升 0 / 分歧震荡 472。`never_seen`：AI 的**冰点**、飞书的**修复与主升** ⇒ 那几行尾挂「从未命中」 |
| 每行左序号 | `i+1`（前端行序） | 悬停（`title`）：「优先级（1 = 最先命中，后面的档被它挡住）」（`Track.tsx:178`） | 退潮永远第 1 —— 这正是这块要给人看见的东西 |
| 每行右读数 | `n 场 · xx.x%`，`n===0` 时灰字且不画条 | `DistBars`（`:171-190`）：**零值档照样占一行**，注释原句「"冰点一次都没出现"是结论，不是缺失」；`neverSeen` 命中才补「从未命中」 | 见上一行 |
| 底色含义 | `bg-primary/60`，宽度 `Math.max(1, Math.round(pctv))%` | `:153` 注释原句「颜色只表"存在与否"，不表好坏（分布不是涨跌）」 | 无涨跌色 |
| `Caliber`「口径」 | 前端硬写 `DIST_CALIBER`（`:142-145`） | 逐字：「按后端给出的优先级顺序排列（1 = 最先命中）。零值档显式保留：某档 0 次是结论，不是缺数据。**本实现未改动 quantdash 的判定顺序，所以「退潮占大头 / 冰点从未命中」要按它原逻辑理解。**」 | 界面上没有"已修正"字样，也不许有（见 §7 第 5 条） |
| 末行 | `dash(data.basis)` + `observed_sources` | 后端 `qd.py:457`/`:458` | 「统计口径来源：qd.sentiment_cycle / qd.board_ladder_leader（PG 基准表，逐场统计） · source 标记 quantdash-logic」⚠️ **这块统计的是 PG 基准表**，不是上面那张卡的本地实算结果；`observed_sources` 只有 `quantdash-logic` 一种，而实时端点交的 `source` 是 `quantdash-logic+local` |

`PanelState`（`Track.tsx:204-235`）四种状态视觉不可合并：`取数中…` / 黄底"接口异常"（含连不到后端那句
「连不到后端（127.0.0.1:8910）。先启动服务再看这一屏。」`qd.ts:221`）/
灰底虚线"本屏无数据 + 后端给出原因"（`available:false`）/ 正常。
`available:false` **但没带 `reason`** 时加粗报契约违规：「（缺失 —— available=false 未带 reason，违反展示规范 01 第一条，请反馈）」。
HTTP 404 单独一句：「接口未提供（HTTP 404：…）。本地计算尚未覆盖该项，所以这里没有数。」（`qd.ts:229-231`）。

---

## 3. 第二块「连板天梯」

`Section note` 原句（`QdCompare.tsx:69`）：

> 「连板天梯 —— qd 轨含首板分档；封板时间不可得的交易日会整列灰底，不像 quantdash 原版那样哈希编一个。」

组件：`frontend/src/components/qd/BoardLadderPanel.tsx`。这一屏的"重头戏"是**把"数据没有"显示出来**
（`:10-16` 注释原句），不是好看。

### 3.1 横幅与本轨那一节

| 功能点 | 显示/行为 | 注意 |
|---|---|---|
| 封板时间横幅 | `seal_time_available !== true` 时出，整段 `whitespace-pre-line` 三行（`SEAL_BANNER` `:22-25`）：「⚠ 本场次封板时间不可得。本地分钟线只覆盖极少数交易日，其余全部没有封板时刻；quantdash 原版在这种情况下用代码哈希编一个时间出来，我们不编 —— 并列的龙头改按成交额选出，这与它的原口径不等价，见「龙头是怎么选出来的」与下方区间分布。」 | ⚠️ **不写死"仅某天可得"**（`:18-21` 注释原句「写死的日期过两天就成了假话」）。实测 2026-10-11 最新场 2026-10-09：横幅**在**（`seal_time_available=false`，72 只成员里 0 只有 `first_seal_time`）；而 2026-09-30 / 09-22 两场实测 `seal_time_available=true`、成员各 52/65 只**全部**有首封时间 ⇒ 横幅会自己消失 |
| 判据 | 屏级 `seal_time_available` = 「当日有任何一条真封板时间才算可用」（`qd.py:282`，本地分支 `qd_ladder.py:160`） | 逐行另有一份 `seal_time_available`，给了就以行内为准（`MemberCard` `:108`；DDL 是逐行列，`qd.ts:99-100` 注释） |
| 本轨那一节（`track="own"` `:90-96`，`provenance={false}`） | 两条链接：`/`「首页 · 盘面数据（昨日连板股今日表现，含首封时间）」、`/first-board`「首板分析」 | hint 原句钉死为什么不并表：「本地口径的梯队按「前一交易日收盘板数」固定分组看今日表现，与 qd 轨的「今日收盘板数分档」不是同一件事，因此不在这里合并成一张表。」⚠️ 链接文案写「盘面数据」，首页那一块现在的标题是「盘面数据 · 涨停与连板（含个股）」（`SummaryBoard.tsx:255`）—— 指的路是对的，措辞是旧的 |

### 3.2 qd 轨的板级梯队（`:52-88`，带来源声明）

| 元素 | 读哪个字段 | 口径出处 | 缺数/边界显示 | 实测 2026-10-11（2026-10-09） |
|---|---|---|---|---|
| 卡片标题 | `板级梯队（含首板，共 ${tiers.length} 档 / ${各档 members 之和} 只）` | 前端算的行数，后端同时交 `stock_count` | — | 「共 7 档 / 72 只」 |
| hint | 「板级行含 board_tier=1 的首板。本轨没有对应行就留空 —— 不会把首板并进「七板以上」（quantdash 原版这么错过，见 D-12）。」 | 前端写的；`D-12` 真身在 `qd_ladder.py:22-24` | — | 在 |
| `ComputedLine` | `computed_by` | `qd.py:152` 固定 `yuanshan` | 兜底分支没有该键 ⇒ 出「后端未标注 computed_by…」。**实测兜底分支可达**：`?date=2021-08-02` 时本地实算返回 `None`（那一天 `stock_daily` 有 12,839 行但 `limit_up` **全空**，`limit_up` 非空的最早场次实测是 **2021-08-03** ⇒ `qd_calc.py:363` 的 `if date not in members: return None`），于是读基准表，屏幕上这一行变成「后端未标注 computed_by —— 无法判断这屏的数是本项目算的还是读表得来的」 |
| 档行 | `board_tier`（`null`→`—`）、`tier_label ?? label`（两条分支键名不同：本地 `tier_label`、PG 分支重命名成 `label`，`qd.py:285` vs `qd_ladder.py:152`） | 分桶上限 `TIER_TOP=7`（`app/compute/qd_common.py:40`，7 档=「七板以上」），档位标签表 `qd_ladder.py:19-20` | `tier=7` 那档把 `board_count>=7` 的**全部**塞进去（`:151`），所以标签「七板以上」下会出现 9 板成员（实测 600825.SH 新华传媒 `board_count=9`） | 七板以上 1 / 六连板 0 / 五连板 2 / 四连板 1 / 三连板 0 / 二连板 8 / 首板 60 |
| 档只数 | `tier.stock_count ?? members.length`；`stock_count !== members.length` 时补「（本屏只列出 N 只）」 | 后端 `stock_count` | **0 只的档照样出一行**（`:55` 空数组分支只在整个 `tiers` 为空时走），成员为空时那行写「该档无成员。」（`:74`） | 两者相等，补句未出现 |
| 最高档徽标 | `is_highest_tier` → 实底 `bg-primary`，否则浅色 | 后端逐档算（`qd_ladder.py:153`） | — | 只有「7 板」那一档是实底 |
| `tiers` 为空 | 「后端给了空 `tiers`：当日无涨停，或该日尚未入库。」（`:56`） | 后端两种情形都交空数组，**不区分** | 这不是 `available:false` 分支（接口仍 `available:true`），页面也不假装知道是哪种 | — |

成员卡（`MemberCard` `:103-157`，**不是表格、没有 SortHeader、不可排序**），逐格：

| 格 | 读哪个字段 | 口径出处 | 缺数显示 | 悬停原文 | 着色/灰显 |
|---|---|---|---|---|---|
| 名称 | `stock_name ?? name` | `qd_ladder.py:132` 取 `reference_data.security.name`（**当前名称，不回溯**：`qd_common.py:22` 的 `L-6`） | `dash()` → `—` | 无 | 常规 |
| 代码 | `security_code ?? code` | DDL 列名；别名规矩见 `qd.ts:81-103` 注释（2026-09-24 实查列名，两种都明确命名，不是猜） | `—` | 无 | `font-mono` 灰 |
| 板数 | `board_count` + " 板" | 连板推进只住 `qd_calc.py:91-102`（`_board_streaks`：上一日不在板上就重置） | `null` → `—`（**不是 0 板**） | 无 | 粗体右对齐 |
| 涨幅 | `pct_change_actual` → `pct()` | 当日真实涨幅；`D-14` 明写"不按代码前缀硬推 10/20/30"（`qd_ladder.py:24`） | `null` → `—`，且 `pctColor(null)` = `FLAT_TEXT` | 无 | 正=红（`text-danger`）/负=绿（`text-success`）/0 与空=灰（`frontend/src/lib/colors.ts:22-25`） |
| 「一字」角标 | `is_one_word === true` | 四价相等容差 `ONE_WORD_TOL=0.001`（`qd_common.py:43`，判据在 `SQL_DAY` `:135-139`） | 非 true 就**整个角标不出现**（不是显示"否"） | 无 | 灰底 |
| 档位角标 | `limit_up_pct_regime` + "%" | 由**当日**涨停价与前收盘反推（`qd_common.py:124-130` 注释：不再用 `board_limit_ratio` 兜底，"拿当前快照补历史正是本轮修掉的病根"） | `null` → 角标不出现 | 「该股适用的涨停幅度制度（10% / 20% / 30% / 5%），**不是涨幅**」（`:113`） | 灰底 |
| 「换手+」角标 | `is_turnover_board === true` | **本轨扩展，quantdash 全库无此概念**，阈值 `TURNOVER_BOARD_PCT=5.0`（`duanxian/qd_ladder.py:17`；参数在 `config.setting` 的 `qd_turnover_board_pct`，`14_qd_settings.sql:24-27`） | `null`（换手率缺）→ 不出现，见 `duanxian/qd_ladder.py:140` | 「非 quantdash 口径：本项目按换手率 ≥5% 判定」（`:118`） | **虚线边框 + 浅主色**（刻意与上面两个实底角标区分）；实测 72 只里 47 只为 true、`is_one_word` 3 只 |
| 首封 | `first_seal_time` | `SQL_POOL` 的分钟线 LATERAL（`qd_common.py:196-204`，注释原句「本机分钟线只有 2026-09-22 一天，其余日期这里必然是 NULL。quantdash 在同样情况下用哈希函数编造时分秒（D-28）—— 不跟」） | 行级不可得时**整格灰底**并写「**封板时间不可得**」（`:141-147`）—— 契约 02 §3 明令「不许显示 `--`」 | 只有原生 `title`（这一格没有单独悬停句） | 不可得：`bg-muted text-muted-foreground/70`；可得：常规字色 + `dash()` |
| 行业/概念 | `industry_name`、`concept_name` | `L-5`（取通达信三级行业里成员数最少者，`qd_common.py:182-195`）与 `L-4`（**`concept_name` 恒 NULL**：一只票挂 5~57 个概念，单值列承载不了，`qd_common.py:20`） | **两者都空 ⇒ 整块不渲染**；`D-13` 明写不许填「市场热点」「连板」「Unknown」 | 无 | 顶部虚线分隔，灰 | 实测 72 只全部有 `industry_name`、`concept_name` **0 只非空**（与 `L-4` 一致） |
| 后端交了但**屏幕上没有**的字段 | 屏级 `board_height`（9 个键：主板/次高/创业板/科创板/北交所最高板 + 各自名单，`qd.py:263-268`/`qd_ladder.py:159`）、成员级 `price_close`/`amount_yuan`/`turnover_rate_pct`（`qd.py:245-249`） | — | — | — | ⚠️ `grep -rn board_height frontend/src` **零命中** ⇒ 整块板高度读数这一屏不显示（首页也不显示）。别把它当"页面坏了"，也别在文档里声称这一屏能看到分市场最高板 |

### 3.3 龙头那一行（`Leader` `:194-223`）

| 元素 | 读哪个字段 | 实测值（2026-10-09） | 文案住所 |
|---|---|---|---|
| 后端没交 `leader` | 「后端未给 `leader`（不是"当日无龙头"，是这一项没算出来）。」（`:196`） | 本次有交 | 前端 |
| 名字/代码/板数 | `leader_stock_name ?? name`、`leader_security_code ?? code`、`leader_board_count ?? board_count` | 新华传媒 / 600825.SH / 9 板 | 后端 `duanxian/qd_ladder.py:198-199`（`_leader()` 返回字典，函数起 `:166`） |
| 状态标签 | `leader_status_label` | 「一字观察」 | 后端 `_status()`（`qd_ladder.py:195`） |
| 「龙头是怎么选出来的：」 | `leader_tie_break` 查 `TIE_BREAK_COPY`（`qd.ts:407-416`），未登记的取值走 `TIE_BREAK_UNKNOWN`「⚠ 未知的 tie-break 取值，不能替你猜它对应哪种选法」并在后面附「（后端值：`<原值>`）」 | 「⚠ 无封板时间，改按成交额最大选出 —— 与 quantdash 原口径不等价」 | 四档文案 = 契约 02 §4 的表格（`:79-82`），选法判据 = `qd_ladder.py:166-179` |
| 降级样式 | `degraded: true` 的那三档：整块 `opacity-70`、名字与文案 `italic`（`:201`、`:204`、`:211`） | 正在降级 | 契约 02 §4 第 2 段「必须视觉上降级」 |
| 次日数据没到 | `leader_next_close_pct == null` 时补「次日数据未到 —— 上面那个状态标签还不是判断结论。」（`:216-220`） | 实测 `leader_next_open_pct`/`leader_next_close_pct` 都是 `null` ⇒ **这一句正在屏幕上** | 前端 |

### 3.4 「龙头是怎么选出来的 · 区间分布」（`TieBreakDistribution` `:165-192`）

| 元素 | 读哪个字段 | 实测（全表，1255 场） |
|---|---|---|
| 头部一句 | `sample_total.tie_break`、`tie_break_distribution.seal_time`、`.amount_no_seal_time` | 「近 1255 场里，按 quantdash 原口径（最早封板时间）选出龙头的只有 **6 场**，按成交额重选的有 **1248 场**（**99.4%**）。**这不是过渡状态**：分钟线不补齐，这个比例就不会变。」 |
| 四行条 | `order=Object.keys(TIE_SHORT)`（前端固定枚举 `:28-33`）：`seal_time（原口径）` 6 场 0.5% / `amount_no_seal_time（按额重选）` 1248 场 99.4% / `code_asc（代码兜底）` **0 场** / `no_candidate（无涨停）` **1 场 0.1%** | ⚠️ 枚举顺序是前端自己列的，不是后端 `stage_priority` 那种"优先级"；后两档 0/1 场与项目记录一致（`09 号文档:507-527` 勘误 2 曾测得 `code_asc`/`no_candidate` 各 0，样本 1169 场；本轮 1255 场里 `no_candidate` 出现了 1 场 —— 那句"全区间无零涨停日"因此已不成立） |
| `Caliber` | 前端硬写（`:186`）：「四种选法的含义与降级规则见契约 02 §4。seal_time 才是 quantdash 原口径；其余三种是本实现为了不留空做的确定性选择。」 | 屏幕上「原口径」字样**就在这里挂着**，共 6 处（`BoardLadderPanel.tsx:25/29/176/186` + `qd.ts:408/410`），**只有 `seal_time` 那一档真的对得上原口径**，实测占比 0.5% |
| 末行 | `dash(data.basis)` | 同 §2.4 那句"PG 基准表" |

`DeviationList items={data.deviation}`（`:87`）在卡片最底部，实测 9 条（见 §2.3 表尾）。

---

## 4. 第三块「板块周期」

`Section note` 原句（`QdCompare.tsx:73`）：

> 「板块周期 —— 两套东西不是同一个东西：本轨=生命周期阶段，qd 轨=轮动矩阵+持续性。
> 合并会逼另一套字段留空，所以分块。」

组件：`frontend/src/components/qd/SectorCyclePanel.tsx`。它是**唯一一块被首页原样复用**的面板
（`SummaryBoard.tsx:294`），所以这里的每一格在首页也出现一遍（见 §6.5）。

### 4.1 本轨「生命周期阶段（本轨）」（`track="own"` `:45-138`，`provenance={false}`）

| 功能点 | 显示/行为 | 实测 2026-10-11（2026-10-09） |
|---|---|---|
| hint | 「回答「这个板块处在启动的哪个阶段」。」 | — |
| 五档计数条 | `OWN_STAGES` 固定顺序 `ignite/follow/coil/fade/none`（`:20-23`），中文名「点火/跟随/蓄势/退潮/未触发」；计数按**全量** `all`（`:49-50`），不受下面的开关影响 | 「点火 1 · 跟随 0 · 蓄势 56 · 退潮 47 · 未触发 879」，为 0 的档灰底（`counts[k] ? 主色 : bg-muted`） |
| 分母句 | `all.length` + 各 `sector_type` 计数（`:66-68`） | 「分母 983 个板块（行业 557 / 概念 268 / 风格 128 / 统计 30）」 |
| 勾选「显示风格与行为统计板块（N 个）」 | 默认**关**。`hidden = s.synthetic === true \|\| s.sector_type === "风格"`（`:52`），纯前端过滤、**不发新请求**；N = `all.length - tradeable.length`（`:73`） | N = **129**（128 个风格 + 1 个 `synthetic=true` 且类型是概念的「ST板块」）。⚠️ 实测**唯一的"点火"是风格板块「近期弱势」**（score 67.2），它默认被收起 ⇒ 屏幕上"点火 1"的计数在，但那一行不在表里，只有打开开关才看得见。计数与分母按全量统计这件事，`:74` 的 `Caliber` 与 `:133` 末句都写明了 |
| `Caliber` 内容 | 前端硬写（`:74`）：「这些不是可交易题材：「昨日突涨正在蓄势」是同义反复，风格类（如「中小银行」）也不是一篮子可买的票。默认收起，但数据没有被裁掉（打标不裁），阶段计数与分母仍按全量统计。」 | `synthetic=true` 的 8 条实测：昨日突涨/昨日上榜/昨日断板/连续亏损/最近情绪指数/板块趋势/轮动趋势（都是风格）+ ST板块（概念）；行内名字后挂「统计」灰标（`:106`） |
| 口径告警 | `data.caveat` 原样透出（`:77-81`，注释「要原样显示的口径告警（藏起来读者就会以为没轮动）」） | 实测原句：「230 个行业板块缺涨跌家数、广度分项算不出，规则上永远不会被判为点火；昨日涨停/昨日突涨这类是通达信的行为统计板块，不是题材」。⚠️ **与这一发的交出对不上**（实测 `elements.breadth` 为 `null` 的只有 **4** 条、全是风格板块，行业板块 0 条；`squeeze`/`position` 为 `null` 的 380 条里 379 条是行业）。后端没交"230 是按哪一列数的"，页面也不核对 ⇒ 文档如实记下这句与交出值不一致，**不替后端说话** |
| 截断句 | `shown.length > 40` 时「仅列出排序后的前 40 行（没点列头时是后端序），共 N 行。」（`:127-131`）；排序在 `slice` **之前**（`:100`，符合 `useSort.ts:10-11` 的规矩） | 共 854 行可展示 ⇒ **正在截断**，屏幕上出这句 |
| 末行 | 「交易日 （分母与阶段计数按全量统计，不受上面开关影响）」—— `ownDate ?? data.date`（`:133`） | ⚠️ 首页与本屏都**没传** `ownDate`（`QdCompare.tsx:74` 只传 `own`/`qd`），所以永远吃 `data.date`。实测 2026-10-09 |
| `Caliber`（整块底部） | 前端硬写 `OWN_CALIBER`（`:26-32`）：五档由后端算、前端**不重排**（「重排会把「点火」挤出首屏」）、五要素 NULL「不是 0 分」、「打标不裁」 | — |

逐列表（9 个 `SortHeader`，`useTableSort()` 无默认序 ⇒ 初始是后端序：`ignite > follow > coil > fade > none`、
同级按 `score` 降序，`qd.ts:334` 注释明令前端不要重排）：

| # | 列名 | sortKey | 格子读哪个字段 | 口径出处 | 缺数显示 | 悬停原文（`:87-96`） | 灰显/着色 |
|---|---|---|---|---|---|---|---|
| 1 | 板块 | `name` | `s.name`（+「统计」灰标当 `synthetic===true`） | 后端 `analytics.ignition` 的板块名 | `dash()` → `—` | 「板块名（文本档）；标了「统计」的是行为统计板块，与行业/概念不是一类」 | 常规 |
| 2 | 阶段 | `stage` | `STAGE_CN[s.stage]`，缺则 `dash(s.stage)` | 五档枚举住在后端（本屏只是查表翻译）；分类档文案「阶段是分类档，按字面排，不代表强弱次序」 | `—` | 同上 | 点火/跟随走 `strengthColor(true)`=红（`text-danger`），其余走 `strengthColor(false)`=绿（`colors.ts:43-46`）⚠️ **"退潮"是绿、"点火"是红**，与 A 股红涨绿跌同一套色觉；但这是**强弱语义不是涨跌语义**，`colors.ts:38-42` 注释就解释了为什么不分两套色 |
| 3 | score | `score` | `num(s.score, 1)` | 周期综合分 | `—`，且 `isMissing` ⇒ **永远排最后**（`useSort.ts:70-73`，升序降序都在最后） | 「周期综合分；缺分的行排最后，不当 0」 | 常规，右对齐 `tabular-nums` |
| 4-8 | squeeze / position / ignition / volume / breadth | `elements.squeeze` … `elements.breadth`（**嵌套键**，`useSort.ts:17-26` 的 `pick()` 支持 `a.b.c`） | `finite(el[k])` 再 `.toFixed(1)` | 五要素分项原值（0~100 一档）；NULL=这一项算不出来 | `finite()` 把 NaN/Infinity/字符串一并算不算数 ⇒ `—` + `FLAT_TEXT` 灰字（`:116`） | 「分项原值（0~100 一档）；后端没交这一项的行排最后，显示 — 而不是 0」 | 缺数灰显；实测 `squeeze`/`position` 各 380 行为灰，`breadth` 4 行灰 |
| 9 | 类型 | `sector_type` | `dash(s.sector_type)` | `reference_data.sector_info.sector_type`（行业/概念/风格/统计） | `—` | （无 `title`，`:96` 只有 label） | `text-muted-foreground` |

后端交但屏幕上**不显示**的键：`term_count`、`heat`（`{pctile,slope3,vol_ratio,share_ratio,up_ratio}` 五个分项，
`heat` 里**恰好是 qd 轨没有的那份热度信息**，本屏一格都不读）。

### 4.2 qd 轨「轮动矩阵与持续性（quantdash 口径）」（`track="qd"` `:140-151`，带来源声明）

| 元素 | 读哪个字段 | 口径出处 | 实测 2026-10-11（2026-10-09） |
|---|---|---|---|
| `ComputedLine` | `computed_by` | ⚠️ **这个端点没有 `computed_by`**（`qd.py:305-375` 的返回里没有该键，实测顶层键 = `available/date/rotation/persistence/scope/universe/sector_types/deviation/spec_version/source`）⇒ 屏幕上**永远**显示「后端未标注 computed_by —— 无法判断这屏的数是本项目算的还是读表得来的」 | 正是这句 |
| hint | 「回答「领涨板块换了几手、谁连庄最久、强度在增还是减」。quantdash 的板块周期没有阶段枚举，所以不和本轨并成一张表。」 | 契约 02 §5 | 在 |
| 轮动矩阵 | `rotation[]` → 每行 `trade_date` + `items[]`（**后端按日分组**，`qd.py:344-357`；注释原句「平铺 8 行会被当成 8 个日期、整表渲染成 —」） | 最近 **5** 个交易日 × 每日**前 8**（`SQL` 的 `limit 5` `qd.py:322`；`top_n=24` 是落库时的排名窗口，与矩阵显示的 8 是两件事 —— `qd.py:348-350` 专门警告过） | 实测 5 行：10-09 / 10-08 / 09-30 / 09-29 / 09-28，每行 8 格 |
| 表头 `colSpan=8` 那句 | 前端硬写 | 「当日领涨板块（前 8）· 格子底色=当日涨幅」 | 在 |
| 配色 `Caliber` | 前端硬写（`:169`） | 「配色阈值照搬 quantdash：涨幅 ≥4 深红 / ≥2 红 / ≥0 浅红 / <0 绿。⚠️ 这是纯涨幅配色，不含资金流 —— 红色不代表资金流入。」判据住所 `colors.ts:48-63`（`heatBg`；注释还写明「**配色方向必须由本文件反转**」） | 实测第一名 +15.14% ⇒ 深红 `bg-danger` |
| 格子 | `sector_name ?? name` + `pct_change` 一位小数带正号；`title` = 「名称｜当日第 N 名｜涨幅 x.xx%（纯涨幅配色，不含资金流）」 | 后端逐格交 `day_rank/pct_change` | 「视频音频媒体 +15.1」 |
| 「新晋领涨」标注 | **不在格子里猜**：`newLeader` = `persistence.strength_delta_pct === null`（`:146`），命中时只贴在 `day_rank===1` 的格子上（`:191`、`:199`） | `qd.ts:171-172` 注释 + `L-17`：NULL 的语义是"新晋领涨"，**不是缺数据** | 实测 `strength_delta_pct=null` ⇒ 第一行第一名格子上挂着「新晋领涨」 |
| `items` 为空的行 | 「该行 `items` 为空（当日无领涨板块，或后端未按契约给 items）」（`:183`） | — | 未触发 |
| 分母句 | `dash(scope)` + 「—— 与 qd 轨自身口径一致，不等同于本轨的板块全集。」（`:234-236`） | 后端整句（`qd.py:366-369`） | 「参与排名板块 915 个（行业）；矩阵只留每日涨幅前 8，共 5 个交易日」⚠️ 类型只剩「行业」⇒ **这 5×8 格里概念板块一条都没有**（与 `09 号文档:539-556` 勘误 3 描述的是同一个宇宙膨胀；本轮 915 vs 829 已在 deviation 之外，界面只有 `scope` 这一句能看出来） |
| 持续性五格（`Persistence` `:215-239`） | `current_leader_name` / `streak_days` / `top_three_appearances` / `strength_delta_pct` / `strongest_repeat_name` | 表 `qd.sector_persistence`，注释 `app/db/sql/12_qd_schema.sql` | 「视频音频媒体」/「1」/「1」/「+0.00%」？—— **不是**：`strength_delta_pct=null` ⇒ `pct()` 出 `—`（0 就显示 0，NULL 不是 0%）；`strongest_repeat_name`=「其他种植业」 |
| 那五格的 hint（`title` 与 9px 小字都出） | ①「后端未给 current_leader_name」（仅 NULL 时）②连庄天数无 hint ③「0 就显示 0（quantdash 缺数据时默认填 1，我们不跟）」= 契约 02 §5 第 3 条 / `D-21` ④「NULL 的语义是「新晋领涨」（quantdash 原 UI 语义，不是缺数据）；该标注落在矩阵里当日第一名的格子上」 ⑤「为 NULL 时显示「无连庄板块」，不回落成当前领涨板块」= 契约 02 §5 第 4 条 / `D-22` | 实测 `window_days=5`、`window_start="2026-09-28"` 交回来但**界面不显示**（`qd.ts:166-176` 声明了 `window_days`/`window_start` 却没被这两块读过） |
| `deviation` | 实测 2 条：`L-17`（前一日领涨与今日不同码 ⇒ `strength_delta_pct` 留 NULL，前端应显示"新晋领涨"，「这条 quantdash 做对了，照搬」）、`L-16`（窗口应取末 5 个交易日，实际可用 5 天） | 基准表逐行 | 折叠 2 条 |
| 本块与另一张表的关系 | `qd.stock_strength`（个股强弱分档，实测 129,265 行）**这一屏一格都不读** | 但 `frontend/src/pages/DataProcess.tsx:56` 的 `CONSUMERS` 把它指到 `/qd-compare` ⇒ **"产物被谁消费"那一行是假指向**（`grep -rn stock_strength frontend/src` 只命中那一行）。`/coverage` 那边它确实在册（`duanxian/coverage.py:152`） | — |

### 4.3 前三甲 · 前五场名次 · 相似板块（`components/qd/LeaderFollow.tsx`，用户 2026-10-25 点名）

用户原话：「轮动矩阵与持续性（quantdash 口径）显示出当前的领涨板块之后，还要把后面 2 名也列一下。
并且列一下前三名最近 5 个交易日的趋势变化的情况……并且根据 abc 的名次，去对照所有板块……
跟 abc 最相近最相似的是哪几个板块。」

| 元素 | 读哪个字段 | 口径出处 | 实测 2026-10-25（基准日 2026-10-23） |
|---|---|---|---|
| 取数 | `useLeaderFollow(date, nonce)` → `/api/qd/sector-follow`（`vr/app.py:498`） | 判据与落库只住写侧 `app/compute/qd_sector_follow.py`（接在加工链 `qd_sector` 那一步**末尾**，不另开步数）；读侧 `duanxian/qd_follow.py` **只读 `qd.sector_follow` 的落库行**；端点只包 `{data}` | 10-25 晚实测：读侧一次 3 行主键查询，接口 0.0x 秒 —— 旧那版"首算 ~1.9 秒 + 一小时缓存"已经没有了 |
| 为什么落库（10-25 晚改的） | 表 `qd.sector_follow`，PK `(trade_date, lag, sector_code, spec_version)`，DDL 52 | `qd.sector_rotation` 每天**只落 `day_rank<=8`**（写侧 `L-16` 那条偏差就记着这事）⇒ 8 名之外"某天排第几"库里查不到，形状匹配必须回母体逐场重排全 914 个板块。这件事**每天只需算一次**，放在读侧等于每个访客重付那两秒 | 搬家前后逐字段比对过 12 份 payload（四个基准日 × 三档 lag，脚本 `.local/p_follow_compare.py`）：**判据字段一名一格都没漂**，只差在口径句子多出 `landing` 那一句、caveat 多出落库的 `L-23` |
| 三名那一行 | `leaders[]` → `day_rank` / `sector_name` / `pct_change` | 母体与排名尺子照写侧同一条：`TDX_` 一族 + `sector_type in (概念,行业)` + 脏数据守卫（`name` 等于代码数字段 或 `|涨幅|>30`）+ `order by pct desc, name, code` | 第1名 机器人 +9.91%、第2名 锂电设备 +8.36%、第3名 锂电池 +6.73%，母体 914 |
| 前五场名次 | `leaders[].trajectory[]` → `trade_date` / `rank` / `pct_change` | 不含当日，从最近往远排五场；`rank` 为 `null` ⇒ 界面 `—`（**不折成第 0 名、也不折成 0%**） | 机器人：45 · 847 · 7 · 1 · 220（10-22/21/20/19/16） |
| 相似榜 | `leaders[].similar.items[]` → `gap_mean_rank` / `gap_pct_of_universe` / `rank_corr` / `current_rank` | **错开两场**对齐（用户按自己写的日期选的口径）：模板＝领涨板块 t-2..t-5 四个名次，候选＝各板块 t..t-3 四个名次；主排序＝平均名次差，距离并列才看秩相关 | 机器人的榜首相似＝胶黏剂及胶带（平均名次差 96.75、占母体 10.59%、秩相关 0.8、当日第 729） |
| 分母两句 | `universe_size` / `match_total` / `skip_total` / 每榜 `candidate_pool` | 母体＝当日参与排名的板块数；候选池＝这六场出现过的并集减掉领涨板块自己（可**比当日母体大**：有板块这几天有行、当天没行）；缺行的进"因缺行未参与"，**不当成"不像"** | 实测三个榜：比对 2,739 个·人次、缺行未参与 3 个·人次；`matched + skipped == candidate_pool` 有测试钉 |
| 模板不齐时 | `similar.incomplete_template` ⇒ 橙字「模板缺场次，本轮不比对」 | 模板不齐就不给榜（`matched=0`）并落一条 `L-24`，不给一张看着正常的空榜 | 实测早期场次会发：2021-08-09 那一批 lag=2/3 全三只都是 `incomplete_template=true`（往前伸已越过库最早一场 2021-08-03） |
| 排序实测 | 六列里四列可点（`SortHeader`，`sortKey` 逐字等于格子里读的字段） | 缺值两向都排末尾（`useTableSort`） | 点「平均名次差」升序＝距离从小到大；整表是三个榜拼的 18 行，**排序跨三个领涨板块** |
| 不给外链 | 板块名是纯文本 | 《板块名→东财板块页》那份对表只认东财 `BK` 码（`reference_data.em_board_link`），这一轨是通达信 `TDX_` 码 ⇒ 没有码就退回纯文本，不猜地址（`SectorLink` 的既有纪律） | — |
| 批次年龄 | `generated_at` / `cache_age_seconds` / `cached`（**键名沿用旧那套，语义换了**） | 三个数都从落库行的 `calculated_at` 折出来：读的是**入库时刻**，不是请求时刻。`spec_version`（当前 `follow-qd-1`）也一起交出 | 文案「本批算于 2026-10-25 22:12:03（加工链 qd_sector 那一步落库的批次，N 秒前；这块不在请求时现算）」——同一场同一档谁打开都是同一批，年龄只增不减（有一条真库测试钉这条） |
| 口径句子 | `calibers`（后端六句：leaders/trajectory/lag/similar/coverage/**landing**） | 前端**不自写**，`Caliber` 读那一份；caveat 带中文 label：`lag_shape_match`＝错开两场比对、`missing_day_row`＝缺行不参与、`partial_trajectory`＝轨迹有缺口，再加**落库偏差** join 出来的 `L-23`＝两个分母、`L-24`＝模板缺场次、`L-22`＝跨日名次不可比（没登记的码照旧念码本身，不编好听的名字） | 屏幕上裸 `**` 数 0（口径与偏差原句里就不留星号）。⚠️ `L-22` 在**当前全史一次都没发过**：实测 1,264 场里相邻两场母体偏离最大不到两成（库里 L-22 行数 0）——它是防"以后母体再扩容"的守卫，不是已发生的缺陷 |
| 测试 | `tests/test_qd_follow.py` 28 例 | 纯判据（缺数出口、距离并列、模板不齐、并集分母）+ 单一来源三条（母体那段 SQL 是**字面嵌进**写侧的、|涨幅| 上限只在 `qd_sector` 定义一次、±20% 宇宙守卫两处共用 `qd_common.universe_break`）+ 读侧不许再算（源码里出现 `market_data.sector_daily`/`row_number`/`mean_rank_gap` 就红）+ 真库五条（前三名与 `sector_rotation` 逐名一致、落库 jsonb 原样读出、独立 SQL 复算榜首四个名次、每场每档 3 行且 1,250+ 场、payload 能 `allow_nan=False`） | 跑完回算后现打，数字与失败名单一起报 |

---

## 5. 第四块「信息采集」

`Section note` 原句（`QdCompare.tsx:77`）：

> 「信息采集 —— quantdash 口径的本地快讯轨（newswire 落的 jsonl）。规则：没有就是没有，绝不放示例新闻。」

| 元素 | 显示/行为 | 口径出处 | 实测 2026-10-11 |
|---|---|---|---|
| 指路句 | 「本轨另有资讯入口：「资讯雷达」（RSS 赛道聚合 + 关注股新闻/公告）。下面是 `qd` 轨（东财 7×24 主 + 新浪 7×24 备）。」（`:79-83`） | 前端写的跨页指路 | 在 |
| `TrackSection track="qd"` 标题/hint | 「当日快讯（本地 jsonl）」/「每条同时给「发布时刻」与「采集时刻」；发布时刻缺失时会标明显示的是采集时刻。」（`:84-86`） | 契约 02 §6 | 在；这一块**也带来源声明**（`provenance` 未显式关掉） |
| 读数行 | 采集日 `data.date`、共 `count ?? items.length` 条、`truncated` ⇒「已截断，仅显示前 N 条」、`corrupt_lines` ⇒ 橙字「N 行解析失败（**未静默丢弃**）」、`supports_code_filter === false` ⇒「不支持按个股过滤 —— 存储里没有个股关联字段，**不用标题匹配硬凑**」（`NewsPanel.tsx:44-52`） | 后端逐键交（`qd.py:528-541`） | 「采集日 2026-10-11 · 共 41 条」（不截断、0 行解析失败、不支持按股过滤那句在）。⚠️ **采集日与行情日不同步**：行情三块是 2026-10-09，快讯取的是目录里最新那份 `news-2026-10-11.jsonl`（非交易日也在采，7×24） |
| `Caliber` | 「快讯不可回补：当天没采就永久没有。所以这里的日期是「采集日」，不是「发布日区间」。」 | 前端硬写（`:52`）；判据住所 `qd.py:480-488`（按天落盘、缺文件就 `available:false`、**不回落到邻近日期**） | — |
| 每条卡片 | 标题（`plain(it.title) \|\| "（无标题）"`，点击展开正文）；时刻一格：有 `published_at` 时「发布 <时刻>」+ 后面另起一格「采集 <captured_at>」；**缺失时退回 `captured_at` 并橙字加粗 + 「（采集时刻，非发布时刻）」**（`NewsPanel.tsx:66-96`，`:76` 的 `title` 逐字给「发布时刻（来自报文）」/「发布时刻缺失，此处显示的是采集时刻」，缺失那档还打 `FLAT_TEXT` 灰）；来源 `source`；`news_type` 前挂「·」；`level != null` 才出「级别 N」；**`sentiment != null` 才出「情感 N」**（契约 02 §6：NULL 是"没做情感分析"，不许显示"中性"）；有 `url` 才出「原文」外链（`target="_blank" rel="noopener noreferrer"`）；正文 `whitespace-pre-wrap`，空正文显示「（该条没有正文，只有标题）」 | `duanxian/newswire.py`（`logic_source`/`spec_version` 也在每条里，实测 `quantdash-logic` / `qd-1`；界面**不显示这两个键**） | 41 条：`published_at` 缺失 **0** 条（那条降级路径本发未触发）、`url` 缺失 **4** 条、`sentiment`/`level` **全部为 null** ⇒ 满屏没有情感/级别标签、也没有一个"中性" |
| 折叠差异清单 | `items.find(x => x.deviation?.length)?.deviation` —— **取第一条非空**当整块的代表（`NewsPanel.tsx:61`） | 后端逐行交，实测每行同三条：`SRC-1`「契约端点（财联社 telegraphList）2026-09-24 实测 404，本行取自新浪 7×24 直播」、`SRC-2`「财联社 level(A/B) 无对应字段，本行 level 置 NULL」、`SINA-T`「该源无标题字段，title 为正文前 60 字截断」 | 3 条 |
| 空态 | `available!==true` **或** `items` 长度为 0 都走同一支：「当日无采集到的资讯。后端给出原因：…」；没给原因就加粗报契约违规（`:31-40`） | 契约 02 §6 禁止 `MOCK_NEWS`（`D-24`） | 实测 `?date=2020-01-01` → `available:false` + 原句「2020-01-01 没有采集文件（本地快讯按天落盘，只保留实采到的日子）。不回落到邻近日期 —— 邻近场次的快讯不是这一天的答案」 |
| 本地覆盖 | 目录 `data_path("news")` 实测 18 个文件，`news-2026-09-24.jsonl` → `news-2026-10-11.jsonl`；行数：10-07 1,549 / 10-08 2,322 / 10-09 2,021 / 10-10 988 / 10-11 41 | — | ⚠️ 前端写死 `limit=200` ⇒ 选 10-09 实测 `count=2021`、交 200 条、`truncated=true`，屏幕上就是那句"已截断" |

---

## 6. 数据现实（2026-10-11 本机实测）

| 读数 | 值 | 实测方式 |
|---|---|---|
| 基准交易日 | **2026-10-09**（情绪/天梯/板块三块同源）；10-10、10-11 非交易日（`reference_data.trade_calendar`，日历 2021-08-02~2026-10-09 开市 **1255** 场） | curl 第二发 |
| 五个端点耗时（第二发） | sentiment 0.276s / ladder 0.160s / sector 0.809s / distribution 0.325s / news 0.012s / 本轨 sector-cycle 0.589s（首发分别 6.21s / 0.14s / 0.92s / 0.36s / 0.01s / 0.66s —— **只有 sentiment 首发慢 22 倍**，因为它要在 `qd_calc` 里现算） | curl `-w %{time_total}` |
| 响应体积 | sentiment 3,056B / ladder 27,958B / sector 8,434B / distribution 778B / news 26,801B / 本轨板块 291,145B | curl `-w %{size_download}` |
| `available` | 六个端点默认请求全部 `true`；`date=2026-10-10` 时 qd 三条全部 `false`（各自点名表名 + 「不回落到邻近交易日 —— 邻近交易日的数不是这一场的答案」），快讯 `2020-01-01` 也 `false` | curl |
| `spec_version` | 三个 qd 读数端点都交 **`qd-1`**（实测）；库里 `qd.sentiment_cycle`/`board_ladder`/`board_ladder_leader`/`sector_rotation`/`sector_persistence` 各自 `count(distinct spec_version) = 1`，唯一取值就是 `qd-1` | PG 只读聚合 |
| 覆盖场数/行数（PG 基准表） | `qd.sentiment_cycle` **1,255 行**（2021-08-02→2026-10-09，1,255 场，缺场 0）；`qd.board_ladder` 8,785 行/1,255 场；`qd.board_ladder_member` 81,173 行；`qd.board_ladder_leader` 1,255 行；`qd.board_height` 1,255 行；`qd.sector_rotation` 17,183 行（2021-08-03 起，**1,254 场**，比情绪/天梯少最早那一场）；`qd.sector_persistence` 1,254 行；`qd.stock_strength` 129,265 行（本屏不读）；`qd.news_item` **0 行**（预期，快讯在本地 jsonl） | PG 只读聚合 + `/api/coverage?table=qd_sentiment_cycle`（`status=ok`、`rows_kind=exact`、`sessions_present/missing_sessions/stale_sessions = 1255/0/0`、`last_touched=2026-10-09 16:08:11`） |
| 「重灌过哪一批」 | 库内 `qd.sentiment_cycle.calculated_at` 分批实测：**2026-10-07 一批 1,249 场**、2026-10-08 1 场、2026-10-09 5 场 ⇒ 全史是 10-07 那次重灌的。**这一屏看不见**：本地实算分支根本不交 `calculated_at`（`qd.py:125-140`/`:143-153` 的 shape 里没有这个键），走 PG 兜底时才交（`:185`），而**前端类型声明里也没有 `calculated_at`**（`qd.ts:64-69`、`:56-62`）⇒ 兜底分支交出的一行 `calculated_at=2026-10-07 22:28:46`（实测 `?date=2021-08-02` 拿到）被界面**整个丢掉**。唯一看得见日期的入口是 `/coverage` 的 `last_touched`（最后一批写入时刻），它同样说不出"分了哪几批" | PG + 两发 curl |
| 分片口径 | `app/compute/qd_common.py:39` `POOL_CHUNK = 250`（`:36-38` 注释原句：2026-10-07 实测 1,253 场一把跑 `SQL_LBC` 在 300 秒被 `statement_timeout` 掐掉；250 场一片约 60~90 秒，每片打一行进度）；`app/compute/qd_ladder.py:365` `POOL_BACK_SESSIONS = 70`，`:404-416` 每片的池 = 本片日期 + 往前 70 场回看，**只写本片日期**（片头不带回看就会让跨片的连板"凭空断成 1 板且不报错"，`qd_common.py:234-236`）。1,255 场 ⇒ 5 片 | 读代码 + `docs/系统配置工作指南/06-自动执行机制自查表.md:668` |
| 区间分布 | `days=1255`，`2021-08-02 → 2026-10-09`，`sample_total={stage:1255, tie_break:1255}`，`observed_sources=["quantdash-logic"]`，`basis` 见 §2.4。分档计数：见 §2.4 与 §3.4 两表 | curl 第二发 |
| 项目记录 vs 本轮实测（**两处已漂**） | ①「退潮占 80%」：记录值 936/1169 = 80.06%（`docs/数据加工工作指南/09-quantdash逻辑规格.md:577`，作者当时就标了「⚠️ 下面这串是**修正成交额环比基期之前**测的」），**本轮实测 846/1255 = 67.4%**；屏幕上那句头部读数用的是实时值 67.4%，而同一张卡折叠里的 `D-43` 原句仍写"退潮 936 / 1169 天" ⇒ **同一屏两个数并存、且互不相等**。②「龙头 99.9% 非原口径」：记录值 1168/1169 = 99.91%（`:511-513`），本轮实测 **1248/1255 = 99.44%**（`seal_time` 已从 1 场涨到 **6 场**，实测 09-22、09-30 两场成员**全部**有首封时间 ⇒ 旧文档「历史里只有 2026-09-22 有分钟线」（`vr/app.py:462-463`、`qd.py:223-225`、`qd_common.py:197`）**已过期**） | 两次实测相减 |
| 三方不一致（已登记未查因） | `docs/系统配置工作指南/06-自动执行机制自查表.md:677-683` 原句登记：2026-09-30 qd 涨停 56 / 触板 **77** / 未封 **21**；`analytics.market_breadth` 56 / 69 / 13；日线双边现算 56 / **70**。近五场都差 7~10 家且**不是单向**（09-22 qd 涨停 66 vs 严格双边 65）；登记时的判断是差异出在 `app/compute/qd_sentiment.py` 取"未封"那批的母体（读天梯池 `pool_kind='touched_not_sealed'`），**不是档位重灌引进的**，因"改了要连 `spec_version` 与 1,253 场重跑一起决策"而**留名未改**。⚠️ 本轮实测的 qd 轨那一屏在 2026-10-09 给的是 `limit_up_count 72 / touched_count 91 / broken_touched_count 19`（`qd_calc.py` 现算，不走 `qd_sentiment` 那条 `pool_kind` 路径）—— **屏幕上没有一处把这三方摆在一起对数**，这一条差异在本屏不可见，只在上面那两份文档里 | curl + 读登记 |
| NaN/Infinity | 六发响应整包 `grep -oE '\bNaN\b|\bInfinity\b'` **命中 0**（实测方式：正则扫原文）；出口收口在两处：后端 `_plain()`（`qd.py:45-59`，float NaN 折成 `None`）、前端 `num/pct/finite`（`qd.ts:350-358`、`agent.ts:40-42`） | 脚本扫包 |
| 渲染文本 `**` | 后端原文里确带粗体星号（实测 `D-3`「分母是触板数」前后、`D-43`「冰点 0 次」、`L-19`「触板口径」），页面全部过 `plain()`（`Track.tsx:101-102`、`Section` `:104`、`:94`）⇒ 渲染后不出现星号。**未开浏览器实测**（本轮只做接口 + 构建产物两侧取证：8910 返回的 `index.html` 引用 `assets/index--pMTtFfT.js`，与 `frontend/dist/assets/index--pMTtFfT.js` 同名，且该文件里「quantdash 原口径」命中 4 次、「原口径」共 6 次，与源码 6 处一一对应 ⇒ 屏幕上挂的就是这些句子） | `grep -o … \| wc -l` |

### 6.5 这一屏之外还有谁在显示 qd 数值（写给下一个人）

实测 `grep -rn "useQd\|components/qd" frontend/src` 收口，**qd 轨数值的展示位共 4 处**：

| # | 位置 | 显示什么 | 证据 |
|---|---|---|---|
| 1 | 本页 `/qd-compare` 四块 | 情绪三列 + 判定强度 + metrics + 区间分布 + 天梯 + 龙头 + 两套板块 + 快讯 | `QdCompare.tsx:28-34`（六个 hook） |
| 2 | 首页 `/`（`SummaryBoard`）「涨停与连板」块 | **涨停家数主数 = qd 轨天梯各档 `stock_count` 之和**、各档一行、龙头名/板数/状态标签（`:63`、`:104-106`、`:214-226`）；注释 `:34-41` 原句「涨停家数在本页只认「qd 轨 · 收盘双边判据」」 | 同一端点 `/api/qd/board-ladder` |
| 3 | 首页「板块周期与资金」块 | **整块复用 `SectorCyclePanel`**（本轨表 + qd 轨轮动矩阵与持续性，`:7`、`:294`） | 同一组件、同一端点 |
| 4 | 复盘报告的证据列表 | qd 轨证据记录旁的 `TrackTag` 徽标「quantdash 口径 QD」 | `frontend/src/components/ReportEvidence.tsx:3`，出处 `review_agent/evidence.py:84`（`"track": "qd"`） |

⇒ **首页那一块「情绪周期」确实已经不在了**（三列 stage、判定强度、区间分布都只剩本页这一处），
但**不能说"qd 数据只在复盘这一屏"**：天梯与板块两块至今同时在首页出现，
而且它们是从**同一套端点**读的。`qd.ts` 里那几个 `num/dash/pct` 还被四个与本屏无关的页面借用
（`Quadrant.tsx:12`、`Scores.tsx:14`、`VolumeDouble.tsx:10`、`quant/NewHighBoard.tsx:10`）——
那是**共用出口函数**，不是共用 qd 数值，别当成"第五处 qd 展示位"。

---

## 7. 不许破的纪律

1. **qd 轨判据只住 `duanxian/qd_calc.py` 一族**（情绪 `qd_calc.py`、天梯/龙头 `qd_ladder.py`、板块写侧
   `app/compute/qd_sector.py`、公共常量 `app/compute/qd_common.py`）。
   页面不许自写第二套判据：`STAGE_PRIORITY`（`qd.py:385-389`）、`TIE_BREAK_COPY`（`qd.ts:407-416`）只是**文案与顺序**，
   判定逻辑一处都不在它们手里。
   ⚠️ **本轨判据与 qd 轨判据刻意不同**：qd 轨用双边严格 `abs(close - limit_up) <= 0.005`
   （`qd_calc.py:33-34`，注释原句点名"与 `pg_sources._ZT_PRED` 那条双规则不同，这不是疏忽"；容差唯一住所
   `app/compute/limit_prices.py` 的 `LIMIT_EPS`，`qd_common.py:34` 是 import 而不是重抄）。
   两套判据给出不同家数是"双轨并存"的应有之义，但谁和谁不一样**必须写在注释里**。
2. **`spec_version` 在主键里**：`app/db/sql/12_qd_schema.sql` 里**七张**按日表的 PK 带它
   （`:93` `(trade_date, spec_version)`、`:207` `(trade_date, board_tier, spec_version)`、
   `:243` `(trade_date, security_code, spec_version)`、`:297`、`:349`、`:390`、`:437` 同族），
   含义是"**判据一改就是新版本、新行，旧行永远保持当时那句话**"（同一族规矩的解释见
   `app/compute/allocator.py:23`、`app/compute/sector_trend_signal.py:14`）。
   ⚠️ **两张是例外，别以为全族都受保护**：`qd.stock_strength` 的 `spec_version` 只是 `NOT NULL` 列、
   PK 是 `(trade_date, security_code)`（`12_qd_schema.sql:529` 与 `:533`）⇒ 它**升版会就地覆盖旧行、不追加新行**，
   与"旧行永远保持当时那句话"相反；`qd.news_item` 的 PK 是 `item_id`（`:478`）。
   实测九张表的 `count(distinct spec_version)` 都是 1（唯一值 `qd-1`），所以现在看不出后果。
   ⇒ 升 `config.setting` 的 `qd_spec_version` 在七张表上是**追加一批行**（历史榜会变双份），
   在 `stock_strength` 上是**盖掉旧的那份**。
   **阈值改动必须升版、不许静默改口径**写死在三处：`app/compute/qd_common.py:8`、`:32`
   与 `14_qd_settings.sql:19-22`（键的 `label` 原句「改阈值必须升版，不许静默改口径」）。
3. **⚠️ 升号现在被硬编码卡住（改口径要一起改的就是这几处）**：
   **`duanxian/qd_calc.py:480`** `"spec_version": "qd-1"`（写死在返回字典里），
   同族还有 `duanxian/qd_ladder.py:161`、`duanxian/newswire.py:56`（`SPEC_VERSION = "qd-1"`，用在 `:168`）；
   `duanxian/qd_sector.py:101` 也有一处，但该模块**没有任何调用点**（`duanxian/qd.py:101` 只 import `qd_calc`/`qd_ladder`，
   板块那一轨走的是 PG 表读取），属于写死但到不了屏幕的一份。
   后果：界面 qd 轨读的是**本地实算**那条分支（实测 `computed_by=yuanshan`），
   它显示的 `spec_version` 是这几个**字面量**，不是 `config.setting` 的读数（写侧才是从
   `app/compute/qd_common.py:83-84` `spec_version()` → `setting('qd_spec_version')` 读的）。
   ⇒ 把表里的键改成 `qd-2` 后，落库行是新版本号、**屏幕上仍然写着 `qd-1`**，两边一起看就是假自洽。
   改口径的完整动作清单：`config.setting` 升版 + 这四行字面量 + 需要时 1,255 场重灌（§7 第 6 条）。
   另注：兜底分支的版本号是 `duanxian/qd.py:297-302` 的 `max(spec_version)` **字符串比** ——
   本项目自己登记过这条不能用（`app/compute/sector_flow_trend.py:49` 原句「不要用 `max(spec_version)` 字符串比」），
   真出现 `qd-9` 与 `qd-10` 并存时它会挑错那个。
4. **"原口径"三个字不许改成"已修正"**。屏幕上 6 处「原口径」（`BoardLadderPanel.tsx:25/29/176/186`、
   `qd.ts:408/410`）全部挂在 **`seal_time` 这一档**上：只有按最早封板时间选出的那 **6 场**（实测 0.5%）
   才是 quantdash 的原口径，其余 1248 场的龙头是**本实现为了不留空做的确定性选择**，
   必须保持降级样式（灰 + 斜体 + ⚠）与那句「与 quantdash 原口径不等价」。
   把 `amount_no_seal_time` 的文案洗成"等价"，这一屏就开始说假话。
5. **不改判定顺序、不改分布**：区间分布只呈现（`vr/app.py:495-497` 原句「这是**它逻辑的性质**，不是本实现算错 ——
   所以界面只呈现分布与优先级，不改判定」）。实测 `never_seen_stages` 交出 AI 版冰点 0 次、飞书版修复/主升各 0 次，
   `DistBars` **必须继续给零值档占一行**（`Track.tsx:151-153`）。
   `D-43` 那条原句里的"1169 天 / 退潮 936"是**旧批次**的静态文案（实测屏幕头部那句已是 1255 场 / 846 场），
   两个数并存是现状，不许把哪一边"顺手改成一致"—— 要改就改 `app/compute/qd_sentiment.py` 落库时那段 deviation 文案并升版。
6. **分片口径不许退回一把跑**：`POOL_CHUNK=250` + 片头 `POOL_BACK_SESSIONS=70`（`qd_common.py:233-246`、
   `qd_ladder.py:404-416`）；测试钉的是「切片是精确分组、尾巴不许丢」（`tests/test_qd_track.py:364`
   `test_切片是精确分组且片头带回看窗`），分组陷阱见 05 号清单第 16 条。**界面上没有"这一场是哪一片灌的"这个读数**，
   所以任何"重灌"要么留作业日志（每片一行进度），要么在 `/coverage` 看 `last_touched`，别指望本屏自证。
7. **缺数是 `—`，不是 0**：`dash/num/pct`（`qd.ts:344-358`）+ 后端 `_plain`（`qd.py:45-59`）。
   实测这一屏正在被用到的 NULL：晋级率族四列（`success_rate_pct`/`avg_board_gain`/`premium_score`/`broken_rate_sealed_pct`，
   `L-3`：T+1 未入库）、`strength_delta_pct`（NULL 语义是"新晋领涨"）、`leader_next_open/close_pct`（次日未到）、
   成员 `concept_name`（`L-4` 恒 NULL）、本轨五要素里算不出的分项。
   契约 02 §7 第 2 条的理由要一起记住：**`0` 会被下游 AI 当成真实数值继续推理**。
8. **两轨永不合并、不许"综合"**：`own === null` 只能显示"本轨暂无"（`qd.ts:12-19` 三条永不违反的规则、
   `vr/app.py:437-438`）。板块那两套分块渲染（契约 02 §5），天梯本轨只给指路链接。
9. **取不到就是取不到**：不回落到"今天"、不拿邻近场次冒充（`qd.py:70-81`、`qd.ts:212-242`、`useQd.ts:5-11`），
   `available:false` 无 `reason` 要**当场报契约违规**（`Track.tsx:227-229`、`NewsPanel.tsx:36-37`）。
10. **一行 quantdash 源码都不许搬**（PolyForm Noncommercial 1.0.0，不是 Apache-2.0）：
    见 §8 之外的红线记录 —— `docs/功能集成分类.md:44`「三条红线」第 ①、`docs/数据加工工作指南/09-quantdash逻辑规格.md:24`、
    `app/compute/qd_common.py:5-8`（「**本文件零行 quantdash 源码**」）、`qd_calc.py:9`、`qd_ladder.py:4`、
    `vr/app.py:434-435`、`12_qd_schema.sql:13-16`。屏幕侧的落点是 `QD_PROVENANCE`（`qd.ts:422-423`，
    原句「逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），本项目仅重实现其算法口径，未复制源码」），
    由 `TrackSection` 在**每个 qd 轨卡片底部**自动挂（`Track.tsx:69-73`），实测本页 **4 处**在挂
    （情绪 qd 块、天梯 qd 块、板块 qd 块、快讯块），三个 `track="own"` 块显式 `provenance={false}` 不挂。
    改这块的唯一禁令：不许把 `qd` 轨的 `TrackSection` 加上 `provenance={false}`，也不许改文案说"参考实现"。
11. **`news()` 的 `reason` 在 `available:true` 时是 `null`**（`qd.py:539-540`），而 `count>0` 才会 `available:true` ——
    0 条文件的日子会被打成 `available:false` 并给那句"那一天确实没采到东西"，
    不要为了让卡片不空而返回 `items: []` + `available: true`。

---

## 8. 验收与复现

```bash
# 五个 qd 端点 + 本轨板块（冷启首打慢 20+ 倍，取第二发的耗时才算证据）
curl "http://127.0.0.1:8910/api/qd/sentiment-cycle"
curl "http://127.0.0.1:8910/api/qd/board-ladder"
curl "http://127.0.0.1:8910/api/qd/sector-cycle"
curl "http://127.0.0.1:8910/api/qd/distribution"
curl "http://127.0.0.1:8910/api/qd/news"
curl "http://127.0.0.1:8910/api/market/sector-cycle"

# 边界与非交易日（都要出声，不许回落）
curl "http://127.0.0.1:8910/api/qd/sentiment-cycle?date=2026-10-10"   # available:false + 点名不回落到邻近交易日
curl "http://127.0.0.1:8910/api/qd/news?date=2020-01-01"              # available:false + "没有采集文件"
curl "http://127.0.0.1:8910/api/qd/distribution?days=5"               # 5 场小样本，零值档仍交出
curl "http://127.0.0.1:8910/api/qd/distribution?days=6000"            # 422（le=5000）
curl "http://127.0.0.1:8910/api/qd/news?limit=0"                      # 422（ge=1）
curl "http://127.0.0.1:8910/api/qd/news?limit=501"                    # 422（le=500）
curl "http://127.0.0.1:8910/api/qd/sentiment-cycle?date=2021-08-02"   # 兜底分支：computed_by 缺失 + calculated_at 出现（界面不显示）
```

后端测试（**两份都在册**，已 `ls` 核实：`tests/test_qd_track.py` 20,602B、`tests/test_qd_evidence.py` 6,003B）：

```
PYTHONIOENCODING=utf-8 .venv\Scripts\python.exe -X utf8 -m pytest tests\test_qd_track.py tests\test_qd_evidence.py -q
```

`test_qd_track.py` 里与这一屏直接相关的用例（名字当场 `grep -n` 过）：
`test_sentiment_keeps_the_two_tracks_apart:67`、`test_missing_inputs_stay_none_not_zero:78`、
`test_reasons_column_is_exposed_as_stage_reasons:92`、`test_decimal_and_time_are_flattened_for_json:102`、
`test_requested_day_without_data_does_not_fall_back:115`、`test_disabled_local_db_says_so:126`、
`test_empty_table_does_not_invent_today:133`、`test_sector_matrix_is_grouped_by_trading_day:165`、
`test_sector_scope_reports_universe_not_a_fake_top_n:178`、
`test_ladder_reports_seal_time_availability_honestly:201`、
`test_sentiment_prefers_local_compute_over_the_table:253`、`test_local_compute_failure_falls_back_loudly:274`、
`test_distribution_keeps_zero_buckets_visible:293`、`test_news_reports_no_files_without_inventing_any:326`、
`test_news_keeps_published_and_captured_apart:336`、`test_切片是精确分组且片头带回看窗:364`、
`test_qd轨每条SQL都不许有裸百分号:395`。
`test_qd_evidence.py` 管的是复盘侧那一处 qd 引用（`test_qd_readings_join_the_same_frozen_bundle:29`、
`test_compare_metric_refuses_across_calibers:73`、`test_qd_failure_only_becomes_a_gap_never_a_failed_review:82`）。

**本屏没有前端单测**：`grep -rln "components/qd\|lib/qd\|SentimentCyclePanel" frontend/test` 零命中，
只有 `frontend/test/workspace.test.ts:24`（路由在册）与 `frontend/test/stalenessCoverage.test.ts:28-32`
（首页块数 5、并写明是谁摘的）沾到这一屏。
⇒ 排序、灰显、展开体这些**只能点击实测**（`node --test frontend/test/sortKeys.test.ts` 覆盖的是 `/quadrant`，不是这里）。

浏览器逐格清单：

1. 日期框留空 → 顶部"当前生效交易日"= 2026-10-09，且括号那句在；改成 2026-10-10 → 顶部照写 2026-10-10，
   下面三块全变灰底虚线"本屏无数据 + 后端给出原因"（**这一对比是本屏最重要的行为验证**）。
2. 情绪块：三个胶囊分别展开一次（飞书那条没有 `hit_rule`）；不一致黄条在；「/ 89 判定强度」的 `Caliber` 点开；
   metrics 网格 28 格，`success_rate_pct` 等四格是 `—`；`与 quantdash 原实现的差异 12 条` 展开逐条读 `code`；
   三条分布条序号 1 都是"退潮"，冰点/修复/主升那几行的「从未命中」在；末行 `basis` 那句在。
3. 天梯块：横幅在；七档都在（0 只的两档也出，且写「该档无成员。」）；成员卡「首封」整格灰底写「封板时间不可得」；
   「换手+」是虚线角标（点开悬停确认写了"非 quantdash 口径"）；龙头行斜体 + ⚠；
   「近 1255 场里…6 场…1248 场（99.4%）」那句在；`code_asc` 那行 0 场仍占一行。
   ⚠️ 屏级 `board_height` 那 9 个读数**不该在任何格子里出现**，出现即说明有人把它塞进来了。
4. 板块块：数一数是不是 9 个可点列头（板块/阶段/score/五个要素/类型）；每列升→降→取消三次，
   `squeeze`/`position` 灰字那 380 行在两种方向下**都排在最后**；开关打开后行数从 40/854 变 40/983 且「近期弱势」
   （唯一的点火）出现在表里；`qd` 轨那一节顶部必须是「后端未标注 computed_by…」那句；格子底色与 `title` 里的
   「纯涨幅配色，不含资金流」对得上；`strength_delta_pct` 是 `—` 且第一名格挂「新晋领涨」。
5. 快讯块：41 条、无截断句（选 10-09 应变成"已截断，仅显示前 200 条"）；点开一条出正文；
   满屏没有"中性"；4 条没有「原文」链接；折叠差异清单 3 条。
6. 四个 qd 卡片底部都有那句 PolyForm 来源声明，三个 own 卡片都没有；渲染文本 `**` 计数为 0
   （含 `<details>`/折叠体全展开后再数一次）。

## 相关

- 双轨展示契约（这一屏的宪法）：[页面展示工作指南/02-quantdash逻辑双轨展示.md](../../页面展示工作指南/02-quantdash逻辑双轨展示.md)
- 算法规格与 42 处伪造点、五条勘误、实现回执：[数据加工工作指南/09-quantdash逻辑规格.md](../../数据加工工作指南/09-quantdash逻辑规格.md)
- 口径纪律总表：[页面展示工作指南/01-数据口径展示规范.md](../../页面展示工作指南/01-数据口径展示规范.md)
- 触板/未封三方不一致的登记与分片口径的由来：[系统配置工作指南/06-自动执行机制自查表.md](../../系统配置工作指南/06-自动执行机制自查表.md)（`:667-683`）
- 跨源炸板口径：[数据加工工作指南/08-跨源口径契约.md](../../数据加工工作指南/08-跨源口径契约.md)（`:13, :28-30`）
- 「首页去掉那一块」的十条一批记录：[页面展示工作指南/04-新增页面落地进度.md](../../页面展示工作指南/04-新增页面落地进度.md)（`:396`）
- 本轨板块判据的另一处呈现：[量化选股/板块热度与点火.md](../量化选股/板块热度与点火.md)（10-18 现查已就位）
- 消费指向：[数据链路/数据加工.md](../数据链路/数据加工.md)（`CONSUMERS` 里六条 qd 行，`:112`；其中 `qd.stock_strength` 那条是假指向，见 §4.2）
