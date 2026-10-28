# 09 · quantdash 逻辑规格（派活给数据加工的算法契约）

> 来源项目：`D:\finance\quantdash-ai-stock-main`，许可证 **PolyForm Noncommercial 1.0.0**。
> 整理日期：2026-09-24。整理人：总协调会话。
> 配套展示契约见 [页面展示工作指南/02-quantdash逻辑双轨展示.md](../页面展示工作指南/02-quantdash逻辑双轨展示.md)。

## 0. 这份文档为什么存在（用户裁定）

此前我给出的结论是"quantdash 五项功能里四项 YuanShan 已有实现，唯一真缺板块周期"。**用户否掉了这个判断**，原话：

> "实际上我的逻辑跟它的逻辑是不一样的，我需要它的逻辑也能够展示出来。你可以把逻辑梳理出来，派活给数据加工，让他去计算，甚至 pg 数据库新建一张表或者一个模式，标注来源于 quantdash 的计算方法。"

所以目标不是"去重后只留一份"，而是**双轨并存**：

| 轨道 | 谁的逻辑 | 落点 |
|---|---|---|
| 本轨 | YuanShan / FinanceTool 自己的口径 | 现有 `analytics.*`、`duanxian/pg_sources.py` |
| **qd 轨** | **quantdash 的口径，逐条照搬它的阈值和公式** | **新 schema `qd`，每张表标 provenance** |

前端要把两轨**并排显示**，让用户自己看差异 —— 差异本身就是信息。

## 1. 三条红线

1. **法律**：PolyForm Noncommercial ≠ Apache-2.0。**一行源码都不许抄进 FinanceTool 或 YuanShan**。本文档只描述算法（阈值、公式、字段语义），实现必须自己写。落库的每张表/每个字段注释里写 `逻辑来源：quantdash <文件:行>`，这叫标注出处，不叫复制。
2. **口径照搬，缺陷不搬**。quantdash 有大量伪造/兜底/静默吞异常的代码（§7 逐条列了 42 处，带出处）。**口径**（阈值、公式、分档方式）照搬；**缺陷**（随机数、常量兜底、缩放凑数）一律不搬，改为写 NULL 并在 `deviation` 里记一条。
3. ~~**写权限**：YuanShan 侧只有 `ft_reader`（只读）。**所有 DDL 与写库只能在 FinanceTool 侧做**（`ft_etl` / `ft_admin`）。YuanShan 只加读取函数。~~
   ⚠️ **本条已作废（2026-09-24 用户裁定）**：`D:\finance\YuanShan` 是唯一落地点，**FinanceTool 退役、由 YuanShan 全吸收**。"写库属 FinanceTool 职责"不再是拒绝理由。
   **新的算法落点 = YuanShan 内计算**（从本地日线现算 + 缓存在 YuanShan 自己的目录），不再依赖 PG 的 `qd` 表。本文档 §4 的全部阈值/公式仍然有效 —— 只是**执行地点变了**：`qd` 那 9 张表退为**对照基准与镜像**（1169 个交易日的回填结果用来验 YuanShan 内算得对不对）。
   凭据问题由 `code：系统配置` 给 YuanShan 的 `.env` 配 `ft_etl`；PG 是 9.7GB 唯一真资产且**无可用备份**，所以任何写库动作前先备份。

## 2. 落库约定

新 schema：**`qd`**（不要塞进 `analytics`，避免和自家口径混淆；也不要叫 `quantdash`，太长）。

每张表**必须**有这四列（沿用 `analytics.ignition` 的习惯）：

| 列 | 类型 | 含义 |
|---|---|---|
| `trade_date` | `date` | **交易日**，主键之一。quantdash 的产物大多只有 `MM-DD`（`limit_up_structure.json` 等），那是缺陷 D-11，我们一律用完整日期 |
| `spec_version` | `text` | 本规格版本号，先用 `'qd-1'`。阈值改了要升版，不要静默改口径 |
| `calculated_at` | `timestamptz` | 计算时刻，与 `trade_date` 严格分开 |
| `source` | `text` | 固定 `'quantdash-logic'`；若某行混了实测数据，写 `'quantdash-logic+local'` |
| `deviation` | `jsonb` | **本行与 quantdash 原实现的已知偏差**，形如 `[{"code":"D-1","note":"原实现用线性模型伪造上涨家数，此处为本地日线实算"}]`。没有偏差写 `'[]'`，不要写 NULL |

数值纪律（沿用 [01-数据口径展示规范](../页面展示工作指南/01-数据口径展示规范.md)）：

- **NULL ≠ 0**。算不出来就是 NULL。quantdash 到处 `Number(x)||0`（D-4），我们不跟。
- **分母写进字段名**。`broken_rate_touched`（分母＝触板数）和 `broken_rate_sealed`（分母＝曾封板数）是两个字段，不许共用一个 `broken_rate`。
- 比率落库统一**分数**（0~1）还是**百分数**（0~100）？quantdash 自己两种混用（`successRate` 是分数、`brokenRate` 是百分数）。**我们统一存百分数 `numeric(6,2)`，字段名一律带 `_pct` 后缀**，展示层不再乘 100。
- 舍入照抄 quantdash 的位数（下面每个公式都标了），**在 SQL 里用 `round()` 显式做**，不要留给前端。

## 3. 输入可行性矩阵（先看这个，决定哪些能算）

本机 PostgreSQL `financetool` 实测（2026-09-24）：

| quantdash 的输入 | 我们有没有 | 说明 |
|---|---|---|
| 涨停池（含连板数 `lbc`） | ✅ 可推 | `market_data.stock_daily` 2021-08-02 ~ 2026-09-23，637 万行；连板数用 `duanxian/pg_sources.py:290 zt_pool()` 的算法（回看 90 自然日数连续涨停）。**判据必须双边严格** `abs(close-limit_up)<=0.005`，`analytics.market_breadth.limit_up_count` 是单边判据、虚高（09-22 给 70，实为 65）→ 不许直接取 |
| 炸板池 | ⚠️ 口径不同 | 日线只能推**触板未封**（`high>=limit_up and close<limit_up`）。09-22 实测：触板 37 只 vs 东财炸板池 18 只（后者是"曾封板后炸开，含收盘回封"）。**炸板率会被系统性抬高 64%**（0.363 vs 0.222）→ 必须按 §4.1 拆成两个字段 |
| 跌停池 | ✅ 可推 | 同涨停，双边判据 |
| 个股日 K（OHLC + 成交量额） | ✅ | `market_data.stock_daily` 有 `turnover_rate_percent`，**换手板判据可做**（quantdash 根本没实现换手板，这是我们比它强的地方） |
| 一字板 | ✅ 可推 | quantdash 判据：`|open-close|<0.001 且 |open-high|<0.001 且 |open-low|<0.001`（`fetch_sentiment_cycle_snapshots.py:178-182`）。直接照抄 |
| **封板时间 `lbt/zttime/fbt`** | ❌ **历史不可得** | `market_data.stock_minute` **只有 2026-09-22 一天**（1 分钟 226 万行 / 5 分钟 45 万行）。所以只有那一天能算首封时刻，之前全部 NULL。→ 龙头 tie-break 见 §4.2 |
| 指数成交额（上证 + 深成指） | ✅ | `market_data.index_daily` |
| 全市场涨跌家数 | ✅ 可推 | 日线实算，**禁止**用 quantdash 的线性估算模型（D-1） |
| 板块列表 / 板块涨幅 | ✅ | `reference_data.sector_info`、`market_data.sector_daily`（1249 个交易日、83.9 万行，**含 `up_count`/`down_count`/`member_count`** —— quantdash 没有板块级涨跌家数，我们有） |
| 板块热度 / 阶段 | ✅ 已有本轨 | `analytics.sector_heat_daily`（至 09-23）、`analytics.ignition`（scope='sector'，62 万行，至 09-23，五档 ignite/follow/coil/fade/none） |
| A 股平均 PE | ✅ **有源**（2026-09-24 `code：数据获取` 实测，更正本会话先前"没找到估值表"的判断） | `market_data.stock_snapshot`（源 `tdx_snapshot_db`，health=ok）有 `pe_ttm`/`pb`/`total_mv`：**5576 票/日、`pe_ttm` 100% 非空、覆盖 2026-07-22→09-23 共 30 个交易日**。实测 09-23 的 `0<pe<5000` 截尾均值 = **103.31**。30 日 > quantdash 要的 10 日 → 够用，且**天然不会犯 D-10**（无需回填）。⚠️ 必须标注的口径事实：**负 PE 有 1638 只（29%）**会被截尾剔掉，这是 quantdash 口径的固有偏差，不是我们算错。更早的历史**没有** —— `reference_data.stock_profile` 的 `pe_static_ttm/pe_dyn`（19007 行）是**无日期的当前快照**，只能当"今天"，**不许当时间序列** |
| 期指多空持仓 | ⚠️ **表无、源可采**（2026-09-24 实测，更正本会话先前"❌ 写 NULL"） | 东财 `datacenter-web` 的 `RPT_FUTU_POSITIONCODE`（取 `IS_MAINCODE='1'` 主合约，实测 `IF → IF2612`）+ `RPT_FUTU_NET_POSITION`（返回 21 页日序列；09-23 实测 多 112254 / 空 137549）。IF/IC/IH/IM 四个 `inner_code` 走同一张报表。⚠️ **过滤条件必须用 `SECURITY_CODE + TYSECURITY_INNER_CODE`**，照 `TRADE_CODE` 过滤直接报 `code 9501 TRADE_CODE列不存在`。→ 要入库归 `code：数据获取`，建表归 `code：数据加工`；未落地前写 NULL |
| 财联社电报 | ❌ **契约里的端点已死**（2026-09-24 实测） | 见 §4.4 的改判 |
| 个股资金流 | ✅ **新浪可全量**（2026-09-24 实测，见 §4.4 附） | 新浪 `MoneyFlow.ssl_bkzj_ssggzj`：**61 页 / 0 失败 / 35 秒 / 5080 个唯一代码**。东财 push2 `clist fid=f62` 拉不完（15 页成 8 败 7）—— 这就是 `market_data.fund_flow_rank` 恒空的真根因 |
| 板块级资金流 | ❌ | 新浪 `ssl_bkzj_bk` 返回 `{"__ERROR":1,"Input error"}`。要板块资金流只能用 `reference_data.sector_member` × 个股聚合**自算**（自算属本轨扩展，不是 quantdash 口径，`source` 要标 `'quantdash-logic+local'`） |
| 研报 | ❌ 先不做 | 见 §4.4 |

