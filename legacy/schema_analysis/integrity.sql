\pset format unaligned
\pset fieldsep '|'
select 'A1 stock_daily26 OHLC违规' k, count(*) v from md.stock_daily_2026 where high<low or high<least(open,close) or low>greatest(open,close) or least(open,high,low,close,pre_close)<=0;
select 'A2 stock_daily26 pct_chg缺失' k, count(*) v from md.stock_daily_2026 where pct_chg is null;
select 'A3 stock_daily26 涨跌幅与前后收不一致(>0.5pp)' k, count(*) v from md.stock_daily_2026 where pre_close>0 and pct_chg is not null and abs(pct_chg-(close-pre_close)/pre_close*100)>0.5;
select 'A4 stock_daily26 change_yuan不一致' k, count(*) v from md.stock_daily_2026 where pre_close>0 and abs(change_yuan-(close-pre_close))>0.011;
select 'A5 stock_daily26 非交易日' k, count(distinct trade_date) v from md.stock_daily_2026 d where not exists (select 1 from ref.trade_calendar c where c.cal_date=d.trade_date and c.is_open);
select 'A6 stock_daily26 ts_code不在security' k, count(distinct ts_code) v from md.stock_daily_2026 d where not exists (select 1 from ref.security s where s.ts_code=d.ts_code);
select 'A7 stock_daily26 收盘突破涨停价' k, count(*) v from md.stock_daily_2026 where limit_up is not null and close>limit_up+0.001;
select 'A8 stock_daily26 收盘跌破跌停价' k, count(*) v from md.stock_daily_2026 where limit_down is not null and close<limit_down-0.001;
select 'A9 stock_daily26 换手率异常(0~100外)' k, count(*) v from md.stock_daily_2026 where turnover_rate_pct is not null and (turnover_rate_pct<0 or turnover_rate_pct>100);
select 'A10 stock_daily26 成交量或额为0' k, count(*) v from md.stock_daily_2026 where vol_shares<=0 or amount_yuan<=0;
select 'B1 index_daily OHLC违规' k, count(*) v from md.index_daily where high<low or least(open,high,low,close)<=0;
select 'B2 index_daily ts_code不在security' k, count(distinct ts_code) v from md.index_daily d where not exists (select 1 from ref.security s where s.ts_code=d.ts_code);
select 'B3 sector_daily OHLC违规' k, count(*) v from md.sector_daily where high<low or least(open,high,low,close)<=0;
select 'B4 sector_daily 板块不在sector_info' k, count(distinct sector_code) v from md.sector_daily d where not exists (select 1 from ref.sector_info i where i.sector_code=d.sector_code);
select 'C1 indicator26 value为NULL' k, count(*) v from fct.indicator_value_2026 where value is null;
select 'C2 indicator26 ind_key未注册' k, string_agg(distinct ind_key, ',') v from fct.indicator_value_2026 iv where not exists (select 1 from config.indicator ci where ci.ind_key=iv.ind_key);
select 'C3 indicator26 ts_code不在security' k, count(distinct ts_code) v from fct.indicator_value_2026 d where not exists (select 1 from ref.security s where s.ts_code=d.ts_code);
select 'D1 sector_member 孤儿ts_code' k, count(*) v from ref.sector_member m where not exists (select 1 from ref.security s where s.ts_code=m.ts_code);
select 'D2 sector_member 孤儿sector_code' k, count(*) v from ref.sector_member m where not exists (select 1 from ref.sector_info i where i.sector_code=m.sector_code);
select 'E1 每日行数<5000的交易日' k, string_agg(trade_date::text, ',' order by trade_date) v from (select trade_date, count(*) c from md.stock_daily_2026 group by 1 having count(*)<5000) t;
select 'E2 2026应有交易日' k, count(*) v from ref.trade_calendar where cal_date between '2026-01-01' and '2026-09-17' and is_open;
select 'E3 2026实际有数据交易日' k, count(distinct trade_date) v from md.stock_daily_2026;
select 'F1 snapshot26 pe_ttm为NULL' k, count(*) v from md.stock_snapshot where pe_ttm is null;
select 'F2 snapshot26 ts_code不在security' k, count(distinct ts_code) v from md.stock_snapshot d where not exists (select 1 from ref.security s where s.ts_code=d.ts_code);
select 'G1 score26 rank缺失' k, count(*) v from fct.score f where trade_date>='2026-01-01' and rank_in_market is null;
select 'G2 日期越界(stock_daily)' k, count(*) v from md.stock_daily_2026 where trade_date>current_date;
