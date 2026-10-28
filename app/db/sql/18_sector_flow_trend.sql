-- -*- coding: utf-8 -*-
-- 18_sector_flow_trend.sql
-- 「资金轮动趋势」落库表（需求：用户 2026-09-27 —— 近 10/20/30 天连续流入流出的统计与趋势图）
--
-- 为什么这张表**只有统计量、没有信号**（实测在 `docs/数据加工工作指南/08` 坑 12，
-- 复核脚本 `.local/flow_share_test.py`，1249 场 × 963 板块）：
--   份额连续 n 天抬升 → 第 n+1 天仍抬升的概率  46%(n=1) → 40% → 35% → 33% → 33%(n=5)
--                                                  → 32% → 32% → 31%(n=9)
--   基线（不分条件）就是 46%。也就是说**越"连续"越不延续**，是均值回复不是惯性。
--   所以这里存 streak / cum / rank，**看多看空由界面和用户自己判断**，
--   数据侧不许把它包装成"主力连续吸金"。
--
-- 两种 `basis`（同一张表，将来主力净额逐日入库后不必改结构）：
--   'share'    份额 = 本板块成交额 ÷ 当日全场板块成交额合计。**现在就能算**（`sector_daily.amount_yuan` 有 1244 场）。
--   'main_net' 主力净流入额，来自 `market_data.fund_flow_rank`。**该表当前 0 行**（逐日入库还没接上），
--              所以这一 basis 现在只会返回 skipped，一行不写。
--   ⚠️ 实测反面教材：用"绝对成交额 ÷ 自身 20 场均额"去测连续性，结论是"73%→83% 延续"，
--   看着像资金有惯性，其实全是全场共同放量的自相关 —— **判据必须用份额，不能用绝对额**。
--   这就是为什么 `value` 存的是份额而不是金额，而 `amount_yuan` 只作辅助列交出。

create table if not exists analytics.sector_flow_trend (
  sector_code     text           not null,
  trade_date      date           not null,
  basis           text           not null,      -- share / main_net
  value           numeric(20,12),                -- 当日度量：share=份额(0~1)，main_net=净额(元)
  value_prev      numeric(20,12),                -- 上一场同口径值（算日变化用的原值）
  day_delta_bps   numeric(12,3),                -- 当日 − 上一场，份额口径按 bp 交（1bp=0.01%）
  streak_up_days  smallint,                     -- 以当日收尾的**严格递增**连续场次（0=不是递增）
  streak_dn_days  smallint,                     -- 以当日收尾的**严格递减**连续场次
  cum_10          numeric(20,12),                -- 近 10 场累计：share=末−首的份额差，main_net=净额求和
  cum_20          numeric(20,12),
  cum_30          numeric(20,12),
  days_10         smallint,                     -- ⚠️ 窗口内**实际有数**的场次数，不是窗口长度
  days_20         smallint,
  days_30         smallint,
  rank_10         integer,                      -- 按 cum_10 在当日母体里的名次（1 最大）；纯排序，不含好坏
  rank_20         integer,
  rank_30         integer,
  universe        integer,                      -- 当日参与统计的板块数：分母不交，名次没有意义
  universe_amount numeric(24,4),                -- 当日全场成交额合计（份额的分母，原样交出可复核）
  amount_yuan     numeric(24,4),                -- 本板块当日成交额（辅助列，**不许当"资金流入"读**）
  missing_days    smallint      not null default 0,
  params_snapshot jsonb         not null,       -- 这一行是按哪套参数算的，事后不许被参数改写
  reasons         jsonb         not null,       -- 原始输入：窗口内逐日 value、首末日期、分母、streak 的比较链
  spec_version    text          not null,
  calculated_at   timestamptz   not null,
  constraint sector_flow_trend_pk primary key (sector_code, trade_date, basis, spec_version),
  constraint sector_flow_trend_basis check (basis in ('share','main_net')),
  constraint sector_flow_trend_streak check (not (coalesce(streak_up_days,0) > 0
                                                  and coalesce(streak_dn_days,0) > 0))
);

comment on table  analytics.sector_flow_trend           is '资金轮动趋势的**统计量**（份额口径连续天数 / 近 10·20·30 场累计 / 名次）。不含方向判断：实测"连续 n 天抬升"之后第 n+1 天延续的概率从 46% 单调掉到 31%，是均值回复，见 08 契约坑 12';
comment on column analytics.sector_flow_trend.basis     is 'share=板块成交额占全场份额（现在能算，sector_daily.amount_yuan）；main_net=主力净流入（fund_flow_rank 当前 0 行，这一 basis 只会 skipped，不写行）';
comment on column analytics.sector_flow_trend.value     is '当日度量。share 口径是**份额不是金额**：份额剔掉了全场共同放量/缩量，绝对额不剔 —— 实测用绝对额测连续性会得出"73%→83% 延续"的假惯性';
comment on column analytics.sector_flow_trend.streak_up_days is '以当日收尾的严格递增连续**场次**（不是日历天）。实测这个数越大，次日继续抬升的概率越低（46%→31%），界面别给它配红色箭头';
comment on column analytics.sector_flow_trend.days_10   is '窗口内实际有数的场次数。攒不满 10 场时 cum_10 是"前 6 场的差"而不是"10 场的差" —— 这两个数不可比，所以 days_N 必须和 cum_N 一起交出去';
comment on column analytics.sector_flow_trend.universe  is '当日母体板块数（默认 行业+概念 里有成交额的这批）。名次离开分母没有意义';
comment on column analytics.sector_flow_trend.reasons   is '原始输入留痕：窗口内逐日份额、首末场次日期、全场分母、连续天数的比较链。事后重算必须与当时一致';
comment on column analytics.sector_flow_trend.spec_version is '判据版本，在主键里：调窗口/口径是新一批行，旧行不覆盖';

create index if not exists sector_flow_trend_day_idx
    on analytics.sector_flow_trend (trade_date, basis, spec_version, cum_30 desc);

-- 生效参数（**从这张单行取，不要用 max(spec_version) 字符串比 —— 出 sft-10 会翻车**）
insert into config.setting (key, value, label, category)
select 'sector_flow_trend.params',
       '{"windows":[10,20,30],"sector_types":["行业","概念"],"basis":["share"],"min_days_ratio":0.6,"spec_version":"sft-1","evidence":"份额连续 n 天抬升→次日仍抬升 46%(n=1) 单调降到 31%(n=9)；基线 46%。见 docs/数据加工工作指南/08 坑 12"}'::jsonb,
       '资金轮动趋势参数（统计量口径，非信号）', 'compute'
 where not exists (select 1 from config.setting where key = 'sector_flow_trend.params');

grant select, insert, update, delete on analytics.sector_flow_trend to ft_etl, ft_app;
grant select on analytics.sector_flow_trend to ft_reader;