## 4. 五个功能的算法契约

### 4.1 情绪周期 → `qd.sentiment_cycle`

**每日一行**。quantdash 的阶段枚举 6 值：`冰点 / 试错 / 主升 / 分歧 / 修复 / 退潮`（默认 `分歧`）。

⚠️ **quantdash 自己有三套互相矛盾的阈值**（前端总览版 / MCP 版 / 飞书版）。这不是我们要修的 bug，这是要**如实呈现**的事实 —— 它的页面和它的 AI 看到的阶段可能不一样。所以存三列，都算：

| 列 | 照抄哪套 | 出处 |
|---|---|---|
| `stage_ui` | 前端总览版 | `services/sentimentCycle/overview.ts:46-76` |
| `stage_mcp` | MCP 版（AI 读的那套） | `scripts/mcp-server.js:611-646` |
| `stage_feishu` | 飞书机器人版 | `scripts/integrations/feishu/bot-server.js:173-212` |

**判定一律 if-elif 自上而下短路**，顺序不可换：

`stage_ui`（主口径）：

| 序 | stage | 条件 |
|---|---|---|
| 1 | 退潮 | `risk_level='high'` **OR** `leader_next_close_pct <= -5` **OR** `broken_repair_rate_pct < 20` |
| 2 | 冰点 | `board_height <= 2` **AND** `limit_up_count < 20` |
| 3 | 修复 | `broken_repair_rate_pct >= 35` **AND** `leader_next_close_pct >= 0` **AND** `volume_state <> '缩量再缩量'` |
| 4 | 主升 | `sentiment_value >= 5` **AND** `high_board_count >= 3` **AND** `leader_board_count >= 5` **AND** `volume_state <> '缩量再缩量'` |
| 5 | 试错 | `first_board_ratio_pct >= 60` **OR** `leader_board_count <= 4` |
| 6 | 分歧 | 兜底 |

`stage_mcp` 的差异只有两处：冰点用 `leader_board_count<=2 AND limit_up_count<20`；主升**不要求** `sentiment_value>=5`。
`stage_feishu`：默认 `分歧震荡`；退潮偏防守 = `risk_level='high' OR broken_rate_pct>=35 OR broken_repair_rate_pct<20`；修复偏进攻 = `broken_repair_rate_pct>=35 AND success_rate_pct>=65 AND leader_next_close_pct>=0`；主升延续 = `leader_board_count>=5 AND success_rate_pct>=70 AND a_kill_count=0`。

**`leader_next_close_pct` 缺失时**：quantdash 用哨兵 `-99` 参与比较（等于强行判成退潮方向）。**我们不搬**：缺失 → 该条件视为不成立，且 `deviation` 记 `D-5`。

**`risk_level` 三档**（`fetch_sentiment_cycle_snapshots.py:496-503`）：

- `high` ⇐ `a_kill_count >= 2 OR broken_rate_sealed_pct >= 35`
- `medium` ⇐ `a_kill_count >= 1 OR broken_rate_sealed_pct >= 20 OR weak_count >= 2`
- 否则 `low`；**输入全 NULL 时写 NULL，不写 `low`**（quantdash 把无数据当"低风险"，D-7）

**`volume_state` 四值**（`:558-568`，输入＝两市成交额日环比 `amount_change_pct`）：

| 值 | 条件 |
|---|---|
| 持续放量 | 最新一日与前一日 `amount_change_pct` **均 > 0** |
| 缩量再缩量 | 两者**均 < 0** |
| 放量滞涨 | 最新 > 0 **且** `sentiment_value[T] <= sentiment_value[T-1]` |
| 存量震荡 | 其余（含 change 为 NULL —— 这条 quantdash 就是这样，照搬，但 `deviation` 记明"NULL 被归入存量震荡"） |

**逐指标公式**（`prev`/`curr` = 昨/今各板数分布 `counts[b]`，b=1..14+）：

| 字段 | 公式 | 舍入 | 分母为 0 / 缺数据 |
|---|---|---|---|
| `sentiment_value` | `10 × Σ_{b=2..14, prev[b]>0} (curr[b+1] / prev[b]) ÷ 参与求和的 b 个数` | 2 位 | quantdash 退化时写常量 `5`（涨停>50）或 `2`（D-2）→ **我们写 NULL** |
| `board_height` | `max{b : counts[b] > 0}` | int | 无涨停 → 0（这条 0 是真值，不是缺数据） |
| `limit_up_count` / `limit_down_count` | 双边严格判据实算 | int | — |
| `first_board_count` | `counts[1]` | int | — |
| `first_board_ratio_pct` | `counts[1] / limit_up_count × 100` | 1 位 | 分母 0 → NULL |
| `high_board_count` | `Σ_{b>=4} counts[b]` | int | — |
| `high_board_ratio_pct` | `Σ_{b>=4} counts[b] / limit_up_count × 100` | 1 位 | 分母 0 → NULL |
| `relay_count` | `max(limit_up_count - counts[1], 0)` | int | — |
| `success_rate_pct`（晋级率） | `#{T日涨停 且 T+1日 lbc > T日 lbc} / limit_up_count × 100` | 2 位 | 分母 0 → NULL。**注意 quantdash 存的是分数**，我们统一 ×100 |
| `avg_board_gain` | `Σ min(next_board - board, 3) / limit_up_count`（**单股封顶 3**） | 不舍入，存 `numeric(6,3)` | 分母 0 → NULL |
| `premium_score`（涨停溢价） | `success_rate × 60 + avg_board_gain × 10`，值域 0~90 | 2 位 | 输入 NULL → NULL。**注意 quantdash 的 `success_rate` 是分数**，换算别搞错 |
| `broken_rate_sealed_pct` | quantdash 面板口径 = `(1 - success_rate) × 100` | 1 位 | — |
| `broken_rate_touched_pct` | quantdash 高位风险口径 = `炸板池 / 涨停池 × 100`。**我们只能用触板未封近似** → `deviation` 记 `D-3`，并在字段注释里写清分母是"触板数" | 1 位 | 分母 0 → NULL |
| `broken_repair_rate_pct` | 昨日炸板（我们用触板未封）中「次日收盘涨幅 > 0」占比 ×100 | 1 位 | 池空 → **NULL，不是 0**（quantdash 写 0，与真实 0% 不可区分，D-6） |
| `big_face_count` | 炸板池中 `当日涨跌幅 <= -5` 的家数 | int | — |
| `big_face_repair_rate_pct` | 上述子集中「次日收盘 > 0」占比 ×100 | 1 位 | 分母 0 → NULL |
| `a_kill_count` | `#{板数>=4 的高板股，次日收盘涨幅 <= -8}` | int | 次日无 K 线 → 该股跳过，**并把跳过家数记进 `deviation`** |
| `weak_count` | 同上，次日收盘涨幅 `< 0` | int | 同上 |
| `rise_count` / `fall_count` / `flat_count` | **本地日线实算**（`close > pre_close` 等），排除北交所与否要写进 `scope` 列 | int | **严禁** quantdash 的 `clamp(round(2000 + 指数涨幅×650 + (涨停-跌停)×4), 200, 4500)` 估算模型（D-1） |
| `amount_yi` | `(上证 amount + 深成 amount) / 1e8` | int（quantdash 用 Python banker's rounding，照抄） | 任一只缺失 → NULL |
| `amount_change_pct` | `(amount - prev_amount) / prev_amount × 100` | 2 位 | 首日或 `prev<=0` → NULL |
| `confidence` | `base(stage) + risk_adj + vol_adj`，`min(95, ·)`。base：主升 72 / 修复 68 / 退潮 75 / 冰点 70 / 其他 62；risk_adj：low +6 / high +8 / 其他 +3；vol_adj：`持续放量` 或 `缩量再缩量` +6，否则 0 | int | **照搬，但必须在字段注释里写明"这是确定性查表加和，不是统计置信度"**（D-8）。理论最大 89，`min(95,·)` 恒不触发 —— 保留这个死代码上限没意义，**去掉上限，直接存加和**，`deviation` 记一条 |
| `stage_reasons` | `jsonb` 数组，**每个 stage 各一条**，形状 `[{stage:"stage_ui"\|"stage_mcp"\|"stage_feishu", hit_rule:<优先级序号>, reason:"中文"}]`。归属规则见 [页面展示 02 §8.1](../页面展示工作指南/02-quantdash逻辑双轨展示.md)，**不许混成一个不分来源的字符串数组** | — | 兜底 `分歧` 时 quantdash 不给 reason（D-9）→ **我们给一条 `"未命中任何阶段条件，兜底为分歧"`** |

