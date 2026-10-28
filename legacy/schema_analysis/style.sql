\pset format unaligned
\pset fieldsep '|'
select n.nspname||'.'||c.relname as tbl, a.attname, d.description
from pg_description d
join pg_class c on c.oid=d.objoid
join pg_namespace n on n.oid=c.relnamespace
join pg_attribute a on a.attrelid=c.oid and a.attnum=d.objsubid
where n.nspname not in ('pg_catalog','information_schema')
  and c.relname in ('stock_daily','indicator_value','score','score_detail','stock_snapshot',
                    'sector_daily','index_daily','fund_daily','margin_detail','security',
                    'trade_calendar','share_capital','dividend','quality_result','task','ingest_batch')
order by 1, a.attnum
limit 200;
