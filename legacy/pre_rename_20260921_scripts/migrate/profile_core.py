# -*- coding: utf-8 -*-
"""
B2 前的数据体检：看清 tq_core.db 里 ref 域数据的实际质量，再决定映射。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402

c = sqlite3.connect(
    f"file:{(config.LEGACY_DATA / 'tq_core.db').as_posix()}?mode=ro", uri=True)


def q(label, sql):
    print('\n--- %s ---' % label)
    cur = c.execute(sql)
    cols = [d[0] for d in cur.description]
    print('  ' + ' | '.join(cols))
    for r in cur.fetchall():
        print('  ' + ' | '.join('' if v is None else str(v) for v in r))


q('security_info 按 asset_type', """
  SELECT asset_type, COUNT(*) n, SUM(name IS NOT NULL) has_name,
         SUM(list_date IS NOT NULL) has_list_date
    FROM security_info GROUP BY asset_type ORDER BY n DESC""")

q('stock_info 按 asset_type', """
  SELECT asset_type, COUNT(*) n, SUM(name IS NOT NULL) has_name,
         SUM(total_shares IS NOT NULL) has_total, SUM(float_shares IS NOT NULL) has_float,
         SUM(industry IS NOT NULL) has_industry
    FROM stock_info GROUP BY asset_type ORDER BY n DESC""")

q('两表 ts_code 差集', """
  SELECT (SELECT COUNT(*) FROM security_info) sec_n,
         (SELECT COUNT(*) FROM stock_info) stk_n,
         (SELECT COUNT(*) FROM security_info WHERE ts_code NOT IN
            (SELECT ts_code FROM stock_info)) only_sec,
         (SELECT COUNT(*) FROM stock_info WHERE ts_code NOT IN
            (SELECT ts_code FROM security_info)) only_stk""")

q('market 取值', 'SELECT market, COUNT(*) n FROM security_info GROUP BY market')

q('有名字的样本', """
  SELECT ts_code, name, asset_type, market, list_date FROM stock_info
   WHERE name IS NOT NULL LIMIT 8""")

q('ST 股数量', """
  SELECT COUNT(*) n FROM stock_info WHERE name LIKE '%ST%'""")

q('sector_info 按类型', """
  SELECT sector_type, COUNT(*) n, SUM(is_active) active,
         SUM(parent_sector_code IS NOT NULL) has_parent
    FROM sector_info GROUP BY sector_type ORDER BY n DESC""")

q('sector_member 概况', """
  SELECT COUNT(*) n, COUNT(DISTINCT sector_code) sectors,
         COUNT(DISTINCT member_ts_code) members,
         COUNT(DISTINCT effective_date) eff_dates,
         MIN(effective_date) eff_min, MAX(effective_date) eff_max,
         SUM(member_name IS NOT NULL) has_name
    FROM sector_member""")

q('sector_member 里 sector_code 是否都在 sector_info', """
  SELECT COUNT(*) orphan FROM (
    SELECT DISTINCT sector_code FROM sector_member
     WHERE sector_code NOT IN (SELECT sector_code FROM sector_info))""")

q('index_info', 'SELECT ts_code, name, market FROM index_info')

q('focus/watchlist', """
  SELECT (SELECT COUNT(*) FROM focus_stocks) focus,
         (SELECT COUNT(*) FROM watchlist_groups) groups,
         (SELECT COUNT(*) FROM watchlist_stocks) wl_stocks""")

c.close()