**日期错位问题（重要）**：quantdash 判阶段时 `leader[-1]`/`sentiment[-1]` 是 T 日，而 `repair[-1]`/`high_risk[-1]` 是 **T-1** 日（因为修复率天然要看次日表现）。它自己没意识到，混着用（D-4）。**我们的做法**：`qd.sentiment_cycle` 一行只描述 `trade_date=T`，其中"修复类"指标**明确是 T-1 场次修到 T 的结果**，字段名带 `_lag1`：`broken_repair_rate_lag1_pct`、`big_face_repair_rate_lag1_pct`、`a_kill_count_lag1`、`weak_count_lag1`、`leader_next_close_pct`（＝T-1 龙头在 T 日的收盘涨幅）。阶段判定用这些 `_lag1` 列，并在 `deviation` 记 `D-4` 说明"原实现日期错位，此处已对齐并显式命名"。

### 4.2 连板天梯 → `qd.board_ladder`

**每日一行一板级**（`trade_date`, `board_tier`）+ 成分股明细 `qd.board_ladder_member`。

板级枚举 —— quantdash 固定 6 行（`fetch_market_core_snapshots.py:249-256`）：

| `board_tier` | quantdash label | 收纳 |
|---|---|---|
| 7 | 七板以上 | `lbc >= 7` |
| 6 | 六连板 | `lbc = 6` |
| 5 | 五连板 | `lbc = 5` |
| 4 | 四连板 | `lbc = 4` |
| 3 | 三连板 | `lbc = 3` |
| 2 | 二连板 | `lbc = 2` |
| **1** | **首板** | **quantdash 没有这一行** —— 而且它的分桶代码在找不到对应行时**默认塞进第 0 行（七板以上）**，导致首板股出现在"七板以上"里（D-12）；TS 版则是静默丢弃（行为相反）。**我们补 `board_tier=1`，并在 `deviation` 记 `D-12`** |

`qd.board_ladder` 字段：`trade_date, board_tier, stock_count, is_highest_tier(bool), spec_version, calculated_at, source, deviation`。

`qd.board_ladder_member` 字段：

| 字段 | 口径 |
|---|---|
| `security_code`, `stock_name` | — |
| `board_count` | 真实连板数（不封顶，`lbc>=7` 的也存真值，比如 9 就存 9；`board_tier` 才是分桶） |
| `industry_name` | quantdash 用涨停池的 `hybk`，缺失填 `'市场热点'`（D-13）→ **我们缺失写 NULL，不编行业** |
| `concept_name` | quantdash 缺失填 `'连板'` → 同上，写 NULL |
| `limit_up_pct_regime` | **真实涨跌幅档位**（10/20/30/5），按 `reference_data.security` + 代码前缀判。quantdash 是由代码前缀硬推 `pctChange`（30/68→20，其余→10，Python 版还漏了北交所 30 档，两端不一致，D-14）→ 我们存**档位**不存假涨幅，真实涨幅从 `stock_daily.pct_change` 取，字段 `pct_change_actual` |
| `is_one_word` | 四价相等容差 0.001（照抄） |
| `is_turnover_board` | **quantdash 全库无此概念**。我们用 `turnover_rate_percent` 判：非一字 且 换手率 >= 阈值 → true。阈值先取 **5%**，写进 `config.setting`（key `qd_turnover_board_pct`）便于调；这是**本轨扩展**，`source` 标 `'quantdash-logic+local'` |
| `first_seal_time` | 首次触及涨停价的分钟。**只有 2026-09-22 有分钟线** → 其余全 NULL |
| `seal_time_available` | `bool`，明示这行的封板时间是不是真有的。**前端必须用它决定要不要显示时间**，不许把 NULL 显示成 `--` 就当没事 |
| `price_close`, `amount_yuan` | 真实值。quantdash 在天梯里塞 `price=0, pe=0, volume='-', turnover='-'` 占位常量（D-15）→ 不搬 |

**最高标 / 龙头**（`qd.board_ladder_leader`，每日一行）：

| 字段 | 口径 |
|---|---|
| `leader_board_count` | `max(lbc)` |
| `leader_count` | `#{lbc = leader_board_count}` |
| `second_highest_board` | `max{lbc < leader_board_count}`，无则 **NULL**（quantdash 写 0，D-16） |
| `three_plus_count` | `#{lbc >= 3}` |
| `continued_count` | `#{同高度龙头，次日 lbc > leader_board_count}` |
| `leader_security_code` | **tie-break 是 quantdash 的核心口径：并列时取"最早封板者"**（`sort(key=str(limitUpTime or "99:99:99"))[0]`）。⚠️ 两个坑：① 我们历史无封板时间；② quantdash 自己排序键**没归一化**，`lbt` 可能是 int `93000` 或非补零串，按字符串比会得到 `"142500" < "93000"` → **它的龙头判定本身就是错的**（D-17）。<br>**我们的裁定**：有 `first_seal_time` 就按它升序取首个；没有 → 按 `amount_yuan` 降序取首个，并置 `leader_tie_break='amount_no_seal_time'`；并列仍不解 → 按 `security_code` 升序，置 `leader_tie_break='code_asc'`。**这个字段必须落库**，前端要显示"龙头是怎么选出来的" |
| `leader_tie_break` | 枚举：`seal_time` / `amount_no_seal_time` / `code_asc` / `no_candidate` |
| `leader_next_open_pct`, `leader_next_close_pct` | 次日开盘/收盘涨幅，`(x_T+1 - close_T)/close_T × 100`，2 位。K 线索引取"**最后一个 date <= 目标日**"（quantdash 口径，非交易日静默前移 → 我们用 `reference_data.trade_calendar` 显式取下一交易日，不静默前移，`deviation` 记 D-18） |
| `leader_status_label` | 9 值，照抄 `fetch_sentiment_cycle_snapshots.py:346-368`：见下表 |

**龙头状态标签**（if-elif 顺序短路）：

| 前置 | 条件 | 标签 / MCP 英文码 |
|---|---|---|
| `next_close_pct` 为 NULL | `leader_count>=2 AND leader_board_count>=4` | 高标抱团 / `high_leader_cluster` |
| | `is_one_word` | 一字观察 / `one_word_watch` |
| | 其余 | 待次日确认 / `await_confirmation` |
| `next_close_pct` 有值 | `is_one_word AND continued` | 一字加速 / `one_word_acceleration` |
| | `continued AND next_close_pct>=0` | 强势晋级 / `strong_continuation` |
| | `next_close_pct>=3` | 断板承接 / `accepted_after_break` |
| | `next_close_pct>=0` | 高位分歧 / `high_level_divergence` |
| | `next_close_pct>-5` | 分歧转弱 / `weakening` |
| | 其余 | 退潮承压 / `retreat_pressure` |

**板高度分市场**（`qd.board_height`，每日一行）：quantdash 的前缀白名单是 主板 `600/601/603/605/000/001/002/003`、创业板 `300/301`，**排除科创板 688/689 与北交所 8xx/4xx**（D-19）。
裁定：字段照它的分法存 `main_board_highest` / `main_board_second_highest` / `chinext_highest`（+ 各自的 `_names` / `_codes` 数组），**另加 `star_highest`（科创板）与 `bj_highest`（北交所）两列**，`deviation` 记 D-19 说明"原实现忽略科创板与北交所"。值为 0 时 quantdash 置空列表 —— 我们保持空列表（这条不是缺陷，是显示约定）。

**天梯昨今对比**（前端算，不落库）：quantdash 的配对规则是 `upgrade_target = tier>=7 ? tier : tier+1`，用昨日第 N 板 vs 今日第 N+1 板配对；今日在别层 = "停留在 X"；都不在 = "未晋级"并显示当日收盘涨幅；今日新增昨日无 = "今日新晋"。**这套配对逻辑照搬**，写进展示契约。

### 4.3 板块周期 → `qd.sector_rotation` + `qd.sector_persistence`（+ 10-25 新增 `qd.sector_follow`，那一族是本机自写）

⚠️ **先说清楚**：quantdash 的"板块周期"**没有阶段枚举**（阶段枚举属于情绪周期，见 §4.1）。它的板块周期实质是**轮动矩阵 + 持续性统计**。所以我们本轨的 `analytics.ignition`（五档 ignite/follow/coil/fade/none）**不是重复实现，是另一套东西**，两轨都要展示。

**取样范围**（`fetch_sector_snapshots.py:16-45`）：**仅概念板块 + 行业板块，无地域板块**。东财 `clist` 按涨幅倒序取**前 24** 个。
我们的对应做法：从 `market_data.sector_daily` 按 `pct_change` 倒序取前 24，`sector_type` 用 `reference_data.sector_info` 的 `sector_type in ('概念','行业')` 过滤。**top_n=24 照搬**，写进 `config.setting`（key `qd_sector_top_n`）。
⚠️ quantdash 的 MCP 版取的是 **top 20 且无名称 tie-break**（`mcp-server.js:397,418`），与它自己的 Python/TS 版不一致（D-20）→ 我们统一 24，排序键 `(pct_change desc, sector_name asc)`，`deviation` 记 D-20。

**`qd.sector_rotation`**（每日一行一板块）：`trade_date, sector_code, sector_name, sector_type, day_rank, pct_change, up_count, down_count, member_count`。
`day_rank` = 当日按 `(pct_change desc, sector_name asc)` 的名次；轮动矩阵只保留 **`day_rank <= 8`**（quantdash 每日取前 8）+ 最近 **5 个交易日**（quantdash 取前 5）。
`up_count`/`down_count`/`member_count` 是**我们比 quantdash 多的**（它只有市场级涨跌家数，没有板块级）→ `source='quantdash-logic+local'`。

