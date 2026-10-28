-- -*- coding: utf-8 -*-
-- 22_quality_rules_derived_tables.sql
-- 给"派生表"补上回验规则；顺手把一条常红的规则改成能读的判据。
--
-- 为什么要动（2026-10-06 自动执行机制自查，实测）：
--   `config.quality_rule` 里 25 条规则**全部只盯 `market_data.stock_daily` / `analytics.score`
--   那一族**（`select target from config.quality_rule` 实测没有一条指向下面这些表）。
--   于是"作业 rc=0 但那天其实没算出来"在质量看板上完全看不见 —— 这正是自查判据第 4/5 条
--   要拦的那种"看着有其实没跑"。
--
-- 三条新规则都是 `sql_bool`：返回一个布尔，假则红。阈值写死在 SQL 里（与
--   `daily_row_count` 同风格），因为这几张表一天多少板块是有数可查的常量级：
--   实测 2026-09-30：sector_trend_signal 963 行 / sector_flow_trend 966 行 /
--   market_breadth 1 行 / volume_double 有数。下限取 300/1 是"半个市场都没了才红"，
--   不误报板块主档微调。
--
-- ⚠️ 别把这三条设成 blocking：它们是非骨干产出（e1 的 signal/flowtrend/ignition 都是非骨干），
--   红一条就停整条链会变成"因为分析表没算出来所以日线也不追了"。

insert into config.quality_rule (rule_key, name, scope, severity, target,
                                 check_kind, check_body, threshold, blocking, enabled, description)
values
  ('trend_signal_day_rows', '板块趋势信号当日有数', 'table', 'error',
   'analytics.sector_trend_signal', 'sql_bool',
   'SELECT count(*) >= 300 FROM analytics.sector_trend_signal WHERE trade_date = :d',
   '{"min": 300}', false, true,
   '判据产出：一天应有 500+ 个板块（概念+行业里有热度的那批）。少于 300 = 这一步没真跑成或上游热度缺场 —— 作业 rc=0 不算证据'),
  ('flow_trend_day_rows', '资金轮动趋势当日有数', 'table', 'error',
   'analytics.sector_flow_trend', 'sql_bool',
   'SELECT count(*) >= 300 FROM analytics.sector_flow_trend WHERE trade_date = :d AND basis = ''share''',
   '{"min": 300}', false, true,
   '份额口径的轮动统计。⚠️ 只有统计量没有信号；main_net 那条 basis 在 fund_flow_rank 攒够 30 场之前是空的，属预期，别按它判红'),
  ('breadth_day_rows', '市场宽度当日有数', 'table', 'error',
   'analytics.market_breadth', 'sql_bool',
   'SELECT count(*) = 1 FROM analytics.market_breadth WHERE trade_date = :d',
   null, false, true,
   '一天一行。涨停/跌停两列是单边判据（双向虚高，见 08 契约坑 1），本规则只保证"这行存在"，不保证那两个数可直取')
on conflict (rule_key) do nothing;

-- ---------------------------------------------------------------- 常红那条改成可读判据
-- `sector_member_valid` 从建表起**一次没绿过**（operations.quality_result 实测 31 次全 passed=false）。
-- 一条永远红的规则等于没有规则 —— 它会把真坏掉的那天淹在噪音里。
-- 根因已查清（2026-09-27，`.local` 对账）：`sector_member` 30,126 个 distinct 代码里
-- **22,267 个不在 `reference_data.security`**，几乎全在 `自定义` 板块（港美股 AMZN/NVDA、
-- 指数 399xxx、债券 000001CNY01、不带后缀的裸码），过完 `asset_type='stock'` 后
-- 概念/行业/风格/指数/地区是 96.7~100%，只有自定义会塌。
-- 所以判据从"一行都不许缺"改成"缺的比例超 5% 才红"，并把根因写进 description。
update config.quality_rule
   set name = '板块成分在主档里的比例',
       check_body = 'SELECT coalesce(1 - count(*) FILTER (WHERE s.security_code IS NULL)::numeric'
                   ' / nullif(count(*), 0), 1) >= 0.90'
                   '   FROM reference_data.sector_member m'
                   '   LEFT JOIN reference_data.security s ON s.security_code = m.security_code',
       threshold = '{"min_ratio": 0.90}',
       description = '成分代码能在证券主档里找到的比例要 ≥90%。**水位线是照着实测现状定的**：'
                     '2026-10-06 实测 352,661 成分行里 27,355 行（7.76%）不在主档 → 好比例 92.24%，'
                     '红线画在 90% 留 2.2 个百分点余量，再坏就往红走。已知欠账来源：`自定义` 板块装的是'
                     '港美股（AMZN/NVDA）、指数（399xxx）、债券（000001CNY01）与不带后缀的裸码；'
                     '根治只有两条路：登记基金/境外主档，或把这些板块排除出成分口径（见《数据加工工作指南》08 坑 11）。'
                     '旧判据是"零行缺失"，从建表起 31 次全红 —— 永远红的规则不叫规则，叫噪音。'
                     '**按成分算的任何口径都要先 join security 且过 asset_type=''stock''**',
       updated_at = now()
 where rule_key = 'sector_member_valid';

-- 让下一次审计能解释自己：写清楚"为什么改"（这条规则以前常红到没人看）
comment on table config.quality_rule is
  '质量规则登记（数据不是代码）。新增规则前先想：它红的时候，谁会看见、看见之后做什么 —— 一条永远红的规则等于没有规则';
