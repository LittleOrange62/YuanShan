-- -*- coding: utf-8 -*-
-- 27_security_session_span.sql
-- 「这只票在日线表里的头 5 场是哪几天」落成一张小表，别再每行去扫全表窗口。
--
-- 为什么要有它（2026-10-07 实测）：`app/compute/limit_prices.py` 的次新判据原写作
-- `row_number() over (partition by security_code order by trade_date)`（全量一趟）或
-- 两条相关子查询（单日/区间一趟）。**两种都不便宜**：
--   * 全库那一趟是一条 640 万行的 UPDATE，跑了 40 分钟还没提交（外部看不见进度）；
--   * 相关子查询那两条要沿 `security_code` 往前/往后找，`stock_daily` 是按 `trade_date`
--     RANGE 分区的（22 个分区），**相关值走不了分区裁剪** ⇒ 每行展开成 22 个分区的索引探测，
--     单日 5,546 行 = 24 万次下探 = 4.6 秒；一个月 21 场 = 566 秒。
-- 而这件事的答案几乎不变（老票的头 5 场永远在那儿），本来就是该物化的东西。
--
-- 表很小：一只票一行。刷新规则写在 `limit_prices.refresh_first_session()`：
-- 全刷一次（首次/重建），日常只补"没有行的"与"还不满 5 场的"两类（后者只有个位数行，很便宜）。

create table if not exists reference_data.security_session_span (
  security_code  text        not null,
  first_session  date        not null,      -- 该股在 market_data.stock_daily 里的最早一场（pre_close 有效才算）
  fifth_session  date,                      -- 第 5 场；不足 5 场为 NULL（= 现在还全在"头 5 场"里）
  sessions       integer     not null,      -- 累计场次（pre_close 有效的行）
  refreshed_at   timestamptz not null default now(),
  constraint security_session_span_pk primary key (security_code),
  constraint security_session_span_sessions check (sessions >= 1)
);

comment on table reference_data.security_session_span is
  '逐票的日线场次跨度。**存在的唯一理由**是 `app/compute/limit_prices.py` 的次新判据：'
  '"这一天是不是这只票的头 5 场"。它要的是这只票**全历史**的头 5 场，而 limit 判据按单日/区间跑，'
  '不做这张表就得每行回扫全表（分区表上还扫不掉）。`list_date` 全库 0/5,577 有值，指望不上，'
  '所以只能用"日线里的第一场"当上市锚点 —— 这也正是 `no_limit_first5` 里"首日不早于 '
  '2021-08-03（全库数据起点）"那条防误判的出处';
comment on column reference_data.security_session_span.first_session is
  '最早一场。判定"这只票是不是库里数据起点之后才上市的"用的就是它（早于起点 ⇒ 老票，头 5 场不设限那条不适用于它）';
comment on column reference_data.security_session_span.fifth_session is
  '第 5 场的日期；NULL 表示累计不足 5 场（此时该票任何一天都还在"头 5 场"内）。'
  '判据写成 `trade_date <= coalesce(fifth_session, infinity)`，所以补进第 5 场那天起自动失效';
comment on column reference_data.security_session_span.sessions is
  'pre_close 有效的场次。`limit_prices` 只算有 pre_close 的行，这里同口径，别一边算一边不算';
comment on column reference_data.security_session_span.refreshed_at is
  '这行什么时候刷的。日常增量只补"缺行"与"还不满 5 场"的两类，老票不会天天被重写';

grant select, insert, update, delete on reference_data.security_session_span to ft_etl, ft_app;
grant select on reference_data.security_session_span to ft_reader;
grant select on reference_data.security_session_span to ft_admin;

-- ------------------------------------------------------------------ 验收（跑完贴这三条）
-- 1) 表在、行数等于有日线的票数
-- select count(*) from reference_data.security_session_span;
--    select count(distinct security_code) from market_data.stock_daily where pre_close > 0;
-- 2) 头 5 场的样例（新票应该 fifth_session 为 NULL 或很近）
-- select * from reference_data.security_session_span order by first_session desc limit 10;
-- 3) 覆盖度：日线里有数、表里没行的票应当为 0（刷过之后）
-- select count(*) from (select distinct security_code from market_data.stock_daily
--    where pre_close > 0) d left join reference_data.security_session_span s using (security_code)
--    where s.security_code is null;
