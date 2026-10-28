-- 48_breadth_arms.sql
-- 市场宽度 + 阿姆氏指标（Arms / NVIA）：给 `analytics.market_breadth` 加「量能」那一半。
--
-- 用户 2026-10-20 夜的原话（标题「风险提示」，位置：首页盘面数据下面）：
--   「一个是市场宽度，一个是阿姆氏指标——这两个数据每天都要计算入库。
--     什么是市场宽度？就是全市场的涨跌家数。
--     第二个指标是阿姆氏：上涨的这些票子，它的成交额除以下跌票子的成交额，这个是看量的。」
-- 宽度（`up_count` / `down_count` / `up_ratio`）这一半 2021-08-02 起就在库里了，
-- **缺的正是量能这一半** —— 所以这次不是新表，是给同一行补齐"钱有没有跟上家数"。
--
-- 三条口径（与 `app/compute/breadth.py` 的写侧逐字对齐，改那边要一起改这里）：
--   ① **分子分母与 `up_count` / `down_count` 同一批票、同一个判据**：
--      `pct_change > 0` 计上涨、`< 0` 计下跌，都与写侧 `today` CTE 一样先把
--      `pct_change IS NULL` 的行（新股首日 / `pre_close` 缺失，全库实测 5,653 行）整批排除。
--      成交额取 `market_data.stock_daily.amount_yuan`（numeric(20,2)，单位元）。
--      实测全库 `amount_yuan` 为 NULL / 0 的行 **0 条**（2026-10-21 现查 6,446,917 行），
--      所以"按判据筛出来的家数"与"同一批票的成交额之和"是同一个母体，没有静默漏票。
--   ② **算不出就是 NULL，绝不写 0**。分母为 0、任一边缺 ⇒ `arms_ratio` NULL。
--      0 在这里是一个真实读数（"下跌票一分钱没成交"），NULL 才是"这一天判不出来"。
--      界面按 NULL 显示 `—`，断线不连。
--   ③ **日频原值，不做平滑**。周 / 月的阿姆斯是"期内上涨额合计 ÷ 期内下跌额合计"
--      （读侧 `duanxian/risk_signal.py` 重算），**不是把日比值取平均**；
--      所以库里必须留着每日的两个成交额合计，只留比值就重算不出来。
--
-- 本机实测分布（2026-10-21 现查 1,262 场，按 ① 的判据直接从 `stock_daily` 聚合算出）：
--   有值 1,261 场；NULL 1 场＝2021-08-02（本机第一场，4,277 行 `pct_change` 全空 ⇒ 涨跌家数都是 0）。
--   arms_ratio min 0.0672（2025-04-07）· p05 0.307 · p50 1.135 · p95 4.512 · max 2039.48（2024-09-30，
--   涨 5,291 家 / 跌 8 家那一天，是真数不是脏数）。
--   所以上界放得极宽（`numeric(14,6)` ⇒ 整数位 8 位），不把真实极端判成越界。
--   单场 `up_amount + down_amount` ≤ 当日全市场成交额合计 ≤ 实测最大 3.99 万亿（numeric(24,2) 有余量）。

alter table analytics.market_breadth add column if not exists up_amount   numeric(24, 2);
alter table analytics.market_breadth add column if not exists down_amount numeric(24, 2);
alter table analytics.market_breadth add column if not exists arms_ratio  numeric(14, 6);

comment on column analytics.market_breadth.up_amount is
    '上涨票的成交额合计（元）：当日 market_data.stock_daily 中 pct_change > 0 那批票的 amount_yuan 之和。'
    '与 up_count **同一批票、同一个判据**（都先排除 pct_change IS NULL 的行），不是另一个母体。'
    '阿姆氏指标的分子。NULL＝这一天算不出（不是 0）。'
    '口径与写侧 app/compute/breadth.py 的 CORE_SQL 逐字对齐；全史回算 python -m app.compute.breadth --backfill。';

comment on column analytics.market_breadth.down_amount is
    '下跌票的成交额合计（元）：当日 pct_change < 0 那批票的 amount_yuan 之和，母体与 down_count 一致。'
    '阿姆氏指标的分母。NULL＝算不出（不是 0）。';

comment on column analytics.market_breadth.arms_ratio is
    '阿姆氏 Advance/Decline Volume Ratio（用户口径，2026-10-20 夜逐字：「上涨的这些票子，'
    '它的成交额除以下跌票子的成交额」）＝ up_amount ÷ down_amount。'
    '分母为 0 或任一边缺 ⇒ NULL，绝不写 0、绝不写 Infinity。'
    '⚠️ 库里存的是**日频原值**：周 / 月的比值由读侧 duanxian/risk_signal.py 用期内两个合计重算，'
    '不是把日比值取平均。本机实测（2026-10-21，1,261 场有值）min 0.0672 / p50 1.1351 / p95 4.512 / max 2039.48。';

-- 应用角色：写侧（ft_app 走 breadth.refresh 的 upsert）与回填（ft_app）要能写，
-- 读侧（ft_reader）要能读；与 46 号同一套 grant 姿势。
grant select, insert, update, delete on analytics.market_breadth to ft_etl, ft_app;
grant select on analytics.market_breadth to ft_reader;
