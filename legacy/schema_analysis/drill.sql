\pset format unaligned
\pset fieldsep '|'
select 'H1 破上限样本' k, string_agg(x, ' ; ') v from (
  select ts_code||' '||trade_date||' close='||close||' limit_up='||limit_up||' pct='||pct_chg||' board='||coalesce(s.board,'?')||' st='||s.is_st::text||' list='||coalesce(s.list_date::text,'?') x
  from md.stock_daily_2026 d left join ref.security s using (ts_code)
  where limit_up is not null and close>limit_up+0.001 order by random() limit 6) t;
select 'H2 破上限按板块分布' k, string_agg(board||'='||c, ', ') v from (
  select coalesce(s.board,'?') board, count(*) c from md.stock_daily_2026 d left join ref.security s using (ts_code)
  where limit_up is not null and close>limit_up+0.001 group by 1 order by 2 desc) t;
select 'H3 破下限按板块分布' k, string_agg(board||'='||c, ', ') v from (
  select coalesce(s.board,'?') board, count(*) c from md.stock_daily_2026 d left join ref.security s using (ts_code)
  where limit_down is not null and close<limit_down-0.001 group by 1 order by 2 desc) t;
select 'H4 破限且上市>5日' k, count(*) v from md.stock_daily_2026 d join ref.security s using (ts_code)
  where (limit_up is not null and close>limit_up+0.001 or limit_down is not null and close<limit_down-0.001)
  and list_date is not null and trade_date-list_date>5;
select 'H5 pct缺失样本' k, string_agg(x,' ; ') v from (
  select ts_code||' '||trade_date||' pre='||coalesce(pre_close::text,'null')||' close='||close x from md.stock_daily_2026 where pct_chg is null limit 5) t;
select 'H6 sector_member孤儿样本' k, string_agg(x,' ; ') v from (
  select ts_code||'@'||sector_code x from (select distinct ts_code, sector_code from ref.sector_member m
  where not exists (select 1 from ref.security s where s.ts_code=m.ts_code)) t limit 8) t;
select 'H7 孤儿是否在security但已下架' k, count(*) v from ref.sector_member m join ref.security s using (ts_code)
  where not s.is_active;
select 'H8 孤儿去重代码数' k, count(distinct ts_code) v from ref.sector_member m
  where not exists (select 1 from ref.security s where s.ts_code=m.ts_code);
select 'H9 snapshot孤儿' k, string_agg(distinct ts_code,',') v from md.stock_snapshot d
  where not exists (select 1 from ref.security s where s.ts_code=d.ts_code);