**`qd.sector_persistence`**（每日一行，窗口＝日期升序末 5 天）：

| 字段 | 公式 | NULL 处理 |
|---|---|---|
| `current_leader_name` | 最新日 `day_rank=1` 的板块名 | 空 → NULL（quantdash 是"不输出该字段"，等价） |
| `streak_days` | 自最新日向前回溯 `current_leader_name` 连续相同的天数 | 最小 1（照搬） |
| `top_three_appearances` | 5 日内进入 `day_rank<=3` 的天数 | quantdash 默认 1（D-21，缺数据当 1 天）→ **我们写 0，并 `deviation` 记 D-21** |
| `strength_delta_pct` | 当日 leader 涨幅 − 前日 leader 涨幅，**仅两日同名时才算**，否则 NULL（前端显示"新晋领涨"） | NULL 显式保留（这条 quantdash 做对了，照搬） |
| `strongest_repeat_name` | `argmax(top_three_appearances desc, sector_name asc)` | quantdash 缺失时回落为当前 leader（D-22）→ **我们写 NULL** |

**`qd.sector_follow`**（2026-10-25 新增，**这一族 quantdash 没有**，是本机按用户点名的判据自写、落在这条轨的母体口径上）：
一行 = `(trade_date, lag, 领涨板块, spec_version)`，`lag` 四档 0/1/2/3 全落（接口的 `lag` 参数开的就是这四档，只落一档就是"参数能填、永远读不到行"）。
母体与排名尺子**不许重抄**：`MOTHER_POOL` / `MOTHER_RANK_ORDER` / `MAX_SECTOR_PCT` 只住在 `qd_sector.py`，
本模块的 SQL 是**字面把那段拼进去**的（有一条测试逐字钉这件事），否则第 1 名会在两块之间换人。
为什么必须落库而不是读侧现算：`sector_rotation` 只留 `day_rank<=8`，"8 名之外某天排第几"库里查不到，
形状匹配要回 `market_data.sector_daily` 逐场重排全 914 个板块 —— 这件事每天只需算一次（实测回算全史 1,264 场 40 场一片约十分钟），
放在读侧等于每个访客重付那两秒。三条落库纪律：缺行交 NULL 不折 0；缺行的板块不进榜、只进 `skipped_incomplete`；
跨日名次可比性守卫与 §4.3 的持续性共用 `qd_common.universe_break`（断点落 `L-22`，实测**当前全史它一次都没发过**）。
deviation 码：`L-22` 窗口跨母体扩容、`L-23` 候选池与当日母体是两个分母、`L-24` 模板缺场次（整块不比对，不给空榜）。

**板块热度配色**（前端，不落库）：quantdash **只用涨幅**配色，无资金流：`>=4` 深红 / `>=2` 红 / `>=0` 浅红 / `<0` 绿（`components/SectorCycleSection.tsx:50-55`）。**阈值照搬**，写进展示契约。

**⚠️ 禁止复现的口径扭曲**：quantdash 前端把涨跌家数**按 `ratio = 5100 / 实际总数` 强制缩放**到硬编码的 5100（差 ≤100 不缩放），平盘家数由 `5100 - rise - fall` 反推（`services/sentimentCycle/sentiment.ts:141-152`）。这是 D-23，**任何情况下都不许出现 5100 这个常量**。

### 4.4 信息采集 → `qd.news_item` + `qd.research_report`

采集侧（抓取、限流、去重）归 **`code：数据获取`**；建表与清洗归 **`code：数据加工`**。

⚠️ **2026-09-24 改判：财联社这一路按原契约做不成 —— 端点已死**（`code：数据获取` 实测）：

| 试的端点 | 结果 |
|---|---|
| `nodeapi/telegraphList`（本契约原端点） | **404，返回 HTML** |
| `nodeapi/updateTelegraphList` / `nodeapi/depthList` | 404 |
| `/v1/roll/get_roll_list` | 活着，但 `errno 10012 签名错误` → **域名没封，签名方案换了**（`sign=md5(md5(query))` 照搬已无意义） |
| `www.cls.cn/telegraph` 网页 | 200，含 `__NEXT_DATA__`（可解析，但依赖前端结构，脆） |

**改为快讯双源**（都已实测 200/JSON）：

| 序 | 源 | 端点与要点 |
|---|---|---|
| 主 | **东财 7×24** | `np-weblist.eastmoney.com/comm/web/getFastNewsList` → `total=5000`、**自带 `sortEnd` 游标和逐条 id**、`summary` 即正文、免鉴权 |
| 备 | **新浪 7×24** | `zhibo.sina.com.cn/api/zhibo/feed?zhibo_id=152` → `feed.list[].id + rich_text` |

**下面这批裁定原样适用**，只是签名与 `last_ts` 文本游标那部分换成各源自带游标：30 秒轮询、游标落 `operations.*`、不截断 200 条、`sentiment` 为 NULL、`published_at` 与 `captured_at` 分列、`item_id` 唯一。

**两个硬前置（不在采集侧手里，卡着这条就不能算完成）**：

1. ~~`qd` schema 与游标表都还不存在~~ **已建**（本文档 §9 的回执行里就写着 09-24 落了 DDL，这一行是更早的草稿没回收）：10-18 现查 `qd` schema **9 张表**（`sentiment_cycle`/`board_ladder`/`sector_rotation`/`news_item`/`stock_strength` 等），游标表同在 `qd`；DDL 归 `code：数据加工`。⚠️ 但**别把 qd 轨当原口径**：`退潮占 80%`、`龙头 99.9% 非原口径` 那两条复算差异见《qd 双轨》。
2. **本项目自己的调度器是全关的**（`scheduler_enabled=false`、`dry_run=true`、心跳 `fired:0`）→ **30 秒轮询没有宿主**。要么给一条常驻进程，要么这条就是跑不起来。**不能退化成"每天手工点一下"** —— 快讯和资金流都**只有即时时间戳，断档不可回补**（见下）。
   ⚠️ 旧版这里写的"计划任务 `FinanceTool-DailyCloseSync` 从未跑"**是错的，就地改正**：那条 Windows 计划任务
   2026-09-24 与 2026-09-25 的 16:05 **各真跑过一次**（c18/c19/c20 三步写库成功、结尾 `taskkill` 强杀通达信，`LastTaskResult=1`），
   已于 2026-09-25 `Disable-ScheduledTask` 停用。它跑的是**收盘后的一次性追平**，本来就**不等于 30 秒轮询的宿主** ——
   所以"没有宿主"这个结论不变，但依据要换成"本项目调度器关着 + 盘后任务只做日频追平"，不是"那边从没跑过"。

**A 股快讯/资金流的"不可回补"性质**：新浪资金流响应里**没有日期字段**，只有即时截面 → **当天不采就永久没有**。这决定了它必须是常驻采集，不是回填任务。

**`qd.news_item` 建表**：`item_id(text, pk), published_at(timestamptz), captured_at(timestamptz), source(text), level(text), title(text), content(text), url(text), sentiment(text, nullable), news_type(text)`。
**`published_at` 与 `captured_at` 必须分列**（D-25 的教训）。

**个股资金流入库的四条实测坑**（`code：数据获取` 2026-09-24 全量压测，务必写进字段注释）：

1. 榜单**混 620 只非股票**（sh51 债/基、sz20、sh90 B 股），而且**主力净流入第一名就是"公司债ETF平安" 107.8 亿** —— 不 join `reference_data.security` 过滤，龙头资金榜第一位是只债基。
2. **无日期字段** → 只有即时截面，当天不采就永久没有。
3. 覆盖 5080 只 vs 库内 5576 只，**缺的约 500 只必须 NULL，不许补 0**。
4. `name` 字段有脏值（实测出现 `False`），一律以主档 join 后的名称为准。

**财联社电报**（`dingding盘中资讯2/.../cls_telegraph_to_dingtalk_single.py`）—— **以下为原始调研记录，端点已死，保留仅供追溯**：

| 项 | quantdash 的值 | 我们的裁定 |
|---|---|---|
| 端点 | `https://www.cls.cn/nodeapi/telegraphList`，`sign = md5(md5(query))` | ❌ **已 404**，改东财/新浪 7×24 |
| 级别过滤 | `level in ('A','B')` | 照搬，但 `level` 要落库，别过滤掉就丢 |
| 条数 / 轮询 | `rn=50`（env `CLS_RN`）、5 秒一轮、启动回溯 240 分钟 | 轮询 5s 太密，**改 30s**；回溯 240 分钟照搬 |
| 重试 | 3 次，退避 `2^i + random` | 照搬 |
| 增量游标 | 仅 `ctime > last_ts`，`last_ts` 落文本文件 | 改落 `operations.*` 游标表，别用文本文件 |
| 去重键 | API `id`，缺失则 `cls-{ctime}-{md5(title)[:8]}` | 照搬，`item_id` 唯一约束 |
| 情感 | **`sentiment = null`**（它根本没做情感） | 照搬为 NULL。**不许**用前端那套 `RECENT_SENTIMENT` 硬编码分数（D-24） |
| 存储上限 | 每文件截断 200 条，整文件重写 | **不搬**。落 PG，不截断；200 条是它的 JSON 文件方案限制 |

