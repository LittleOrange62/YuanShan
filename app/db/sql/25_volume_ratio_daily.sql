-- -*- coding: utf-8 -*-
-- 25_volume_ratio_daily.sql
-- 逐票「量能增长倍数」：今天的成交量是上一根有日线的交易日的多少倍。
--
-- 为什么要单开一张表（用户 2026-10-07 原话）：
--   「每日定时计算的任务中，就是数据计算，需要新增一个字段的计算：有效交易日进行了逐日量能比对，
--     计算每日增长倍数 —— 就是今天的成交量是前一天的多少倍。然后顺便帮我计算所有票近一年的
--     每日量能增长倍数，计算结果放进数据库，应该是要新建一张表，后续定时任务每天都要跑一遍的。」
--   它不落库就只是"每次现算一遍 132 万行"，而近一年的逐日倍数本身就是要留着比的历史。
--
-- 三条口径必须写死在这里，否则日后一定被读错：
--   ① **"前一天"是这只票自己的上一根日线**，不是全场上一交易日。停牌十天的票复牌首日
--      分母是十天前的量 —— 所以配 `prev_trade_date` 与 `prev_gap_days` 一起交，
--      光看 `vol_ratio` 会把"停牌三个月后复牌放量"读成"今天突然放量"。
--   ② **分母缺/为零就不写倍数**（`vol_ratio` 留 NULL + `missing` 记原因），不写 0 也不写 1：
--      0 会被读成"缩量到没有"，1 会被读成"量没变"，两个都是假读数。
--   ③ 量用 `volume_shares`（股数，未复权原值），**不用成交额**。换手/量能比的常规读法是量，
--      换成额就把价格变动混进"倍数"里（价格涨一倍、量不变，倍数会被做成 2.0）。
--
-- 主键带 `spec_version`：改口径（比如将来改成"对 5 日均量的倍数"）是新写一批行，
-- 历史那批不动，两套口径可以并排对账（约定同 analytics.market_breadth / market_sentiment_daily）。

create table if not exists analytics.volume_ratio_daily (
  trade_date        date           not null,
  security_code     text           not null,
  spec_version      text           not null,      -- 判据版本，见 app/compute/volume_ratio.py::SPEC_VERSION
  volume_shares     bigint,                       -- 当日成交量（股，原值未复权）
  prev_trade_date   date,                         -- 这只票上一根有日线的交易日（不是全场上一交易日）
  prev_volume       bigint,                       -- 那一天的成交量；NULL 表示没有上一根（本库首场 / 停牌前无数据）
  prev_gap_days     integer,                      -- 两根日线之间隔了几个自然日：1=紧邻，>4 基本是停牌或长假
  vol_ratio         numeric(14,4),                -- 当日量 ÷ 上一根量。**缺分母就是 NULL，不是 0/1**
  missing           text,                         -- 算不出的原因（no_prev_bar / zero_prev_volume / no_volume…）
  calculated_at     timestamptz    not null default now(),
  primary key (security_code, trade_date, spec_version)
);

comment on table  analytics.volume_ratio_daily              is '逐票每日量能增长倍数（今日成交量 ÷ 上一根日线成交量）。判据与写入：app/compute/volume_ratio.py（入口就是它自己的 main()）；每日加工段步骤名 `volratio`（ingest/jobs/e1_fill_all.py 的 STAGES + plan）';
comment on column analytics.volume_ratio_daily.prev_trade_date is '**这只票自己的**上一根日线日期。停牌/退市整理/本库首场都会让它离 trade_date 很远，那时候的 vol_ratio 读作"对比很久以前那天的量"';
comment on column analytics.volume_ratio_daily.prev_gap_days   is '两根日线之间隔的自然日数。1=紧邻上一日；长假后首日会是 3~10；明显偏大＝中间停牌，倍数不可与紧邻日并排读';
comment on column analytics.volume_ratio_daily.vol_ratio       is '成交量倍数。NULL＝算不出（原因看 missing 列），**绝不用 0 或 1 顶替** —— 0 会被读成"缩量到没有"、1 会被读成"量没变"';
comment on column analytics.volume_ratio_daily.missing         is '算不出时的原因，与 vol_ratio=NULL 成对出现。有值而 vol_ratio 也有值＝不该发生，体检会报';
comment on column analytics.volume_ratio_daily.spec_version    is '判据版本在主键里：换口径（如改成对 5 日均量之比）是新写一批行，历史那批不动';

create index if not exists volume_ratio_daily_day_idx
  on analytics.volume_ratio_daily (spec_version, trade_date desc, vol_ratio desc nulls last);
create index if not exists volume_ratio_daily_code_idx
  on analytics.volume_ratio_daily (security_code, trade_date desc);

-- 应用角色要能读也能写（写入侧走 ft_etl/ft_app，读侧走 ft_reader；与 24 号同一套 grant 姿势）
grant select, insert, update, delete on analytics.volume_ratio_daily to ft_etl, ft_app;
grant select                         on analytics.volume_ratio_daily to ft_reader;
