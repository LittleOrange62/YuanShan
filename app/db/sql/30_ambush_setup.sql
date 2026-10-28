-- 埋伏票（左侧机会）榜：横盘蓄势 / 均线汇集 / 周线重心抬升 / 回踩不破 / 量价背离 / 龙回头
-- 用户 2026-10-13 点名：突破信号那屏改左右两栏，左栏放"埋伏票"，重点关注各均线汇集到一起的横盘股、
-- 且周线基本每周重心都上移的那种。判据强度默认走严档（三条全过，实测 2026-10-12 约 40 只）。
--
-- 一物一行：一行 = 一只票在一场的全部埋伏读数。一只票同场可以命中多个类型，
-- 所以 `ambush_types` 是数组，筛选走数组重叠（`&&`）而不是 join 一张明细表 ——
-- 明细表会让"这一场某类有几只"这种全场计数变得要说清按谁去重。
--
-- 判据与写入：app/compute/ambush_setup.py（步骤名 ambush，每日加工段）。
-- 读数口径全部按后复权价（close × adjustment_factor），与 15 日涨幅、四维象限同一套价格。

create table if not exists analytics.ambush_setup (
  trade_date        date        not null,
  security_code     text        not null,

  -- 均线汇集程度：MA5/10/20/30/60 里最高与最低之差，除以最低那根（%）。这是"成本收敛"的读数
  ma_spread_pct     numeric(8,3),
  ma5               numeric(16,6),
  ma10              numeric(16,6),
  ma20              numeric(16,6),
  ma30              numeric(16,6),
  ma60              numeric(16,6),
  close_hfq         numeric(16,6),        -- 当日后复权收盘（榜上的"价"就是它，可自证）

  -- 周线重心：按 ISO 周聚合的周均后复权价；streak 是"最近连续几周比上一周高"的周数（在 8 周窗口内数）
  weekly_streak     int,
  weekly_ups6       int,                  -- 近 6 个完整周里重心抬升的周数（含当前不完整周时另说）
  weekly_bars       int,                  -- 真有几个完整周（不足 8 周的次新股会小，判据据此出声）
  week_sessions_cur int,                  -- 当前这一周已有几个交易日（节假日周只有 2~4 天，必须交代）
  week_partial      boolean,              -- 当前周是不是不完整周（不完整时 streak 不把本周算进去）

  -- 横盘窄幅：20 日内 (最高-最低)/均线，以及它在当天的分位（低=横得紧）
  range20_pct       numeric(8,3),
  box_pos_pct       numeric(8,3),          -- 现价在 60 日区间里的位置（0=贴下沿，100=贴上沿）
  dev_ma20_pct      numeric(8,3),          -- 现价离 MA20 的百分比（带符号）
  dev_ma60_pct      numeric(8,3),          -- 现价离 MA60 的百分比（带符号）
  trend60_pct       numeric(8,3),          -- 60 日涨幅（后复权），回踩型用它确认"趋势还在"

  -- 量能：近 5 场（含当日）均量 / 第 6~60 场均量；<1 是缩量。极端地量另有阈值
  --（2026-10-14 对着 `app/compute/ambush_setup.py:272-273,303` 的 SQL 改的口：
  --  原先这里与列注释都写「近 10 日 / 第 21~60 场」，是抄错的同一句话）
  shrink_ratio      numeric(8,3),
  obv_ratio         numeric(8,4),          -- 近 20 日"涨日量 - 跌日量"占总量之比，量价背离用

  -- 龙回头：最近一根放量大阳/涨停的位置与它的关键位
  big_day_date      date,
  big_low_hfq       numeric(16,6),         -- 那根阳线的最低价（守住它 = 主力成本没被砸穿）
  big_close_hfq     numeric(16,6),
  days_since_big    int,                   -- 距那根阳线几个交易日
  hold_vs_big_pct   numeric(8,3),          -- 现价 / 那根阳线收盘 - 1（%）：脱离太多就不是埋伏是追高

  -- 各类型判定（一只票同场可多个为真）
  is_sticky         boolean not null default false,   -- 均线汇集 + 横盘窄幅 + 周线重心连升（严档三条）
  is_pullback_ma20  boolean not null default false,   -- 趋势中回踩 MA20 且缩量
  is_pullback_ma60  boolean not null default false,   -- 趋势中回踩 MA60 且缩量
  is_obv_bid        boolean not null default false,   -- 量价背离（钱进价不涨）
  is_dragon_back    boolean not null default false,   -- 龙回头（大阳后横盘不破低点且缩量）
  ambush_types      text[]  not null default '{}',    -- 命中的类型码，筛选就按这个数组重叠走
  strict_pass       boolean not null default false,   -- 是否过"严档"三条（默认那档，实测约 40 只/场）
  ambush_score      numeric(8,3),                     -- 排序用的合成分（判据在 app/compute/ambush_setup.py）

  -- 覆盖与缺数：判据算不出来的那些必须自己说，不许留 0 冒充
  bars_60           int,                    -- 60 日窗口里真有几根 K 线（<60 时 MA60 不成立）
  missing           text,                   -- 缺了哪些读数（逗号分隔的列名），全都有就 null
  spec_version      text        not null,

  calculated_at     timestamptz not null default now(),

  primary key (security_code, trade_date, spec_version)
);