**NewsFilter（Playwright 抓浏览器 DOM）**：quantdash 用 CDP 接管本机 Chrome（`127.0.0.1:19223`），卡片选择器 `div.sc-htoDjs`，刷新 15~35s 随机，时效过滤 `RECENT_MAX_MIN=2`。
裁定：**这一路不搬**。理由：① 依赖用户本机常开一个带调试端口的 Chrome，脆弱；② 它的 `created_at` 用 `datetime.now()` **冒充发布时刻**（D-25），数据本身不可信；③ 选择器是第三方页面的 CSS class 哈希，随时失效。要做外部资讯，走 `code：数据获取` 已验证抗封的渠道（见 [数据获取工作指南/05-外部数据源地图](../数据获取工作指南/05-外部数据源地图.md)）。

**`qd.news_item`** 建表：`item_id(text, pk), published_at(timestamptz), captured_at(timestamptz), source(text), level(text), title(text), content(text), url(text), sentiment(text, nullable), news_type(text)`。
**`published_at` 与 `captured_at` 必须分列**（D-25 的教训）。

**新闻 → 个股/板块 的匹配规则**（`services/aiDailyReview/promptBuilders.ts:280-297`）：关键词集合 = {代码, 名称, 行业, 概念}，对 `title || content` 做大小写不敏感子串匹配，命中取前 4。
⚠️ **零命中时它回退取前 3 条**（等于硬塞无关新闻给 AI）→ **不搬**，零命中就返回空数组，让 AI 如实说"无相关新闻"。
⚠️ 真实新闻为空时它回落 5 条逼真的 `MOCK_NEWS`（带情感标签，界面上无法区分，D-24）→ **绝对禁止**。

**研报**（`scripts/fetch_research_reports.py`）：5 个东财栏目（个股/行业/策略/宏观/券商晨会）+ 3 个快照源，每栏目上限 40 条，摘要 ≤4000 字符，输出 markdown 文件 + manifest。
裁定：**先不做**。PDF 需要过 `__tst_status`/`EO_Bot_Ssid` cookie 挑战，属对抗性爬取；且它的 `published_at` 解析失败会**回填当前时间**（D-26）。等 `code：数据获取` 有稳定研报渠道再说。

**个股强弱分档**（喂 AI 用，`promptBuilders.ts:260-266`）：`>= +3%` 强 / `<= -2%` 弱 / 其余中性。**阈值照搬**，落 `qd.stock_strength`（或直接在前端算，二选一，别两处都算）。

### 4.5 AI 对接 → **不落库，是契约**

quantdash 的 AI 对接有三样东西值得搬，都**不是代码**：

**(a) STRUCTURED_DATA_RULES.md 六条硬规则**（`:22-27`）—— 直接采纳为本次所有新表的验收标准：

1. 先定义结构化数据对象，再写取数；
2. 明确数据来源；
3. 明确服务层入口（页面不许直接拼原始源数据）；
4. 明确本地落盘/缓存策略；
5. 页面只消费结构化对象；
6. **若该数据将被 AI 使用，MCP 或服务层必须能读到同一份结构化结果**（即：AI 看到的和人看到的是同一份数，不许 AI 走另一条取数路径）。

字段要求（`:52-57`）：字段名稳定、语义单一、日期用 `YYYY-MM-DD`/ISO、**数值保持数值类型不预格式化**、枚举可预期（`source_type`/`risk_level`/`stage`）、保留 `source`/`updated_at`/`trade_date`/`symbol` 元信息。

⚠️ 第 6 条对我们有特殊意义：YuanShan 的 `review_agent` 有**冻结证据包**机制（一次复盘的证据在运行前冻结），新表要接进去必须走 `duanxian/pg_sources.py` 那条读取路径，**不许**给 AI 单开一个取数口。

**(b) MCP 工具面**：quantdash 有 12 个 stdio MCP 工具（`get_market_dashboard`、`get_leader_state`、`get_sector_persistence`、`get_cycle_overview`、`get_volume_trend`、`get_high_risk_panel`、`get_sentiment_snapshot`、`get_news_feed`、`get_research_reports`、`get_research_report_content`、`get_expert_holding_snapshots`、`get_expert_holding_snapshot`），返回 text 是 JSON **截断 12000 字符** + 另附完整 `structuredContent`。
YuanShan 已有 6 个受控 MCP 工具。**裁定**：不加工具数量，改为把 qd 轨数据**并进现有工具的返回体**（例如 `get_cycle_overview` 的返回里同时给 `stage_own` 与 `stage_qd_ui/stage_qd_mcp`）。理由：工具面越大，引擎的 function_call 命名空间出错概率越高（本机已有过"缺 namespace 静默变成 unsupported call"的教训）。

**(c) Prompt 风格约束**：quantdash 6 套模板共有的两条值得抄进我们的复盘 prompt——
- **"数据不足必须指出，不要补脑"**；
- **先结论后依据 + 固定分节输出**。
另外它的 `{{var}}` 渲染规则：变量为 null/undefined/空串时渲染成 `—`（`rendering.ts:89-96`）。**这条要搬**，因为空值渲染成 `—` 比渲染成 `0` 或空字符串诚实。

**(d) 游资 skills**（`游资skills/爱在冰川.md`、`陈小群.md`）：quantdash 把它们作为文本追加进 prompt，标题写明"**优先级高于默认风格要求，但不能违背事实数据**"（`promptBuilders.ts:218-221`）。
⚠️ 这与 Vibe-Trading 那 90 个 skill **不是一回事**（那批是引擎级 skill，已被强制禁用，且会破坏 agent 启动）。quantdash 这套只是 prompt 追加文本 → **可以搬**，落成 `config.help_note` 或 prompt 模板片段，**不要**注册成引擎 skill。

**(e) 输出校验**：quantdash 的"验证结论"**不是模型结构化输出**，而是对模型文本做关键词正则分类（`明显偏差|基本失效|…` → missed；`部分成立|…` → partial；`整体正确|…` → matched；**否则一律 partial + "待人工判断"**）（`services/aiDailyReview/utils.ts:26-39`）。
裁定：**不搬**。YuanShan 已有更强的 `validate_answer` 引用校验。关键词正则分类会把"没提到"和"部分成立"混为一谈。

## 5. 建表清单（给 `code：数据加工计算`）

| 表 | 粒度 | 主键 |
|---|---|---|
| `qd.sentiment_cycle` | 每日一行 | `(trade_date, spec_version)` |
| `qd.board_ladder` | 每日一行一板级 | `(trade_date, board_tier, spec_version)` |
| `qd.board_ladder_member` | 每日一股 | `(trade_date, security_code, spec_version)` |
| `qd.board_ladder_leader` | 每日一行 | `(trade_date, spec_version)` |
| `qd.board_height` | 每日一行 | `(trade_date, spec_version)` |
| `qd.sector_rotation` | 每日一板块（`day_rank<=8`） | `(trade_date, sector_code, spec_version)` |
| `qd.sector_persistence` | 每日一行 | `(trade_date, spec_version)` |
| `qd.sector_follow` | 每日一行一领涨板块一错开档（前三甲 × `lag` 0/1/2/3 ⇒ 12 行/天） | `(trade_date, lag, sector_code, spec_version)` |
| `qd.news_item` | 一条一行 | `(item_id)` |
| `qd.stock_strength` | 每日一股（可选，若前端算则不建） | `(trade_date, security_code)` |

DDL 要求：每张表 `comment on table` 写 **"逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），仅重实现其算法口径，未复制源码。规格见 数据加工工作指南/09-quantdash逻辑规格.md"**；每个照搬阈值的列 `comment on column` 写出处 `文件:行`。
授权：`grant select on all tables in schema qd to ft_reader;`（YuanShan 只读）；写入用 `ft_etl`。
迁移文件按现有习惯放 `app/db/sql/`，编号接在 `11_jobs_tdx_enrich.sql` 之后。
作业登记：新增 job_key `calc_qd_sentiment` / `calc_qd_ladder` / `calc_qd_sector`，注册进 `config.job` + `app/orchestrator/runner.py` 的 `RUNNERS`，先做**手动触发**，跑稳了再排定时（依赖日线入库完成，见 [07-定时任务](07-定时任务.md)）。

**回填范围**：`market_data.stock_daily` 有 2021-08-02 起的数据，但涨停池推导需要 90 日回看 → **从 2021-12-01 起回填**。第一次先只跑最近 30 个交易日验证口径，再全量回填。

## 6. 派活边界（2026-09-24 按"YuanShan 唯一落地点"重写）

⚠️ 本节旧版把计算与建表派给 FinanceTool 侧会话，**已作废**。用户裁定 FinanceTool 退役、YuanShan 全吸收，所以：

| 落点 | 干什么 |
|---|---|
| **`YuanShan/duanxian/qd_calc.py`（新）** | §4 全部算法在 YuanShan 内实现：从 `market_data.stock_daily` 等只读表现算，产结构与 `/api/qd/*` 契约一致 |
| `YuanShan/.local/qd_cache/`（新） | 现算结果按 `trade_date` 落本地 JSON 缓存（沿用 `breadth.py` 的 `atomic_write_json` 那套），避免每次请求重算 1169 天 |
| PG `qd` 9 张表 | **退为对照基准**：YuanShan 内算出的每个数要能和已回填的 1169 天对上，对得上才证明搬对了。~~不再扩列、不再回填~~ → 见本节末改判：本项目是唯一写入方，基准算错了允许纠正 |
| `YuanShan/duanxian/qd.py` | 从"读 PG qd 表"改为"走 qd_calc（带本地缓存）"；**接口响应形状保持不变**，前端不用改 |
| `YuanShan：数据加工` | 停止 `qd` 侧新工作；收尾那条 09-23 板块 universe 异常（它关系到对照基准本身可不可信）；转做吸收方案 |
| `YuanShan：数据获取` | 快讯采集落在 YuanShan 本地（东财 7×24 主 / 新浪 7×24 备），宿主用 YuanShan 常驻 uvicorn 进程，不再等旧项目调度器 |
| `YuanShan：页面展示` | 双轨展示已完成（`/qd-compare`），无需改动 |
| `YuanShan：系统配置` | ~~给 YuanShan 配 `ft_etl` 凭据~~ ✅ 已完成（09-25：四个角色的 DSN、超管口令等 19 个键已从旧项目 `.env` 迁入本项目 `.env`）；~~先做一次可用的整库备份~~ ✅ 已完成（daily/weekly 两档，判据是 `pg_restore -l` 能列 TOC）；剩下是定调度宿主与注册三档备份任务 |
| 本会话（总协调） | 写 `qd_calc.py` 与改造 `qd.py`；对账 `qd` 表基准 |

