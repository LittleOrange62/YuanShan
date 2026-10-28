-- financetool 数据完整性体检（2026-09-23 全称版）。用法：
--   PGCLIENTENCODING=UTF8 "D:/PostgreSQL/17/bin/psql.exe" <FT_DSN_READER> -f integrity.sql
-- 期望：除 A2/A8 类"合理留空"外全部为 0。
\pset format unaligned
\pset fieldsep '|'
select 'A1 日线26 OHLC违规' k, count(*) v from market_data.stock_daily_2026 where high<low or high<least(open,close) or low>greatest(open,close) or least(open,high,low,close,pre_close)<=0;
select 'A2 日线26 pct缺失(新股首日为合理)' k, count(*) v from market_data.stock_daily_2026 where pct_change is null;
select 'A3 涨跌幅与前后收不一致(>0.5pp)' k, count(*) v from market_data.stock_daily_2026 where pre_close>0 and pct_change is not null and abs(pct_change-(close-pre_close)/pre_close*100)>0.5;
select 'A4 非交易日有数据' k, count(distinct trade_date) v from market_data.stock_daily_2026 d where not exists (select 1 from reference_data.trade_calendar c where c.calendar_date=d.trade_date and c.is_open);
select 'A5 日线代码不在主档' k, count(distinct security_code) v from market_data.stock_daily_2026 d where not exists (select 1 from reference_data.security s where s.security_code=d.security_code);
select 'A6 收盘越过涨停价(ST回算口径已知问题)' k, count(*) v from market_data.stock_daily_2026 where limit_up is not null and close>limit_up+0.001;
select 'A7 收盘越过跌停价(同上)' k, count(*) v from market_data.stock_daily_2026 where limit_down is not null and close<limit_down-0.001;
select 'A8 换手率越界(0~100)' k, count(*) v from market_data.stock_daily_2026 where turnover_rate_percent is not null and (turnover_rate_percent<0 or turnover_rate_percent>100);
select 'A9 量或额为0' k, count(*) v from market_data.stock_daily_2026 where volume_shares<=0 or amount_yuan<=0;
select 'A10 派生列整列漏算(按日看limit空值)' k, string_agg(trade_date::text||':'||null_lu||'/'||n, ', ') v from (select trade_date, count(*) filter (where limit_up is null) null_lu, count(*) n from market_data.stock_daily_2026 where trade_date>=current_date-7 group by 1) t;
select 'B1 指数OHLC违规' k, count(*) v from market_data.index_daily where high<low or least(open,high,low,close)<=0;
select 'B2 板块日线OHLC违规' k, count(*) v from market_data.sector_daily where high<low or least(open,high,low,close)<=0;
select 'B3 板块代码不在板块表' k, count(distinct sector_code) v from market_data.sector_daily d where not exists (select 1 from reference_data.sector_info i where i.sector_code=d.sector_code);
select 'C1 指标值NULL' k, count(*) v from analytics.indicator_value_2026 where value is null;
select 'C2 指标代码不在主档' k, count(distinct security_code) v from analytics.indicator_value_2026 d where not exists (select 1 from reference_data.security s where s.security_code=d.security_code);
select 'D1 板块成员孤儿(基金/指数板块为已知遗留)' k, count(*) v from reference_data.sector_member m where not exists (select 1 from reference_data.security s where s.security_code=m.security_code);
select 'E1 近7日每日行数异常(<5000)' k, string_agg(trade_date::text, ',') v from (select trade_date, count(*) c from market_data.stock_daily_2026 where trade_date>=current_date-10 group by 1 having count(*)<5000) t;
select 'E2 快照代码不在主档' k, count(distinct security_code) v from market_data.stock_snapshot d where not exists (select 1 from reference_data.security s where s.security_code=d.security_code);
select 'E3 快照市值NULL' k, count(*) v from market_data.stock_snapshot where trade_date=(select max(trade_date) from market_data.stock_snapshot) and total_market_value_yuan is null;
select 'F1 主档list_date缺失' k, count(*) filter (where list_date is null) v, count(*) total from reference_data.security;
