\pset format unaligned
\pset fieldsep '|'
select table_schema||'.'||table_name as tbl, ordinal_position as ord, column_name, data_type,
       is_nullable, column_default
from information_schema.columns
where table_schema||'.'||table_name in (
 'config.datasource','config.help_note','config.popup_rule','fct.ignition',
 'fct.sector_heat_daily','fct.sector_trend','md.etf_share','md.fund_flow_rank',
 'md.lhb_detail','md.lhb_inst','md.margin_detail','md.market_fund_flow',
 'ops.datasource_probe','ref.sector_alias')
order by 1, 2;
select tablename, indexname, indexdef from pg_indexes where
 schemaname||'.'||tablename in ('config.datasource','config.help_note','config.popup_rule','fct.ignition',
 'fct.sector_heat_daily','fct.sector_trend','md.etf_share','md.fund_flow_rank',
 'md.lhb_detail','md.lhb_inst','md.margin_detail','md.market_fund_flow',
 'ops.datasource_probe','ref.sector_alias') order by 1,2;
