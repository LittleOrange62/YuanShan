-- 59_national_team_denom_source.sql —— 国家队那一屏的分母换尺（nt-1 → nt-2）
--
-- 为什么要换（用户 2026-10-31 点名「图表做的连贯一点」，量完数据现实后的结论）：
--   nt-1 的分母只用 `market_data.stock_snapshot.float_market_value_yuan` 求和，而那张快照表
--   最早只有 **2026-07-22**（现查 2026-10-31：49 天 / 2026-07-22~2026-10-29）⇒
--   `analytics.national_team_etf_daily` 里 679 场只有 **47 场**有占比（现查同一天），
--   占比线在图上就是"前面一大段全空、只有末端一小截"。这不是画法问题，是分母没有底。
--
-- 新尺（nt-2）：分母改成**用当日日线自算**——
--   每票流通股本＝当日成交量 ÷ 当日换手率（`stock_daily.volume_shares / (turnover_rate_percent/100)`），
--   乘当日**未复权**收盘＝该票当日流通市值，再对 `reference_data.sector_member` 的 ZS_沪深300 成分求和。
--   这不是插值也不是模型外推：用的就是那一天的成交、换手、收盘三个原始读数。
--
-- 实测对账（2026-10-31 现查，只比两边都有数的 2026-10-22 ~ 10-29 这 6 天，成分 299~300 只）：
--   日线自算 48.007 / 48.482 / 48.761 / 48.283 / 48.537 / 48.436 万亿
--   快照求和 48.096 / 48.574 / 48.856 / 48.378 / 48.631 / 48.560 万亿
--   差 −0.186% ~ −0.256% ⇒ 与当初「快照 vs 东财 −0.08%」同一量级，可以当主尺。
--   差额来源：那天有一两只成分停牌或换手率没给（299 只有数 vs 300 只成分），少算那一只的市值。
--
-- 两件事明确不做：
--   ① `total_mv_yuan` / `ratio_total_pct` **不换尺、不反推**：总市值要的是总股本，
--      日线里没有那一列（换手率只覆盖流通股本）⇒ 备用尺仍然只有快照有数的那几十天，
--      读侧与界面上要如实说"这一把尺只有 N 天"，不许与主尺混成一条线。
--   ② nt-1 那 47 行留着不动（新行是新的 spec_version，主键含 spec_version ⇒ 不覆盖），
--      换尺前后可以在同一张表里对账。
--
-- 新增三列（都是"这一天的分母是怎么来的、漏了几只"的自证，不是判据）：
--   denom_source   'daily_derived'（nt-2）/ 'snapshot'（nt-1 那批回填补记）/ null（没算出分母）
--   denom_covered  求和时真正有数的成分只数
--   denom_members  当天这份成分名单的总只数（分母）
--   ⇒ `denom_covered < denom_members` 就是"这一天的分母少算了票"，界面要能分开说。

alter table analytics.national_team_etf_daily
    add column if not exists denom_source  text,
    add column if not exists denom_covered integer,
    add column if not exists denom_members integer;

comment on column analytics.national_team_etf_daily.denom_source is
  '分母是哪把尺给的：''daily_derived''＝当日成交量÷换手率反推流通股本 × 当日未复权收盘，对 300 成分求和（nt-2 全史用这一把）；'
  '''snapshot''＝直接加 `stock_snapshot.float_market_value_yuan`（nt-1 那批，最早只到 2026-07-22）。'
  '同一天的两把尺实测差 −0.186%~−0.256%（2026-10-22~10-29 六天，见 app/db/sql/59 头部），'
  '所以**跨 spec_version 的占比值不许直接连成一条线**，读侧按版本取一把。null＝这一天没有分母。';
comment on column analytics.national_team_etf_daily.denom_covered is
  '求和时真正有数的成分只数（分母侧的覆盖）。某只成分那天停牌或换手率没给，它就进不了和。';
comment on column analytics.national_team_etf_daily.denom_members is
  '当天那份成分名单的总只数（`denom_covered` 的分母）。两者不相等 ⇒ `missing` 里带 '
  '''float_mv_yuan.partial''，屏幕上要说"这一天的分母少算了 N 只"，不能念成一个看起来正常的占比。';

-- 覆盖 56 里那两条换尺前写的列注释（库里现在读这一版；56 文件本身不动，避免校验和漂移）
comment on column analytics.national_team_etf_daily.ratio_float_pct is
  '分子分母**同日**才有值。主尺（占流通市值）的分母＝当日日线自算：300 成分每票「成交量 ÷ 换手率」反推流通股本'
  ' × 当日未复权收盘，再求和（denom_source=''daily_derived''，nt-2 起 679 场全史都有底）。'
  '换尺之前（nt-1）用 stock_snapshot.float_market_value_yuan 求和，那张表最早 2026-07-22 ⇒ 679 场只有 47 场有占比。'
  '仍然为 null 只有两种：那天成分日线整段没取到（missing 带 float_mv_yuan），'
  '或有票反推不出股本（float_mv_yuan.partial，少算几只记在 denom_covered/denom_members）；界面读 — 不插值不补 0。';
comment on column analytics.national_team_etf_daily.ratio_total_pct is
  '备用尺（占总市值）＝规模 ÷ 300 成分总市值。分母**仍然只从 stock_snapshot 来**（总股本不在日线里，'
  '换手率只覆盖流通股本）⇒ 这一把尺的覆盖照旧只有快照那几十场；'
  '读侧与界面历史档位/趋势线对它如实读「判不了」，不许因为主尺补齐了就一起放开。';

-- 三列都要能被读侧与只读实例看见（与 56 同一套授权）
grant select on analytics.national_team_etf_daily to ft_reader;
grant select, insert, update, delete on analytics.national_team_etf_daily to ft_app, ft_etl;
