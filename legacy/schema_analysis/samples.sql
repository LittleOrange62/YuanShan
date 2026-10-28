\pset format unaligned
\pset fieldsep '|'
select 'ignition.stage='||stage||' | scope='||scope, count(*) from fct.ignition group by 1,2 order by 2 desc limit 12;
select 'heat.src='||src, n_parts, count(*) from fct.sector_heat_daily group by 1,2 order by 3 desc limit 6;
select 'trend.win='||win, count(*) from fct.sector_trend group by 1 order by 1;
select 'ffr.scope='||scope||' win='||window_days, count(*) from md.fund_flow_rank group by 1 order by 2 desc limit 8;
select 'ds.layer='||layer||' role='||coalesce(ds_role,'-')||' kind='||kind||' active='||is_active::text, count(*) from config.datasource group by 1 order by 1 limit 30;
select 'popup.match='||match_type||' action='||action, count(*) from config.popup_rule group by 1;
select 'help.scope='||scope, count(*) from config.help_note group by 1 order by 2 desc limit 10;
select 'alias.src='||coalesce(src,'-'), count(*) from ref.sector_alias group by 1;
select 'probe.msg='||left(coalesce(message,''),40), health, count(*) from ops.datasource_probe group by 1,2 order by 3 desc limit 8;
select 'lhb.reason='||left(reason,20) from md.lhb_detail group by 1 limit 8;
select 'margin.ts='||ts_code||' rz='||rz_balance||' rq='||rq_balance from md.margin_detail order by trade_date desc limit 3;
select 'mff.dates='||min(trade_date)||'~'||max(trade_date)||' rows='||count(*) from md.market_fund_flow;
select 'etf.type='||coalesce(etf_type,'-'), count(*) from md.etf_share group by 1 limit 8;
select 'ign.sample='||to_jsonb(t) from (select * from fct.ignition order by trade_date desc limit 2) t;
select 'trend.sample='||row_to_json(t) from (select * from fct.sector_trend where sector_code like '88%' limit 3) t;
