# -*- coding: utf-8 -*-
"""一屏看全各表新鲜度（新旧数据是否断档）。用法：python check_freshness.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]   # 本文件在 docs/<指南>/scripts/ 下，上溯三级即项目根
sys.path.insert(0, str(ROOT))                # 用项目根，绝不指外部仓库
sys.stdout.reconfigure(encoding='utf-8')
from app.db import engine                    # noqa: E402  凭据只从 .env 走，不写进源码
Q = """
select * from (
  select 1 ord, '行情 stock_daily' t, max(trade_date)::text v, count(*) filter (where trade_date>=(select max(trade_date) from market_data.stock_daily))::text n_last from market_data.stock_daily
  union all select 2, '行情 index_daily', max(trade_date)::text, '' from market_data.index_daily
  union all select 3, '行情 sector_daily', max(trade_date)::text, '' from market_data.sector_daily
  union all select 4, '行情 fund_daily', max(trade_date)::text, '' from market_data.fund_daily
  union all select 5, '行情 snapshot', max(trade_date)::text, '' from market_data.stock_snapshot
  union all select 6, '行情 margin', max(trade_date)::text, '' from market_data.margin_detail
  union all select 7, '行情 dragon_tiger', max(trade_date)::text, '' from market_data.dragon_tiger_detail
  union all select 8, '行情 etf_share', max(trade_date)::text, '' from market_data.etf_share
  union all select 9, '行情 market_fund_flow', max(trade_date)::text, '' from market_data.market_fund_flow
  union all select 10, '计算 indicator_value', max(trade_date)::text, '' from analytics.indicator_value
  union all select 11, '计算 score', max(trade_date)::text, '' from analytics.score
  union all select 12, '计算 heat', max(trade_date)::text, '' from analytics.sector_heat_daily
  union all select 13, '计算 sector_trend', max(trade_date)::text, '' from analytics.sector_trend
  union all select 14, '计算 ignition', max(trade_date)::text, '' from analytics.ignition
  union all select 15, '计算 score_trend', max(trade_date)::text, '' from analytics.score_trend
  union all select 16, '计算 breadth', max(trade_date)::text, '' from analytics.market_breadth
  union all select 17, '计算 volume_double', max(trade_date)::text, '' from analytics.volume_double
) x order by ord
"""
base = engine.query('select max(trade_date)::text v from market_data.stock_daily',
                    role='reader')[0]['v']
for r in engine.query(Q, role='reader'):
    print('%-26s %s%s' % (r['t'], r['v'], '' if r['v'] >= base else '  ← 落后'))