⚠️ **2026-09-25 补一条改判**（用户：「PG 的这个库就只有你能写，读的话可能会有别的人来读，
但写、更新和删除就只有你能做」）：上表把 `qd` 9 张表写成"不再扩列、不再回填"是**双跑期**的纪律，
那个前提已经不成立了 —— 旧那一边已停，本项目是唯一写入方。现在的准确说法是：

- `qd.*` 仍是**对照基准**（1169 天已回填的历史），但它是"跟自己对"的基准，不是"跟另一个进程对"的护栏；
  发现本项目算得对而基准错时，**允许回填纠正基准**（走临时表比完再覆盖，并记一条 deviation）。
- 唯一还禁止的是**第二个写入进程**：包括旧项目那条没停成的计划任务（已 `Disable` 停用），
  也包括本项目 `s9_ingest.py` 的 `--legacy` 分支（它往 PG 之外写 SQLite，属违禁品，待删）。

## 7. 禁止复现清单（quantdash 的 42 处伪造/兜底，逐条给出处）

编号即上文 `deviation.code`。**左列一律不许出现在我们的实现里**；确实无法真算的，写 NULL + 记 deviation。

| 编号 | 缺陷 | 出处 |
|---|---|---|
| D-1 | 上涨家数用线性模型 `clamp(round(2000+指数涨幅×650+(涨停-跌停)×4), 200, 4500)` 伪造，前端却标"实盘" | `fetch_market_core_snapshots.py:81-84`；`sentiment.ts:57-65` |
| D-2 | 情绪分退化时写常量 `5`（涨停>50）或 `2`；TS 兜底路径**恒用常量**，从不算公式 | `fetch_market_core_snapshots.py:173`；`sentiment.ts:210` |
| D-3 | 涨停池抓取失败 `except Exception` → **把空列表写进缓存**并返回，同日全部指标静默变 0（炸板率 0 / 修复率 0 / riskLevel=low） | `fetch_sentiment_cycle_snapshots.py:221-223, 251-253` |
| D-4 | 阶段判定**混用不同交易日**：leader/sentiment 是 T，repair/high_risk 是 T-1 | `fetch_sentiment_cycle_snapshots.py:395-422, 483-514, 549-556` |
| D-5 | `试错` 条件 `leader_board_count<=4`，缺失时 `int(... or 0)=0<=4` 恒真 → **无数据即判"试错"**（实测其 `cycle_overview.json` 就是 stage=试错/confidence=65） | `:588-593` |
| D-6 | `broken_repair_rate` 池空 → 写 0，与真实 0% 不可区分 | `:399-420` |
| D-7 | `risk_level` 缺失 → 默认"低风险"（把无数据当安全） | `:630-634` |
| D-8 | `confidence` 是查表加和却以百分比展示；`min(95,·)` 恒不触发（理论最大 89） | `:607-629`；`mcp-server.js:646` |
| D-9 | 兜底 `分歧` 时不给任何 reason 文案 | `:570-593` |
| D-10 | A 股平均 PE：**最新一日 PE 回填给全部 10 个历史日**，历史估值曲线失真 | `fetch_emotion_indicators.py:490-494` |
| D-11 | 年份硬编码 `"2026-"`：把 `MM-DD` 补成 2026 年 | `fetch_emotion_indicators.py:253, 341` |
| D-12 | **首板股被塞进"七板以上"行**（Python 默认取第 0 行）；TS 版则静默丢弃 —— 两端行为相反且都错 | `fetch_market_core_snapshots.py:304-306` vs `stockService.ts:150-153` |
| D-13 | 行业缺失填 `'市场热点'`、概念缺失填 `'连板'`（编造分类） | `fetch_market_core_snapshots.py:310-317`；`mcp-server.js:157`（行业填 `'Unknown'`） |
| D-14 | `pct_change` 由代码前缀硬推（10/20/30），非真实涨幅；Python 版漏 30cm 档，与 TS 版不一致 | `fetch_market_core_snapshots.py:311` vs `stockService.ts:130-134` |
| D-15 | 天梯里 `price=0, pe=0, volume='-', turnover='-'` 占位常量，但类型与真实个股快照同构，下游无法区分 | `fetch_market_core_snapshots.py:310-317` |
| D-16 | `second_highest_board` 无值时写 0（与"真有一板"不可区分） | `:424-436` |
| D-17 | 龙头 tie-break 排序键未归一化：`lbt` 可能是 int `93000` 或非补零串，按字符串比 → `"142500" < "93000"`，**最早封板判定错误** | `fetch_sentiment_cycle_snapshots.py:210-215, 429` vs `limitUpPoolService.ts:23-55` |
| D-18 | K 线取"最后一个 date <= 目标日"，非交易日**静默前移**，不提示 | `mcp-server.js:122-131` |
| D-19 | 板高度前缀白名单**排除科创板 688/689 与北交所 8xx/4xx**，这些票的最高板被完全忽略 | `fetch_sentiment_cycle_snapshots.py:22-27`；`boardHeight.ts:10-11` |
| D-20 | MCP 板块持续性取 top **20** 且无名称 tie-break，与 Python/TS 的 top24 + `(−pct, name)` 不一致 | `mcp-server.js:397, 418` |
| D-21 | `top_three_appearances` 缺失默认 **1**（把无数据当"出现过一天"） | `fetch_sector_snapshots.py:131-199` |
| D-22 | `strongest_repeat` 缺失时回落为当前 leader（编造"最强连庄"） | `services/sectorService.ts:274-275` |
| D-23 | **涨跌家数按 `ratio=5100/实际总数` 强制缩放**到硬编码 5100（差 ≤100 不缩放），平盘由 `5100-rise-fall` 反推；全失败时用 `Math.random()` 按 4 档比例伪造 | `services/sentimentCycle/sentiment.ts:91-112, 141-152` |
| D-24 | 真实新闻为空 → 回落 5 条逼真的 `MOCK_NEWS`（带情感标签，界面无法区分）；`SENTIMENT_CHART_DATA` 用 `Math.random()`；`RECENT_SENTIMENT` 分数全硬编码 | `services/newsService.ts:44-49`；`constants.ts:14-32` |
| D-25 | NewsFilter 的 `created_at` 用 `datetime.now()` **冒充发布时刻**；另有假 `lru_cache`（缓存永不生效） | `newsfilter_to_dingtalk.py:138-141, 194-197, 227` |
| D-26 | 研报 `published_at` 解析失败 → **回填当前时间** | `fetch_research_reports.py:259-266` |

**另有一批（多在 TS 前端与运行期兜底路径）。我们后端落库天然不会碰到它们，但必须知道它们存在** —— 否则拿 quantdash 的页面截图跟我们的数对账，永远对不上，还会误以为是我们算错了：

