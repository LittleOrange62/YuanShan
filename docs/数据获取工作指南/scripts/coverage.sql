\pset format unaligned
\pset fieldsep '|'
-- comment coverage, excluding yearly/monthly partition children
with t as (
  select c.oid, n.nspname||'.'||c.relname as tbl, c.relkind,
         obj_description(c.oid) as tbl_comment,
         (select count(*) from pg_attribute a where a.attrelid=c.oid and a.attnum>0 and not a.attisdropped) as cols,
         (select count(*) from pg_attribute a join pg_description d on d.objoid=c.oid and d.objsubid=a.attnum
          where a.attrelid=c.oid and a.attnum>0 and not a.attisdropped) as commented
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where c.relkind in ('r','v','m','p') and n.nspname not in ('pg_catalog','information_schema')
)
select tbl,
  case relkind when 'r' then 'table' when 'p' then 'part_parent' when 'v' then 'view' when 'm' then 'matview' end kind,
  cols, commented,
  case when tbl_comment is null then 'NO' else 'YES' end tbl_cmt
from t
where tbl_comment is null or commented < cols
order by relkind, tbl;
