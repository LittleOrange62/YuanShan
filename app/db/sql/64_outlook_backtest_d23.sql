-- -*- coding: utf-8 -*-
-- 64_outlook_backtest_d23.sql
-- 「⑩ 明日展望」回测表加 2/3 个交易日列（用户 2026-11-05 要求：
-- 「算一下这些个股在计算得出的当天往后 1、2、3 个交易日的涨跌情况，还有冲高率、整体高点数值涨幅」）。
--
-- 与 63 同一把尺：一律**后复权价**（价 × coalesce(adjustment_factor,1)），基准是信号日收价；
-- 原始价另留的列只为对表，判据与聚合只读后复权。缺数照旧写 NULL 并在 `missing` 点名，不写 0。
--
-- 口径（一行的含义不变："那天那榜点了这只票"，只是多算它往后两天）：
--   `cl2_pct`/`cl3_pct`   后复权收价 ÷ 信号日后复权收 − 1（第 2/第 3 个交易日收盘对信号日收盘）
--   `hi_max_3d_pct`       往后 3 个交易日的最高价（后复权）取最大 ÷ 基准 − 1（"整体高点数值涨幅"）
--   `up3d`                hi_max_3d_pct > 0（3 日内冲过信号日收盘）。NULL＝判不了（3 日全无行）。
--                        汇总里的"冲高率"= up3d 为 true 的占比（分母按 up3d 有值的行数算）。
--   第 2/3 日没到的场次（最近那两场）cl2/cl3/hi_max_3d/up3d 是 NULL —— 不是"没涨"，是还没发生。
--
-- 基线表同步加 2/3 日：没有它们，名单的"3 日均涨 X%"就没有对照（大盘自己那段均值多少，差值才是信息量）。
--
-- 列可空且默认无值：63 已经落的历史行（如果以后回填前改了口径）读新列就是 NULL，界面按"判不了"念。
-- 授权沿用 63 给表的授权（PG 新列自动继承表 ACL，这里不重复 grant）。

alter table analytics.outlook_pick_daily add column if not exists next2_date    date;
alter table analytics.outlook_pick_daily add column if not exists next3_date    date;
alter table analytics.outlook_pick_daily add column if not exists cl2_pct       numeric(10,4);
alter table analytics.outlook_pick_daily add column if not exists cl3_pct       numeric(10,4);
alter table analytics.outlook_pick_daily add column if not exists hi_max_3d_pct numeric(10,4);
alter table analytics.outlook_pick_daily add column if not exists up3d          boolean;

comment on column analytics.outlook_pick_daily.next2_date is '信号日后第 2 个交易日（还没到就是 NULL）';
comment on column analytics.outlook_pick_daily.next3_date is '信号日后第 3 个交易日（还没到就是 NULL）';
comment on column analytics.outlook_pick_daily.cl2_pct is '后复权：第 2 日收盘 / 基准 − 1（百分数）';
comment on column analytics.outlook_pick_daily.cl3_pct is '后复权：第 3 日收盘 / 基准 − 1（百分数）';
comment on column analytics.outlook_pick_daily.hi_max_3d_pct is '后复权：往后 3 日最高价取最大 / 基准 − 1（整体高点数值涨幅）';
comment on column analytics.outlook_pick_daily.up3d is '3 日内冲过信号日收盘（hi_max_3d_pct > 0）。NULL＝判不了';

alter table analytics.outlook_baseline_daily add column if not exists next2_date    date;
alter table analytics.outlook_baseline_daily add column if not exists next3_date    date;
alter table analytics.outlook_baseline_daily add column if not exists cl2_pct       numeric(10,4);
alter table analytics.outlook_baseline_daily add column if not exists cl3_pct       numeric(10,4);
alter table analytics.outlook_baseline_daily add column if not exists hi_max_3d_pct numeric(10,4);
alter table analytics.outlook_baseline_daily add column if not exists hi_win_3d_pct numeric(10,4);
alter table analytics.outlook_baseline_daily add column if not exists cl2_win_pct   numeric(10,4);
alter table analytics.outlook_baseline_daily add column if not exists cl3_win_pct   numeric(10,4);

comment on column analytics.outlook_baseline_daily.hi_max_3d_pct is '全市场等权：3 日最高点涨幅的均值（与名单表同尺）';
comment on column analytics.outlook_baseline_daily.hi_win_3d_pct is '全市场等权：3 日最高点涨幅 > 0 的占比（基线的"冲高率"）';
