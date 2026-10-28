-- -*- coding: utf-8 -*-
-- 16_sector_trend_signal.sql
-- 板块趋势形成度与左侧低吸信号（判据文档：docs/页面展示工作指南/05-板块趋势与低吸判据.md）
--
-- 为什么要落库而不是接口现算：判据要回答的是「某一天它算不算刚转」。
-- 现算意味着每次请求重跑一遍全窗口，而且参数一改，历史上所有信号的含义就跟着被改写。
-- 落库 + spec_version 进主键 = 当时那一版参数算出的结果永远原样在。
--
-- 两张表：
--   analytics.sector_trend_signal        每个板块每交易日每窗口集每判据版本一行
--   reference_data.watchlist_member_snapshot
--       自选组成员每日快照。**这张表存在的唯一理由**：config.watchlist_stock 只有当前状态，
--       拿今天的名单回算过去的组涨幅是未来函数（reference_data.sector_member 有 in_date/out_date，
--       所以通达信板块没这个问题、只有自选板块有）。从今天起攒，攒够窗口才有"实盘序列"。

create table if not exists analytics.sector_trend_signal (
  sector_code    text           not null,
  trade_date     date           not null,
  window_set     text           not null,       -- 如 '5/11/21'；换窗口集就是另一批结论，不互相覆盖
  scope          text           not null default 'sector',   -- sector=通达信板块 / group=自选组
  universe       integer,                       -- 当日参与排名的母体板块数：分母不写出来，前 N 名没有意义
  dir_agree      numeric(10,3),                 -- 三窗口斜率方向一致度（0~100），NULL=算不出
  persist        numeric(10,3),                 -- 窗口内热度逐日抬升占比
  breadth        numeric(10,3),                 -- 上涨家数占比（≥BREADTH_MIN 才算普涨）
  volume_ok      numeric(10,3),                 -- 量比分（>3 反而降分）
  position       numeric(10,3),                 -- 收盘价在长窗口高低区间里的位置（低吸的核心一项）
  rank_momentum  numeric(10,3),                 -- 热度排名 5 日前进量
  heat           numeric(12,4),
  heat_pctile    numeric(10,4),                 -- **自身**长窗口滚动分位（1.0=自身区间最高），不是当日横截面名次
  slope_short    numeric(12,4),
  slope_mid      numeric(12,4),
  slope_long     numeric(12,4),
  points_short   smallint,
  points_mid     smallint,
  points_long    smallint,
  dip_score      numeric(10,3),                 -- 只在 turn_up_seed / forming 下有值
  regime         text           not null,       -- insufficient/downtrend/turn_up_seed/forming/established/overheated
  reasons        jsonb          not null,       -- 每个分项当时的原始输入；界面「凭什么叫它刚转」念的是它
  missing_terms  smallint       not null default 0,
  spec_version   text           not null,       -- 判据版本：改阈值/权重就是新一批行
  calculated_at  timestamptz    not null,
  constraint sector_trend_signal_pk primary key (sector_code, trade_date, window_set, scope, spec_version),
  constraint sector_trend_signal_regime check (regime in
    ('insufficient','no_shape','downtrend','turn_up_seed','forming','established','overheated'))
);

comment on table  analytics.sector_trend_signal           is '板块趋势形成度与低吸候选（判据见 docs/页面展示工作指南/05）。一行=一个板块在一个交易日、一个窗口集、一个判据版本下的结论';
comment on column analytics.sector_trend_signal.window_set is '窗口集指纹，如 5/11/21。窗口是判据的一部分：换窗口就是另一批结论，不互相覆盖';
comment on column analytics.sector_trend_signal.regime     is '互斥、按顺序判：insufficient→downtrend→turn_up_seed→forming→established→overheated。点不足永远是 insufficient，不猜';
comment on column analytics.sector_trend_signal.dip_score  is '低吸分。权重是**可商量的先验**（35/25/20/10/10），不是市场规律；只在 turn_up_seed/forming 下有值';
comment on column analytics.sector_trend_signal.reasons    is '分项的原始输入（斜率值、点数、量比、高低位、排名前后差）。事后重算必须与当时一致，否则界面上"为什么"是假话';
comment on column analytics.sector_trend_signal.spec_version is '判据版本，在主键里：调阈值不会覆盖旧行，历史信号不被今天的参数悄悄改写';
comment on column analytics.sector_trend_signal.universe   is '当日进排名的母体（默认概念+行业）。行为统计板块如"昨日涨停"不进分母，否则"资金往哪走"会被读成"谁涨了"';
comment on column analytics.sector_trend_signal.heat_pctile is
  '热度在**自身**长窗口（默认 21 场）里的滚动分位，0~1，1.0=正处于自身区间最高；与 `analytics.ignition.detail._q_heat` 同口径，只看过去、窗口含当日。'
  '⚠️ 不是"当日全场第几名"—— 横截面名次在 `reasons.heat_rank` + `universe` 里。两者方向相反：'
  '横截面名次 1=最热，所以 rank/universe 的 1.0 是**最冷**；sts-1 把它当分位喂给过热判据（>=0.9），实测把 369 行热度低于当日中位的板块判成过热。'
  '所以 `overheated` 说的是"在自己区间顶部"，不等于"今天全场最热"';

create index if not exists sector_trend_signal_day_idx
    on analytics.sector_trend_signal (trade_date, window_set, spec_version, regime);


create table if not exists reference_data.watchlist_member_snapshot (
  trade_date    date    not null,
  group_id      bigint  not null,
  group_name    text,                 -- 当日组名冗余存一份：组会改名，事后要知道当时叫什么
  security_code text    not null,
  captured_at   timestamptz not null,
  constraint watchlist_member_snapshot_pk primary key (trade_date, group_id, security_code)
);

comment on table reference_data.watchlist_member_snapshot is
  '自选组成员每日快照。没这张表，自选板块的趋势只能"以今日成员回算"（未来函数），不能用于回测与胜率统计';
comment on column reference_data.watchlist_member_snapshot.group_name is
  '当日组名快照。组改名后，历史序列的标签仍然对得上当时那个组';

create index if not exists watchlist_member_snapshot_day_idx
    on reference_data.watchlist_member_snapshot (trade_date, group_id);


-- 生效参数（界面顶部要显示当前值；不硬编码在页面里）
-- `config.setting.key` **没有唯一约束**（实测 0 个 table_constraint），所以这里用 not exists 而不是
-- `on conflict (key)` —— 后者在现库上直接报错。已存在就不覆盖：参数是人在用的，迁移脚本不许改口。
insert into config.setting (key, value, label, category)
select 'sector_trend_signal.params',
       '{"windows":[5,11,21],"min_points_ratio":0.7,"position_low":0.35,"position_high":0.6,"turn_lookback":3,"breadth_min":0.55,"vol_range":[1.0,3.0],"overheat":{"position":0.85,"heat_pctile":0.9},"weights":{"freshness":35,"position":25,"breadth":20,"volume":10,"rank":10},"sector_types":["概念","行业"],"spec_version":"sts-2"}'::jsonb,
       '板块趋势形成度与低吸信号参数', 'compute'
 where not exists (select 1 from config.setting where key = 'sector_trend_signal.params');


-- 权限：写只有 etl/app，读包括 reader（页面走只读身份）
grant select, insert, update, delete on analytics.sector_trend_signal              to ft_etl, ft_app;
grant select on analytics.sector_trend_signal                                       to ft_reader;
grant select, insert, update, delete on reference_data.watchlist_member_snapshot     to ft_etl, ft_app;
grant select on reference_data.watchlist_member_snapshot                             to ft_reader;
