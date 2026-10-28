-- -*- coding: utf-8 -*-
-- 23_quality_rules_limit_prices.sql
-- 把"涨跌停档位"这件事的三条后果变成看得见的规则（2026-10-07 实测之后立）。
--
-- 背景见 `app/compute/limit_prices.py` 的模块说明：档位以前来自 `security.is_st` 当前快照
-- （updated_at 停在 2026-08-27）套全史 → 越界 16,074 行 / 1,698 只；另有 `c15_snapshot`
-- 把通达信未复权 ZTPrice 填进除权日 → 6,088 行的 limit_up/pre_close 不落在任何一档。
-- 这两件事以前**没有任何一条规则盯着**。

-- 1) 哨兵：不许有收盘价越过当日的涨跌停价。
--    容差 0.5 分：交易所把限价四舍五入到分，我们的 round 也一样，差一分以内算贴住。
insert into config.quality_rule (rule_key, name, scope, severity, target,
                                 check_kind, check_body, threshold, blocking, enabled, description)
values ('close_within_limit', '收盘价不越当日涨跌停价', 'table', 'error',
        'market_data.stock_daily', 'sql_bool',
        'SELECT count(*) = 0 FROM market_data.stock_daily'
        '  WHERE trade_date = :d AND limit_up IS NOT NULL AND close IS NOT NULL'
        '    AND (close > limit_up + 0.005 OR close < limit_down - 0.005)',
        '{"tolerance_yuan": 0.005}', false, true,
        '有越界就说明档位用错了（历史上是 is_st 当前快照套全史造成的）。'
        'NULL 的 limit 不算越界 —— 无涨跌幅限制日与次新前 5 场本来就故意留 NULL')
on conflict (rule_key) do nothing;

-- 2) 混基哨兵：limit_up/pre_close 必须落在四档之一。
--    这条专抓"另一条写者又出现了"：谁再把未复权的限价写进复权后的行，当天就红。
insert into config.quality_rule (rule_key, name, scope, severity, target,
                                 check_kind, check_body, threshold, blocking, enabled, description)
values ('limit_on_band', '涨跌停价落在合法档位之一', 'table', 'error',
        'market_data.stock_daily', 'sql_bool',
        'SELECT count(*) = 0 FROM market_data.stock_daily'
        '  WHERE trade_date = :d AND limit_up IS NOT NULL AND pre_close > 0'
        '    AND least(abs(limit_up/pre_close-1-0.05), abs(limit_up/pre_close-1-0.10),'
        '              abs(limit_up/pre_close-1-0.20), abs(limit_up/pre_close-1-0.30)) > 0.004',
        '{"bands": [0.05, 0.10, 0.20, 0.30]}', false, true,
        '实测 2026 年这一条抓到 6,088 行，其中 5,895 行是复权因子变化日 —— '
        '即通达信未复权的 ZTPrice 被写进了与 pre_close 不同基的行')
on conflict (rule_key) do nothing;

-- 3) 出处可见：每一行的 limit_basis 必须被唯一路径写过。
--    没写过 = 有别的东西在动这两列（或那次全库重刷没跑到这天）。
insert into config.quality_rule (rule_key, name, scope, severity, target,
                                 check_kind, check_body, threshold, blocking, enabled, description)
values ('limit_basis_present', '涨跌停价有出处标记', 'table', 'warning',
        'market_data.stock_daily', 'sql_bool',
        'SELECT coalesce(count(*) FILTER (WHERE limit_up IS NOT NULL'
        '                              AND limit_basis IS NULL)::numeric'
        '       / nullif(count(*) FILTER (WHERE limit_up IS NOT NULL), 0), 0) <= 0.01'
        '  FROM market_data.stock_daily WHERE trade_date = :d',
        '{"max_untagged_pct": 1.0}', false, true,
        'limit_up 非空但没有 limit_basis 的行超过 1% 就提醒：说明有第二条路径在写涨跌停价，'
        '或那天的重刷没跑到。出处取值见列注释（tdx/proved_non_st/st_period/rule_st_snapshot/rule/'
        'unbounded/no_limit_first5）')
on conflict (rule_key) do nothing;

-- ---------------------------------------------------------------- 字段字典跟着口径改
-- 以前写的是"pre_close * (1 + board_limit_ratio(board, is_st))" —— 那句话本身没错，
-- 但它让人以为 is_st 快照可以用于历史，正是这次错判的来源。
update operations.view_field_dict
   set formula = 'pre_close × (1 ± 当日档位)；档位权威见 reference_data.limit_price_daily 与 '
                 'app/compute/limit_prices.py（通达信当日快照反推 → 当日行情可证 → ST 档期 → 规则），'
                 '出处记在 limit_basis 列。⚠️ board_limit_ratio(board, is_st) 用的是当前快照，'
                 '只能作最后兜底，不许套历史'
 where table_name = 'market_data.stock_daily' and column_name in ('limit_up', 'limit_down');

-- 列注释**不在这份工单里**：`limit_up/limit_down` 归 `app/db/sql/06_comments.sql`，
-- `limit_basis` 归建它的那份 DDL `app/db/sql/26_limit_price_daily.sql`。
-- 一个列的口径只许住一处，两处文本迟早漂成两个答案（这里以前就有第三份）。