| 编号 | 缺陷 | 出处 |
|---|---|---|
| D-27 | **整份天梯可为随机模拟数据**：`Math.random()>0.6` 决定填充、伪造代码 `600xxx`、名称"模拟股份N"、`pctChange=10.0`、`turnover='1.2亿'`、`pe=20`；仅在 freshness meta 标 `source:'mock'`，数据结构与真实完全同形 | `services/stockService.ts:13-48, 193-210` |
| D-28 | **封板时间为哈希伪造**：`derivePseudoTime(symbol,dateLabel)` 用 `hash=hash*31+charCode` 生成时分秒，凡 `limitUpTime` 为空即填充 → 天梯卡片显示的"最后涨停时间"是假的，**且会污染龙头 tie-break** | `services/limitUpPoolService.ts:57-84` |
| D-29 | **K 线兜底为确定性伪随机走势**：`catch` 后返回 `generateMockKlines`（`seededRandom=sin(seed)*10000` 取小数，起始价 `10+seed%100`，波动 3%），**无任何 mock 标记**，直接影响 `isOneWord`/`nextClosePct`/修复率/A杀等全部情绪指标 | `services/quotes/kline.ts:7-40, 98-100` |
| D-30 | 空池日**回填伪造梯队**：复制上一快照第 N+1 板个股并 `pctChange-5`，生成"看似掉队"的假梯队，无任何标记 | `fetch_market_core_snapshots.py:322-329`；`stockService.ts:160-169` |
| D-31 | 交易日轴只用 `weekday()<5`，**法定节假日被当成交易日** → 空池 → 触发 D-30 | `fetch_market_core_snapshots.py:23-32`；`stockService.ts:50-65` |
| D-32 | 增量复用按 **MM-DD** 匹配，跨年同日会静默复用去年数据 | `fetch_market_core_snapshots.py:275-278` |
| D-33 | `ladder.json.date` 是**运行日**（非最新交易日），`dates[0]` 亦为"今天"（可能是节假日/开盘前）→ 首列常空并触发 D-30 | `fetch_market_core_snapshots.py:246, 334` |
| D-34 | 最新一日（无次日数据）时返回 `broken_count=limit_up_count, success_rate=0, avg_board_gain=0` → 面板恒定显示"炸板率 100%、溢价 0"，形似真实数据 | `fetch_market_core_snapshots.py:346-358`；`sentimentSnapshot.js:44-57` |
| D-35 | 涨停溢价面板取 `data[length-2]`（倒数第二日）当"当前"，与其余面板取 `data[length-1]` 口径不一致 | `useSentimentDataLoaders.ts:259-264` vs `:293-296` |
| D-36 | 全市场分页中途失败时**保留部分行**继续计算（`if rows: break`），涨跌家数偏小但无标记 | `fetch_emotion_indicators.py:269-278` |
| D-37 | 两市成交额 = 上证指数 + 深证成指 amount，**不等于全 A 成交额**（漏北交所与深市非成分股），但字段名/展示为"两市" | `fetch_sentiment_cycle_snapshots.py:523-547` |
| D-38 | `range_buckets` 标签与区间错位："0~-1%" 实际装 `(-5,-1]`，"-1~-5%" 实际装 `<=-5` | `fetch_emotion_indicators.py:391-396` |
| D-39 | 自然涨停/跌停：跌停池为空时用 `f3 <= -9.9` 近似（漏 20cm/30cm 档） | `fetch_emotion_indicators.py:404-409` |
| D-40 | mootdx 备源抓取异常被吞成空字典 → 所有 price/pct 落 `0.00`；`f9/f23/f20` 直接填 `"-"` 占位 | `scripts/server/modules/eastmoney_secondary.py:229-249, 259-262` |
| D-41 | 单板块历史失败 `continue`、板块服务 `catch→[]` → **轮动/持续性在残缺样本上计算且不标记** | `fetch_sector_snapshots.py:97-100`；`sectorService.ts:83-87`；`mcp-server.js:402-406` |
| D-42 | `cycle_overview.json` **无日期字段**、单对象整体覆盖 → 无法按日回看历史阶段 | `fetch_sentiment_cycle_snapshots.py:625-640` |
| D-43 | **两市成交额环比拿的是 T-2，不是前一交易日**。⚠️ 这条**不是 quantdash 原版的缺陷，是 YuanShan 侧对账时发现的**：PG `qd.sentiment_cycle.amount_change_pct` 最近 25 场里 **22 场可复现地等于"跳过紧邻前一日"算出的环比**（09-18 基准给 12.94%，对应基数是 09-16 的 18391 亿，而不是 09-17 的 18231 亿；逐日皆然，无一例外）。同一行的 `amount_yi` 本身没问题（与 `index_daily` 逐场吻合到 0.5 亿）。后果：`volume_state` → `confidence` → 阶段链里"修复/主升要求 ≠ 缩量再缩量"这一条全被污染。修法：取前一日一律走 `reference_data.trade_calendar` 的上一场次，不要用数组下标 `-1` 猜 | YuanShan 侧对账（`duanxian/qd_calc.py` vs `qd.sentiment_cycle`，2026-09-24） |

## 8. 验收清单

- [ ] `qd` schema 建好，9 张表（或裁定后的子集）都有 `comment on table` 标注 quantdash 出处与许可证
- [ ] `ft_reader` 对 `qd` 有 select 权限；YuanShan 侧能 `select` 成功
- [ ] 最近 30 个交易日回填完成，**无一行 `deviation` 是空的却用了兜底值**
- [ ] 抽 2026-09-22 一天做三方对数：`qd.sentiment_cycle.limit_up_count` vs `pg_sources.zt_pool()` vs 东财池。**三个数不一致是预期的**（判据不同），但每个数都要能说出自己是什么口径
- [ ] `stage_ui` / `stage_mcp` / `stage_feishu` 三列都算了；至少找出一个三列不一致的历史交易日，作为"quantdash 自己前后矛盾"的展示样本
- [ ] `qd.board_ladder` 里 `board_tier=1`（首板）有数据，且没有任何首板股出现在 `board_tier=7`
- [ ] `first_seal_time` 除 2026-09-22 外全为 NULL，且 `seal_time_available=false`
- [ ] `leader_tie_break` 三种取值都能出现，前端能看到"龙头是怎么选的"
- [ ] 全库搜不到常量 `5100`；搜不到 `Math.random` / `random()` 参与任何 qd 指标计算
- [ ] 三个 job_key 注册进 `/jobs`，能手动触发，失败时**不写空行**（宁可缺行，不可假行）

## 9. 实现回执与勘误（`code：数据加工` 2026-09-24）

§5 的建表与计算已全部落地并回填完（`2021-12-01 .. 2026-09-23`，1169 个交易日）。
落点：`app/db/sql/12_qd_schema.sql`、`13_qd_jobs.sql`、
`app/compute/qd_common.py` / `qd_ladder.py` / `qd_sentiment.py` / `qd_sector.py`、
`ingest/jobs/qd0_st_period.py` / `qd1_sentiment.py` / `qd2_ladder.py` / `qd3_sector.py`（跑法 `python -m ingest.jobs.qd1_sentiment`）。

**这一节只写两件"照文档做会得到错结果"的事，其余按 §4 字面实现。**
`code：页面展示` 与后续任何人读 §4.1 / §4.2 时请连带读本节。

### 勘误 1 · §4.1 的 `risk_level` 接错了炸板率（已按 quantdash 真实代码实现）

§4.1 同时写了这两句，但它们是两个不同的数：

- `risk_level` 判据：`high ⇐ a_kill>=2 OR broken_rate_sealed_pct >= 35`
- `broken_rate_sealed_pct` 定义：`(1 - success_rate) × 100`

quantdash 真实代码里，喂 `risk_level` 的那条炸板率是**炸板池/涨停池**：

```
scripts/fetch_sentiment_cycle_snapshots.py:496
    broken_rate = round((len(broken_pool) / len(today_pool)) * 100, 1) if today_pool else 0
    risk_level = "high" if a_kill_count >= 2 or broken_rate >= 35 else ...
services/sentimentCycle/highRisk.ts:32      同一口径
```

而 `(1 - successRate) × 100` 属于**另一个面板**的历史炸板率曲线：

```
services/sentimentCycle/performance.ts:88   successRate = followThrough / limitUpCount
services/sentimentCycle/performance.ts:124  brokenRate  = (1 - successRate) * 100
```

实证：它自己 `data/high_risk.json` 里 `brokenRate` 是 16.7 / 20.5 / 46.2，
`riskLevel` 依次 low / medium / high —— 与 20/35 阈值严丝合缝；
而 `(1-晋级率)×100` 在 A 股常年 50~80，按 §4.1 字面接会得到**每天都是 high、
`stage_*` 恒为退潮**（实测第一版照字面接，32 个交易日全是 `退潮/退潮/退潮`，
§8 要求的"三套 stage 出现分歧"永远做不出来）。

现实现：`broken_rate_sealed_pct` 列**留在原位不动**（它本来就是 performance 面板那条曲线），
只把 `risk_level` 的输入换成 `broken_rate_touched_pct`。每行 `deviation` 记 `L-19` 说明。
改完 1169 天的分布：`risk` high 890 / medium 272 / low 7；
`stage_ui` 退潮 936 / 修复 124 / 试错 89 / 主升 19 / 分歧 1；三套 stage 有 268 天不一致。

⚠️ §4.1 的文字本身没改（那是本次派活的输入，改动权在总协调）。**引用 §4.1 的
`risk_level` 判据时请连带读本节。**

### 勘误 2 · §4.2 的 `leader_tie_break` 四值枚举，实测只能出两值

§5 与 §8 期望"三种取值都能出现"。1169 天实跑结果只有两种：

| 取值 | 天数 | 说明 |
|---|---|---|
| `amount_no_seal_time` | 1168 | 无分钟线 → 按成交额降序 |
| `seal_time` | 1 | 只有 2026-09-22 有分钟线 |
| `code_asc` | **0** | 前置条件在真实数据里不存在 |
| `no_candidate` | **0** | 同上 |

- `code_asc` 要求**最高板多只票的 `amount_yuan` 精确相等**。实测：279 天存在
  两只以上同高度龙头，但**完全同额的天数为 0**（成交额精确到分）。
- `no_candidate` 要求当日零涨停。实测 `limit_up_count` 最小 11（全区间无零涨停日）。

枚举四个值仍按契约保留在 CHECK 里（万一以后出现），但**前端不要指望能渲染出四态**：
按两态设计。要真做出"龙头是怎么选的"这层信息，有效差异在
`seal_time` vs `amount_no_seal_time`，而它等价于"这天有没有分钟线"
—— 也就是 §3 那条封板时间不可得的另一种表达。

### 顺带记两条与 quantdash 无关、但影响对账的本地数据事实

1. `sector_daily` 里 `|pct_change|>100` 的 1994 行中 **1963 行落在 49 个"无名板块"**
   （`sector_info.name` 就等于代码数字本身，如 `TDX_880046` 名叫 `880046`，
   是 c17 解析通达信板块指数留下的产物；最大一条 2024-02-08 为 +429900%）。
   §4.3 的前 24 排名必须先排掉它们，否则领涨板块天天是解析垃圾。
   现实现按天把排除数写进 `deviation`（`L-18`，含 `excluded_unnamed` /
   `excluded_over_cap` 两个计数），不静默丢。
