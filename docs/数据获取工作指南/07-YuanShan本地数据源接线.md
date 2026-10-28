# 07 · YuanShan 本地数据源接线（四条已落地）

> 对象：`D:\finance\YuanShan`（= yuanshan v1.1.0 + 自写本地 PG 层）。
> 全部读本项目 PostgreSQL 库 `financetool`（原旧项目 FinanceTool 的库，现已并入 YuanShan），账号 `ft_reader` **只读**，
> 任何一路取不到就回退在线源或如实报不可用，**绝不写库**。
> 口径与判据的规矩见 [数据加工工作指南/08](../数据加工工作指南/08-跨源口径契约.md)；呈现规矩见 [页面展示工作指南/01](../页面展示工作指南/01-数据口径展示规范.md)。

## 接线总览

| 数据 | 本地表 | 代码 | 界面/API | 原本从哪来 |
|---|---|---|---|---|
| 全市场宽度 + 涨停/触板/炸板/跌停 | `market_data.stock_daily`（自算） | `pg_sources.breadth()` → `breadth._from_pg()` | 复用既有宽度面板字段 + 4 个新增键 | `push2delay` clist 二分探测 |
| 龙虎榜 + 机构净买 | `dragon_tiger_detail` + `dragon_tiger_institution` | `pg_sources.lhb()` | 既有 `inst_net` 键（原恒为 `None`） | akshare `stock_lhb_detail_em`（无机构列） |
| 板块周期五档 | `analytics.ignition` | `pg_sources.sector_cycle()` | `GET /api/market/sector-cycle` | **原本没有** |
| 个股技术指标 35 项（10-14 加了 `ma250`；`config.indicator` 现查 35 条） | `analytics.indicator_value` + `config.indicator` | `pg_sources.indicators()` | `GET /api/stock/indicators` | **原本没有** |

## 三条必须知道的坑

### ① 涨停池有三条独立入口，别以为接了一条就全程本地

| 入口 | 走什么 | 本地化了吗 |
|---|---|---|
| `fetchers.fetch_zt_pool` | akshare `stock_zt_pool_em` → 现走 PG | ✅ 是（喂 prompt 文本、晋级率、梯队） |
| `market_facts.pools()` | `duanxian/pool_source.py:9` **直连 akshare** | ❌ 否（喂封板质量、亏钱效应、题材结构、归档） |
| `vr/astock.py:516` 盯盘 | **东财 `push2ex` 直连** | ❌ 否 |

→ 同一份复盘里"涨停家数/晋级率"与"封板质量面板"目前**仍是两套源**。判断某个涨停数出自哪里，先看它出自哪个模块。

### ② 宽度这条只能在原判断点头之后接手

`breadth.market_breadth()` 有两条被测试锁住的不变量：**盘中必须拒绝，且一次网络都不许发**；**只回答最近一个已收盘场次**。
本地日线**不许抢答**这两类场次（第一版就是这么写的，直接弄红 3 条测试）。所以 `_from_pg()` 的位置是：读缓存 → `live_quotes_are_close_of()` 点头 → `is_settled()` 为真 → 才问本地。

副作用要知道：**更早的历史日期不会因为接了 PG 就突然有数** —— 那需要另外放开一条产品规则，是决定不是顺手改。

### ③ 不读 `analytics.market_breadth` 那张现成的表

它当时的涨停/跌停判据是单边 `close >= limit_up - 0.01`，会把"收盘高于涨停价"的行也算成涨停：2026-09-22 该表给涨停 **70**、跌停 **11**，双边严格判据给 **65**、**4**，东财池 63、3。**10-07 已收口**：容差改成单一归属的 `LIMIT_EPS = 0.005`（`duanxian/breadth.py:38` import 它，且比较一律 `::numeric`），当日档位改读 `limit_price_daily`，单边/双边在 0.005 下等价 —— 上面那组差值是**修之前**的实测，别当现状引。
虚高那 5 只全是 ST 标记票（000056/002082/002581/002650/600337），当日实际涨 5.88%~9.99%，根因是 `reference_data.security.is_st` 停在 2026-08-27 的旧快照。
→ 该表的 `touched_count`/`promotion_rate`/`median_pct`/`up_ratio` 可用，**`limit_up_count`/`limit_down_count` 必须自己重算**。

## 已知边界（不是 bug，是要如实告诉读者的）

- ~~`fund_flow_rank` 是空表（0 行）~~ **10-18 现查：89,020 行（2026-09-28~10-16）** ⇒ `individual_fund_flow()` 有数了（新浪主源 `c21`／加工链第 23 步 `fundflow_rank`），板块那路 `sector_fund_flow_daily` 630 行到 10-16（`c22`／第 24 步 `sectorflow`）。**没变的那半**：akshare 那两路（`ak_concept_em`/`ak_fundflow_em`）仍是 down，而新浪/同花顺**只给当天** ⇒ 采集停一天就永久缺一天，这条是数据现实、不是待修的 bug。
- **230 个行业板块没有涨跌家数** → 板块周期的广度分项算不出，`term_count ≤ 4`，**规则上永远不会被判为点火**。榜上看不到它们不是没轮动。
- **板块周期的板块口径是通达信 TDX_880（607 个）+ THS_881（85 个）**，与 YuanShan 判"所属行业"用的 TDX_881 是**两套编号**；且里面混着 `昨日涨停/昨日断板/昨日突涨/板块趋势/轮动趋势/最近情绪指数` 这类行为统计板块 —— 已打 `synthetic` 标并交出 `sector_type`，由界面决定默认展示哪一类。
- 性能：本地 `zt_pool` 单次 3.2s（90 日回看 + Python 逐票算连板），`breadth` 0.46s、`sector_cycle` 0.6s、`indicators` 0.2s、`lhb` 0.44s。

## 怎么验

```bash
cd /d/finance/YuanShan
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_pg_sources.py -q   # 24 passed
# 服务在跑的话：
curl "http://127.0.0.1:8910/api/market/sector-cycle" | head -c 300
curl "http://127.0.0.1:8910/api/stock/indicators?code=600519" | head -c 300
curl "http://127.0.0.1:8910/api/stock/indicators?code=999999"              # 应 available=false + reason
```

全量回归基线：`pytest tests -q` → **15 failed / 1021 passed**；`VIBE_PG=0` 复跑同样 15 条（Claude/CodeBuddy CLI 未装、GBK `read_text`、POSIX `killpg`），**不是本次改动引入的**。

相关：[[yuanshan-local-patch]]、[06-跨项目数据源对照](06-跨项目数据源对照.md)
