-- 「个股研究 · 财务关键指标（多期）」的落库镜像：同花顺财务摘要，累计档 + 单季档并排存
--
-- 为什么要这张表（2026-10-16 实测，不是猜的）：
--   · 页面上那块「财务关键指标」本来就出网读同花顺（`vr/astock.py:299 financials()` →
--     `ak.stock_financial_abstract_ths(symbol, indicator="按报告期")`），但**只取了末行**。
--     同一次请求里其实带着 **103 期**（600519 实测 1998-12-31 … 2026-06-30），
--     还有「按单季度」一档（98 期）。所以近 5 年的营收/利润不需要新数据源，只需要把多期存下来。
--   · 本机库里**没有**任何营收/净利列：`market_data.stock_snapshot` 216,517 行里
--     `eps`、`roe_percent` 非零 **0 行**（10-15 已核过 bps/eps 全表为 0），反推是死路。
--   · 稳定性实测：同花顺连发 12 只 12/12 成、0.57 秒/只；连发 30 只（东财版）30/30 成、0.90 秒/只
--     ⇒ 全市场 5,200 只外推 50~78 分钟，可以后台分批回填，不必逐日全刷。
--
-- 两档都是**源给的**，不是我们自己差的（可加性已核：茅台 2026 Q1 547.03 亿 + Q2 375.75 亿
-- = 累计 H1 922.78 亿，误差 0.00%）⇒ 这一屏不参与"自己差一遍再和源对不上"那类事故。
-- ⚠️ 两档的数**不可混用**：`period_kind='cum'` 是年初到报告期累计，`'single'` 是这一季自己。
--    同比那一列也是各档各自的同比（源的口径），所以任何"跨档比较"都要先看 `period_kind`。
--
-- 这是**源镜像**（同 `market_data.stock_snapshot`、`market_data.sector_daily` 一类），
-- 不是判据产出 ⇒ 主键不含 spec_version；改解析规则改的是采集器 `ingest/jobs/c25_financials_ths.py`，
-- 原文留在 `raw` 里可回查（源交回来是「547.03亿」「11.56%」这种带单位字符串，缺数是 False/''）。
--
-- 单位：金额一律**元**（列名带 _yuan），比率/增速一律**百分数**（列名带 _percent，5.3 表示 5.3%），
-- 每股类是**元/股**。写 0 只有一种含义：源真的给了 0；缺数一律 NULL。

create table if not exists reference_data.stock_financial_period (
  security_code             text        not null,
  report_date               date        not null,             -- 报告期（2026-03-31 这种季末，不是披露日）
  period_kind               text        not null,             -- cum=年初到本报告期累计 / single=本季度单季

  revenue_yuan              numeric(24,4),                    -- 营业总收入（元）
  revenue_yoy_percent       numeric(12,4),                    -- 营业总收入同比增长 %（源给的本档同比）
  net_profit_yuan           numeric(24,4),                    -- 净利润（元，含少数股东）
  net_profit_yoy_percent    numeric(12,4),                    -- 净利润同比增长 %
  deduct_profit_yuan        numeric(24,4),                    -- 扣非净利润（元）：剔一次性损益，看主营更干净
  deduct_profit_yoy_percent numeric(12,4),                    -- 扣非同比 %

  eps                       numeric(16,6),                    -- 基本每股收益（元/股，本档口径）
  bvps                      numeric(16,6),                    -- 每股净资产（元/股，期末）
  cap_reserve_ps            numeric(16,6),                    -- 每股资本公积金（元/股）
  undistributed_ps          numeric(16,6),                    -- 每股未分配利润（元/股）
  op_cf_ps                  numeric(16,6),                    -- 每股经营现金流（元/股）：利润有没有变成钱

  net_margin_percent        numeric(12,4),                    -- 销售净利率 %
  gross_margin_percent      numeric(12,4),                    -- 销售毛利率 %
  roe_percent               numeric(12,4),                    -- 净资产收益率 %（本档口径，cum 档是年内累计 ROE）
  roe_diluted_percent       numeric(12,4),                    -- 摊薄 ROE %

  operating_cycle_days      numeric(16,4),                    -- 营业周期（天）
  inventory_turnover        numeric(16,6),                    -- 存货周转率（次，本档口径）
  inventory_days            numeric(16,4),                    -- 存货周转天数
  receivable_days           numeric(16,4),                    -- 应收账款周转天数
  current_ratio             numeric(16,6),                    -- 流动比率
  quick_ratio               numeric(16,6),                    -- 速动比率
  conservative_quick_ratio  numeric(16,6),                    -- 保守速动比率
  equity_ratio              numeric(16,6),                    -- 产权比率
  debt_ratio_percent        numeric(12,4),                    -- 资产负债率 %

  source                    text        not null default 'ths',   -- 只此一个源：同花顺（东财版留作对账，不混写）
  raw                       jsonb,                              -- 源交回的那一行原文（含「547.03亿」这类带单位串）
  fetched_at                timestamptz not null default now(),

  primary key (security_code, report_date, period_kind)
);