comment on table  analytics.ambush_setup               is '埋伏票（左侧机会）榜：横盘蓄势、均线汇集、周线重心抬升、回踩不破、量价背离、龙回头。一物一行，一只票同场可命中多个类型（ambush_types 数组）。判据与写入 app/compute/ambush_setup.py，每日加工段步骤名 ambush。价格一律后复权（close × adjustment_factor）';
comment on column analytics.ambush_setup.ma_spread_pct is 'MA5/10/20/30/60 里最高与最低之差除以最低那根，单位 %。越小 = 各周期持仓成本越趋同（俗称均线粘合）。实测 2026-10-12 满窗 5,526 只：中位 6.46%，P25 3.87%，小于 1.5% 的 181 只';
comment on column analytics.ambush_setup.weekly_streak is '周线重心（周均后复权价）从当前周往回数、连续比上一周高的周数。周线比日线难做形态，重心连升是"有人在慢慢抬价"的证据。缺一个完整周就不算连断，宁可少报';
comment on column analytics.ambush_setup.week_sessions_cur is '当前 ISO 周已有几个交易日。节假日周只有 2~4 天（实测 2026-W41 只有 2 场），这种周的重心与 5 天周不完全可比，所以一起交出';
comment on column analytics.ambush_setup.week_partial    is '当前周是不是不完整周（少于 5 个交易日）。true 时 weekly_streak 不把本周计入连升，只用已完成的周';
comment on column analytics.ambush_setup.range20_pct     is '近 20 日 (最高-最低) / 当日后复权收盘，单位 %。横盘窄幅的量尺。实测 2026-10-12：中位 16.9%，P25 11.7%';
comment on column analytics.ambush_setup.box_pos_pct     is '现价在 60 日高低区间里的位置，0 = 贴下沿、100 = 贴上沿。贴内（还没突破）才是埋伏，已经贴上沿接近突破就归右侧那边管';
comment on column analytics.ambush_setup.shrink_ratio    is '近 5 场（含当日）均量 / 第 6~60 场均量。<1 缩量，越小越安静。实测 2026-10-12 中位 0.76';
comment on column analytics.ambush_setup.obv_ratio       is '近 20 日（涨日成交量 - 跌日成交量）/ 总成交量。为正且偏大 = 钱在进而价没走，吸筹那一类。取值范围 -1~1';
comment on column analytics.ambush_setup.hold_vs_big_pct is '现价相对最近一根放量大阳/涨停收盘的涨跌幅，单位 %。守住但没脱离（0.90~1.12）才算回踩买点，脱离太多就是追高';
comment on column analytics.ambush_setup.ambush_types    is '本场命中的类型码数组：sticky_pull / pullback_ma20 / pullback_ma60 / obv_bid / dragon_back。榜单筛选按数组重叠（&&）走，且必须拼在 limit 之前';
comment on column analytics.ambush_setup.strict_pass     is '严档三条全过：均线极差 < 1.5% 且 20 日振幅 < 11.7% 且 周线重心近 6 周连升 >= 4 周。实测 2026-10-12 约 40 只';
comment on column analytics.ambush_setup.ambush_score    is '排序用合成分（判据只住 app/compute/ambush_setup.py，页面不自己重算）。分高 = 埋伏形态更标准（更紧、更静、重心抬得更稳）';
comment on column analytics.ambush_setup.bars_60         is '60 日窗口里真有几根 K 线。小于 60 时 MA60 与趋势读数不可信（次新股不凑数），严档直接判不通过';
comment on column analytics.ambush_setup.missing         is '这一行缺了哪些读数（逗号分隔列名），全有则为 null。缺的列一律留 NULL，不按 0 参与排序或判据 —— 0 会被读成"很紧/很缩"，是反的';

create index if not exists ambush_setup_date_idx    on analytics.ambush_setup (trade_date);
create index if not exists ambush_setup_score_idx   on analytics.ambush_setup (trade_date, ambush_score desc nulls last);
create index if not exists ambush_setup_types_idx   on analytics.ambush_setup using gin (ambush_types);

grant select, insert, update, delete on analytics.ambush_setup to ft_etl, ft_app;
grant select on analytics.ambush_setup to ft_reader;