2. `sector_daily` 的 TDX_ 与 THS_ 两套板块体系并存（概念 36 vs 232、行业 612 vs 86），
   混排会让同义板块互相挤位。§4.3 只写了"仅概念+行业"，没写体系选择 ——
   现实现只取 `TDX_`，记 `L-15`。quantdash 用东财单一体系故无此问题。

### 勘误 3 · 板块排名的"宇宙"在 2026-09-23 断了（跨日字段已置 NULL）

`qd.sector_rotation` 是**按当日 universe 排名**的，所以跨日名次只有在宇宙一致时才可比。
实测全历史 1169 个交易日里有一个断点：

| 日期 | 参与排名的概念数 | 参与排名的行业数 | 合计 |
|---|---|---|---|
| 2026-09-22 及以前 | 268 | 约 180 | 448 |
| 2026-09-23 | 268 | **561** | **829** |

上游 `market_data.sector_daily` 的通达信行业**原始行**从 228 跳到 609（c3/c17 那天起补了
381 个三级行业）。后果：09-23 领涨前 8 全是行业、概念被整体挤出（当日最强概念
`复合铜箔` +2.485% 只排第 9），而 `streak_days` / `top_three_appearances` 是拿
"180 只行业 + 268 只概念"的名次去和"561 + 268"的名次比 → **连庄天数会静默算错**。

处理：`compute_persistence()` 加了守卫 —— 窗口内分类型样本数相对**窗口众数**偏离超两成
即判为断点，把 `streak_days` / `top_three_appearances` / `strength_delta_pct` /
`strongest_repeat_*` **一律置 NULL**（只保留当日自洽的领涨板块与涨幅），deviation 记 `L-20`。
按"总数是否完全相等"判过一版，会把 398/1169 天误判成断点（宇宙本来就有
概念 266~268、行业 179~182 的自然浮动），故改成相对幅度判。现全库 `L-20` 只命中 1 天 = 09-23。

`qd.sector_rotation` 因此**多了两列**（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，
已同步进 12 文件建表段）：`concept_candidates` / `industry_candidates`。
前端做名次变化对比时应先看这两列一致不一致。

### 勘误 4 · 领涨板块按 `sector_code` 认领，不按名字

新补的三级行业里有**同名不同码**的板块（实测 09-23 `房产服务` 同时占第 4 与第 12 名），
按 `current_leader_name` 回溯连庄会把两个不同板块当成一个。改为按 `sector_code` 判同一性，
名字只用于显示；轮动排序的 tie-break 也补到第三级 `(pct DESC, name ASC, code ASC)`。deviation 记 `L-21`。

### 两条已按 §8.1 / 你改的契约落地的（无需再核）

1. **`reasons` 形状**：`[{stage, hit_rule, reason}]`，`stage ∈ {stage_ui, stage_mcp, stage_feishu}`
   **各一条**，`hit_rule` 是该套判据链里命中的优先级序号。全库 1169/1169 行合形状。
2. **`stage_ui` 分布偏斜已写进表注释 + 每行固定 deviation `D-43`**。库里实测（不是估的）：
   ⚠️ 下面这串是**修正成交额环比基期之前**（2026-09-25 08:44 前）测的，现值见勘误 5；
   结论本身不变（退潮仍占绝对多数、冰点仍 0 次）。
   `stage_ui` 退潮 936 / 修复 125 / 试错 88 / 主升 19 / 分歧 1 / **冰点 0**；
   `stage_mcp` 退潮 936 / 修复 125 / 试错 84 / 主升 23 / 分歧 1；
   `risk` high 890 / medium 272 / low 7；三套不一致 **268** 天（其中 `ui≠mcp` 4 天）。
   ⚠️ 你上一条消息里写的是 退潮 971 / 修复 101 / 主升 43 / 分歧 29 / 试错 25、
   飞书 952 / 217、不一致 217 天 —— 与库里实测**都对不上**，请说明那几个数是从哪张表
   或哪次查询取的；接口里要显示分布数字的话，以本节这份重跑后的值为准。

### 勘误 5 · `amount_change_pct` 的基期取成 T-2（2026-09-25 已修，走裁定 (a)）

**症状**：`qd.sentiment_cycle.amount_change_pct` 全库 1148 个非空值**没有一个是 T 对 T-1**，
全部是 T 对 T-2。最严重的一天符号都是反的：2026-09-17 真值 `−0.87%`，库里 `+13.05%`。

**根因**：FT `app/compute/qd_sentiment.py:356-358` 把基期取成 `prev2`：

```python
chg   = chg_of(d, pd2)          # 应为 chg_of(d, pd_)
chg_p = chg_of(pd2, prev3)      # 应为 chg_of(pd_, pd2)
```

`sentiment_value_p` 用 `pd2` 是对的（它要的就是"前一天的情绪分"），所以这个错**只**发生在
量能这一族，也正因为只错一半、它没有在自洽校验里暴露。

**波及面（修正后逐行实测，不是估算）**：`amount_change_pct` 1169 行全变（含 21 行原本
NULL 被填上）、`volume_state` 656、`stage_ui`/`stage_mcp`/`reasons` 各 45、`confidence` 528。
修正前后分布：`volume_state` 存量震荡 512→497 / 缩量再缩量 270→290 / 持续放量 209→224 /
放量滞涨 178→158；`stage_ui` 只有 修复 125→126、试错 88→91、主升 19→15 三档动，
**退潮 936 与 冰点 0 不变** —— 也就是 `D-43` 那条"优先级链必然偏斜"的结论不受影响。

**修法（用户 2026-09-25 选定 (a)）**：不改 FT 一行（FT 冻结为只读参考实现），
在 YuanShan 侧建生产者把修正值写回基线表：

- `compute/qd_sentiment.py` —— 从 FT 移植的计算层，**唯一**算法差异就是上面那两行基期。
- `compute/db.py` —— 读写两条连接分开；写走 `PG_ETL_USER`（`ft_etl` 对 9 张 `qd` 表
  本来就有 INSERT/UPDATE/DELETE，**UPDATE 不需要 CREATE**，所以这一步不等 `ys` 授权）。
- `scripts/qd_fix_amount_change.py` —— 快照 → 重算 → **移植等价性闸** → 差异报告 → 写回。

**等价性闸是这次能安全写回的前提**：除勘误波及的 7 列，其余 **32 列**必须逐行与库里现值
完全一致，只要有一列对不上就整批拒绝写入（否则一次勘误会顺带改掉二十几个无关列，
比原 bug 更糟）。实测通过；重跑一遍 7 列差异全为 0，即幂等。

**独立复核**（不信任自己的生产者）：用一条纯 SQL 的 `lag()` 从 `market_data.index_daily`
重算 T/T-1 环比，与库里现值并排比 —— **1169 天 0 处不一致、0 个残留 NULL**。

**可逆性**：`ft_etl` 无 CREATE 权限、建不了备份表，所以回滚走
`.local/qd_sentiment_fix/snapshot_<ts>.jsonl` + `--restore`（参数化 UPDATE，不拼 jsonb
字面量）。**已实测**：取快照尾 2 行还原，`2026-09-22` 连那个 `NULL` 都原样回来了，
再全量写回收敛。

**两件顺带修掉 / 留下的**：

1. 我自己的替换逻辑第一次把 `deviation` 里 `D-43` 那条的 `code` 键丢了（只剩 `note`），
   会导致按码检索失效 —— 已修并重写，现全库 1169 行的每个 deviation 元素都含 `code`+`note`。
2. **表注释改不动**：`qd.sentiment_cycle` 的 owner 是 `ft_admin`，`ft_etl` 无权 `COMMENT ON`。
   注释里那串修正前的分布数字因此仍是旧的；勘误正文已逐行落在每行的 `D-43` 与 `Q-fix-1`
   里，读接口拿到的就是新值。要把注释也刷新，得用 `ft_admin` 跑一次 `COMMENT ON TABLE`。
3. **编号纪律**：`D-*` 的语义是"quantdash 的缺陷未被复现"，本实现的自纠不该占 D 号，
   所以这条用 `Q-fix-1`。原先 `D-43` 已被"阶段分布偏斜"占用，两者不是一回事。

### 另外两件的回执

- **`config.setting` 三个 `qd_*` 键已落库**（`app/db/sql/14_qd_settings.sql`，
  `ON CONFLICT DO NOTHING`，category=`compute`）。三个作业改由真 `/jobs` 链路各触发一次，
  **全部 succeeded**。顺带修掉一个只有真读库才会暴露的 bug：`setting()` 里对 jsonb 值
  又 `json.loads` 了一遍，`"qd-1"` 解成字符串 `qd-1` 后再解必抛 `JSONDecodeError`
  —— 测试侧注入参数走不到那条路径，所以之前全绿的跑法没发现它。
- **`alter default privileges` 覆盖面已实测**：`pg_default_acl` 里 qd 的两条记在
  **ft_admin 名下**，而本仓库所有 DDL 都是 `init_db.py` 用 ft_admin 跑的 → 下次在 qd 建表
  自动带 `ft_reader` select / `ft_etl` 写权限（实测建一张探针表，reader 立刻能读、
  etl 立刻能写，已删）。但 **ft_etl 连 `CREATE` on schema qd 都没有**（permission denied
  for schema qd），所以它建不了表；真要允许，得超级用户补
  `ALTER DEFAULT PRIVILEGES FOR ROLE ft_etl`（ft_admin 非 superuser 也不是任何角色成员，自己加不了）。
  结论一句话：**qd 的建表固定走 ft_admin**，这条已写进 12 文件末尾。
