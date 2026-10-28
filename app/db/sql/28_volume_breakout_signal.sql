-- -*- coding: utf-8 -*-
-- 28_volume_breakout_signal.sql
-- 「缩量收敛后首次突破」形态信号表。
-- 用户 2026-10-11 的原话拆成三段式：
--   ① 2 个月前有过一波放量（启动痕迹，vol ≥ 前5日均量 ×1.8）
--   ② 之后持续缩量到均线附近（筹码沉淀，连续 10 天以上 vol < 前5日均量 且 |close/ma60-1| < 3%）
--   ③ 第一个同时满足"突破均线 + 放量 ≥1.6×"的那天 = 潜在买点
--
-- 为什么要单列一张表（而不是在 volume_double 里加几列）：
--   ① 这是**时序形态**，不是单日统计：要扫 40 根 K 线、找阶段转换点，逻辑独立于"倍量次数"；
--   ② 信号可回溯测试：哪天出现、哪天后失效、后续涨幅如何 —— 这些都要落库才能跑回测；
--   ③ 每日链里单独一步（d8），失败不影响 d7（倍量震荡）的产出。
--
-- 主键带 spec_version（全项目约定：改判据是新写一批行，历史那批留着对账）。

create table if not exists analytics.volume_breakout_signal (
  security_code     text          not null,
  trade_date        date          not null,       -- 信号触发日（首破那天）
  spec_version      text          not null,       -- 判据版本（当前值见 app/compute/breakout_signal.py 的 SPEC_VERSION）
  
  -- 三类信号（互斥，一行只有一种）
  signal_type       text          not null,       -- 'first_break' / 'surge_after_shrink' / 'converging'
  
  -- 阶段信息
  last_surge_date   date,                         -- 上一波放量的日期（窗口内最后一次 vol_ratio >= 1.8）
  surge_vol_ratio   numeric(8,2),                 -- 那波放量的最大倍数
  shrink_days       smallint,                     -- 从 last_surge_date 到 signal_date 之间的缩量天数（vol < 前5日均量）
  converge_days     smallint,                     -- 收敛段长度（连续 |dev| < 3% 的天数，含首破日）
  
  -- 突破日的读数
  break_vol_ratio   numeric(8,2),                 -- 突破那天的放量倍数（vol / 前5日均量）
  break_dev         numeric(8,4),                 -- 突破那天的离线程度 |close/ma60 - 1|
  break_close       numeric(10,2),                -- 突破日收盘价
  break_ma60        numeric(10,2),                -- 突破日 MA60
  
  -- 辅助字段
  window_days       smallint      not null default 40,  -- 观察窗长度
  bars_used         smallint,                     -- 窗口内实际有行情的天数（停牌多的票会小于 40）
  
  calculated_at     timestamptz   not null default now(),
  primary key (security_code, trade_date, spec_version)
);

comment on table  analytics.volume_breakout_signal            is '缩量收敛后首次突破形态信号（三段式：放量波 → 缩量收敛 → 首破）。判据与写入：app/compute/breakout_signal.py；每日加工段步骤名 `breakout`。原料来自 market_data.stock_daily';
comment on column analytics.volume_breakout_signal.signal_type is '三类信号：first_break=收敛后首破（买点候选）；surge_after_shrink=放量波后正在缩量（观察期）；converging=正在收敛中（还没突破）';
comment on column analytics.volume_breakout_signal.last_surge_date is '窗口内最后一次"放量波"的日期（vol ≥ 前5日均量 ×1.8）。没有就 NULL，表示这段没出现过明显放量';
comment on column analytics.volume_breakout_signal.surge_vol_ratio is '那波放量的最大倍数（窗口内所有 vol_ratio >= 1.8 的日子取 max）。读它才知道"上次放量有多凶"';
comment on column analytics.volume_breakout_signal.shrink_days is '从 last_surge_date 到 trade_date 之间，成交量 < 前5日均量的天数。这一列量化"缩量收敛"的长度';
comment on column analytics.volume_breakout_signal.converge_days is '收敛段长度：连续 |close/ma60 - 1| < 3% 的天数（含首破日）。这一列衡量"贴着均线盘整了多久"';
comment on column analytics.volume_breakout_signal.break_vol_ratio is '突破那天的放量倍数（vol / 前5日均量）。阈值是 1.6，但实际值可能更高（2.0/3.0 都有）';
comment on column analytics.volume_breakout_signal.break_dev is '突破那天的离线程度 |close/ma60 - 1|。理论上应该很小（刚突破时贴着线），但如果跳空高开就会大';
comment on column analytics.volume_breakout_signal.spec_version is '判据版本在主键里：改窗口长度/阈值都是新写一批行，旧行留着对账。当前版本 breakout-sv-1';
comment on column analytics.volume_breakout_signal.security_code is '证券代码（带 .SZ/.SH/.BJ 后缀）。一行＝"这只票在这一天触发了某个信号"';
comment on column analytics.volume_breakout_signal.trade_date is '信号触发日：first_break=首破那天；surge_after_shrink=放量波最后一天；converging=收敛段最后一天。它是排序锚点';
comment on column analytics.volume_breakout_signal.bars_used is '窗口内实际参与统计的天数。< window_days 说明这只票往前数不够 40 根（次新/长期停牌），页面必须标出来';
comment on column analytics.volume_breakout_signal.calculated_at is '这一行最后一次被算的时刻。判据重跑是按主键 upsert 覆盖，所以它是"这一版读数新鲜不新鲜"的唯一线索';

create index if not exists volume_breakout_signal_type_idx
  on analytics.volume_breakout_signal (signal_type, spec_version, trade_date desc);
create index if not exists volume_breakout_signal_code_idx
  on analytics.volume_breakout_signal (security_code, trade_date desc);

grant select, insert, update, delete on analytics.volume_breakout_signal to ft_etl, ft_app;
grant select                                          on analytics.volume_breakout_signal to ft_reader;
