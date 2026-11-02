-- -*- coding: utf-8 -*-
-- 57_xy_rank_daily.sql
-- 「X 名次 + Y 名次的合计名次」落一张表：`analytics.xy_rank_daily`
--
-- ── 为什么要落这一张（2026-10-28 用户点名）──
-- 用户看首页「首板分析」时问：近 5 场 xy 合计得分的名次，能不能像象限那样直接摆一串第几名
-- （例：`21-20-19-18-5`）。当场查证：
--   · `analytics.score` 里**每个 capability 每场每票都有一个 `rank_in_market`**（实测 2026-10-28：
--     八个键各 5,570 行、`rank_in_market` 非空 5,570/5,570，含 `stock_quality`(X) 与 `env_resonance`(Y)）；
--   · 但**没有任何一列存"X 名次 + Y 名次"这个和** —— 全库 `information_schema` 里带 xy 的只有
--     `analytics.score_trend.x_plus_y_max` / `.x_plus_y_max_date`，那是 X+Y 的**分值峰值与其日期**，
--     既不是逐场序列也不是名次。
-- 所以这一档此前只在「名次稳步前进榜」的 SQL 里现加（`duanxian/quant.py`：
-- `case when 'xy' then rx + ry`）。用户裁定：**落成库里的列，作为数据加工的一环每天跑**，
-- 别每个屏各加各的。
--
-- ── 这一列是什么尺（三条容易读错的，全写进列注释）──
-- 1. **它是两个名次相加，不是"对合计再排一次名"**：`rank_sum = rank_x + rank_y`，
--    两个加数各自都是「该分量在全场的排位」（1 = 第一名）。所以它**越小越靠前**，
--    量级上限是 2 × 当日只数（实测 2026-10-28：5,570 只 ⇒ 上限约 11,140）。
--    方向那句全站只有一份：`duanxian/ranking.py::DIRECTION_NOTE`，界面读它，别另写。
-- 2. **与 `oper_score` 的名次不是同一件事**：`oper_score` 是合成分（0.40×X + 0.60×Y）的全场排位，
--    这一列是两轴名次的和。同一只票这两个数常常差一截（实测 605488.SH 2026-10-28：
--    `rank_sum` 100、`oper_score` 名次 25）⇒ 谁也不许替谁、更不许相减。
-- 3. **旧算法迁入批（`is_stale=true`）进不了这张表**（这一条是 2026-10-28 回查库改的口，
--    原来写成"照样落，但带标记"，与数据不符）：`analytics.score` 里 `is_stale=true` 那 59,756 行
--    （2026-07-15~07-27）的 `rank_in_market` **全是 NULL** ⇒ 少一轴就没有"和"可算，那些 (票, 场) 整行不落。
--    所以 `stale` 这一列当前全库 0 行 true —— 它是哨兵，不是装饰：哪天旧批也重算出名次，
--    读侧那一段格子必须标〈旧基准〉、名次不参与"前进/后退"的比较（与「近 5 场四维象限」那一格同一条规矩）。
--
-- ── 覆盖与深度（别拿它当全史）──
-- 两键都有名次的场次实测 **275 场**（`analytics.score` 里 rank 非空的 distinct trade_date），
-- 起点 2025-08-28。早于那天的历史本机**没有**这两个名次（评分批次就到那儿），
-- 所以这张表最早那一行就是 2025-08-28，**不插值、不回填假名次**。
-- 只有两轴**同时有名次**的票才落行：少一轴就没有"和"可算，那一格在界面上是 `—`（读不到），
-- 不是 0、也不是"没上榜"。

create table if not exists analytics.xy_rank_daily (
    security_code   text           not null,   -- 带市场后缀（与 analytics.score 同一套代码）
    trade_date      date           not null,
    spec_version    text           not null,   -- 'xyr-1'
    rank_x          integer        not null,   -- stock_quality 的当日全场名次
    rank_y          integer        not null,   -- env_resonance 的当日全场名次
    rank_sum        integer        not null,   -- rank_x + rank_y：越小越靠前
    universe        integer        not null,   -- 当日两轴都有名次的只数（这一列的分母）
    stale           boolean        not null default false,  -- 任一加数来自 is_stale 批就为 true
    calculated_at   timestamptz    not null default now(),
    primary key (security_code, trade_date, spec_version)
);

create index if not exists xy_rank_daily_date_sum_idx
    on analytics.xy_rank_daily (trade_date, rank_sum);

comment on table analytics.xy_rank_daily is
  'X 名次 + Y 名次的合计名次，逐场逐票一行（用户 2026-10-28 点名落库并每天跑，加工段步骤 `xyrank`，'
  '紧跟在 `scores` 之后 —— 它只读那一步刚写好的 `analytics.score`）。'
  '这一列是**两个已入库名次相加**，不是对合计再排一次名；与 `analytics.score` 里 `oper_score` 的名次不是同一把尺。'
  '异动、分位、"稳步前进"这类阈值型判据都不落在这里，仍由读侧按窗口现算。';
comment on column analytics.xy_rank_daily.rank_x is
  'X 轴（`capability_key = stock_quality`）当日全场名次，1 = 第一名。数字取自 `analytics.score.rank_in_market`，'
  '这里不重算 —— 重算一遍就是第二份真相源。';
comment on column analytics.xy_rank_daily.rank_y is
  'Y 轴（`capability_key = env_resonance`）当日全场名次。少这一轴的那只票整行不落（没有和可算），'
  '界面上那一格是 —（读不到），不是 0、不是"没上榜"。';
comment on column analytics.xy_rank_daily.rank_sum is
  'rank_x + rank_y ⇒ **越小越靠前**（两个名次之和，不是分值）。上限约 2 × 当日只数（实测 2026-10-28：'
  '5,570 只 ⇒ ~11,140）。与 `oper_score` 的合成分名次（0.40X+0.60Y 再排一次）不是一件事：'
  '实测同一只票同一天 100 对 25，谁也不许替谁。';
comment on column analytics.xy_rank_daily.universe is
  '当日**两轴都有名次**的只数 = 这一列的分母。全场是 5,570 只，但有一轴缺行时这个数会小于 5,570，'
  '屏上"第几名"旁边要能说出分母是多少（母体有上限时不许说"全场"）。';
comment on column analytics.xy_rank_daily.stale is
  '任一加数来自 `analytics.score.is_stale = true` 那一批就为 true。实测 2026-10-28 回查：那一批 59,756 行的 '
  '`rank_in_market` 全是 NULL ⇒ 它们压根进不了这张表（少一轴就没有和可算），所以这一列当前全库 0 行 true。'
  '它是哨兵不是装饰：哪天旧批也重算出名次，读侧那一段必须标〈旧基准〉、不参与"名次前进/后退"的比较'
  '（象限可以用，名次混比会得出假趋势）。';

grant select                     on analytics.xy_rank_daily to ft_reader;
grant select, insert, update, delete on analytics.xy_rank_daily to ft_app, ft_etl;
