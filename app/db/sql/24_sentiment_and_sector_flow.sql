-- -*- coding: utf-8 -*-
-- 24_sentiment_and_sector_flow.sql
-- 两张"以前只有现算、没人留历史"的表补成逐日落库：
--   analytics.market_sentiment_daily    首页「市场情绪 · 乐咕乐股口径」的 大盘宽度 / 题材投机 逐日档位
--   market_data.sector_fund_flow_daily  首页「板块资金趋势榜」的 行业净流入 逐日落点
--
-- 为什么要落库（用户 2026-10-06 直接点名）：
--   ① 这两块原来是**每次请求现抓 akshare**（`vr/market.py` 的 `_sentiment()` / `_sectors()`，
--      5 分钟内存缓存），页面一关就什么都没有 —— 所以"近 20 场折线"和"2024 年以来累计"
--      这两件事**根本做不出来**，不是不想做，是没料。
--   ② 落库之后历史才存在；同时也才谈得上"哪天没采到"（见下面 net 表的 source 与 captured_at）。
--
-- ⚠️ 两张表各有一个**补不回来**的性质，必须写在这里，否则日后有人以为能回填：
--   · 情绪档位可以回算（`analytics.market_breadth` 有逐日 up/down/涨停数，判据同一份），
--     所以历史能建；但 `limit_up_count` 是**单边判据**（09-22 给 70，双边实算是 65），
--     与乐咕的「真实涨停」会有几只的出入 —— 回算段要标 `source='local'`。
--   · 行业净流入**回算不了**：接口只有「即时」一档（3/5/10 日窗口在本机 akshare 1.18.97
--     已坏：`Length mismatch: Expected axis has 11 elements, new values have 8`），
--     而且**报文里没有日期字段** —— 非交易日去采会拿到上一场的数，事后无从判别。
--     所以写入侧必须自己判归属日（`c22_sector_flow.py` 与 `c21_fundflow_sina.py` 同一条规矩：
--     只认"最近一个已收盘交易日或今天"，隔太就拒写），不许回填历史日期。

create table if not exists analytics.market_sentiment_daily (
  trade_date     date           not null,
  spec_version   text           not null,        -- 判据版本：改阈值就是另一批行，不改写历史
  up_count       integer,
  down_count     integer,
  flat_count     integer,
  ratio          numeric(10,4),                  -- 上涨 ÷ 下跌（分母夹 1，别除零）
  limit_up_real  integer,                        -- 真实涨停家数（乐咕口径；回算段是单边判据，见 source）
  breadth_level  text,                           -- 冰点 / 偏弱 / 中性 / 偏强 / 普涨
  breadth_score  numeric(8,3),                   -- 编码：中性=0 轴（冰点-2 偏弱-1 中性0 偏强+1 普涨+2）
  theme_level    text,                           -- 冰点 / 普通 / 活跃 / 亢奋
  theme_score    numeric(8,3),                   -- 编码：**普通与活跃的正中间=0 轴**（-1.5 / -0.5 / +0.5 / +1.5）
  source         text,                           -- legu=当天现抓的乐咕读数；local=由 market_breadth 回算
  captured_at    timestamptz    not null default now(),
  primary key (trade_date, spec_version)
);

comment on table  analytics.market_sentiment_daily             is '首页「市场情绪 · 乐咕乐股口径」两档的逐日落点（判据与编码见 app/compute/sentiment_daily.py；折线图读这张表，不现抓接口）';
comment on column analytics.market_sentiment_daily.breadth_score is '大盘宽度编码：中性=0 是**用户指定的 0 轴**（原话「中性为 0 轴」）。0 不是"没有情绪"，是"情绪居中"，图上的 0 线就是这个意思';
comment on column analytics.market_sentiment_daily.theme_score   is '题材投机编码：0 轴落在「普通与活跃的正中间」（用户指定的 0 轴），所以取值是 ±0.5 / ±1.5 而不是整数 —— 让它有中位线可看';
comment on column analytics.market_sentiment_daily.source        is 'legu=当天现抓的乐咕读数（权威口径）；local=由 analytics.market_breadth 回算（涨停数是**单边判据**，与乐咕「真实涨停」会有几只出入）。两种来源画在同一条线上时必须能在 tooltip 里看出来';
comment on column analytics.market_sentiment_daily.spec_version  is '判据版本在主键里：调阈值（600/0.7/1.2/2.5 与 30/60/100）是新写一批行，历史那批不动';

create index if not exists market_sentiment_daily_day_idx
  on analytics.market_sentiment_daily (spec_version, trade_date desc);


create table if not exists market_data.sector_fund_flow_daily (
  trade_date     date           not null,       -- **归属日**：由写入侧按交易日历判，不来自报文（报文里没有日期）
  sector_name    text           not null,       -- 接口给的"行业"中文名（同花顺口径，与通达信板块不是同一套分类）
  source         text           not null,       -- ths_legu_daily_net：akshare stock_fund_flow_industry(即时)
  industry_index numeric(14,4),                  -- 行业指数当日值（可与本地板块日线对照，抓错行业时看得出来）
  pct            numeric(10,4),                  -- 行业涨跌幅 %
  inflow_yi      numeric(16,4),                  -- 流入资金（亿）
  outflow_yi     numeric(16,4),                  -- 流出资金（亿）
  net_yi         numeric(16,4),                  -- 净额（亿）= 流入 − 流出（接口给什么记什么，本地不再推算）
  firms          integer,                        -- 公司家数
  leader         text,                           -- 领涨股名（只作对照，不作为个股推荐依据）
  leader_pct     numeric(10,4),
  captured_at    timestamptz    not null default now(),
  primary key (trade_date, sector_name, source)
);

comment on table  market_data.sector_fund_flow_daily           is '首页「板块资金趋势榜」的逐日落点（写侧 ingest/jobs/c22_sector_flow.py）。**这张表之前不存在**：净流入只存在于每次请求现抓的接口里，关掉页面就没了';
comment on column market_data.sector_fund_flow_daily.trade_date is '归属日由交易日历判（报文里没有日期字段）。非交易日采到的是上一场的榜 ⇒ 写入侧隔太久就**拒写**，与 c21 个股资金流同一条规矩';
comment on column market_data.sector_fund_flow_daily.net_yi     is '净流入（亿）。⚠️ 同花顺的"净额"是推断口径（大单方向），不是交易所的资金流实测；跨源不可比';
comment on column market_data.sector_fund_flow_daily.sector_name is '同花顺行业名，**不是**通达信板块代码：与 analytics.sector_* 那批表不同一套分类，join 不上是设计如此，不要硬凑';

create index if not exists sector_fund_flow_daily_day_idx
  on market_data.sector_fund_flow_daily (trade_date desc, net_yi desc);

grant select, insert, update, delete on analytics.market_sentiment_daily    to ft_etl, ft_app;
grant select on analytics.market_sentiment_daily                            to ft_reader;
grant select, insert, update, delete on market_data.sector_fund_flow_daily to ft_etl, ft_app;
grant select on market_data.sector_fund_flow_daily                         to ft_reader;