comment on table  reference_data.stock_financial_period is '个股财务多期镜像（采集器 ingest/jobs/c25_financials_ths.py，源＝同花顺财务摘要）：同一只票的**累计档**与**单季档**并排存，一报告期一行。供「个股深度复盘」的近 5 年营收/利润逐期分析与加权外推用。⚠️ 两档不可混用（cum 是年初到报告期累计、single 是本季自己），同比也是各档各自的；金额单位元、比率单位百分数。本机没有第二个财务源，接口断供时这里就是**停在最后一场**，读侧要按 fetched_at 说清有多旧，不许拿旧值冒充当期。';
comment on column reference_data.stock_financial_period.period_kind   is 'cum＝年初到本报告期累计（源 indicator=按报告期，实测 103 期到 1998）；single＝本季度单季（indicator=按单季度，实测 98 期到 2002）。两档都直接来自源、**不是我们自己差的**：可加性已核（茅台 Q1+Q2=H1 到分毫不差）。算季节性与环比一律用 single，算年度趋势与同比用 cum，混着用会把"半年"和"一季"排在一起。';
comment on column reference_data.stock_financial_period.revenue_yuan  is '营业总收入（元）。源交回是带单位字符串（"547.03亿"），解析规则只住在 c25 一处；解析不出来的写 NULL 并留 raw，不写 0。';
comment on column reference_data.stock_financial_period.revenue_yoy_percent is '营业总收入同比增长 %（**源自己的口径**，cum 档是"对去年同一累计档"）。⚠️ 基期接近 0 时源会给出天文数字（库里现存绝对值最大约 1.4e7，99.9 分位 5,040）⇒ 这一列只当"源是怎么说的"来读，外推与情景带一律用我们自己按同季重算的同比（`financial_forecast.yoy`），别拿它算平均。缺数是 NULL。';
comment on column reference_data.stock_financial_period.net_profit_yoy_percent is '净利润同比增长 %（源口径）。同 revenue_yoy_percent：这一族在扭亏、基期近零时会爆到 1e6~1e8（现存最大 9.78e7），是源的算术而不是数据坏了；屏上那一格要念"源同比"，不能当增速均值用。';
comment on column reference_data.stock_financial_period.roe_percent  is '净资产收益率 %。⚠️ 在 cum 档里它是**年内累计** ROE（Q1 的 10.57% 与全年的不是同一个尺子），跨期比绝对值会得出"三季度在跌"的假结论 ⇒ 要看趋势就改用 single 档或先年化，页面必须写清是哪一种。';
comment on column reference_data.stock_financial_period.op_cf_ps     is '每股经营现金流（元/股）。用来对照利润是不是真收到钱：净利涨而每股经营现金流不涨，那一格要能看出来（本屏只交两个数并列，不写"现金流差＝造假"这类推断）。';
comment on column reference_data.stock_financial_period.raw          is '源那一行的原文（dict）。带单位字符串的解析有过误读风险（"万/亿"、False、百分号），留原文是为了解释与回查，不是给页面读的。';

create index if not exists stock_fin_period_code_idx on reference_data.stock_financial_period (security_code, period_kind, report_date desc);
create index if not exists stock_fin_period_date_idx on reference_data.stock_financial_period (report_date desc, security_code);

-- 百分数列从 numeric(12,4) 放宽到 numeric(20,4)（10-16 实测的必要修正，不是洁癖）：
--   源那一列在**基期接近 0** 时会给出天文数字——库里现存的绝对值最大到 9,781 万（net_profit_yoy_percent），
--   99.9 分位是 2.77 万；而 12,4 的天花板是 10^8。补跑时实测有 11 只票（000408/000586/000776/000892/
--   000901/000936/301131/301379/600201/600867/688197）因为**一个**这样的值整只票 ~200 行全部写不进去
--   （psycopg 报 numeric field overflow，upsert 是一整批）。源镜像表就把源给的存下来，
--   "这个数能不能当增速读"是读侧的事（`app/compute/financial_forecast.py` 自己按同季重算同比，不拿这一列做外推）。
--   这段是可重复执行的：已经是 20 位就一句 ALTER 都不发。
do $$
declare
  c text;
begin
  for c in select column_name from information_schema.columns
            where table_schema = 'reference_data' and table_name = 'stock_financial_period'
              and numeric_precision = 12 and numeric_scale = 4
  loop
    execute format('alter table reference_data.stock_financial_period alter column %I type numeric(20,4)', c);
    raise notice '放宽 %', c;
  end loop;
end $$;

grant select, insert, update, delete on reference_data.stock_financial_period to ft_etl;
grant select on reference_data.stock_financial_period to ft_app, ft_reader;
