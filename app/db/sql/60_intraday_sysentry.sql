-- -*- coding: utf-8 -*-
-- 60_intraday_sysentry.sql
-- 盘中 14:40 那一刀「交易体系榜」（三条同时成立）的**取数落库**侧。
--
-- 为什么要单独落一张表（用户 2026-11-03 定的分层：判断与取数属于数据加工环节，是取数落地的，
-- 跟推送到飞书的那些行为分开，不要互相影响）：
--   外部那份 `sysentry_intraday.py` 是**边取边发**——14:40 拉 TQ/新浪快照、现算判据、当场拼文本发群。
--   那样有三个后患：①发失败就想重发，而重发时快照已经变了，群里的第二条与第一条不是同一场数；
--   ②判据算完的中间结果只活在一条消息里，事后没法问"那天 14:40 榜上都有谁、为什么它没上"；
--   ③取数一挂（TQ 没起、快照非今日）整条链就没有留痕，界面上只看到"没发"。
--   拆开后：`sysentry_fill` 只把判据结果落成行（不发任何东西），`sysentry_report` 只读这张表渲染并发送。
--   于是 B 侧任何时候重跑（`--at`/补发）拿到的数与当时一字不差，A 侧的失败也自成一格留痕。
--
-- ⚠️ 与 `analytics.system_entry`（DDL 33/51）的分工，这条必须先看：
--   · 33 = **收盘后**那一榜，母体是当天已落库的日线，15:59 收盘链算，是"今天到底谁进榜"的准数。
--   · 60 = **盘中 14:40 那一刀**，今天这一根是实时快照拼出来的（未收盘），
--     所以它**允许与 33 不一致**（尾盘跳水、尾盘涨停都会让两边不同）。
--   两边共用同一把尺子：判据都住 `app/compute/system_entry.judge()`，`spec_version` 都是 `sys-1`；
--   这张表另有 `quote_source`/`quote_day` 两格说明"今天这根是从哪个源、哪一天的行情拼的"——
--   盘中播报最容易出的事是拿昨天的行情当今天算，出处必须跟着行进表，不能只写在消息里。
--
-- 口径纪律（沿用项目既定）：
--   · 缺数写 NULL 不写 0（`pullback_max_vol_ratio` 为 null 是"这一格判不了"，0 是真读数）。
--   · `list_kind`：`entry`=三条全过（榜上那批），`watch`=①②成立③未确认（观察池）。
--     原来观察池只在消息里报个数、不落库，事后无从回查"那天为什么没上榜"，这里逐票落。
--   · 价格单位元；`pct`/`gap_pct` 一律**百分数**；`adjustment_factor` 是后复权因子（止损位换算要用）。

create table if not exists analytics.intraday_sysentry (
    trade_date              date        not null,      -- 交易日（非交易日不写：由作业侧的日历闸判）
    captured_at             timestamptz not null,      -- 这一场快照的时刻（同一次 fill 的所有行共用）
    spec_version            text        not null,      -- 'sys-1'，与 analytics.system_entry 同一把尺子
    security_code           text        not null,      -- '600001.SH' 形状（与 stock_daily 一致）
    list_kind               text        not null check (list_kind in ('entry','watch')),

    security_name           text,                      -- 快照带的商品名，原样不改写
    rank                    smallint,                  -- 榜内名次（entry 从 1 起；watch 按缺口幅度另编）
    close                   numeric(12,4),             -- 快照现价（元）＝"今天这根"的收盘位
    pre_close               numeric(12,4),             -- 快照昨收（元）；与库昨收不符的已在 fill 侧剔除
    pct                     numeric(10,4),             -- 涨幅百分数（现价/昨收）

    gap_date                date,                      -- 向上整段跳空缺口那一天
    gap_pct                 numeric(10,4),             -- 缺口幅度（百分点）
    gap_unfilled            smallint,                  -- 到今场仍未回补的场数
    pullback_max_vol_ratio  numeric(10,4),             -- 回调段最大单日量 / 缺口日量
    pullback_down_cnt       smallint,                  -- 回调段阴线根数
    stop_ref_hfq            numeric(12,4),             -- 止损参考位（后复权价，渲染时按因子折回元）
    adjustment_factor       numeric(12,6),             -- 本场后复权因子

    board                   text,                      -- 档位资料里的板块（主板/创业板/科创板/北交所）
    quote_source            text        not null,      -- 通达信TQ / 新浪 / 腾讯 / 东财ulist / 降级说明
    quote_day               text,                      -- 源标的"行情日期"（盘中把它当证据存下来）
    calculated_at           timestamptz not null default now(),

    constraint intraday_sysentry_pkey primary key (trade_date, captured_at, security_code, spec_version)
);

create index if not exists ix_intraday_sysentry_day
    on analytics.intraday_sysentry (trade_date desc, captured_at desc, list_kind);

comment on table analytics.intraday_sysentry is
    '盘中 14:40 交易体系榜的取数落库侧（今天这根由实时快照拼，允许与收盘后的 system_entry 不同）；发送侧只读这张表。';
comment on column analytics.intraday_sysentry.list_kind is
    'entry=三条全过（榜上那批）｜watch=①②成立③未确认（观察池，逐票落，原来只报个数）';
comment on column analytics.intraday_sysentry.quote_source is
    '今天这一根的出处。降级（TQ 不可用改走网络源）要把原因写进这一格，榜尾与表里都看得见。';
comment on column analytics.intraday_sysentry.quote_day is
    '源标的行情日期（形如 2026-11-04）。盘中拿昨天行情当今天算是这套链最容易出的事，故随行进表。';

grant select on analytics.intraday_sysentry to ft_reader;
grant select, insert, update, delete on analytics.intraday_sysentry to ft_etl, ft_app;
