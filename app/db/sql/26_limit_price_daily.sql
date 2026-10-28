-- -*- coding: utf-8 -*-
-- 26_limit_price_daily.sql
-- 涨跌停档位的**当日**权威源落库 + `stock_daily.limit_basis` 出处列。
--
-- 背景（2026-10-06/07 实测，全可复算）：`recompute_limits()` 一直用
-- `reference_data.security.is_st`（当前快照，updated_at 停在 2026-08-27）套全部历史，
-- 于是"主板 + 现在是 ST"的票在**它还不是 ST 的那些天**被判成 ±5%：
--   越界 **16,074 行 / 1,698 只**（2021-08-03~2026-09-30，`--check` 那条哨兵重刷前的现数），
--   其中"主板 + 快照 ST 但当日冲过 5.2 个点"的 15,550/180,604 行（8.6%）走明明是 ±10%。
-- 另一套写者 `c15_snapshot` 把通达信 ZTPrice 直接填进 stock_daily：2026 年 limit_up/pre_close
-- 不落在 5/10/20/30 任何一档的行 6,088 个，其中 **5,895 行是复权因子变化日（除权日）**
-- —— 快照给的涨停价与库里 pre_close 不同基，写进去就是脏的。
-- 直接后果：2026-09-30 涨停家数 严格口径 52 只，而通达信本地统计指数与乐咕乐股都是 56 只。
--
-- 本迁移只做两件事：① 新表存"当日真实档位"（通达信快照反推 + 人工/档期可补充）；
-- ② `stock_daily` 加 `limit_basis`，让每一行的 limit 值说清自己是哪层来的（不许假装都是权威）。
-- ⚠️ 不改任何 limit_up/limit_down 的值 —— 刷值走 `app/compute/limit_prices.py`，且刷前有回退快照。

create table if not exists reference_data.limit_price_daily (
  security_code  text         not null,
  trade_date     date         not null,
  band           numeric(5,4) not null,      -- 当日真实涨跌停幅度：0.05 / 0.10 / 0.20 / 0.30
  tdx_limit_up   numeric(12,4),              -- 通达信当日实报涨停价（只作旁证；**不直接进 stock_daily**）
  tdx_limit_down numeric(12,4),
  source         text         not null,      -- tdx_snapshot / st_period / manual
  captured_at    timestamptz  not null default now(),
  constraint limit_price_daily_pk primary key (security_code, trade_date),
  constraint limit_price_daily_band check (band in (0.05, 0.10, 0.20, 0.30)),
  constraint limit_price_daily_source check (source in ('tdx_snapshot','st_period','manual'))
);

comment on table  reference_data.limit_price_daily is
  '逐票逐日的**涨跌停档位权威**。存在的理由：档位是"那一天"的属性，不是"这只票现在是什么属性" —— '
  '`security.is_st` 是 2026-08-27 的当前快照，拿它套历史会把非 ST 的年份判成 ±5%';
comment on column reference_data.limit_price_daily.band is
  '当日真实幅度。合法值只有 0.05（主板风险警示）/ 0.10（主板）/ 0.20（创业、科创）/ 0.30（北交所）。'
  '注意创业板/科创板的 ST 股仍是 20%，只有主板分 5%/10% 两档';
comment on column reference_data.limit_price_daily.tdx_limit_up is
  '通达信 day_end_*.ZTPrice。**只用它反推档位（band），价格本身不进 stock_daily** —— '
  '实测除权日它与库里 pre_close 不同基（6,088 行不落在任何一档，其中 5,895 行是复权因子变化日）；'
  '非除权日它等于 round(pre_close×(1+band),2)，即不增加信息、只暴露档位';
comment on column reference_data.limit_price_daily.source is
  'tdx_snapshot=由通达信当日涨停价反推；st_period=由 `security_st_period` 档期确证；manual=人工登记';

alter table market_data.stock_daily add column if not exists limit_basis text;

comment on column market_data.stock_daily.limit_basis is
  '这一行的 limit_up/limit_down 是怎么来的：'
  'tdx=当日快照反推的档位（最硬）；proved_non_st=当日行情可证不是 ±5 个点那一档'
      '（涨幅超 5.2 个百分点，或 ±5 个点那个价根本算不出今天的收盘 —— 低价股 3.68 按 5% 只得 3.86）；'
  'st_period=落在 `security_st_period` 确证的 ST 区间内；rule_st_snapshot=**只有当前 is_st 快照**支持 5%（可能是错的，读数侧要能看见）；'
  'rule=按板块规则；'
  'unbounded=可证幅度超过本板块合法档 +2pp（无涨跌幅限制日，或 pre_close 与行情不同基）→ limit 两列给 NULL，不硬凑；'
  'no_limit_first5=次新股上市前 5 个交易日不设限（"头 5 场"读 `reference_data.security_session_span`，'
      '并要求首日不早于 2021-08-03 这个全库起点，免得把数据边界当成上市日）→ 两列 NULL；'
  'contradicted=档已选定但当天收盘价仍越过按该档算出的上下限（多半是除权行 pre_close 与行情不同基）'
      '→ 两列 NULL。这一层是**收口**：`python -m app.compute.limit_prices --check` 那条哨兵靠它结构上归零';

grant select, insert, update, delete on reference_data.limit_price_daily to ft_etl, ft_app;
grant select on reference_data.limit_price_daily to ft_reader;
grant select on reference_data.limit_price_daily to ft_admin;

-- ------------------------------------------------------------------ 验收（跑完贴这四条）
-- 1) 表和列在
-- select column_name from information_schema.columns
--  where table_schema='market_data' and table_name='stock_daily' and column_name='limit_basis';
-- 2) 档位只可能是四个值
-- select band, count(*) from reference_data.limit_price_daily group by 1 order by 1;
-- 3) 通达信快照覆盖了多少场（当前实测 **22 场，2026-09-01~10-06**：再早的 day_end_* 表只有
--    ItemNum、没有 Code，对不回票，`--seed-tdx` 会把它们跳过并报 tables_unusable）
-- select count(distinct trade_date) days, min(trade_date), max(trade_date) from reference_data.limit_price_daily;
